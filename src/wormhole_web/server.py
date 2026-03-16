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
            filename = sanitize_filename(raw_filename)
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
