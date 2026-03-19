const CACHE_NAME = 'wormhole-v1';
const SHELL_ASSETS = [
  '/',
  '/static/index.html',
  '/static/style.css',
  '/static/wasm-client.js',
  '/static/qr.js',
  '/static/wasm/wormhole_wasm.js',
  '/static/wasm/wormhole_wasm_bg.wasm',
  '/static/manifest.json',
  '/static/favicon.svg',
];

const OFFLINE_HTML = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>wormhole.page — Offline</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; background: #fafafa; color: #333; text-align: center; }
  @media (prefers-color-scheme: dark) { body { background: #1a1b1e; color: #e0e0e0; } }
  .offline { max-width: 400px; padding: 2rem; }
  h1 { font-size: 1.5rem; margin-bottom: 0.5rem; }
  p { color: #666; line-height: 1.5; }
  @media (prefers-color-scheme: dark) { p { color: #aaa; } }
</style>
</head>
<body>
<div class="offline">
  <h1>You're offline</h1>
  <p>Connect to the internet to transfer files.</p>
</div>
</body>
</html>`;

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Never intercept WebSocket or transit/health endpoints
  if (
    url.pathname === '/transit' ||
    url.pathname === '/health' ||
    event.request.headers.get('upgrade') === 'websocket'
  ) {
    return;
  }

  // Cache-first for static assets and root
  if (url.pathname.startsWith('/static/') || url.pathname === '/') {
    event.respondWith(
      caches.match(event.request).then((cached) =>
        cached || fetch(event.request).then((response) => {
          // Cache successful responses for future use
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          }
          return response;
        }).catch(() => {
          // Offline fallback for root/HTML
          if (url.pathname === '/') {
            return new Response(OFFLINE_HTML, {
              headers: { 'Content-Type': 'text/html' },
            });
          }
          return caches.match('/');
        })
      )
    );
    return;
  }

  // Network-first for receive URLs (need fresh HTML) and anything else
  event.respondWith(
    fetch(event.request).catch(() => {
      // For receive pages, try serving cached root (SPA)
      if (url.pathname.startsWith('/receive/')) {
        return caches.match('/').then((cached) =>
          cached || new Response(OFFLINE_HTML, {
            headers: { 'Content-Type': 'text/html' },
          })
        );
      }
      return new Response(OFFLINE_HTML, {
        headers: { 'Content-Type': 'text/html' },
      });
    })
  );
});
