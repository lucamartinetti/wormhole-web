import { test, expect } from '@playwright/test';

test.describe('Page load', () => {
  test('loads successfully with 200 status', async ({ page }) => {
    const response = await page.goto('/');
    expect(response?.status()).toBe(200);
  });

  test('has correct title', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveTitle('wormhole.page');
  });

  test('dropzone is visible', async ({ page }) => {
    await page.goto('/');
    const dropzone = page.locator('#dropzone');
    await expect(dropzone).toBeVisible();
    await expect(dropzone).toContainText('Drop a file here or click to browse');
  });

  test('receive code input is visible after switching to Receive tab', async ({ page }) => {
    await page.goto('/');
    await page.click('#main-tab-receive');
    const receiveInput = page.locator('#receive-code');
    await expect(receiveInput).toBeVisible();
    await expect(receiveInput).toHaveAttribute('placeholder', 'Enter wormhole code');
  });

  test('"How it works" section is expandable', async ({ page }) => {
    await page.goto('/');
    const details = page.locator('details.cli-section');
    const summary = details.locator('summary');
    const content = details.locator('.cli-content');

    // Initially collapsed
    await expect(details).not.toHaveAttribute('open', '');
    await expect(content).not.toBeVisible();

    // Click to expand
    await summary.click();
    await expect(details).toHaveAttribute('open', '');
    await expect(content).toBeVisible();
    await expect(content).toContainText('Magic Wormhole');

    // Click to collapse
    await summary.click();
    await expect(details).not.toHaveAttribute('open', '');
  });
});
