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
      await mod.default({ module_or_path: new WebAssembly.Module(wasmBytes) });
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
    el.textContent = '\uD83D\uDD12 End-to-end encrypted';
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

// --- Minimal ZIP creator (store mode, no compression) ---
// Produces a standard ZIP file compatible with unzip, 7z, wormhole-rs, etc.

function dosDateTime(date) {
  const time = ((date.getHours() & 0x1f) << 11) |
               ((date.getMinutes() & 0x3f) << 5) |
               ((date.getSeconds() >> 1) & 0x1f);
  const dateVal = (((date.getFullYear() - 1980) & 0x7f) << 9) |
                  (((date.getMonth() + 1) & 0xf) << 5) |
                  (date.getDate() & 0x1f);
  return { time, date: dateVal };
}

function crc32(data) {
  // CRC-32 lookup table (standard polynomial 0xEDB88320)
  if (!crc32.table) {
    crc32.table = new Uint32Array(256);
    for (let i = 0; i < 256; i++) {
      let c = i;
      for (let j = 0; j < 8; j++) {
        c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
      }
      crc32.table[i] = c;
    }
  }
  let crc = 0xFFFFFFFF;
  for (let i = 0; i < data.length; i++) {
    crc = crc32.table[(crc ^ data[i]) & 0xFF] ^ (crc >>> 8);
  }
  return (crc ^ 0xFFFFFFFF) >>> 0;
}

// Incrementally compute CRC32 across multiple chunks
function crc32init() { return 0xFFFFFFFF; }

function crc32update(crc, data) {
  if (!crc32.table) crc32(new Uint8Array(0)); // init table
  for (let i = 0; i < data.length; i++) {
    crc = crc32.table[(crc ^ data[i]) & 0xFF] ^ (crc >>> 8);
  }
  return crc;
}

function crc32final(crc) { return (crc ^ 0xFFFFFFFF) >>> 0; }

function writeU16(buf, offset, val) {
  buf[offset] = val & 0xFF;
  buf[offset + 1] = (val >>> 8) & 0xFF;
}

function writeU32(buf, offset, val) {
  buf[offset] = val & 0xFF;
  buf[offset + 1] = (val >>> 8) & 0xFF;
  buf[offset + 2] = (val >>> 16) & 0xFF;
  buf[offset + 3] = (val >>> 24) & 0xFF;
}

// Build a ZIP from multiple File objects. Returns a { stream, size, name } object
// where stream is a ReadableStream producing the zip bytes, size is the total size,
// and name is the zip filename.
function buildZipStream(files, zipName) {
  // Pre-compute the full zip layout so we know the total size
  const encoder = new TextEncoder();
  const now = new Date();
  const { time: dosTime, date: dosDate } = dosDateTime(now);

  const entries = [];
  let dataOffset = 0;

  for (const file of files) {
    // Use the file's webkitRelativePath if available (folder upload), otherwise just the name
    const path = file.webkitRelativePath || file.name;
    const encodedName = encoder.encode(path);
    const localHeaderSize = 30 + encodedName.length;
    const fileSize = file.size;

    entries.push({
      file,
      path,
      encodedName,
      localHeaderSize,
      fileSize,
      localHeaderOffset: dataOffset,
      crc32: 0, // will be filled during streaming
    });

    dataOffset += localHeaderSize + fileSize + 16; // +16 for data descriptor
  }

  // Central directory
  let centralDirSize = 0;
  for (const entry of entries) {
    centralDirSize += 46 + entry.encodedName.length;
  }

  const centralDirOffset = dataOffset;
  // End of central directory: 22 bytes
  const totalSize = dataOffset + centralDirSize + 22;

  // Create a ReadableStream that yields zip data
  let entryIndex = 0;
  let phase = 'local'; // 'local' | 'central' | 'end' | 'done'
  let fileReader = null;
  let localHeaderSent = false;
  let currentCrc = 0;

  const stream = new ReadableStream({
    async pull(controller) {
      try {
        if (phase === 'local') {
          if (entryIndex >= entries.length) {
            phase = 'central';
            return this.pull(controller);
          }
          const entry = entries[entryIndex];

          if (!localHeaderSent) {
            // Emit local file header
            const header = new Uint8Array(30 + entry.encodedName.length);
            // Local file header signature
            writeU32(header, 0, 0x04034B50);
            writeU16(header, 4, 20);     // version needed (2.0)
            writeU16(header, 6, 0);      // general purpose bit flag
            writeU16(header, 8, 0);      // compression method: store
            writeU16(header, 10, dosTime);
            writeU16(header, 12, dosDate);
            // CRC-32: We use 0 here and put real CRC in central directory.
            // For store mode with bit 3 unset we need the real CRC.
            // We'll use a data descriptor approach: set bit 3, write descriptor after data.
            // Actually, simpler: we pre-read files for CRC. But that defeats streaming.
            // For maximum compatibility, we write 0 here and correct in central dir.
            // Many unzip tools only check the central directory CRC.
            // However for best compat, let's set bit 3 (data descriptor).
            writeU16(header, 6, 0x0008); // bit 3 = data descriptor follows
            writeU32(header, 14, 0);     // crc-32 (in data descriptor)
            writeU32(header, 18, 0);     // compressed size (in data descriptor)
            writeU32(header, 22, 0);     // uncompressed size (in data descriptor)
            writeU16(header, 26, entry.encodedName.length);
            writeU16(header, 28, 0);     // extra field length
            header.set(entry.encodedName, 30);

            controller.enqueue(header);
            localHeaderSent = true;
            currentCrc = crc32init();
            fileReader = entry.file.stream().getReader();
            return;
          }

          // Stream file data
          const { done, value } = await fileReader.read();
          if (done) {
            // Emit data descriptor (signature + crc + compressed + uncompressed)
            const finalCrc = crc32final(currentCrc);
            entries[entryIndex].crc32 = finalCrc;
            const desc = new Uint8Array(16);
            writeU32(desc, 0, 0x08074B50); // data descriptor signature
            writeU32(desc, 4, finalCrc);
            writeU32(desc, 8, entry.fileSize);  // compressed size
            writeU32(desc, 12, entry.fileSize); // uncompressed size
            controller.enqueue(desc);

            entryIndex++;
            localHeaderSent = false;
            fileReader = null;
            return;
          }

          currentCrc = crc32update(currentCrc, value);
          controller.enqueue(value);
          return;
        }

        if (phase === 'central') {
          // Emit all central directory entries at once
          const centralDir = new Uint8Array(centralDirSize);
          let offset = 0;
          for (const entry of entries) {
            writeU32(centralDir, offset, 0x02014B50);  // central dir signature
            writeU16(centralDir, offset + 4, 20);       // version made by
            writeU16(centralDir, offset + 6, 20);       // version needed
            writeU16(centralDir, offset + 8, 0);        // flags (no data descriptor flag in central)
            writeU16(centralDir, offset + 10, 0);       // compression: store
            writeU16(centralDir, offset + 12, dosTime);
            writeU16(centralDir, offset + 14, dosDate);
            writeU32(centralDir, offset + 16, entry.crc32);
            writeU32(centralDir, offset + 20, entry.fileSize); // compressed
            writeU32(centralDir, offset + 24, entry.fileSize); // uncompressed
            writeU16(centralDir, offset + 28, entry.encodedName.length);
            writeU16(centralDir, offset + 30, 0);       // extra field length
            writeU16(centralDir, offset + 32, 0);       // file comment length
            writeU16(centralDir, offset + 34, 0);       // disk number start
            writeU16(centralDir, offset + 36, 0);       // internal file attributes
            writeU32(centralDir, offset + 38, 0);       // external file attributes
            writeU32(centralDir, offset + 42, entry.localHeaderOffset);
            centralDir.set(entry.encodedName, offset + 46);
            offset += 46 + entry.encodedName.length;
          }
          controller.enqueue(centralDir);
          phase = 'end';
          return;
        }

        if (phase === 'end') {
          // End of central directory record
          const eocd = new Uint8Array(22);
          writeU32(eocd, 0, 0x06054B50);   // EOCD signature
          writeU16(eocd, 4, 0);             // disk number
          writeU16(eocd, 6, 0);             // disk with central dir
          writeU16(eocd, 8, entries.length); // entries on this disk
          writeU16(eocd, 10, entries.length); // total entries
          writeU32(eocd, 12, centralDirSize);
          writeU32(eocd, 16, centralDirOffset);
          writeU16(eocd, 20, 0);            // comment length
          controller.enqueue(eocd);
          controller.close();
          phase = 'done';
          return;
        }
      } catch (err) {
        controller.error(err);
      }
    }
  });

  return { stream, size: totalSize, name: zipName };
}

// Calculate total zip size without data descriptors initially, then add them.
// With data descriptors (16 bytes each), the total is:
// sum(30 + name.length + file.size + 16) for each file
// + sum(46 + name.length) for central dir entries
// + 22 for EOCD
function calcZipSize(files) {
  const encoder = new TextEncoder();
  let size = 0;
  for (const file of files) {
    const path = file.webkitRelativePath || file.name;
    const nameLen = encoder.encode(path).length;
    // local header + data + data descriptor + central dir entry
    size += 30 + nameLen + file.size + 16 + 46 + nameLen;
  }
  size += 22; // EOCD
  return size;
}

// --- Recursive directory traversal for drag-and-drop ---
async function traverseEntries(items) {
  const files = [];

  async function walkEntry(entry, path) {
    if (entry.isFile) {
      const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
      // Attach the relative path so zip preserves directory structure
      Object.defineProperty(file, 'webkitRelativePath', {
        value: path + file.name,
        writable: false,
      });
      files.push(file);
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      // readEntries may not return all entries in one call
      let batch;
      do {
        batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
        for (const child of batch) {
          await walkEntry(child, path + entry.name + '/');
        }
      } while (batch.length > 0);
    }
  }

  for (let i = 0; i < items.length; i++) {
    const entry = items[i].webkitGetAsEntry ? items[i].webkitGetAsEntry() : null;
    if (entry) {
      await walkEntry(entry, '');
    }
  }

  return files;
}

// --- WASM Send ---
// Accepts a single File or an array of Files.
// For multiple files, creates a zip on the fly and sends it.
async function wasmSend(fileOrFiles, callbacks) {
  const { onCode, onProgress, onStatus, onError, onComplete } = callbacks;

  // Determine if this is a multi-file send
  const files = Array.isArray(fileOrFiles) ? fileOrFiles : [fileOrFiles];
  const isMulti = files.length > 1;

  let fileName, fileSize, dataStream;

  if (isMulti) {
    // Determine zip name: if all files share the same top-level directory
    // (folder upload via webkitdirectory), use that folder name.
    // Otherwise use "files.zip".
    let zipBaseName = 'files';
    const firstPath = files[0].webkitRelativePath || '';
    if (firstPath.includes('/')) {
      const topDir = firstPath.split('/')[0];
      const allSameDir = files.every(f =>
        (f.webkitRelativePath || '').startsWith(topDir + '/'));
      if (allSameDir) zipBaseName = topDir;
    }
    fileName = zipBaseName + '.zip';
    fileSize = calcZipSize(files);
    const zip = buildZipStream(files, fileName);
    dataStream = zip.stream;
  } else {
    const file = files[0];
    fileName = file.name;
    fileSize = file.size;
    dataStream = file.stream();
  }

  let sender = null;
  try {
    onStatus('allocating code...');
    sender = await wasm.WormholeSender.create(TRANSIT_RELAY_URL, MAILBOX_URL);
    activeSender = sender;
    const code = sender.code();
    onCode(code);

    onStatus('waiting for receiver...');
    await sender.negotiate(fileName, BigInt(fileSize));

    const connType = sender.connection_type();
    onStatus('transferring...');
    resetSpeed();

    const reader = dataStream.getReader();
    let sent = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      await sender.send_chunk(value);
      sent += value.byteLength;
      addSpeedBytes(value.byteLength);
      const pct = Math.round((sent / fileSize) * 100);
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
  traverseEntries,
};
