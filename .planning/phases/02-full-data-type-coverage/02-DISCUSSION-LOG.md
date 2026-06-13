# Phase 2: Full Data-Type Coverage - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-13
**Phase:** 2-Full Data-Type Coverage
**Areas discussed:** Funding rate dedup strategy, Bar source (venue klines vs internal aggregation), Order book depth validation (spot cap 50), Mark/index price & funding rate scope

---

## Funding Rate Dedup Strategy

| Option | Description | Selected |
|--------|-------------|----------|
| Dedup on value change | Strategy caches last seen funding rate per instrument; only persists when the value differs. Keeps catalog lean. | ✓ |
| Record every push, dedup later | Persist every FundingRateUpdate as-is; dedup downstream. | |
| Periodic snapshot only | Ignore the high-frequency ticker; record current funding rate on a fixed timer regardless of change. | |

**User's choice:** Dedup on value change (recommended option).
**Notes:** None — accepted the recommendation directly.

---

## Bar Source: Venue Klines vs Internal Aggregation

| Option | Description | Selected |
|--------|-------------|----------|
| Venue-native klines | Subscribe to Bybit's kline WebSocket per configured interval; exchange-accurate, independent stream. | ✓ |
| Internal aggregation from trades | Nautilus BarAggregator derives bars from recorded trade ticks; avoids extra subscription but is a derived view. | |

**User's choice:** Venue-native klines, recorded in ADDITION to trades/quotes/order-book (not as a replacement).
**Notes:** User's initial response was "I want to have access to full order book and as much data as possible — should I aggregate from this or get the klines from venue?" Claude recommended venue-native klines as an additive independent stream (since internal aggregation would just be a derived view of data already captured), reasoning that bars can always be re-derived from recorded trades later if a different interval is needed. User confirmed this framing.

---

## Order Book Depth Validation (Spot Cap 50)

| Option | Description | Selected |
|--------|-------------|----------|
| Fail fast with clear error | Validation rejects config before connecting if spot depth > 50, naming the instrument and depth — consistent with Phase 1's fail-fast philosophy. | ✓ |
| Auto-clamp to 50 with warning | Recorder silently reduces requested spot depth to 50 and logs a warning, allowing startup to proceed. | |

**User's choice:** Fail fast with clear error (recommended option).
**Notes:** None — accepted the recommendation directly.

---

## Mark/Index Price & Funding Rate Scope

| Option | Description | Selected |
|--------|-------------|----------|
| Automatic for all linear instruments | Any instrument in [[instruments.linear]] automatically gets funding-rate/mark-price/index-price subscriptions; no new TOML fields. | ✓ |
| Explicit per-instrument opt-in flags | Add funding/mark_price/index_price boolean fields to [[instruments.linear]] entries. | |

**User's choice:** Automatic for all linear instruments (recommended option).
**Notes:** None — accepted the recommendation directly.

---

## Claude's Discretion

- Exact code organization for funding-rate dedup state in `RecorderStrategy` (instance dict attributes vs helper methods)
- Exact `BarType`/`BarSpecification` string construction from `bar_intervals` config strings (research to confirm Bybit adapter's expected format, e.g. `-LAST-EXTERNAL`)
- Whether order-book-depth validation happens at config-load time (`config.py`) or in `on_start` (`strategy.py`)
- Whether `MarkPriceUpdate`/`IndexPriceUpdate`/`FundingRateUpdate` need any additional `CUSTOM_ENCODINGS`/Arrow-schema registration beyond Phase 1's pattern

## Deferred Ideas

None — discussion stayed within phase scope.
