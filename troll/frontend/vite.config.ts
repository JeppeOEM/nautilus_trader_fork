/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { createLogger, defineConfig } from 'vite'

// Makefile's `frontend-dev` target exports DATA_API_PORT (shifted to 19100 under
// LOCAL_DEV=true, troll/.env, to avoid colliding with the SSH-tunnel-forwarded port a
// desktop uses to reach the real VPS -- see troll/CLAUDE.md's "Desktop <-> VPS
// Connection"). Falls back to 9100 (data_api's own default) for a bare `npm run dev`.
// A dropped socket (tab reload, `data_api` restart, SSH tunnel reset) surfaces as EPIPE /
// ECONNRESET from vite's ws proxy, logged by vite itself as a stack trace per event. The
// client already reconnects with backoff (useLiveChannel/useLiveCandle), so collapse that
// class to one short line per burst; every other vite error passes through untouched.
const PROXY_LOG_THROTTLE_MS = 5000
const PROXY_RESET = /ws proxy (socket )?error[\s\S]*(EPIPE|ECONNRESET)/
let lastProxyLogMs = 0
const logger = createLogger()
const baseError = logger.error.bind(logger)
logger.error = (msg, options) => {
  if (!PROXY_RESET.test(msg)) return baseError(msg, options)
  const now = Date.now()
  if (now - lastProxyLogMs < PROXY_LOG_THROTTLE_MS) return
  lastProxyLogMs = now
  logger.warn('[vite] ws proxy connection reset (client reconnects; further resets muted for 5s)', { timestamp: true })
}

const dataApiPort = process.env.DATA_API_PORT || '9100'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  customLogger: logger,
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
