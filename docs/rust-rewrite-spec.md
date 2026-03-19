# Rust Rewrite Spec

## Motivation

With WASM E2E encryption working, the Python server's role has shrunk to:
1. Serve static files
2. WebSocket-to-TCP transit bridge
3. Health check

The curl/HTTP proxy (sender.py, receiver.py, streaming.py) is now dead code —
browser users bypass it entirely via WASM. Dropping it removes 1,205 lines of
Python, the entire Twisted dependency, and the insecure "server sees plaintext"
codepath.

A Rust server doing the same three things is ~200 lines, deploys as a single
static binary, and shares the toolchain with the WASM crate.

## What gets deleted

| File | Lines | Purpose | Replacement |
|------|-------|---------|-------------|
| sender.py | 120 | Wormhole send proxy | None (WASM) |
| receiver.py | 147 | Wormhole receive proxy | None (WASM) |
| streaming.py | 300 | ChunkQueue, backpressure | None (WASM) |
| server.py | 392 | Twisted HTTP + transit bridge | Rust server |
| constants.py | 9 | APPID, relay URLs | None (in WASM) |
| routing.py | 33 | Consistent hashing | None (no routing needed) |
| fly.py | 138 | Fly.io machine discovery | None (single binary) |
| util.py | 65 | Timeouts, filename sanitize | None |
| pyproject.toml | - | Python project config | Cargo.toml |
| uv.lock | - | Python lock file | Cargo.lock |
| tests/*.py | 858 | Python tests | Rust tests |
| **Total removed** | **~2,060** | | |

## What the Rust server does

Three things, nothing more:

### 1. Static file server

Serve files from an embedded or on-disk directory:
- `GET /` → `index.html`
- `GET /static/*` → JS, CSS, WASM files
- `GET /receive/<code>` → `index.html` (SPA-style, JS handles the code)

### 2. WebSocket-to-TCP transit bridge

- Listen for WebSocket connections on `/transit`
- For each incoming WS connection, open a TCP connection to
  `transit.magic-wormhole.io:4001`
- Pipe bytes bidirectionally: WS ↔ TCP
- Close both when either side disconnects

This replaces the separate port 4002 listener. The bridge runs on the same
HTTP port via WebSocket upgrade on the `/transit` path.

### 3. Health check

- `GET /health` → `200 ok`

## Architecture

```
┌──────────────────────────────────────┐
│  Rust Binary (single process)        │
│                                      │
│  HTTP server (axum or warp)          │
│  ├── GET /           → index.html    │
│  ├── GET /static/*   → static files  │
│  ├── GET /receive/*  → index.html    │
│  ├── GET /health     → "ok"          │
│  └── WS  /transit    → TCP bridge    │
│          │                           │
│          └──TCP──▶ transit relay     │
│                                      │
│  WASM artifacts embedded or served   │
│  from /static/wasm/                  │
└──────────────────────────────────────┘
```

Single port. No Python. No Twisted. No separate transit port.

## Rust crate choice

**axum** — the standard choice for Rust web servers:
- Built on tokio + hyper
- Native WebSocket support via `axum::extract::ws`
- Static file serving via `tower-http::services::ServeDir`
- Tiny binary size (~5MB static)
- Well-documented, widely used

## Project structure

```
wormhole-web/
├── Cargo.toml              # workspace
├── crates/
│   ├── wormhole-wasm/      # WASM crate (unchanged)
│   ├── magic-wormhole-patched/  # patched upstream (unchanged)
│   └── server/             # NEW: Rust HTTP server
│       ├── Cargo.toml
│       └── src/
│           └── main.rs     # ~200 lines
├── static/                 # moved from src/wormhole_web/static/
│   ├── index.html
│   ├── wasm-client.js
│   ├── qr.js
│   └── wasm/
│       ├── wormhole_wasm.js
│       └── wormhole_wasm_bg.wasm
├── Containerfile           # multi-stage: build Rust, copy binary + static
├── fly.toml
└── Makefile
```

## JS changes

### Transit bridge URL

Currently `ws://hostname:4002`. Change to `ws://hostname/transit` (same port,
path-based). The wasm-client.js `TRANSIT_RELAY_URL` becomes:

```javascript
const TRANSIT_RELAY_URL = (location.protocol === 'https:' ? 'wss://' : 'ws://') +
  location.host + '/transit';
```

### Receive URL handling

`/receive/<code>` now serves `index.html` (SPA-style). The JS already checks
`window.location.pathname` for `/receive/<code>` and auto-starts the WASM
receive flow. This just works.

## Containerfile

```dockerfile
FROM rust:1.94-slim AS builder
WORKDIR /build
COPY Cargo.toml Cargo.lock ./
COPY crates/ crates/
RUN cargo build --release -p wormhole-web-server
RUN cargo install wasm-pack
RUN cd crates/wormhole-wasm && wasm-pack build --target web --release

FROM debian:bookworm-slim
COPY --from=builder /build/target/release/wormhole-web-server /usr/local/bin/
COPY --from=builder /build/crates/wormhole-wasm/pkg/ /app/static/wasm/
COPY static/ /app/static/
EXPOSE 8080
CMD ["wormhole-web-server", "--port", "8080", "--static-dir", "/app/static"]
```

~15MB final image (vs ~200MB Python image).

## fly.toml

```toml
app = 'wormhole-web'
primary_region = 'ams'

[build]
  dockerfile = 'Containerfile'

[http_service]
  internal_port = 8080
  force_https = true
  auto_stop_machines = 'stop'
  auto_start_machines = true
  min_machines_running = 0

[[vm]]
  memory = '256mb'
  cpu_kind = 'shared'
  cpus = 1
  count = 2
```

Single port. Memory drops from 1GB to 256MB (no Python runtime, no wormhole
protocol state).

## Migration

1. Build the Rust server
2. Move static files to `static/`
3. Update JS transit URL from `:4002` to `/transit`
4. Delete all Python code
5. Update Containerfile
6. Update fly.toml (remove port 4002 service)
7. Deploy

## What this does NOT change

- WASM crate (unchanged)
- index.html (unchanged except transit URL in wasm-client.js)
- The wormhole protocol flow (still browser → mailbox relay → transit relay)
- Interop with CLI clients
