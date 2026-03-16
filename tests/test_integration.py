"""Integration tests — our server against the magic-wormhole library.

These tests use the public relay and may be flaky if the relay is down.

Note: Tests run the server as a subprocess to avoid pytest-twisted reactor
conflicts with concurrent wormhole operations.
"""

import hashlib
import json
import os
import signal
import socket
import subprocess
import tempfile
import time

import pytest


def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server_url():
    """Start wormhole-web server as a subprocess."""
    port = _find_free_port()
    proc = subprocess.Popen(
        ["uv", "run", "wormhole-web", "--port", str(port)],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

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
def test_data():
    """Generate 10KB of random test data with checksum."""
    data = os.urandom(1024 * 10)
    sha = hashlib.sha256(data).hexdigest()
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
        f.write(data)
        path = f.name
    yield path, sha, len(data)
    os.unlink(path)


def _run_wormhole_script(script, timeout=60):
    """Run a Python script that uses the wormhole library.

    Returns stdout as string. The script runs with the Twisted reactor.
    """
    result = subprocess.run(
        ["uv", "run", "python", "-c", script],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Script failed:\nstdout: {result.stdout}\nstderr: {result.stderr}")
    return result.stdout.strip()


class TestReceivePath:
    """Test: wormhole library sends → our server receives via HTTP."""

    def test_receive_file(self, server_url, test_data):
        src_path, expected_hash, size = test_data

        # Run sender + curl receiver in a script
        script = f'''
import os, sys, hashlib, tempfile, subprocess
from twisted.internet import defer, reactor
from wormhole import create
from wormhole.transit import TransitSender
from wormhole.util import dict_to_bytes, bytes_to_dict

APPID = "lothar.com/wormhole/text-or-file-xfer"
RELAY = "ws://relay.magic-wormhole.io:4000/v1"
TRANSIT = "tcp:transit.magic-wormhole.io:4001"

@defer.inlineCallbacks
def main():
    data = open("{src_path}", "rb").read()

    # Create sender wormhole
    w = create(APPID, RELAY, reactor)
    w.allocate_code()
    code = yield w.get_code()

    # Start curl to receive via our server (in background)
    dst = tempfile.NamedTemporaryFile(delete=False, suffix=".bin")
    dst.close()
    curl = subprocess.Popen(
        ["curl", "-sf", "-o", dst.name,
         "{server_url}/receive/" + code],
    )

    # Do the sender protocol
    yield w.get_unverified_key()
    yield w.get_verifier()

    ts = TransitSender(TRANSIT, no_listen=True, reactor=reactor)
    ts.set_transit_key(w.derive_key(APPID + "/transit-key", 32))
    our_hints = yield ts.get_connection_hints()
    w.send_message(dict_to_bytes({{
        "transit": {{"abilities-v1": ts.get_connection_abilities(), "hints-v1": our_hints}}
    }}))
    w.send_message(dict_to_bytes({{
        "offer": {{"file": {{"filename": "testfile.bin", "filesize": len(data)}}}}
    }}))

    peer_msg = bytes_to_dict((yield w.get_message()))
    ts.add_connection_hints(peer_msg["transit"]["hints-v1"])
    answer = bytes_to_dict((yield w.get_message()))
    assert answer.get("answer", {{}}).get("file_ack") == "ok"

    connection = yield ts.connect()
    offset = 0
    while offset < len(data):
        chunk = data[offset:offset + 262144]
        connection.send_record(chunk)
        offset += len(chunk)
    connection.close()
    yield w.close()

    curl.wait(timeout=30)
    assert curl.returncode == 0

    received = open(dst.name, "rb").read()
    actual_hash = hashlib.sha256(received).hexdigest()
    os.unlink(dst.name)

    print(f"hash_match:{{actual_hash == '{expected_hash}'}}")
    print(f"size_match:{{len(received) == {size}}}")
    reactor.stop()

reactor.callWhenRunning(main)
reactor.run()
'''
        output = _run_wormhole_script(script, timeout=60)
        assert "hash_match:True" in output
        assert "size_match:True" in output


class TestSendPath:
    """Test: our server sends via HTTP → wormhole library receives."""

    def test_send_file(self, server_url, test_data):
        src_path, expected_hash, size = test_data

        # Step 1: Get a code from our server
        result = subprocess.run(
            ["curl", "-sf", "-X", "POST", f"{server_url}/send/new"],
            capture_output=True, text=True, timeout=30,
        )
        code = result.stdout.strip()
        assert "-" in code, f"Bad code: {code!r}"

        # Step 2: Start upload in background
        upload = subprocess.Popen(
            ["curl", "-sf", "-T", src_path,
             "-H", f"X-Wormhole-Filename: testfile.bin",
             "-H", f"Content-Length: {size}",
             f"{server_url}/send/{code}"],
            stdout=subprocess.PIPE,
            text=True,
        )

        # Step 3: Run receiver script
        script = f'''
import os, sys, hashlib
from twisted.internet import defer, reactor
from wormhole import create
from wormhole.transit import TransitReceiver
from wormhole.util import dict_to_bytes, bytes_to_dict

APPID = "lothar.com/wormhole/text-or-file-xfer"
RELAY = "ws://relay.magic-wormhole.io:4000/v1"
TRANSIT = "tcp:transit.magic-wormhole.io:4001"

@defer.inlineCallbacks
def main():
    w = create(APPID, RELAY, reactor)
    w.set_code("{code}")

    yield w.get_unverified_key()
    yield w.get_verifier()

    tr = TransitReceiver(TRANSIT, no_listen=True, reactor=reactor)
    tr.set_transit_key(w.derive_key(APPID + "/transit-key", 32))
    our_hints = yield tr.get_connection_hints()
    w.send_message(dict_to_bytes({{
        "transit": {{"abilities-v1": tr.get_connection_abilities(), "hints-v1": our_hints}}
    }}))

    msg1 = bytes_to_dict((yield w.get_message()))
    msg2 = bytes_to_dict((yield w.get_message()))

    transit_msg = msg1 if "transit" in msg1 else msg2
    offer_msg = msg1 if "offer" in msg1 else msg2

    assert "offer" in offer_msg
    assert offer_msg["offer"]["file"]["filename"] == "testfile.bin"
    tr.add_connection_hints(transit_msg["transit"]["hints-v1"])

    w.send_message(dict_to_bytes({{"answer": {{"file_ack": "ok"}}}}))

    connection = yield tr.connect()
    received = b""
    while len(received) < {size}:
        record = yield connection.receive_record()
        received += record
    connection.close()
    yield w.close()

    actual_hash = hashlib.sha256(received).hexdigest()
    print(f"hash_match:{{actual_hash == '{expected_hash}'}}")
    print(f"size_match:{{len(received) == {size}}}")
    reactor.stop()

reactor.callWhenRunning(main)
reactor.run()
'''
        output = _run_wormhole_script(script, timeout=60)
        assert "hash_match:True" in output
        assert "size_match:True" in output

        # Check upload completed
        stdout = upload.communicate(timeout=10)[0]
        assert "transfer complete" in stdout


class TestSendPathDelayed:
    """Test: upload completes, receiver connects later."""

    def test_send_file_delayed_receiver(self, server_url, test_data):
        src_path, expected_hash, size = test_data

        # Step 1: Get a code
        result = subprocess.run(
            ["curl", "-sf", "-X", "POST", f"{server_url}/send/new"],
            capture_output=True, text=True, timeout=30,
        )
        code = result.stdout.strip()
        assert "-" in code, f"Bad code: {code!r}"

        # Step 2: Upload file (starts curl, which will block on "waiting for receiver...")
        upload = subprocess.Popen(
            ["curl", "-sf", "-T", src_path,
             "-H", f"X-Wormhole-Filename: testfile.bin",
             "-H", f"Content-Length: {size}",
             f"{server_url}/send/{code}"],
            stdout=subprocess.PIPE,
            text=True,
        )

        # Step 3: Wait 2 seconds — simulating a delayed receiver
        time.sleep(2)

        # Step 4: Now start the receiver
        script = f'''
import os, sys, hashlib
from twisted.internet import defer, reactor
from wormhole import create
from wormhole.transit import TransitReceiver
from wormhole.util import dict_to_bytes, bytes_to_dict

APPID = "lothar.com/wormhole/text-or-file-xfer"
RELAY = "ws://relay.magic-wormhole.io:4000/v1"
TRANSIT = "tcp:transit.magic-wormhole.io:4001"

@defer.inlineCallbacks
def main():
    w = create(APPID, RELAY, reactor)
    w.set_code("{code}")

    yield w.get_unverified_key()
    yield w.get_verifier()

    tr = TransitReceiver(TRANSIT, no_listen=True, reactor=reactor)
    tr.set_transit_key(w.derive_key(APPID + "/transit-key", 32))
    our_hints = yield tr.get_connection_hints()
    w.send_message(dict_to_bytes({{
        "transit": {{"abilities-v1": tr.get_connection_abilities(), "hints-v1": our_hints}}
    }}))

    msg1 = bytes_to_dict((yield w.get_message()))
    msg2 = bytes_to_dict((yield w.get_message()))

    transit_msg = msg1 if "transit" in msg1 else msg2
    offer_msg = msg1 if "offer" in msg1 else msg2

    assert "offer" in offer_msg
    tr.add_connection_hints(transit_msg["transit"]["hints-v1"])

    w.send_message(dict_to_bytes({{"answer": {{"file_ack": "ok"}}}}))

    connection = yield tr.connect()
    received = b""
    while len(received) < {size}:
        record = yield connection.receive_record()
        received += record
    connection.close()
    yield w.close()

    actual_hash = hashlib.sha256(received).hexdigest()
    print(f"hash_match:{{actual_hash == '{expected_hash}'}}")
    print(f"size_match:{{len(received) == {size}}}")
    reactor.stop()

reactor.callWhenRunning(main)
reactor.run()
'''
        output = _run_wormhole_script(script, timeout=60)
        assert "hash_match:True" in output
        assert "size_match:True" in output

        # Check upload completed
        stdout = upload.communicate(timeout=10)[0]
        assert "transfer complete" in stdout
