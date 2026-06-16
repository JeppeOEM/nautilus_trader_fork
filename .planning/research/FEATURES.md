# Feature Research

**Domain:** dYdX v4 perpetuals market-data recorder (NautilusTrader fork, v1.1 milestone)
**Researched:** 2026-06-15
**Confidence:** HIGH (all findings traced to adapter source in `nautilus_trader/adapters/dydx/` and `crates/adapters/dydx/`)

## Scope Note

This research covers ONLY the NEW dYdX recorder feature set. The shared recorder infra
(catalog conversion via `StreamingConfig`/`convert_stream_to_data`, hot-reload, heartbeat,
graceful SIGTERM, reconnect) already exists from the Bybit milestone and is reused as-is.
Each feature below is assessed for how the dYdX adapter behaves vs. the Bybit adapter the
existing recorder was built against, so the roadmap knows where dYdX needs special handling.

## Feature Landscape

### Table Stakes (Users Expect These)

Data types the recorder must capture for dYdX perpetuals to reach parity with the Bybit recorder.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Trade ticks | Core data type; native `v4_trades` channel | LOW | `subscribe_trades` → native trade channel. No surprises; behaves like Bybit. |
| Order book deltas (L2) | Core data type; native `v4_orderbook` channel | MEDIUM | Full-depth L2_MBP deltas, NO level cap, NO depth param (see Q1). Only `BookType.L2_MBP` accepted — L1/L3 subscriptions are rejected with a warning (`data.py:377-381`). |
| Quote ticks (synthesized top-of-book) | Recorder requirement; dYdX has no native quote channel | MEDIUM | Synthesized from order-book top-of-book on each delta; already deduped in adapter (see Q2). Requires an active order-book stream underneath. |
| Bars / klines | Core data type; native `v4_candles` channel | LOW-MEDIUM | Native candles for a fixed resolution set; unsupported steps raise `ValueError` at subscribe time (see Q5). No internal aggregation needed for supported intervals. |
| Funding rate | Perp-specific requirement | MEDIUM | Emitted on the `markets` channel with NO adapter-side dedup (see Q3). Recorder MUST dedup, same as Bybit requirement. |
| Mark price | Perp-specific requirement | LOW | Derived from oracle price on `markets` channel (see Q4). |
| Index price | Perp-specific requirement | LOW | Derived from the SAME oracle price as mark price — values are identical (see Q4). |

### Differentiators (Competitive Advantage)

Not a competitive product; "differentiators" here = capabilities worth capturing because dYdX exposes them cheaply.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Full-depth L2 book | dYdX streams the entire book, not a truncated top-N | LOW (free) | Unlike Bybit's depth-capped snapshots, dYdX deltas are uncapped — richer backtest data at no extra config cost. Just subscribe to deltas. |
| Instrument status | Trading-halt / market-status events on `markets` channel | LOW | Adapter supports `subscribe_instrument_status`; already parsed (`data.py:431`). Optional, not in the stated v1.1 target list — treat as future. |

### Anti-Features (Commonly Requested, Often Problematic)

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| Recording index price as a separate, distinct series | "Bybit records both mark and index, so record both" | On dYdX, index == mark == oracle price (single `oracle_price` field, `data.rs:1326-1345`). Recording both writes duplicate values to two catalog series. | Record mark price; optionally record index as an explicit alias of oracle, but flag in docs that they are identical. Do NOT expect divergence. |
| Order-book depth subscription (`subscribe_order_book_depth`) | Mirror Bybit's depth-limited snapshot stream | dYdX adapter explicitly does NOT support it — `_subscribe_order_book_depth` logs a warning and no-ops (`data.py:388-392`). | Use `subscribe_order_book_deltas` with a managed book (already the recorder's approach). |
| Open interest | Parity with Bybit recorder | dYdX adapter has no OI support at all — out of scope per PROJECT.md. | None; explicitly excluded for v1.1. |
| dYdX spot | Parity with Bybit (perps + spot) | dYdX v4 has no spot market. | Perpetuals only. |
| Historical quote backfill | "Fill gaps on restart" | `_request_quote_ticks` warns "not published by dYdX" (`data.py:555-560`). No historical quotes exist. | Live synthesis only; gaps are unrecoverable for quotes (matches v1 live-only decision). |

## Per-Question Findings (Evidence)

### Q1 — Order book depth: levels / limits

- dYdX exposes a single full-depth `v4_orderbook` channel. `subscribe_orderbook` takes only an instrument id — **no depth/level parameter** (`websocket/client.rs:715-729`).
- The book is full L2 (MBP). `_subscribe_order_book_deltas` rejects any non-`L2_MBP` book type (`data.py:377-381`); Rust enforces the same (`data.rs:445-447`).
- A `depth` argument on snapshot requests is **ignored** with a warning: "Requesting book snapshot ... with specified `depth` which has no effect" (`data.rs:830-832`).
- **Implication:** No analog to Bybit's spot-50-level cap. dYdX is full-depth. The recorder's per-instrument "depth" config knob is meaningless for dYdX — either ignore it or validate that it isn't set for dYdX instruments.
- The adapter includes crossed-book resolution logic (`resolve_crossed_order_book`, `data.rs:1547+`), so transient crossed states are handled internally — no recorder action needed.

### Q2 — Quotes: synthesis gotchas

- No native quote channel. Quotes are synthesized in Python from order-book deltas (`_handle_orderbook_deltas`, `data.py:318-366`).
- **Gotcha 1 — quote requires an order book:** A quote is only emitted when an order-book subscription feeds the synthesizer. `_subscribe_quote_ticks` internally calls `subscribe_orderbook` (`data.py:404-405`), so subscribing to quotes implicitly subscribes to the book even if deltas aren't being recorded. If the recorder wants quotes, it gets a book stream whether or not it records deltas.
- **Gotcha 2 — emitted only on top-of-book change:** Quotes fire only when bid/ask price OR size changes vs. the last quote (`data.py:342-351`). Already deduped in-adapter. A quiet top-of-book emits NO quotes — so quote-stream silence is normal, not a fault.
- **Gotcha 3 — needs both sides:** No quote is emitted until both best bid and best ask exist (`data.py:336-341`). Early after (re)connect, before the book fills, expect a quote gap.
- **Heartbeat/staleness implication:** Quote inter-arrival is event-driven and bursty. A stale-stream heartbeat threshold tuned to Bybit's native quote cadence will produce false "stale" warnings on quiet dYdX markets. The recorder's heartbeat threshold for dYdX quote streams should be relaxed, OR heartbeat should track the underlying book/delta stream (which updates far more often) rather than the synthesized quote stream.

### Q3 — Funding rate: cadence and dedup

- Funding rate is published on the `markets` channel as `next_funding_rate`, emitted on EVERY trading update that carries the field (`data.rs:1415-1434`).
- **NO adapter-side dedup** — unlike synthesized quotes, the funding path emits a `FundingRateUpdate` whenever `next_funding_rate` is present, even if unchanged. The markets channel pushes frequent updates, so expect many repeated identical funding values.
- Funding interval is hardcoded to `Some(60)` (60 minutes — dYdX funds hourly), `data.rs:1421`.
- **Implication:** Same dedup concern as Bybit (REC requirement was "deduped to actual changes"). The recorder MUST apply its existing funding dedup (emit only on rate change) to the dYdX stream. This reuses the Bybit recorder's dedup mechanism directly — confirm it keys on (instrument_id, rate) and is exchange-agnostic.

### Q4 — Mark / index price: source and cadence

- Both mark and index price are derived from the dYdX **oracle price**, delivered via the `markets` channel `oracle_prices` map on a continuous cadence (`data.rs:1322-1345`), plus a one-time snapshot value from the per-market `oracle_price` field (`data.rs:1436-1457`).
- **Mark price == index price == oracle price.** They are built from the same `oracle_price` value (`data.rs:1326`, used for both `MarkPriceUpdate` and `IndexPriceUpdate`). There is no separate index feed.
- Cadence is driven by oracle updates on the markets channel — frequent but irregular (not a fixed funding-style tick).
- **Difference vs. Bybit:** Bybit delivers mark and index as distinct values on a ticker stream. dYdX collapses them into one oracle-derived value. Recording both produces duplicate series.
- **Dedup note:** Mark/index are NOT deduped in-adapter. If the recorder cares about file size, dedup-on-change applies here too (oracle price can repeat between updates), though price moves more than funding so dedup yield is lower.

### Q5 — Bars / klines: native vs. internal aggregation

- Bars come from the native `v4_candles` channel (`websocket/client.rs:761`, `subscribe_candles`). No Nautilus internal `BarAggregator` is needed for supported intervals.
- Supported resolutions (`DydxCandleResolution`, `common/enums.rs:679-709` + `from_bar_spec` mapping `:717-734`):
  - `1-MINUTE`, `5-MINUTE`, `15-MINUTE`, `30-MINUTE`, `1-HOUR`, `4-HOUR`, `1-DAY`.
- **Gotcha — fail-fast on unsupported intervals:** `py_subscribe_bars` calls `from_bar_spec`, which raises `ValueError` for any step/aggregation not in the list above (e.g. `3-MINUTE`, `2-HOUR`, tick/volume bars), `python/websocket.rs:961-967`. The recorder's config validation should restrict dYdX bar intervals to the supported set, OR catch the error at subscribe time so a bad config doesn't crash startup. This mirrors the existing recorder's fail-fast-against-instrument-cache pattern but adds a per-venue valid-interval whitelist.
- Bars must be price-aggregation on standard time intervals; non-time aggregations are unsupported.

## Feature Dependencies

```
Quote ticks (synthesized)
    └──requires──> Order book deltas (L2) subscription (implicit, auto-subscribed)

Funding rate recording
    └──requires──> Funding dedup mechanism (reused from Bybit recorder)

Mark price ≡ Index price  (both ← oracle price; recording both = duplicate series)

Bars (dYdX)
    └──requires──> Per-venue valid-interval whitelist in config validation

All data types
    └──requires──> Shared catalog/heartbeat/reconnect infra (already built; extracted to common module per v1.1 plan)

Heartbeat staleness (dYdX quotes)
    └──conflicts──> Bybit-tuned heartbeat thresholds (event-driven quotes go quiet → false stale)
```

### Dependency Notes

- **Quotes require a book subscription:** Subscribing to quotes implicitly subscribes to the order book (`data.py:404-405`). Recording quotes therefore incurs book traffic regardless of whether deltas are also recorded.
- **Funding dedup is shared:** The dYdX funding stream has no adapter dedup, so the recorder's existing Bybit dedup must apply venue-agnostically. Verify it keys on (instrument_id, rate).
- **Heartbeat thresholds conflict with Bybit defaults:** dYdX synthesized quotes are event-driven and silent on quiet markets; Bybit-tuned stale thresholds will misfire. The shared heartbeat module needs per-venue (or per-stream-type) thresholds, or should monitor the book/delta stream instead of the quote stream for dYdX.
- **Bar interval validation is new:** dYdX rejects non-standard intervals at subscribe time. Config validation must whitelist supported resolutions per venue.

## MVP Definition

### Launch With (v1.1)

Minimum to record the stated dYdX target feature set into the shared catalog.

- [ ] Trade ticks — native, no special handling
- [ ] Order book deltas (L2 full-depth) — drop/ignore any `depth` config for dYdX
- [ ] Quote ticks (synthesized) — relax heartbeat threshold for event-driven quotes
- [ ] Bars — whitelist supported resolutions in config validation; fail-fast on unsupported
- [ ] Funding rate — apply existing dedup (adapter does NOT dedup)
- [ ] Mark price — record from oracle-derived stream
- [ ] Index price — record, but document it equals mark price (no divergence on dYdX)

### Add After Validation (v1.x)

- [ ] Instrument status recording — adapter supports it; capture trading halts. Trigger: when halt/listing events become analytically useful.
- [ ] Dedup mark/index on-change to shrink catalog — Trigger: if oracle-price file volume becomes a disk concern.

### Future Consideration (v2+)

- [ ] Historical backfill for trades/bars/funding — dYdX HTTP supports `request_trade_ticks`/`request_bars`/`request_funding_rates` (`data.py:562-669`), but quotes cannot be backfilled. Defer per v1 live-only decision.

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Trade ticks | HIGH | LOW | P1 |
| Order book deltas (L2) | HIGH | LOW | P1 |
| Quote ticks (synthesized) | HIGH | MEDIUM (heartbeat tuning) | P1 |
| Bars (native candles) | HIGH | LOW-MEDIUM (interval whitelist) | P1 |
| Funding rate (with dedup) | HIGH | MEDIUM (reuse dedup) | P1 |
| Mark price | MEDIUM | LOW | P1 |
| Index price | LOW (== mark) | LOW | P2 |
| Instrument status | LOW | LOW | P3 |
| Mark/index on-change dedup | LOW | LOW | P3 |

**Priority key:**
- P1: Must have for v1.1 launch
- P2: Should have; low marginal value because index duplicates mark
- P3: Nice to have, future consideration

## dYdX vs. Bybit Adapter Behavior (drives special handling)

| Feature | Bybit (existing recorder built for this) | dYdX (new) | Recorder Special Handling |
|---------|------------------------------------------|------------|---------------------------|
| Order book depth | Depth-capped (e.g. spot 50 levels); per-instrument depth config | Full L2, no cap, no depth param | Ignore/forbid `depth` config for dYdX instruments |
| Quotes | Native quote stream | Synthesized from book top-of-book, deduped in-adapter, emitted only on change | Relax heartbeat staleness threshold; expect gaps on quiet markets and post-reconnect |
| Funding rate | Deduped to actual changes (REC req) | Emitted every markets update, NO adapter dedup, hourly interval | Reuse existing dedup; verify venue-agnostic |
| Mark / Index | Distinct ticker-stream values | Both = oracle price (identical) | Document equality; consider recording mark only |
| Bars | Standard intervals via adapter | Native candles, fixed resolution set, ValueError on unsupported | Per-venue interval whitelist + fail-fast at config validation |
| Open interest | Recorded | Not supported by adapter | Excluded (out of scope) |
| Spot | Recorded | No spot market on dYdX v4 | Excluded (out of scope) |

## Sources

- `nautilus_trader/adapters/dydx/data.py` — Python data client: quote synthesis & dedup (318-366), book-type/depth rejection (377-392), bar subscribe (421-425), market-data update routing without dedup (260-287), historical-quote unavailability (555-560). HIGH confidence (source-of-truth).
- `nautilus_trader/adapters/dydx/providers.py` — instrument provider (full HTTP-loaded instrument list). HIGH.
- `crates/adapters/dydx/src/data.rs` — mark/index from single oracle price (1322-1345, 1436-1457), funding emitted with no dedup + hardcoded 60-min interval (1415-1434), depth param ignored (830-832), L2-only enforcement (445-447), crossed-book resolution (1547+). HIGH.
- `crates/adapters/dydx/src/websocket/client.rs` — orderbook subscribe with no depth arg (715-729), candle subscribe (761-780), heartbeat plumbing. HIGH.
- `crates/adapters/dydx/src/common/enums.rs` — `DydxCandleResolution` supported set + `from_bar_spec` validation (679-734). HIGH.
- `crates/adapters/dydx/src/python/websocket.rs` — `py_subscribe_bars` raising ValueError for unsupported specs (961-1009). HIGH.
- `crates/adapters/dydx/src/websocket/enums.rs` / `messages.rs` / `handler.rs` — channel routing for `v4_orderbook`, `v4_candles`, `markets`. HIGH.
- `.planning/PROJECT.md` — v1.1 scope, out-of-scope (OI, spot), existing Bybit recorder mechanisms. HIGH.

---
*Feature research for: dYdX v4 perpetuals data recorder (v1.1)*
*Researched: 2026-06-15*
