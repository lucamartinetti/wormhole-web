from twisted.trial import unittest
from twisted.web.test.requesthelper import DummyRequest

from wormhole_web.server import HealthResource


class TestHealthResource(unittest.TestCase):
    def test_health_returns_ok(self):
        resource = HealthResource()
        request = DummyRequest(b"/health")
        request.method = b"GET"
        result = resource.render_GET(request)
        self.assertEqual(result, b"ok")
