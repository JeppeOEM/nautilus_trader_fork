# Epic 31 Context: Trade-grade data: every captured value and every derived signal proven against an independent oracle (Bybit + Hyperliquid)

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Prove that every datapoint captured for Bybit (`BTCUSDT`/`ETHUSDT`, linear and spot) and Hyperliquid (`BTC`/`ETH` perp) is correct, and every signal derived from it is correct too, so bots can trade on it with confidence. Nothing may be lost, misrepresented or misinterpreted without a trace. dYdX is out of scope. The epic adds an independent reference recorder with no shared code, closes every silent drop and silent substitution, and checks trades, book, derivatives, instruments, catalog, candles and live/backtest parity against that oracle. It produces a one-off verdict over a fresh local soak, plus permanent gates: tests in `make test` and a nightly `verify_day` step in the `archive` saga.

## Stories

- Story 31.1: Verification context, independent reference recorder and a clean-slate side-by-side stack
- Story 31.2: Every drop is counted, ledgered and explainable (DATA-07 closure)
- Story 31.3: Derived signals against independent reference implementations
- Story 31.4: Trades proven id by id against the venue
- Story 31.5: The stored book proven against an independently rebuilt book
- Story 31.6: Mark, index, funding, open interest and instrument definitions proven
- Story 31.7: Catalog integrity and backtest-read parity
- Story 31.8: Candles and klines on every timeframe, with pass rates recorded
- Story 31.9: Live, backtest and display parity, including the bot's own signals
- Story 31.10: Fault injection proves every loss is accounted for
- Story 31.11: The verification run, the report and the permanent nightly gate

## Requirements & Constraints

- **Oracle independence (DATA-02):** the reference side shares no code with capture. It uses aiohttp WS/REST, `json` and `Decimal`, with its own book builder and its own fold. It never imports `nautilus_pyo3`, `capture`, `candles`, `ranking`, `views`, `kernel.fold` or `kernel.second_snapshot`. It may import `kernel.venue_http`, `kernel.venues` and `observability`. `tests/test_boundaries.py` enforces this.
- **Exact comparison:** compare Decimal values at the instrument's precision. Float storage noise is measured and reported as its own class and never absorbed into a tolerance. Any non-exact tolerance (statistical metrics only) is written next to its justification.
- **Verify the verifiers:** every comparator ships a planted-defect test that must make it fail or report a non-zero count.
- **Two end states per finding:** either fixed, with a test and an audit row, or registered OPEN in `docs/DATA_INTEGRITY_AUDIT.md` with a follow-up story. "Probably fine" is never an end state.
- **No silent loss:** every tolerated failure is recorded through `observability.error_ledger.record` at a named site constant (`sites.py`). Log-only drop sites become ledgered. Missing fields raise instead of defaulting to 0. Rejected seconds get durable per-reason coverage records. Conservation reports must show **unexplained = 0**.
- **Silent substitutions** in derived values (micro→mid, mark prices mixed into trade closes, shortened `pct_*` horizons, OFI not reset on gaps, untolerated nearest-match, 1W week start and 1W indicators computed on 1D bars, three volatility definitions, mislabelled display units) must each be made loud or kept as a tested, documented `Known limit:`.
- **No new dependency.** aiohttp, numpy, pandas and pyarrow are already pinned. Property-style tests use seeded stdlib `random`, not `hypothesis`.
- **Binding rules:** DATA-01..08, OPS-01 (defer VPS/operator steps to DEPLOY_CHECKLIST's "Deferred operator actions", never park a story), FORK-01 (never touch `nautilus_trader/` or `crates/`), NAUT-01..03 (backtests stream through `BacktestNode`/`BacktestDataConfig`), MEM-01 (every catalog query bounded by `start=` and `end=`), TEST-01..04 (warnings are not noise), DESIGN-01, SSOT-01/02, MR4.
- **Operator-facing outputs:** `docs/VERIFICATION_REPORT.md` holds the verdict table (VERIFIED / DEVIATION / OPEN per data type and instrument, with numbers, soak window, code revision and a repro command). The audit, `docs/DATA_DICTIONARY.md` and `platform/CLAUDE.md` DATA-02 are updated as the epic proceeds.

## Technical Decisions

- **New DDD context `platform/verification/`** with `domain/`, `application/`, `infrastructure/` and `tests/`. Its module docstrings state the invariant "the reference side never imports the code it checks". It is registered in `test_boundaries.py`'s `CONTEXTS` and its entrypoints in `COMPOSITION_ROOTS`. No other context may import `verification`, except `archive`'s nightly composition root.
- **Layering:** `domain/` holds only stdlib, `kernel` and Nautilus model/core types, with no I/O or asyncio. `application/` holds the ports (`typing.Protocol`) and the loops. `infrastructure/` is imported only by the composition roots. There is no module-level mutable state. Config comes from TOML loaders that reject unknown keys.
- **Venue URLs:** REST and WS URLs live only in `kernel/venue_http.py`. WS URLs are added there. Every venue REST call goes through that module.
- **Recorder output:** verbatim frames with a local receive time taken before parsing, stored as `<VERIFY_DATA_DIR>/raw/<venue>/<channel>/<UTC hour>.jsonl.zst`. Connection transitions are written as lines of their own, so a recorder gap is never mistaken for a collector gap. Instruments are read from each venue's `config.toml`.
- **Tools** follow the pattern `python -m verification.<tool> --venue V --day D`. The tools are `recorder`, `conservation`, `trades`, `book`, `derivs`, `catalog`, `candles` and `chaos`. Each one runs over one closed UTC day.
- **Reuse, don't rebuild:** `archive.compare_klines`, `archive.rebuild_seconds`, `capture/application/book_check.py`, `archive.crosscheck_errors`, `research/application/inspection.py`, `archive/tools/measure_lag.py` and the durable error ledger.
- **Trade truth:** production has exactly one fold (`kernel.fold.fold_trades`), and a live second is provisional until the nightly rebuild re-buckets trades by `ts_event` into `[S, S+1)`. A rebuilt row must match the reference fold exactly. Hyperliquid `ts_event` is compared at millisecond truth.
- **Book close rule (DATA-01):** second S includes deltas with venue `ts_event < S+1`. Bybit `u` contiguity is checked independently. Hyperliquid `l2Book` is a full snapshot per message.
- **Catalog:** there is one writer per leaf. Catalog readers do not dedupe, so a duplicate `(instrument, ts_event)` reaches every consumer. `ts_init` intervals must not overlap.
- **Snapshot encoding:** Epic 30.2's integer-exact snapshot has landed. Comparators must work on the stored integer units at the per-row precision.
- **Verify stack:** `platform/docker-compose.verify.yml` is an override (the base file is unchanged). It uses `verify-` container names and ports 26379/29100/28080 bound to `127.0.0.1`, and excludes the dYdX collector. Recorders run on the collector image with `network_mode: host` under a uid other than 1000:1000, so chaos tests can cut the collectors' connections by uid. It is driven by `make verify-up`/`verify-down`/`verify-wipe` with `-p verify`.

## Cross-Story Dependencies

- Order: 31.1 → 31.2 → 31.3 → 31.4 → 31.5 → 31.6 → 31.7 → 31.8 → 31.9 → 31.10 → 31.11.
- 31.1's recorder and fixtures feed every later story. After 31.2, the soak is wiped and restarted on the fixed code.
- 31.3 needs no soak data, but its real fixtures come from the 31.2 soak. Its reference signals are reused by 31.8 (candle fold) and 31.9 (OFI).
- 31.2's coverage record and `verification.conservation` are used by 31.4, 31.5 and 31.8 to explain gaps, and by 31.10 for each scenario window.
- 31.3 defers `DummyStrategy`'s gap handling to 31.9, which decides it with the operator.
- 31.11 needs every verifier plus at least one full clean closed UTC day (target 48 h), with chaos windows kept separate. It adds `verify_day` to `archive/application/nightly.py` after `compare_klines`. That step writes `verification_days` (never gating pruning, which stays `verified_days`) and returns a `"no reference data"` status when there are no recorders.
- Epic 30.2 (integer snapshot) and Epic 29 (dYdX → Bybit/Hyperliquid cutover) are the surrounding context. The epic runs on its own branch/worktree and is merged by hand into `troll`.
