import { test, expect } from '@playwright/test';

test.describe('WASM initialization', () => {
  test('WASM initializes within 5 seconds', async ({ page }) => {
    await page.goto('/');
    await page.waitForFunction(
      () => window['wasmClient']?.ready === true,
      null,
      { timeout: 5000 },
    );
  });

  test('encryption badge shows "End-to-end encrypted" after WASM loads', async ({ page }) => {
    await page.goto('/');
    const badge = page.locator('#encryption-status');

    // Wait for WASM to load
    await page.waitForFunction(
      () => window['wasmClient']?.ready === true,
      null,
      { timeout: 5000 },
    );

    await expect(badge).toHaveText('End-to-end encrypted');
    await expect(badge).toHaveClass(/encrypted/);
    await expect(badge).not.toHaveClass(/warning/);
  });

  test('shows warning when WASM script is blocked', async ({ context, page }) => {
    // Block the WASM JS module so initialization fails
    await context.route('**/static/wasm/**', (route) => route.abort());

    await page.goto('/');
    const badge = page.locator('#encryption-status');

    // Give it a moment to attempt (and fail) initialization
    await page.waitForTimeout(2000);

    await expect(badge).toHaveClass(/warning/);
    await expect(badge).toContainText('does not support WebAssembly');
  });
});
