<!-- Research + architecture plan reviewed and approved by the user on 2026-09-20 (Claude Code plan mode). Source of truth for Epic 22 in epics.md. -->

# Multi-Exchange (dYdX, Bybit, Hyperliquid): Architecture + Order Book Fetching Plan

## Context

Goal: trade, paper-trade and collect backtest data on Bybit (linear perps + spot) and Hyperliquid (a few perps Bybit doesn't list), alongside the existing dYdX stack, with **one shared collector core and no duplicated fetcher code**, so a fourth exchange is a thin module.

What already exists (epic 19, merged on `troll`):
- `troll/bybit_collector/` and `troll/hyperliquid_collector/` — working collectors, built as *siblings* of `dydx_collector` (story 19.3 AC: "no shared base until duplication across all three is visible"). ~200 of their ~250 lines are byte-identical; they lack dYdX's trade dedup (DATA-06), minute rollup (DATA-05), Redis publish, watchdog and lag canary.
- Shared catalog root, `venue` field in `data_api`/frontend, `common/venues.py` CEX/DEX registry, screener venue column.
- `troll/live_paper/` — paper/real trading, hard-wired to dYdX.

Decisions taken with the user (2026-09-20):
1. All three collectors move onto one shared core (dYdX included).
2. Snapshot cadence unified to 1.0 s for every venue.
3. Bybit = linear USDT perps **and** spot; perp-vs-spot must be explicit in backend and frontend.
4. Paper trading = Nautilus Sandbox (live mainnet data, simulated fills) as primary; Bybit Demo / Hyperliquid Testnet reachable only via the explicit exec-config path.

Deliverable after approval: this plan becomes a BMad architecture artifact + Epic 22 stories (`_bmad-output/planning-artifacts/`), then implementation story by story.

---

## Part A — Research: how each venue's order book works (official docs) and what the Nautilus Rust clients do with it

Sources: Hyperliquid GitBook (`for-developers/api/websocket/subscriptions`, `info-endpoint`, `rate-limits-and-user-limits`, `tick-and-lot-size`, `websocket/timeouts-and-heartbeats`), Bybit v5 docs (`websocket/public/orderbook`, `ws/connect`, `rate-limit`, `demo`, `market/orderbook`, `websocket/public/ticker`, `websocket/public/trade`), and the fork's own `crates/adapters/{bybit,hyperliquid,dydx}`.

| | dYdX v4 (existing) | Bybit v5 | Hyperliquid |
|---|---|---|---|
| **Book channel** | `v4_orderbook` | `orderbook.{depth}.{symbol}`; depths linear/spot 1 (10ms), 50 (20ms), 200 (100ms), 1000 (200ms) | `l2Book` (optional `nSigFigs`, `mantissa`) |
| **Documented semantics** | snapshot then deltas; no per-market sequence (connection-global `message_id`); crossed books are architectural (DATA-04) | first msg `type=snapshot`, then `type=delta`; size `0` = delete, unknown price = insert, else update; `u` = update id (sequence); `u=1` = service-restart snapshot, overwrite local book; `seq` = cross-sequence for ordering across topics; level-1 resends snapshot every 3s when unchanged; **no documented gap-recovery procedure** | "Snapshot feed, pushed on each block that is at least 0.5s since last push"; every message is the **full book**, max **20 levels per side**; no sequence numbers; on reconnect "missed data will be present in the snapshot ack" |
| **Rust client mapping** (`websocket/parse.rs`) | Clear + Add / Update + Delete; `sequence` = connection message_id | `snapshot` → `Clear` + `Add`; `delta` → `Update`/`Delete`; flags `F_MBP` (+`F_LAST`); `u` → `BookOrder.order_id`, `seq` → `delta.sequence`; **no gap check anywhere** (`parse.rs:232-316`, grep of handler/dispatch/client) | `Clear` (carries `F_SNAPSHOT`) + `Add` per level, `sequence=0` (`parse.rs:103-163`); pyo3 `subscribe_book` never passes `nSigFigs` → venue default full precision |
| **Depth we keep** | full | 50 (covers `BOOK_DEPTH=20`; a level-50 book holds ≤50 levels, top-20 always exact) | 20/side = venue max unaggregated = `BOOK_DEPTH` |
| **Crossed book** | expected; per-level msg-id uncross (Indexer algorithm), resync fallback | local corruption → loud ERROR (error_ledger) + resync after 10s (DATA-03 fallback, never the fix) | impossible unless venue bug: ERROR ledger; next message replaces the book |
| **Precision** | per-tick label from digit count → `_at_fixed_precision` re-stamp (AD-5) | instrument precision from `tickSize` digit count (constant per instrument), all data parsed at `instrument.price_precision()` | `price_decimals = 6 − szDecimals` (perp) / `8 − szDecimals` (spot), ≤5 significant figures; data parsed at instrument precision |
| **REST book snapshot (independent truth for DATA-02 cross-checks)** | `/v4/orderbooks/perpetualMarket/{ticker}` | `GET /v5/market/orderbook?category=linear|spot&limit≤1000` — pyo3 `BybitHttpClient.request_orderbook_snapshot()`; its `u` aligns with the **1000-level** stream only, so compare price levels, not `u` | `POST /info {"type":"l2Book"}` (weight 2), 20 levels/side — **not exposed to Python**, stdlib `urllib` POST like `dydx_collector/open_interest.py` |
| **Trades** | `v4_trades`, subscribed reply replays ≤1000 trades (DATA-06 dedup exists) | `publicTrade.{symbol}`, up to 1024 trades/msg, `seq` aligns with book; replay-on-subscribe **must be verified** | `trades`; docs say subscription ack carries a snapshot for time-series channels; replay **must be verified** |
| **Mark / index / funding / OI** | global `v4_markets` | `tickers.{symbol}` (linear: markPrice, indexPrice, fundingRate, openInterest; 100ms, delta = changed fields only); **spot ticker has no bid/ask, no funding/OI**; pyo3 drops OI → REST `/v5/market/tickers` poll (exists) | `activeAssetCtx` (markPx, oraclePx, funding, openInterest) → pyo3 forwards all incl. OI as `HyperliquidOpenInterest` |
| **Rate limits** | 2 subscribe msgs/s per connection (hard) | REST 600 req / 5 s per IP; ≤500 new WS connections / 5 min; spot subscribe ≤10 args/req; ping every 20s | REST 1200 weight/min per IP (`l2Book`=2); WS ≤10 connections/IP, ≤1000 subscriptions/IP, ≤2000 msgs/min; server closes after 60s idle (`{"method":"ping"}`) — 6 subs/coin → ~160 coins per IP |
| **Paper environments** | testnet | **Demo**: `api-demo.bybit.com` + `wss://stream-demo.bybit.com` (private only; public data from mainnet); no WS Trade API (Nautilus falls back to HTTP orders); funds via `POST /v5/account/demo-apply-money`; separate demo API key; do NOT use testnet's demo. Nautilus: `BybitEnvironment.DEMO` | **Testnet**: `api.hyperliquid-testnet.xyz`; faucet 1,000 mock USDC via `claimDrip`, requires a prior mainnet deposit from the same address; thin liquidity. Nautilus: `HyperliquidEnvironment.TESTNET`, key via `HYPERLIQUID_TESTNET_PK` |
| **Instrument ids** | `BTC-USD-PERP.DYDX` | `BTCUSDT-LINEAR.BYBIT`, `BTCUSDT-SPOT.BYBIT` (suffix ⇒ product type, one public WS per product type) | `BTC-USD-PERP.HYPERLIQUID`, `HYPE-USDC-SPOT.HYPERLIQUID`, HIP-3 `km:US500-USD-PERP` |

Key conclusions:
- Hyperliquid needs **no** delta bookkeeping at all: each `l2Book` message is authoritative. The 1 s sampler just reads the last book.
- Bybit is the only venue where a **silent desync** is possible (delta stream with sequence ids the Rust client ignores). We own the `u`-monotonicity canary and a REST cross-check.
- dYdX keeps its uncross machinery; it is the only venue-specific book logic that exists.
- The 20-level `DydxSecondSnapshot` schema is already venue-neutral and is what all three write today. Keep the class name (renaming changes the catalog directory `custom_dydx_second_snapshot` and orphans existing data).

---

## Part B — Architecture plan

### B1. Shared collector core: `troll/collector_core/`

Ponytail shape: the current `bybit_collector/collector.py` generalized, plus the venue-neutral features dYdX already has. One class, duck-typed venue client, dYdX as a subclass. No registries, no interfaces.

```
troll/collector_core/
  collector.py       Collector(config, client) + run_forever(build_collector)
  config.py          CoreConfig + load_core_config(path, environments=(...), extra=...)
  second_snapshot.py (moved from dydx_collector, class name unchanged)
  minute_rollup.py   (moved, unchanged)
  integrity.py       (moved, unchanged)
  open_interest.py   one `OpenInterest(Data)` type replacing the three identical classes
  tests/
```

`Collector` (core) owns, for every venue:
- `_on_data` O(1) enqueue → `_ingest_loop` (yield every 64) → `_process_data`
- live `OrderBook`s applied from `OrderBookDeltas`, `_last_book_update_ns`, per-side delta timestamps
- trade path: stale-age filter + bounded `trade_id` dedup (DATA-06, from dYdX; thresholds in config, dYdX values as defaults) → per-second OHLC/volume accumulators
- `_second_loop` at 1.0 s: lag canary, empty-top-of-book / crossed / stale gates (AD-1 single gate), `DydxSecondSnapshot` build, `ohlc_outside_book` canary, minute rollup, Redis `snapshots:raw` publish, catalog buffer
- `_flush_loop` → `asyncio.to_thread(catalog.write_data)`; zstd `write_table` patch
- watchdog (OBS-01, ntfy env var), `run()` task supervision, `run_forever()` signal + backoff restart loop (identical in all three today)
- crossed-book default: skip sample + `error_ledger.record` + if the client has `resync_orderbook` and crossed > `crossed_resync_seconds`, resync (Bybit); Hyperliquid client has no `resync_orderbook`, so skip-only.
- `extra_loops: tuple[Callable[[], Awaitable[None]], ...]` on the constructor for venue-specific periodic jobs (Bybit OI REST poll; dYdX status/control/prune/reload/incident loops).

Venue client contract (duck-typed, documented in the core docstring, verified by each venue's `test_client.py`): `fetch_instruments()`, `connect(loop, instruments)`, `disconnect()`, `subscribe(iid)`, `unsubscribe(iid)`, optional `subscribe_global()` (dYdX markets channel), optional `resync_orderbook(iid)`.

Per-venue package after the refactor:
- `bybit_collector/`: `client.py` (linear + spot WS, routing by suffix), `open_interest.py` (REST poll only, uses the shared `OpenInterest` type), `collector.py` ≈ 25 lines (`Collector(config, BybitClient(...), extra_loops=(oi_loop,))` + `run_forever`).
- `hyperliquid_collector/`: `client.py`, `collector.py` ≈ 15 lines.
- `dydx_collector/`: `client.py` (unchanged), `collector.py` = `class DydxCollector(Collector)` overriding `_apply_deltas` (per-level msg-id tagging) and `_handle_crossed_book` (uncross + escalation, moved to `dydx_collector/uncross.py`), plus its control plane (`_reload_config_loop`, `_status_loop`, `_control_loop`, `_prune_loop`, `_ws_raw_debug_flush_loop`, incident reports, liquidity tiering) as `extra_loops`. Its `config.py` extends `CoreConfig` with the dYdX-only fields.

Adding a venue = `client.py` + a config file + a 15-line entrypoint + one line in `common/venues.py`.

### B2. Shared data types
- Move `second_snapshot.py`, `minute_rollup.py`, `integrity.py` into `collector_core/` unchanged (class names unchanged ⇒ catalog directories unchanged). Update importers: `data_api/routes/*`, `ml_signals/catalog_stats.py`, `ranking_engine`, the two sibling collectors and tests.
- Collapse `DydxOpenInterest` / `BybitOpenInterest` / `HyperliquidOpenInterest` into one `collector_core.open_interest.OpenInterest` (same 4 fields, `from_pyo3` staticmethod for Hyperliquid). Catalog migration: one idempotent script renaming `custom_{dydx,bybit,hyperliquid}_open_interest/` → `custom_open_interest/` and rewriting the Arrow `type` metadata; readers (`grep custom_data(` in `ml_signals`/`data_api`) updated in the same story.

### B3. Perp vs spot made explicit
- `common/venues.py` gains `market_kind(instrument_id) -> "perp" | "spot" | "unknown"` from the Nautilus suffix (`-PERP`, `-LINEAR`, `-INVERSE` ⇒ perp; `-SPOT` ⇒ spot). Pure function, unit-tested against all real id shapes.
- `market` field added next to `venue` in every `data_api` response that carries `venue` (`routes/candles.py:173,177`, `snapshots.py:194,198`, `indicators.py:365,378`, `indicator_series.py:163,167`, rankings) and in `ranking_engine/engine.py:441` rank entries.
- Frontend: `schema.ts` field, screener column + filter (same pattern as Story 19.5's venue column), chart header badge.
- Bybit spot subscribes trades + `orderbook.50` only (no ticker/mark/funding/OI — per docs); snapshot writer is unchanged.

### B4. Redis contract
- All collectors publish to the existing `snapshots:raw` channel; payload entries are disjoint by `instrument_id`, `ranking_engine` already keys by iid. Spine "one producer per channel" wording amended to "one producer per (channel, venue)". No consumer change.
- `collector:status` / `collector:control` stay dYdX-only for now (bot_tui collector pane); promoting them to the core is a later story once the pane is venue-aware.

### B5. Trading and paper trading: `troll/live_paper/` goes multi-venue
- `live_paper/venues.py`: a dict `VENUE → (data_config_cls, data_factory, exec_config_cls, exec_factory, allowed_environments, paper_quote_currency)` for `DYDX`, `BYBIT`, `HYPERLIQUID`. Plain dict, no class hierarchy.
- `build_node`: venues in use = `{venue_of(bot.instrument_id)}`; one data client per venue and (paper) one `SandboxExecutionClientConfig` per venue. Nautilus allows one exec client per venue per node, so this stays one `TradingNode` per process — AD-11 wording changes from "one `DydxDataClientConfig`" to "one data client and one exec client per venue in use".
- `PaperConfig`: `starting_balances` becomes per-venue (`[venues.BYBIT] starting_balances = ["10_000 USDT"]`), `network` becomes per-venue `environment` strings validated against the venue's allowed set.
- Exchange demo/testnet: a second explicit-path mode. `load_real_money_config` accepts `mode = "real_money"` (requires `environment = "mainnet"`) or `mode = "exchange_demo"` (requires a non-mainnet environment: Bybit `demo`/`testnet`, Hyperliquid `testnet`, dYdX `testnet`). The two-signal safety design (separate file + separate loader + explicit `mode`) is preserved; a demo file cannot promote to mainnet without editing both keys. Credentials remain env-only: `BYBIT_API_KEY/SECRET` (or `BYBIT_DEMO_*`/`BYBIT_TESTNET_*` as Nautilus's factory reads), `HYPERLIQUID_PK`/`HYPERLIQUID_TESTNET_PK`, dYdX mnemonic as today.
- `DummyStrategy` is venue-agnostic already (quotes, L2 deltas, INTERNAL bars). Bybit spot quotes come from `orderbook.1` inside the Nautilus adapter, Hyperliquid quotes from `bbo` — no strategy change.
- `bot_status`/`trade_history`/`fills_store` key by `bot_id`; the only venue-sensitive value is the quote currency in equity math (USDC vs USDT) — carried via `paper_quote_currency`.

### B6. Backtesting
- Nothing new: `.BYBIT`/`.HYPERLIQUID` ids already load through `BacktestDataConfig` on `DydxSecondSnapshot`/`DydxMinuteRollup` (NAUT-03). Optional later story: historical `Bar` backfill via pyo3 `BybitHttpClient.request_bars` (years of 1m klines) and Hyperliquid `candleSnapshot` (last 5000 candles) for longer backtests than the collector's own history.

---

## Part C — Order book data fetching plan (per venue, what we verify and how)

1. **Bybit sequence canary.** In the core, when a delta's `order_id` (= Bybit `u`) is ≤ the previous `u` for that instrument, or a non-snapshot arrives with `u` gap > 1: `error_ledger.record("collector.book_sequence", …)` + counter; then resync. Bybit documents no contiguity guarantee, so a *gap* is logged as WARNING-with-counter first and promoted to ERROR only if a live capture shows `u` is contiguous in practice (DATA-02: evidence, not assumption). dYdX and Hyperliquid opt out (`sequence` semantics differ; documented in the venue client).
2. **REST cross-check job** (`extra_loops`, every N minutes, per venue): fetch the REST book (Bybit pyo3 `request_orderbook_snapshot`; Hyperliquid stdlib POST `l2Book`), diff top-20 against the live book at the same instant, record mismatches beyond a tolerance in `error_ledger` and `DATA_INTEGRITY_AUDIT.md`. This is the independent source of truth DATA-02 demands; dYdX keeps its incident-report path.
3. **Subscribe-time trade replay verification** (Bybit `publicTrade`, Hyperliquid `trades`): capture the first messages after subscribe with the Rust `[WS_RAW]` debug feed already used by dYdX, decide per venue whether replay exists, and register the finding (DATA-06). The core's dedup/age filter runs regardless.
4. **Stale vs quiet.** Per-venue `stale_book_seconds` in config (dYdX 5, Bybit 5, Hyperliquid 30 as today) plus a feed-level liveness timestamp (any message from the venue). A book that is silent while the feed is alive is *unchanged*, not stale; only "feed dead" or "post-reconnect before this instrument's fresh snapshot" is stale. Verified live against Hyperliquid's observed ~5 s push spacing (docs say ≥0.5 s per block — the discrepancy is itself a DATA-02 open item to resolve with a raw capture).
5. **Hyperliquid depth.** 20 levels/side is the venue max without aggregation; `BOOK_DEPTH=20` fits exactly. `nSigFigs` is never sent (pyo3 does not expose it) — documented so nobody assumes aggregation.
6. **Bybit spot.** Second public WS (`product_type=SPOT`), `orderbook.50` + `publicTrade`; spot has no mark/index/funding/OI streams. OI REST poll and ticker subscribe are linear-only by construction.
7. **Precision.** No re-stamp for Bybit/Hyperliquid (instrument-constant precision); dYdX keeps `_at_fixed_precision`. AD-5 unchanged.

---

## Part D — Migration of dYdX onto the core (highest-risk step)

- Order: build core from Bybit/HL first (story 1), live-verify both on the VPS, then move dYdX (story 2). dYdX's 18 test modules must pass unchanged in behavior; tests that poke `Collector` internals are updated to the subclass, not rewritten.
- The core's `_second_loop` gate is literally dYdX's current gate (`collector.py:1188-1269`) minus the uncross call, which becomes the overridable `_handle_crossed_book`. AD-1's "structural enforcement once a second writer exists" is satisfied by this being the one class every venue writes through.
- dYdX-only behavior that stays in `dydx_collector/`: uncross + escalation, `_level_msg_id` tagging, control plane, liquidity tiering, prune, incident reports, `_MAX_WS_SUBSCRIPTIONS=32`, ws-raw debug flush, `-PERP.DYDX` id construction.

---

## Part E — Draft story list (Epic 22, to be written properly after this review)

1. **22.1 `collector_core` from Bybit + Hyperliquid** — core class, `run_forever`, trade dedup, minute rollup, Redis publish, lag canary, watchdog; 1.0 s cadence; both venue packages shrink to client + entrypoint; live-verify on VPS.
2. **22.2 dYdX onto the core** — `DydxCollector(Collector)`, uncross moved to `uncross.py`, control plane as `extra_loops`; all existing tests green; live-verify.
3. **22.3 Shared data types** — move snapshot/rollup/integrity modules; single `OpenInterest` type + catalog directory migration script; importers updated.
4. **22.4 Bybit spot + perp/spot visibility** — spot WS in `BybitClient`, `market_kind`, API `market` field, screener column/filter, chart badge.
5. **22.5 Order book validation** — Bybit `u` canary, REST cross-check loop (Bybit + Hyperliquid), trade-replay verification, stale-vs-quiet liveness; `DATA_INTEGRITY_AUDIT.md` entries.
6. **22.6 `live_paper` multi-venue sandbox** — `venues.py`, per-venue data + sandbox clients, per-venue balances, AD-11 amendment, `test_node.py`.
7. **22.7 Exchange demo/testnet + real money for Bybit/Hyperliquid** — `mode = "exchange_demo"`, env credentials, `DEPLOY_CHECKLIST.md`.
8. **22.8 Docs/spine** — ARCHITECTURE-SPINE AD-1/AD-4/AD-11 amendments, `troll/CLAUDE.md` scope line (rules now cover `collector_core` + all venue collectors), story 19.3/19.4's "extract later" note closed.
9. **22.9 (optional) Historical bar backfill** — Bybit klines / Hyperliquid candleSnapshot into the catalog.

---

## Verification (end-to-end, after implementation)

- `cd troll && make test` (pytest over all packages incl. new `collector_core/tests`); ruff + mypy clean.
- Each collector run locally for ~60 s against mainnet, then `catalog.query(DydxSecondSnapshot, identifiers=[...])` returns rows for `.DYDX`, `.BYBIT` (linear and spot) and `.HYPERLIQUID` ids at 1 s spacing; `custom_open_interest/` populated for all three.
- Bybit: force a resync and confirm the canary/ledger entry; REST cross-check loop reports zero top-20 mismatches over an hour.
- `data_api` `/api/candles?instrument_id=BTCUSDT-SPOT.BYBIT` returns `venue="BYBIT", market="spot"`; screener shows and filters both.
- `live_paper` paper mode with three bots (one per venue) fills in Sandbox; `bots:status` shows all three; then `mode="exchange_demo"` on Bybit Demo places and cancels one order.
- VPS: `make up`, Dozzle shows all three collectors healthy; ranking_engine memory stays flat (epic 13 baseline).

## Open items surfaced (tracked, not silently resolved)
- Hyperliquid `l2Book` cadence: docs ≥0.5 s/block vs observed ~5 s — raw capture needed.
- Bybit `u` contiguity in practice — capture before promoting gap to ERROR.
- Sandbox account type for a venue that mixes spot and perps (Bybit `MARGIN` with `CurrencyPair` instruments) — verify a spot fill in Sandbox during 22.6.
