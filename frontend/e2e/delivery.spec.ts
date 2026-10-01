import { expect, test } from '@playwright/test'

// ADR-0045 on the running stack (the web tier is nginx, so the plain Vite server can't show it):
// the static site is compressed and the API is not.

async function assetsOf(request: import('@playwright/test').APIRequestContext) {
  const html = await (await request.get('/')).text()
  const script = /src="(\/assets\/[^"]+\.js)"/.exec(html)?.[1]
  const style = /href="(\/assets\/[^"]+\.css)"/.exec(html)?.[1]
  expect(script, 'the page names its script').toBeTruthy()
  expect(style, 'the page names its stylesheet').toBeTruthy()
  return { html, script: script ?? '', style: style ?? '' }
}

test.describe('delivery', () => {
  test('the page, script and style are gzipped; the API and its stream are not', async ({ request }) => {
    const { script, style } = await assetsOf(request)
    for (const path of ['/', script, style]) {
      const response = await request.get(path, { headers: { 'accept-encoding': 'gzip' } })
      expect(response.headers()['content-encoding'], path).toBe('gzip')
      expect(response.headers()['vary'], path).toMatch(/accept-encoding/i)
    }
    const small = await request.get(script, { headers: { 'accept-encoding': 'identity' } })
    expect(small.headers()['content-encoding'], 'only when the browser asks').toBeUndefined()
    const api = await request.get('/v1/meta', { headers: { 'accept-encoding': 'gzip' } })
    expect(api.ok()).toBeTruthy()
    expect(api.headers()['content-encoding'], 'API responses are never compressed').toBeUndefined()
  })

  test('compression keeps the security headers and the caching', async ({ request }) => {
    const { script } = await assetsOf(request)
    const asset = await request.get(script, { headers: { 'accept-encoding': 'gzip' } })
    expect(asset.headers()['content-security-policy']).toContain("default-src 'self'")
    expect(asset.headers()['x-content-type-options']).toBe('nosniff')
    expect(asset.headers()['cache-control']).toMatch(/max-age=31536000/)
    const page = await request.get('/', { headers: { 'accept-encoding': 'gzip' } })
    expect(page.headers()['cache-control']).toMatch(/no-cache/)
  })
})
