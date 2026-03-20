import { test, expect } from '@playwright/test';

test.describe('Service worker', () => {
  test('registers after page load', async ({ page }) => {
    await page.goto('/');

    const swUrl = await page.evaluate(async () => {
      const registration = await navigator.serviceWorker.ready;
      return registration.active?.scriptURL ?? null;
    });

    expect(swUrl).not.toBeNull();
    expect(swUrl).toContain('/sw.js');
  });

  test('scope is the site origin', async ({ page }) => {
    await page.goto('/');

    const scope = await page.evaluate(async () => {
      const registration = await navigator.serviceWorker.ready;
      return registration.scope;
    });

    expect(scope).toBe('http://localhost:8080/');
  });
});
