"""Integration tests — our server against the magic-wormhole library.

These tests use the public relay and may be flaky if the relay is down.

Note: Tests run the server as a subprocess to avoid pytest-twisted reactor
conflicts with concurrent wormhole operations.
"""

import hashlib
import os
import subprocess
import tempfile

import pytest


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

    def test_receive_file(self, server_url, test_file):
        src_path, expected_hash, size = test_file

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
    """Test: our server sends via PUT /send → wormhole library receives."""

    def test_send_file(self, server_url, test_file):
        src_path, expected_hash, size = test_file

        # Start inline send (curl -T file http://host/send)
        # -N disables output buffering so we can read the first line immediately
        upload = subprocess.Popen(
            ["curl", "-sfN", "-T", src_path,
             "-H", "X-Wormhole-Filename: testfile.bin",
             f"{server_url}/send"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Read the first line of output — should be "wormhole receive <code>"
        first_line = upload.stdout.readline().strip()
        assert first_line.startswith("wormhole receive "), f"Bad first line: {first_line!r}"
        code = first_line.split()[-1]
        assert "-" in code

        # Run receiver
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

        stdout = upload.communicate(timeout=10)[0]
        assert "transfer complete" in stdout
