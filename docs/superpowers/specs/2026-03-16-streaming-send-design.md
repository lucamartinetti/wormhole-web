# Streaming Send — Design Spec

True streaming for the send path: upload body flows directly to wormhole transit without buffering to disk. Supports TB-scale transfers with constant memory usage.

## Problem

Twisted's `twisted.web` buffers the entire request body (to RAM for <100KB, to a temp file for larger) before calling `render_PUT`. This means:
- The full file must fit on disk before transfer begins
- TB-scale files require TB of temp disk space
- Upload and wormhole transfer are sequential, not concurrent

## Goal

Body data flows from the HTTP upload directly to the wormhole transit connection via a bounded in-memory queue. Memory usage is constant (~4MB buffer) regardless of file size. Upload and transfer happen concurrently, connected by backpressure.

Also: replace the `PUT /send` redirect with an inline handler that creates the wormhole, returns the code immediately, then streams data — no `-L` flag needed.

## Design

### Custom Request subclass

A `StreamingRequest` subclass of `twisted.web.server.Request` intercepts PUT requests to `/send` and `/send/<code>` before the body is buffered.

**Detecting the request path in `gotLength`:** `self.path` and `self.method` are NOT populated until `requestReceived` runs (after body). In `gotLength`, the path and method must be read from the channel's private attributes: `self.channel._path` and `self.channel._command`. These are set during header parsing before `gotLength` is called.

**Overridden methods:**

`gotLength(length)` — called after headers are parsed, before any body data. For streaming PUT requests:
- Reads path from `self.channel._path` and method from `self.channel._command`
- Determines if this is `PUT /send` (inline) or `PUT /send/<code>` (two-step)
- Sets `self.content = io.BytesIO()` as a harmless sentinel (prevents crashes if anything tries to access `self.content` later, e.g. `_cleanup`)
- Manually populates `self.method`, `self.uri`, `self.path` from channel attributes (since `requestReceived` will be skipped)
- Marks request as streaming (`self._streaming = True`)
- Initializes a `ChunkQueue` for body data
- Fires a background `@inlineCallbacks` Deferred chain that does the async work (wormhole creation, code allocation, PAKE, transit, queue consumption)
- Does NOT call `super().gotLength()` — prevents Twisted from setting up a content buffer
- For non-streaming requests, falls through to `super().gotLength(length)`

`handleContentChunk(data)` — called for each chunk of body data as it arrives from the transport. For streaming requests:
- Pushes the chunk to the `ChunkQueue`
- If queue is full, pauses the transport via `self.transport.pauseProducing()` (this is the TCP transport, NOT `self.channel.pauseProducing()` which is silently inert during body reception due to the `_handlingRequest` guard)
- Does NOT call `super().handleContentChunk()` — prevents buffering
- For non-streaming requests, falls through to `super()`

`requestReceived(command, path, version)` — called when the body is complete. For streaming requests:
- Signals EOF to the `ChunkQueue` via `queue.finish()`
- Does NOT call `super().requestReceived()` — the handler was already started from `gotLength`
- For non-streaming requests, falls through to `super()`

### ChunkQueue

Bounded async queue connecting `handleContentChunk` (producer) to wormhole transit (consumer).

```
ChunkQueue(max_chunks=16, transport=None)
  put(data: bytes) -> None
    Appends to internal deque. If deque length >= max_chunks,
    calls transport.pauseProducing() to apply backpressure.
  get() -> Deferred[bytes | None]
    Returns next chunk. If empty, returns a Deferred that fires
    when data is available. Returns None for EOF.
    After each get(), if deque dropped below max_chunks and transport
    was paused, calls transport.resumeProducing().
  finish() -> None
    Signals no more data. Pending get() fires with None.
  error(failure) -> None
    Signals an error. Pending get() errbacks with the failure.
```

Backpressure flow:
- Queue full → `put()` calls `transport.pauseProducing()` → TCP stops reading → curl blocks
- Consumer calls `get()` → queue drops below threshold → `get()` calls `transport.resumeProducing()` → TCP resumes → curl continues

The resume is triggered inline within `get()`, not by a separate `set_consumer_ready()` call. This keeps the logic in one place.

### Chunked transfer encoding (no Content-Length)

If the client uses `Transfer-Encoding: chunked` (no Content-Length), `gotLength` receives `length=None`. The wormhole file-transfer protocol requires a `filesize` in the offer message.

When `Content-Length` is absent, the server requires an `X-Wormhole-Filesize` header. If neither is present, the server responds with `411 Length Required` and a message explaining the requirement. This keeps the streaming path simple without special-casing unknown-length transfers.

### Inline PUT /send flow

```
1. curl sends: PUT /send + headers (Content-Length, X-Wormhole-Filename)
2. gotLength fires (synchronous):
   a. Read path from self.channel._path
   b. Set self._streaming = True, init ChunkQueue
   c. Set self.content = io.BytesIO() (sentinel)
   d. Set self.method, self.uri, self.path from channel attrs
   e. Fire background @inlineCallbacks chain (does NOT yield)
3. Background chain runs (async, concurrent with body):
   a. Create wormhole, allocate code
   b. Write "wormhole receive <code>\n" to response
   c. Write "waiting for receiver...\n"
   d. Start PAKE, call complete_send (blocks until receiver connects)
   e. Transit established → write "transferring...\n"
   f. Consume loop: get() from ChunkQueue → send_record to transit
   g. After EOF: wait for receiver ack → write "transfer complete\n" → finish
4. handleContentChunk fires repeatedly (concurrent with step 3):
   - Push chunks to ChunkQueue
   - Backpressure via transport.pauseProducing() if full
5. requestReceived fires (body complete):
   - queue.finish() → EOF sentinel
6. notifyFinish detects sender disconnect at any point:
   - Abort background chain, close wormhole, finish response
```

### Two-step PUT /send/<code> flow

```
1. curl sends: PUT /send/<code> + headers
2. gotLength fires (synchronous):
   a. Read path, extract code from URL
   b. Look up session by code — if not found, write 404 and return
   c. Set self._streaming = True, init ChunkQueue
   d. Fire background chain
3. Background chain runs:
   a. Write "<code>\nwaiting for receiver...\n"
   b. Call complete_send (uses session's stored key_exchange_d)
   c-g. Same as inline flow
4-6. Same as inline flow
```

### Sender module changes

Add a new function:

```
send_data_from_queue(connection, queue, request) -> Deferred
    Consume loop: calls queue.get() repeatedly, pipes each chunk
    through connection.send_record(). After EOF (get() returns None),
    waits for receiver ack record. Writes "transfer complete\n" to request.
    Each get() call may trigger transport.resumeProducing() internally
    (backpressure release is handled inside ChunkQueue.get()).
```

### Server module changes

- `make_site` passes `requestFactory=StreamingRequest`
- Remove `SendResource.render_PUT` (redirect handler) — replaced by inline streaming in `StreamingRequest`
- Remove `SendCodeResource` class — streaming PUT is handled in the Request subclass
- Keep `SendNewResource` (`POST /send/new`) — not a streaming endpoint
- Keep `SendResource` as a container for `SendNewResource` child

### Sender disconnect during PAKE wait

The background chain (step 3) may be waiting for a receiver to connect (PAKE). If the sender disconnects during this wait:
- `request.notifyFinish()` fires its errback
- The background chain catches this, closes the wormhole, and aborts
- The session is removed from the manager

This mirrors the pattern already used in `ReceiveCodeResource._do_receive` (server.py lines 103-108).

### What stays the same

- `POST /send/new` — unchanged, returns code
- `GET /receive/<code>` — unchanged, already streams
- `GET /health` — unchanged
- Session management — unchanged
- `start_key_exchange` / `complete_send` — unchanged
- All non-PUT-send requests use normal Twisted buffering

### Error handling

- `gotLength` is synchronous — async work is fired as a background Deferred chain. If the chain fails, the error is written to the response.
- If the session lookup fails in `gotLength` for `PUT /send/<code>` (expired code), write 404 and return — don't start streaming.
- If the sender disconnects mid-transfer, `notifyFinish` fires, ChunkQueue is aborted, transit connection closed.
- If the receiver disconnects mid-transfer, the transit errbacks, the handler writes an error to the response.
- If `Content-Length` and `X-Wormhole-Filesize` are both absent, respond 411.

## Testing

- Modify `tests/test_integration.py` `TestSendPath::test_send_file` to use inline `PUT /send` (no redirect)
- Add a streaming test: generate data incrementally (not all in memory), verify transfer works
- All existing E2E tests should still pass (two-step flow unchanged, inline flow improved)
- Add a test for the delayed-receiver scenario with the inline flow

## UX improvement

The inline `PUT /send` response now shows a copy-pasteable receive command:

```
$ curl -T huge-file.iso -H "X-Wormhole-Filename: huge-file.iso" http://host/send
wormhole receive 7-guitarist-revenge
waiting for receiver...
transferring...
transfer complete
```
