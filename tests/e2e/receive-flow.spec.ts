import { test, expect } from '@playwright/test';

test.describe('Receive flow', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    // Wait for WASM so receive flow can proceed
    await page.waitForFunction(
      () => window['wasmClient']?.ready === true,
      null,
      { timeout: 5000 },
    );
    // Switch to receive tab
    await page.click('#main-tab-receive');
  });

  test('entering a code and clicking Receive shows receive status', async ({ page }) => {
    await expect(page.locator('#receive-initial')).toBeVisible();
    await expect(page.locator('#receive-status')).toBeHidden();

    await page.fill('#receive-code', '7-guitar-hero');
    await page.click('#receive-initial button.btn');

    await expect(page.locator('#receive-status')).toBeVisible();
    await expect(page.locator('#receive-initial')).toBeHidden();
  });

  test('shows "establishing encrypted connection..." status', async ({ page }) => {
    await page.fill('#receive-code', '7-guitar-hero');
    await page.click('#receive-initial button.btn');

    await expect(page.locator('#receive-status-text')).toHaveText(
      'establishing encrypted connection...',
    );
  });

  test('cancel button returns to initial state', async ({ page }) => {
    await page.fill('#receive-code', '7-guitar-hero');
    await page.click('#receive-initial button.btn');

    // Verify we are in receive status
    await expect(page.locator('#receive-status')).toBeVisible();

    // Click cancel
    await page.locator('#receive-cancel-btn').click();

    // Should return to initial state
    await expect(page.locator('#receive-initial')).toBeVisible();
    await expect(page.locator('#receive-status')).toBeHidden();
  });

  test('URL-based receive (/receive/test-code) pre-fills the code input', async ({ page }) => {
    // Navigate directly to a receive URL
    await page.goto('/receive/7-guitar-hero');

    // The receive tab should be active and code input should be filled
    // Give it a moment — the URL handler uses setTimeout polling
    await page.waitForTimeout(500);

    const receiveTab = page.locator('#main-tab-receive');
    await expect(receiveTab).toHaveClass(/active/);

    // The code should be pre-filled (either in the input or already started receiving)
    const codeInput = page.locator('#receive-code');
    const receiveStatus = page.locator('#receive-status');

    // Either the code is in the input, or receive has already started
    const inputValue = await codeInput.inputValue();
    const statusVisible = await receiveStatus.isVisible();
    expect(inputValue === '7-guitar-hero' || statusVisible).toBeTruthy();
  });

  test('canceling URL-based receive cleans the URL back to /', async ({ page }) => {
    await page.goto('/receive/7-guitar-hero');
    await page.waitForTimeout(500);

    // Wait for receive to start
    await expect(page.locator('#receive-status')).toBeVisible({ timeout: 5000 });

    // Cancel
    await page.locator('#receive-cancel-btn').click();

    // URL should be cleaned back to /
    await expect(page).toHaveURL('http://localhost:8080/');
  });
});
