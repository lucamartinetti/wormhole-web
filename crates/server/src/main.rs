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
    service_worker: Arc<String>,
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

    // Read sw.js at startup for root-scoped service worker
    let sw_path = args.static_dir.join("sw.js");
    let service_worker = std::fs::read_to_string(&sw_path)
        .unwrap_or_else(|e| panic!("Failed to read {}: {e}", sw_path.display()));

    let transit_relay = args.transit_relay;
    let state = AppState {
        index_html: Arc::new(index_html),
        service_worker: Arc::new(service_worker),
        transit_relay: Arc::new(transit_relay.clone()),
    };

    // Static file service
    let static_service = ServeDir::new(&args.static_dir);

    let app = Router::new()
        .route("/health", axum::routing::get(health))
        .route("/sw.js", axum::routing::get(serve_sw))
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
        HeaderValue::from_static("default-src 'none'; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; connect-src 'self' wss://relay.magic-wormhole.io:443; img-src 'self' data:; font-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"),
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

/// GET /sw.js — serve the service worker from root scope with no-cache
async fn serve_sw(State(state): State<AppState>) -> impl IntoResponse {
    (
        [
            (
                HeaderName::from_static("content-type"),
                HeaderValue::from_static("application/javascript"),
            ),
            (
                HeaderName::from_static("cache-control"),
                HeaderValue::from_static("no-cache"),
            ),
        ],
        state.service_worker.as_str().to_string(),
    )
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
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::Duration;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    tracing::info!("transit bridge: browser connected");
    let start = std::time::Instant::now();

    // Connect to upstream TCP relay with a timeout
    let tcp = match tokio::time::timeout(
        Duration::from_secs(10),
        TcpStream::connect(relay_addr.as_str()),
    )
    .await
    {
        Ok(Ok(stream)) => {
            // Set TCP keepalive to detect dead connections
            let sock_ref = socket2::SockRef::from(&stream);
            let keepalive = socket2::TcpKeepalive::new()
                .with_time(Duration::from_secs(30))
                .with_interval(Duration::from_secs(10));
            let _ = sock_ref.set_tcp_keepalive(&keepalive);
            stream
        }
        Ok(Err(e)) => {
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
        Err(_) => {
            tracing::warn!("transit bridge: upstream connect timed out (10s)");
            let (mut sink, _) = ws.split();
            let _ = sink
                .send(Message::Close(Some(axum::extract::ws::CloseFrame {
                    code: 1011,
                    reason: "upstream relay connect timeout".into(),
                })))
                .await;
            return;
        }
    };

    tracing::info!("transit bridge: connected to upstream relay");

    let (tcp_read, tcp_write) = tcp.into_split();
    let (mut ws_sink, mut ws_stream) = ws.split();

    let ws_to_tcp_bytes = Arc::new(AtomicU64::new(0));
    let tcp_to_ws_bytes = Arc::new(AtomicU64::new(0));

    // WS → TCP: forward browser messages to upstream relay
    let ws_to_tcp_count = ws_to_tcp_bytes.clone();
    let ws_to_tcp = async move {
        let mut tcp_write = tokio::io::BufWriter::new(tcp_write);
        while let Some(msg) = ws_stream.next().await {
            match msg {
                Ok(Message::Binary(data)) => {
                    let len = data.len() as u64;
                    // Use a timeout on TCP write to avoid hanging on stalled relay
                    match tokio::time::timeout(
                        Duration::from_secs(30),
                        async {
                            tcp_write.write_all(&data).await?;
                            tcp_write.flush().await
                        },
                    )
                    .await
                    {
                        Ok(Ok(())) => {
                            ws_to_tcp_count.fetch_add(len, Ordering::Relaxed);
                        }
                        Ok(Err(e)) => {
                            tracing::warn!("transit bridge: TCP write error: {e}");
                            break;
                        }
                        Err(_) => {
                            tracing::warn!("transit bridge: TCP write timeout (30s)");
                            break;
                        }
                    }
                }
                Ok(Message::Close(_)) => {
                    tracing::debug!("transit bridge: WS close frame received");
                    break;
                }
                Err(e) => {
                    tracing::warn!("transit bridge: WS read error: {e}");
                    break;
                }
                _ => {} // ignore text, ping, pong
            }
        }
        // Flush any remaining data before dropping
        let _ = tcp_write.flush().await;
        let _ = tcp_write.shutdown().await;
    };

    // TCP → WS: forward upstream relay data to browser
    let tcp_to_ws_count = tcp_to_ws_bytes.clone();
    let tcp_to_ws = async move {
        let mut tcp_read = tokio::io::BufReader::new(tcp_read);
        let mut buf = vec![0u8; 64 * 1024];
        loop {
            // Timeout on TCP read — if the relay goes silent for 2 minutes,
            // the connection is likely dead
            match tokio::time::timeout(Duration::from_secs(120), tcp_read.read(&mut buf)).await {
                Ok(Ok(0)) => {
                    tracing::debug!("transit bridge: TCP EOF");
                    break;
                }
                Ok(Ok(n)) => {
                    tcp_to_ws_count.fetch_add(n as u64, Ordering::Relaxed);
                    if ws_sink
                        .send(Message::Binary(buf[..n].to_vec().into()))
                        .await
                        .is_err()
                    {
                        tracing::warn!("transit bridge: WS send failed");
                        break;
                    }
                }
                Ok(Err(e)) => {
                    tracing::warn!("transit bridge: TCP read error: {e}");
                    break;
                }
                Err(_) => {
                    tracing::warn!("transit bridge: TCP read timeout (120s), closing");
                    break;
                }
            }
        }
        // Send close frame to browser
        let _ = ws_sink
            .send(Message::Close(Some(axum::extract::ws::CloseFrame {
                code: 1000,
                reason: "relay closed".into(),
            })))
            .await;
    };

    // Run both directions concurrently; when either finishes, the other is dropped.
    // This is correct for a bidirectional proxy — once one side closes, the bridge
    // is no longer functional. The flush/shutdown above ensures pending data is sent.
    tokio::select! {
        _ = ws_to_tcp => tracing::debug!("transit bridge: WS→TCP finished"),
        _ = tcp_to_ws => tracing::debug!("transit bridge: TCP→WS finished"),
    }

    let elapsed = start.elapsed();
    let ws_bytes = ws_to_tcp_bytes.load(Ordering::Relaxed);
    let tcp_bytes = tcp_to_ws_bytes.load(Ordering::Relaxed);
    tracing::info!(
        "transit bridge: closed after {:.1}s (WS→TCP: {} bytes, TCP→WS: {} bytes)",
        elapsed.as_secs_f64(),
        ws_bytes,
        tcp_bytes,
    );
}
