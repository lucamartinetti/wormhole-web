# wormhole-web

HTTP gateway for [Magic Wormhole](https://magic-wormhole.readthedocs.io/). Send and receive files via `curl` on machines without a wormhole client installed, interoperable with the standard `wormhole` and `wormhole-rs` CLIs.

## Usage

### Send a file

```bash
curl -T myfile.tar.gz -H "X-Wormhole-Filename: myfile.tar.gz" http://localhost:8080/send
```

Output:
```
wormhole receive 7-guitarist-revenge
waiting for receiver...
transferring...
transfer complete
```

Share the first line with the receiver. They run `wormhole receive 7-guitarist-revenge` on their machine.

The upload streams directly to the receiver with constant memory usage — no file size limit.

### Receive a file

Someone sends you a file with `wormhole send`. You receive it with curl:

```bash
curl -OJ http://localhost:8080/receive/7-guitarist-revenge
```

## Install

Requires Python 3.12+.

```bash
git clone https://github.com/lucamartinetti/wormhole-web.git
cd wormhole-web
uv sync
uv run wormhole-web --port 8080
```

### Container

```bash
podman build -t wormhole-web .
podman run -p 8080:8080 wormhole-web
```

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `PUT` | `/send` | Send a file. Streams upload directly to wormhole transit. Returns the code on the first line of the response. |
| `GET` | `/receive/<code>` | Receive a file. Streams with `Content-Disposition` and `Content-Length`. |
| `GET` | `/health` | Health check. Returns `ok`. |
| `GET` | `/` | Web UI. |

## How it works

The server acts as a wormhole client on behalf of the HTTP user. It completes the SPAKE2 key exchange, decrypts/encrypts data, and streams it between the HTTP connection and the wormhole transit connection. The server sees plaintext — if you need end-to-end encryption, use the wormhole CLI directly.

Uploads are true-streaming: body data flows directly from the HTTP request to the wormhole transit connection via a bounded in-memory queue with backpressure. Memory usage is constant (~4MB) regardless of file size. There is no file size limit.

Uses the public Magic Wormhole relay (`relay.magic-wormhole.io`) for signaling and transit. No bundled relay server needed.

## Configuration

```
wormhole-web [OPTIONS]

  --port PORT              Listen port (default: 8080)
  --transfer-timeout SECONDS  Stall timeout during transfers (default: 120)
```

TLS termination should be handled by a reverse proxy (Caddy, nginx, etc.).

## Horizontal scaling

Multiple instances can run behind a load balancer. Each `PUT /send` is handled by whichever instance receives it. `GET /receive/<code>` is routed to the instance that owns the wormhole code via consistent hashing.

On **Fly.io**, this works automatically using the `fly-replay` header. When a receive request hits the wrong instance, it transparently replays to the correct one. Set `FLY_API_TOKEN` as a secret for machine discovery:

```bash
flyctl tokens create deploy -a wormhole-web
flyctl secrets set FLY_API_TOKEN="FlyV1 ..."
flyctl scale count 4
```

No shared state between instances. Scale events during active transfers may disrupt ~1/N of in-flight sessions (wormhole codes are short-lived, so the window is small).

## License

MIT
