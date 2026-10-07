---
title: 'DW-78: refuse to start a bot whose bot_id another live process already owns'
type: 'feature'
created: '2026-10-07'
status: 'done'
baseline_revision: '5b90a749abe3173057c6a263c7db46d7cfdeef65'
final_revision: 'bd1fa7207dc3b7dcf99ac4ad0700b7f5f5470284'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
warnings: []
---

<intent-contract>

## Intent

**Problem:** Nothing stops two `python3 -m bots` processes (the paper fleet `config.toml` and an exec config via `LIVE_PAPER_REAL_MONEY_CONFIG`, or a cloned config) from running the same `bot_id` against one Redis: their `bots:status`/`bots:control`/`bots:incidents`/`bots:history` traffic silently merges paper and real-money history (DW-78). `bots:status` is pub/sub only, so there is no presence to check.

**Approach:** A per-bot Redis ownership lease `bots:owner:{bot_id}` whose value records the holder (a per-process instance token, mode label, config path, host). `bots/__main__.py` claims every bot's lease (atomic claim-or-refresh, `PX` TTL = 3x the status heartbeat) on the node's loop *before* `node.run()`; a lease still held by another holder after one full TTL (so a stale key from a crashed prior life has expired) aborts startup with an error naming both sides. Each `Supervisor` heartbeat tick refreshes its lease; shutdown releases it (compare-and-delete).

## Boundaries & Constraints

**Always:** claim, refresh and release are atomic compare-on-value Lua scripts (never GET-then-SET); the lease value is built once per process and compared verbatim; a refused start raises before `node.run()` and releases any leases this process already claimed; an unreachable Redis at startup is retried (each attempt ledgered as `bots.redis`) — the fleet never starts with ownership unverified; a lease lost at runtime to another holder is error-ledgered (`bots.ownership`) on the transition into and out of that state, and re-claimed as soon as it is free; the new key is documented as published language beside `bots:incidents:{bot_id}`.

**Block If:** a design would require changing the frozen `bots:status` payload or `bots:control` semantics.

**Never:** stop or flatten a running strategy automatically on a runtime ownership loss (documented `Known limit:` instead); key the lease on mode/config only (two processes of the same config are a collision too); edit the deferred-work ledger; touch `nautilus_trader/` or `crates/`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Fresh start | no `bots:owner:*` keys | every lease claimed, node runs | none |
| Clean restart | prior life released its leases | immediate claim | none |
| Crash restart | prior life's lease present, not refreshed | waits ≤ TTL + one heartbeat, then claims | none |
| Collision | another live process refreshes the lease (any mode/config) | startup refused, `BotIdCollision` naming bot_id + holder's mode/config/host vs ours; own claimed leases released | process exits non-zero (`on-failure:5`) |
| Refresh | lease ours | TTL renewed each heartbeat tick | none |
| Lease expired during a Redis blip, free | key gone | next tick re-claims | none |
| Lease taken over at runtime | key holds another value | not overwritten; ledgered once; ledgered again when regained | strategy keeps running (Known limit) |

</intent-contract>

## Code Map

- `platform/bots/application/ports.py` -- published key names + `BusConnection` protocol: add `owner_key`, `hold`, `release`
- `platform/bots/infrastructure/redis.py` -- `RedisBus`: implement `hold`/`release` as Lua scripts
- `platform/bots/application/ownership.py` (new) -- lease value, TTL, startup `claim_all`, `release_all`, `BotIdCollision`
- `platform/bots/application/supervise.py` -- `Supervisor.heartbeat_tick` refreshes the lease
- `platform/bots/__main__.py` -- claim before `node.run()`, release in `_stop`
- `platform/bots/tests/support.py` -- `FakeBus` gains TTL-aware `hold`/`release`
- `platform/docs/DATABASE_SETUP.md`, `platform/ARCHITECTURE.md`, `platform/bots/README.md`, `platform/docs/BOT_OPERATIONS.md` -- key + operator behaviour docs

## Tasks & Acceptance

**Execution:**
- [x] `platform/bots/application/ports.py` -- add `owner_key(bot_id)`; `BusConnection.hold(key, value, ttl_ms) -> bool` (claim if absent, renew if equal, else False) and `release(key, value) -> None` (delete only if equal)
- [x] `platform/bots/infrastructure/redis.py` -- implement both as `EVAL` Lua scripts over `PX`
- [x] `platform/bots/application/ownership.py` -- `OWNER_TTL_SECONDS = 3 * STATUS_HEARTBEAT_SECONDS`, `owner_value(mode, config, host, token, claimed_at) -> str`, `async claim_all(connect, bot_ids, value, ...)` (retry until each held or the deadline passes; Redis errors retried and ledgered), `async release_all`, `BotIdCollision`
- [x] `platform/bots/application/supervise.py` -- `Supervisor` takes the lease value; each heartbeat tick holds it, ledgering ownership loss/regain on transition
- [x] `platform/bots/__main__.py` -- build the value (config path = exec path or paper path), `loop.run_until_complete(claim_all(...))` before `node.run()`, release after the tasks unwind in `_stop`
- [x] `platform/bots/tests/support.py` + `platform/bots/tests/test_ownership.py` (new) -- unit-test the matrix: fresh claim, collision refusal (and release of already-claimed leases), crash restart (own stale key expiry), refresh, runtime takeover ledgered not overwritten, unreachable Redis retried; plus a real-Redis adapter test of the Lua scripts (skipped when no Redis answers)
- [x] docs listed in the Code Map -- document `bots:owner:{bot_id}` and the refusal

**Acceptance Criteria:**
- Given a bot_id leased by a live process of a different config, when `python3 -m bots` starts, then it exits non-zero before the node runs, logging both holders.
- Given the full bots test suite, when run, then it passes with no new warnings.

## Spec Change Log

## Review Triage Log

### 2026-10-07 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 9 (high 1, medium 3, low 5)
- defer: 0
- reject: 5
- addressed_findings:
  - `[high]` `[patch]` SIGTERM/SIGINT during the claim wait was swallowed by the node's signal handler and the node started anyway; the claim now runs in `main` on the loop the node later adopts, before `build_node` (no handlers yet, a refused start builds no strategy), with Ctrl-C cancelling the claim and releasing its leases.
  - `[medium]` `[patch]` `claim_all` kept its contested deadline across a Redis outage (could refuse on the first round back); the deadline now resets on any failed round.
  - `[medium]` `[patch]` a hung (unanswering) Redis could hang startup silently or wedge shutdown; claim rounds and the release are bounded by one TTL (`asyncio.wait_for`) and ledgered.
  - `[medium]` `[patch]` Known limit extended: a Redis restart (all leases vanish) or a >15 s blocked loop lets a starting duplicate win, since it retries faster than the heartbeat re-claims; upgrade path named.
  - `[low]` `[patch]` a holder expiring between the supervisor's failed `hold` and its `get` logged a false loss "(None)" then a "regained"; now no transition is recorded.
  - `[low]` `[patch]` leases were not released when `build_node` raised or a history drain failed; release now runs on both paths (before `node.dispose()` closes the loop).
  - `[low]` `[patch]` the refusal comment/docs claimed compose keeps re-refusing; corrected to `on-failure:5` leaving the whole fleet down, plus the blocked-loop cause in README.
  - `[low]` `[patch]` two over-100-char docstring lines.
  - `[low]` `[patch]` tests added: outage mid-contest restarts the wait, holder freed at refusal is claimed, freed-between-hold-and-get is no takeover.

### 2026-10-07 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 12 (high 0, medium 2, low 10)
- defer: 0
- reject: 6
- addressed_findings:
  - `[medium]` `[patch]` leases could lapse between the claim and the supervisors' first renewal (building 40 strategies + node startup can outlast the 15 s TTL), letting a duplicate starter claim them unrefused; new `ownership.reconfirm` renews every lease once right after `build_node`, before any bot task exists -- refusing at once (no wait: the node now owns the signal handlers) when one was taken, ledgering and going on when Redis fails.
  - `[medium]` `[patch]` a claim round was bounded by a whole TTL, so a slow round could let the leases it renews expire; rounds (and the refusal's holder read) are now bounded by one heartbeat (TTL / 3).
  - `[low]` `[patch]` the refusal's holder read could hang on an unanswering Redis, unledgered; it is bounded and a timeout restarts the wait (ledgered `bots.redis`).
  - `[low]` `[patch]` a `SqliteFillsStore` open failure after the claim left every lease standing; `__main__._host` releases them on that path.
  - `[low]` `[patch]` two processes of overlapping bot sets starting at the same instant can split the leases and both be refused: documented as a `Known limit:` on `claim_all` with the all-or-nothing-script upgrade path.
  - `[low]` `[patch]` `_stop`'s early return (loop closed or running) skipped the release silently; it is now ledgered (`bots.ownership`).
  - `[low]` `[patch]` the "node adopted the claim's loop" invariant was an `assert` (stripped under `-O`); now an explicit `RuntimeError`.
  - `[low]` `[patch]` `Supervisor(owner_ttl_seconds=...)` was never passed and was a second source of truth for the TTL; removed.
  - `[low]` `[patch]` `BOT_OPERATIONS.md` promised an immediate restart after any clean stop; now names the Docker grace-period SIGKILL / unanswering Redis cases that wait like a crash restart.
  - `[low]` `[patch]` the DEPLOY_CHECKLIST lease-count check hard-coded 40; it now derives the count from `config.toml`.
  - `[low]` `[patch]` the supervisor's runtime-loss `Known limit:` now names a stalled heartbeat tick, not only a blocked loop.
  - `[low]` `[patch]` tests: the outage-mid-contest test no longer relies on exact float equality; added hung-round, hung-holder-read, the four `reconfirm` cases, and a real-Redis PX-expiry contract test.

### 2026-10-07 — Review pass (second follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 6 (high 0, medium 1, low 5)
- defer: 0
- reject: 15
- addressed_findings:
  - `[medium]` `[patch]` `docker stop` during the claim wait was ignored: `live-paper` runs Python as PID 1 (no `init`, no tini), and the kernel never delivers a default-action SIGTERM to PID 1, so the stop waited out the grace period and SIGKILL; `_claim` now installs a SIGTERM handler on the loop that cancels the claim, releases its leases and exits 143, removed again before the node installs its own (new test sends a real SIGTERM).
  - `[low]` `[patch]` `release_all` was bounded by one TTL (15 s), longer than Docker's 10 s stop grace, so its timeout could never be ledgered in the container; now bounded by one heartbeat.
  - `[low]` `[patch]` `main` left the event loop unclosed on every way out before a node existed (Ctrl-C during the claim, fills store open failure); a `finally` closes it when `node.dispose()` did not.
  - `[low]` `[patch]` `main`'s comment claimed a refused start never builds a strategy; corrected to name `reconfirm`'s post-build refusal.
  - `[low]` `[patch]` a >100-char line in the supervisor's Known limit docstring rewrapped.
  - `[low]` `[patch]` the refusal-timing test had only a lower bound; it now also asserts the refusal lands on the first retry past the window.

## Design Notes

The lease, not `bots:status`, carries presence because pub/sub has no state. Waiting one TTL before refusing is what makes a crash-restart self-heal while a live duplicate (which refreshes every 5 s) is still refused. The instance token makes the value unique per process life, so a same-config second process is refused too.

## Verification

**Commands:**
- `cd platform && python3 -m pytest bots/tests -q -p no:cacheprovider --deselect <TLS node test>` (throwaway redis on 6379) -- expected: all pass
- `ruff check platform/bots && ruff format --check platform/bots && mypy platform/bots` -- expected: clean

## Auto Run Result

**Summary:** Second follow-up review pass of DW-78 (`bots:owner:{bot_id}` ownership lease; `python3 -m bots` refuses to start while another live process owns a hosted bot_id). The main fix: `docker stop` during the startup claim wait now actually stops the process, since Python is PID 1 in the `live-paper` container and never received the default-action SIGTERM.

**Files changed (this pass):**
- `platform/bots/__main__.py` -- SIGTERM handler around the claim (cancel, release, exit 143); event loop closed on every pre-node exit; comment corrected
- `platform/bots/application/ownership.py` -- `release_all` bounded by one heartbeat (inside Docker's stop grace)
- `platform/bots/application/supervise.py` -- Known limit docstring rewrapped to 100 chars
- `platform/bots/tests/test_ownership.py` -- real-SIGTERM claim test; refusal-timing upper bound

**Review:** 6 patches applied (1 medium, 5 low), 0 deferred, 15 rejected. Rejected: a second Ctrl-C during `_stop`'s gather (tasks are already cancelled); 1 Hz ledgering during a startup outage, "regained" in the error ledger and no `__main__` composition tests (all rejected in earlier passes); an unrecorded holder change while the lease is lost; the supervisor TTL derived from its own heartbeat (production never overrides it); `claimed_at`/`host` wording; the reconfirm→first-renewal gap (covered by the supervisor's blocked-loop Known limit); the DEPLOY_CHECKLIST "commit: this change's" (the file's convention); SIGTERM during `build_node`/`reconfirm` (the node's handler queues `stop()` exactly as it did before this change for a signal between build and run); `reconfirm` going on after a Redis failure (by design, documented); per-bot refusal deadline; non-transient `ResponseError`s retried; a `hold` error stopping status publishing; `seed()` before the first hold (all covered by earlier rejections or the Redis-down Known limit).

**Verification:** `python3 -m pytest bots/tests bot_tui/tests tests/test_boundaries.py -v -W error::DeprecationWarning -k "not test_build_node_passes_redis_credentials_and_ssl_from_url"` (from `platform/`, throwaway redis:8-alpine on 6379; the excluded TLS node test hangs without a TLS Redis) -- 817 passed, 1 deselected, no warnings; `ruff check` + `ruff format --check` + `mypy` clean on the four changed files.

**Residual risks:** unchanged from the prior pass -- simultaneous starts of overlapping fleets refuse both (documented Known limit); a node startup that blocks the loop past one TTL after `reconfirm` is covered only by the supervisor's Known limit; no two-container end-to-end run was performed.
