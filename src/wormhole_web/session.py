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
