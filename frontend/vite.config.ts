import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'

/** Preload the Interface regular (latin), the face almost every first-render glyph uses. */
function preloadInterfaceFont(): Plugin {
  return {
    name: 'preload-interface-font',
    apply: 'build',
    transformIndexHtml: {
      order: 'post',
      handler(_html, ctx) {
        const file = Object.keys(ctx.bundle ?? {}).find((name) => /instrument-sans-latin-standard-normal.*\.woff2$/.test(name))
        if (!file) return []
        return [
          {
            tag: 'link',
            attrs: { rel: 'preload', as: 'font', type: 'font/woff2', href: `/${file}`, crossorigin: '' },
            injectTo: 'head',
          },
        ]
      },
    },
  }
}

// The SPA only talks to /v1 (FR-17.1). In dev, Vite proxies it to the API so the
// browser sees a single origin (cookies and SSE work without CORS).
const apiTarget = process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react(), tailwindcss(), preloadInterfaceFont()],
  server: {
    port: 5173,
    proxy: {
      '/v1': { target: apiTarget, changeOrigin: false },
    },
  },
})
