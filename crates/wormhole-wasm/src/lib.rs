use std::sync::Arc;

use futures::channel::{mpsc, oneshot};
use futures::io::Cursor;
use futures::{AsyncRead, AsyncWrite, StreamExt};
use magic_wormhole::transit::{self, ConnectionType, TransitInfo};
use magic_wormhole::transfer::{self, AppVersion};
use magic_wormhole::{AppConfig, Code, MailboxConnection, Wormhole};
use wasm_bindgen::prelude::*;

#[wasm_bindgen(start)]
pub fn init() {
    console_error_panic_hook::set_once();
}

fn app_config() -> AppConfig<AppVersion> {
    transfer::APP_CONFIG.clone()
}

/// Default public transit relay (TCP). The CLI peer will connect here directly.
const PUBLIC_TRANSIT_RELAY: &str = "tcp://transit.magic-wormhole.io:4001";

fn relay_hints(bridge_url: &str) -> Vec<transit::RelayHint> {
    // The browser connects to our WebSocket bridge, which forwards to
    // the public TCP relay. We advertise BOTH URLs so:
    // - Our WASM code uses the ws:// bridge URL
    // - The CLI peer uses the tcp:// public URL
    // Both end up at the same upstream relay.
    vec![
        transit::RelayHint::from_urls(
            None,
            [
                bridge_url.parse().unwrap(),
                PUBLIC_TRANSIT_RELAY.parse().unwrap(),
            ],
        )
        .unwrap(),
    ]
}

/// Map verifier bytes to an emoji pair.
fn verifier_to_emoji(verifier: &[u8]) -> String {
    const EMOJI: &[&str] = &[
        "🐶", "🐱", "🐭", "🐹", "🐰", "🦊", "🐻", "🐼",
        "🐨", "🐯", "🦁", "🐮", "🐷", "🐸", "🐵", "🐔",
        "🐧", "🐦", "🦆", "🦅", "🦉", "🐝", "🐛", "🦋",
        "🐌", "🐞", "🐜", "🐢", "🐍", "🦎", "🐙", "🦑",
        "🦐", "🐠", "🐟", "🐡", "🐬", "🦈", "🐳", "🐋",
        "🐊", "🐆", "🐅", "🐃", "🦬", "🐂", "🐄", "🐪",
        "🐫", "🦙", "🐘", "🦣", "🦏", "🦛", "🐐", "🐏",
        "🐑", "🐎", "🐴", "🦌", "🦘", "🦥", "🦡", "🐿",
        "🦫", "🦨", "🦦", "🦝", "🐓", "🦃", "🦤", "🦚",
        "🦜", "🦢", "🦩", "🐇", "🦔", "🐉", "🌸", "🌺",
        "🌻", "🌹", "🌷", "🌼", "🌾", "🍀", "🍁", "🍂",
        "🍃", "🌿", "🪴", "🌵", "🌴", "🎋", "🎍", "🪻",
        "🍄", "🌰", "🎃", "🌈", "⭐", "🌙", "☀️", "⛅",
        "🌊", "❄️", "🔥", "💧", "🍎", "🍊", "🍋", "🍌",
        "🍉", "🍇", "🍓", "🫐", "🍒", "🍑", "🥭", "🍍",
        "🥝", "🍅", "🌽", "🥕", "🥑", "🫑", "🌶", "🧄",
        "🧅", "🥔", "🍞", "🥐", "🧀", "🥚", "🍳", "🧈",
        "🥞", "🧇", "🍕", "🍔", "🍟", "🌭", "🍿", "🧂",
        "🍩", "🍪", "🎂", "🍰", "🧁", "🍫", "🍬", "🍭",
        "☕", "🍵", "🧃", "🧊", "🎵", "🎶", "🎸", "🎹",
        "🥁", "🎺", "🎻", "🎲", "🎯", "🎳", "🎮", "🕹",
        "🧩", "🎪", "🎨", "🖌", "🔮", "🧿", "🪄", "🎩",
        "📷", "💡", "🔑", "🗝", "🧲", "🪜", "🛠", "⚙️",
        "🧰", "🔧", "🔨", "⛏", "🪓", "🗡", "🛡", "🏹",
        "🚀", "🛸", "🌍", "🗺", "🧭", "⛵", "🚂", "🚁",
        "🎈", "🎉", "🎊", "🎁", "🏆", "🥇", "🏅", "⚽",
        "🏀", "🏈", "⚾", "🎾", "🏐", "🏓", "🥊", "⛳",
        "🎣", "🤿", "🎿", "🛷", "🪁", "🏄", "🚴", "🧗",
        "💎", "👑", "🧸", "🪆", "🎭", "🎤", "📚", "🔬",
        "🔭", "💻", "🖥", "📱", "⌚", "🕰", "⏳", "🧬",
        "🪐", "🌋", "🏔", "🏝", "🌄", "🌅", "🏰", "🗽",
        "🎡", "🎢", "⛲", "🌁", "🏗", "🛤", "🌉", "🗼",
    ];
    let a = verifier.first().copied().unwrap_or(0) as usize;
    let b = verifier.get(1).copied().unwrap_or(0) as usize;
    format!("{} {}", EMOJI[a % EMOJI.len()], EMOJI[b % EMOJI.len()])
}

/// File metadata from a wormhole offer.
#[wasm_bindgen]
pub struct FileOffer {
    #[wasm_bindgen(readonly)]
    pub filesize: u64,
    filename: String,
}

#[wasm_bindgen]
impl FileOffer {
    #[wasm_bindgen(getter)]
    pub fn filename(&self) -> String {
        self.filename.clone()
    }
}

// -- Channel-based AsyncRead for feeding JS chunks into Rust --

struct ChannelReader {
    rx: mpsc::Receiver<Vec<u8>>,
    buf: Cursor<Vec<u8>>,
}

impl ChannelReader {
    fn new(rx: mpsc::Receiver<Vec<u8>>) -> Self {
        Self {
            rx,
            buf: Cursor::new(Vec::new()),
        }
    }
}

impl AsyncRead for ChannelReader {
    fn poll_read(
        mut self: std::pin::Pin<&mut Self>,
        cx: &mut std::task::Context<'_>,
        buf: &mut [u8],
    ) -> std::task::Poll<std::io::Result<usize>> {
        // Try reading from current buffer first
        let remaining = self.buf.get_ref().len() as u64 - self.buf.position();
        if remaining > 0 {
            return std::pin::Pin::new(&mut self.buf).poll_read(cx, buf);
        }

        // Need more data from channel
        match self.rx.poll_next_unpin(cx) {
            std::task::Poll::Ready(Some(data)) => {
                self.buf = Cursor::new(data);
                std::pin::Pin::new(&mut self.buf).poll_read(cx, buf)
            }
            std::task::Poll::Ready(None) => std::task::Poll::Ready(Ok(0)), // EOF
            std::task::Poll::Pending => std::task::Poll::Pending,
        }
    }
}

// -- Channel-based AsyncWrite that sends decrypted chunks to JS --

struct ChannelWriter {
    tx: mpsc::UnboundedSender<Vec<u8>>,
}

impl AsyncWrite for ChannelWriter {
    fn poll_write(
        self: std::pin::Pin<&mut Self>,
        _cx: &mut std::task::Context<'_>,
        buf: &[u8],
    ) -> std::task::Poll<std::io::Result<usize>> {
        let len = buf.len();
        self.tx
            .unbounded_send(buf.to_vec())
            .map_err(|_| std::io::Error::new(std::io::ErrorKind::BrokenPipe, "channel closed"))?;
        std::task::Poll::Ready(Ok(len))
    }

    fn poll_flush(
        self: std::pin::Pin<&mut Self>,
        _cx: &mut std::task::Context<'_>,
    ) -> std::task::Poll<std::io::Result<()>> {
        std::task::Poll::Ready(Ok(()))
    }

    fn poll_close(
        self: std::pin::Pin<&mut Self>,
        _cx: &mut std::task::Context<'_>,
    ) -> std::task::Poll<std::io::Result<()>> {
        self.tx.close_channel();
        std::task::Poll::Ready(Ok(()))
    }
}

/// A wormhole sender that runs entirely in the browser via WASM.
#[wasm_bindgen]
pub struct WormholeSender {
    code: String,
    mailbox: Option<MailboxConnection<AppVersion>>,
    transit_relay_url: String,
    verifier_bytes: Option<Vec<u8>>,
    conn_type: Option<String>,
    chunk_tx: Option<mpsc::Sender<Vec<u8>>>,
    done_rx: Option<oneshot::Receiver<Result<(), String>>>,
}

#[wasm_bindgen]
impl WormholeSender {
    /// Allocate a code and connect to the mailbox relay.
    /// Returns immediately with the code — does NOT wait for receiver.
    /// transit_relay_url: WebSocket URL for the transit relay (e.g. "ws://localhost:4002")
    #[wasm_bindgen]
    pub async fn create(transit_relay_url: &str) -> Result<WormholeSender, JsError> {
        web_sys::console::log_1(&"[wormhole] connecting to mailbox relay...".into());
        let config = app_config();
        let mailbox = MailboxConnection::create(config, 2)
            .await
            .map_err(|e| JsError::new(&format!("Failed to connect to relay: {e}")))?;
        let code = mailbox.code().to_string();
        web_sys::console::log_1(&format!("[wormhole] code allocated: {code}").into());

        Ok(WormholeSender {
            code,
            mailbox: Some(mailbox),
            transit_relay_url: transit_relay_url.to_string(),
            verifier_bytes: None,
            conn_type: None,
            chunk_tx: None,
            done_rx: None,
        })
    }

    /// Get the allocated wormhole code.
    #[wasm_bindgen]
    pub fn code(&self) -> String {
        self.code.clone()
    }

    /// Get the verification emoji pair (available after negotiate).
    #[wasm_bindgen]
    pub fn verifier(&self) -> Option<String> {
        self.verifier_bytes.as_ref().map(|v| verifier_to_emoji(v))
    }

    /// Get connection type: "direct" or "relayed" (available after negotiate).
    #[wasm_bindgen]
    pub fn connection_type(&self) -> Option<String> {
        self.conn_type.clone()
    }

    /// Wait for receiver, perform SPAKE2 key exchange, send file offer,
    /// and set up transit. After this returns, call send_chunk() to stream data.
    #[wasm_bindgen]
    pub async fn negotiate(
        &mut self,
        filename: &str,
        filesize: u64,
    ) -> Result<(), JsError> {
        let mailbox = self
            .mailbox
            .take()
            .ok_or_else(|| JsError::new("Already negotiated"))?;

        // SPAKE2 key exchange — blocks until receiver connects
        web_sys::console::log_1(&"[wormhole] waiting for receiver (SPAKE2)...".into());
        let wormhole = Wormhole::connect(mailbox)
            .await
            .map_err(|e| JsError::new(&format!("Key exchange failed: {e}")))?;

        // Store verifier before we consume wormhole
        self.verifier_bytes = Some(AsRef::<[u8]>::as_ref(wormhole.verifier()).to_vec());
        web_sys::console::log_1(&"[wormhole] SPAKE2 complete, starting file transfer...".into());

        // Create channel for streaming chunks from JS to Rust
        let (chunk_tx, chunk_rx) = mpsc::channel(16);
        let mut reader = ChannelReader::new(chunk_rx);

        // Create completion channel
        let (done_tx, done_rx) = oneshot::channel();

        let filename = filename.to_string();
        let relay = relay_hints(&self.transit_relay_url);

        // Spawn the send task - it will run in the background, reading from
        // the channel as JS feeds chunks in
        let conn_type_tx = Arc::new(std::sync::Mutex::new(None::<String>));
        let conn_type_tx2 = conn_type_tx.clone();

        wasm_bindgen_futures::spawn_local(async move {
            web_sys::console::log_1(&"[wormhole] send task: starting send_file...".into());
            let result = transfer::send_file(
                wormhole,
                relay,
                &mut reader,
                filename,
                filesize,
                transit::Abilities::ALL,
                move |info: TransitInfo| {
                    let ct = match info.conn_type {
                        ConnectionType::Direct => "direct".to_string(),
                        ConnectionType::Relay { .. } => "relayed".to_string(),
                        _ => "unknown".to_string(),
                    };
                    web_sys::console::log_1(&format!("[wormhole] transit connected: {ct}").into());
                    *conn_type_tx2.lock().unwrap() = Some(ct);
                },
                |sent, total| {
                    if sent % (1024 * 1024) < 65536 {
                        web_sys::console::log_1(
                            &format!("[wormhole] progress: {sent}/{total}").into(),
                        );
                    }
                },
                futures::future::pending(),
            )
            .await;

            match &result {
                Ok(()) => web_sys::console::log_1(&"[wormhole] send task: complete".into()),
                Err(e) => web_sys::console::log_1(&format!("[wormhole] send task ERROR: {e}").into()),
            }
            let _ = done_tx.send(result.map_err(|e| e.to_string()));
        });

        self.chunk_tx = Some(chunk_tx);
        self.done_rx = Some(done_rx);
        // Note: conn_type will be populated asynchronously by the transit handler
        // We store the Arc so we can read it later
        // For now, we need a small yield to let the send task start
        // The connection type will be available after the first send_chunk

        Ok(())
    }

    /// Send one chunk of file data. Call this repeatedly with the file contents.
    /// Awaits if the internal buffer is full (backpressure).
    #[wasm_bindgen]
    pub async fn send_chunk(&mut self, data: &[u8]) -> Result<(), JsError> {
        use futures::SinkExt;
        let tx = self
            .chunk_tx
            .as_mut()
            .ok_or_else(|| JsError::new("Not negotiated or already finished"))?;

        tx.send(data.to_vec())
            .await
            .map_err(|e| JsError::new(&format!("Send failed: {e}")))?;

        Ok(())
    }

    /// Signal that all data has been sent and wait for the transfer to complete.
    #[wasm_bindgen]
    pub async fn finish(&mut self) -> Result<(), JsError> {
        // Close the chunk channel to signal EOF
        self.chunk_tx.take();

        // Wait for the send task to complete
        if let Some(done_rx) = self.done_rx.take() {
            match done_rx.await {
                Ok(Ok(())) => Ok(()),
                Ok(Err(e)) => Err(JsError::new(&e)),
                Err(_) => Err(JsError::new("Transfer task dropped")),
            }
        } else {
            Err(JsError::new("Not negotiated"))
        }
    }

    /// Close and clean up.
    #[wasm_bindgen]
    pub fn close(mut self) {
        self.chunk_tx.take();
        self.done_rx.take();
        self.mailbox.take();
    }
}

/// A wormhole receiver that runs entirely in the browser via WASM.
#[wasm_bindgen]
pub struct WormholeReceiver {
    mailbox: Option<MailboxConnection<AppVersion>>,
    transit_relay_url: String,
    verifier_bytes: Option<Vec<u8>>,
    conn_type: Option<String>,
    receive_request: Option<transfer::ReceiveRequest>,
    chunk_rx: Option<mpsc::UnboundedReceiver<Vec<u8>>>,
    done_rx: Option<oneshot::Receiver<Result<(), String>>>,
}

#[wasm_bindgen]
impl WormholeReceiver {
    /// Connect to mailbox relay with the given code.
    /// transit_relay_url: WebSocket URL for the transit relay (e.g. "ws://localhost:4002")
    #[wasm_bindgen]
    pub async fn create(code: &str, transit_relay_url: &str) -> Result<WormholeReceiver, JsError> {
        web_sys::console::log_1(&format!("[wormhole] connecting to mailbox with code: {code}").into());
        let config = app_config();
        let code: Code = code
            .parse()
            .map_err(|_| JsError::new("Invalid wormhole code"))?;
        let mailbox = MailboxConnection::connect(config, code, false)
            .await
            .map_err(|e| JsError::new(&format!("Failed to connect: {e}")))?;
        web_sys::console::log_1(&"[wormhole] connected to mailbox".into());

        Ok(WormholeReceiver {
            mailbox: Some(mailbox),
            transit_relay_url: transit_relay_url.to_string(),
            verifier_bytes: None,
            conn_type: None,
            receive_request: None,
            chunk_rx: None,
            done_rx: None,
        })
    }

    /// Get the verification emoji pair (available after negotiate).
    #[wasm_bindgen]
    pub fn verifier(&self) -> Option<String> {
        self.verifier_bytes.as_ref().map(|v| verifier_to_emoji(v))
    }

    /// Get connection type (available after accept).
    #[wasm_bindgen]
    pub fn connection_type(&self) -> Option<String> {
        self.conn_type.clone()
    }

    /// Perform SPAKE2 exchange, wait for file offer, negotiate transit.
    /// Returns file metadata (name, size).
    #[wasm_bindgen]
    pub async fn negotiate(&mut self) -> Result<FileOffer, JsError> {
        let mailbox = self
            .mailbox
            .take()
            .ok_or_else(|| JsError::new("Already negotiated"))?;

        web_sys::console::log_1(&"[wormhole] performing SPAKE2 key exchange...".into());
        let wormhole = Wormhole::connect(mailbox)
            .await
            .map_err(|e| JsError::new(&format!("Key exchange failed: {e}")))?;

        self.verifier_bytes = Some(AsRef::<[u8]>::as_ref(wormhole.verifier()).to_vec());
        web_sys::console::log_1(&"[wormhole] SPAKE2 complete, waiting for file offer...".into());

        let relay = relay_hints(&self.transit_relay_url);
        let req = transfer::request_file(
            wormhole,
            relay,
            transit::Abilities::ALL,
            futures::future::pending(),
        )
        .await
        .map_err(|e| JsError::new(&format!("Receive failed: {e}")))?
        .ok_or_else(|| JsError::new("Transfer cancelled"))?;

        let offer = FileOffer {
            filename: req.file_name(),
            filesize: req.file_size(),
        };

        self.receive_request = Some(req);

        Ok(offer)
    }

    /// Accept the file offer and start receiving.
    /// After this, call receive_chunk() to get decrypted data.
    #[wasm_bindgen]
    pub async fn accept(&mut self) -> Result<(), JsError> {
        let req = self
            .receive_request
            .take()
            .ok_or_else(|| JsError::new("No pending receive request"))?;

        // Create channel for streaming chunks from Rust to JS
        let (chunk_tx, chunk_rx) = mpsc::unbounded();
        let mut writer = ChannelWriter { tx: chunk_tx };

        let (done_tx, done_rx) = oneshot::channel();

        wasm_bindgen_futures::spawn_local(async move {
            let result = req
                .accept(
                    |info: TransitInfo| {
                        let _ct = match info.conn_type {
                            ConnectionType::Direct => "direct",
                            ConnectionType::Relay { .. } => "relayed",
                            _ => "unknown",
                        };
                    },
                    |_received, _total| {},
                    &mut writer,
                    futures::future::pending(),
                )
                .await;

            let _ = done_tx.send(result.map_err(|e| e.to_string()));
        });

        self.chunk_rx = Some(chunk_rx);
        self.done_rx = Some(done_rx);

        Ok(())
    }

    /// Receive the next decrypted chunk.
    /// Returns empty vec when transfer is complete.
    #[wasm_bindgen]
    pub async fn receive_chunk(&mut self) -> Result<Vec<u8>, JsError> {
        let rx = self
            .chunk_rx
            .as_mut()
            .ok_or_else(|| JsError::new("Not accepted yet"))?;

        match rx.next().await {
            Some(data) => Ok(data),
            None => {
                // Channel closed - check if transfer completed successfully
                if let Some(done_rx) = self.done_rx.take() {
                    match done_rx.await {
                        Ok(Ok(())) => Ok(Vec::new()),
                        Ok(Err(e)) => Err(JsError::new(&e)),
                        Err(_) => Err(JsError::new("Transfer task dropped")),
                    }
                } else {
                    Ok(Vec::new())
                }
            }
        }
    }

    /// Close and clean up.
    #[wasm_bindgen]
    pub fn close(mut self) {
        self.chunk_rx.take();
        self.done_rx.take();
        self.mailbox.take();
        self.receive_request.take();
    }
}
