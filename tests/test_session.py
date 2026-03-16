from unittest.mock import MagicMock
from twisted.internet import task

from wormhole_web.session import SessionManager, Session, SessionState


class TestSessionManager:
    def setup_method(self):
        self.clock = task.Clock()
        self.manager = SessionManager(
            max_sessions=3,
            session_ttl=60,
            reactor=self.clock,
        )

    def test_create_session_returns_session(self):
        wormhole = MagicMock()
        session = self.manager.create("7-guitarist-revenge", wormhole)
        assert isinstance(session, Session)
        assert session.code == "7-guitarist-revenge"
        assert session.state == SessionState.WAITING_FOR_UPLOAD

    def test_get_session_by_code(self):
        wormhole = MagicMock()
        self.manager.create("7-guitarist-revenge", wormhole)
        session = self.manager.get("7-guitarist-revenge")
        assert session is not None
        assert session.code == "7-guitarist-revenge"

    def test_get_nonexistent_returns_none(self):
        assert self.manager.get("nonexistent") is None

    def test_max_sessions_enforced(self):
        for i in range(3):
            self.manager.create(f"{i}-code", MagicMock())
        assert self.manager.is_full()

    def test_remove_session(self):
        wormhole = MagicMock()
        self.manager.create("7-guitarist-revenge", wormhole)
        self.manager.remove("7-guitarist-revenge")
        assert self.manager.get("7-guitarist-revenge") is None
        assert not self.manager.is_full()

    def test_ttl_cleanup(self):
        wormhole = MagicMock()
        self.manager.create("7-guitarist-revenge", wormhole)
        self.clock.advance(61)
        assert self.manager.get("7-guitarist-revenge") is None
        wormhole.close.assert_called_once()

    def test_ttl_not_triggered_when_uploading(self):
        wormhole = MagicMock()
        session = self.manager.create("7-guitarist-revenge", wormhole)
        session.state = SessionState.UPLOADING
        self.clock.advance(61)
        assert self.manager.get("7-guitarist-revenge") is not None


class TestSession:
    def test_initial_state(self):
        session = Session(code="7-test", wormhole=MagicMock())
        assert session.state == SessionState.WAITING_FOR_UPLOAD
        assert session.code == "7-test"

    def test_transition_to_uploading(self):
        session = Session(code="7-test", wormhole=MagicMock())
        assert session.claim_upload()
        assert session.state == SessionState.UPLOADING

    def test_double_claim_fails(self):
        session = Session(code="7-test", wormhole=MagicMock())
        assert session.claim_upload()
        assert not session.claim_upload()
