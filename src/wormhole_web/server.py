"""Twisted Web HTTP server for wormhole-web."""

import mimetypes
import os

from twisted.internet import defer, endpoints
from twisted.internet import reactor as default_reactor
from twisted.web import resource, server, static

from wormhole_web.constants import (
    DEFAULT_MAX_SESSIONS,
    DEFAULT_PORT,
    DEFAULT_SESSION_TTL,
    DEFAULT_TRANSFER_TIMEOUT,
)
from wormhole_web.receiver import (
    BadCodeError,
    ReceiveError,
    receive_file,
)
from wormhole_web.util import WormholeTimeout, sanitize_filename
from wormhole_web.sender import create_send_session, start_key_exchange
from wormhole_web.session import SessionManager
from wormhole_web.streaming import StreamingRequest


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

        # Serve index.html at GET / (empty child path)
        if self._index_html:
            self.putChild(b"", _IndexResource(self._index_html))

        self.putChild(b"health", HealthResource())
        self.putChild(
            b"receive", ReceiveResource(self._reactor, self._transfer_timeout)
        )
        self.putChild(
            b"send",
            SendResource(session_manager, self._reactor, self._transfer_timeout),
        )

    def render_GET(self, request):
        """Serve the web UI at GET / (direct hit without trailing slash)."""
        if self._index_html:
            request.setHeader(b"content-type", b"text/html; charset=utf-8")
            return self._index_html
        request.setResponseCode(404)
        return b"web UI not found"


class _IndexResource(resource.Resource):
    """Serves index.html for the empty-string child (GET /)."""
    isLeaf = True

    def __init__(self, html_bytes):
        super().__init__()
        self._html = html_bytes

    def render_GET(self, request):
        request.setHeader(b"content-type", b"text/html; charset=utf-8")
        return self._html


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


# --- CLI entry point ---


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Wormhole Web — HTTP gateway")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="0.0.0.0")
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
    endpoint = endpoints.TCP4ServerEndpoint(default_reactor, args.port, interface=args.host)
    endpoint.listen(site)
    print(f"wormhole-web listening on :{args.port}")
    default_reactor.run()


if __name__ == "__main__":
    main()
