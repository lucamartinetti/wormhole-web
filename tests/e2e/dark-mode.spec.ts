import { test, expect } from '@playwright/test';

test.describe('Dark mode toggle', () => {
  test('theme toggle button exists', async ({ page }) => {
    await page.goto('/');
    const toggle = page.locator('#theme-toggle');
    await expect(toggle).toBeVisible();
    await expect(toggle).toHaveAttribute('role', 'switch');
  });

  test('toggle changes theme attribute', async ({ page }) => {
    await page.goto('/');
    const toggle = page.locator('#theme-toggle');
    const html = page.locator('html');

    // Click to toggle theme
    await toggle.click();
    const themeAfterFirst = await html.getAttribute('data-theme');
    expect(themeAfterFirst).toBeTruthy();

    // Click again to toggle back
    await toggle.click();
    const themeAfterSecond = await html.getAttribute('data-theme');
    expect(themeAfterSecond).not.toBe(themeAfterFirst);
  });

  test('toggle switches between sun and moon icons', async ({ page }) => {
    await page.goto('/');
    const toggle = page.locator('#theme-toggle');

    // Click to switch to dark mode
    await toggle.click();
    const theme = await page.locator('html').getAttribute('data-theme');

    if (theme === 'dark') {
      // Moon icon visible, sun hidden
      await expect(page.locator('#theme-icon-moon')).toBeVisible();
      await expect(page.locator('#theme-icon-sun')).toBeHidden();
    } else {
      // Sun icon visible, moon hidden
      await expect(page.locator('#theme-icon-sun')).toBeVisible();
      await expect(page.locator('#theme-icon-moon')).toBeHidden();
    }
  });

  test('theme preference persists in localStorage', async ({ page }) => {
    await page.goto('/');
    const toggle = page.locator('#theme-toggle');

    // Toggle theme
    await toggle.click();
    const theme = await page.locator('html').getAttribute('data-theme');

    // Check localStorage
    const stored = await page.evaluate(() => localStorage.getItem('theme'));
    expect(stored).toBe(theme);

    // Reload and verify persistence
    await page.reload();
    const themeAfterReload = await page.locator('html').getAttribute('data-theme');
    expect(themeAfterReload).toBe(theme);
  });
});
