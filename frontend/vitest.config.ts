import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// Unit tests for what Playwright is slow or blind at: a component given a stubbed client, a pure
// helper. The browser behaviour is still held by e2e/. No Tailwind here: nothing reads styles.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: ['./src/test-setup.ts'],
    clearMocks: true,
    restoreMocks: true,
  },
})
