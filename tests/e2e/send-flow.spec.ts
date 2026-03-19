import { test, expect } from '@playwright/test';

test.describe('Send flow', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    // Wait for WASM so send flow can proceed
    await page.waitForFunction(
      () => window['wasmClient']?.ready === true,
      null,
      { timeout: 5000 },
    );
  });

  test('selecting a file shows send status panel', async ({ page }) => {
    // The send-initial panel should be visible, send-status hidden
    await expect(page.locator('#send-initial')).toBeVisible();
    await expect(page.locator('#send-status')).toBeHidden();

    // Use file chooser to select a file
    const fileChooserPromise = page.waitForEvent('filechooser');
    await page.locator('#dropzone').click();
    const fileChooser = await fileChooserPromise;
    await fileChooser.setFiles({
      name: 'test-file.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('hello wormhole'),
    });

    // Send status panel should now be visible, initial hidden
    await expect(page.locator('#send-status')).toBeVisible();
    await expect(page.locator('#send-initial')).toBeHidden();
  });

  test('file name and size are displayed correctly', async ({ page }) => {
    const content = 'a'.repeat(2048); // 2 KB file
    const fileChooserPromise = page.waitForEvent('filechooser');
    await page.locator('#dropzone').click();
    const fileChooser = await fileChooserPromise;
    await fileChooser.setFiles({
      name: 'my-document.pdf',
      mimeType: 'application/pdf',
      buffer: Buffer.from(content),
    });

    await expect(page.locator('#send-filename')).toHaveText('my-document.pdf');
    await expect(page.locator('#send-filesize')).toHaveText('2.0 KB');
  });

  test('status shows "allocating code..." initially', async ({ page }) => {
    const fileChooserPromise = page.waitForEvent('filechooser');
    await page.locator('#dropzone').click();
    const fileChooser = await fileChooserPromise;
    await fileChooser.setFiles({
      name: 'test.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('test'),
    });

    await expect(page.locator('#send-status-text')).toHaveText('allocating code...');
  });

  test('cancel button returns to initial state', async ({ page }) => {
    const fileChooserPromise = page.waitForEvent('filechooser');
    await page.locator('#dropzone').click();
    const fileChooser = await fileChooserPromise;
    await fileChooser.setFiles({
      name: 'test.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('test'),
    });

    // Verify we are in send status
    await expect(page.locator('#send-status')).toBeVisible();

    // Click cancel
    await page.locator('#send-cancel-btn').click();

    // Should return to initial state
    await expect(page.locator('#send-initial')).toBeVisible();
    await expect(page.locator('#send-status')).toBeHidden();
    await expect(page.locator('#dropzone')).toBeVisible();
  });

  test('dropzone is hidden during send', async ({ page }) => {
    const fileChooserPromise = page.waitForEvent('filechooser');
    await page.locator('#dropzone').click();
    const fileChooser = await fileChooserPromise;
    await fileChooser.setFiles({
      name: 'test.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('test'),
    });

    // Dropzone is inside send-initial which should be hidden
    await expect(page.locator('#send-initial')).toBeHidden();
  });
});
