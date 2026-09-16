/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Makefile's `frontend-dev` target exports DATA_API_PORT (shifted to 19100 under
// LOCAL_DEV=true, troll/.env, to avoid colliding with the SSH-tunnel-forwarded port a
// desktop uses to reach the real VPS -- see troll/CLAUDE.md's "Desktop <-> VPS
// Connection"). Falls back to 9100 (data_api's own default) for a bare `npm run dev`.
const dataApiPort = process.env.DATA_API_PORT || '9100'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Dev-server proxy to a running data_api instance (architecture spine's Deployment
    // section: "the frontend has zero server-rendering dependency ... can be developed
    // with Vite's dev server proxying /api/ws to a running data_api instance").
    proxy: {
      '/api': `http://127.0.0.1:${dataApiPort}`,
      '/ws': { target: `ws://127.0.0.1:${dataApiPort}`, ws: true },
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
