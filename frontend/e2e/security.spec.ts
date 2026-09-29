import { expect, test } from '@playwright/test'

/**
 * R.11 through the web tier: the headers a browser gets, no CORS, a write from another site
 * refused, a body over the limit refused, and the dev helpers present only where dev sign-in is.
 */
test.describe('web hardening', () => {
  test('the page and the API carry the security headers', async ({ request }) => {
    const page = await request.get('/')
    expect(page.headers()['x-content-type-options']).toBe('nosniff')
    expect(page.headers()['x-frame-options']).toBe('DENY')
    expect(page.headers()['referrer-policy']).toBeTruthy()
    const csp = page.headers()['content-security-policy'] ?? ''
    expect(csp).toContain("default-src 'self'")
    expect(csp).toContain("frame-ancestors 'none'")

    const api = await request.get('/v1/meta')
    expect(api.headers()['x-content-type-options']).toBe('nosniff')
    expect(api.headers()['cache-control']).toBe('no-store')
    expect(api.headers()['content-security-policy']).toContain("default-src 'none'")
    // Hashed assets keep the headers too (a location's own headers would otherwise drop them).
    const html = await page.text()
    const asset = /\/assets\/[^"']+\.js/.exec(html)?.[0]
    if (asset) expect((await request.get(asset)).headers()['x-content-type-options']).toBe('nosniff')
  })

  test('a browser is given no CORS permission', async ({ request }) => {
    const preflight = await request.fetch('/v1/auth/dev-login', {
      method: 'OPTIONS',
      headers: { Origin: 'https://evil.example', 'Access-Control-Request-Method': 'POST' },
    })
    const names = Object.keys(preflight.headers())
    expect(names.filter((h) => h.startsWith('access-control-'))).toEqual([])
  })

  test('a write from another site is refused, and so is one that is not JSON', async ({ request }) => {
    const foreign = await request.post('/v1/auth/dev-login', { data: {}, headers: { Origin: 'https://evil.example' } })
    expect(foreign.status()).toBe(403)
    const crossSite = await request.post('/v1/auth/dev-login', { data: {}, headers: { 'Sec-Fetch-Site': 'cross-site' } })
    expect(crossSite.status()).toBe(403)
    const form = await request.post('/v1/auth/dev-login', { headers: { 'Content-Type': 'text/plain' }, data: '{}' })
    expect(form.status()).toBe(415)
  })

  test('a body over the limit is refused at the door', async ({ request }) => {
    const big = await request.post('/v1/auth/dev-login', { data: { email: `${'a'.repeat(400_000)}@example.test` } })
    expect([413, 422]).toContain(big.status())
    expect(big.status()).not.toBe(200)
  })

  test('the interactive docs and the dev helpers are for development and staging', async ({ request }) => {
    const meta = (await (await request.get('/v1/meta')).json()) as { env: string; dev_auth: boolean }
    const docs = await request.get('/docs/api')
    expect(docs.status()).toBe(meta.env === 'production' ? 404 : 200)
    if (!meta.dev_auth) expect((await request.post('/v1/dev/seed-recall')).status()).toBe(404)
  })
})
