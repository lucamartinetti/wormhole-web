"""Tests for wormhole_web.server resources."""

import os
import tempfile
from unittest.mock import MagicMock, patch, PropertyMock

from twisted.internet import defer, task
from twisted.trial import unittest
from twisted.web.test.requesthelper import DummyRequest

from wormhole_web.receiver import BadCodeError, ReceiveError
from wormhole_web.server import (
    HealthResource,
    ReceiveCodeResource,
    ReceiveResource,
    RootResource,
    make_site,
)
from wormhole_web.streaming import StreamingRequest
from wormhole_web.util import WormholeTimeout


class TestHealthResource(unittest.TestCase):
    def test_health_returns_ok(self):
        resource = HealthResource()
        request = DummyRequest(b"/health")
        request.method = b"GET"
        result = resource.render_GET(request)
        self.assertEqual(result, b"ok")


class TestRootResource(unittest.TestCase):
    def test_serves_index_html(self):
        """RootResource serves index.html at GET / when static dir exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            static_dir = os.path.join(tmpdir, "static")
            os.makedirs(static_dir)
            index_path = os.path.join(static_dir, "index.html")
            with open(index_path, "wb") as f:
                f.write(b"<html>hello</html>")

            with patch("wormhole_web.server.os.path.dirname", return_value=tmpdir):
                root = RootResource(reactor=task.Clock())

            request = DummyRequest(b"/")
            request.method = b"GET"
            result = root.render_GET(request)
            self.assertEqual(result, b"<html>hello</html>")

    def test_returns_404_when_static_dir_missing(self):
        """RootResource returns 404 when static dir does not exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # No static dir created
            with patch("wormhole_web.server.os.path.dirname", return_value=tmpdir):
                root = RootResource(reactor=task.Clock())

            request = DummyRequest(b"/")
            request.method = b"GET"
            result = root.render_GET(request)
            self.assertEqual(result, b"web UI not found")
            self.assertEqual(request.responseCode, 404)


class TestReceiveResource(unittest.TestCase):
    def test_get_child_returns_receive_code_resource(self):
        """ReceiveResource.getChild returns ReceiveCodeResource with correct code."""
        clock = task.Clock()
        res = ReceiveResource(clock, transfer_timeout=60)
        request = DummyRequest(b"/receive/7-guitar-revenge")
        child = res.getChild(b"7-guitar-revenge", request)
        self.assertIsInstance(child, ReceiveCodeResource)
        self.assertEqual(child._code, "7-guitar-revenge")


class TestReceiveCodeResource(unittest.TestCase):
    @defer.inlineCallbacks
    def test_returns_404_on_bad_code_error(self):
        """_do_receive returns 404 on BadCodeError."""
        clock = task.Clock()
        res = ReceiveCodeResource("bad-code", clock, transfer_timeout=60)
        request = DummyRequest(b"/receive/bad-code")
        request.method = b"GET"

        with patch("wormhole_web.server.receive_file") as mock_recv:
            mock_recv.return_value = defer.fail(BadCodeError("invalid code"))
            yield res._do_receive(request)

        self.assertEqual(request.responseCode, 404)
        self.assertIn(b"invalid or expired wormhole code", b"".join(request.written))

    @defer.inlineCallbacks
    def test_returns_408_on_wormhole_timeout(self):
        """_do_receive returns 408 on WormholeTimeout."""
        clock = task.Clock()
        res = ReceiveCodeResource("slow-code", clock, transfer_timeout=60)
        request = DummyRequest(b"/receive/slow-code")
        request.method = b"GET"

        with patch("wormhole_web.server.receive_file") as mock_recv:
            mock_recv.return_value = defer.fail(WormholeTimeout("timed out"))
            yield res._do_receive(request)

        self.assertEqual(request.responseCode, 408)
        self.assertIn(b"timeout waiting for sender", b"".join(request.written))

    @defer.inlineCallbacks
    def test_returns_500_on_receive_error(self):
        """_do_receive returns 500 on ReceiveError."""
        clock = task.Clock()
        res = ReceiveCodeResource("err-code", clock, transfer_timeout=60)
        request = DummyRequest(b"/receive/err-code")
        request.method = b"GET"

        with patch("wormhole_web.server.receive_file") as mock_recv:
            mock_recv.return_value = defer.fail(ReceiveError("something broke"))
            yield res._do_receive(request)

        self.assertEqual(request.responseCode, 500)
        self.assertIn(b"wormhole error: something broke", b"".join(request.written))

    @defer.inlineCallbacks
    def test_handles_fly_replay(self):
        """_do_receive sets fly-replay header and finishes when fly_router returns replay."""
        clock = task.Clock()
        fly_router = MagicMock()
        fly_router.get_replay_header = MagicMock(
            return_value=defer.succeed("instance=m2")
        )

        res = ReceiveCodeResource(
            "routed-code", clock, transfer_timeout=60, fly_router=fly_router
        )
        request = DummyRequest(b"/receive/routed-code")
        request.method = b"GET"

        yield res._do_receive(request)

        self.assertEqual(request.responseCode, 200)
        self.assertEqual(
            request.responseHeaders.getRawHeaders(b"fly-replay"),
            [b"instance=m2"],
        )


class TestMakeSite(unittest.TestCase):
    def test_sets_streaming_request_factory(self):
        """make_site sets StreamingRequest as the request factory."""
        clock = task.Clock()
        site = make_site(reactor=clock)
        self.assertIs(site.requestFactory, StreamingRequest)
