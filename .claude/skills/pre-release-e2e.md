---
name: pre-release-e2e
description: |
  Comprehensive end-to-end test suite for wormhole.page.
  Run before every deploy and after every production release.
  Tests actual file transfers, security headers, error states,
  UI flows, and cross-tab communication via headless browser.
allowed-tools:
  - Bash
  - Read
  - Write
  - Agent
---

# /pre-release-e2e — Wormhole.page Release Verification

Run this skill before every deploy (`fly deploy`) and after every production release.
It tests everything the Playwright unit tests don't: real file transfers, security
headers, error handling, and production-specific behavior.

## Arguments

- No arguments: test against production (https://wormhole.page)
- `local`: test against local dev server (http://localhost:8080)
- `<url>`: test against a custom URL

## Setup

```bash
B=~/.claude/skills/gstack/browse/dist/browse
if [ ! -x "$B" ]; then
  echo "ERROR: gstack browse not available. Run /browse first to set up."
  exit 1
fi

# Determine target URL
TARGET="${1:-https://wormhole.page}"
if [ "$TARGET" = "local" ]; then
  TARGET="http://localhost:8080"
fi
echo "TARGET: $TARGET"
```

Create a test results tracker. Report results as you go using this format:

```
## Test Results

| # | Category | Test | Result | Notes |
|---|----------|------|--------|-------|
| 1 | Load     | ... | PASS/FAIL | ... |
```

Update the table after each test group. At the end, print a summary with pass/fail
count and a SHIP / NO-SHIP verdict.

---

## Test Groups

Run ALL groups in order. Do not skip any group. If a test fails, note it and continue
— do not abort the run.

### Group 1: Health & Infrastructure

**1.1 Health endpoint**
```bash
$B goto $TARGET/health
$B text
```
Verify: response body is exactly `ok`.

**1.2 404 handling**
```bash
$B goto $TARGET/nonexistent-page-that-should-404
```
Verify: HTTP status is 404 (check via `$B js "document.title"` or response).

**1.3 SPA fallback for /receive routes**
```bash
$B goto $TARGET/receive/test-code-12345
$B text
```
Verify: page loads (not 404), contains "wormhole.page" heading.

---

### Group 2: Security Headers

**2.1 Content-Security-Policy**
```bash
$B goto $TARGET
$B js "fetch(window.location.href).then(r => r.headers.get('content-security-policy'))"
```
Verify ALL of these directives are present:
- `default-src 'none'`
- `script-src 'self'` (must NOT contain `unsafe-inline`)
- `style-src 'self' 'unsafe-inline'`
- `connect-src 'self' wss://relay.magic-wormhole.io:443` (must NOT contain bare `ws:` or `wss:`)
- `frame-ancestors 'none'`
- `form-action 'self'`

**2.2 Other security headers**
```bash
$B js "fetch(window.location.href).then(r => JSON.stringify({xfo: r.headers.get('x-frame-options'), xcto: r.headers.get('x-content-type-options'), hsts: r.headers.get('strict-transport-security'), ref: r.headers.get('referrer-policy'), perm: r.headers.get('permissions-policy')}))"
```
Verify:
- `x-frame-options` = `DENY`
- `x-content-type-options` = `nosniff`
- `strict-transport-security` contains `max-age=31536000`
- `referrer-policy` = `no-referrer`
- `permissions-policy` contains `camera=(), microphone=(), geolocation=()`

---

### Group 3: Page Load & WASM

**3.1 Page structure**
```bash
$B goto $TARGET
$B snapshot -i
```
Verify these elements exist:
- Theme toggle button (`#theme-toggle`)
- Send tab (selected by default)
- Receive tab
- Dropzone
- "How it works" section

**3.2 WASM initialization**
```bash
$B js "window.wasmClient?.ready"
```
If not `true`, wait up to 8 seconds:
```bash
$B wait --load
sleep 3
$B js "window.wasmClient?.ready"
```
Verify: returns `true`.

**3.3 Encryption badge**
```bash
$B js "document.getElementById('encryption-status').className"
$B js "document.getElementById('encryption-status').textContent"
```
Verify:
- Class contains `encrypted` (not `warning`)
- Text contains `End-to-end encrypted`

**3.4 Console errors**
```bash
$B console --errors
```
Verify: no errors. Warnings about SRI in dev mode are acceptable.

---

### Group 4: UI Interactions

**4.1 Tab switching**
```bash
$B snapshot -i
$B click "#main-tab-receive"
$B is visible "#panel-receive"
$B is visible "#receive-code"
$B click "#main-tab-send"
$B is visible "#panel-send"
$B is visible "#dropzone"
```
Verify: both tabs switch correctly, correct panels show/hide.

**4.2 Dark mode toggle**
```bash
$B js "document.documentElement.getAttribute('data-theme')"
$B click "#theme-toggle"
$B js "document.documentElement.getAttribute('data-theme')"
```
Verify: `data-theme` attribute changes (e.g., null → 'dark' or 'light' → 'dark').

**4.3 Dark mode persistence**
```bash
$B js "localStorage.getItem('theme')"
```
Verify: localStorage has the theme value matching what was set.

**4.4 "How it works" expandable section**
```bash
$B click "details.cli-section summary"
$B js "document.querySelector('details.cli-section').open"
```
Verify: returns `true` (section expanded).

---

### Group 5: Send Flow (Real Transfer)

This is the most critical test — an actual end-to-end file transfer.

**5.1 Create test file**
```bash
echo "wormhole e2e test $(date -u +%Y-%m-%dT%H:%M:%SZ) content:$(head -c 32 /dev/urandom | base64)" > /tmp/wormhole-e2e-test.txt
wc -c /tmp/wormhole-e2e-test.txt
```

**5.2 Upload file and verify send UI**
```bash
$B goto $TARGET
$B wait --load
sleep 2
$B upload "#file-input" /tmp/wormhole-e2e-test.txt
sleep 2
$B snapshot -i
```
Verify:
- `#send-status` is visible
- `#send-initial` is hidden
- `#send-filename` shows "wormhole-e2e-test.txt"
- `#send-filesize` shows the file size

**5.3 Wait for code allocation**
```bash
sleep 3
$B js "document.getElementById('send-code')?.textContent || 'no-code'"
```
Verify: returns a URL like `https://wormhole.page/receive/<code>` (not 'no-code').
Save the code for receive test.

**5.4 Verify QR code generated**
```bash
$B js "document.getElementById('qr-display')?.innerHTML?.length > 100"
```
Verify: returns `true` (SVG QR code is rendered).

**5.5 Verify status text**
```bash
$B js "document.getElementById('send-status-text')?.textContent"
```
Verify: shows "waiting for receiver..." (not an error).

**5.6 Verify code section visible**
```bash
$B is visible "#send-code-section"
```

---

### Group 6: Receive Flow (Complete Transfer)

**6.1 Open receive URL in new tab**
Use the code from Group 5:
```bash
$B newtab $RECEIVE_URL
```

**6.2 Verify receive auto-starts**
```bash
sleep 3
$B snapshot -i
$B js "document.getElementById('receive-status')?.classList.contains('hidden')"
```
Verify: receive-status is NOT hidden (flow started automatically from URL).

**6.3 Wait for transfer to complete**
Poll for completion (max 30 seconds):
```bash
for i in $(seq 1 15); do
  STATUS=$($B js "document.getElementById('receive-status-text')?.textContent" 2>/dev/null)
  echo "[$i] $STATUS"
  if echo "$STATUS" | grep -q "Transfer complete"; then
    echo "TRANSFER COMPLETE"
    break
  fi
  if echo "$STATUS" | grep -qi "error"; then
    echo "TRANSFER ERROR: $STATUS"
    break
  fi
  sleep 2
done
```
Verify: status reaches "Transfer complete!" within 30 seconds.

**6.4 Verify receive UI completion state**
```bash
$B js "document.getElementById('receive-progress')?.style.width"
$B js "document.getElementById('receive-status-text')?.textContent"
$B js "document.getElementById('receive-filename')?.textContent"
```
Verify:
- Progress bar at "100%"
- Status shows "Transfer complete!"
- Filename shows "wormhole-e2e-test.txt"

**6.5 Verify sender also completed**
Switch back to the send tab:
```bash
$B tab 1
$B js "document.getElementById('send-status-text')?.textContent"
$B js "document.getElementById('send-progress')?.style.width"
```
Verify:
- Status shows "Transfer complete!"
- Progress bar at "100%"

**6.6 Verify no console errors after transfer**
```bash
$B console --errors
```
Verify: no new errors.

---

### Group 7: Cancel Flows

**7.1 Send cancel**
```bash
$B closetab 2
$B goto $TARGET
$B wait --load
sleep 2
$B upload "#file-input" /tmp/wormhole-e2e-test.txt
sleep 3
$B click "#send-cancel-btn"
$B is visible "#send-initial"
$B is visible "#dropzone"
```
Verify: UI returns to initial state cleanly.

**7.2 Receive cancel**
```bash
$B click "#main-tab-receive"
$B fill "#receive-code" "99-fake-code"
$B click "#receive-btn"
sleep 1
$B is visible "#receive-status"
$B click "#receive-cancel-btn"
$B is visible "#receive-initial"
$B is visible "#receive-code"
```
Verify: UI returns to initial state cleanly.

---

### Group 8: Error Handling

**8.1 Invalid wormhole code**
```bash
$B goto $TARGET
$B wait --load
sleep 2
$B click "#main-tab-receive"
$B fill "#receive-code" "invalid"
$B click "#receive-btn"
sleep 5
$B js "document.getElementById('receive-status-text')?.textContent"
$B js "document.getElementById('receive-status-text')?.className"
```
Verify: error message is shown (status text class contains "error" or text indicates failure).

**8.2 Empty code rejected**
```bash
$B goto $TARGET
$B wait --load
sleep 2
$B click "#main-tab-receive"
$B click "#receive-btn"
$B is visible "#receive-initial"
```
Verify: clicking Receive with empty input does nothing (stays on initial state).

---

### Group 9: Responsive Design

**9.1 Mobile viewport**
```bash
$B viewport 375x812
$B goto $TARGET
$B wait --load
sleep 1
$B screenshot /tmp/wormhole-mobile.png
$B is visible "#dropzone"
$B is visible "h1"
$B js "document.body.scrollWidth <= 375"
```
Verify: no horizontal scroll, key elements visible.

**9.2 Tablet viewport**
```bash
$B viewport 768x1024
$B goto $TARGET
$B wait --load
sleep 1
$B is visible "#dropzone"
$B js "document.body.scrollWidth <= 768"
```
Verify: no horizontal scroll, layout works.

**9.3 Restore desktop viewport**
```bash
$B viewport 1280x800
```

---

### Group 10: Service Worker

**10.1 Service worker registered**
```bash
$B goto $TARGET
$B wait --load
sleep 2
$B js "navigator.serviceWorker.controller !== null || navigator.serviceWorker.ready.then(() => true)"
```
Verify: service worker is registered (may need a reload for activation).

**10.2 Service worker scope**
```bash
$B js "navigator.serviceWorker.ready.then(r => r.scope)"
```
Verify: scope is the site origin (e.g., `https://wormhole.page/`).

---

### Group 11: Copy & Share Buttons

**11.1 Copy button exists and is clickable**
```bash
$B goto $TARGET
$B wait --load
sleep 2
$B upload "#file-input" /tmp/wormhole-e2e-test.txt
sleep 4
$B is visible "#btn-copy"
$B click "#btn-copy"
$B js "document.getElementById('copy-label')?.textContent"
```
Verify: copy label changes to "Copied!" after click.

**11.2 Code box click copies**
```bash
$B click "#send-code"
sleep 1
$B js "document.getElementById('copy-label')?.textContent"
```
Verify: label shows "Copied!".

**11.3 QR mode toggle**
```bash
$B click "#tab-wormhole"
$B js "document.getElementById('send-code')?.textContent"
```
Verify: code box shows the raw wormhole code (not a URL). Should NOT start with "https://".

```bash
$B click "#tab-web"
$B js "document.getElementById('send-code')?.textContent"
```
Verify: code box shows the receive URL (starts with "https://").

**11.4 Cancel and clean up**
```bash
$B click "#send-cancel-btn"
```

---

### Group 12: Transit Bridge (WebSocket)

**12.1 WebSocket endpoint reachable**
```bash
$B js "new Promise(resolve => { const ws = new WebSocket(location.protocol.replace('http','ws') + '//' + location.host + '/transit'); ws.onopen = () => { ws.close(); resolve('open'); }; ws.onerror = () => resolve('error'); setTimeout(() => resolve('timeout'), 5000); })"
```
Verify: returns `open` (WebSocket connection established to transit bridge).

---

### Group 13: URL-based Receive

**13.1 Direct URL starts receive automatically**
```bash
$B goto $TARGET/receive/7-test-code
$B wait --load
sleep 3
$B js "document.getElementById('main-tab-receive')?.classList.contains('active')"
$B js "!document.getElementById('receive-status')?.classList.contains('hidden')"
```
Verify:
- Receive tab is active (not Send)
- Receive status is visible (flow started)

**13.2 URL cleaned on cancel**
```bash
$B click "#receive-cancel-btn"
$B url
```
Verify: URL is now just `$TARGET/` (not `/receive/...`).

---

## Final Report

After all groups complete, produce a summary:

```
## Pre-Release E2E Results

**Target:** $TARGET
**Date:** $(date -u)
**Browser:** Chromium (headless)

### Results

| # | Test | Result |
|---|------|--------|
| 1.1 | Health endpoint | PASS |
| ... | ... | ... |

### Summary
- **Passed:** X / Y
- **Failed:** Z
- **Verdict:** SHIP ✓ / NO-SHIP ✗

### Failed Tests (if any)
- [test id]: [description of failure]
```

**SHIP criteria:**
- Groups 1-6 MUST all pass (infrastructure, security, WASM, UI, send, receive)
- Group 7-8 (cancel/error) — failures are SHIP-BLOCKING
- Group 9-13 — failures are warnings, not blocking (note them for follow-up)

If verdict is NO-SHIP, list the blocking failures and stop the deploy.
If verdict is SHIP, proceed with deploy.

---

## Cleanup

```bash
$B stop 2>/dev/null || true
rm -f /tmp/wormhole-e2e-test.txt /tmp/wormhole-mobile.png
```
