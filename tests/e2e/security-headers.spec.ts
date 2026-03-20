import { test, expect } from '@playwright/test';

test.describe('Security headers', () => {
  test.describe('CSP directives', () => {
    let csp: string;

    test.beforeAll(async ({ request }) => {
      const response = await request.get('/');
      csp = response.headers()['content-security-policy'] ?? '';
    });

    test("default-src 'none'", () => {
      expect(csp).toContain("default-src 'none'");
    });

    test("script-src 'self' 'wasm-unsafe-eval'", () => {
      expect(csp).toContain("script-src 'self' 'wasm-unsafe-eval'");
    });

    test("style-src 'self' 'unsafe-inline'", () => {
      expect(csp).toContain("style-src 'self' 'unsafe-inline'");
    });

    test('connect-src includes wss://relay.magic-wormhole.io:443', () => {
      expect(csp).toContain("connect-src 'self' wss://relay.magic-wormhole.io:443");
    });

    test("frame-ancestors 'none'", () => {
      expect(csp).toContain("frame-ancestors 'none'");
    });

    test("base-uri 'self'", () => {
      expect(csp).toContain("base-uri 'self'");
    });

    test("form-action 'self'", () => {
      expect(csp).toContain("form-action 'self'");
    });

    test("script-src does NOT contain 'unsafe-inline'", () => {
      // Extract the script-src directive value
      const scriptSrc = csp.match(/script-src\s+([^;]+)/)?.[1] ?? '';
      expect(scriptSrc).not.toContain('unsafe-inline');
    });
  });

  test.describe('Other security headers', () => {
    let headers: Record<string, string>;

    test.beforeAll(async ({ request }) => {
      const response = await request.get('/');
      headers = response.headers();
    });

    test('x-frame-options is DENY', () => {
      expect(headers['x-frame-options']).toBe('DENY');
    });

    test('x-content-type-options is nosniff', () => {
      expect(headers['x-content-type-options']).toBe('nosniff');
    });

    test('strict-transport-security contains max-age=31536000', () => {
      expect(headers['strict-transport-security']).toContain('max-age=31536000');
    });

    test('referrer-policy is no-referrer', () => {
      expect(headers['referrer-policy']).toBe('no-referrer');
    });

    test('permissions-policy contains camera=(), microphone=(), geolocation=()', () => {
      const policy = headers['permissions-policy'];
      expect(policy).toContain('camera=()');
      expect(policy).toContain('microphone=()');
      expect(policy).toContain('geolocation=()');
    });
  });

  test('security headers present on /health too', async ({ request }) => {
    const response = await request.get('/health');
    const headers = response.headers();
    expect(headers['content-security-policy']).toBeTruthy();
    expect(headers['x-frame-options']).toBe('DENY');
    expect(headers['x-content-type-options']).toBe('nosniff');
    expect(headers['strict-transport-security']).toContain('max-age=31536000');
    expect(headers['referrer-policy']).toBe('no-referrer');
    expect(headers['permissions-policy']).toContain('camera=()');
  });
});
