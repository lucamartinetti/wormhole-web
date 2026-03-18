"""Tests for wormhole_web.sender — send session and transit logic."""

from unittest.mock import MagicMock, patch

from twisted.internet import defer, task
from twisted.trial import unittest

from wormhole.util import dict_to_bytes

from wormhole_web.sender import SendError, complete_send, create_send_session
from wormhole_web.util import WormholeTimeout


class TestCreateSendSession(unittest.TestCase):
    @defer.inlineCallbacks
    def test_allocates_code(self):
        """create_send_session calls allocate_code and returns (code, wormhole)."""
        clock = task.Clock()
        mock_wormhole = MagicMock()
        mock_wormhole.allocate_code = MagicMock()
        mock_wormhole.get_code = MagicMock(return_value=defer.succeed("7-guitar-revenge"))

        with patch("wormhole_web.sender.create", return_value=mock_wormhole):
            code, w = yield create_send_session(clock)

        self.assertEqual(code, "7-guitar-revenge")
        self.assertIs(w, mock_wormhole)
        mock_wormhole.allocate_code.assert_called_once()


class TestCompleteSend(unittest.TestCase):
    def _make_wormhole(self, messages):
        """Create a mock wormhole that yields the given messages in order."""
        w = MagicMock()
        w.get_unverified_key = MagicMock(return_value=defer.succeed(b"key"))
        w.get_verifier = MagicMock(return_value=defer.succeed(b"verifier"))
        w.derive_key = MagicMock(return_value=b"\x00" * 32)
        w.send_message = MagicMock()

        msg_iter = iter(messages)
        w.get_message = MagicMock(side_effect=lambda: defer.succeed(next(msg_iter)))

        return w

    @defer.inlineCallbacks
    def test_sends_transit_hints_and_file_offer(self):
        """complete_send sends transit hints and file offer messages."""
        clock = task.Clock()

        transit_msg = dict_to_bytes({
            "transit": {
                "abilities-v1": [{"type": "relay-v1"}],
                "hints-v1": [],
            }
        })
        answer_msg = dict_to_bytes({
            "answer": {"file_ack": "ok"}
        })

        w = self._make_wormhole([transit_msg, answer_msg])

        mock_ts = MagicMock()
        mock_ts.get_connection_abilities = MagicMock(return_value=[{"type": "relay-v1"}])
        mock_ts.get_connection_hints = MagicMock(return_value=defer.succeed([]))
        mock_ts.add_connection_hints = MagicMock()
        mock_connection = MagicMock()
        mock_ts.connect = MagicMock(return_value=defer.succeed(mock_connection))

        with patch("wormhole_web.sender.TransitSender", return_value=mock_ts):
            connection = yield complete_send(w, "test.txt", 1024, clock, timeout=60)

        self.assertIs(connection, mock_connection)
        # Verify wormhole sent messages (transit hints + file offer)
        self.assertEqual(w.send_message.call_count, 2)

    @defer.inlineCallbacks
    def test_raises_send_error_on_receiver_rejection(self):
        """complete_send raises SendError when receiver sends error message."""
        clock = task.Clock()

        error_msg = dict_to_bytes({"error": "rejected"})
        # Need a second message to not exhaust iterator before error is processed
        transit_msg = dict_to_bytes({
            "transit": {
                "abilities-v1": [{"type": "relay-v1"}],
                "hints-v1": [],
            }
        })

        w = self._make_wormhole([transit_msg, error_msg])

        mock_ts = MagicMock()
        mock_ts.get_connection_abilities = MagicMock(return_value=[{"type": "relay-v1"}])
        mock_ts.get_connection_hints = MagicMock(return_value=defer.succeed([]))
        mock_ts.add_connection_hints = MagicMock()
        mock_ts.connect = MagicMock(return_value=defer.Deferred())

        with patch("wormhole_web.sender.TransitSender", return_value=mock_ts):
            try:
                yield complete_send(w, "test.txt", 1024, clock, timeout=60)
                self.fail("Expected SendError")
            except SendError as e:
                self.assertIn("rejected", str(e))

    @defer.inlineCallbacks
    def test_raises_wormhole_timeout(self):
        """complete_send raises WormholeTimeout when key exchange times out."""
        clock = task.Clock()

        w = MagicMock()
        # get_unverified_key never resolves
        w.get_unverified_key = MagicMock(return_value=defer.Deferred())

        with patch("wormhole_web.sender.TransitSender"):
            d = complete_send(w, "test.txt", 1024, clock, timeout=5)
            clock.advance(6)

            try:
                yield d
                self.fail("Expected WormholeTimeout")
            except WormholeTimeout:
                pass
