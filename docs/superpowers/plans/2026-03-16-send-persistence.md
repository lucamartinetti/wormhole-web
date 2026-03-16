# Send Persistence Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start PAKE key exchange at session creation time so the sender's upload stays available until a receiver connects.

**Architecture:** Split `prepare_send()` into `start_key_exchange()` (called at `POST /send/new`) and `complete_send()` (called from `PUT /send/<code>`). Store the PAKE Deferred in the Session object.

**Tech Stack:** Python, Twisted, magic-wormhole

**Spec:** `docs/superpowers/specs/2026-03-16-send-persistence-design.md`

---

## Chunk 1: Implementation

### Task 1: Split sender.py — start_key_exchange + complete_send

**Files:**
- Modify: `src/wormhole_web/sender.py`

- [ ] **Step 1: Replace `prepare_send` with `start_key_exchange` and `complete_send`**

Replace the entire `prepare_send` function with two new functions. Keep `create_send_session` unchanged.

```python
@defer.inlineCallbacks
def start_key_exchange(wormhole, reactor):
    """Start PAKE key exchange in the background.

    Returns a Deferred that fires when key exchange completes.
    Does NOT apply a timeout — the caller stores the Deferred and
    Phase 2 (complete_send) applies the timeout.
    """
    yield wormhole.get_unverified_key()
    yield wormhole.get_verifier()


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

        # Set up transit sender (derive_key requires PAKE to be done)
        ts = TransitSender(
            TRANSIT_RELAY,
            no_listen=True,
            reactor=reactor,
        )

        transit_key = w.derive_key(APPID + "/transit-key", TRANSIT_KEY_LENGTH)
        ts.set_transit_key(transit_key)

        # Send our transit hints
        our_abilities = ts.get_connection_abilities()
        our_hints = yield ts.get_connection_hints()
        w.send_message(dict_to_bytes({
            "transit": {
                "abilities-v1": our_abilities,
                "hints-v1": our_hints,
            }
        }))

        # Send file offer
        w.send_message(dict_to_bytes({
            "offer": {
                "file": {
                    "filename": filename,
                    "filesize": filesize,
                }
            }
        }))

        # Read two messages from receiver (either order)
        connect_d = None
        answer_msg = None

        for _ in range(2):
            msg_bytes = yield with_timeout(
                w.get_message(), timeout, reactor,
                "Timed out waiting for receiver response"
            )
            msg = bytes_to_dict(msg_bytes)

            if "transit" in msg:
                ts.add_connection_hints(msg["transit"].get("hints-v1", []))
                if connect_d is None:
                    connect_d = ts.connect()
            elif "answer" in msg:
                answer_msg = msg
            elif "error" in msg:
                raise SendError(f"Receiver rejected: {msg['error']}")

        if answer_msg is None:
            raise SendError("Never received file_ack from receiver")

        if "file_ack" not in answer_msg.get("answer", {}):
            raise SendError(f"Unexpected answer: {answer_msg}")

        # Establish transit connection
        if connect_d is None:
            connect_d = ts.connect()
        connection = yield with_timeout(
            connect_d, timeout, reactor,
            "Timed out establishing transit"
        )

        defer.returnValue(connection)
    except (SendError, WormholeTimeout):
        raise
    except Exception as e:
        raise SendError(f"Send failed: {e}") from e
```

- [ ] **Step 2: Verify imports still work**

Run: `cd /home/luca/github/wormhole-web && uv run python -c "from wormhole_web.sender import create_send_session, start_key_exchange, complete_send; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add src/wormhole_web/sender.py
git commit -m "refactor: split prepare_send into start_key_exchange + complete_send"
```

---

### Task 2: Add key_exchange_d to Session, fix _expire

**Files:**
- Modify: `src/wormhole_web/session.py`
- Modify: `tests/test_session.py`

- [ ] **Step 1: Add `key_exchange_d` field to Session**

In `Session.__init__`, add after `self._cleanup_timer = None`:

```python
        self.key_exchange_d = None
```

- [ ] **Step 2: Fix `_expire` to handle wormhole.close() Deferred**

Replace the `_expire` method:

```python
    def _expire(self, code: str):
        session = self._sessions.get(code)
        if session and session.state == SessionState.WAITING_FOR_UPLOAD:
            # wormhole.close() returns a Deferred; suppress any errback
            d = session.wormhole.close()
            if d is not None:
                d.addErrback(lambda f: None)
            self._sessions.pop(code, None)
```

- [ ] **Step 3: Run existing session tests**

Run: `cd /home/luca/github/wormhole-web && uv run pytest tests/test_session.py -v`
Expected: All 10 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/wormhole_web/session.py
git commit -m "feat: add key_exchange_d to Session, handle close() Deferred in _expire"
```

---

### Task 3: Wire up early PAKE in server.py

**Files:**
- Modify: `src/wormhole_web/server.py`

- [ ] **Step 1: Update imports**

Replace:
```python
from wormhole_web.sender import SendError, create_send_session, prepare_send
```
With:
```python
from wormhole_web.sender import SendError, create_send_session, start_key_exchange, complete_send
```

- [ ] **Step 2: Update `_do_create` in SendNewResource to start PAKE**

Replace the body of `_do_create` (lines 258-277):

```python
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
            d.addErrback(lambda f: f)  # pass-through: suppress unhandled warning, preserve Failure
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

- [ ] **Step 3: Update `_do_redirect` in SendResource to start PAKE**

Replace the body of `_do_redirect` (lines 220-241):

```python
    @defer.inlineCallbacks
    def _do_redirect(self, request):
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
            d.addErrback(lambda f: f)  # pass-through: suppress unhandled warning, preserve Failure
            session.key_exchange_d = d

            request.setResponseCode(307)
            request.setHeader(b"location", f"/send/{code}".encode())
            request.setHeader(b"content-type", b"text/plain")
            request.write(code.encode() + b"\n")
            request.finish()
        except Exception as e:
            request.setResponseCode(500)
            request.setHeader(b"content-type", b"text/plain")
            request.write(f"error: {e}\n".encode())
            request.finish()
```

- [ ] **Step 4: Update `_do_upload` in SendCodeResource to use complete_send**

Replace the `prepare_send` call (line 338-342):

```python
            connection = yield complete_send(
                session.wormhole, session.key_exchange_d,
                filename, filesize, self._reactor,
                timeout=self._transfer_timeout,
            )
```

- [ ] **Step 5: Run all unit tests**

Run: `cd /home/luca/github/wormhole-web && uv run pytest tests/test_util.py tests/test_session.py tests/test_server.py -v`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/wormhole_web/server.py
git commit -m "feat: start PAKE at session creation, use complete_send in upload handler"
```

---

### Task 4: Run integration and E2E tests

**Files:**
- No changes — just verify existing tests pass with the refactored code.

- [ ] **Step 1: Run integration tests**

Run: `cd /home/luca/github/wormhole-web && uv run pytest tests/test_integration.py -v --timeout=120`
Expected: Both tests PASS.

- [ ] **Step 2: Run E2E tests**

Run: `cd /home/luca/github/wormhole-web && uv run pytest tests/test_e2e.py -v --timeout=120`
Expected: All 4 tests PASS (or skip if CLIs not installed).

- [ ] **Step 3: Run full test suite**

Run: `cd /home/luca/github/wormhole-web && uv run pytest tests/ -v --timeout=120`
Expected: All 25 tests PASS.

---

### Task 5: Add delayed-receiver integration test

**Files:**
- Modify: `tests/test_integration.py`

- [ ] **Step 1: Add a test where receiver connects 2 seconds after upload**

Add a new test class to `tests/test_integration.py`:

```python
class TestSendPathDelayed:
    """Test: upload completes, receiver connects later."""

    def test_send_file_delayed_receiver(self, server_url, test_data):
        src_path, expected_hash, size = test_data

        # Step 1: Get a code
        result = subprocess.run(
            ["curl", "-sf", "-X", "POST", f"{server_url}/send/new"],
            capture_output=True, text=True, timeout=30,
        )
        code = result.stdout.strip()
        assert "-" in code, f"Bad code: {code!r}"

        # Step 2: Upload file (starts curl, which will block on "waiting for receiver...")
        upload = subprocess.Popen(
            ["curl", "-sf", "-T", src_path,
             "-H", f"X-Wormhole-Filename: testfile.bin",
             "-H", f"Content-Length: {size}",
             f"{server_url}/send/{code}"],
            stdout=subprocess.PIPE,
            text=True,
        )

        # Step 3: Wait 2 seconds — simulating a delayed receiver
        time.sleep(2)

        # Step 4: Now start the receiver
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

        # Check upload completed
        stdout = upload.communicate(timeout=10)[0]
        assert "transfer complete" in stdout
```

- [ ] **Step 2: Run the new test**

Run: `cd /home/luca/github/wormhole-web && uv run pytest tests/test_integration.py::TestSendPathDelayed -v --timeout=120`
Expected: PASS.

- [ ] **Step 3: Run full suite**

Run: `cd /home/luca/github/wormhole-web && uv run pytest tests/ -v --timeout=120`
Expected: All 26 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add delayed-receiver integration test for send persistence"
```
