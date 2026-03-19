# Rust Rewrite — Implementation Plan

Spec: [docs/rust-rewrite-spec.md](./rust-rewrite-spec.md)

## Phase 1: Rust server crate

### 1.1 Scaffold workspace and server crate

- Create root `Cargo.toml` as workspace:
  ```toml
  [workspace]
  members = ["crates/server", "crates/wormhole-wasm"]
  ```
- Create `crates/server/Cargo.toml`:
  - `axum` + `tokio` (async runtime)
  - `tower-http` (static files, CORS)
  - `tokio-tungstenite` (WebSocket client to transit relay)
  - `clap` (CLI args)
  - `tracing` + `tracing-subscriber` (logging)
- Create `crates/server/src/main.rs` with hello world
- Verify: `cargo run -p wormhole-web-server`

### 1.2 Static file serving

- `GET /` → serve `index.html`
- `GET /static/*` → serve from static directory
- `GET /health` → return "ok"
- `GET /receive/*` → serve `index.html` (SPA fallback)
- CLI args: `--port`, `--static-dir`

### 1.3 WebSocket-to-TCP transit bridge

- `GET /transit` with WebSocket upgrade
- On WS connect: open TCP to `transit.magic-wormhole.io:4001`
- **Buffer WS messages until TCP is established** — the browser may send
  the transit handshake before the TCP connection completes. Without
  buffering, the first message is dropped and transit silently fails.
- Spawn two tasks: WS→TCP and TCP→WS
- On either side close/error: close both
- Configurable upstream via `--transit-relay` arg + `TRANSIT_RELAY` env var

### 1.4 Test the server

- Integration test: start server, fetch `/`, verify HTML
- Integration test: WS connect to `/transit`, verify TCP bridge
- Build and run: `cargo build --release -p wormhole-web-server`

## Phase 2: Move static files

### 2.1 Relocate

- `src/wormhole_web/static/` → `static/`
- Update Makefile wasm output path

### 2.2 Update JS transit URL

In `static/wasm-client.js`, change:
```javascript
// OLD:
const TRANSIT_RELAY_URL = (location.protocol === 'https:' ? 'wss://' : 'ws://') +
  location.hostname + ':4002';

// NEW:
const TRANSIT_RELAY_URL = (location.protocol === 'https:' ? 'wss://' : 'ws://') +
  location.host + '/transit';
```

### 2.3 Verify locally

- `cargo run -p wormhole-web-server -- --static-dir static/`
- Open browser, confirm WASM loads, E2E badge shows
- Test send + receive with CLI

## Phase 3: Delete Python

### 3.1 Remove Python source

- Delete `src/wormhole_web/` (all .py files)
- Delete `tests/` (Python tests)
- Delete `pyproject.toml`, `uv.lock`
- Delete `.venv/` if present

### 3.2 Remove patched wormhole dependency

- `crates/magic-wormhole-patched/` stays (WASM crate needs it)

### 3.3 Update .gitignore

Remove Python-specific entries, add Rust target:
```
/target/
crates/*/target/
crates/wormhole-wasm/pkg/
```

## Phase 4: Containerfile + deployment

### 4.1 New Containerfile

Multi-stage build:
1. Rust builder: compile server binary + WASM
2. Runtime: debian-slim + binary + static files

### 4.2 Update fly.toml

- Remove `[[services]]` block (no port 4002)
- Reduce memory to 256MB
- Single port 8080

### 4.3 Deploy and verify

- `fly deploy`
- Test on https://wormhole-web.fly.dev/

## Phase 5: Cleanup

### 5.1 Update README

- Remove curl usage examples
- Remove Python install instructions
- Add Rust build instructions
- Update architecture description

### 5.2 Update docs

- Remove references to server-proxied flow
- Update spec to reflect Rust server
- Clean up TODOS.md

## Implementation order

```
Phase 1.1 ──▶ 1.2 ──▶ 1.3 ──▶ 1.4
(scaffold)   (static)  (bridge) (test)
                                  │
Phase 2.1 ◀──────────────────────┘
(move files)
  │
Phase 2.2 ──▶ 2.3
(JS URL)     (verify)
               │
Phase 3.1 ◀───┘
(delete py)
  │
Phase 3.2 ──▶ 3.3 ──▶ Phase 4 ──▶ Phase 5
(deps)       (git)    (deploy)    (docs)
```

## Definition of done

- [ ] `cargo build --release` produces a single binary
- [ ] Binary serves index.html, static files, health check
- [ ] WebSocket /transit bridges to public transit relay
- [ ] Browser send → CLI receive works (E2E)
- [ ] CLI send → browser receive works (E2E)
- [ ] No Python code in the repo
- [ ] Deployed to Fly.io, working at https://wormhole-web.fly.dev/
- [ ] Container image < 30MB
- [ ] README updated
