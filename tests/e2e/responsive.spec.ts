import { test, expect } from '@playwright/test';

test.describe('Responsive design', () => {
  test('mobile viewport (375px) — UI is usable', async ({ browser }) => {
    const context = await browser.newContext({
      viewport: { width: 375, height: 667 },
    });
    const page = await context.newPage();
    await page.goto('/');

    // Key elements are visible and not overflowing
    const dropzone = page.locator('#dropzone');
    await expect(dropzone).toBeVisible();

    const title = page.locator('h1');
    await expect(title).toBeVisible();

    const subtitle = page.locator('.subtitle');
    await expect(subtitle).toBeVisible();

    // Tabs are visible
    await expect(page.locator('#main-tab-send')).toBeVisible();
    await expect(page.locator('#main-tab-receive')).toBeVisible();

    // Theme toggle is accessible
    await expect(page.locator('#theme-toggle')).toBeVisible();

    // Container does not cause horizontal scroll
    const bodyWidth = await page.evaluate(() => document.body.scrollWidth);
    expect(bodyWidth).toBeLessThanOrEqual(375);

    await context.close();
  });

  test('tablet viewport (768px) — UI is usable', async ({ browser }) => {
    const context = await browser.newContext({
      viewport: { width: 768, height: 1024 },
    });
    const page = await context.newPage();
    await page.goto('/');

    // Key elements are visible
    await expect(page.locator('#dropzone')).toBeVisible();
    await expect(page.locator('h1')).toBeVisible();
    await expect(page.locator('#main-tab-send')).toBeVisible();
    await expect(page.locator('#main-tab-receive')).toBeVisible();

    // Switch to receive tab and verify input is usable
    await page.click('#main-tab-receive');
    await expect(page.locator('#receive-code')).toBeVisible();

    // No horizontal overflow
    const bodyWidth = await page.evaluate(() => document.body.scrollWidth);
    expect(bodyWidth).toBeLessThanOrEqual(768);

    await context.close();
  });

  test('desktop viewport (1280px) — container is centered', async ({ browser }) => {
    const context = await browser.newContext({
      viewport: { width: 1280, height: 800 },
    });
    const page = await context.newPage();
    await page.goto('/');

    const container = page.locator('.container');
    await expect(container).toBeVisible();

    // Container should be centered — left margin should be roughly equal to right margin
    const box = await container.boundingBox();
    expect(box).not.toBeNull();
    if (box) {
      const leftMargin = box.x;
      const rightMargin = 1280 - (box.x + box.width);
      // Margins should be approximately equal (within 10px)
      expect(Math.abs(leftMargin - rightMargin)).toBeLessThan(10);
    }

    await context.close();
  });
});
