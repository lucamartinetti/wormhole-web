"""Wormhole send logic — stream a file to a wormhole receiver."""

from twisted.internet import defer
from wormhole import create
from wormhole.transit import TransitSender
from wormhole.util import dict_to_bytes, bytes_to_dict

from wormhole_web.constants import APPID, RELAY_URL, TRANSIT_RELAY, TRANSIT_KEY_LENGTH
from wormhole_web.util import WormholeTimeout, with_timeout


class SendError(Exception):
    """Base error for send failures."""


@defer.inlineCallbacks
def create_send_session(reactor):
    """Create a wormhole and allocate a code for sending.

    Returns:
        tuple: (code, wormhole_object)
    """
    w = create(APPID, RELAY_URL, reactor)
    w.allocate_code()
    code = yield w.get_code()
    defer.returnValue((code, w))


@defer.inlineCallbacks
def prepare_send(wormhole, filename: str, filesize: int, reactor, timeout=120):
    """Exchange protocol messages and establish transit for sending.

    Must be called after the wormhole code has been allocated.
    Blocks until a receiver connects and accepts the file offer.

    Args:
        wormhole: The wormhole object (from create_send_session).
        filename: Name of the file being sent.
        filesize: Size in bytes.
        reactor: The Twisted reactor.
        timeout: Seconds to wait for receiver before giving up.

    Returns:
        Connection: the transit connection, ready for send_record().

    Raises:
        SendError: If the receiver rejects or something fails.
        WormholeTimeout: If the receiver never connects.
    """
    w = wormhole
    try:
        # Step 1: Complete key exchange FIRST (with timeout)
        yield with_timeout(
            w.get_unverified_key(), timeout, reactor,
            "Timed out waiting for receiver"
        )
        yield w.get_verifier()

        # Step 2: Set up transit sender
        ts = TransitSender(
            TRANSIT_RELAY,
            no_listen=True,  # server doesn't accept inbound connections
            reactor=reactor,
        )

        transit_key = w.derive_key(APPID + "/transit-key", TRANSIT_KEY_LENGTH)
        ts.set_transit_key(transit_key)

        # Step 3: Send our transit hints
        our_abilities = ts.get_connection_abilities()
        our_hints = yield ts.get_connection_hints()
        w.send_message(dict_to_bytes({
            "transit": {
                "abilities-v1": our_abilities,
                "hints-v1": our_hints,
            }
        }))

        # Step 4: Send file offer
        w.send_message(dict_to_bytes({
            "offer": {
                "file": {
                    "filename": filename,
                    "filesize": filesize,
                }
            }
        }))

        # Step 5: Read two messages from receiver: transit hints and file_ack
        # (either order — the receiver may send them differently).
        # We fire the transit connection as soon as we have the receiver's
        # hints so that both sides hit the relay at the same time.
        connect_d = None
        answer_msg = None

        for _ in range(2):
            msg_bytes = yield with_timeout(
                w.get_message(), timeout, reactor,
                "Timed out waiting for receiver response"
            )
            msg = bytes_to_dict(msg_bytes)

            if "transit" in msg:
                ts.add_connection_hints(msg["transit"].get("hints-v1", []))
                # Start transit connection immediately after we have hints
                if connect_d is None:
                    connect_d = ts.connect()
            elif "answer" in msg:
                answer_msg = msg
            elif "error" in msg:
                raise SendError(f"Receiver rejected: {msg['error']}")

        if answer_msg is None:
            raise SendError("Never received file_ack from receiver")

        if "file_ack" not in answer_msg.get("answer", {}):
            raise SendError(f"Unexpected answer: {answer_msg}")

        # Step 6: Establish transit connection (with timeout).
        # connect_d may already be in-flight (started when hints arrived).
        if connect_d is None:
            connect_d = ts.connect()
        connection = yield with_timeout(
            connect_d, timeout, reactor,
            "Timed out establishing transit"
        )

        defer.returnValue(connection)
    except (SendError, WormholeTimeout):
        raise
    except Exception as e:
        raise SendError(f"Send failed: {e}") from e
