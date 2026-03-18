"""Fly.io-specific machine discovery and request routing.

Uses Fly's internal API to discover running machines, then wraps
a :class:`CodeRouter` to decide whether a receive request should be
handled locally or replayed to the owning machine.
"""

import json
import os
import time
import urllib.request

from twisted.internet import defer, threads
from twisted.python import log

from wormhole_web.routing import CodeRouter


class FlyRouter:
    """Discover Fly machines, maintain a hash ring, emit replay headers."""

    def __init__(self, app_name, my_machine_id, cache_ttl=10):
        self._app_name = app_name
        self._my_id = my_machine_id
        self._cache_ttl = cache_ttl

        self._cached_machines = None
        self._cache_time = 0.0
        self._router = None

        self._api_url = (
            f"http://_api.internal:4280/v1/apps/{app_name}/machines"
        )

        self._local_codes = set()

        log.msg(
            f"routing: enabled my_id={self._my_id} app={self._app_name}"
        )

    def _fetch_machines_sync(self):
        """Synchronous HTTP call to Fly API (runs in thread)."""
        req = urllib.request.Request(self._api_url)
        token = os.environ.get("FLY_API_TOKEN")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())

    # -- machine discovery --------------------------------------------------

    @defer.inlineCallbacks
    def get_machines(self):
        """Return a list of running machine IDs (cached with TTL)."""
        now = time.monotonic()
        if (
            self._cached_machines is not None
            and now - self._cache_time < self._cache_ttl
        ):
            log.msg(
                f"fly: discovered machines={self._cached_machines} "
                f"count={len(self._cached_machines)} cached=true"
            )
            defer.returnValue(self._cached_machines)

        try:
            data = yield threads.deferToThread(self._fetch_machines_sync)


            machines = [
                m["id"] for m in data if m.get("state") == "started"
            ]

            old_machines = self._cached_machines or []
            old_set = set(old_machines)
            new_set = set(machines)
            added = sorted(new_set - old_set)
            removed = sorted(old_set - new_set)

            self._cached_machines = machines
            self._cache_time = now

            log.msg(
                f"fly: discovered machines={machines} "
                f"count={len(machines)} cached=false"
            )

            if old_machines and (added or removed):
                log.msg(
                    f"fly: ring updated old_count={len(old_machines)} "
                    f"new_count={len(machines)} "
                    f"added={added} removed={removed}"
                )

            self._router = CodeRouter(machines) if machines else None
            defer.returnValue(machines)

        except Exception as exc:
            using_cached = self._cached_machines is not None
            log.msg(
                f'fly: machine discovery failed error="{exc}" '
                f"using_cached={str(using_cached).lower()}"
            )
            if using_cached:
                defer.returnValue(self._cached_machines)
            else:
                # Fall back to single-instance mode: only us
                defer.returnValue([self._my_id])

    # -- local code registration --------------------------------------------

    def register_local_code(self, code):
        """Mark *code* as owned by this instance (created locally)."""
        self._local_codes.add(code)
        log.msg(f"routing: registered local code={code}")

    def unregister_local_code(self, code):
        """Remove *code* from the local set."""
        self._local_codes.discard(code)
        log.msg(f"routing: unregistered local code={code}")

    # -- routing helpers ----------------------------------------------------

    @defer.inlineCallbacks
    def get_replay_header(self, code):
        """Return ``"instance=<machine-id>"`` if we should replay, else None.

        Also refreshes the machine list / hash ring as needed.
        """
        if code in self._local_codes:
            log.msg(
                f"routing: code={code} action=handle (local override)"
            )
            defer.returnValue(None)

        machines = yield self.get_machines()

        if not self._router:
            self._router = CodeRouter(machines) if machines else None

        if self._router is None:
            # Can't route; handle locally.
            defer.returnValue(None)

        target = self._router.get_target(code)

        if target == self._my_id:
            log.msg(
                f"routing: code={code} target={target} "
                f"my_id={self._my_id} action=handle"
            )
            defer.returnValue(None)
        else:
            log.msg(
                f"routing: code={code} target={target} "
                f"my_id={self._my_id} action=replay"
            )
            defer.returnValue(f"instance={target}")
