// WASM Wormhole Client — E2E encrypted file transfer in the browser

let wasm = null;
let wasmReady = false;
let wasmLoadError = null;
let activeSender = null;
let activeReceiver = null;

// Verify a fetch response against a known SRI hash using Web Crypto API.
// Returns the ArrayBuffer if verification passes, throws on mismatch.
async function verifyIntegrity(buffer, expectedSri) {
  if (!expectedSri || !expectedSri.startsWith('sha384-')) {
    console.warn('[wormhole] No valid SRI hash found, skipping integrity check');
    return buffer;
  }
  const expectedB64 = expectedSri.slice('sha384-'.length);
  const hashBuf = await crypto.subtle.digest('SHA-384', buffer);
  const hashArr = new Uint8Array(hashBuf);
  // Convert to base64
  let binary = '';
  for (let i = 0; i < hashArr.length; i++) {
    binary += String.fromCharCode(hashArr[i]);
  }
  const actualB64 = btoa(binary);
  if (actualB64 !== expectedB64) {
    throw new Error(
      'WASM integrity check failed: expected sha384-' + expectedB64 +
      ' but got sha384-' + actualB64
    );
  }
  return buffer;
}

// Load WASM module in background
async function initWasm() {
  try {
    // Verify .wasm binary integrity before instantiation
    const wasmIntegrityMeta = document.querySelector('meta[name="wasm-integrity"]');
    const expectedHash = wasmIntegrityMeta ? wasmIntegrityMeta.content : null;

    if (expectedHash && expectedHash.startsWith('sha384-')) {
      // Fetch and verify the .wasm binary, then pass it to the init function
      const wasmResp = await fetch('/static/wasm/wormhole_wasm_bg.wasm');
      const wasmBytes = await wasmResp.arrayBuffer();
      await verifyIntegrity(wasmBytes, expectedHash);

      // The JS glue is integrity-checked via <link rel="modulepreload">
      const mod = await import('/static/wasm/wormhole_wasm.js');
      await mod.default(new WebAssembly.Module(wasmBytes));
      wasm = mod;
    } else {
      // No SRI hash injected (dev mode) — load without verification
      const mod = await import('/static/wasm/wormhole_wasm.js');
      await mod.default();
      wasm = mod;
    }
    wasmReady = true;
  } catch (err) {
    wasmLoadError = err;
    console.warn('[wormhole] WASM load failed:', err);
  }
  updateEncryptionBadge();
}

function updateEncryptionBadge() {
  const el = document.getElementById('encryption-status');
  if (!el) return;
  if (wasmReady) {
    el.textContent = 'End-to-end encrypted';
    el.classList.remove('warning');
    el.classList.add('encrypted');
  } else {
    el.textContent = 'Your browser does not support WebAssembly. Use a modern browser or the wormhole CLI.';
    el.classList.remove('encrypted');
    el.classList.add('warning');
  }
}

// --- Speed calculation ---
let speedBytes = 0;
let speedStart = 0;

function resetSpeed() {
  speedBytes = 0;
  speedStart = performance.now();
}

function addSpeedBytes(n) {
  speedBytes += n;
}

function getSpeed() {
  const elapsed = (performance.now() - speedStart) / 1000;
  if (elapsed < 0.5) return null;
  const bytesPerSec = speedBytes / elapsed;
  if (bytesPerSec < 1024 * 1024) {
    return (bytesPerSec / 1024).toFixed(1) + ' KB/s';
  }
  return (bytesPerSec / (1024 * 1024)).toFixed(1) + ' MB/s';
}

// --- Filename sanitization ---
function sanitizeFilename(name) {
  // Strip path separators, null bytes, and limit length
  return name
    .replace(/[\/\\]/g, '_')
    .replace(/\0/g, '')
    .replace(/\.\./g, '_')
    .slice(0, 255)
    || 'download';
}

// Transit relay URL — same server, /transit path (WS-to-TCP bridge)
const TRANSIT_RELAY_URL = window.WORMHOLE_TRANSIT_RELAY ||
  ((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/transit');

// Mailbox relay URL — defaults to the public Magic Wormhole relay
const MAILBOX_URL = window.WORMHOLE_MAILBOX_URL || 'wss://relay.magic-wormhole.io:443/v1';

// --- WASM Send ---
async function wasmSend(file, callbacks) {
  const { onCode, onProgress, onStatus, onError, onComplete } = callbacks;

  let sender = null;
  try {
    onStatus('allocating code...');
    sender = await wasm.WormholeSender.create(TRANSIT_RELAY_URL, MAILBOX_URL);
    activeSender = sender;
    const code = sender.code();
    onCode(code);

    onStatus('waiting for receiver...');
    await sender.negotiate(file.name, BigInt(file.size));

    const connType = sender.connection_type();
    onStatus('transferring...');
    resetSpeed();

    const reader = file.stream().getReader();
    let sent = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      await sender.send_chunk(value);
      sent += value.byteLength;
      addSpeedBytes(value.byteLength);
      const pct = Math.round((sent / file.size) * 100);
      const speed = getSpeed();
      onProgress(pct, speed, connType);
    }

    await sender.finish();
    onComplete();
  } catch (err) {
    onError(err.message || String(err));
  } finally {
    activeSender = null;
    if (sender) {
      try { sender.close(); } catch (_) {}
    }
  }
}

// --- WASM Receive ---
async function wasmReceive(code, callbacks) {
  const { onFileInfo, onProgress, onStatus, onError, onComplete } = callbacks;

  let receiver = null;
  try {
    onStatus('establishing encrypted connection...');
    receiver = await wasm.WormholeReceiver.create(code, TRANSIT_RELAY_URL, MAILBOX_URL);
    activeReceiver = receiver;

    onStatus('waiting for file offer...');
    const offer = await receiver.negotiate();

    const filename = sanitizeFilename(offer.filename);
    const filesize = Number(offer.filesize);
    onFileInfo(filename, filesize);

    await receiver.accept();

    const connType = receiver.connection_type();
    onStatus('transferring...');
    resetSpeed();

    // Try File System Access API to stream directly to disk (Chromium only).
    // Falls back to in-memory Blob for Firefox/Safari.
    let writer = null;
    let chunks = null;
    const MAX_BLOB_SIZE = 512 * 1024 * 1024; // 512MB in-memory limit

    if ('showSaveFilePicker' in window) {
      try {
        const handle = await window.showSaveFilePicker({ suggestedName: filename });
        writer = await handle.createWritable();
      } catch (e) {
        if (e.name === 'AbortError') {
          onError('Save cancelled');
          return;
        }
        writer = null;
      }
    }

    if (!writer) {
      if (filesize > MAX_BLOB_SIZE) {
        onError('File too large for this browser (' + formatSize(filesize) +
          '). Use Chrome for files over 512 MB, or use the wormhole CLI.');
        try { receiver.close(); } catch (_) {}
        return;
      }
      chunks = [];
    }

    let received = 0;
    while (true) {
      const chunk = await receiver.receive_chunk();
      if (chunk.length === 0) break;

      if (writer) {
        try {
          await writer.write(chunk);
        } catch (e) {
          if (e.name === 'QuotaExceededError') {
            onError('Not enough disk space');
            await writer.abort();
            return;
          }
          throw e;
        }
      } else {
        // Copy chunk — the WASM Uint8Array may reference freed memory after next call
        chunks.push(new Uint8Array(chunk).slice());
      }

      received += chunk.length;
      addSpeedBytes(chunk.length);
      const pct = filesize > 0 ? Math.round((received / filesize) * 100) : 0;
      const speed = getSpeed();
      onProgress(pct, speed, connType);
    }

    if (writer) {
      await writer.close();
    } else {
      // Trigger browser download from Blob
      const blob = new Blob(chunks);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      // Delay cleanup — browsers may not start the download synchronously,
      // so removing the element or revoking the URL too early drops small files.
      setTimeout(() => {
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      }, 60000);
    }

    onComplete();
  } catch (err) {
    onError(err.message || String(err));
  } finally {
    activeReceiver = null;
    if (receiver) {
      try { receiver.close(); } catch (_) {}
    }
  }
}

// Export for use from index.html
window.wasmClient = {
  get ready() { return wasmReady; },
  send: wasmSend,
  receive: wasmReceive,
  initWasm,
  cancelSend() {
    if (activeSender) {
      try { activeSender.close(); } catch (_) {}
      activeSender = null;
    }
  },
  cancelReceive() {
    if (activeReceiver) {
      try { activeReceiver.close(); } catch (_) {}
      activeReceiver = null;
    }
  },
};
