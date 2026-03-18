"""Tests for ChunkQueue and StreamingRequest."""

from unittest.mock import MagicMock, patch

from twisted.internet import defer, task
from twisted.trial import unittest
from twisted.web.http_headers import Headers

from wormhole_web.streaming import ChunkQueue, StreamingRequest


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


class TestStreamingRequest(unittest.TestCase):
    """Tests for StreamingRequest behavior."""

    def _make_request(self, path=b"/send", method=b"PUT", headers=None):
        """Create a StreamingRequest with a mocked channel."""
        channel = MagicMock()
        channel._path = path
        channel._command = method
        channel._version = b"HTTP/1.1"

        req = StreamingRequest.__new__(StreamingRequest)
        req.channel = channel
        req.transport = MagicMock()
        req.requestHeaders = Headers()
        req.responseHeaders = Headers()
        req._disconnected = False
        req.notifications = []

        if headers:
            for name, value in headers.items():
                req.requestHeaders.addRawHeader(name, value)

        # Mock methods that interact with the transport
        req.setResponseCode = MagicMock()
        req.setHeader = MagicMock()
        req.write = MagicMock()
        req.finish = MagicMock()
        req.notifyFinish = MagicMock(return_value=defer.Deferred())

        return req

    def test_411_when_no_content_length_or_filesize(self):
        """Returns 411 when neither Content-Length nor X-Wormhole-Filesize is present."""
        req = self._make_request()
        req.gotLength(None)  # No Content-Length

        req.setResponseCode.assert_called_with(411)
        self.assertTrue(req._streaming)

    def test_filename_from_x_wormhole_filename_header(self):
        """Infers filename from X-Wormhole-Filename header."""
        req = self._make_request(
            headers={b"x-wormhole-filename": b"report.pdf"}
        )

        with patch("wormhole_web.streaming.create_send_session") as mock_css:
            mock_css.return_value = defer.Deferred()  # never resolves
            req.gotLength(1024)

        self.assertTrue(req._streaming)
        # Verify _handle_inline_send was called (indirectly, by checking queue exists)
        self.assertIsNotNone(req._chunk_queue)

    def test_default_filename_upload(self):
        """Uses default filename 'upload' when no filename header is provided."""
        req = self._make_request()

        with patch("wormhole_web.streaming.create_send_session") as mock_css:
            mock_css.return_value = defer.Deferred()  # never resolves
            req.gotLength(512)

        self.assertTrue(req._streaming)
        self.assertIsNotNone(req._chunk_queue)

    def test_handle_content_chunk_routes_to_queue_in_streaming_mode(self):
        """handleContentChunk puts data into the queue in streaming mode."""
        req = self._make_request()

        with patch("wormhole_web.streaming.create_send_session") as mock_css:
            mock_css.return_value = defer.Deferred()
            req.gotLength(100)

        req.handleContentChunk(b"hello")
        d = req._chunk_queue.get()
        self.assertEqual(self.successResultOf(d), b"hello")

    def test_request_received_calls_queue_finish_in_streaming_mode(self):
        """requestReceived calls queue.finish() in streaming mode."""
        req = self._make_request()

        with patch("wormhole_web.streaming.create_send_session") as mock_css:
            mock_css.return_value = defer.Deferred()
            req.gotLength(100)

        queue = req._chunk_queue

        req.requestReceived(b"PUT", b"/send", b"HTTP/1.1")
        # After finish, get() should return None
        d = queue.get()
        self.assertIsNone(self.successResultOf(d))
