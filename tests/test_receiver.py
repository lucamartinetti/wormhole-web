"""Tests for wormhole_web.receiver — receive logic."""

from unittest.mock import MagicMock, patch

from twisted.internet import defer, task
from twisted.trial import unittest

from wormhole.util import dict_to_bytes

from wormhole_web.receiver import (
    BadCodeError,
    FileOffer,
    OfferError,
    ReceiveError,
    receive_file,
)
from wormhole_web.util import WormholeTimeout


class TestReceiveFile(unittest.TestCase):
    def _make_wormhole(self, messages=None, key_error=None):
        """Create a mock wormhole object.

        Args:
            messages: List of message dicts to return from get_message.
            key_error: If set, get_unverified_key will fail with this exception.
        """
        w = MagicMock()
        w.set_code = MagicMock()
        w.close = MagicMock(return_value=defer.succeed(None))
        w.send_message = MagicMock()
        w.derive_key = MagicMock(return_value=b"\x00" * 32)

        if key_error:
            w.get_unverified_key = MagicMock(return_value=defer.fail(key_error))
        else:
            w.get_unverified_key = MagicMock(return_value=defer.succeed(b"key"))

        w.get_verifier = MagicMock(return_value=defer.succeed(b"verifier"))

        if messages:
            msg_iter = iter([dict_to_bytes(m) for m in messages])
            w.get_message = MagicMock(
                side_effect=lambda: defer.succeed(next(msg_iter))
            )

        return w

    @defer.inlineCallbacks
    def test_returns_file_offer_with_correct_metadata(self):
        """receive_file returns a FileOffer with correct filename and filesize."""
        clock = task.Clock()

        transit_msg = {
            "transit": {
                "abilities-v1": [{"type": "relay-v1"}],
                "hints-v1": [],
            }
        }
        offer_msg = {
            "offer": {
                "file": {
                    "filename": "document.pdf",
                    "filesize": 4096,
                }
            }
        }

        w = self._make_wormhole(messages=[transit_msg, offer_msg])
        mock_connection = MagicMock()

        mock_tr = MagicMock()
        mock_tr.get_connection_abilities = MagicMock(return_value=[{"type": "relay-v1"}])
        mock_tr.get_connection_hints = MagicMock(return_value=defer.succeed([]))
        mock_tr.add_connection_hints = MagicMock()
        mock_tr.connect = MagicMock(return_value=defer.succeed(mock_connection))

        with patch("wormhole_web.receiver.create", return_value=w), \
             patch("wormhole_web.receiver.TransitReceiver", return_value=mock_tr):
            offer, connection, wormhole = yield receive_file("7-test-code", clock, timeout=60)

        self.assertIsInstance(offer, FileOffer)
        self.assertEqual(offer.filename, "document.pdf")
        self.assertEqual(offer.filesize, 4096)
        self.assertIs(connection, mock_connection)
        self.assertIs(wormhole, w)

    @defer.inlineCallbacks
    def test_raises_bad_code_error_on_key_exchange_failure(self):
        """receive_file raises BadCodeError when key exchange fails."""
        clock = task.Clock()
        w = self._make_wormhole(key_error=Exception("wrong password"))

        with patch("wormhole_web.receiver.create", return_value=w):
            try:
                yield receive_file("bad-code", clock, timeout=60)
                self.fail("Expected BadCodeError")
            except BadCodeError as e:
                self.assertIn("Key exchange failed", str(e))

        # Verify wormhole was closed on error
        w.close.assert_called_once()

    @defer.inlineCallbacks
    def test_raises_offer_error_on_non_file_offer(self):
        """receive_file raises OfferError when the offer is not a file offer."""
        clock = task.Clock()

        transit_msg = {
            "transit": {
                "abilities-v1": [{"type": "relay-v1"}],
                "hints-v1": [],
            }
        }
        offer_msg = {
            "offer": {
                "directory": {"dirname": "mydir", "numfiles": 3}
            }
        }

        w = self._make_wormhole(messages=[transit_msg, offer_msg])

        mock_tr = MagicMock()
        mock_tr.get_connection_abilities = MagicMock(return_value=[{"type": "relay-v1"}])
        mock_tr.get_connection_hints = MagicMock(return_value=defer.succeed([]))
        mock_tr.add_connection_hints = MagicMock()

        with patch("wormhole_web.receiver.create", return_value=w), \
             patch("wormhole_web.receiver.TransitReceiver", return_value=mock_tr):
            try:
                yield receive_file("dir-code", clock, timeout=60)
                self.fail("Expected OfferError")
            except OfferError as e:
                self.assertIn("Only single-file", str(e))

        # Verify wormhole was closed on error
        w.close.assert_called_once()

    @defer.inlineCallbacks
    def test_raises_wormhole_timeout(self):
        """receive_file raises WormholeTimeout when key exchange times out."""
        clock = task.Clock()
        w = MagicMock()
        w.set_code = MagicMock()
        w.close = MagicMock(return_value=defer.succeed(None))
        # get_unverified_key never resolves
        w.get_unverified_key = MagicMock(return_value=defer.Deferred())

        with patch("wormhole_web.receiver.create", return_value=w):
            d = receive_file("slow-code", clock, timeout=5)
            clock.advance(6)

            try:
                yield d
                self.fail("Expected WormholeTimeout")
            except WormholeTimeout:
                pass

        # Verify wormhole was closed on error
        w.close.assert_called()

    @defer.inlineCallbacks
    def test_closes_wormhole_on_error(self):
        """receive_file closes the wormhole when any error occurs."""
        clock = task.Clock()
        w = self._make_wormhole(key_error=RuntimeError("unexpected"))

        with patch("wormhole_web.receiver.create", return_value=w):
            try:
                yield receive_file("err-code", clock, timeout=60)
            except BadCodeError:
                pass

        w.close.assert_called_once()
