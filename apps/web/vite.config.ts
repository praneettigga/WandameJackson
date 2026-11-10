import { configDefaults, defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    rollupOptions: {
      output: {
        // Large, rarely-changing libraries get their own long-cached chunks.
        manualChunks: {
          three: ['three'],
          r3f: ['@react-three/fiber', '@react-three/drei'],
          react: ['react', 'react-dom', 'zustand'],
        },
      },
    },
    chunkSizeWarningLimit: 900,
  },
  server: {
    port: 5173,
    strictPort: true,
    fs: { allow: [fileURLToPath(new URL('../..', import.meta.url))] },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
    restoreMocks: true,
    // Browser tests run under Playwright (npm run e2e).
    exclude: [...configDefaults.exclude, 'e2e/**', 'e2e-capture/**', 'e2e-calibration/**'],
  },
});
