import { defineConfig } from '@playwright/test';
import { resolve } from 'node:path';
export default defineConfig({
  testDir: './e2e-calibration',
  timeout: 60_000,
  workers: 1,
  use: {
    baseURL: 'http://127.0.0.1:5189',
    viewport: { width: 1440, height: 1100 },
    launchOptions: {
      executablePath: process.env.ROOMSHIFT_CHROMIUM,
      args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
    },
  },
  webServer: [
    {
      command: `"${resolve('../../services/api/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')}" "${resolve('../../services/api/scripts/calibration_e2e_server.py')}"`,
      url: 'http://127.0.0.1:8012/api/health',
      reuseExistingServer: false,
    },
    {
      command: 'node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5189 --strictPort',
      url: 'http://127.0.0.1:5189',
      reuseExistingServer: false,
      env: { VITE_USE_MOCK_API: 'false', VITE_API_BASE_URL: 'http://127.0.0.1:8012' },
    },
  ],
});
