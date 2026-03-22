import { test, expect } from '@playwright/test';

test.describe('Analytics proxy', () => {
  test('GET /a/script.js returns JavaScript', async ({ request }) => {
    const response = await request.get('/a/script.js');
    expect(response.status()).toBe(200);
    expect(response.headers()['content-type']).toContain('application/javascript');
    expect(response.headers()['cache-control']).toContain('max-age=86400');
    const body = await response.text();
    expect(body).toContain('umami');
  });

  test('POST /a/api/send proxies to Umami', async ({ request }) => {
    const response = await request.post('/a/api/send', {
      data: {
        type: 'event',
        payload: {
          website: '199379c8-f7f1-4363-af6d-80a1b24e24d0',
          hostname: 'localhost',
          url: '/',
          event_name: 'test',
        },
      },
    });
    // Umami returns 200 for valid payloads
    expect(response.status()).toBe(200);
  });

  test('CSP does not reference third-party analytics domains', async ({ request }) => {
    const response = await request.get('/');
    const csp = response.headers()['content-security-policy'] ?? '';
    expect(csp).not.toContain('cloud.umami.is');
    expect(csp).not.toContain('umami.dev');
  });

  test('index.html loads analytics from first-party /a/ path', async ({ request }) => {
    const response = await request.get('/');
    const html = await response.text();
    expect(html).toContain('src="/a/script.js"');
    expect(html).toContain('data-host-url="/a"');
    expect(html).not.toContain('cloud.umami.is');
  });
});
