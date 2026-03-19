// WASM Wormhole Client — E2E encrypted file transfer in the browser
// Falls back to server-proxied transfer if WASM fails to load.

let wasm = null;
let wasmReady = false;
let wasmLoadError = null;

// Load WASM module in background
async function initWasm() {
  try {
    const mod = await import('./wasm/wormhole_wasm.js');
    await mod.default();
    wasm = mod;
    wasmReady = true;
  } catch (err) {
    wasmLoadError = err;
    console.warn('[wormhole] WASM load failed, using server proxy:', err);
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
    el.innerHTML = 'Not end-to-end encrypted \u2014 the server sees file contents.<br>For E2E encryption, use the <a href="https://magic-wormhole.readthedocs.io/" target="_blank">wormhole CLI</a> directly.';
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

// Transit relay URL — our server embeds a WebSocket transit relay
const TRANSIT_RELAY_URL = (location.protocol === 'https:' ? 'wss://' : 'ws://') +
  location.hostname + ':4002';

// --- WASM Send ---
async function wasmSend(file, callbacks) {
  const { onCode, onVerifier, onProgress, onStatus, onError, onComplete } = callbacks;

  let sender = null;
  try {
    onStatus('allocating code...');
    sender = await wasm.WormholeSender.create(TRANSIT_RELAY_URL);
    const code = sender.code();
    onCode(code);

    onStatus('waiting for receiver...');
    await sender.negotiate(file.name, BigInt(file.size));

    const verifier = sender.verifier();
    if (verifier) onVerifier(verifier);

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
    if (sender) {
      try { sender.close(); } catch (_) {}
    }
  }
}

// --- WASM Receive ---
async function wasmReceive(code, callbacks) {
  const { onFileInfo, onVerifier, onProgress, onStatus, onError, onComplete } = callbacks;

  let receiver = null;
  try {
    onStatus('establishing encrypted connection...');
    receiver = await wasm.WormholeReceiver.create(code, TRANSIT_RELAY_URL);

    onStatus('waiting for file offer...');
    const offer = await receiver.negotiate();

    const verifier = receiver.verifier();
    if (verifier) onVerifier(verifier);

    const filename = sanitizeFilename(offer.filename);
    const filesize = Number(offer.filesize);
    onFileInfo(filename, filesize);

    await receiver.accept();

    const connType = receiver.connection_type();
    onStatus('transferring...');
    resetSpeed();

    // Try File System Access API for large files
    let writer = null;
    let chunks = null;
    const useFileSystemAccess = filesize > 100 * 1024 * 1024 && 'showSaveFilePicker' in window;

    if (useFileSystemAccess) {
      try {
        const handle = await window.showSaveFilePicker({ suggestedName: filename });
        writer = await handle.createWritable();
      } catch (e) {
        if (e.name === 'AbortError') {
          onError('Save cancelled');
          return;
        }
        // Fall back to Blob
        writer = null;
      }
    }

    if (!writer) {
      chunks = [];
    }

    let received = 0;
    console.log('[wormhole] JS: starting receive loop');
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
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }

    onComplete();
  } catch (err) {
    onError(err.message || String(err));
  } finally {
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
};
