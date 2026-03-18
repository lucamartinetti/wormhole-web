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
    dl.addCallback(lambda result: result[0])

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
    # Replace double quotes to avoid breaking Content-Disposition headers
    name = name.replace('"', "_")
    # Normalize path separators and take basename
    name = name.replace("\\", "/")
    name = os.path.basename(name)
    # Fallback if empty
    return name if name else "upload"
