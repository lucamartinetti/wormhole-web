from twisted.internet import defer, task
from twisted.trial import unittest as trial_unittest

from wormhole_web.util import WormholeTimeout, sanitize_filename, with_timeout


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


class TestWithTimeout(trial_unittest.TestCase):
    """Tests for with_timeout() using a deterministic Clock."""

    def test_returns_result_before_timeout(self):
        """When the deferred fires before the timeout, return its result."""
        clock = task.Clock()
        d = defer.Deferred()
        result_d = with_timeout(d, 10, clock)

        d.callback("success")
        self.assertEqual(self.successResultOf(result_d), "success")

    def test_raises_wormhole_timeout_on_expiry(self):
        """When timeout expires first, errback with WormholeTimeout."""
        clock = task.Clock()
        d = defer.Deferred()
        result_d = with_timeout(d, 5, clock, msg="timed out")

        clock.advance(6)
        f = self.failureResultOf(result_d)
        f.trap(WormholeTimeout)
        self.assertIn("timed out", str(f.value))

    def test_cancels_original_deferred_on_timeout(self):
        """When timeout fires, the original deferred should be cancelled."""
        clock = task.Clock()
        cancelled = [False]

        def on_cancel(_):
            cancelled[0] = True

        d = defer.Deferred(on_cancel)
        result_d = with_timeout(d, 5, clock)

        clock.advance(6)
        # Consume the timeout error
        self.failureResultOf(result_d)
        self.assertTrue(cancelled[0])
