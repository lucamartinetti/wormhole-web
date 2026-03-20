import { test, expect } from '@playwright/test';

test.describe('Transit WebSocket', () => {
  test('/transit endpoint accepts connections', async ({ page }) => {
    await page.goto('/');

    const result = await page.evaluate(() => {
      return new Promise<{ event: string; code?: number }>((resolve) => {
        const ws = new WebSocket(`ws://${location.host}/transit`);

        ws.addEventListener('open', () => {
          ws.close();
          resolve({ event: 'open' });
        });

        ws.addEventListener('close', (e) => {
          resolve({ event: 'close', code: e.code });
        });

        ws.addEventListener('error', () => {
          resolve({ event: 'error' });
        });

        // Timeout after 5 seconds
        setTimeout(() => {
          ws.close();
          resolve({ event: 'timeout' });
        }, 5000);
      });
    });

    // Accept both open (relay reachable) and close:1011 (relay unreachable but endpoint functional)
    expect(
      result.event === 'open' || (result.event === 'close' && result.code === 1011),
    ).toBeTruthy();
  });
});
