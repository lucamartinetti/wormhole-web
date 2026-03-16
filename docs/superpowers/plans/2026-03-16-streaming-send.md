# Streaming Send Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** True streaming for the send path — upload body flows directly to wormhole transit via bounded ChunkQueue with backpressure, supporting TB-scale transfers with constant memory.

**Architecture:** A custom `StreamingRequest` subclass intercepts PUT requests to `/send` paths in `gotLength` (before body buffering), fires a background async chain for wormhole protocol, and pipes body chunks from `handleContentChunk` through a `ChunkQueue` to wormhole transit.

**Tech Stack:** Python, Twisted (`twisted.web.server.Request` subclass), magic-wormhole

**Spec:** `docs/superpowers/specs/2026-03-16-streaming-send-design.md`

---

## Chunk 1: ChunkQueue and StreamingRequest

### Task 1: Create ChunkQueue

**Files:**
- Create: `src/wormhole_web/streaming.py`
- Create: `tests/test_streaming.py`

- [ ] **Step 1: Write ChunkQueue tests**

```python
# tests/test_streaming.py
"""Tests for ChunkQueue and StreamingRequest."""

from unittest.mock import MagicMock, call

from twisted.internet import defer, task
from twisted.trial import unittest

from wormhole_web.streaming import ChunkQueue


class TestChunkQueue(unittest.TestCase):
    def test_put_and_get(self):
        q = ChunkQueue(max_chunks=16)
        q.put(b"hello")
        d = q.get()
        self.assertEqual(self.successResultOf(d), b"hello")

    def test_get_before_put_waits(self):
        q = ChunkQueue(max_chunks=16)
        d = q.get()
        self.assertNoResult(d)
        q.put(b"hello")
        self.assertEqual(self.successResultOf(d), b"hello")

    def test_finish_signals_eof(self):
        q = ChunkQueue(max_chunks=16)
        d = q.get()
        q.finish()
        self.assertIsNone(self.successResultOf(d))

    def test_get_after_finish_returns_none(self):
        q = ChunkQueue(max_chunks=16)
        q.put(b"data")
        q.finish()
        d1 = q.get()
        self.assertEqual(self.successResultOf(d1), b"data")
        d2 = q.get()
        self.assertIsNone(self.successResultOf(d2))

    def test_backpressure_pauses_transport(self):
        transport = MagicMock()
        q = ChunkQueue(max_chunks=2, transport=transport)
        q.put(b"a")
        q.put(b"b")  # queue is full
        transport.pauseProducing.assert_called_once()

    def test_get_resumes_transport(self):
        transport = MagicMock()
        q = ChunkQueue(max_chunks=2, transport=transport)
        q.put(b"a")
        q.put(b"b")  # pauses
        transport.pauseProducing.assert_called_once()
        q.get()  # drains one, should resume
        transport.resumeProducing.assert_called_once()

    def test_error_errbacks_pending_get(self):
        q = ChunkQueue(max_chunks=16)
        d = q.get()
        q.error(Exception("boom"))
        f = self.failureResultOf(d)
        self.assertIn("boom", str(f.value))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/luca/github/wormhole-web && uv run pytest tests/test_streaming.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement ChunkQueue**

```python
# src/wormhole_web/streaming.py
"""Streaming request handling for wormhole-web send path."""

import io
from collections import deque

from twisted.internet import defer
from twisted.web import server


class ChunkQueue:
    """Bounded async queue with backpressure for streaming uploads.

    Connects handleContentChunk (producer) to wormhole transit (consumer).
    """

    def __init__(self, max_chunks=16, transport=None):
        self._queue = deque()
        self._max_chunks = max_chunks
        self._transport = transport
        self._waiting = None  # Deferred waiting for data
        self._finished = False
        self._paused = False
        self._error = None

    def put(self, data: bytes):
        """Add a chunk. Pauses transport if queue is full."""
        if self._waiting is not None:
            # Consumer is waiting — deliver directly
            d, self._waiting = self._waiting, None
            d.callback(data)
            return

        self._queue.append(data)
        if len(self._queue) >= self._max_chunks and not self._paused:
            if self._transport:
                self._transport.pauseProducing()
                self._paused = True

    def get(self):
        """Get next chunk. Returns Deferred[bytes | None].

        Returns None for EOF. Resumes transport if queue drops below limit.
        """
        if self._queue:
            data = self._queue.popleft()
            # Resume transport if we dropped below the limit
            if self._paused and len(self._queue) < self._max_chunks:
                if self._transport:
                    self._transport.resumeProducing()
                    self._paused = False
            return defer.succeed(data)

        if self._error is not None:
            return defer.fail(self._error)

        if self._finished:
            return defer.succeed(None)

        # No data available — return a Deferred that waits
        self._waiting = defer.Deferred()
        return self._waiting

    def finish(self):
        """Signal EOF. Pending get() fires with None."""
        self._finished = True
        if self._waiting is not None:
            d, self._waiting = self._waiting, None
            d.callback(None)

    def error(self, reason):
        """Signal error. Pending get() errbacks."""
        self._error = reason
        self._finished = True
        if self._waiting is not None:
            d, self._waiting = self._waiting, None
            d.errback(reason)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/luca/github/wormhole-web && uv run pytest tests/test_streaming.py -v`
Expected: All 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wormhole_web/streaming.py tests/test_streaming.py
git commit -m "feat: ChunkQueue for streaming upload backpressure"
```

---

### Task 2: Create StreamingRequest

**Files:**
- Modify: `src/wormhole_web/streaming.py`

This is the core of the feature. `StreamingRequest` overrides `gotLength`, `handleContentChunk`, and `requestReceived` to intercept PUT requests to `/send` paths and handle them without buffering.

- [ ] **Step 1: Add StreamingRequest to streaming.py**

Append to `src/wormhole_web/streaming.py`:

```python
from wormhole_web.sender import (
    SendError,
    create_send_session,
    start_key_exchange,
    complete_send,
)
from wormhole_web.util import sanitize_filename, WormholeTimeout


class StreamingRequest(server.Request):
    """Custom Request that streams PUT /send bodies without buffering."""

    _streaming = False
    _chunk_queue = None
    _finished_flag = None

    def gotLength(self, length):
        """Called after headers parsed, before body. Start streaming if PUT /send."""
        path = self.channel._path
        command = self.channel._command

        if command != b"PUT" or not path.startswith(b"/send"):
            super().gotLength(length)
            return

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

        # Get filesize from Content-Length or X-Wormhole-Filesize
        filesize = None
        if length is not None:
            filesize = length
        else:
            # Check X-Wormhole-Filesize for chunked transfers
            fs_header = self.getHeader(b"x-wormhole-filesize")
            if fs_header:
                try:
                    filesize = int(fs_header)
                except ValueError:
                    pass

        if filesize is None:
            # Can't proceed without filesize — respond 411
            self._streaming = True
            self.content = io.BytesIO()
            self.method = command
            self.uri = path
            self.path = path
            self.setResponseCode(411)
            self.setHeader(b"content-type", b"text/plain")
            self.write(b"Content-Length or X-Wormhole-Filesize header required\n")
            self.finish()
            return

        # Enter streaming mode
        self._streaming = True
        self.content = io.BytesIO()  # sentinel
        self.method = command
        self.uri = path
        self.path = path

        self._finished_flag = [False]
        self._chunk_queue = ChunkQueue(max_chunks=16, transport=self.transport)

        # Wire disconnect detection
        def on_disconnect(reason):
            if not self._finished_flag[0]:
                self._finished_flag[0] = True
                self._chunk_queue.error(
                    Exception("sender disconnected")
                )

        self.notifyFinish().addErrback(on_disconnect)

        # Get filename
        raw_filename = self.requestHeaders.getRawHeaders(b"x-wormhole-filename")
        filename = "upload"
        if raw_filename:
            filename = sanitize_filename(raw_filename[0].decode("utf-8", errors="replace"))

        # Fire background handler (do NOT yield)
        if mode == "inline":
            self._handle_inline_send(filename, filesize)
        else:
            self._handle_twostep_send(code, filename, filesize)

    def handleContentChunk(self, data):
        if self._streaming:
            if self._chunk_queue and not self._finished_flag[0]:
                self._chunk_queue.put(data)
            return
        super().handleContentChunk(data)

    def requestReceived(self, command, path, version):
        if self._streaming:
            if self._chunk_queue:
                self._chunk_queue.finish()
            # Must set _handlingRequest for HTTP/1.1 keep-alive
            self.channel._handlingRequest = True
            return
        super().requestReceived(command, path, version)

    @defer.inlineCallbacks
    def _handle_inline_send(self, filename, filesize):
        """Background chain for PUT /send (inline flow)."""
        finished = self._finished_flag
        queue = self._chunk_queue
        site = self.channel.site
        session_manager = site.resource._session_manager
        reactor = site.resource._reactor
        transfer_timeout = site.resource._transfer_timeout

        try:
            if session_manager.is_full():
                if not finished[0]:
                    self.setResponseCode(503)
                    self.setHeader(b"content-type", b"text/plain")
                    self.write(b"too many active sessions\n")
                    self.finish()
                    finished[0] = True
                return

            code, wormhole = yield create_send_session(reactor)
            session = session_manager.create(code, wormhole)

            # Start PAKE
            d = start_key_exchange(wormhole, reactor)
            d.addErrback(lambda f: f)
            session.key_exchange_d = d

            if finished[0]:
                session_manager.remove(code)
                yield wormhole.close()
                return

            self.setResponseCode(200)
            self.setHeader(b"content-type", b"text/plain")
            self.write(f"wormhole receive {code}\n".encode())
            self.write(b"waiting for receiver...\n")

            connection = yield complete_send(
                wormhole, session.key_exchange_d,
                filename, filesize, reactor,
                timeout=transfer_timeout,
            )

            if finished[0]:
                connection.close()
                session_manager.remove(code)
                yield wormhole.close()
                return

            self.write(b"transferring...\n")

            yield _send_data_from_queue(connection, queue)

            if not finished[0]:
                self.write(b"transfer complete\n")
                self.finish()
                finished[0] = True

            session_manager.remove(code)
            yield wormhole.close()

        except Exception as e:
            if not finished[0]:
                self.write(f"error: {e}\n".encode())
                self.finish()
                finished[0] = True

    @defer.inlineCallbacks
    def _handle_twostep_send(self, code, filename, filesize):
        """Background chain for PUT /send/<code> (two-step flow)."""
        finished = self._finished_flag
        queue = self._chunk_queue
        site = self.channel.site
        session_manager = site.resource._session_manager
        reactor = site.resource._reactor
        transfer_timeout = site.resource._transfer_timeout

        session = session_manager.get(code)
        if session is None:
            if not finished[0]:
                self.setResponseCode(404)
                self.setHeader(b"content-type", b"text/plain")
                self.write(b"unknown or expired code\n")
                self.finish()
                finished[0] = True
            return

        if not session.claim_upload():
            if not finished[0]:
                self.setResponseCode(409)
                self.setHeader(b"content-type", b"text/plain")
                self.write(b"upload already in progress\n")
                self.finish()
                finished[0] = True
            return

        try:
            self.setResponseCode(200)
            self.setHeader(b"content-type", b"text/plain")
            self.write(code.encode() + b"\n")
            self.write(b"waiting for receiver...\n")

            connection = yield complete_send(
                session.wormhole, session.key_exchange_d,
                filename, filesize, reactor,
                timeout=transfer_timeout,
            )

            if finished[0]:
                connection.close()
                session_manager.remove(code)
                yield session.wormhole.close()
                return

            self.write(b"transferring...\n")

            yield _send_data_from_queue(connection, queue)

            if not finished[0]:
                self.write(b"transfer complete\n")
                self.finish()
                finished[0] = True

            session_manager.remove(code)
            yield session.wormhole.close()

        except Exception as e:
            if not finished[0]:
                self.write(f"error: {e}\n".encode())
                self.finish()
                finished[0] = True
            session_manager.remove(code)
            try:
                yield session.wormhole.close()
            except Exception:
                pass


@defer.inlineCallbacks
def _send_data_from_queue(connection, queue):
    """Consume chunks from queue and pipe through wormhole transit."""
    while True:
        chunk = yield queue.get()
        if chunk is None:
            break
        connection.send_record(chunk)

    # Wait for receiver ack
    try:
        yield connection.receive_record()
    except Exception:
        pass
    connection.close()
```

- [ ] **Step 2: Verify imports**

Run: `cd /home/luca/github/wormhole-web && uv run python -c "from wormhole_web.streaming import StreamingRequest, ChunkQueue; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add src/wormhole_web/streaming.py
git commit -m "feat: StreamingRequest for true upload streaming"
```

---

## Chunk 2: Server Wiring and Cleanup

### Task 3: Wire StreamingRequest into server, remove old send code

**Files:**
- Modify: `src/wormhole_web/server.py`

- [ ] **Step 1: Update server.py**

Replace the entire send section. Changes:
1. Import `StreamingRequest` from streaming module
2. `make_site` passes `requestFactory=StreamingRequest` to `Site`
3. Store `session_manager`, `reactor`, `transfer_timeout` on `RootResource` as public attributes (StreamingRequest accesses them via `self.channel.site.resource`)
4. Remove `SendResource.render_PUT` and `_do_redirect`
5. Remove `SendCodeResource` class entirely
6. Remove `getChild` from `SendResource` (no longer needed for PUT — StreamingRequest handles it)

In `server.py`, replace everything from `# --- Send resources ---` through the end of `SendCodeResource` (lines 195-387) with:

```python
# --- Send resources ---


class SendResource(resource.Resource):
    """Container for /send/new. PUT /send and PUT /send/<code> are handled
    by StreamingRequest before the body is buffered."""

    def __init__(self, session_manager, reactor, transfer_timeout=120):
        super().__init__()
        self._session_manager = session_manager
        self._reactor = reactor
        self._transfer_timeout = transfer_timeout
        self.putChild(b"new", SendNewResource(session_manager, reactor))


class SendNewResource(resource.Resource):
    """POST /send/new — allocate a wormhole code."""
    isLeaf = True

    def __init__(self, session_manager, reactor):
        super().__init__()
        self._session_manager = session_manager
        self._reactor = reactor

    def render_POST(self, request):
        self._do_create(request)
        return server.NOT_DONE_YET

    @defer.inlineCallbacks
    def _do_create(self, request):
        try:
            if self._session_manager.is_full():
                request.setResponseCode(503)
                request.setHeader(b"content-type", b"text/plain")
                request.write(b"too many active sessions\n")
                request.finish()
                return

            code, wormhole = yield create_send_session(self._reactor)
            session = self._session_manager.create(code, wormhole)

            # Start PAKE in background — store Deferred for Phase 2
            d = start_key_exchange(wormhole, self._reactor)
            d.addErrback(lambda f: f)
            session.key_exchange_d = d

            request.setHeader(b"content-type", b"text/plain")
            request.write(code.encode() + b"\n")
            request.finish()
        except Exception as e:
            request.setResponseCode(500)
            request.setHeader(b"content-type", b"text/plain")
            request.write(f"error: {e}\n".encode())
            request.finish()
```

Also update `make_site` to use `StreamingRequest`:

```python
from wormhole_web.streaming import StreamingRequest

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

And remove the unused `SessionState` import (no longer needed in server.py — `SendCodeResource` used it but is now gone). Remove from imports:
- `from wormhole_web.session import SessionManager, SessionState` → `from wormhole_web.session import SessionManager`

Also remove the unused `SendError` and `complete_send` imports since they're now only used in streaming.py:
- `from wormhole_web.sender import SendError, create_send_session, start_key_exchange, complete_send` → `from wormhole_web.sender import create_send_session, start_key_exchange`

- [ ] **Step 2: Run unit tests**

Run: `cd /home/luca/github/wormhole-web && uv run pytest tests/test_server.py tests/test_session.py tests/test_util.py tests/test_streaming.py -v`
Expected: All tests PASS.

- [ ] **Step 3: Smoke test**

Run: `cd /home/luca/github/wormhole-web && timeout 5 uv run python -m wormhole_web.server --port 18080 & sleep 1 && curl -s http://localhost:18080/health; kill %1 2>/dev/null; wait`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add src/wormhole_web/server.py
git commit -m "feat: wire StreamingRequest, remove buffered send handlers"
```

---

## Chunk 3: Tests

### Task 4: Run existing integration and E2E tests, fix issues

**Files:**
- No changes expected — just verify everything works.

- [ ] **Step 1: Run integration tests**

Run: `cd /home/luca/github/wormhole-web && uv run pytest tests/test_integration.py -v --timeout=120`

The two-step send test should still pass (it uses `POST /send/new` + `PUT /send/<code>`). If it fails, debug and fix.

- [ ] **Step 2: Run E2E tests**

Run: `cd /home/luca/github/wormhole-web && uv run pytest tests/test_e2e.py -v --timeout=120`

The E2E tests that use the two-step flow should pass. If any fail, debug and fix.

- [ ] **Step 3: Run full suite**

Run: `cd /home/luca/github/wormhole-web && uv run pytest tests/ -v --timeout=120`
Expected: All tests PASS.

- [ ] **Step 4: Commit any fixes**

```bash
git add -u
git commit -m "fix: adjust tests for streaming send"
```

---

### Task 5: Add inline PUT /send integration test

**Files:**
- Modify: `tests/test_integration.py`

- [ ] **Step 1: Add inline send test**

Add a new test class to `tests/test_integration.py`:

```python
class TestInlineSend:
    """Test: PUT /send inline flow (no redirect, no two-step)."""

    def test_inline_send(self, server_url, test_data):
        src_path, expected_hash, size = test_data

        # Start inline send (curl -T file http://host/send)
        upload = subprocess.Popen(
            ["curl", "-sf", "-T", src_path,
             "-H", "X-Wormhole-Filename: testfile.bin",
             f"{server_url}/send"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Read the first line of output — should be "wormhole receive <code>"
        first_line = upload.stdout.readline().strip()
        assert first_line.startswith("wormhole receive "), f"Bad first line: {first_line!r}"
        code = first_line.split()[-1]
        assert "-" in code

        # Run receiver
        script = f'''
import os, sys, hashlib
from twisted.internet import defer, reactor
from wormhole import create
from wormhole.transit import TransitReceiver
from wormhole.util import dict_to_bytes, bytes_to_dict

APPID = "lothar.com/wormhole/text-or-file-xfer"
RELAY = "ws://relay.magic-wormhole.io:4000/v1"
TRANSIT = "tcp:transit.magic-wormhole.io:4001"

@defer.inlineCallbacks
def main():
    w = create(APPID, RELAY, reactor)
    w.set_code("{code}")

    yield w.get_unverified_key()
    yield w.get_verifier()

    tr = TransitReceiver(TRANSIT, no_listen=True, reactor=reactor)
    tr.set_transit_key(w.derive_key(APPID + "/transit-key", 32))
    our_hints = yield tr.get_connection_hints()
    w.send_message(dict_to_bytes({{
        "transit": {{"abilities-v1": tr.get_connection_abilities(), "hints-v1": our_hints}}
    }}))

    msg1 = bytes_to_dict((yield w.get_message()))
    msg2 = bytes_to_dict((yield w.get_message()))
    transit_msg = msg1 if "transit" in msg1 else msg2
    offer_msg = msg1 if "offer" in msg1 else msg2
    assert "offer" in offer_msg
    tr.add_connection_hints(transit_msg["transit"]["hints-v1"])
    w.send_message(dict_to_bytes({{"answer": {{"file_ack": "ok"}}}}))

    connection = yield tr.connect()
    received = b""
    while len(received) < {size}:
        record = yield connection.receive_record()
        received += record
    connection.close()
    yield w.close()

    actual_hash = hashlib.sha256(received).hexdigest()
    print(f"hash_match:{{actual_hash == '{expected_hash}'}}")
    print(f"size_match:{{len(received) == {size}}}")
    reactor.stop()

reactor.callWhenRunning(main)
reactor.run()
'''
        output = _run_wormhole_script(script, timeout=60)
        assert "hash_match:True" in output
        assert "size_match:True" in output

        stdout = upload.communicate(timeout=10)[0]
        assert "transfer complete" in stdout
```

- [ ] **Step 2: Run the new test**

Run: `cd /home/luca/github/wormhole-web && uv run pytest tests/test_integration.py::TestInlineSend -v --timeout=120`
Expected: PASS.

- [ ] **Step 3: Run full suite**

Run: `cd /home/luca/github/wormhole-web && uv run pytest tests/ -v --timeout=120`
Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add inline PUT /send integration test"
```

---

## Implementation Notes

### How StreamingRequest accesses server state

`StreamingRequest` needs the session manager, reactor, and transfer timeout. These are accessed via:
```python
site = self.channel.site        # the Site object
root = site.resource             # RootResource
session_manager = root._session_manager
reactor = root._reactor
transfer_timeout = root._transfer_timeout
```

This works because `Site.resource` is the root of the resource tree, and `RootResource` stores these as instance attributes.

### Why gotLength uses self.requestHeaders for X-Wormhole-Filesize

`self.getHeader()` works in `gotLength` because headers are already parsed. However, `self.getHeader` returns `str` (for `server.Request`), while `self.requestHeaders.getRawHeaders(b"...")` returns a list of bytes. The implementation uses `requestHeaders.getRawHeaders` because `getHeader` depends on `self.method` being set (which it isn't yet in `gotLength` for non-streaming paths).

Actually, looking at the Twisted source, `getHeader` on `http.Request` reads from `self.received_headers` (deprecated) or `self.requestHeaders`. It does NOT depend on `self.method`. So either approach works. The implementation uses `requestHeaders.getRawHeaders` for clarity.

### Backpressure target

`self.transport` on a Twisted `Request` is the underlying TCP transport. Calling `self.transport.pauseProducing()` directly stops the socket read, which is exactly what we want for upload backpressure. Do NOT use `self.channel.pauseProducing()` — that operates on the response producer chain, not the input.
