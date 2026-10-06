---
title: 'DW-222/224/226/228/229/230: bots persistence and host hardening'
type: 'bugfix'
created: '2026-10-06'
status: 'done'
baseline_revision: 'a89bc6a54b0648571c5c4ce44a6c1610ee2f2a0c'
review_loop_iteration: 0
followup_review_recommended: false
final_revision: '67c0257c1bd07034bb715660b5228c6be835a13f'
context:
  - '{project-root}/platform/CLAUDE.md'
warnings: ['multiple-goals', 'oversized']
---

<intent-contract>

## Intent

**Problem:** Six defects in `platform/bots/`: `fills.db` reads block the TradingNode's one event loop (DW-222); `fills.db` has no idempotency key, so a re-delivered `OrderFilled` inflates stats forever (DW-224); `DummyStrategy` decides from account-wide portfolio positions, reading other bots' positions on the same instrument (DW-226, AD-11); an unreachable Redis at the incident-log seed later overwrites the prior life's `bots:incidents:*` (DW-228); the Nautilus Cache gets percent-encoded Redis credentials while redis-py decodes them (DW-229); an exception in `FillLedger.attribute` escapes into message-bus dispatch, the fill neither written nor ledgered (DW-230).

**Approach:** Run every `fills.db` read on the loop's default executor (Cache reads stay on the loop); add a `trade_id` column + unique `(bot_id, trade_id)` index and make duplicate inserts a no-op; read the strategy's own open position from the Cache; retry the seed's GET until it completes; `unquote` URL credentials/host; guard attribution and ledger its failure as `bots.fill_lost`.

## Boundaries & Constraints

**Always:** Nautilus Cache/strategy reads (`runtime.positions()`, `cache.*`) stay on the event-loop thread -- only `FillsStore` calls go to the executor. The published payloads (`bots:status`, `bots:history:*`, `bots:incidents:*`) stay byte-identical in shape and field order (`bots/tests/test_replay.py` fixture). Existing `fills.db` files migrate in place (legacy rows keep a NULL `trade_id`; SQLite unique indexes allow many NULLs). A dropped duplicate is logged at WARNING, never silent. Type hints, LGPL headers, ruff line length 100, functions < ~30 lines.

**Block If:** the unique key would drop a legitimate fill (trade ids not unique per venue across process restarts).

**Never:** edit `nautilus_trader/`, `crates/` or the deferred-work ledger; `INSERT OR IGNORE` (it also swallows NOT NULL/CHECK violations); write an unattributed fill row with fabricated/NULL PnL; read portfolio-wide position aggregates in `DummyStrategy`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Re-delivered fill | same `(bot_id, trade_id)` written twice | one row; `write_fill` returns False | WARNING log in `HistoryPublisher._write` |
| Same trade id, two bots | `(b1, T-1)`, `(b2, T-1)` | two rows | none |
| Legacy db | `fills` table without `trade_id` | column + index added on open; old rows readable | none |
| Seed, Redis down | GET raises twice then succeeds | prior log adopted, `process_start` at `bot.started_at`, written | each failure ledgered `bots.redis`, retry after `reconnect_seconds` |
| Seed, malformed prior | GET returns bad JSON / bad shape | starts from `[process_start]`, written | ledgered `bots.incidents_write` once |
| Attribution raises | `FillLedger.attribute` raises | nothing written, no exception out of `record_fill` | ledgered `bots.fill_lost` with the fill |
| Encoded credentials | `redis://us%40r:p%40ss@h:6379` | Cache `username="us@r"`, `password="p@ss"` | none |
| Two bots, one instrument | bot A long, bot B flat on same instrument | B's `_maybe_trade` sees itself flat | none |

</intent-contract>

## Code Map

- `platform/bots/infrastructure/fills_store.py` -- `_SCHEMA`, `_MIGRATIONS`, `write_fill`, the queries.
- `platform/bots/domain/fill_ledger.py` -- `FillRecord` (gains `trade_id`), `FillLedger.attribute`.
- `platform/bots/application/ports.py` -- `FillsStore` Protocol (`write_fill -> bool`).
- `platform/bots/application/history.py` -- `record_fill`, `_write`, `compute`, `refresh`.
- `platform/bots/application/supervise.py` -- `build_status`, `Supervisor.seed`, `heartbeat_tick`.
- `platform/bots/strategies/dummy.py` -- `_maybe_trade`, `_wanted_side`, `_flatten` portfolio reads.
- `platform/bots/infrastructure/nautilus_host.py` -- `_cache_config`.
- `platform/bots/tests/{support,test_fills_store,test_ports,test_bot_status,test_trade_history,test_node,test_strategy}.py` -- tests.
- `platform/docs/DATABASE_SETUP.md` §2.2 -- the `fills` table's documented columns.
- `nautilus_trader/backtest/engine.pyx` `_generate_trade_id_str` -- Sandbox trade id = FNV hash of `(venue, raw_id, ts_init)` + counter: unique across restarts (Block-If check passes).

## Tasks & Acceptance

**Execution:**
- [x] `platform/bots/domain/fill_ledger.py` -- add `trade_id: str` (last field) to `FillRecord`, set from `fill.trade_id.value` in `attribute` -- idempotency key (DW-224).
- [x] `platform/bots/infrastructure/fills_store.py` -- migration `ALTER TABLE fills ADD COLUMN trade_id TEXT`, then (outside the suppressed loop) `CREATE UNIQUE INDEX IF NOT EXISTS idx_bot_trade ON fills(bot_id, trade_id)`; `write_fill` inserts `trade_id` with `ON CONFLICT(bot_id, trade_id) DO NOTHING` and returns whether a row was inserted; docstrings name the key (DW-224).
- [x] `platform/bots/application/ports.py` -- `FillsStore.write_fill -> bool` documented (False = already stored).
- [x] `platform/bots/application/history.py` -- `record_fill` guards `attribute` (ledger `bots.fill_lost` with the fill, return) (DW-230); `_write` WARNs on a duplicate (DW-224); `compute` takes an optional precomputed `all_time_pnl_by_day`, a new `compute_all(now_ns)` reads it once for the four ranges; `refresh` runs `compute_all` via `run_in_executor` (DW-222); module docstring `Known limit:` that the full-history scans still grow with the file, off the loop, upgrade path a per-day rollup table.
- [x] `platform/bots/application/supervise.py` -- `FillStats` dataclass + `read_fill_stats(fills, bot_id)`; `build_status(bot, runtime, stats, now)`; `heartbeat_tick` reads stats via `run_in_executor` inside the existing `bots.status_build` guard (DW-222); `seed` retries only the GET (ledger `bots.redis`, sleep `reconnect_seconds`) until it completes, adopts at `bot.started_at`, falls back to an empty log only for unusable content, then writes (DW-228).
- [x] `platform/bots/strategies/dummy.py` -- one `_own_position()` (`cache.positions_open(instrument_id=, strategy_id=self.id)`, NETTING: at most one) replaces `portfolio.is_flat/is_net_long/is_net_short/net_position`; `_flatten` sizes from `position.quantity` (DW-226).
- [x] `platform/bots/infrastructure/nautilus_host.py` -- `_cache_config` passes `unquote`d host/username/password, matching redis-py `parse_url` (DW-229).
- [x] `platform/bots/tests/*` -- `support.write_fill` gains a unique default `trade_id`; `status_of` uses `read_fill_stats`; new tests for every I/O-matrix row; update existing `FillRecord(...)` constructions.
- [x] `platform/docs/DATABASE_SETUP.md` -- §2.2 table columns + idempotency key.

**Acceptance Criteria:**
- Given a running loop, when `refresh`/`heartbeat_tick` run, then every `FillsStore` read executes off the loop thread (test asserts the reading thread differs from the loop's).
- Given the existing replay fixture, when `bots/tests/test_replay.py` runs, then payloads are byte-identical.
- Given the bots suite, when run, then all tests pass with no new warnings.

## Design Notes

- **Prior attempt (2026-10-06):** the first dev session was cut off by a host power-off at 01:01 UTC with its work uncommitted. That work (tracked + untracked, this spec included) is pinned on branch `dw2-bots-persistence-and-host-prior-attempt`. Start from it: `git read-tree -m -u HEAD dw2-bots-persistence-and-host-prior-attempt && git reset -q`, restamp `baseline_revision` with `git rev-parse HEAD`, then re-verify every task and AC rather than trusting the checkboxes.

Why no in-memory dedup in `FillLedger`: within one process the ExecutionEngine already rejects a duplicate `trade_id` before publishing (`nautilus_trader/execution/engine.pyx`, `is_duplicate_fill_c`); only a cross-life re-delivery can reach the store, where the ledger starts empty -- so the store key alone makes it idempotent.

Seed: only the GET is retried (a retry after `Bot.start` would raise "already started"). Supervision cannot publish anything without Redis, so waiting costs nothing; the strategy and fill recording run independently.

## Verification

**Commands:**
- `cd platform && timeout -s KILL 600 python3 -m pytest -o addopts="" --rootdir=. bots/tests -q -o faulthandler_timeout=240 --deselect bots/tests/test_node.py::test_build_node_passes_redis_credentials_and_ssl_from_url` (throwaway redis on 6379) -- expected: all pass.
- `ruff check` / `ruff format --check` / `mypy` on changed files (scratch venv `ruff==0.15.16`, `mypy==1.20.2`) -- expected: no new findings vs HEAD.

## Spec Change Log

- none

## Review Triage Log

### 2026-10-06 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 1: (high 0, medium 0, low 1)
- defer: 0
- reject: 17: (high 0, medium 3, low 14)
- addressed_findings:
  - `[low]` `[patch]` Seed overwrote an unusable prior incident log with nothing kept: the `bots.incidents_write` ledger detail now carries the replaced raw value (`supervise.py` `seed`), asserted in `test_an_unreadable_prior_log_starts_an_empty_one_that_still_marks_the_start`.

Rejected, with reasons (medium first): seed retrying forever / ledger every `reconnect_seconds` / WRONGTYPE -- the key is only ever SET by this code, nothing can publish without Redis, and the run loop already ledgers at the same cadence; executor shutdown racing heartbeat/refresh reads -- `bots/__main__.py` `_stop` cancels and awaits every bot task and drains writes before `node.dispose()` shuts the executor down; reduce-only flatten vs a venue's account-wide net -- paper NETTING positions are per strategy (`{instrument}-{strategy_id}`), and the one live `ExecBot` hosts a single bot. Low: HEDGING / several open positions (every venue client and Sandbox are NETTING and `oms_type` is a reserved param); duplicate fill mutating `FillLedger` pending PnL (a cross-life re-delivery restores the prior life's pending share, keeping the round-trip sum correct; in-process duplicates are refused by the ExecutionEngine); `trade_id` nullable (`TradeId` cannot be empty, `FillRecord.trade_id: str`); `(bot_id, trade_id)` omitting venue/instrument (key fixed by the intent contract, Block-If check passed); self-matched orders sharing a trade id (venues apply self-trade prevention; Sandbox matches against market data only); legacy NULL row re-delivered across the upgrade (one-off transition); `compute_all`/status reads not one snapshot (pre-existing per-query reads, self-heals next cycle); malformed-log ledger category (spec matrix fixes it); EXTERNAL reconciled position (Cache is Redis-persisted); test weaknesses (payload equality covered by `test_replay.py`; the reconnect fixture counts connections deliberately and documents it); failed `ALTER` masked (then `CREATE UNIQUE INDEX` fails loudly and the store refuses to open).


### 2026-10-06 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 3: (high 0, medium 0, low 3)
- defer: 0
- reject: 16: (high 0, medium 3, low 13)
- addressed_findings:
  - `[low]` `[patch]` Reconciliation-inferred fills carry a synthesised trade id (`nautilus_trader/live/reconciliation.py` `create_inferred_order_filled_event`), so the `(bot_id, trade_id)` key cannot dedupe them against an already-stored row: documented as a `Known limit:` with upgrade path in `fills_store.py` beside `_UNIQUE_TRADE_INDEX`.
  - `[low]` `[patch]` The heartbeat's `win_rate_stats` full-history scan had no `Known limit:` (only the refresh did): added to `supervise.py` `read_fill_stats`.
  - `[low]` `[patch]` `Supervisor.seed` docstring line at 135 columns reflowed to the 100-column limit.

Rejected, with reasons (medium first): seed retrying forever on a permanent error (WRONGPASS/WRONGTYPE) -- as the prior pass held: the key is only ever SET by this code, a wrong password also breaks every publish, and the retry ledgers each failure; executor reads outliving shutdown / `run_in_executor` after dispose -- `_stop` cancels and awaits bot tasks and drains writes before `node.dispose()`, as the prior pass verified; reduce-only flatten against a venue's account-wide net -- paper positions are per strategy and the one live `ExecBot` hosts a single bot. Low: nullable `trade_id` column, legacy-NULL re-delivery, status/refresh reads not one snapshot, HEDGING, stale flatten quantity, `FillLedger` pending mutation on a failed/duplicate attribution, malformed-log ledger site and size, duplicate WARN not ledgered (spec fixes WARNING), test-depth gaps -- all re-raised from the prior pass, reasons there unchanged.

## Auto Run Result

**Summary:** Follow-up review of the done bundle (DW-222/224/226/228/229/230, final_revision 58d96a77bf). Fresh Blind Hunter + Edge Case Hunter pass over `a89bc6a54b..58d96a77bf` (`platform/`): no defect in behaviour; three documentation patches applied.

**Files changed (this pass):**
- `platform/bots/infrastructure/fills_store.py` -- `Known limit:` for reconciliation-inferred trade ids.
- `platform/bots/application/supervise.py` -- `Known limit:` on the heartbeat's full-history scan; docstring reflowed to 100 columns.

**Review:** 3 patches applied (low), 0 deferred, 16 rejected (see triage log).

**Verification:** `python3 -m pytest bots/tests` (throwaway redis:7-alpine on 6379, TLS node test deselected): `310 passed, 1 deselected`. ruff 0.15.16 check + format clean on both changed files.

**Residual risks:** unchanged from the first run -- the full-history scans grow with `fills.db` (now documented on both the refresh and heartbeat paths); a legacy NULL-`trade_id` row re-delivered across the upgrade is stored again (one-off); inferred reconciliation fills are not deduplicated (documented `Known limit:`).
