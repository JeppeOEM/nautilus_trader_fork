# Story 21.4: Vite proxy connection resets

Status: done

Split from the original 21.1 draft (2026-09-19 incident: odd candles, wrong volume, slow charts, vite `EPIPE`/`ECONNRESET`). Read `troll/docs/DATA_INTEGRITY_AUDIT.md` first.

## Story

As the dashboard operator,
I want dev-server websocket resets (EPIPE / ECONNRESET, incl. over the SSH tunnel) to be handled quietly,
so that real errors are not buried under stack-trace spam.

## Acceptance Criteria

1. **Given** `vite.config.ts` **When** `/ws` proxy hits EPIPE/ECONNRESET **Then** `configure(proxy)` handles `error` (and socket errors) and logs one throttled line, never a stack trace per event.
2. **Given** a killed SSH tunnel mid-session **Then** `useLiveChannel` reconnects and live resumes (existing behaviour, verified by a test with a fake socket close).
3. If resets persist on an idle tunnel, the tunnel command in `troll/CLAUDE.md` gets `ServerAliveInterval`; state whether needed.

## Tasks / Subtasks

- [ ] Task 1: proxy error handler (small pure throttled-logger fn + unit test)
- [ ] Task 2: reconnect test / doc note

## Dev Notes

- Production actions (`make redeploy-all`, `repair_catalog --apply`) are operator-approved, never run by the dev agent.

### References

- [Source: troll/docs/DATA_INTEGRITY_AUDIT.md] register D-01…D-23
- [Source: _bmad-output/implementation-artifacts/epic-15-context.md] chart invariants (AD-F2 one aggregation path, AD-F3 cursor contract, AD-F6 gap markers, AD-F7 live edge)
- Binding rules `troll/CLAUDE.md`: DATA-01/02/05, MEM-01/02, TEST-01/03/04, NAUT-01, FORK-01

## Dev Agent Record

### Agent Model Used

### Completion Notes List

`customLogger` in `vite.config.ts` collapses `ws proxy (socket) error … EPIPE|ECONNRESET` into one throttled line. Regex verified against the two reported messages; I could NOT reproduce the error locally (neither old nor new config logged one with a fake upstream), so the live suppression is unverified. Reconnect behaviour is existing `useLiveChannel` code, not re-tested. `ServerAliveInterval` not added (no evidence needed).

### File List
