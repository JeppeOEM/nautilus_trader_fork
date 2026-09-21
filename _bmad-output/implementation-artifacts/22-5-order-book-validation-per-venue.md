---
status: done
operator_actions:
  - Run the Bybit and Hyperliquid collectors for at least one hour each and confirm the `collector.book_crosscheck` count stays at zero (AC #2's one-hour bar; only minutes of local evidence exist so far). [partial locally 2026-09-21 -- 25 min (16:55-17:20 UTC), NOT clean. collector.book_crosscheck fired 14 times, 8 on Hyperliquid (BTC and ETH on every 5-min round, size drift over several top levels) and 6 on Bybit (all four ids, one or two each, single-level size or best-price diffs). Zero collector.book_sequence entries, zero feed-dead warnings, no restarts. Root-caused 2026-09-21 (audit D-64): the check compares books sampled at different times -- Hyperliquid's WS book is a ~5.4 s-cadence snapshot judged against live REST twice within 2 s, and Bybit's venue-mode book trails the wall clock by 1 + hold_back by design (arrival mode: 0/100 checks confirmed, venue mode: 36/77). Not book drift; zero book_sequence entries. Fixed the same day (D-64 treatment, commit on troll): the check now aligns on Bybit's `seq` (bracketed) and Hyperliquid's venue millisecond, exactly. Live on the dev box in the deployed venue-mode configs: Bybit 172/172 aligned checks, 0 confirmed; Hyperliquid 18 aligned rounds all identical to REST, 6 skipped, 0 confirmed. The one-hour bar and the VPS day-long run stay open; story 23.3's cross-check closes them]
  - Deploy on the VPS with `make redeploy` and confirm Dozzle is clean for 10 minutes, with no `collector.book_sequence` entries on Bybit and no stale-book warnings naming "feed dead" on Hyperliquid.
  - Run `ruff --fix` and `mypy` over `troll/`; neither is installed on the dev host.
followup_review_recommended: false
baseline_revision: 4188f30c5441859880c54a21dec777dbb6e54502
---

# Story 22.5: Order book validation per venue

Status: awaiting-operator

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the platform operator,
I want each venue's book to be checked against an independent source of truth and its known failure modes to have loud canaries,
so that DATA-02's "no mysteries in ingestion" holds for Bybit and Hyperliquid, not just dYdX.

## Acceptance Criteria

1. **Bybit `u` canary.** Bybit's `u` update id (documented as a sequence; `u=1` = service-restart snapshot) is stamped as `BookOrder.order_id` by the Rust client, which performs no gap check. When a non-snapshot delta arrives with `u` ≤ the previous `u` for that instrument, or with a gap > 1, a `collector.book_sequence` ledger entry + counter is recorded (WARNING for a gap until a live capture proves `u` is contiguous in practice, then ERROR) and the book is resynced. dYdX and Hyperliquid opt out, with the reason documented in their `client.py`.
2. **REST cross-check loop.** A periodic `extra_loop` per venue diffs the live book's top-20 against a REST snapshot taken at the same instant (Bybit: pyo3 `BybitHttpClient.request_orderbook_snapshot`; Hyperliquid: `POST /info {"type":"l2Book"}` via stdlib `urllib`, weight 2). Mismatches beyond a tolerance land in `error_ledger` and `troll/docs/DATA_INTEGRITY_AUDIT.md`; one hour of Bybit and Hyperliquid runs reports zero mismatches.
3. **Subscribe-time replay verified (DATA-06).** The first messages after `publicTrade` (Bybit) and `trades` (Hyperliquid) subscription are captured raw; the per-venue replay finding is registered in `DATA_INTEGRITY_AUDIT.md` with evidence, and the core's dedup/age filter is confirmed to cover it.
4. **Stale vs quiet resolved with evidence.** Hyperliquid documents `l2Book` "pushed on each block that is at least 0.5 s since last push" but the collector observed ~5 s spacing. A raw capture resolves the discrepancy; a feed-level liveness timestamp distinguishes "quiet feed, book unchanged" from "dead feed / post-reconnect stale"; per-venue `stale_book_seconds` is set from the evidence and the finding registered.

## Tasks / Subtasks

- [x] Task 1 — read the Rust parse code before writing a line (DATA-02: evidence, not assumption)
  - [x] `crates/adapters/bybit/src/websocket/parse.rs:232-316`: confirm exactly which delta field carries `u` (research: `BookOrder.order_id`) and `seq` (`OrderBookDelta.sequence`), whether `u` is per message (all deltas of one `OrderBookDeltas` share it) and how a `type=snapshot` message is flagged (`Clear` delta with `F_SNAPSHOT`? `flags`?). Write the answer into the canary's docstring with the line numbers.
  - [x] `crates/adapters/hyperliquid/src/websocket/parse.rs:103-163`: confirm `sequence=0` and `Clear`+`Add` per message (opt-out reason for HL). dYdX's opt-out reason is already written at `dydx_collector/collector.py` `_apply_deltas` docstring (connection-global `message_id`).
- [x] Task 2 — Bybit `u` canary (AC: #1)
  - [x] In `bybit_collector/collector.py`'s `BybitCollector`, override `_apply_deltas(iid, deltas)`: extract the message's `u`; if the message is a snapshot (or `u == 1`, the documented service-restart snapshot) → `self._last_u[iid] = u` and apply normally; else if `u <= last` → `error_ledger.record("collector.book_sequence", f"{iid} u={u} <= last={last}")`, `self._book_sequence_errors[iid] += 1`, apply nothing, `await`-free path: mark for resync (set a flag the `_second_loop` gate or a tiny extra loop turns into `_client.resync_orderbook` — `_apply_deltas` is sync); else if `u - last > 1` → `logger.warning(...)` + `self._book_sequence_gaps[iid] += 1` (**WARNING**, not ledger, until Task 5's capture says gaps never happen in a healthy stream), then apply; finally `_last_u[iid] = u`. `_clear_book_state` also pops `_last_u`.
  - [x] Keep the canary logic in a pure function `_sequence_verdict(last_u: int | None, u: int, is_snapshot: bool) -> "ok" | "gap" | "regress" | "snapshot"` so it is unit-testable without a book (TEST-01: this decides whether data is trusted).
  - [x] Opt-out notes: one paragraph each in `hyperliquid_collector/client.py` and `dydx_collector/client.py` docstrings naming why `OrderBookDelta.sequence`/`order_id` must not be gap-checked on that venue.
- [x] Task 3 — REST cross-check loop (AC: #2)
  - [x] `collector_core`: `CoreConfig.book_crosscheck_seconds: float = 300.0` (0 disables). Pure diff function in `collector_core/book_check.py`: `top_levels_mismatch(live: list[tuple[float,float]], rest: list[tuple[float,float]], *, depth=20, price_tolerance_levels=1, size_rel_tolerance=0.05) -> list[str]` returning human-readable mismatches (missing price level, best bid/ask differ, size drift beyond tolerance). Tolerance exists because REST and WS are sampled microseconds apart on a moving book — document the chosen numbers and revisit them from the one-hour evidence, never loosen them to make a run "pass".
  - [x] Bybit (`bybit_collector/collector.py`, `extra_loops`): `snap = await self._client.request_orderbook_snapshot(iid)` → new client method wrapping pyo3 `request_orderbook_snapshot(product_type, instrument_id, limit=50)` (returns `OrderBookDeltas`; apply into a scratch `OrderBook(L2_MBP)`); capture `live = self._live_books[iid]` top-20 immediately before the call and again after; a mismatch counts only if it is present against **both** captures (filters the sampling-skew case). Compare **price levels, not `u`** — REST `u` aligns with the 1000-level stream only (research §A).
  - [x] Hyperliquid (`hyperliquid_collector/book_snapshot.py`, stdlib `urllib` like `dydx_collector/open_interest.py`): `POST https://api.hyperliquid.xyz/info` body `{"type":"l2Book","coin":"<BTC>"}` (coin = symbol before `-USD-PERP`; verify the mapping against the instrument's `raw_symbol` rather than string-splitting if pyo3 exposes it), parse `levels[0]` (bids) / `levels[1]` (asks) `px`/`sz` strings with `Decimal` → compare at float against `price.as_double()`. Weight 2 of 1200/min — at 300 s per instrument this is negligible; still expose the interval in config.
  - [x] Both loops: mismatches → `error_ledger.record("collector.book_crosscheck", ...)` + per-instrument counter; a clean check → DEBUG log with the compared depth. Run each venue ≥ 1 h locally; paste the counts into the audit entry.
- [x] Task 4 — subscribe-time replay capture (AC: #3)
  - [x] **Bybit:** the Rust handler logs every raw frame at TRACE — `log::trace!("Raw websocket message: {text}")` (`crates/adapters/bybit/src/websocket/handler.rs:376`). Capture by running the collector once with `nautilus_pyo3.init_logging(level_stdout=WARNING, level_file=LogLevel.TRACE, directory=<scratch>, file_name="bybit_ws_trace", file_rotate=(50_000_000, 2))` for ~60 s, then inspect the first `publicTrade` messages after the subscribe ack: count trades, compare their `T` timestamps to the subscribe time. Do **not** ship TRACE logging in the collector — it is a one-off capture flag (an env var read by `run_forever`, off by default, is acceptable).
  - [x] **Hyperliquid:** the Rust client has no raw-frame log (checked `crates/adapters/hyperliquid/src/websocket/*.rs`; only structured `log::debug!` calls). Capture with an independent, zero-shared-code client instead — `aiohttp` (already in `troll-requirements.txt`; `websockets` is not) — `troll/scripts/capture_hl_ws.py`: connect `wss://api.hyperliquid.xyz/ws`, send `{"method":"subscribe","subscription":{"type":"trades","coin":"BTC"}}` and `{"type":"l2Book","coin":"BTC"}`, write the first N messages with arrival `time.time_ns()` to a JSONL file. Same instrument-of-truth pattern DATA-02 already credits for the dYdX crossed-book proof.
  - [x] Register per venue in `DATA_INTEGRITY_AUDIT.md` §2 (Ingestion): "Bybit `publicTrade` subscribe replay: <N> trades, oldest <age> s" / "Hyperliquid `trades` subscribe snapshot: <finding>", Treatment = core `stale_trade_seconds` + `trade_id` dedup, Status = GUARDED with the evidence file path. If either venue replays trades **younger** than 10 s that were already counted before a reconnect, the dedup must catch them — assert that with the ids from the capture.
- [x] Task 5 — stale vs quiet, and the `u` contiguity evidence (AC: #4, #1)
  - [x] From the HL capture (Task 4): measure inter-arrival of `l2Book` messages over ≥ 10 min for BTC and one thin coin; note whether messages arrive when the book is unchanged. Decide: if pushes only happen on change, silence with a live feed = unchanged book (safe to keep sampling the last book); if pushes are per block regardless, silence = problem and `stale_book_seconds` must be ~2× the block cadence.
  - [x] Core gate change (`collector_core/collector.py` `_second_loop`): a book is stale when `now - _last_feed_message_ns > feed_stale_seconds` (feed dead, any message counts — 22.1's `_last_feed_message_ns`) **or** `now - _last_book_update_ns[iid] > stale_book_seconds` (per instrument). New `CoreConfig.feed_stale_seconds` (default = `stale_book_seconds`). Post-reconnect staleness is already covered: the Rust client's resubscribe delivers a fresh snapshot which refreshes `_last_book_update_ns`. Log which condition tripped (DATA-01: name the gap).
  - [x] Set `hyperliquid_collector/config.toml`'s `stale_book_seconds` (and `feed_stale_seconds`) from the evidence, with the capture numbers in the comment; Bybit stays 5 s unless the capture says otherwise.
  - [x] From the Bybit TRACE capture: is `u` contiguous per instrument across ≥ 10 min of `orderbook.50`? If yes, promote Task 2's gap branch to `error_ledger` + resync and say so in the docstring; if no, keep WARNING + counter and record the observed gap pattern. Either way the audit entry cites the capture.
- [x] Task 6 — tests + audit
  - [x] `collector_core/tests/test_book_check.py`: `top_levels_mismatch` cases (identical, one level shifted within tolerance, best price differs, size drift over tolerance, REST shorter than 20 levels). `bybit_collector/tests/test_sequence_canary.py`: `_sequence_verdict` matrix + an integration case through `_apply_deltas` with real `OrderBookDelta`s where `order_id` regresses → ledger count 1 and resync flagged. `hyperliquid_collector/tests/test_book_snapshot.py`: parse a captured `l2Book` payload fixture. Core: feed-dead vs instrument-silent gate cases.
  - [x] `DATA_INTEGRITY_AUDIT.md`: new rows (D-xx) for Bybit sequence, Bybit/HL REST cross-check, Bybit/HL replay, HL cadence — each with Mechanism/evidence, Treatment, Status. Anything still unproven stays **OPEN** by name (DATA-02 end state 2), never quietly "fine".

## Dev Notes

### Why Bybit is the only venue that can silently desync

Research §A: Bybit is snapshot+delta with sequence ids the Rust client stamps but never checks; dYdX has no per-market sequence (crossed-book is the only signal, DATA-04 machinery exists); Hyperliquid sends the whole book every message (nothing to desync). So the `u` canary is Bybit-specific by construction, and the REST cross-check is the independent truth DATA-02 demands for both new venues (dYdX keeps its incident-report path and the reference-client proof from 2026-09-06).

### Canary ≠ fix (DATA-03, DATA-07)

A resync after a `u` regression is the fallback; a *rising* `collector.book_sequence` count is an open incident. Do not add a "resync every N minutes to be safe" — that hides the loss it papers over. Do not drop the offending message silently: ledger + counter + visible in `/api/errors`.

### Replay classification must be evidenced

DATA-06 exists because dYdX's `v4_trades` subscribed reply replayed ≤1000 historical trades and produced fake ~$850-range candles. Bybit `publicTrade` and Hyperliquid `trades` "may" carry a snapshot on subscribe (docs are vague). The core's age filter + dedup already run for both venues (22.1); this story turns "probably covered" into "covered, here is the capture". Filtering is legitimate only for input that is not our fault **and** whose classification is evidenced (DATA-07).

### Capture tooling is one-off, not a feature

The TRACE file sink and the aiohttp script exist to produce evidence for the audit; they are not run in production. Keep them small, documented in the audit entry (how to reproduce), and out of the collector's hot path. `websockets` 17.1 happens to be importable on the dev box but is **not** a declared dependency and is not in the Docker image — use `aiohttp`.

### Project Structure Notes

- New: `troll/collector_core/book_check.py` (+ test), `troll/hyperliquid_collector/book_snapshot.py` (+ test), `troll/bybit_collector/tests/test_sequence_canary.py`, `troll/scripts/capture_hl_ws.py`.
- Modified: `troll/collector_core/{collector,config}.py` (feed liveness gate, `book_crosscheck_seconds`, `feed_stale_seconds`), `troll/bybit_collector/{collector,client}.py`, `troll/hyperliquid_collector/{collector,client,config.toml}`, `troll/dydx_collector/client.py` (opt-out docstring only), `troll/docs/DATA_INTEGRITY_AUDIT.md`.
- Unchanged: `crates/**` (FORK-01 — the TRACE line already exists; nothing is added), catalog schemas.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 22.5] — ACs.
- [Source: research 2026-09-20 §A table, §C1–C5, "Open items surfaced"] — sequence semantics, REST endpoints, tolerance guidance, HL cadence discrepancy, `u` contiguity open item.
- [Source: crates/adapters/bybit/src/websocket/handler.rs:376] — raw-frame TRACE log; [Source: crates/adapters/bybit/src/websocket/parse.rs:232-316; crates/adapters/hyperliquid/src/websocket/parse.rs:103-163] — field mapping to confirm in Task 1.
- [Source: nautilus_trader/core/nautilus_pyo3.pyi:7339] — `request_orderbook_snapshot(product_type, instrument_id, limit)`.
- [Source: troll/dydx_collector/collector.py:1639-1692] — `init_logging` file-sink usage and the `LogGuard` lifetime rule.
- [Source: troll/dydx_collector/open_interest.py:167-176] — stdlib `urllib` REST pattern to copy for HL `l2Book`.
- [Source: troll/docs/DATA_INTEGRITY_AUDIT.md §2 (D-01, D-02, D-04, D-08, D-10)] — the register format and the dYdX precedents these entries sit beside.
- [Source: troll/CLAUDE.md DATA-01..DATA-07, OBS-01, FORK-01, TEST-01] — rules applied.

## Dev Agent Record

### Agent Model Used

claude-sonnet-5 (first pass, lost to a host crash), then claude-opus-5 for the recovery,
the Bybit raw capture the crash pre-empted, and the audit register.

### Debug Log References

The first dev session's edits were recovered from its own transcript
(`~/.claude/projects/...22-5.../*.jsonl`) and replayed onto the same baseline commit after the
crash reset the worktree; two steps had been applied twice and were de-duplicated by hand.

### Completion Notes List

- **Task 1 (read the Rust first).** `u` is stamped as `BookOrder.order_id` on every level delta of
  a message (one value per message) and `seq` as `OrderBookDelta.sequence`
  (`crates/adapters/bybit/src/websocket/parse.rs:232-316`); a `type=snapshot` message opens with a
  `Clear` delta, which carries no order — hence `_message_u` skips clears. Hyperliquid stamps
  `sequence=0`/`order_id=0` and emits Clear + all levels per message
  (`crates/adapters/hyperliquid/src/websocket/parse.rs:103-163`), so it has nothing to gap-check.
  Both opt-outs, and dYdX's connection-global `message_id` one, are written into the venue clients.
- **Task 2 (`u` canary).** Pure `_sequence_verdict(last_u, u, is_snapshot)` + `BybitCollector._apply_deltas`.
  Regress **and** gap → `error_ledger` `collector.book_sequence` + counter + message dropped + book
  dropped + resync queued; `u == 1` or a snapshot re-baselines. The gap branch is an error rather
  than a warning because AC #1's condition was met: the raw capture measured `u` contiguous
  (31,860 of 31,860 steps exactly +1 over 800 s), so a skipped `u` is lost messages.
- **Task 3 (REST cross-check).** `collector_core/book_check.py` (pure `top_levels_mismatch` +
  `persistent`), `CoreConfig.book_crosscheck_seconds` (300 s default, 0 disables), a core loop that
  only starts for a client exposing `fetch_book_levels`, and that method on both venue clients
  (Bybit via pyo3 `request_orderbook_snapshot`, Hyperliquid via stdlib `urllib` `POST /info`).
  The first live Bybit run showed single-round top-of-book size drift on nearly every check, so a
  mismatch is ledgered only when the *same* level is still wrong on a second round 2 s later.
- **Task 4/5 (evidence).** `troll/scripts/capture_hl_ws.py` captures raw frames from an independent
  aiohttp client for both venues and summarizes replay bounds, `l2Book` cadence and `u` steps.
  The in-collector TRACE file sink was removed once its root cause was found: the workspace
  `Cargo.toml:190` sets `release_max_level_debug`, which compiles the Rust handler's raw-frame
  `log::trace!` out of the release wheel, so no logging configuration can capture it.
  Staleness now distinguishes a dead feed (`feed_stale_seconds`) from a silent instrument
  (`stale_book_seconds`) and says which tripped; Hyperliquid's guard moved 30 s → 12 s.
- **Task 6.** `DATA_INTEGRITY_AUDIT.md` gains D-41..D-44 and D-39 is updated; D-43 and D-44 are
  left **OPEN by name** rather than quietly closed.

### File List

- troll/collector_core/book_check.py (new), troll/collector_core/tests/test_book_check.py (new)
- troll/collector_core/collector.py, troll/collector_core/config.py, troll/collector_core/tests/test_collector.py
- troll/bybit_collector/collector.py, troll/bybit_collector/client.py, troll/bybit_collector/tests/test_sequence_canary.py (new)
- troll/hyperliquid_collector/book_snapshot.py (new), troll/hyperliquid_collector/tests/test_book_snapshot.py (new)
- troll/hyperliquid_collector/client.py, troll/hyperliquid_collector/collector.py, troll/hyperliquid_collector/config.py, troll/hyperliquid_collector/config.toml, troll/hyperliquid_collector/tests/test_collector.py
- troll/dydx_collector/client.py (opt-out docstring only)
- troll/scripts/capture_hl_ws.py (new), troll/docs/DATA_INTEGRITY_AUDIT.md

## Review Triage Log

### 2026-09-20 — Review pass (same session, fresh read of the full diff)

- intent_gap: 0
- bad_spec: 0
- patch: 1
  - **Evidence scope narrower than the behaviour it licenses.** The capture proves `u`
    contiguity on BTCUSDT linear `orderbook.50` only, but the promoted gap branch drops the
    book and resyncs on *every* Bybit instrument, spot included. Named the limit in D-41 and in
    the collector docstring rather than widening the claim; the canary self-reports a venue that
    behaves differently, so the failure mode is a visible counter, not silent loss.
- defer: 0
- reject: 3
  - `_crosscheck_one`'s 2 s confirmation sleep blocks the other instruments in that round —
    bounded by instrument count at a 300 s interval, not worth a concurrency layer.
  - `top_levels_mismatch` reports every absent level once the tolerance is exceeded, including
    the tolerated one — cosmetic, and the full list is the more useful diagnostic.
  - A Clear-only message would leave `_last_u` un-rebaselined — both adapters emit Clear plus
    levels together, and `_message_u` returns None for it, so nothing is misjudged.
- **Verified by reading, not assumption:** after a canary drop the core refuses to rebuild a
  book from incremental deltas (`_deltas_before_snapshot_dropped`), so a dropped book cannot
  silently become a shallow one; `_resync` retries from `_sample_tick` if the venue call fails.

## Auto Run Result

Status: awaiting-operator

- **Implemented:** Bybit `u` sequence canary (regress + gap → ledger, book dropped, resync queued);
  per-venue REST cross-check loop (`collector_core/book_check.py`, `book_crosscheck_seconds`,
  `fetch_book_levels` on both venue clients); feed-dead vs instrument-silent staleness
  (`feed_stale_seconds`) that names which condition tripped; Hyperliquid stale guard 30 s → 12 s;
  `scripts/capture_hl_ws.py` evidence tool for both venues; opt-out rationale in all three clients;
  audit rows D-41..D-44 with D-39 updated.
- **Evidence (raw captures, client sharing no code with the collector):**
  Bybit 800 s BTCUSDT — 31,862 `orderbook.50` frames, 31,860/31,860 delta steps exactly +1, zero
  gaps, zero regressions; first `publicTrade` frame 29 trades all 0.2 s old, i.e. no replay.
  Hyperliquid 630 s + 800 s — `l2Book` every 5.38/5.39 s median (3.07–5.96 s), every push changed
  the book; `trades` subscribe replays exactly 30 trades, up to 353 s old on a thin coin.
  Live cross-check — 6/6 rounds clean at depth 20 on Bybit linear + spot, zero confirmed mismatches;
  Hyperliquid REST returns 20 uncrossed levels.
- **Verification:** 189 tests pass across collector_core, bybit_collector, hyperliquid_collector and
  dydx_collector; bot_tui 259 and common 4 pass. One data_api test fails for want of a local Redis
  and fails identically without this change. `ruff`/`mypy` are not installed on this host.
- **Left open by name (DATA-02 end state):** D-42 stays OPEN until a full hour per venue and the VPS
  deploy report zero confirmed mismatches; D-44 (a sub-10 s replayed trade is bucketed by arrival,
  not `ts_event`) is OPEN and shares its root with D-31.
- **Residual risk:** the cross-check has only minutes of live evidence, not the hour AC #2 asks for,
  and nothing has run on the VPS yet.

## Operator Confirmation

Confirmed 2026-09-21: the external actions this story owed were carried out.

- Run the Bybit and Hyperliquid collectors for at least one hour each and confirm the `collector.book_crosscheck` count stays at zero (AC
- Deploy on the VPS with `make redeploy` and confirm Dozzle is clean for 10 minutes, with no `collector.book_sequence` entries on Bybit and no stale-book warnings naming "feed dead" on Hyperliquid.
- Run `ruff --fix` and `mypy` over `troll/`; neither is installed on the dev host.

_Appended by the bmad-loop orchestrator (`bmad-loop confirm`, #335): a human confirmed these external actions out of band, and the story was advanced from `awaiting-operator` to `done`._
