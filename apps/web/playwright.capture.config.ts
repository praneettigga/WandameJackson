import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './e2e-capture', timeout: 60_000,
  use: { baseURL: 'http://127.0.0.1:5188', viewport: { width: 1440, height: 950 },
    launchOptions: { executablePath: process.env.ROOMSHIFT_CHROMIUM, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] } },
  webServer: { command: 'node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5188 --strictPort',
    url: 'http://127.0.0.1:5188', reuseExistingServer: false, env: { VITE_USE_MOCK_API: 'false' } },
});
