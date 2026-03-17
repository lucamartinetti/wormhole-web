"""E2E compatibility tests with wormhole CLI binaries.

Requires `wormhole` (Python) and/or `wormhole-rs` installed on PATH.
Uses the public relay — may be flaky.

The server runs as a subprocess to avoid blocking issues with
the Twisted reactor and synchronous subprocess calls.
"""

import hashlib
import os
import re
import signal
import socket
import subprocess
import tempfile
import time

import pytest


def has_command(cmd):
    try:
        subprocess.run([cmd, "--version"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


requires_wormhole = pytest.mark.skipif(
    not has_command("wormhole"), reason="wormhole CLI not installed"
)
requires_wormhole_rs = pytest.mark.skipif(
    not has_command("wormhole-rs"), reason="wormhole-rs CLI not installed"
)


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
    """Create a temp file with 100KB random content."""
    data = os.urandom(1024 * 100)
    sha = hashlib.sha256(data).hexdigest()
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
        f.write(data)
        path = f.name
    yield path, sha, len(data)
    os.unlink(path)


def _extract_code_python_wormhole(proc, timeout=15):
    """Read stderr lines from a `wormhole send` process to find the code.

    The Python wormhole CLI prints to stderr:
        Wormhole code is: <code>
        On the other computer, please run:

        wormhole receive <code>
    """
    import select
    import os as _os

    deadline = time.time() + timeout
    buf = b""
    fd = proc.stderr.fileno()

    while time.time() < deadline:
        remaining = deadline - time.time()
        r, _, _ = select.select([fd], [], [], min(remaining, 0.5))
        if r:
            chunk = _os.read(fd, 4096)
            if not chunk:
                break
            buf += chunk
            text = buf.decode("utf-8", errors="replace")
            # Look for "wormhole receive <code>" pattern
            m = re.search(r"wormhole receive\s+(\S+)", text)
            if m:
                return m.group(1)

    return None


def _extract_code_wormhole_rs(proc, timeout=15):
    """Read stdout lines from a `wormhole-rs send` process to find the code.

    The wormhole-rs CLI prints to stdout:
        This wormhole's code is: <code> (it has been copied to your clipboard)
    """
    import select
    import os as _os

    deadline = time.time() + timeout
    buf = b""
    fd = proc.stdout.fileno()

    while time.time() < deadline:
        remaining = deadline - time.time()
        r, _, _ = select.select([fd], [], [], min(remaining, 0.5))
        if r:
            chunk = _os.read(fd, 4096)
            if not chunk:
                break
            buf += chunk
            text = buf.decode("utf-8", errors="replace")
            # Look for "code is: <code>" pattern
            m = re.search(r"code is:\s+(\S+)", text)
            if m:
                code = m.group(1)
                # Strip trailing parenthetical like "(it has been copied...)"
                code = re.sub(r"\s*\(.*", "", code).strip()
                return code

    return None


@requires_wormhole
class TestPythonCLI:
    def test_cli_send_web_receive(self, server_url, test_file):
        """wormhole send → curl receive via our server."""
        src_path, expected_hash, _ = test_file

        sender = subprocess.Popen(
            ["wormhole", "send", "--hide-progress", src_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        code = _extract_code_python_wormhole(sender)
        assert code is not None, "Failed to get wormhole code from sender"

        with tempfile.NamedTemporaryFile(delete=False) as dst:
            dst_path = dst.name

        try:
            result = subprocess.run(
                ["curl", "-sf", "-o", dst_path,
                 f"{server_url}/receive/{code}"],
                timeout=60,
            )
            assert result.returncode == 0

            with open(dst_path, "rb") as f:
                assert hashlib.sha256(f.read()).hexdigest() == expected_hash
        finally:
            os.unlink(dst_path)
            sender.wait(timeout=10)

    def test_web_send_cli_receive(self, server_url, test_file):
        """curl send via our server → wormhole receive."""
        src_path, expected_hash, size = test_file

        # Start inline send (curl -T to /send, reads code from first line)
        upload = subprocess.Popen(
            ["curl", "-sN", "-T", src_path,
             "-H", f"X-Wormhole-Filename: {os.path.basename(src_path)}",
             f"{server_url}/send"],
            stdout=subprocess.PIPE,
            text=True,
        )

        # Read first line to get the code
        first_line = upload.stdout.readline().strip()
        assert first_line.startswith("wormhole receive "), f"Bad first line: {first_line!r}"
        code = first_line.split()[-1]

        # Receive with wormhole CLI
        with tempfile.TemporaryDirectory() as tmpdir:
            recv = subprocess.run(
                ["wormhole", "receive", "--accept-file",
                 "-o", tmpdir, code],
                capture_output=True, text=True, timeout=60,
            )
            assert recv.returncode == 0

            files = os.listdir(tmpdir)
            assert len(files) == 1
            with open(os.path.join(tmpdir, files[0]), "rb") as f:
                assert hashlib.sha256(f.read()).hexdigest() == expected_hash

        stdout = upload.communicate(timeout=10)[0]
        assert "transfer complete" in stdout


@requires_wormhole_rs
class TestRustCLI:
    def test_rs_send_web_receive(self, server_url, test_file):
        """wormhole-rs send → curl receive via our server."""
        src_path, expected_hash, _ = test_file

        sender = subprocess.Popen(
            ["wormhole-rs", "send", src_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        code = _extract_code_wormhole_rs(sender)
        assert code is not None, "Failed to get wormhole code from wormhole-rs sender"

        with tempfile.NamedTemporaryFile(delete=False) as dst:
            dst_path = dst.name

        try:
            result = subprocess.run(
                ["curl", "-sf", "-o", dst_path,
                 f"{server_url}/receive/{code}"],
                timeout=60,
            )
            assert result.returncode == 0

            with open(dst_path, "rb") as f:
                assert hashlib.sha256(f.read()).hexdigest() == expected_hash
        finally:
            os.unlink(dst_path)
            sender.wait(timeout=10)

    def test_web_send_rs_receive(self, server_url, test_file):
        """curl send via our server → wormhole-rs receive."""
        src_path, expected_hash, size = test_file

        upload = subprocess.Popen(
            ["curl", "-sN", "-T", src_path,
             "-H", f"X-Wormhole-Filename: {os.path.basename(src_path)}",
             f"{server_url}/send"],
            stdout=subprocess.PIPE,
            text=True,
        )

        first_line = upload.stdout.readline().strip()
        assert first_line.startswith("wormhole receive "), f"Bad first line: {first_line!r}"
        code = first_line.split()[-1]

        with tempfile.TemporaryDirectory() as tmpdir:
            recv = subprocess.run(
                ["wormhole-rs", "receive", "--noconfirm",
                 "--out-dir", tmpdir, code],
                capture_output=True, text=True, timeout=60,
            )
            assert recv.returncode == 0

            files = os.listdir(tmpdir)
            assert len(files) == 1
            with open(os.path.join(tmpdir, files[0]), "rb") as f:
                assert hashlib.sha256(f.read()).hexdigest() == expected_hash

        stdout = upload.communicate(timeout=10)[0]
        assert "transfer complete" in stdout
