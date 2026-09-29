---
title: 'Story 31.2: Every drop is counted, ledgered and explainable (DATA-07 closure)'
type: 'feature'
created: '2026-09-29'
status: 'done'
baseline_revision: '09d566eeb8'
final_revision: 'f88f338826'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-31-context.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** Capture can lose trades and seconds without a durable, countable trace:
- The stale-trade filter judges a trade's age at processing time, so an ingest backlog over 10 s drops genuine live trades with an INFO line only.
- Several sites only log, or skip silently: the OHLC canary, second rejections, the venue-mode catch-up cap, the Redis publish, a `run()` crash, the OI parsers and unknown messages.
- The 2000-id dedup window can let an evicted id be archived twice.
- Nothing can prove that a missing trade or second is explained.

**Approach:**
1. Prove the backlog drop on recorded frames, then judge age on arrival (`ts_init`).
2. Ledger every log-only site at a named constant.
3. Make the dedup window time-bounded and seed it from the archive at startup.
4. Write a durable per-venue coverage record (`<catalog>/../coverage/<venue>.jsonl`) of rejected/skipped seconds and trade loss/backfill.
5. Add `python -m verification.conservation`, which reconciles reference ids and expected seconds against the archive plus that record. Unexplained must be 0.
6. Restart the verify soak on the fixed code.

## Boundaries & Constraints

**Always:**
- **Ledger sites:** every new site is a constant in `capture/application/sites.py`, and `CaptureService._ledger` is the only ledger caller in capture. Clients and parsers either get the ledger handed in, or return or raise to a caller that ledgers.
- **Per-flush summaries** (stale trades, rejected seconds) are one ledger line per flush that names every instrument, so they stay inside the 60/site/min cap. Per-event sites rely on the ledger's `suppressed` carry.
- **Stale filter:**
  - Age = `trade.ts_init - trade.ts_event`, where `ts_init` is the Rust receipt stamp.
  - A trade with no receipt stamp (`ts_init == 0`) keeps the processing-time rule, and this is documented.
  - The replay check still runs first. A venue replay (old `ts_event` at arrival) is still `STALE`.
- **Dedup:**
  - Evict an id only when the window is over `seen_trade_ids` **and** its registration time (arrival `ts_init`; fetch time for REST; archived `ts_init` for seeded ids) is older than `DEDUP_HORIZON_NS = MAX_TS_INIT_SKEW_NS + READ_SPAN_MARGIN_NS`.
  - At `run()`, before `apply`, seed every plan id from the archive's own trades over `[now - horizon, now]`. The read is bounded (MEM-01). Seeded ids use a first feed named `archive`.
  - A live copy of a seeded id is a `duplicate_feed` and is never archived or folded.
- **Coverage record:**
  - Append-only, fsync'd, one JSON object per line, written at each flush after the catalog writes, and on the final flush.
  - A failed write is ledgered (`collector.coverage_write`) and its lines are kept for the next flush, bounded at 10 000 lines. Beyond that bound, the lost count is ledgered.
  - Every sample tick notes, for every plan id, exactly one of: a row, a rejection reason, or `not_collected`.
  - Seconds a loop skips are noted explicitly (`catch_up_cap` in venue mode, `missed_tick` in arrival mode). Nothing is inferred as a fallback.
- **Conservation tool:**
  - Reads only verbatim raw records, raw Parquet (pyarrow; `trade_tick`, `custom_dydx_second_snapshot`), `_archive_gaps`, and the coverage file.
  - Imports nothing denied by `test_boundaries.py`. It is registered in `VERIFICATION_ROOTS` and in the runtime probe.
  - Memory stays bounded by one instrument-day of archived ids in Arrow plus one hour of Python id sets (a `Known limit:`).
- LGPL header on every file; type hints; functions under about 30 lines; the cognitive complexity limit of 10. Every deliberate simplification carries a `Known limit:`.
- Tests are plain `-> None` functions, with real Nautilus types and no mocking of Nautilus internals. Every comparator has a planted-defect test.

**Block If:**
- The recorded frames cannot be replayed into a `TradeTick` stream at all (for example, the fixtures lack `publicTrade` frames). Then the proof cannot be made; do not fabricate it.

**Never:**
- Never modify `nautilus_trader/` or `crates/`.
- Never touch `sprint-status.yaml`.
- Never change a venue `config.toml`.
- Never add a dependency.
- Never set `awaiting-operator` or write `operator_actions` (OPS-01): VPS steps go to `DEPLOY_CHECKLIST.md`.
- No read-time filtering and no tolerance that hides a loss.
- No trade/candle/book comparators (Stories 31.4+).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Backlog | recorded burst processed 15 s after arrival | every trade archived (none stale) | none |
| Replay | `ts_event` 30 s before `ts_init`, processed at once | `STALE`, counted | `collector.stale_trade` once per flush: count, oldest/youngest age per iid; coverage `trades_dropped` |
| Evicted id | window 3, 5 ids inside horizon, REST replays id 1 | not archived again | none |
| After horizon | id registered > horizon ago, window full | evicted (MEM-02) | none |
| Restart | archive holds id X at `now-10s`; new service; live X | `duplicate_feed`, not archived | none |
| Stale book 5 s | venue mode, S..S+4 stale | coverage run `stale` S..S+4 count 5 | `collector.second_rejected` flush summary |
| Catch-up cap | 40 s stall | 10 oldest seconds → `catch_up_cap` run for every plan id | `collector.skipped_seconds` |
| Restart gap | last archived row S0, first verdict S1 > S0+1 | `restart` run S0+1..S1-1 | `collector.restart_gap` naming iid and span |
| OHLC canary | high outside the book | row kept, canary loud | `collector.ohlc_outside_book` per occurrence |
| Redis down | publish raises | Parquet unaffected | `collector.snapshot_publish` |
| Crash | `run()` raises | restart with backoff | `collector.crash` with traceback |
| OI malformed row | planned symbol without `openInterest` | other rows kept | poll site ledgers the malformed rows (planned or unknown ids only) |
| Unknown message | Bybit `BybitWebSocketError`, unknown dYdX dict | not archived | `collector.unknown_message` with type and repr (dYdX `block_height` and `new_instrument_discovered` ignored by name, with a comment) |
| Conservation clean | fixture day: ids archived, seconds rows or runs | unexplained 0, exit 0 | none |
| Planted defects | archived trade deleted / coverage line deleted | unexplained > 0, exit 1 | none |

</intent-contract>

## Code Map

- `capture/domain/trade_intake.py` -- `accept` (stale rule), `register`/`_order` (window), `IntakeCounts`, `take_counts`.
- `capture/application/capture_service.py`:
  - `_process_data`/`_accept_live_trade` (`now_ns`, `arrival_ns`), `_report_stale_trades`.
  - `_flush_once`, `_sample_tick`, `_report_rejection`, `_check_impossible_ohlc`.
  - `_second_loop`/`_venue_second_loop`/`_due_seconds` (catch-up cap).
  - `_backfill_instrument`/`_apply_backfill`, `poll_loop`, `run`, `run_forever`.
  - `_catch_up_candle_store` (warning+continue), `_crosscheck_one` (log+return).
- `capture/application/trade_backfill.py` -- `admit_backfill` (registers `rest`).
- `capture/application/ports.py` -- `ArchiveWriter` (add `recent_trade_ids`, `last_snapshot_second`, `append_coverage`), `LiveStream`.
- `capture/infrastructure/parquet_writer.py`, `gap_markers.py` -- the writer and the append+fsync pattern.
- `capture/infrastructure/redis_stream.py` -- the swallowed publish failure.
- `capture/venues/{bybit,dydx}/open_interest.py`, `capture/venues/{bybit,hyperliquid,dydx}/client.py` `_handle_message`, `capture/venues/dydx/__main__.py` (the client factory: give dYdX the ledger).
- `kernel/second_snapshot.py` `from_dict` -- already strict (30.2). Add a `Known limit:` naming the pre-OHLC path (`archive.tools.migrate_snapshot_ints`) and a test that every field is required.
- `verification/infrastructure/raw_store.py` (`iter_records`, `channel_file`), `verification/domain/{plan_file,subscriptions}.py` (plan ids, `bybit_symbol`, `hyperliquid_coin`), `verification/recorder.py` (root pattern).
- `tests/test_boundaries.py` -- `VERIFICATION_ROOTS`, runtime probe, the AST-rule idiom with a self-test.
- `docker-compose.yml`, `docker-compose.verify.yml`, `Makefile` (`up`, `VERIFY_DATA_DIRS`), `tests/test_compose_verify.py`.
- Docs: `docs/DATA_DICTIONARY.md` (§1.1, new §1.16), `docs/DATA_INTEGRITY_AUDIT.md` (D-70+), `docs/VERIFICATION_REPORT.md`, `docs/DEPLOY_CHECKLIST.md`, `platform/CLAUDE.md` DATA-06/DATA-07.

## Tasks & Acceptance

**Execution:**
- [x] `tests/test_stale_trade_burst.py` (moved from `verification/tests/`: `test_cross_context_edges_follow_the_graph` exempts only `platform/tests` from cross-context imports) -- the proof, written first:
  - Rebuild `TradeTick`s from the committed Bybit `linear.publicTrade` fixture (`ts_event`=`T`, `ts_init`=`recv_ns`, real `Price`/`Quantity`).
  - Replay them through `CaptureService._process_data` with a fake processing clock (15 s ingest stall).
  - Assert that the old processing-time rule would have dropped N > 0 of them (the evidence), and that the fixed service archives every reference id, with 0 neither archived nor ledgered.
- [x] `capture/domain/trade_intake.py` -- arrival-age rule; stale min/max `ts_event` and age in the counts; time-bounded eviction with registration times; `ARCHIVE_FEED_NAME`; a `seed` method.
- [x] `capture/domain/coverage.py` (new) -- `SecondCoverage`, pure: per-iid runs of `(reason, first_s, last_s)`, extended when contiguous; `take()` returns closed and open runs and resets them. The line encoders/types for the four line kinds.
- [x] `capture/application/sites.py` -- `STALE_TRADE`, `SECOND_REJECTED`, `SKIPPED_SECONDS`, `RESTART_GAP`, `OHLC_OUTSIDE_BOOK`, `SNAPSHOT_PUBLISH`, `CRASH`, `UNKNOWN_MESSAGE`, `COVERAGE_WRITE`, `CANDLE_STORE_BEHIND`, `BOOK_CROSSCHECK_UNCONFIRMED`.
- [x] `capture/application/capture_service.py`:
  - Wire the stale-trade report and the coverage notes: rows, rejections, `not_collected`, `catch_up_cap`, `missed_tick`, and `restart` (via `last_snapshot_second` in a thread at an id's first verdict).
  - Coverage lines for stale drops, backfilled ids and unrecoverable windows. Seed at `run()`.
  - Ledger the OHLC canary, the publish failure, the crash and the two log+return sites.
- [x] `capture/application/ports.py`, `capture/infrastructure/parquet_writer.py`, `capture/infrastructure/coverage_file.py` (new) -- `recent_trade_ids`, `last_snapshot_second`, `append_coverage` (`<catalog>/../coverage/<venue>.jsonl`, append+fsync, raises for the service to ledger).
- [x] `capture/infrastructure/redis_stream.py` -- publish raises; the service ledgers it.
- [x] `capture/venues/{bybit,dydx}/open_interest.py` + `poll_loop` -- the parser returns `(rows, malformed)`; `poll_loop` ledgers the malformed rows at its `site`.
- [x] `capture/venues/{bybit,hyperliquid,dydx}/client.py` (+ the dYdX factory) -- ledger unknown messages; the dYdX info dicts are ignored by name.
- [x] `kernel/second_snapshot.py` + `kernel/tests/test_second_snapshot.py` -- the `from_dict` Known limit, and a parametrized missing-key test.
- [x] `tests/test_boundaries.py` -- an AST rule (with a self-test) that fails a `logger.warning/debug(...)` statement directly followed by `continue`/`return` in non-test `capture/`. Register `verification.conservation`.
- [x] `verification/domain/conservation.py`, `verification/application/conservation.py`, `verification/infrastructure/catalog_reader.py`, `verification/conservation.py` (root) -- per instrument:
  - Trades: reference ids seen (WS + REST, trade time in D; the `rest_only` share reported), archived, backfilled, ledgered-unrecoverable, unexplained, archived-not-seen, archived-twice.
  - Seconds: expected (86 400), rows, explained per reason, unexplained, duplicate rows, row-and-reason.
  - Text and `--json` output. Exit 1 on any unexplained, archived-twice, duplicate or row-and-reason count.
- [x] `verification/tests/test_conservation.py` -- a real catalog written via `ParquetDataCatalog`, the raw fixture records and a coverage file; clean → 0; each planted defect → non-zero.
- [x] `capture/tests/*` -- arrival rule and replay, stale report/ledger, evict-then-replay, restart seed, coverage runs (every row of the I/O matrix), OI malformed, unknown messages, publish/crash ledger, coverage write failure retained.
- [x] `docker-compose.yml` (`./data/coverage:/app/coverage` on the three collectors), `Makefile` (`coverage` in `up`'s mkdir and in `VERIFY_DATA_DIRS`), `tests/test_compose_verify.py` if it pins them.
- [x] Docs:
  - `DATA_DICTIONARY` §1.1 and a new §1.16 (coverage record format and conservation).
  - Audit rows with the evidence.
  - `VERIFICATION_REPORT` (the conservation row and the soak restart).
  - A `DEPLOY_CHECKLIST` deferred entry (create `data/coverage`, redeploy the collectors).
  - `CLAUDE.md` DATA-06/07 amendments.
- [x] OPS-01 soak restart:
  - Stop the verify stack in the checkout that runs it (`../nautilus_trader_fork-epic30/platform`: `make verify-down` and `make verify-wipe`).
  - `make verify-up` in this checkout on the fixed code.
  - Record the new soak start, the checkout and the revision in `VERIFICATION_REPORT.md`.

**Acceptance Criteria:**
- Given a recorded burst processed after a 15 s stall, when replayed through the service, then every reference trade is archived, and the test also shows the old rule would have dropped some.
- Given each site in the epic preamble, when it fires, then an entry is recorded at its `sites` constant, and the AST rule fails a new bare log+continue/return in `capture/`.
- Given an evicted or a pre-restart id, when a backfill or live copy repeats it, then the archive holds it at most once.
- Given a day of capture, when `python -m verification.conservation --venue V --day D` runs, then it prints the per-instrument trade and second tables, reports unexplained = 0 on consistent data, and exits non-zero on each planted defect.
- Given OPS-01, when the story finalizes, then the verify stack runs from this checkout on the fixed code, and `VERIFICATION_REPORT.md` records the new soak start.

## Design Notes

Coverage lines, one per line, in `<catalog>/../coverage/<venue lower>.jsonl`:
```
{"kind":"seconds","instrument_id":"BTCUSDT-LINEAR.BYBIT","reason":"stale","first_s":1759150000,"last_s":1759150004,"count":5}
{"kind":"trades_dropped","instrument_id":"…","reason":"stale","first_ns":…,"last_ns":…,"count":3}
{"kind":"trades_backfilled","instrument_id":"…","count":2,"trade_ids":["…","…"]}
{"kind":"trades_unrecoverable","instrument_id":"…","reason":"depth|fetch_failed","from_ns":…,"to_ns":…}
```
The seconds reasons are `no_book`, `empty_top`, `crossed`, `stale`, `unencodable`, `not_collected`, `catch_up_cap`, `missed_tick`, `restart` and `write_failed` (a row whose catalog write failed, added by the third review pass). A second is identified by `ts_event // 1 s`, the same on both sides.

The conservation tool partitions trades by the trade's own hour on both sides: reference `T`/`time` in ms, and archive `ts_event`. For hour H it reads raw files H-1..H+1, so a receive time that crosses the hour boundary is still counted. Ledgered-unrecoverable means not archived and inside one of: a `trades_dropped` range, a `trades_unrecoverable` window, or an `_archive_gaps` marker widened by `READ_SPAN_MARGIN_NS` below (marker spans are `ts_init`).

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. capture/tests verification/tests kernel/tests tests/test_boundaries.py tests/test_compose_verify.py tests/test_images.py tests/test_stale_trade_burst.py tests/test_coverage_contract.py tests/test_hotpath.py -q` -- expected: all pass.
- `cd platform && ruff format --check capture verification kernel tests && ruff check capture verification kernel tests && mypy capture verification` -- expected: clean.
- `docker ps --filter name=verify- --format '{{.Names}} {{.Label "com.docker.compose.project.working_dir"}}'` -- expected: 9 containers, working dir this checkout.

## Spec Change Log

## Review Triage Log

### 2026-09-29 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 19: (high 3, medium 9, low 7)
- defer: 0
- reject: 5: (medium 1, low 4)
- addressed_findings:
  - `[high]` `[patch]` A torn coverage line from a failed or killed append, or a retry that duplicated lines, would make the strict reader refuse every later day. The append now truncates back to its starting size on any failure. A torn tail is cut once per process and ledgered at `collector.coverage_write`.
  - `[high]` `[patch]` The restart-gap lookup and the seeding awaited inside the write gate, between `sample` and `close_second`, so trades could land in an already-sampled bucket and be counted late. Both now run in `apply()` for every added id, before it is subscribed, and the notes are synchronous.
  - `[high]` `[patch]` A `trades_dropped` window, or a gap marker, explained any missing trade in its span. Each now explains at most its recorded `count`. Uncapped windows (`trades_unrecoverable`, count-0 markers) are a documented Known limit.
  - `[medium]` `[patch]` `platform/data/coverage/` was not git-ignored. Added to `.gitignore`.
  - `[medium]` `[patch]` Capture and the tool could resolve different coverage paths (`.` or a symlinked catalog). Both now resolve the catalog root first.
  - `[medium]` `[patch]` `_write_coverage` caught only `OSError` and took its lines before the `try`. It now catches any exception and keeps the lines, and the candle-store apply runs before the coverage write.
  - `[medium]` `[patch]` One non-span or vanished file disabled seeding for the whole instrument, ledgered at the wrong site. It is now skipped per file and ledgered at the new site `collector.dedup_seed`.
  - `[medium]` `[patch]` A day passed vacuously with no coverage file or with a missing raw hour. Both now fail the day.
  - `[medium]` `[patch]` An `openInterest` of NaN or Infinity was archived, and a non-string value raised. Both are now malformed rows (`finite_decimal`).
  - `[medium]` `[patch]` An instrument added at runtime was never seeded. Seeding moved to `apply()`.
  - `[medium]` `[patch]` An id removed and re-added in one process left its out-of-plan seconds unexplained. Its verdict state is now forgotten on removal, so the re-add gets a fresh `restart` gap and seed.
  - `[medium]` `[patch]` A cancelled flush's thread could append concurrently with the final flush. Appends are now serialized by a `threading.Lock` on the writer.
  - `[low]` `[patch]` An open or future day now refuses with "day not closed".
  - `[low]` `[patch]` The malformed-row ledger detail is bounded to the first 10 rows plus "(+N more)".
  - `[low]` `[patch]` The dead `_IMPOSSIBLE_LOG_EVERY_NS` is renamed `_REJECTION_LOG_EVERY_NS`, with a true comment.
  - `[low]` `[patch]` The window-memory Known limit now uses measured per-id costs: about 47 MB per instrument at 1000 trades/s, and 84 MB with two feeds.
  - `[low]` `[patch]` A truncated or missing neighbour hour outside the day no longer refuses the day (`truncated_neighbour_files`).
  - `[low]` `[patch]` A live copy of an archive-seeded id is a `duplicate_feed` before the stale check, so it never widens a `trades_dropped` window.
  - `[low]` `[patch]` Addresses the review's DATA-06 concern about per-message receipt stamps: the arrival rule's dependence on them is now evidenced in D-70 for all three Rust clients, including dYdX's `v4_trades` subscribed reply (`crates/adapters/dydx/src/python/websocket.rs`).

### 2026-09-29 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 9: (high 0, medium 4, low 5)
- defer: 0
- reject: 16: (medium 3, low 13)
- addressed_findings:
  - `[medium]` `[patch]` A re-added id's `restart` run started after the last row on disk, so it overlapped that id's rows still in the flush buffer (`row_and_reason`, the day fails). `_last_noted` (kept across removal) now starts the run after the later of the two.
  - `[medium]` `[patch]` A backfill cancelled at shutdown, or abandoned before it ran, wrote no coverage window, so its missing trades were unexplained. The instruments it never fetched are now `fetch_failed` windows from their baseline to shutdown, written by the final flush.
  - `[medium]` `[patch]` A flush cancelled mid-append took its lines out of the pending list, and a failure in the still-running thread was swallowed. The append is now shielded and left in `_coverage_append`; the next (final) flush settles it before taking new lines.
  - `[medium]` `[patch]` `finite_decimal` accepted `float` (`Decimal(0.1)` is its binary expansion), against the price/quantity integrity rule. A float is now a malformed row.
  - `[low]` `[patch]` A final-flush append failure was ledgered as "kept for the next flush"; it is now ledgered as lines LOST at shutdown.
  - `[low]` `[patch]` The writer: a repaired tail whose append then failed went unreported, and a failed rollback's fragment was never re-checked. Repaired bytes are now carried to the next successful append, and the tail is checked again after any failed append. The repair reads back from the end in 64 KiB chunks, not the whole never-rotated file (MEM-01).
  - `[low]` `[patch]` Arrival mode kept the last tick as a float epoch, and `int()` of it can round up across a second boundary, dropping one second from the `missed_tick` span. It is now integer nanoseconds.
  - `[low]` `[patch]` Conservation spent overlapping capped windows first-fit by start, which could leave a trade unexplained that another assignment explains. It now spends the covering window that ends first (earliest deadline first).
  - `[low]` `[patch]` A day run just after midnight failed spuriously, because its last rows and coverage runs were not flushed yet. The tool now refuses until `DAY_SETTLE_NS` (2 h) after the day ends. This is a `Known limit:` with an upgrade path.

### 2026-09-29 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 6: (high 0, medium 1, low 5)
- defer: 0
- reject: 18: (medium 3, low 15)
- addressed_findings:
  - `[medium]` `[patch]` A snapshot batch whose catalog write failed was ledgered LOST, but its seconds had been noted `ROW`, so they had no coverage run and the day failed as unexplained. That also let a later `restart` run hide an earlier process's ledgered write loss. The flush now re-notes those seconds with the new reason `write_failed`, and the coverage append that follows in the same flush carries them.
  - `[low]` `[patch]` `_coverage_reason` fell through to `unencodable` for any verdict it did not know. It now checks `Unencodable` explicitly and raises `TypeError` for an unknown verdict.
  - `[low]` `[patch]` `_note_skipped` with an empty plan ledgered "no row for any of 0 planned instruments". Nothing is lost there, so it now returns early.
  - `[low]` `[patch]` A raw reference line that decoded to a non-object JSON value crashed the conservation tool with an unledgered `AttributeError`. It is now a `MalformedLine` refusal.
  - `[low]` `[patch]` A SIGKILL between a flush's catalog writes and its coverage append leaves that interval's reason runs unwritten under rows already on disk (a loud false fail). Documented as a `Known limit:` in `_flush_once` and in DATA_DICTIONARY §1.16, with a per-id "noted through" watermark as the upgrade path.
  - `[low]` `[patch]` A `write_failed` gap marker of a REST-backfilled batch spans the restamped `ts_init`, so it misses trades with older venue time (a loud false fail). Documented as a `Known limit:` at `GAP_MARKER_MARGIN_NS` and in §1.16, with markers carrying the `ts_event` span as the upgrade path.

## Auto Run Result

Status: done

**Summary:** Third (follow-up) review of Story 31.2 over the whole story diff (`09d566eeb8`..`96536d0bfb`), then 6 patches. The story's behaviour is unchanged. Capture now explains a snapshot row whose catalog write failed (a new `write_failed` seconds reason), and three boundary cases fail loudly and correctly. The two loud false-fail windows that remain are documented as Known limits.

**Files changed (this pass):**
- `capture/domain/coverage.py`: the `WRITE_FAILED` seconds reason.
- `capture/application/capture_service.py`:
  - `_note_lost_rows` in `_flush_once`.
  - `_coverage_reason` refuses an unknown verdict.
  - `_note_skipped` does nothing when nothing is planned.
  - The kill-window `Known limit:` in `_flush_once`.
- `verification/domain/conservation.py`: a non-object raw line is refused; the backfill-marker `Known limit:`.
- `capture/tests/test_coverage.py`: 3 tests (write-failed runs, unknown verdict, empty-plan skip).
- `verification/tests/test_conservation.py`: the non-object refusal test.
- `docs/DATA_DICTIONARY.md` §1.16: the `write_failed` row and two Known limits.

**Review:** Blind Hunter (16 findings) and Edge Case Hunter (10), deduplicated to 24.
- **Patches:** 6 applied.
- **Deferred:** 0.
- **Rejected:** 18.
  - A `trades_dropped` budget over-spend: a stale span is a contiguous venue replay, so the missing trades in it were themselves delivered and dropped.
  - `fetch_failed` windows match the coverage the fetch would have checked.
  - The AST rule's scope, a non-1 s interval, and `new_instrument_discovered` are as specified or already ledgered.
  - `kernel.venues` in the reference is 31.1's documented common-mode limit.
  - These were speculative or double faults: the host/container symlink, a failed truncate after a failed append, and a wall-clock step back.
  - Pre-existing and already rejected: the loud OI round failures and `run_forever`'s `build()`.
  - These are documented or by design: file growth, reasons not checked by the reader, cross-day duplicates, a mid-append read refusal, the startup read cost, and the OI message wording.

**Follow-up review recommended:** false. The patches are local, 5 of the 6 are low, and the one medium adds a single reason on an existing path, with a fix-removal test.

**Verification:**
- The spec's pytest command: 1015 passed. `capture/venues`: 198 passed.
- Fix-removal: the write-failed and empty-plan tests each fail without their fix.
- `ruff format --check` passes on `capture`, `verification`, `kernel` and `tests`.
- `ruff check` is clean on every changed file. It reports 3 errors in unchanged test files (`capture/venues/bybit/tests/test_collector.py:101`/`:103` and `capture/venues/dydx/tests/test_integration.py:77`); the `:101`/`:103` asserts were already there at baseline.
- `mypy capture verification`: none of its 58 errors is in a file this pass changed; the count is the same as at the last pass.

**Residual risks:**
- The two loud false-fail windows above (a kill between the writes, a failed write of a backfill batch).
- Uncapped `trades_unrecoverable` windows remain a documented limit.
- The verify soak stack was not restarted for these capture-side patches. They take effect on the next verify-stack or collector redeploy, which is covered by the existing `DEPLOY_CHECKLIST` 31-2 entry.
