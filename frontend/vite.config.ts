import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The SPA only talks to /v1 (FR-17.1). In dev, Vite proxies it to the API so the
// browser sees a single origin (cookies and SSE work without CORS).
const apiTarget = process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/v1': { target: apiTarget, changeOrigin: false },
    },
  },
})
