# Phase 1: Bootstrap, Config & End-to-End Slice - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-13
**Phase:** 1-Bootstrap, Config & End-to-End Slice
**Areas discussed:** Conversion scheduling, Validation failure behavior, TOML config schema, Phase 1 slice scope

---

## Conversion Scheduling

### Trigger mechanism

| Option | Description | Selected |
|--------|-------------|----------|
| In-process timer | Strategy uses clock.set_timer() to call catalog.convert_stream_to_data() periodically | ✓ |
| Separate scheduled script | A second script/cron job runs independently to convert feather→parquet | |
| You decide | Claude picks based on simplicity | |

**User's choice:** In-process timer (recommended)

### Frequency

| Option | Description | Selected |
|--------|-------------|----------|
| Hourly | Frequent enough for dev feedback, keeps feather files small | |
| Daily at UTC midnight | Matches day-partitioning exactly | |
| Configurable interval | Add conversion_interval_minutes config field | ✓ |

**User's choice:** Configurable interval

### Instance ID / streaming directory convention

| Option | Description | Selected |
|--------|-------------|----------|
| Fixed instance_id | Constant instance_id so streaming directory is stable across restarts | ✓ |
| Timestamped per run | New instance_id each run | |

**User's choice:** Fixed instance_id (recommended)

### Partial-day handling

| Option | Description | Selected |
|--------|-------------|----------|
| Convert everything each run | Re-read feather files and write parquet each run, rely on catalog write_data de-dup | ✓ |
| Skip current/incomplete day | Only convert prior, fully-closed UTC day partitions | |

**User's choice:** Convert everything each run (recommended)

**Notes:** All four conversion-scheduling sub-questions answered in a single batch with recommended/flexible options selected.

---

## Validation Failure Behavior

### Failure mode

| Option | Description | Selected |
|--------|-------------|----------|
| Raise + exit non-zero | Log clear error listing missing instruments, raise to stop startup with non-zero exit | ✓ |
| Log error and stop gracefully | Log error, clean shutdown with exit 0 | |

**User's choice:** Raise + exit non-zero (recommended)

### Validation timing

| Option | Description | Selected |
|--------|-------------|----------|
| On strategy on_start, before subscribing | Validate against cache.instruments() before any subscriptions | ✓ |
| Config-time pre-check via REST | Separate pre-flight script validates instrument list before TradingNode starts | |

**User's choice:** On strategy on_start, before subscribing (recommended)

### Error message format

| Option | Description | Selected |
|--------|-------------|----------|
| List all missing instrument IDs at once | Operator fixes whole config in one pass | ✓ |
| Fail on first missing instrument | Simpler logic, multiple restart cycles needed | |

**User's choice:** List all missing instrument IDs at once (recommended)

### Config schema for linear vs spot

| Option | Description | Selected |
|--------|-------------|----------|
| Unified list, infer from ID | One instruments list, product type parsed from InstrumentId suffix | |
| Separate linear / spot sections | Two config sections mirroring BybitProductType | ✓ |

**User's choice:** Separate linear / spot sections

**Notes:** All four validation sub-questions answered in a single batch.

---

## TOML Config Schema

### Per-instrument settings structure

| Option | Description | Selected |
|--------|-------------|----------|
| Table per instrument | [[instruments.linear]] / [[instruments.spot]] arrays of tables with id, depth, bar_intervals | ✓ |
| Global defaults + per-instrument overrides | Top-level defaults with per-instrument override tables | |

**User's choice:** Table per instrument (recommended)

### Location of recorder-level settings

User asked for clarification on the practical difference between a `[recorder]` section vs flat top-level keys. Claude explained both are functionally equivalent; `[recorder]` groups recorder-wide settings (trader_id, catalog_path, streaming_path, conversion_interval_minutes, environment) separately from instrument arrays, slightly tidier as the file grows (e.g. Phase 5 ops settings).

| Option | Description | Selected |
|--------|-------------|----------|
| Single [recorder] section | One top-level table for trader_id, catalog_path, streaming_path, conversion_interval_minutes, environment | ✓ |
| Flat top-level keys | Settings sit directly at TOML root alongside [instruments.*] tables | |

**User's choice:** Single [recorder] section

### Credentials

Claude explained: env-only means BYBIT_API_KEY/BYBIT_API_SECRET (or _TESTNET_ variants) are read automatically by the existing adapter — no code needed, and no secrets in config files. TOML override would allow api_key/api_secret fields for local dev convenience but risks committing secrets.

| Option | Description | Selected |
|--------|-------------|----------|
| Environment variables only | BYBIT_API_KEY/BYBIT_API_SECRET env vars, adapter already supports this | ✓ |
| Optional override in TOML | TOML can specify api_key/api_secret, falling back to env vars | |

**User's choice:** Environment variables only (recommended)

---

## Phase 1 Slice Scope

### Proof environment/instrument

Claude explained: mainnet + a liquid linear perp (e.g. BTCUSDT-LINEAR.BYBIT) gives continuous trade flow for fast verification with no API key needed for public market data; testnet trade volume is sparse and slow to verify.

| Option | Description | Selected |
|--------|-------------|----------|
| Mainnet, one liquid linear perp | e.g. BTCUSDT-LINEAR.BYBIT on mainnet, guarantees trade flow | ✓ |
| Testnet instrument | Avoids mainnet dependency but sparse trade volume | |

**User's choice:** Mainnet, one liquid linear perp (recommended)

---

## Claude's Discretion

- Exact `[recorder]` field names beyond those captured, internal module/file layout for the collector script, and additional optional fields in the array-of-tables entries.

## Deferred Ideas

None — discussion stayed within phase scope.
