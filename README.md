# wormhole.page

End-to-end encrypted file transfer in the browser. Drop a file, share the code, done.

Built on [Magic Wormhole](https://magic-wormhole.readthedocs.io/) — fully interoperable with the standard `wormhole` and `wormhole-rs` CLIs. Your files never touch the server.

**Live at [wormhole.page](https://wormhole.page)**

## How it works

Your browser runs a full Magic Wormhole client via WebAssembly (compiled from [wormhole-rs](https://github.com/magic-wormhole/magic-wormhole.rs)). SPAKE2 key exchange and NaCl SecretBox encryption happen entirely client-side — the server only serves static files and bridges WebSocket transit to the public relay.

```
Browser ──WSS──▶ mailbox relay    (SPAKE2 key exchange)
Browser ──WS───▶ our server ──TCP──▶ transit relay  (encrypted data)
CLI ────────────────────TCP────────▶ transit relay  (encrypted data)
```

The server **cannot** decrypt your files. It sees only encrypted transit traffic.

## Usage

### Browser → CLI

1. Open [wormhole.page](https://wormhole.page)
2. Drop a file
3. Share the code with the receiver
4. Receiver runs: `wormhole-rs receive <code>`

### CLI → Browser

1. Run: `wormhole-rs send myfile.txt`
2. Open [wormhole.page](https://wormhole.page)
3. Enter the code and click Receive

### Browser → Browser

Open [wormhole.page](https://wormhole.page) in two tabs (or two devices). Send in one, receive in the other.

## Self-hosting

Requires Rust 1.94+ and wasm-pack.

```bash
git clone https://github.com/lucamartinetti/wormhole-web.git
cd wormhole-web
make build
make run
```

### Container

```bash
podman build -t wormhole-page .
podman run -p 8080:8080 wormhole-page
```

### Configuration

```
wormhole-page-server [OPTIONS]

  --port PORT              Listen port (default: 8080, env: PORT)
  --static-dir DIR         Static files directory (default: static/, env: STATIC_DIR)
  --transit-relay ADDR     Upstream transit relay (default: transit.magic-wormhole.io:4001, env: TRANSIT_RELAY)
```

## License

MIT
