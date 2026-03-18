"""Pytest configuration and shared fixtures for tests."""

import hashlib
import os
import signal
import socket
import subprocess
import tempfile
import time

import pytest


def _find_free_port():
    """Find a free TCP port by briefly binding to port 0."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server_url():
    """Start wormhole-web server as a subprocess, yield its URL."""
    port = _find_free_port()
    proc = subprocess.Popen(
        ["uv", "run", "wormhole-web", "--port", str(port)],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for server to be ready
    url = f"http://127.0.0.1:{port}"
    for _ in range(20):
        try:
            result = subprocess.run(
                ["curl", "-sf", f"{url}/health"],
                capture_output=True, timeout=2,
            )
            if result.returncode == 0:
                break
        except subprocess.TimeoutExpired:
            pass
        time.sleep(0.5)
    else:
        proc.kill()
        raise RuntimeError("Server failed to start")

    yield url

    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture
def test_file():
    """Create a temp file with 100KB random content and its checksum."""
    data = os.urandom(1024 * 100)
    sha = hashlib.sha256(data).hexdigest()
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
        f.write(data)
        path = f.name
    yield path, sha, len(data)
    os.unlink(path)
