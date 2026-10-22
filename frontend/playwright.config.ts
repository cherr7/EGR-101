import { defineConfig, devices } from '@playwright/test'
const external = process.env.PLAYWRIGHT_BASE_URL
export default defineConfig({
  testDir: './tests', fullyParallel: false, workers: 1, timeout: 35000,
  expect: { timeout: 12000 }, reporter: 'list',
  use: { baseURL: external || 'http://127.0.0.1:5173', trace: 'retain-on-failure', ...devices['Desktop Chrome'] },
  webServer: external ? undefined : {
    command: 'npm run dev -- --port 5173',
    url: 'http://127.0.0.1:5173',
    env: { API_URL: process.env.API_URL || 'http://127.0.0.1:8001' },
    reuseExistingServer: false,
    timeout: 30000,
  },
})
