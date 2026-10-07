---
title: 'DW-223/225: bots realized PnL from Nautilus PositionClosed events'
type: 'bugfix'
created: '2026-10-07'
status: 'done'
baseline_revision: '0d7f75dbd771ee7b116b602c7da7cc36a0741745'
final_revision: '618c81ad6346cee99f0b7752deb5d518b933d0de'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** `FillLedger` estimates each reducing fill's realized PnL in an in-memory `_pending_realized_pnl` dict, so a close after a process restart records `total - 0` over estimates already in `fills.db` (double count), and the `fill.order_side == position.entry` short-circuit drops a flipped position's closed leg from PnL and win rate (DW-223). `bots:status.realized_pnl` sums `cache.positions_closed()`, which keeps only a NETTING position's latest round trip, so it disagrees with `bots:history` (DW-225).

**Approach:** Record one row per Nautilus `PositionClosed` event, live as it fires on `events.position.{strategy_id}`, into a new append-only `position_closes` table in `fills.db` (the event's own `realized_pnl`, never a Cache read-back). Fills become plain fill rows with no PnL. Every realized-PnL figure (round-trip stats, win rate, daily PnL, the trades blotter's PnL column, and `bots:status.realized_pnl`) reads `position_closes`.

## Boundaries & Constraints

**Always:**
- Realized PnL comes only from `PositionClosed.realized_pnl` as delivered to the subscriber. Never read a closed position back from the Cache: in a flip, the engine has already overwritten the NETTING id with the new position before it publishes the events.
- A close is idempotent on `(bot_id, position_id, ts_closed)`. `ON CONFLICT DO NOTHING` (not `INSERT OR IGNORE`), and a re-delivery is logged at WARNING, as fills are.
- A close write happens off the event loop on the executor, like fill writes. `drain()` awaits it, and a failure is ledgered (`bots.fill_lost`-style, with every field), never raised into the msgbus dispatch.
- Link the close to its closing fill's `trade_id` by using the `OrderFilled` that the engine published immediately before it, on the same synchronous dispatch (same `position_id`, `ts_event == ts_closed`). If no fill matches, store `trade_id` NULL, still count the PnL, and record an `error_ledger` entry. Never drop the close.
- Migrate legacy rows once: when `position_closes` is first created, copy each existing `fills` row with non-NULL `position_realized_pnl` into it (`position_id = 'legacy-' || rowid`, `ts_closed = ts`, `trade_id = trade_id`). The legacy `fills.realized_pnl`/`position_realized_pnl` columns stay but are never written again.
- Keep the `bots:status` and `bots:history` field order frozen. Only value sources change.

**Block If:** the change would require editing `nautilus_trader/` or `crates/`.

**Never:** edit the deferred-work ledger; keep any per-fill PnL estimate or other cross-event PnL state in memory; read `cache.positions_closed()` anywhere in `bots/`; rewrite or delete an existing `fills` row.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Multi-fill close | open 0.002, reduce 0.001 twice | 3 fill rows with NULL PnL except the closing fill, which shows the round-trip total; 1 close; total == PositionClosed.realized_pnl | — |
| Restart mid-position | open + partial reduce recorded by publisher A; close recorded by a fresh publisher B on the same store | exactly one close, equal to the event's total (no double count) | — |
| Flip | long 0.001, SELL 0.002, BUY 0.001 | 2 closes (long leg, short leg); the flip fill's trade row shows the long leg's PnL; closed_trades == 2 | — |
| Re-delivered close | same (bot, position_id, ts_closed) twice | stored once, WARNING logged | no ledger entry |
| Unlinked close | PositionClosed with no matching prior fill | close stored with trade_id NULL, PnL counted | `error_ledger` entry |
| Status after NETTING reopen | several round trips, Cache closed list empty | status.realized_pnl == sum of history's per-trip PnL | — |

</intent-contract>

## Code Map

- `platform/bots/domain/fill_ledger.py` -- `FillLedger`/`FillRecord`, the module docstring describing the Cache overwrite.
- `platform/bots/infrastructure/fills_store.py` -- `SqliteFillsStore`: schema, migrations, all PnL queries.
- `platform/bots/application/ports.py` -- `BotRuntime.on_fill`, `FillsStore` protocol, `PositionSnapshot.realized_pnl`.
- `platform/bots/application/history.py` -- `HistoryPublisher.attach/record_fill/_write/drain`.
- `platform/bots/application/supervise.py` -- `FillStats`, `read_fill_stats`, `build_status`.
- `platform/bots/infrastructure/cache_reader.py` -- `positions()` sums `positions_closed`; `on_fill` subscribes `events.order.*`.
- `platform/bots/tests/{support.py,test_fills_store.py,test_trade_history.py,test_bot_status.py,test_ownership.py,test_ports.py,test_domain.py}` -- fakes/helpers touching the changed APIs.
- `platform/bot_tui/bots_pane.py:405` -- the blotter's "blank PnL for a non-closing fill" contract (unchanged semantics).

## Tasks & Acceptance

**Execution:**
- [x] `platform/bots/domain/fill_ledger.py` -- drop the PnL fields from `FillRecord`. Add a frozen `PositionCloseRecord(bot_id, position_id, ts_closed, realized_pnl, trade_id | None)`. Rewrite `FillLedger` with `record_fill(fill) -> FillRecord`, which remembers only the last fill, and `record_close(event: PositionClosed) -> PositionCloseRecord`, which links the trade id. Remove `_pending_realized_pnl`, then update the docstrings.
- [x] `platform/bots/infrastructure/fills_store.py` -- add the `position_closes` table with a unique `(bot_id, position_id, ts_closed)` key, plus the one-time legacy copy guarded by table existence. Add `write_position_close`. Re-source the PnL queries from `position_closes`: `position_realized_pnls`, `win_rate_stats` and `pnl_by_day` (by `ts_closed`). Add a new `total_realized_pnl(bot_id) -> float`. `recent_trades` LEFT JOINs closes on `(bot_id, trade_id)`, using `COALESCE(close pnl, legacy position_realized_pnl)`. Remove `realized_pnls` (per-fill shares no longer exist).
- [x] `platform/bots/application/ports.py` -- replace the `on_fill` handler type with `Callable[[OrderFilled], None]` and add `on_position_closed(handler: Callable[[PositionClosed], None])`. Update `FillsStore` to match the store, and remove `realized_pnl` from `PositionSnapshot`.
- [x] `platform/bots/infrastructure/cache_reader.py` -- update `on_fill` to stop reading the Cache position, and add `on_position_closed`, which subscribes to `events.position.{strategy_id}` and filters to `PositionClosed`. Make `positions()` stop reading `positions_closed`.
- [x] `platform/bots/application/history.py` -- attach both handlers. `record_close` mirrors `record_fill`'s attribute-then-executor-write, with ledgering, drain tracking and the duplicate WARNING.
- [x] `platform/bots/application/supervise.py` -- add `realized_pnl` to `FillStats` from `total_realized_pnl`, and make `build_status` publish it. Document the semantics: closed round trips only, consistent with `bots:history`.
- [x] `platform/bots/__init__.py` + tests above -- update the docstrings and fakes, and rewrite the multi-fill test to the new semantics. Add tests for the restart (replay real captured events into two publishers), the flip, the re-delivered close, the unlinked close, the legacy migration, and status == history total.

**Acceptance Criteria:**
- Given the DummyStrategy oscillating run, when the status is built, then `status["realized_pnl"] == sum(store.position_realized_pnls(bot, None))`, and `bots/` contains no `positions_closed` call.
- Given a pre-existing `fills.db` with legacy closing rows, when the store opens, then `win_rate_stats`/`position_realized_pnls` return the same values as before the migration, and reopening does not copy them again.

## Design Notes

Engine ordering (`nautilus_trader/execution/engine.pyx` `_handle_order_fill`): the position update runs first, then the `OrderFilled` is published, then the pending position events. In a flip, `_flip_position` closes the original (`PositionClosed`, whose `realized_pnl` covers the closed leg) and then `_open_position` overwrites the NETTING id in the Cache, all before either publish. So the event is the only correct source of the closed leg's PnL, and the last fill seen on the order topic is that close's fill.

Daily PnL is now attributed to the UTC day the round trip closed. Before, a partial reduction counted on its own day. This is a deliberate consequence of per-close attribution.

## Verification

**Commands:**
- `cd platform && python3 -m pytest bots bot_tui -q -p no:cacheprovider` -- expected: all pass (a throwaway redis on 6379 if the bots tests need it)
- `cd platform && ruff check bots && ruff format --check bots && mypy bots` -- expected: clean


## Review Triage Log

### 2026-10-07 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 6: (high 0, medium 1, low 5)
- defer: 0
- reject: 11: (high 0, medium 0, low 11)
- addressed_findings:
  - `[medium]` `[patch]` A close stored unlinked in one process life and re-delivered linked in a later one (or the reverse) went into different partial unique indexes, so it was double counted. `write_position_close` now also refuses a close whose `(bot_id, position_id, ts_closed)` is already stored when either side is unlinked; two linked closes with distinct trade ids stay distinct. Covered by a new test in both directions.
  - `[low]` `[patch]` A rollback followed by a re-deploy never copies the closes the older image wrote. Added as `Known limit:` (5) on `_LEGACY_CLOSES_COPY`, with a no-rollback warning and a recovery note in the DW-223 DEPLOY_CHECKLIST entry.
  - `[low]` `[patch]` Rows written before `position_realized_pnl` existed (per-fill PnL only) leave `pnl_by_day`/equity. Added as `Known limit:` (4), and noted in the checklist.
  - `[low]` `[patch]` Docstrings overclaimed. Two now name their exceptions: the store docstring says the blotter sums short when a close is unlinked, and `build_status` no longer says "byte-identical" (realized_pnl is now all-time). The checklist states the per-run to all-time change for the TUI total.
  - `[low]` `[patch]` `test_ports.py` named the wrong close key.
  - `[low]` `[patch]` A `bots/__init__.py` docstring line was 122 characters.

## Auto Run Result

Status: done

**Summary:** This was a follow-up review of the DW-223/225 change, which sources realized PnL from Nautilus `PositionClosed` events in the `position_closes` table of `fills.db`. The review found one real data-integrity hole. A close re-delivered with a different link state than when it was stored, linked versus unlinked, escaped both partial unique indexes and counted twice. That is now closed. The rest of the patches correct or extend the documented limits.

**Files changed (platform/):**
- `bots/infrastructure/fills_store.py`: `write_position_close` gains a cross-key `NOT EXISTS` check. The `_POSITION_CLOSES_DDL` comment explains it, `_LEGACY_CLOSES_COPY` gains `Known limit:` points (4) for pre-column rows and (5) for rollback, and the module docstring names the unlinked-close blotter exception.
- `bots/application/supervise.py`: the `build_status` docstring no longer claims byte-identical values and states the all-time `realized_pnl`.
- `bots/__init__.py`: the over-long docstring line is wrapped.
- `bots/tests/test_fills_store.py`: new `test_a_close_re_delivered_with_the_other_link_state_is_stored_once`.
- `bots/tests/test_ports.py`: the close-key comment is corrected.
- `docs/DEPLOY_CHECKLIST.md`: the DW-223 entry now notes the all-time PnL change and the pre-column rows, and gains a no-rollback item.

**Review:** 6 patches applied (1 medium, 5 low), 0 deferred, 11 rejected:
- Status fields read in separate queries: transient.
- Fill and close executor ordering: transient.
- A close linked to a fill whose write failed: already ledgered as `bots.fill_lost`.
- The 16-fill lookback wording.
- Inferred-fill closes: an existing `Known limit:`.
- The migration's busy timeout: one-time, and the store retries the open on its next call.
- A weak-assertion claim: line 656 already pins it.
- The test fake's return type.
- The checklist's "commit: this change's": the file's convention.
- A re-delivery of a legacy close without a trade id: those pre-DW-224 closes cannot be re-delivered.
- The drain snapshot: not shown to be reachable.

**Verification:**
- `python3 -m pytest -o addopts="" bots bot_tui tests/test_boundaries.py --deselect platform/bots/tests/test_node.py::test_build_node_passes_redis_credentials_and_ssl_from_url` (throwaway redis on 6379): 837 passed, 1 deselected. The deselected test hangs doing TLS against a plain redis and is unrelated.
- Ruff check and format, using pre-commit's ruff 0.15.16, are clean on the touched files.
- `uv run mypy bots`: 41 errors both before and after this pass (checked by stashing the changes), so none are new.

**Residual risks:**
- Two linked closes with different trade ids at the same `(position_id, ts_closed)` are still kept apart by design, for dYdX same-block closes. An inferred-fill re-close under a synthesised trade id therefore remains the documented `Known limit:`.
- Legacy pre-column per-fill PnL and any rollback-window closes are not carried into `position_closes`. Both are documented in code and in the checklist.

**Follow-up review recommended:** false. There was one localized, tested change to the write's duplicate guard; everything else was documentation.
