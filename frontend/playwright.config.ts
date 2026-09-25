import { defineConfig, devices } from '@playwright/test'

// Runs against the compose stack in fake-provider mode (`make e2e`).
export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  // Locally the whole stack shares a small Docker VM; more workers only add contention.
  workers: process.env.CI ? undefined : 2,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:8080',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'desktop', testIgnore: /motion\.spec/, use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 } } },
    { name: 'mobile-360', testIgnore: /motion\.spec/, use: { ...devices['Pixel 5'], viewport: { width: 360, height: 740 } } },
    // UI.13: the docked inspector and the widest layout.
    { name: 'wide-1440', testMatch: /(layout|a11y)\.spec/, use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } },
    // UI.13: a whole turn with the OS asking for less motion.
    {
      name: 'reduced-motion',
      testMatch: /motion\.spec/,
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 }, contextOptions: { reducedMotion: 'reduce' } },
    },
  ],
})
