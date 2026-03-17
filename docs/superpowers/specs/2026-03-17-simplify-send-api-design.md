# Simplify Send API — Design Spec

Remove the two-step send flow (`POST /send/new` + `PUT /send/<code>`), making `PUT /send` (inline) the only send path. This eliminates in-memory sessions and enables horizontal scaling via consistent hashing on the wormhole code.

## Problem

The two-step send flow requires cross-request session state: `POST /send/new` creates a wormhole and stores it in a `SessionManager`, then `PUT /send/<code>` looks it up. This state is in-memory and tied to a single process, making horizontal scaling impossible without sticky sessions or shared state.

## Solution

Drop the two-step flow entirely. `PUT /send` is the only send endpoint. It creates the wormhole, allocates the code, does the PAKE exchange, and streams data — all in a single HTTP request. No cross-request state.

## What we remove

- `POST /send/new` endpoint and `SendNewResource` class
- `PUT /send/<code>` endpoint and `_handle_twostep_send` in `StreamingRequest`
- `src/wormhole_web/session.py` — `SessionManager`, `Session`, `SessionState` are no longer needed
- `start_key_exchange` from `sender.py` — PAKE happens inline within the request
- All session-related imports and wiring in `server.py` and `streaming.py`

## What stays unchanged

- `PUT /send` — inline streaming flow (already implemented)
- `GET /receive/<code>` — unchanged
- `GET /health` — unchanged
- `GET /` — web UI (updated to use inline flow)
- `create_send_session` and `complete_send` in `sender.py` — still used by `_handle_inline_send`

## API after simplification

| Method | Endpoint | Description |
|--------|----------|-------------|
| `PUT` | `/send` | Send a file. Streams response: code on first line, then status updates. |
| `GET` | `/receive/<code>` | Receive a file. |
| `GET` | `/health` | Health check. |
| `GET` | `/` | Web UI. |

## Web UI changes

Replace the two-step flow (`fetch POST /send/new` → `XHR PUT /send/<code>`) with a single `XHR PUT /send`:

1. User drops file
2. `XHR PUT /send` with `X-Wormhole-Filename` header
3. Upload progress from `xhr.upload.onprogress`
4. Parse code from first line of streaming response via `xhr.onprogress`
5. Show code, QR, status updates as before

**Safari limitation:** `xhr.onprogress` may not fire for the response body on PUT requests with upload bodies in Safari. The code and status text won't appear until the upload completes and `xhr.onload` fires. Upload progress still works. This is an acceptable tradeoff — Safari users see the progress bar during upload, then the code + "transfer complete" at the end.

## Horizontal scaling (future)

With this simplified API:
- `PUT /send` → any machine (creates and owns the wormhole)
- `GET /receive/<code>` → consistent hash on code routes to the owning machine
- No shared state between machines
- A reverse proxy extracts the code from the URL and routes deterministically

## Files to modify

- **Delete:** `src/wormhole_web/session.py`
- **Delete:** `tests/test_session.py`
- **Modify:** `src/wormhole_web/streaming.py` — remove `_handle_twostep_send`, remove session imports, simplify `gotLength` (no two-step path detection)
- **Modify:** `src/wormhole_web/server.py` — remove `SendNewResource`, `SendResource` (no children to route), remove session manager, simplify `RootResource`
- **Modify:** `src/wormhole_web/sender.py` — remove `start_key_exchange`
- **Modify:** `src/wormhole_web/static/index.html` — replace two-step JS with single XHR PUT
- **Modify:** `tests/test_integration.py` — remove two-step tests, update inline test
- **Modify:** `tests/test_e2e.py` — update send tests to use `PUT /send` instead of two-step

## Testing

- Existing inline send test (`TestInlineSend`) becomes the primary send test
- Remove `TestSendPath` (two-step) and `TestSendPathDelayed` (two-step delayed)
- E2E tests updated: `test_web_send_cli_receive` and `test_web_send_rs_receive` use `curl -T file http://host/send` instead of the two-step flow
- All receive tests unchanged
