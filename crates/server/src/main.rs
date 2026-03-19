use axum::{
    Router,
    extract::{
        State,
        ws::{Message, WebSocket, WebSocketUpgrade},
    },
    http::{StatusCode, header::{HeaderName, HeaderValue}},
    response::{Html, IntoResponse},
};
use clap::Parser;
use futures_util::{SinkExt, StreamExt};
use std::{net::SocketAddr, path::PathBuf, sync::Arc};
use tokio::net::TcpStream;
use tower_http::services::ServeDir;

/// wormhole.page — E2E encrypted file transfer in the browser.
#[derive(Parser)]
struct Args {
    /// Port to listen on
    #[arg(long, default_value = "8080", env = "PORT")]
    port: u16,

    /// Directory containing static files (index.html, wasm/, etc.)
    #[arg(long, default_value = "static", env = "STATIC_DIR")]
    static_dir: PathBuf,

    /// Upstream transit relay address (TCP)
    #[arg(
        long,
        default_value = "transit.magic-wormhole.io:4001",
        env = "TRANSIT_RELAY"
    )]
    transit_relay: String,
}

#[derive(Clone)]
struct AppState {
    index_html: Arc<String>,
    transit_relay: Arc<String>,
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info".into()),
        )
        .init();

    let args = Args::parse();

    // Read index.html at startup for SPA fallback
    let index_path = args.static_dir.join("index.html");
    let index_html = std::fs::read_to_string(&index_path)
        .unwrap_or_else(|e| panic!("Failed to read {}: {e}", index_path.display()));

    let transit_relay = args.transit_relay;
    let state = AppState {
        index_html: Arc::new(index_html),
        transit_relay: Arc::new(transit_relay.clone()),
    };

    // Static file service
    let static_service = ServeDir::new(&args.static_dir);

    let app = Router::new()
        .route("/health", axum::routing::get(health))
        .route("/transit", axum::routing::get(transit_ws))
        // SPA fallback: /receive/<code> serves index.html
        .route("/receive/{code}", axum::routing::get(spa_fallback))
        // Static files under /static/
        .nest_service("/static", static_service)
        // Root serves index.html
        .fallback(axum::routing::get(root_or_404))
        .layer(axum::middleware::from_fn(security_headers))
        .with_state(state);

    let addr = SocketAddr::from(([0, 0, 0, 0], args.port));
    tracing::info!("wormhole.page listening on {addr}");
    tracing::info!("transit bridge → {transit_relay}");

    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}

/// Middleware that adds security headers to every response.
async fn security_headers(
    req: axum::http::Request<axum::body::Body>,
    next: axum::middleware::Next,
) -> impl IntoResponse {
    let mut response = next.run(req).await;
    let headers = response.headers_mut();
    headers.insert(
        HeaderName::from_static("content-security-policy"),
        HeaderValue::from_static("default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; connect-src 'self' wss://relay.magic-wormhole.io ws: wss:; img-src 'self' data:; font-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"),
    );
    headers.insert(
        HeaderName::from_static("x-content-type-options"),
        HeaderValue::from_static("nosniff"),
    );
    headers.insert(
        HeaderName::from_static("x-frame-options"),
        HeaderValue::from_static("DENY"),
    );
    headers.insert(
        HeaderName::from_static("strict-transport-security"),
        HeaderValue::from_static("max-age=31536000; includeSubDomains"),
    );
    headers.insert(
        HeaderName::from_static("referrer-policy"),
        HeaderValue::from_static("no-referrer"),
    );
    headers.insert(
        HeaderName::from_static("permissions-policy"),
        HeaderValue::from_static("camera=(), microphone=(), geolocation=()"),
    );
    response
}

/// GET /health
async fn health() -> &'static str {
    "ok"
}

/// GET / (and fallback for unknown routes)
async fn root_or_404(
    State(state): State<AppState>,
    req: axum::http::Request<axum::body::Body>,
) -> impl IntoResponse {
    if req.uri().path() == "/" {
        Html(state.index_html.as_str().to_string()).into_response()
    } else {
        StatusCode::NOT_FOUND.into_response()
    }
}

/// GET /receive/<code> — serve index.html (SPA, JS handles the code)
async fn spa_fallback(State(state): State<AppState>) -> Html<String> {
    Html(state.index_html.as_str().to_string())
}

/// GET /transit — WebSocket-to-TCP bridge
///
/// Bridges a browser WebSocket connection to the upstream transit relay
/// via TCP. Both the browser and CLI peer meet at the same public relay.
///
///   Browser ──WS──▶ this server ──TCP──▶ transit.magic-wormhole.io:4001
///   CLI ──────────────────TCP──────────▶ transit.magic-wormhole.io:4001
async fn transit_ws(
    ws: WebSocketUpgrade,
    State(state): State<AppState>,
) -> impl IntoResponse {
    ws.on_upgrade(move |socket| handle_transit(socket, state.transit_relay))
}

async fn handle_transit(ws: WebSocket, relay_addr: Arc<String>) {
    tracing::debug!("transit bridge: browser connected");

    // Connect to upstream TCP relay
    let tcp = match TcpStream::connect(relay_addr.as_str()).await {
        Ok(stream) => stream,
        Err(e) => {
            tracing::warn!("transit bridge: upstream connect failed: {e}");
            let (mut sink, _) = ws.split();
            let _ = sink
                .send(Message::Close(Some(axum::extract::ws::CloseFrame {
                    code: 1011,
                    reason: "upstream relay unreachable".into(),
                })))
                .await;
            return;
        }
    };

    tracing::debug!("transit bridge: connected to upstream");

    let (tcp_read, tcp_write) = tcp.into_split();
    let (ws_sink, ws_stream) = ws.split();

    // WS → TCP: forward browser messages to upstream relay
    let ws_to_tcp = {
        let mut ws_stream = ws_stream;
        let mut tcp_write = tokio::io::BufWriter::new(tcp_write);
        async move {
            use tokio::io::AsyncWriteExt;
            while let Some(msg) = ws_stream.next().await {
                match msg {
                    Ok(Message::Binary(data)) => {
                        if tcp_write.write_all(&data).await.is_err() {
                            break;
                        }
                        if tcp_write.flush().await.is_err() {
                            break;
                        }
                    }
                    Ok(Message::Close(_)) | Err(_) => break,
                    _ => {} // ignore text, ping, pong
                }
            }
        }
    };

    // TCP → WS: forward upstream relay data to browser
    let tcp_to_ws = {
        let mut tcp_read = tokio::io::BufReader::new(tcp_read);
        let mut ws_sink = ws_sink;
        async move {
            use tokio::io::AsyncReadExt;
            let mut buf = vec![0u8; 64 * 1024];
            loop {
                match tcp_read.read(&mut buf).await {
                    Ok(0) => break, // EOF
                    Ok(n) => {
                        if ws_sink
                            .send(Message::Binary(buf[..n].to_vec().into()))
                            .await
                            .is_err()
                        {
                            break;
                        }
                    }
                    Err(_) => break,
                }
            }
        }
    };

    // Run both directions concurrently; when either finishes, the other is dropped
    tokio::select! {
        _ = ws_to_tcp => tracing::debug!("transit bridge: WS closed"),
        _ = tcp_to_ws => tracing::debug!("transit bridge: TCP closed"),
    }

    tracing::debug!("transit bridge: connection ended");
}
