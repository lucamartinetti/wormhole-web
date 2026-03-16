# Send Persistence — Design Spec

Make uploaded files available until a receiver connects, rather than expiring immediately after upload completes.

## Problem

Currently, `POST /send/new` creates a wormhole session and `PUT /send/<code>` does everything: starts key exchange, sends the file offer, waits for a receiver, and pipes data through transit — all inside one HTTP handler. If the receiver isn't connected when the upload finishes, the transfer completes into the void and the file is lost.

For small files, this happens in milliseconds: the upload finishes, the wormhole transit completes (or fails), and the code becomes invalid before anyone can use it.

## Goal

After uploading via `PUT /send/<code>`, the sender's curl stays open with "waiting for receiver..." until someone runs `wormhole receive <code>`. The file is then transferred and the response returns "transfer complete."

This design must also work for future true streaming (no disk buffering) where the HTTP upload body is piped directly to the wormhole transit connection via backpressure.

## Design

### How it works

PAKE key exchange requires both sender and receiver to be connected to the mailbox server — it blocks until the receiver shows up. The key insight is **starting this wait early** (at session creation time) rather than inside the upload handler. This gives the receiver the entire window from code allocation through upload completion to connect.

File persistence comes from two things working together:
1. **Twisted's temp-file buffering** — for uploads >100KB, Twisted writes the body to a temp file on disk. The file survives after `render_PUT` returns.
2. **The PUT handler blocks on the stored PAKE Deferred** — the sender's curl stays open, showing "waiting for receiver...", until the PAKE Deferred resolves (meaning the receiver connected). Then data flows from the temp file through the wormhole transit.

### Two-phase send flow

**Phase 1 — Session creation (`POST /send/new` or `PUT /send` redirect):**
- Create wormhole, allocate code
- Start PAKE key exchange (`get_unverified_key` + `get_verifier`), store the resulting Deferred in the session
- Attach a no-op errback to the Deferred to suppress Twisted's "unhandled error" warnings if the Deferred errbacks before Phase 2 consumes it
- Return the code to the caller immediately

**Phase 2 — Upload (`PUT /send/<code>`):**
- Read filename and filesize from request headers
- Yield the stored key exchange Deferred (may already be resolved if receiver connected early, or blocks until receiver connects)
- Create `TransitSender`, call `derive_key` (only valid after PAKE completes), send transit hints and file offer
- Wait for receiver's transit hints and file_ack
- Establish transit connection
- Stream data from request body (currently from Twisted's disk buffer, future: directly from upload stream)
- Wait for receiver's ack record
- Return "transfer complete"

### Sender module changes

Split `prepare_send()` into two functions:

```
start_key_exchange(wormhole, reactor) -> Deferred
    Starts PAKE key exchange (get_unverified_key + get_verifier).
    Returns a Deferred that fires when key exchange completes.
    Does NOT apply a timeout — the caller attaches a no-op errback
    and stores the Deferred. Timeout is applied in Phase 2.
    Does NOT create TransitSender, derive keys, send hints, or
    send the file offer — all of that requires PAKE to be done
    and stays in complete_send.

complete_send(wormhole, key_exchange_d, filename, filesize, reactor, timeout) -> Connection
    Wraps key_exchange_d with a timeout, then yields it.
    After PAKE completes: creates TransitSender, calls derive_key,
    sends transit hints and file offer, reads receiver's hints and
    file_ack, establishes and returns the transit connection.
```

### Session changes

Add a `key_exchange_d` field to `Session` to hold the PAKE Deferred from Phase 1.

### Server changes

`SendNewResource._do_create` and `SendResource._do_redirect`:
- After creating the wormhole, call `start_key_exchange()` and store the Deferred in the session

`SendCodeResource._do_upload`:
- Retrieve `session.key_exchange_d`
- Call `complete_send(session.wormhole, session.key_exchange_d, filename, filesize, ...)`
- The rest (data streaming, ack, cleanup) stays the same

### Session TTL interaction

When `SessionManager._expire` fires (session TTL, no upload arrived):
- `session.wormhole.close()` is called — this causes the in-flight PAKE Deferred to errback
- The no-op errback attached in Phase 1 suppresses the unhandled error warning
- No additional cleanup is needed; the wormhole close transitively cancels the PAKE

### Timeout semantics

- **Phase 1:** No timeout on the PAKE Deferred. The session TTL (default 60s) is the backstop — if no upload arrives, the session expires and the wormhole is closed.
- **Phase 2:** `complete_send` wraps the key exchange Deferred with `with_timeout`. The timeout (default `--transfer-timeout`, 120s) starts from when Phase 2 begins. If the receiver hasn't connected by then, the sender's curl gets an error.

This means the total window for a receiver to connect is: session TTL (until upload starts) + transfer timeout (after upload starts).

### Timing behavior

| Scenario | Behavior |
|----------|----------|
| Receiver connects before upload | PAKE completes during Phase 1. Phase 2 immediately sends offer and streams data. Fast. |
| Receiver connects during upload | PAKE completes while Twisted buffers the body. Phase 2 proceeds as soon as render_PUT is called. |
| Receiver connects after upload | Phase 2 writes "waiting for receiver...", blocks until PAKE completes. Sender's curl hangs. When receiver connects, data flows from disk buffer. |
| Receiver never connects | Phase 2 blocks until `--transfer-timeout` expires, then returns an error to the sender's curl. |
| Sender never uploads (no PUT) | Session TTL (60s) expires, wormhole is closed and cleaned up. PAKE Deferred errbacks silently. |

### What doesn't change

- **API surface** — same endpoints, same headers, same curl commands
- **Receiver side** — `GET /receive/<code>` is unchanged
- **Session manager** — create/get/remove/is_full/TTL logic unchanged (only Session gains a field)
- **Streaming architecture for receive path** — unchanged
- **Error handling** — same HTTP status codes and error messages

### Streaming compatibility

This design is forward-compatible with true HTTP upload streaming:

- **v1 (current):** Twisted buffers the body before `render_PUT` runs. Phase 2 reads from the temp file after receiver connects. The sender's curl finishes uploading before seeing "waiting for receiver..."
- **Future streaming:** `render_PUT` would process the body incrementally. Phase 2 applies backpressure on the upload until the receiver connects, then data flows directly. The sender's curl blocks on the upload itself (not just the response).

In both cases, the wormhole protocol exchange (PAKE, offer, ack) happens the same way. Only the data source changes (temp file vs live stream).

## Testing

Modify the existing integration test `TestSendPath::test_send_file` to verify:
- Upload completes, receiver connects 2 seconds later, transfer still works
- The existing test (receiver connects concurrently) should still pass

No new test files needed — this is a behavioral change to existing functionality.
