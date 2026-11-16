import { defineConfig } from '@playwright/test';
import { resolve } from 'node:path';
// Grouped projects (buildings and floors) against a real API on an empty temporary data folder.
export default defineConfig({
  testDir: './e2e-assembly',
  timeout: 180_000,
  workers: 1,
  use: {
    baseURL: 'http://127.0.0.1:5190',
    viewport: { width: 1440, height: 1000 },
    launchOptions: {
      executablePath: process.env.ROOMSHIFT_CHROMIUM,
      args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
    },
  },
  webServer: [
    {
      command: `"${resolve('../../services/api/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')}" "${resolve('../../services/api/scripts/assembly_e2e_server.py')}"`,
      url: 'http://127.0.0.1:8013/api/health',
      reuseExistingServer: !process.env.CI,
    },
    {
      command: 'node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5190 --strictPort',
      url: 'http://127.0.0.1:5190',
      reuseExistingServer: !process.env.CI,
      env: {
        VITE_USE_MOCK_API: 'false',
        VITE_API_BASE_URL: 'http://127.0.0.1:8013',
        VITE_E2E: 'true',
      },
    },
  ],
});
