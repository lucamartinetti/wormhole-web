# Simplify Send API Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the two-step send flow, making `PUT /send` the only send path. Delete session management. Simplify streaming, server, sender, web UI, and tests.

**Architecture:** Delete `session.py`, `SendNewResource`, `SendResource`, `_handle_twostep_send`, `start_key_exchange`. Rewrite `_handle_inline_send` to not use sessions. Update web UI JS to single XHR PUT. Update E2E tests to use `curl -T`.

**Tech Stack:** Python, Twisted, magic-wormhole

**Spec:** `docs/superpowers/specs/2026-03-17-simplify-send-api-design.md`

---

## Chunk 1: Backend simplification

### Task 1: Remove start_key_exchange from sender.py

**Files:**
- Modify: `src/wormhole_web/sender.py`

- [ ] **Step 1: Remove start_key_exchange function**

Delete the `start_key_exchange` function (lines 29-38). Keep `create_send_session` and `complete_send`.

- [ ] **Step 2: Update complete_send to accept a plain wormhole instead of key_exchange_d**

`complete_send` currently takes a `key_exchange_d` parameter (a Deferred from `start_key_exchange`). Now it should do the PAKE itself. Replace the function signature and the first part of the body:

Replace:
```python
@defer.inlineCallbacks
def complete_send(wormhole, key_exchange_d, filename, filesize, reactor, timeout=120):
    """Complete the send protocol after key exchange.

    Awaits the key exchange Deferred (may already be resolved),
    then sends transit hints + file offer, waits for receiver's
    response, and establishes transit.

    Args:
        wormhole: The wormhole object.
        key_exchange_d: Deferred from start_key_exchange (may be resolved).
        filename: Name of the file being sent.
        filesize: Size in bytes.
        reactor: The Twisted reactor.
        timeout: Seconds to wait before giving up.

    Returns:
        Connection: the transit connection, ready for send_record().
    """
    w = wormhole
    try:
        # Wait for PAKE to complete (with timeout starting now)
        yield with_timeout(
            key_exchange_d, timeout, reactor,
            "Timed out waiting for receiver"
        )
```

With:
```python
@defer.inlineCallbacks
def complete_send(wormhole, filename, filesize, reactor, timeout=120):
    """Do PAKE key exchange, send transit hints + file offer, establish transit.

    Args:
        wormhole: The wormhole object (from create_send_session).
        filename: Name of the file being sent.
        filesize: Size in bytes.
        reactor: The Twisted reactor.
        timeout: Seconds to wait before giving up.

    Returns:
        Connection: the transit connection, ready for send_record().
    """
    w = wormhole
    try:
        # PAKE key exchange (with timeout)
        yield with_timeout(
            w.get_unverified_key(), timeout, reactor,
            "Timed out waiting for receiver"
        )
        yield w.get_verifier()
```

The rest of `complete_send` stays the same (transit setup, offer, ack, connect).

- [ ] **Step 3: Verify imports**

Run: `cd /home/luca/github/wormhole-web && uv run python -c "from wormhole_web.sender import create_send_session, complete_send; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add src/wormhole_web/sender.py
git commit -m "refactor: remove start_key_exchange, inline PAKE into complete_send"
```

---

### Task 2: Simplify streaming.py — remove two-step, remove sessions

**Files:**
- Modify: `src/wormhole_web/streaming.py`

- [ ] **Step 1: Update imports**

Replace:
```python
from wormhole_web.sender import (
    SendError,
    create_send_session,
    start_key_exchange,
    complete_send,
)
```
With:
```python
from wormhole_web.sender import (
    SendError,
    create_send_session,
    complete_send,
)
```

- [ ] **Step 2: Simplify gotLength — remove two-step path detection**

In `gotLength`, replace the path detection block (lines 104-117):

```python
        # Determine which send path
        path_str = path.decode("utf-8", errors="replace")
        if path_str == "/send":
            mode = "inline"
            code = None
        elif path_str.startswith("/send/"):
            code = path_str[len("/send/"):]
            if not code or code == "new":
                super().gotLength(length)
                return
            mode = "two-step"
        else:
            super().gotLength(length)
            return
```

With:
```python
        path_str = path.decode("utf-8", errors="replace")
        if path_str != "/send":
            super().gotLength(length)
            return
```

- [ ] **Step 3: Remove mode branching at end of gotLength**

Replace:
```python
        # Fire background handler (do NOT yield)
        if mode == "inline":
            self._handle_inline_send(filename, filesize)
        else:
            self._handle_twostep_send(code, filename, filesize)
```
With:
```python
        # Fire background handler (do NOT yield)
        self._handle_inline_send(filename, filesize)
```

- [ ] **Step 4: Rewrite _handle_inline_send to not use sessions**

Replace the entire `_handle_inline_send` method with:

```python
    @defer.inlineCallbacks
    def _handle_inline_send(self, filename, filesize):
        """Background chain for PUT /send (inline flow)."""
        finished = self._finished_flag
        queue = self._chunk_queue
        site = self.channel.site
        reactor = site.resource._reactor
        transfer_timeout = site.resource._transfer_timeout
        wormhole = None

        try:
            code, wormhole = yield create_send_session(reactor)

            if finished[0]:
                yield wormhole.close()
                return

            self.setResponseCode(200)
            self.setHeader(b"content-type", b"text/plain")
            self.write(f"wormhole receive {code}\n".encode())
            self.write(b"waiting for receiver...\n")

            connection = yield complete_send(
                wormhole, filename, filesize, reactor,
                timeout=transfer_timeout,
            )

            if finished[0]:
                connection.close()
                yield wormhole.close()
                return

            self.write(b"transferring...\n")

            yield _send_data_from_queue(connection, queue)

            if not finished[0]:
                self.write(b"transfer complete\n")
                self.finish()
                finished[0] = True

            yield wormhole.close()

        except Exception as e:
            if not finished[0]:
                self.write(f"error: {e}\n".encode())
                self.finish()
                finished[0] = True
            if wormhole:
                try:
                    yield wormhole.close()
                except Exception:
                    pass
```

- [ ] **Step 5: Delete _handle_twostep_send entirely**

Remove the entire `_handle_twostep_send` method (lines 263-331).

- [ ] **Step 6: Verify imports**

Run: `cd /home/luca/github/wormhole-web && uv run python -c "from wormhole_web.streaming import StreamingRequest, ChunkQueue; print('ok')"`
Expected: `ok`

- [ ] **Step 7: Commit**

```bash
git add src/wormhole_web/streaming.py
git commit -m "refactor: remove two-step send, remove session usage from streaming"
```

---

### Task 3: Simplify server.py — remove SendNewResource, SendResource, sessions

**Files:**
- Modify: `src/wormhole_web/server.py`

- [ ] **Step 1: Remove session and send-related imports and classes**

Remove these imports:
```python
from wormhole_web.sender import create_send_session, start_key_exchange
from wormhole_web.session import SessionManager
```

Remove these classes entirely:
- `SendResource` (lines 237-246)
- `SendNewResource` (lines 249-287)

- [ ] **Step 2: Remove unused constants imports**

Remove `DEFAULT_MAX_SESSIONS` and `DEFAULT_SESSION_TTL` from the constants import.

- [ ] **Step 3: Simplify RootResource — remove session_manager parameter**

Replace `RootResource.__init__`:

```python
    def __init__(self, reactor=None, transfer_timeout=120):
        super().__init__()
        self._reactor = reactor or default_reactor
        self._transfer_timeout = transfer_timeout

        # Static files (web UI)
        static_dir = os.path.join(os.path.dirname(__file__), "static")
        if os.path.isdir(static_dir):
            index_path = os.path.join(static_dir, "index.html")
            if os.path.isfile(index_path):
                with open(index_path, "rb") as f:
                    self._index_html = f.read()
            else:
                self._index_html = None
            self.putChild(b"static", static.File(static_dir))
        else:
            self._index_html = None

        if self._index_html:
            self.putChild(b"", _IndexResource(self._index_html))

        self.putChild(b"health", HealthResource())
        self.putChild(
            b"receive", ReceiveResource(self._reactor, self._transfer_timeout)
        )
```

Note: no more `putChild(b"send", ...)` — PUT /send is handled by `StreamingRequest` before the resource tree is consulted.

- [ ] **Step 4: Simplify make_site**

Replace:
```python
def make_site(reactor=None, max_sessions=128, session_ttl=60, transfer_timeout=120):
    """Create and return a Twisted Site with all resources wired up."""
    reactor = reactor or default_reactor
    session_manager = SessionManager(
        max_sessions=max_sessions,
        session_ttl=session_ttl,
        reactor=reactor,
    )
    root = RootResource(session_manager, reactor, transfer_timeout)
    site = server.Site(root)
    site.requestFactory = StreamingRequest
    return site
```

With:
```python
def make_site(reactor=None, transfer_timeout=120):
    """Create and return a Twisted Site with all resources wired up."""
    reactor = reactor or default_reactor
    root = RootResource(reactor, transfer_timeout)
    site = server.Site(root)
    site.requestFactory = StreamingRequest
    return site
```

- [ ] **Step 5: Simplify CLI args**

Remove `--max-sessions` and `--session-ttl` from argparse. Update the `make_site` call:

```python
    site = make_site(
        reactor=default_reactor,
        transfer_timeout=args.transfer_timeout,
    )
```

- [ ] **Step 6: Run unit tests**

Run: `cd /home/luca/github/wormhole-web && uv run pytest tests/test_server.py tests/test_util.py tests/test_streaming.py -v`
Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add src/wormhole_web/server.py
git commit -m "refactor: remove SendNewResource, SendResource, session management from server"
```

---

### Task 4: Delete session.py and test_session.py

**Files:**
- Delete: `src/wormhole_web/session.py`
- Delete: `tests/test_session.py`

- [ ] **Step 1: Delete files**

```bash
rm src/wormhole_web/session.py tests/test_session.py
```

- [ ] **Step 2: Verify no remaining imports**

Run: `cd /home/luca/github/wormhole-web && grep -r "session" src/wormhole_web/ --include="*.py" | grep -v __pycache__`
Expected: no hits (or only comments)

- [ ] **Step 3: Run remaining unit tests**

Run: `cd /home/luca/github/wormhole-web && uv run pytest tests/test_server.py tests/test_util.py tests/test_streaming.py -v`
Expected: All pass.

- [ ] **Step 4: Commit**

```bash
git add -u
git commit -m "refactor: delete session.py and test_session.py"
```

---

## Chunk 2: Web UI and tests

### Task 5: Update web UI to use inline PUT /send

**Files:**
- Modify: `src/wormhole_web/static/index.html`

- [ ] **Step 1: Replace two-step JS with single XHR**

Replace the entire `startSend` function (currently does `fetch POST /send/new` then `uploadFile`) with:

```javascript
function startSend(file) {
  hide('send-initial');
  show('send-status');

  document.getElementById('send-filename').textContent = file.name;
  document.getElementById('send-filesize').textContent = formatSize(file.size);
  document.getElementById('send-status-text').textContent = 'uploading...';
  document.getElementById('send-status-text').className = 'status-text';
  document.getElementById('send-progress').style.width = '0%';
  document.getElementById('send-progress').className = 'progress-bar';
  hide('send-code-section');

  const xhr = new XMLHttpRequest();
  sendXhr = xhr;

  xhr.upload.onprogress = function(e) {
    if (e.lengthComputable) {
      const pct = Math.round((e.loaded / e.total) * 100);
      document.getElementById('send-progress').style.width = pct + '%';
    }
  };

  let lastResponseLen = 0;
  xhr.onprogress = function() {
    const text = xhr.responseText.substring(lastResponseLen);
    lastResponseLen = xhr.responseText.length;
    const lines = text.split('\n').filter(l => l.trim());
    for (const line of lines) {
      if (line.startsWith('wormhole receive ')) {
        currentCode = line.substring('wormhole receive '.length).trim();
        show('send-code-section');
        setQrMode(true);
        if (navigator.share) {
          document.getElementById('btn-share').style.display = '';
        }
        document.getElementById('send-status-text').textContent = 'waiting for receiver...';
      } else if (line.startsWith('waiting for receiver')) {
        document.getElementById('send-status-text').textContent = 'waiting for receiver...';
      } else if (line.startsWith('transferring')) {
        document.getElementById('send-status-text').textContent = 'transferring...';
      } else if (line.startsWith('transfer complete')) {
        markSendComplete();
      } else if (line.startsWith('error:')) {
        document.getElementById('send-status-text').textContent = line;
        document.getElementById('send-status-text').className = 'status-text error';
      }
    }
  };

  xhr.onload = function() {
    sendXhr = null;
    if (xhr.status !== 200) {
      document.getElementById('send-status-text').textContent = 'Error: server returned ' + xhr.status;
      document.getElementById('send-status-text').className = 'status-text error';
      return;
    }
    // Parse full response on load (Safari fallback — onprogress may not fire)
    const lines = xhr.responseText.split('\n');
    for (const line of lines) {
      if (line.startsWith('wormhole receive ') && !currentCode) {
        currentCode = line.substring('wormhole receive '.length).trim();
        show('send-code-section');
        setQrMode(true);
      }
    }
    if (xhr.responseText.includes('transfer complete')) {
      markSendComplete();
    }
  };

  xhr.onerror = function() {
    sendXhr = null;
    document.getElementById('send-status-text').textContent = 'Connection error';
    document.getElementById('send-status-text').className = 'status-text error';
  };

  xhr.open('PUT', '/send');
  xhr.setRequestHeader('X-Wormhole-Filename', file.name);
  xhr.send(file);
}
```

- [ ] **Step 2: Remove the old uploadFile function**

Delete the `uploadFile(file, code)` function entirely — it's been inlined into `startSend`.

- [ ] **Step 3: Verify the page loads**

Start server, open browser, check no JS errors:
Run: `cd /home/luca/github/wormhole-web && timeout 5 bash -c 'uv run wormhole-web --port 18099 & sleep 2; curl -s http://localhost:18099/ | grep "wormhole-web"; kill %1 2>/dev/null'`
Expected: output contains `wormhole-web`

- [ ] **Step 4: Commit**

```bash
git add src/wormhole_web/static/index.html
git commit -m "feat: web UI uses inline PUT /send (single request, no two-step)"
```

---

### Task 6: Update integration tests

**Files:**
- Modify: `tests/test_integration.py`

- [ ] **Step 1: Remove two-step test classes**

Delete `TestSendPath` and `TestSendPathDelayed` classes entirely. Keep `TestReceivePath` and `TestInlineSend` (rename to `TestSendPath`).

- [ ] **Step 2: Update server_url fixture if it uses session params**

Check `make_site` calls in tests — remove `max_sessions` and `session_ttl` params if present. The subprocess-based server fixture doesn't call `make_site` directly, so this may not apply.

- [ ] **Step 3: Run integration tests**

Run: `cd /home/luca/github/wormhole-web && uv run pytest tests/test_integration.py -v --timeout=120`
Expected: All pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: remove two-step send tests, keep inline send test"
```

---

### Task 7: Update E2E tests

**Files:**
- Modify: `tests/test_e2e.py`

- [ ] **Step 1: Update test_web_send_cli_receive in TestPythonCLI**

Replace the two-step flow (POST /send/new + curl -T to /send/<code>) with inline:

```python
    def test_web_send_cli_receive(self, server_url, test_file):
        """curl send via our server → wormhole receive."""
        src_path, expected_hash, size = test_file

        # Start inline send (curl -T to /send, reads code from first line)
        upload = subprocess.Popen(
            ["curl", "-sN", "-T", src_path,
             "-H", f"X-Wormhole-Filename: {os.path.basename(src_path)}",
             f"{server_url}/send"],
            stdout=subprocess.PIPE,
            text=True,
        )

        # Read first line to get the code
        first_line = upload.stdout.readline().strip()
        assert first_line.startswith("wormhole receive "), f"Bad first line: {first_line!r}"
        code = first_line.split()[-1]

        # Receive with wormhole CLI
        with tempfile.TemporaryDirectory() as tmpdir:
            recv = subprocess.run(
                ["wormhole", "receive", "--accept-file",
                 "-o", tmpdir, code],
                capture_output=True, text=True, timeout=60,
            )
            assert recv.returncode == 0

            files = os.listdir(tmpdir)
            assert len(files) == 1
            with open(os.path.join(tmpdir, files[0]), "rb") as f:
                assert hashlib.sha256(f.read()).hexdigest() == expected_hash

        stdout = upload.communicate(timeout=10)[0]
        assert "transfer complete" in stdout
```

- [ ] **Step 2: Update test_web_send_rs_receive in TestRustCLI**

Same pattern — replace POST /send/new + curl to /send/<code> with inline curl -T to /send:

```python
    def test_web_send_rs_receive(self, server_url, test_file):
        """curl send via our server → wormhole-rs receive."""
        src_path, expected_hash, size = test_file

        upload = subprocess.Popen(
            ["curl", "-sN", "-T", src_path,
             "-H", f"X-Wormhole-Filename: {os.path.basename(src_path)}",
             f"{server_url}/send"],
            stdout=subprocess.PIPE,
            text=True,
        )

        first_line = upload.stdout.readline().strip()
        assert first_line.startswith("wormhole receive "), f"Bad first line: {first_line!r}"
        code = first_line.split()[-1]

        with tempfile.TemporaryDirectory() as tmpdir:
            recv = subprocess.run(
                ["wormhole-rs", "receive", "--noconfirm",
                 "--out-dir", tmpdir, code],
                capture_output=True, text=True, timeout=60,
            )
            assert recv.returncode == 0

            files = os.listdir(tmpdir)
            assert len(files) == 1
            with open(os.path.join(tmpdir, files[0]), "rb") as f:
                assert hashlib.sha256(f.read()).hexdigest() == expected_hash

        stdout = upload.communicate(timeout=10)[0]
        assert "transfer complete" in stdout
```

- [ ] **Step 3: Run E2E tests**

Run: `cd /home/luca/github/wormhole-web && uv run pytest tests/test_e2e.py -v --timeout=120`
Expected: All 4 pass.

- [ ] **Step 4: Run full suite**

Run: `cd /home/luca/github/wormhole-web && uv run pytest tests/ -v --timeout=120`
Expected: All tests pass (fewer total — session tests removed).

- [ ] **Step 5: Commit**

```bash
git add tests/test_e2e.py
git commit -m "test: update E2E send tests to use inline PUT /send"
```

---

### Task 8: Update README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Remove two-step section and update API table**

Remove the "Two-step send (programmatic)" section. Update the API table to only show `PUT /send`, `GET /receive/<code>`, `GET /health`, `GET /`. Remove `--max-sessions` and `--session-ttl` from the Configuration section.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: update README for simplified send API"
```
