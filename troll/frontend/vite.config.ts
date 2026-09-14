/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Dev-server proxy to a running data_api instance (architecture spine's Deployment
    // section: "the frontend has zero server-rendering dependency ... can be developed
    // with Vite's dev server proxying /api/ws to a running data_api instance").
    proxy: {
      '/api': 'http://127.0.0.1:9100',
      '/ws': { target: 'ws://127.0.0.1:9100', ws: true },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    // scripts/*.test.mjs run under Node's own built-in test runner (see package.json's
    // "test:codegen" script) -- plain Node-script tests, no DOM/React involved, and
    // node:test's `test()` isn't meant to run inside Vitest's own collection mechanism.
    exclude: ['node_modules/**', 'scripts/**'],
  },
})
