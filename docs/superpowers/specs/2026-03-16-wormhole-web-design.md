# Wormhole Web — Design Spec

A self-hostable HTTP gateway for the Magic Wormhole protocol. Enables sending and receiving files via `curl` or any HTTP client, interoperable with the standard `wormhole` CLI.

## Problem

Magic Wormhole is a great protocol for secure file transfer, but it requires the `wormhole` CLI on both ends. On servers, VMs, containers, or borrowed machines where you can't install software, you're stuck. This project bridges that gap with a plain HTTP API.

## Core Decisions

- **Server-side wormhole client:** The server completes the SPAKE2 exchange and handles encryption/decryption on behalf of the HTTP user. The server sees plaintext. Users who need E2E encryption should use the CLI directly.
- **Public relay:** Connects to the default Magic Wormhole relay (`relay.magic-wormhole.io`). No bundled relay server.
- **No browser UI:** Pure HTTP API, curl-friendly. A frontend may be added later.
- **Twisted-native:** Uses `twisted.web` directly with the `magic-wormhole` Python library. No async bridging, no extra framework.

## API

### `GET /receive/<code>`

Completes the wormhole exchange using the provided code and streams the file to the HTTP response.

**Response:**
- `Content-Disposition: attachment; filename="<original-name>"`
- `Content-Type` set from filename where possible
- `Transfer-Encoding: chunked`
- Body: raw file bytes, streamed

**Example:**
```bash
curl -OJ https://wormhole.example.com/receive/7-guitarist-revenge
```

**Errors:**
- `404` — invalid or expired wormhole code
- `500` — wormhole exchange failed
- `408` — timeout waiting for sender

### `PUT /send` (redirect flow)

For clients that support redirects (e.g., `curl -L`).

1. Client sends `PUT /send` (with `Expect: 100-continue` for large files)
2. Server creates a wormhole, obtains a code
3. Server responds `307 Temporary Redirect` → `Location: /send/<code>`
4. Client follows redirect, sends file body to `/send/<code>`
5. Response body contains the wormhole code and transfer status

**Example:**
```bash
curl -L -T myfile.tar.gz https://wormhole.example.com/send
# Output:
# 7-guitarist-revenge
# waiting for receiver...
# transfer complete
```

### `POST /send/new` (escape hatch — step 1)

Creates a wormhole and returns the code. No file data.

**Response:**
- `Content-Type: text/plain`
- Body: the wormhole code (e.g., `7-guitarist-revenge`)

**Example:**
```bash
CODE=$(curl -s https://wormhole.example.com/send/new)
echo "Tell the receiver: $CODE"
```

### `PUT /send/<code>` (upload — step 2, or redirect target)

Uploads file data for an existing wormhole session. Streams the request body into the wormhole transit connection once a receiver connects.

**Headers:**
- `Content-Type` — preserved for the receiver
- `X-Wormhole-Filename: <name>` — original filename (required for raw uploads)

**Response (streaming, text/plain):**
- Line 1: the wormhole code (repeated for convenience)
- Subsequent lines: status updates (`waiting for receiver...`, `transfer complete`)

**Example (two-step):**
```bash
CODE=$(curl -s https://wormhole.example.com/send/new)
curl -T myfile.tar.gz -H "X-Wormhole-Filename: myfile.tar.gz" \
  https://wormhole.example.com/send/$CODE
```

### `GET /health`

Returns `200 OK` with body `ok`. For load balancers and monitoring.

## Streaming Architecture

All transfers are streaming. The server never buffers a full file in memory.

### Receive path

1. HTTP request arrives at `/receive/<code>`
2. Server initiates wormhole exchange with the provided code
3. SPAKE2 key exchange completes with the sender
4. Server accepts the file offer
5. As data arrives over the wormhole transit connection, chunks are written directly to the HTTP response via `Request.write(chunk)`
6. When transfer completes, `Request.finish()` closes the response

### Send path

1. Wormhole is created (either via `PUT /send` redirect or `POST /send/new`)
2. Code is returned to the client
3. File upload begins on `PUT /send/<code>`
4. Upload chunks are read from the HTTP request body and held in a bounded in-memory buffer (e.g., 256KB)
5. When a receiver connects and PAKE completes, data flows from the buffer through the wormhole transit
6. Backpressure: if the buffer is full, the server pauses reading from the HTTP request (Twisted's `pauseProducing` / `resumeProducing`)
7. Response completes when the wormhole transfer finishes

### Memory budget

One buffer per active transfer (~256KB). A server with 512MB RAM can comfortably handle many concurrent multi-GB transfers.

## Project Structure

```
wormhole-web/
├── pyproject.toml          # uv project config, dependencies
├── Containerfile           # Podman/Docker container build
├── src/
│   └── wormhole_web/
│       ├── __init__.py
│       ├── server.py       # Twisted Web resource tree, CLI entry point
│       ├── sender.py       # Wormhole send logic, streaming upload
│       └── receiver.py     # Wormhole receive logic, streaming download
└── tests/
    └── ...
```

## Dependencies

- `magic-wormhole` — the protocol implementation (brings Twisted as a transitive dep)
- `twisted` — listed explicitly for `twisted.web`

No other runtime dependencies.

## Container

- Base image: `python:3.12-slim`
- Uses `uv` for dependency installation
- Exposes port 8080 (configurable)
- Single process, no supervisor

```
podman build -t wormhole-web .
podman run -p 8080:8080 wormhole-web
```

## Running locally

```bash
uv run python -m wormhole_web.server --port 8080
```

## Out of scope (for now)

- Browser UI / frontend
- Bundled mailbox relay server
- End-to-end encryption in the browser (WebAssembly wormhole client)
- Authentication / access control
- Rate limiting
