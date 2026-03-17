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

Uses the **two-step API** (`POST /send/new` then `PUT /send/<code>`) as the primary path. This avoids Safari's limitation where `XMLHttpRequest.onprogress` does not fire for response bodies on PUT requests with upload bodies. The two-step flow obtains the code synchronously before the upload begins, so the QR code and status display don't depend on streaming the response.

1. User drops or selects a file
2. Drop zone transforms inline (no modal, no page change):
   - File info: name + size
   - Status: "allocating code..."
3. JS calls `POST /send/new` via `fetch()` — response is the wormhole code
4. UI updates:
   - Wormhole code in dark monospace box
   - QR code generated from the code
   - Status: "uploading... waiting for receiver"
   - Upload progress bar
   - Cancel button
5. JS starts `XMLHttpRequest` PUT to `/send/<code>` with `X-Wormhole-Filename` header
   - Upload progress from `xhr.upload.onprogress` (browser-native)
   - Response body parsed line-by-line when available: `<code>\n`, `waiting for receiver...\n`, `transferring...\n`, `transfer complete\n`, or `error: <message>\n`
   - On non-200 response: show error (e.g., 404 expired code, 409 duplicate, 503 session limit, 411 missing Content-Length)
6. Cancel: `xhr.abort()`, reset UI to initial state

### Receive flow

1. User enters wormhole code (e.g., `7-guitarist-revenge`), clicks Receive
2. Code input area transforms inline:
   - Shows "connecting..." with spinner
   - Cancel button
3. Browser navigates to `/receive/<code>` via a hidden `<a>` element with `download` attribute. This triggers a standard browser download — the browser handles progress natively.
4. Before triggering the download, do a `fetch()` with `method: 'HEAD'` or a short-timeout `fetch()` to `/receive/<code>` to check connectivity first. On error (404, 408, 500): show error message, let user retry. On success: trigger the actual download.

**Note on large files:** The receive flow uses the browser's native download mechanism (anchor click), NOT `fetch()` + blob buffering. This avoids loading the entire file into browser memory. The browser streams directly to disk regardless of file size.

**Note:** A HEAD request won't work since `/receive/<code>` must complete the wormhole exchange to know the file metadata. Instead, simply trigger the download directly via anchor click. If the server returns an error (non-200), the browser will show the error text. The UI resets and shows a retry option. This is the simplest approach and avoids consuming the wormhole code on a preflight check.

Revised receive flow:
1. User enters code, clicks Receive
2. UI shows "starting download..." briefly
3. Create `<a href="/receive/<code>" download>` and click it programmatically
4. Reset UI to initial state
5. If the code was wrong, the browser tab/download shows the error text — the user can retry

### QR codes

Shown during send flow after code is allocated (step 4).

- **Default: web URL** — `{window.location.origin}/receive/<code>`. Any phone camera → browser → file downloads.
- **Toggle: wormhole-transfer format** — `wormhole-transfer:<code>`. Interop with wormhole mobile apps that register the `wormhole-transfer:` URI scheme.
- Small toggle link below QR: "Show wormhole QR" / "Show web link QR"
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

Add `index.html` serving at the root path. In Twisted, `RootResource` gets a `render_GET` that reads and returns the `index.html` file. The QR library JS file is served via a `static` child resource using `twisted.web.static.File`.

```python
# In RootResource:
def render_GET(self, request):
    request.setHeader(b"content-type", b"text/html")
    return self._index_html  # read once at startup

self.putChild(b"static", static.File(static_dir))
```

The QR library is referenced as `<script src="/static/qr.min.js">`.

### No API changes

The existing API endpoints are sufficient. The browser uses them directly via same-origin requests (no CORS needed since the page is served from the same origin).

The server can return `error: <message>\n` as a status line during send — the JS parser must handle this in addition to the four standard status lines.

## File structure

```
src/wormhole_web/
├── static/
│   ├── index.html      # The web UI (HTML + inline CSS + inline JS)
│   └── qr.min.js       # QR code generator library (~4KB)
├── server.py            # Modified: add render_GET on RootResource, static child
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
