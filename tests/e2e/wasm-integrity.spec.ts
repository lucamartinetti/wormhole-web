import { test, expect } from '@playwright/test';
import { execSync } from 'child_process';
import { readFileSync } from 'fs';
import { resolve } from 'path';

const ROOT = resolve(__dirname, '../..');

test.describe('WASM SRI integrity', () => {
  test('wasm binary hash matches meta[name="wasm-integrity"]', async ({ request }) => {
    // Get the expected hash from the HTML
    const html = await (await request.get('/')).text();
    const match = html.match(/<meta\s+name="wasm-integrity"\s+content="([^"]+)"/);
    const expectedHash = match?.[1];

    // In dev mode the placeholder is not a real hash — skip
    if (!expectedHash || !expectedHash.startsWith('sha384-')) {
      test.skip(true, 'SRI hashes not injected (dev mode)');
      return;
    }

    // Fetch the actual wasm binary and compute its hash
    const wasmResp = await request.get('/static/wasm/wormhole_wasm_bg.wasm');
    const wasmBytes = await wasmResp.body();
    const hashBuf = await crypto.subtle.digest('SHA-384', wasmBytes);
    const actualB64 = Buffer.from(hashBuf).toString('base64');
    const actualHash = `sha384-${actualB64}`;

    expect(actualHash).toBe(expectedHash);
  });

  test('wasm JS modulepreload integrity matches actual file', async ({ request }) => {
    // Get the expected hash from the HTML
    const html = await (await request.get('/')).text();
    const match = html.match(
      /<link\s+rel="modulepreload"\s+href="[^"]*wormhole_wasm\.js"\s+integrity="([^"]+)"/,
    );
    const expectedHash = match?.[1];

    if (!expectedHash || !expectedHash.startsWith('sha384-')) {
      test.skip(true, 'SRI hashes not injected (dev mode)');
      return;
    }

    // Fetch the actual JS file and compute its hash
    const jsResp = await request.get('/static/wasm/wormhole_wasm.js');
    const jsBytes = await jsResp.body();
    const hashBuf = await crypto.subtle.digest('SHA-384', jsBytes);
    const actualB64 = Buffer.from(hashBuf).toString('base64');
    const actualHash = `sha384-${actualB64}`;

    expect(actualHash).toBe(expectedHash);
  });

  test('WASM loads without integrity errors', async ({ page }) => {
    // Listen for console warnings about integrity
    const integrityErrors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'warning' && msg.text().includes('integrity check failed')) {
        integrityErrors.push(msg.text());
      }
    });

    await page.goto('/');

    // Wait for WASM to load — use the same check as wasm-init tests
    await page.waitForFunction(
      () => window['wasmClient']?.ready === true,
      null,
      { timeout: 10000 },
    );

    expect(integrityErrors).toEqual([]);
  });
});
