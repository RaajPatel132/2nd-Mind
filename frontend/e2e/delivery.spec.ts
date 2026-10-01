import { expect, test } from '@playwright/test'

// ADR-0045 on the running stack (the web tier is nginx, so the plain Vite server can't show it):
// the static site is compressed and the API is not, and the page names the address it was
// reached at in its link-preview tags.

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

  test('the page names the address it was reached at', async ({ request, baseURL }) => {
    const { html } = await assetsOf(request)
    expect(html).not.toContain('__SITE_ORIGIN__')
    expect(html).toContain(`<meta property="og:image" content="${baseURL ?? ''}/og.png"`)
    expect(html).toContain(`<meta property="og:url" content="${baseURL ?? ''}/"`)
    // Tags may span lines in the source; what matters is the name and a real sentence.
    expect(html).toMatch(/<meta\s+name="description"\s+content="[^"]{60,200}"/)
    expect(html).toMatch(/<meta\s+property="og:description"\s+content="[^"]{40,200}"/)
    expect(html).toContain('<meta name="theme-color" content="#08080A"')
    expect(html).toContain('<meta name="twitter:card" content="summary_large_image"')
  })

  test('behind the edge the scheme comes from X-Forwarded-Proto', async ({ request, baseURL }) => {
    const host = new URL(baseURL ?? 'http://localhost:8080').host
    const html = await (await request.get('/', { headers: { 'x-forwarded-proto': 'https' } })).text()
    expect(html).toContain(`<meta property="og:image" content="https://${host}/og.png"`)
  })

  test('a crafted header never reaches the page: the tags fall back to relative paths', async ({ request }) => {
    for (const proto of ['javascript:', 'https"><script>alert(1)</script>', 'ftp']) {
      const response = await request.get('/', { headers: { 'x-forwarded-proto': proto } })
      expect(response.ok(), proto).toBeTruthy()
      const html = await response.text()
      expect(html, proto).toContain('<meta property="og:image" content="/og.png"')
      expect(html, proto).not.toContain('alert(1)')
    }
  })

  test('the preview image is a 1200 by 630 PNG', async ({ request }) => {
    const response = await request.get('/og.png')
    expect(response.ok()).toBeTruthy()
    expect(response.headers()['content-type']).toBe('image/png')
    const bytes = await response.body()
    expect(bytes.subarray(0, 8).toString('hex')).toBe('89504e470d0a1a0a')
    expect(bytes.readUInt32BE(16)).toBe(1200)
    expect(bytes.readUInt32BE(20)).toBe(630)
    expect(bytes.length).toBeLessThan(600 * 1024)
  })
})
