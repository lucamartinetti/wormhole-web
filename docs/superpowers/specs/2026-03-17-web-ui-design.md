# Web UI — Design Spec

A simple browser interface for sending and receiving files through wormhole-web.

## Goal

A single HTML page served at `GET /` that lets users send and receive files through the browser. Clean, minimal, no build step. Works on phones.

## Design

### Layout

Single centered column:
- Title: "wormhole-web"
- Subtitle: "Send and receive files securely"
- **Drop zone** — drag-and-drop or click to select a file
- "— or —" divider
- **Receive input** — text field for wormhole code + Receive button

### Send flow

1. User drops or selects a file
2. Drop zone transforms inline (no modal, no page change):
   - File info: name + size
   - Wormhole code in dark monospace box (appears after server allocates it)
   - QR code (web URL by default, toggle for `wormhole-transfer:` format)
   - Upload progress bar (from `XMLHttpRequest.upload.onprogress`)
   - Status text: "allocating code...", "waiting for receiver...", "transferring...", "transfer complete"
   - Cancel button (aborts XHR, resets to initial state)
3. Implementation:
   - `XMLHttpRequest` PUT to `/send` with `X-Wormhole-Filename` header
   - Upload progress from `xhr.upload.onprogress` (browser-native, no backend needed)
   - Server status from streaming response body — read progressively via `xhr.onprogress`, parse lines as they arrive
   - Lines from server: `wormhole receive <code>\n`, `waiting for receiver...\n`, `transferring...\n`, `transfer complete\n`
   - Parse the code from the first line to generate the QR code
4. Cancel: `xhr.abort()`, reset UI to initial state

### Receive flow

1. User enters wormhole code (e.g., `7-guitarist-revenge`), clicks Receive
2. Code input area transforms inline:
   - Shows "connecting..." with spinner/animation
   - Cancel button
3. `fetch()` to `/receive/<code>`:
   - On success (200 + headers): extract filename from `Content-Disposition`, create blob URL from streamed response, trigger download via `<a download>` click, show "download started — check your downloads", reset after brief delay
   - On error (404, 408, 500): show error message, let user retry
4. Cancel: `AbortController.abort()`, reset UI

### QR codes

Shown during send flow after code is allocated.

- **Default: web URL** — `{window.location.origin}/receive/<code>`. Any phone camera → browser → file downloads. Works for everyone.
- **Toggle: wormhole format** — `wormhole-transfer:<code>`. Interop with wormhole mobile apps that register the `wormhole-transfer:` URI scheme.
- Small toggle link below QR: "Show wormhole:// QR" / "Show web link QR"
- Generated client-side using a lightweight JS QR library bundled as a static asset (e.g., `qrcode-generator`, ~4KB minified). No CDN dependency.

### Styling

- No CSS framework. Inline `<style>` block in the HTML file.
- System font stack (`system-ui, -apple-system, sans-serif`)
- Max width ~480px, centered
- Responsive — works on mobile screens
- Light background, dark text. No dark mode (YAGNI).
- Monospace box for wormhole code (dark background, green/white text — terminal feel)
- Progress bar: simple colored div inside a gray track
- Drop zone: dashed border, changes on dragover

### No build step

The entire UI is a single `index.html` file with:
- Inline `<style>` for CSS
- Inline `<script>` for JS (or a second `<script src="qr.min.js">` for the QR library)
- No npm, no bundler, no transpilation

The QR library is the only external dependency. It's served as a static file alongside `index.html`.

## Backend changes

### New: `GET /` serves the web page

Add a resource that serves `index.html` from `src/wormhole_web/static/`.

Options for serving static files in Twisted:
- `twisted.web.static.File` — serves a directory. Simple but exposes the directory.
- Custom resource that reads and returns the file. More control.

Use `twisted.web.static.File` pointed at the `static/` directory. This also serves the QR library JS file.

### No API changes

The existing API endpoints (`PUT /send`, `GET /receive/<code>`, `POST /send/new`, `GET /health`) are sufficient. The browser uses them directly.

The streaming response from `PUT /send` is already parseable line-by-line — the browser reads it progressively via `XMLHttpRequest.onprogress`.

## File structure

```
src/wormhole_web/
├── static/
│   ├── index.html      # The web UI (HTML + inline CSS + inline JS)
│   └── qr.min.js       # QR code generator library (~4KB)
├── server.py            # Modified: add static file serving at /
└── ...                  # Existing files unchanged
```

## Testing

- Manual testing in browser (send and receive flows)
- The existing integration and E2E tests continue to cover the API
- No automated browser tests (YAGNI for a single static page)

## Out of scope

- Dark mode
- Multiple file / directory transfer
- Drag-and-drop folder upload
- E2E encryption in browser (future: WASM wormhole client)
- Internationalization
