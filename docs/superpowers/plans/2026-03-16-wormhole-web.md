# Wormhole Web Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-hostable HTTP gateway for Magic Wormhole that lets users send/receive files via `curl`, interoperable with the `wormhole` and `wormhole-rs` CLIs.

**Architecture:** A Twisted Web server that acts as a wormhole client on behalf of HTTP users. It reimplements the file-transfer protocol on top of `wormhole.create()` and `wormhole.transit`, streaming data between HTTP requests/responses and wormhole transit connections without buffering full files.

**Tech Stack:** Python 3.12+, `twisted.web`, `magic-wormhole`, `uv`, `pytest`, `podman`

**Spec:** `docs/superpowers/specs/2026-03-16-wormhole-web-design.md`

---

## Chunk 1: Project Setup & Utilities

### Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `src/wormhole_web/__init__.py`
- Create: `src/wormhole_web/constants.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `.gitignore`

- [ ] **Step 1: Create `.gitignore`**

```gitignore
__pycache__/
*.pyc
*.egg-info/
dist/
build/
.venv/
.superpowers/
```

- [ ] **Step 2: Create `pyproject.toml`**

```toml
[project]
name = "wormhole-web"
version = "0.1.0"
description = "HTTP gateway for Magic Wormhole"
requires-python = ">=3.12"
dependencies = [
    "magic-wormhole",
    "twisted[tls]",
]

[project.scripts]
wormhole-web = "wormhole_web.server:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
testpaths = ["tests"]

[dependency-groups]
dev = [
    "pytest",
    "pytest-twisted",
    "pytest-timeout",
    "treq",
]
```

- [ ] **Step 3: Create shared constants**

```python
# src/wormhole_web/constants.py
"""Shared constants for wormhole-web."""

APPID = "lothar.com/wormhole/text-or-file-xfer"
RELAY_URL = "ws://relay.magic-wormhole.io:4000/v1"
TRANSIT_RELAY = "tcp:transit.magic-wormhole.io:4001"
TRANSIT_KEY_LENGTH = 32  # SecretBox.KEY_SIZE

DEFAULT_PORT = 8080
DEFAULT_MAX_SESSIONS = 128
DEFAULT_SESSION_TTL = 60
DEFAULT_TRANSFER_TIMEOUT = 120
```

- [ ] **Step 4: Create `src/wormhole_web/__init__.py`**

```python
"""HTTP gateway for Magic Wormhole."""
```

- [ ] **Step 5: Create `tests/__init__.py` and `tests/conftest.py`**

`tests/__init__.py` — empty file.

```python
# tests/conftest.py
"""Pytest configuration for twisted async tests."""

# pytest-twisted auto-detects and handles Deferred-returning tests
# when installed. No additional configuration needed beyond having
# it in dev dependencies.
```

- [ ] **Step 6: Install dependencies and verify**

Run: `cd /home/luca/github/wormhole-web && uv sync`
Expected: Dependencies install successfully, `uv.lock` created.

Run: `uv run python -c "from wormhole import create; from twisted.web import resource; print('ok')"`
Expected: `ok`

- [ ] **Step 7: Commit**

```bash
git add .gitignore pyproject.toml src/ tests/
git commit -m "feat: project scaffolding with uv"
```

---

### Task 2: Filename sanitization utility

**Files:**
- Create: `src/wormhole_web/util.py`
- Create: `tests/test_util.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_util.py
from wormhole_web.util import sanitize_filename


def test_passthrough_normal_filename():
    assert sanitize_filename("document.pdf") == "document.pdf"


def test_strips_path_traversal():
    assert sanitize_filename("../../etc/passwd") == "passwd"


def test_strips_leading_slashes():
    assert sanitize_filename("/etc/passwd") == "passwd"


def test_strips_backslash_paths():
    assert sanitize_filename("..\\..\\file.txt") == "file.txt"


def test_removes_null_bytes():
    assert sanitize_filename("file\x00.txt") == "file.txt"


def test_fallback_for_empty_after_sanitization():
    assert sanitize_filename("../../") == "upload"
    assert sanitize_filename("") == "upload"
    assert sanitize_filename(None) == "upload"


def test_preserves_spaces_and_dots():
    assert sanitize_filename("my file.tar.gz") == "my file.tar.gz"


def test_strips_control_characters():
    assert sanitize_filename("file\nname\r.txt") == "filename.txt"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/luca/github/wormhole-web && uv run pytest tests/test_util.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wormhole_web.util'`

- [ ] **Step 3: Implement `sanitize_filename`**

```python
# src/wormhole_web/util.py
"""Utility functions for wormhole-web."""

import os
import re

from twisted.internet import defer


class WormholeTimeout(Exception):
    """Timed out waiting for a wormhole operation."""


def with_timeout(d, timeout, reactor, msg="operation timed out"):
    """Race a Deferred against a timeout.

    Returns a new Deferred that fires with the result of d,
    or errbacks with WormholeTimeout if the timeout expires first.
    Cancels d on timeout.
    """
    timeout_d = defer.Deferred()

    def on_timeout():
        timeout_d.errback(WormholeTimeout(msg))
        d.cancel()

    timer = reactor.callLater(timeout, on_timeout)

    def cancel_timer(result):
        if timer.active():
            timer.cancel()
        return result

    d.addBoth(cancel_timer)

    dl = defer.DeferredList(
        [d, timeout_d],
        fireOnOneCallback=True,
        fireOnOneErrback=True,
        consumeErrors=True,
    )
    dl.addCallback(lambda result: result[1])

    def unwrap_first_error(failure):
        failure.trap(defer.FirstError)
        return failure.value.subFailure

    dl.addErrback(unwrap_first_error)
    return dl


def sanitize_filename(name: str | None) -> str:
    """Sanitize a filename for use in Content-Disposition headers.

    Strips path components, null bytes, and control characters.
    Returns 'upload' if the result is empty.
    """
    if not name:
        return "upload"
    # Remove null bytes and control characters
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    # Normalize path separators and take basename
    name = name.replace("\\", "/")
    name = os.path.basename(name)
    # Fallback if empty
    return name if name else "upload"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/luca/github/wormhole-web && uv run pytest tests/test_util.py -v`
Expected: All 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wormhole_web/util.py tests/test_util.py
git commit -m "feat: filename sanitization utility"
```

---

## Chunk 2: Session Management

### Task 3: Session manager

Tracks active wormhole send sessions (created by `POST /send/new`, consumed by `PUT /send/<code>`). The receive path does not need session management — it's a single request.

**Files:**
- Create: `src/wormhole_web/session.py`
- Create: `tests/test_session.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_session.py
from unittest.mock import MagicMock
from twisted.internet import task

from wormhole_web.session import SessionManager, Session, SessionState


class TestSessionManager:
    def setup_method(self):
        self.clock = task.Clock()
        self.manager = SessionManager(
            max_sessions=3,
            session_ttl=60,
            reactor=self.clock,
        )

    def test_create_session_returns_session(self):
        wormhole = MagicMock()
        session = self.manager.create("7-guitarist-revenge", wormhole)
        assert isinstance(session, Session)
        assert session.code == "7-guitarist-revenge"
        assert session.state == SessionState.WAITING_FOR_UPLOAD

    def test_get_session_by_code(self):
        wormhole = MagicMock()
        self.manager.create("7-guitarist-revenge", wormhole)
        session = self.manager.get("7-guitarist-revenge")
        assert session is not None
        assert session.code == "7-guitarist-revenge"

    def test_get_nonexistent_returns_none(self):
        assert self.manager.get("nonexistent") is None

    def test_max_sessions_enforced(self):
        for i in range(3):
            self.manager.create(f"{i}-code", MagicMock())
        assert self.manager.is_full()

    def test_remove_session(self):
        wormhole = MagicMock()
        self.manager.create("7-guitarist-revenge", wormhole)
        self.manager.remove("7-guitarist-revenge")
        assert self.manager.get("7-guitarist-revenge") is None
        assert not self.manager.is_full()

    def test_ttl_cleanup(self):
        wormhole = MagicMock()
        self.manager.create("7-guitarist-revenge", wormhole)
        # Advance past TTL
        self.clock.advance(61)
        assert self.manager.get("7-guitarist-revenge") is None
        wormhole.close.assert_called_once()

    def test_ttl_not_triggered_when_uploading(self):
        wormhole = MagicMock()
        session = self.manager.create("7-guitarist-revenge", wormhole)
        session.state = SessionState.UPLOADING
        self.clock.advance(61)
        # Session still exists because it's in UPLOADING state
        assert self.manager.get("7-guitarist-revenge") is not None


class TestSession:
    def test_initial_state(self):
        session = Session(code="7-test", wormhole=MagicMock())
        assert session.state == SessionState.WAITING_FOR_UPLOAD
        assert session.code == "7-test"

    def test_transition_to_uploading(self):
        session = Session(code="7-test", wormhole=MagicMock())
        assert session.claim_upload()
        assert session.state == SessionState.UPLOADING

    def test_double_claim_fails(self):
        session = Session(code="7-test", wormhole=MagicMock())
        assert session.claim_upload()
        assert not session.claim_upload()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/luca/github/wormhole-web && uv run pytest tests/test_session.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement session manager**

```python
# src/wormhole_web/session.py
"""Send session management with TTL cleanup."""

import enum


class SessionState(enum.Enum):
    WAITING_FOR_UPLOAD = "waiting_for_upload"
    UPLOADING = "uploading"
    TRANSFERRING = "transferring"
    DONE = "done"


class Session:
    """A wormhole send session awaiting or processing an upload."""

    def __init__(self, code: str, wormhole):
        self.code = code
        self.wormhole = wormhole
        self.state = SessionState.WAITING_FOR_UPLOAD
        self.transit = None
        self._cleanup_timer = None

    def claim_upload(self) -> bool:
        """Try to claim this session for upload. Returns False if already claimed."""
        if self.state != SessionState.WAITING_FOR_UPLOAD:
            return False
        self.state = SessionState.UPLOADING
        if self._cleanup_timer and self._cleanup_timer.active():
            self._cleanup_timer.cancel()
        return True


class SessionManager:
    """Manages active send sessions with TTL and concurrency limits."""

    def __init__(self, max_sessions: int = 128, session_ttl: int = 60, reactor=None):
        self._sessions: dict[str, Session] = {}
        self._max_sessions = max_sessions
        self._session_ttl = session_ttl
        self._reactor = reactor

    def create(self, code: str, wormhole) -> Session:
        """Create a new session. Caller must check is_full() first."""
        session = Session(code=code, wormhole=wormhole)
        self._sessions[code] = session
        # Schedule TTL cleanup
        session._cleanup_timer = self._reactor.callLater(
            self._session_ttl, self._expire, code
        )
        return session

    def get(self, code: str) -> Session | None:
        return self._sessions.get(code)

    def remove(self, code: str):
        session = self._sessions.pop(code, None)
        if session and session._cleanup_timer and session._cleanup_timer.active():
            session._cleanup_timer.cancel()

    def is_full(self) -> bool:
        return len(self._sessions) >= self._max_sessions

    def _expire(self, code: str):
        session = self._sessions.get(code)
        if session and session.state == SessionState.WAITING_FOR_UPLOAD:
            session.wormhole.close()
            self._sessions.pop(code, None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/luca/github/wormhole-web && uv run pytest tests/test_session.py -v`
Expected: All 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wormhole_web/session.py tests/test_session.py
git commit -m "feat: session manager with TTL cleanup"
```

---

## Chunk 3: Wormhole Protocol Logic

### Task 4: Wormhole receive logic

Implements the receiver side of the file-transfer protocol: set code → PAKE exchange → receive transit hints + file offer (in either order) → send file_ack → establish transit → return connection for streaming.

**Files:**
- Create: `src/wormhole_web/receiver.py`

This module speaks the wormhole protocol and requires a real relay to test. It will be covered by integration tests in Task 10.

- [ ] **Step 1: Implement receiver**

```python
# src/wormhole_web/receiver.py
"""Wormhole receive logic — stream a file from a wormhole sender."""

from twisted.internet import defer
from wormhole import create
from wormhole.transit import TransitReceiver
from wormhole.util import dict_to_bytes, bytes_to_dict

from wormhole_web.constants import APPID, RELAY_URL, TRANSIT_RELAY, TRANSIT_KEY_LENGTH
from wormhole_web.util import WormholeTimeout, with_timeout


class ReceiveError(Exception):
    """Base error for receive failures."""


class BadCodeError(ReceiveError):
    """The wormhole code was invalid or expired."""


class OfferError(ReceiveError):
    """The offer was not a file offer or was malformed."""


class FileOffer:
    """Metadata from a wormhole file offer."""
    def __init__(self, filename: str, filesize: int):
        self.filename = filename
        self.filesize = filesize


@defer.inlineCallbacks
def receive_file(code: str, reactor, timeout=120):
    """Initiate a wormhole receive and return (offer, transit_connection, wormhole).

    The caller is responsible for reading data from the transit connection
    and closing the wormhole when done.

    Args:
        code: The wormhole code to receive from.
        reactor: The Twisted reactor.
        timeout: Seconds to wait before giving up.

    Returns:
        tuple: (FileOffer, Connection, wormhole_object)

    Raises:
        BadCodeError: If the code is invalid or PAKE fails.
        OfferError: If the offer is not a single-file offer.
        WormholeTimeout: If the operation times out.
    """
    w = create(APPID, RELAY_URL, reactor)
    timed_out = [False]
    try:
        w.set_code(code)

        # Wait for key exchange (with timeout)
        try:
            yield with_timeout(
                w.get_unverified_key(), timeout, reactor,
                "Timed out waiting for sender"
            )
        except WormholeTimeout:
            timed_out[0] = True
            raise
        except Exception as e:
            raise BadCodeError(f"Key exchange failed: {e}") from e

        yield w.get_verifier()

        # Set up transit receiver
        tr = TransitReceiver(
            TRANSIT_RELAY,
            no_listen=True,  # server doesn't accept inbound connections
            reactor=reactor,
        )

        transit_key = w.derive_key(APPID + "/transit-key", TRANSIT_KEY_LENGTH)
        tr.set_transit_key(transit_key)

        # Read two messages: transit hints and file offer (either order)
        msg1_bytes = yield with_timeout(
            w.get_message(), timeout, reactor, "Timed out waiting for offer"
        )
        msg1 = bytes_to_dict(msg1_bytes)
        msg2_bytes = yield with_timeout(
            w.get_message(), timeout, reactor, "Timed out waiting for offer"
        )
        msg2 = bytes_to_dict(msg2_bytes)

        # Sort out which is which
        transit_msg = None
        offer_msg = None
        for msg in (msg1, msg2):
            if "transit" in msg:
                transit_msg = msg
            elif "offer" in msg:
                offer_msg = msg

        if transit_msg is None:
            raise OfferError("Never received transit hints message")
        if offer_msg is None:
            raise OfferError("Never received file offer message")

        # Send our transit hints
        our_abilities = tr.get_connection_abilities()
        our_hints = yield tr.get_connection_hints()
        w.send_message(dict_to_bytes({
            "transit": {
                "abilities-v1": our_abilities,
                "hints-v1": our_hints,
            }
        }))

        # Add peer's hints
        tr.add_connection_hints(transit_msg["transit"].get("hints-v1", []))

        # Parse the file offer
        offer = offer_msg["offer"]
        if "file" not in offer:
            raise OfferError(
                f"Only single-file offers are supported, got: {list(offer.keys())}"
            )

        file_offer = FileOffer(
            filename=offer["file"]["filename"],
            filesize=offer["file"]["filesize"],
        )

        # Send file_ack
        w.send_message(dict_to_bytes({
            "answer": {"file_ack": "ok"}
        }))

        # Establish transit connection (with timeout)
        connection = yield with_timeout(
            tr.connect(), timeout, reactor, "Timed out establishing transit"
        )

        defer.returnValue((file_offer, connection, w))
    except ReceiveError:
        yield w.close()
        raise
    except Exception as e:
        yield w.close()
        if timed_out[0]:
            raise
        raise ReceiveError(f"Receive failed: {e}") from e
```

- [ ] **Step 2: Verify it imports correctly**

Run: `cd /home/luca/github/wormhole-web && uv run python -c "from wormhole_web.receiver import receive_file; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add src/wormhole_web/receiver.py
git commit -m "feat: wormhole receive logic"
```

---

### Task 5: Wormhole send logic

Implements the sender side: allocate code → (key exchange) → send transit hints → send file offer → receive peer transit + file_ack → establish transit → return connection.

**Critical: message ordering.** The key exchange (`get_unverified_key` / `get_verifier`) must complete *before* sending application messages. The wormhole library queues `send_message` calls, but the peer won't receive them until PAKE completes. We must complete PAKE first, then send transit/offer, then read peer's transit/ack.

**Files:**
- Create: `src/wormhole_web/sender.py`

- [ ] **Step 1: Implement sender**

```python
# src/wormhole_web/sender.py
"""Wormhole send logic — stream a file to a wormhole receiver."""

from twisted.internet import defer
from wormhole import create
from wormhole.transit import TransitSender
from wormhole.util import dict_to_bytes, bytes_to_dict

from wormhole_web.constants import APPID, RELAY_URL, TRANSIT_RELAY, TRANSIT_KEY_LENGTH
from wormhole_web.util import WormholeTimeout, with_timeout


class SendError(Exception):
    """Base error for send failures."""


@defer.inlineCallbacks
def create_send_session(reactor):
    """Create a wormhole and allocate a code for sending.

    Returns:
        tuple: (code, wormhole_object)
    """
    w = create(APPID, RELAY_URL, reactor)
    w.allocate_code()
    code = yield w.get_code()
    defer.returnValue((code, w))


@defer.inlineCallbacks
def prepare_send(wormhole, filename: str, filesize: int, reactor, timeout=120):
    """Exchange protocol messages and establish transit for sending.

    Must be called after the wormhole code has been allocated.
    Blocks until a receiver connects and accepts the file offer.

    Args:
        wormhole: The wormhole object (from create_send_session).
        filename: Name of the file being sent.
        filesize: Size in bytes.
        reactor: The Twisted reactor.
        timeout: Seconds to wait for receiver before giving up.

    Returns:
        Connection: the transit connection, ready for send_record().

    Raises:
        SendError: If the receiver rejects or something fails.
        WormholeTimeout: If the receiver never connects.
    """
    w = wormhole
    try:
        # Step 1: Complete key exchange FIRST (with timeout)
        yield with_timeout(
            w.get_unverified_key(), timeout, reactor,
            "Timed out waiting for receiver"
        )
        yield w.get_verifier()

        # Step 2: Set up transit sender
        ts = TransitSender(
            TRANSIT_RELAY,
            no_listen=True,  # server doesn't accept inbound connections
            reactor=reactor,
        )

        transit_key = w.derive_key(APPID + "/transit-key", TRANSIT_KEY_LENGTH)
        ts.set_transit_key(transit_key)

        # Step 3: Send our transit hints
        our_abilities = ts.get_connection_abilities()
        our_hints = yield ts.get_connection_hints()
        w.send_message(dict_to_bytes({
            "transit": {
                "abilities-v1": our_abilities,
                "hints-v1": our_hints,
            }
        }))

        # Step 4: Send file offer
        w.send_message(dict_to_bytes({
            "offer": {
                "file": {
                    "filename": filename,
                    "filesize": filesize,
                }
            }
        }))

        # Step 5: Read two messages from receiver: transit hints and file_ack
        # (either order — the receiver may send them differently)
        msg1_bytes = yield with_timeout(
            w.get_message(), timeout, reactor,
            "Timed out waiting for receiver response"
        )
        msg1 = bytes_to_dict(msg1_bytes)
        msg2_bytes = yield with_timeout(
            w.get_message(), timeout, reactor,
            "Timed out waiting for receiver ack"
        )
        msg2 = bytes_to_dict(msg2_bytes)

        transit_msg = None
        answer_msg = None
        for msg in (msg1, msg2):
            if "transit" in msg:
                transit_msg = msg
            elif "answer" in msg:
                answer_msg = msg
            elif "error" in msg:
                raise SendError(f"Receiver rejected: {msg['error']}")

        if transit_msg is not None:
            ts.add_connection_hints(transit_msg["transit"].get("hints-v1", []))

        if answer_msg is None:
            raise SendError("Never received file_ack from receiver")

        if "file_ack" not in answer_msg.get("answer", {}):
            raise SendError(f"Unexpected answer: {answer_msg}")

        # Step 6: Establish transit connection (with timeout)
        connection = yield with_timeout(
            ts.connect(), timeout, reactor,
            "Timed out establishing transit"
        )

        defer.returnValue(connection)
    except (SendError, WormholeTimeout):
        raise
    except Exception as e:
        raise SendError(f"Send failed: {e}") from e
```

- [ ] **Step 2: Verify it imports correctly**

Run: `cd /home/luca/github/wormhole-web && uv run python -c "from wormhole_web.sender import create_send_session, prepare_send; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add src/wormhole_web/sender.py
git commit -m "feat: wormhole send logic"
```

---

## Chunk 4: HTTP Server

### Task 6: Health endpoint and server skeleton

**Files:**
- Create: `src/wormhole_web/server.py`
- Create: `tests/test_server.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_server.py
from twisted.trial import unittest
from twisted.web.test.requesthelper import DummyRequest

from wormhole_web.server import HealthResource


class TestHealthResource(unittest.TestCase):
    def test_health_returns_ok(self):
        resource = HealthResource()
        request = DummyRequest(b"/health")
        request.method = b"GET"
        result = resource.render_GET(request)
        self.assertEqual(result, b"ok")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/luca/github/wormhole-web && uv run pytest tests/test_server.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement server skeleton**

```python
# src/wormhole_web/server.py
"""Twisted Web HTTP server for wormhole-web."""

import mimetypes
from collections import deque

from twisted.internet import defer, endpoints
from twisted.internet import reactor as default_reactor
from twisted.python import log
from twisted.web import resource, server

from wormhole_web.constants import (
    DEFAULT_MAX_SESSIONS,
    DEFAULT_PORT,
    DEFAULT_SESSION_TTL,
    DEFAULT_TRANSFER_TIMEOUT,
)
from wormhole_web.receiver import (
    BadCodeError,
    OfferError,
    ReceiveError,
    receive_file,
)
from wormhole_web.util import WormholeTimeout
from wormhole_web.sender import SendError, create_send_session, prepare_send
from wormhole_web.session import SessionManager, SessionState
from wormhole_web.util import sanitize_filename


class HealthResource(resource.Resource):
    isLeaf = True

    def render_GET(self, request):
        request.setHeader(b"content-type", b"text/plain")
        return b"ok"


class RootResource(resource.Resource):
    """Root resource that wires up the URL tree."""

    def __init__(self, session_manager, reactor=None, transfer_timeout=120):
        super().__init__()
        self._session_manager = session_manager
        self._reactor = reactor or default_reactor
        self._transfer_timeout = transfer_timeout
        self.putChild(b"health", HealthResource())
        self.putChild(
            b"receive", ReceiveResource(self._reactor, self._transfer_timeout)
        )
        self.putChild(
            b"send",
            SendResource(session_manager, self._reactor, self._transfer_timeout),
        )


def make_site(reactor=None, max_sessions=128, session_ttl=60, transfer_timeout=120):
    """Create and return a Twisted Site with all resources wired up."""
    reactor = reactor or default_reactor
    session_manager = SessionManager(
        max_sessions=max_sessions,
        session_ttl=session_ttl,
        reactor=reactor,
    )
    root = RootResource(session_manager, reactor, transfer_timeout)
    return server.Site(root)


# --- Receive resources ---


class ReceiveResource(resource.Resource):
    """GET /receive/<code> — receive a file from a wormhole sender."""

    def __init__(self, reactor, transfer_timeout):
        super().__init__()
        self._reactor = reactor
        self._transfer_timeout = transfer_timeout

    def getChild(self, name, request):
        return ReceiveCodeResource(
            name.decode("utf-8"), self._reactor, self._transfer_timeout
        )


class ReceiveCodeResource(resource.Resource):
    isLeaf = True

    def __init__(self, code, reactor, transfer_timeout):
        super().__init__()
        self._code = code
        self._reactor = reactor
        self._transfer_timeout = transfer_timeout

    def render_GET(self, request):
        self._do_receive(request)
        return server.NOT_DONE_YET

    @defer.inlineCallbacks
    def _do_receive(self, request):
        finished = [False]
        wormhole_ref = [None]
        connection_ref = [None]

        def on_disconnect(_):
            finished[0] = True
            if connection_ref[0]:
                connection_ref[0].close()

        request.notifyFinish().addBoth(on_disconnect)

        try:
            offer, connection, wormhole = yield receive_file(
                self._code, self._reactor, timeout=self._transfer_timeout
            )
            wormhole_ref[0] = wormhole
            connection_ref[0] = connection
        except WormholeTimeout:
            if not finished[0]:
                request.setResponseCode(408)
                request.setHeader(b"content-type", b"text/plain")
                request.write(b"timeout waiting for sender\n")
                request.finish()
            return
        except BadCodeError:
            if not finished[0]:
                request.setResponseCode(404)
                request.setHeader(b"content-type", b"text/plain")
                request.write(b"invalid or expired wormhole code\n")
                request.finish()
            return
        except ReceiveError as e:
            if not finished[0]:
                request.setResponseCode(500)
                request.setHeader(b"content-type", b"text/plain")
                request.write(f"wormhole error: {e}\n".encode())
                request.finish()
            return

        if finished[0]:
            yield wormhole.close()
            return

        filename = sanitize_filename(offer.filename)
        content_type = (
            mimetypes.guess_type(filename)[0] or "application/octet-stream"
        )

        request.setHeader(b"content-type", content_type.encode())
        request.setHeader(
            b"content-disposition",
            f'attachment; filename="{filename}"'.encode(),
        )
        request.setHeader(b"content-length", str(offer.filesize).encode())

        # Stream data from transit to HTTP response
        bytes_received = 0
        stall_timer = None

        def reset_stall_timer():
            nonlocal stall_timer
            if stall_timer and stall_timer.active():
                stall_timer.cancel()
            stall_timer = self._reactor.callLater(
                self._transfer_timeout, on_stall
            )

        def on_stall():
            nonlocal finished
            if not finished[0]:
                finished[0] = True
                request.loseConnection()

        try:
            reset_stall_timer()
            while bytes_received < offer.filesize and not finished[0]:
                record = yield connection.receive_record()
                remaining = offer.filesize - bytes_received
                chunk = record[:remaining]
                bytes_received += len(chunk)
                if not finished[0]:
                    request.write(chunk)
                    reset_stall_timer()
            if not finished[0]:
                request.finish()
                finished[0] = True
        except Exception:
            if not finished[0]:
                request.loseConnection()
                finished[0] = True
        finally:
            if stall_timer and stall_timer.active():
                stall_timer.cancel()
            yield wormhole.close()


# --- Send resources ---


class SendResource(resource.Resource):
    """Routes /send/new and /send/<code>."""

    def __init__(self, session_manager, reactor, transfer_timeout=120):
        super().__init__()
        self._session_manager = session_manager
        self._reactor = reactor
        self._transfer_timeout = transfer_timeout
        self.putChild(b"new", SendNewResource(session_manager, reactor))

    def getChild(self, name, request):
        return SendCodeResource(
            name.decode("utf-8"), self._session_manager, self._reactor,
            self._transfer_timeout,
        )

    def render_PUT(self, request):
        """PUT /send — convenience redirect."""
        self._do_redirect(request)
        return server.NOT_DONE_YET

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
            self._session_manager.create(code, wormhole)

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
            self._session_manager.create(code, wormhole)

            request.setHeader(b"content-type", b"text/plain")
            request.write(code.encode() + b"\n")
            request.finish()
        except Exception as e:
            request.setResponseCode(500)
            request.setHeader(b"content-type", b"text/plain")
            request.write(f"error: {e}\n".encode())
            request.finish()


class SendCodeResource(resource.Resource):
    """PUT /send/<code> — upload file data.

    IMPORTANT: Twisted buffers the full request body before calling render_PUT.
    For streaming large uploads, we need a custom approach. For v1, we accept
    this limitation — Twisted writes the body to a temp file for large uploads
    (>100KB), so memory usage stays bounded even for large files. The file is
    read back in chunks during the wormhole transit phase.

    True streaming (backpressure from HTTP request body to wormhole transit)
    would require implementing IBodyReceiver or a custom Request subclass.
    This is deferred to a future version.
    """
    isLeaf = True

    def __init__(self, code, session_manager, reactor, transfer_timeout=120):
        super().__init__()
        self._code = code
        self._session_manager = session_manager
        self._reactor = reactor
        self._transfer_timeout = transfer_timeout

    def render_PUT(self, request):
        self._do_upload(request)
        return server.NOT_DONE_YET

    @defer.inlineCallbacks
    def _do_upload(self, request):
        session = self._session_manager.get(self._code)
        if session is None:
            request.setResponseCode(404)
            request.setHeader(b"content-type", b"text/plain")
            request.write(b"unknown or expired code\n")
            request.finish()
            return

        if not session.claim_upload():
            request.setResponseCode(409)
            request.setHeader(b"content-type", b"text/plain")
            request.write(b"upload already in progress\n")
            request.finish()
            return

        # Get filename from header or fallback
        raw_filename = request.getHeader("x-wormhole-filename")
        if raw_filename:
            filename = sanitize_filename(
                raw_filename.decode("utf-8", errors="replace")
            )
        else:
            filename = "upload"

        # Get file size from Content-Length
        content_length = request.getHeader("content-length")
        filesize = int(content_length) if content_length else 0

        request.setHeader(b"content-type", b"text/plain")
        request.write(self._code.encode() + b"\n")
        request.write(b"waiting for receiver...\n")

        try:
            connection = yield prepare_send(
                session.wormhole, filename, filesize, self._reactor,
                timeout=self._transfer_timeout,
            )
            session.state = SessionState.TRANSFERRING

            # Read from request body (Twisted has buffered it to disk/memory)
            # and send in chunks through wormhole transit
            request.content.seek(0)
            while True:
                chunk = request.content.read(262144)  # 256KB
                if not chunk:
                    break
                connection.send_record(chunk)

            connection.close()
            request.write(b"transfer complete\n")
        except SendError as e:
            request.write(f"error: {e}\n".encode())
        except Exception as e:
            request.write(f"error: {e}\n".encode())
        finally:
            self._session_manager.remove(self._code)
            try:
                yield session.wormhole.close()
            except Exception:
                pass
            request.finish()


# --- CLI entry point ---


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Wormhole Web — HTTP gateway")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--max-sessions", type=int, default=DEFAULT_MAX_SESSIONS)
    parser.add_argument("--session-ttl", type=int, default=DEFAULT_SESSION_TTL)
    parser.add_argument(
        "--transfer-timeout", type=int, default=DEFAULT_TRANSFER_TIMEOUT
    )
    args = parser.parse_args()

    site = make_site(
        reactor=default_reactor,
        max_sessions=args.max_sessions,
        session_ttl=args.session_ttl,
        transfer_timeout=args.transfer_timeout,
    )
    endpoint = endpoints.TCP4ServerEndpoint(default_reactor, args.port)
    endpoint.listen(site)
    print(f"wormhole-web listening on :{args.port}")
    default_reactor.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/luca/github/wormhole-web && uv run pytest tests/test_server.py -v`
Expected: PASS.

- [ ] **Step 5: Smoke-test the server starts and serves health**

Run: `cd /home/luca/github/wormhole-web && timeout 3 uv run python -m wormhole_web.server --port 18080 & sleep 1 && curl -s http://localhost:18080/health; kill %1 2>/dev/null`
Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add src/wormhole_web/server.py tests/test_server.py
git commit -m "feat: HTTP server with all endpoints"
```

---

## Chunk 5: Container

### Task 7: Containerfile

**Files:**
- Create: `Containerfile`

- [ ] **Step 1: Write Containerfile**

```dockerfile
FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies first (layer caching)
COPY pyproject.toml .
RUN uv sync --no-dev --no-install-project

# Copy source
COPY src/ src/

# Install project
RUN uv sync --no-dev

EXPOSE 8080

ENTRYPOINT ["uv", "run", "wormhole-web"]
CMD ["--port", "8080"]
```

- [ ] **Step 2: Build the container**

Run: `cd /home/luca/github/wormhole-web && podman build -t wormhole-web .`
Expected: Build succeeds.

- [ ] **Step 3: Verify the container runs and serves health**

Run: `podman run --rm -d --name ww-test -p 18080:8080 wormhole-web && sleep 2 && curl -s http://localhost:18080/health; podman stop ww-test`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add Containerfile
git commit -m "feat: add Containerfile for podman/docker"
```

---

## Chunk 6: Integration & E2E Tests

### Task 8: Integration tests using magic-wormhole library

Tests the full flow using the magic-wormhole Python library as the other end. These hit the real public relay.

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write integration tests**

```python
# tests/test_integration.py
"""Integration tests — our server against the magic-wormhole library.

These tests use the public relay and may be flaky if the relay is down.
"""

import hashlib
import os

import pytest
from twisted.internet import defer, reactor
from twisted.web import server
from treq import content, get, post, put

from wormhole import create
from wormhole.transit import TransitReceiver, TransitSender
from wormhole.util import bytes_to_dict, dict_to_bytes

from wormhole_web.constants import APPID, RELAY_URL, TRANSIT_RELAY, TRANSIT_KEY_LENGTH
from wormhole_web.server import make_site


@pytest.fixture
def site_port():
    """Start the server on a random port, yield the port, then clean up."""
    site = make_site(reactor=reactor, max_sessions=10, session_ttl=60)
    port_obj = reactor.listenTCP(0, site, interface="127.0.0.1")
    port_num = port_obj.getHost().port
    yield port_num
    port_obj.stopListening()


def base_url(port):
    return f"http://127.0.0.1:{port}"


@pytest.fixture
def test_data():
    """Generate 1MB of random test data."""
    data = os.urandom(1024 * 1024)
    return data, hashlib.sha256(data).hexdigest()


class TestReceivePath:
    """Test: wormhole library sends → our server receives via HTTP."""

    @pytest.inlineCallbacks
    def test_receive_file(self, site_port, test_data):
        data, expected_hash = test_data

        # --- Sender side (simulates `wormhole send`) ---
        w = create(APPID, RELAY_URL, reactor)
        w.allocate_code()
        code = yield w.get_code()

        # Start the HTTP receive concurrently
        receive_d = get(f"{base_url(site_port)}/receive/{code}")

        # Complete key exchange
        yield w.get_unverified_key()
        yield w.get_verifier()

        # Set up transit
        ts = TransitSender(TRANSIT_RELAY, no_listen=True, reactor=reactor)
        transit_key = w.derive_key(APPID + "/transit-key", TRANSIT_KEY_LENGTH)
        ts.set_transit_key(transit_key)

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
                    "filename": "testfile.bin",
                    "filesize": len(data),
                }
            }
        }))

        # Get peer transit hints
        peer_msg = bytes_to_dict((yield w.get_message()))
        ts.add_connection_hints(peer_msg["transit"]["hints-v1"])

        # Get file_ack
        answer = bytes_to_dict((yield w.get_message()))
        assert answer.get("answer", {}).get("file_ack") == "ok"

        # Establish transit and send data
        connection = yield ts.connect()
        offset = 0
        while offset < len(data):
            chunk = data[offset:offset + 262144]
            connection.send_record(chunk)
            offset += len(chunk)
        connection.close()
        yield w.close()

        # --- Check HTTP response ---
        response = yield receive_d
        assert response.code == 200
        assert response.headers.hasHeader(b"content-disposition")

        body = yield content(response)
        assert hashlib.sha256(body).hexdigest() == expected_hash
        assert len(body) == len(data)


class TestSendPath:
    """Test: our server sends via HTTP → wormhole library receives."""

    @pytest.inlineCallbacks
    def test_send_file(self, site_port, test_data):
        data, expected_hash = test_data

        # Step 1: Get a wormhole code from our server
        resp = yield post(f"{base_url(site_port)}/send/new")
        assert resp.code == 200
        code = (yield content(resp)).decode().strip()
        assert "-" in code

        # Step 2: Start upload concurrently
        upload_d = put(
            f"{base_url(site_port)}/send/{code}",
            data=data,
            headers={
                b"X-Wormhole-Filename": [b"testfile.bin"],
                b"Content-Length": [str(len(data)).encode()],
            },
        )

        # --- Receiver side (simulates `wormhole receive`) ---
        w = create(APPID, RELAY_URL, reactor)
        w.set_code(code)

        yield w.get_unverified_key()
        yield w.get_verifier()

        # Exchange transit hints
        tr = TransitReceiver(TRANSIT_RELAY, no_listen=True, reactor=reactor)
        transit_key = w.derive_key(APPID + "/transit-key", TRANSIT_KEY_LENGTH)
        tr.set_transit_key(transit_key)

        our_abilities = tr.get_connection_abilities()
        our_hints = yield tr.get_connection_hints()
        w.send_message(dict_to_bytes({
            "transit": {
                "abilities-v1": our_abilities,
                "hints-v1": our_hints,
            }
        }))

        # Read two messages (transit + offer, either order)
        msg1 = bytes_to_dict((yield w.get_message()))
        msg2 = bytes_to_dict((yield w.get_message()))

        transit_msg = None
        offer_msg = None
        for msg in (msg1, msg2):
            if "transit" in msg:
                transit_msg = msg
            elif "offer" in msg:
                offer_msg = msg

        assert offer_msg is not None
        assert offer_msg["offer"]["file"]["filename"] == "testfile.bin"
        tr.add_connection_hints(transit_msg["transit"]["hints-v1"])

        # Send ack
        w.send_message(dict_to_bytes({"answer": {"file_ack": "ok"}}))

        # Receive data via transit
        connection = yield tr.connect()
        received = b""
        while len(received) < len(data):
            record = yield connection.receive_record()
            received += record

        connection.close()
        yield w.close()

        assert hashlib.sha256(received).hexdigest() == expected_hash

        # Wait for upload response
        upload_resp = yield upload_d
        upload_body = (yield content(upload_resp)).decode()
        assert "transfer complete" in upload_body
```

- [ ] **Step 2: Run integration tests**

Run: `cd /home/luca/github/wormhole-web && uv run pytest tests/test_integration.py -v --timeout=120`
Expected: Both tests PASS (may take 10-30 seconds due to relay round-trips).

**Debugging note:** If tests fail, the most likely issues are:
- Message ordering mismatch — add debug logging in receiver.py/sender.py
- Transit connection timeout — check if `no_listen=True` is causing issues
- Compare our message exchange to the CLI's by reading `cmd_send.py` and `cmd_receive.py`

Iterate on `receiver.py`, `sender.py`, and the test code until both tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: integration tests against magic-wormhole library"
```

---

### Task 9: E2E compatibility tests with CLI binaries

Tests the 4-combination matrix from the spec using real CLI binaries. The server runs in a **separate process** to avoid blocking the Twisted reactor.

**Files:**
- Create: `tests/test_e2e.py`

- [ ] **Step 1: Write E2E tests**

```python
# tests/test_e2e.py
"""E2E compatibility tests with wormhole CLI binaries.

Requires `wormhole` (Python) and/or `wormhole-rs` installed on PATH.
Uses the public relay — may be flaky.

The server runs as a subprocess to avoid blocking issues with
the Twisted reactor and synchronous subprocess calls.
"""

import hashlib
import os
import signal
import subprocess
import tempfile
import time

import pytest


def has_command(cmd):
    try:
        subprocess.run([cmd, "--version"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


requires_wormhole = pytest.mark.skipif(
    not has_command("wormhole"), reason="wormhole CLI not installed"
)
requires_wormhole_rs = pytest.mark.skipif(
    not has_command("wormhole-rs"), reason="wormhole-rs CLI not installed"
)


def _find_free_port():
    """Find a free TCP port by briefly binding to port 0."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server_url():
    """Start wormhole-web server as a subprocess, yield its URL."""
    port = _find_free_port()
    proc = subprocess.Popen(
        ["uv", "run", "wormhole-web", "--port", str(port)],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for server to be ready
    url = f"http://127.0.0.1:{port}"
    for _ in range(20):
        try:
            result = subprocess.run(
                ["curl", "-sf", f"{url}/health"],
                capture_output=True, timeout=2,
            )
            if result.returncode == 0:
                break
        except subprocess.TimeoutExpired:
            pass
        time.sleep(0.5)
    else:
        proc.kill()
        raise RuntimeError("Server failed to start")

    yield url

    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=5)


@pytest.fixture
def test_file():
    """Create a temp file with 100KB random content."""
    data = os.urandom(1024 * 100)
    sha = hashlib.sha256(data).hexdigest()
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
        f.write(data)
        path = f.name
    yield path, sha, len(data)
    os.unlink(path)


def _extract_code_from_cli_output(proc, keyword="wormhole receive"):
    """Read stderr lines from a wormhole send process to find the code."""
    for line in iter(proc.stderr.readline, ""):
        if keyword in line:
            return line.strip().split()[-1]
    return None


@requires_wormhole
class TestPythonCLI:
    def test_cli_send_web_receive(self, server_url, test_file):
        """wormhole send → curl receive via our server."""
        src_path, expected_hash, _ = test_file

        sender = subprocess.Popen(
            ["wormhole", "send", "--hide-progress", src_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        code = _extract_code_from_cli_output(sender)
        assert code is not None, "Failed to get wormhole code from sender"

        with tempfile.NamedTemporaryFile(delete=False) as dst:
            dst_path = dst.name

        try:
            result = subprocess.run(
                ["curl", "-sf", "-o", dst_path,
                 f"{server_url}/receive/{code}"],
                timeout=60,
            )
            assert result.returncode == 0

            with open(dst_path, "rb") as f:
                assert hashlib.sha256(f.read()).hexdigest() == expected_hash
        finally:
            os.unlink(dst_path)
            sender.wait(timeout=10)

    def test_web_send_cli_receive(self, server_url, test_file):
        """curl send via our server → wormhole receive."""
        src_path, expected_hash, size = test_file

        # Get a code
        result = subprocess.run(
            ["curl", "-sf", "-X", "POST", f"{server_url}/send/new"],
            capture_output=True, text=True, timeout=30,
        )
        code = result.stdout.strip()
        assert "-" in code

        # Start upload in background
        upload = subprocess.Popen(
            ["curl", "-sf", "-T", src_path,
             "-H", f"X-Wormhole-Filename: {os.path.basename(src_path)}",
             "-H", f"Content-Length: {size}",
             f"{server_url}/send/{code}"],
            stdout=subprocess.PIPE,
            text=True,
        )

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


@requires_wormhole_rs
class TestRustCLI:
    def test_rs_send_web_receive(self, server_url, test_file):
        """wormhole-rs send → curl receive via our server."""
        src_path, expected_hash, _ = test_file

        sender = subprocess.Popen(
            ["wormhole-rs", "send", src_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        code = _extract_code_from_cli_output(sender, keyword="wormhole")
        assert code is not None

        with tempfile.NamedTemporaryFile(delete=False) as dst:
            dst_path = dst.name

        try:
            result = subprocess.run(
                ["curl", "-sf", "-o", dst_path,
                 f"{server_url}/receive/{code}"],
                timeout=60,
            )
            assert result.returncode == 0

            with open(dst_path, "rb") as f:
                assert hashlib.sha256(f.read()).hexdigest() == expected_hash
        finally:
            os.unlink(dst_path)
            sender.wait(timeout=10)

    def test_web_send_rs_receive(self, server_url, test_file):
        """curl send via our server → wormhole-rs receive."""
        src_path, expected_hash, size = test_file

        result = subprocess.run(
            ["curl", "-sf", "-X", "POST", f"{server_url}/send/new"],
            capture_output=True, text=True, timeout=30,
        )
        code = result.stdout.strip()
        assert "-" in code

        upload = subprocess.Popen(
            ["curl", "-sf", "-T", src_path,
             "-H", f"X-Wormhole-Filename: {os.path.basename(src_path)}",
             "-H", f"Content-Length: {size}",
             f"{server_url}/send/{code}"],
            stdout=subprocess.PIPE,
            text=True,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            recv = subprocess.run(
                ["wormhole-rs", "receive", "--accept-file",
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

- [ ] **Step 2: Run E2E tests**

Run: `cd /home/luca/github/wormhole-web && uv run pytest tests/test_e2e.py -v --timeout=120`
Expected: Tests PASS (or skip if CLI binaries not installed).

- [ ] **Step 3: Commit**

```bash
git add tests/test_e2e.py
git commit -m "test: E2E compatibility tests with wormhole CLIs"
```

---

## Implementation Notes

### Streaming limitation (v1)

The send path (`PUT /send/<code>`) does **not** truly stream in v1. Twisted's `twisted.web` buffers the full request body before calling `render_PUT` — for bodies >100KB it spools to a temp file, so memory stays bounded, but the file must be fully uploaded before the wormhole transfer begins.

True streaming would require implementing `twisted.web.iweb.IBodyReceiver` or a custom `Request` subclass that processes the body incrementally. This is deferred to v2. The receive path (`GET /receive/<code>`) does stream correctly — chunks flow directly from wormhole transit to the HTTP response.

### Message ordering reference

**Sender side:**
1. `allocate_code()` → `get_code()`
2. `get_unverified_key()` → `get_verifier()` ← **key exchange first**
3. Send transit hints message
4. Send file offer message
5. Receive peer's transit hints + file_ack (either order)
6. Establish transit connection → `send_record()`

**Receiver side:**
1. `set_code(code)`
2. `get_unverified_key()` → `get_verifier()`
3. Receive transit hints + file offer (either order)
4. Send our transit hints
5. Send `{"answer": {"file_ack": "ok"}}`
6. Establish transit connection → `receive_record()`

### Key source files in magic-wormhole

| File | Purpose |
|------|---------|
| `/usr/lib/python3.13/site-packages/wormhole/cli/cmd_send.py` | CLI sender — message ordering reference |
| `/usr/lib/python3.13/site-packages/wormhole/cli/cmd_receive.py` | CLI receiver — message ordering reference |
| `/usr/lib/python3.13/site-packages/wormhole/transit.py` | `send_record` / `receive_record` / `Connection` |
| `/usr/lib/python3.13/site-packages/wormhole/wormhole.py` | `create()` API |
