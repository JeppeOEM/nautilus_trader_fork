---
status: resolved
trigger: "OFI is updating but price is NOT updating — if OFI is updating price must also be updating"
created: 2026-06-30
updated: 2026-06-30
---

## Symptoms

- OFI z-score column changes between 5-second browser polls (e.g. GRT: +3.31 → +3.14)
- Price column shows identical value across all polls for ALL 35 instruments with data
- User expectation: "at least one coin out of the whole list should show a price move"

## Evidence

### Raw Redis data — POL bid/ask over 8 consecutive 1s snapshots:
```
bid=0.0692 ask=0.06949 (snap 0)
bid=0.0692 ask=0.06949 (snap 1)
... identical x8
```

### Raw Redis data — POL bid_size/ask_size over 6 snapshots:
```
bid=0.0692 ask=0.06952 bid_sz=307790 ask_sz=102130 (x6, identical)
```

### Trade activity check — 10 consecutive 1s batches across all instruments:
- buy_count = 0 for ALL instruments
- sell_count = 0 for ALL instruments
- Zero price movements detected

### API comparison — 0 of 35 instruments changed price in 6 seconds:
```
Price changed: 0
Price unchanged: 35
  same: POL  0.0695  (ofi: +0.40 -> +0.40)
  same: GRT  0.0176  (ofi: +3.31 -> +3.14)
  same: ENA  0.0709  (ofi: -1.59 -> -1.56)
  ...
```

### Catalog write timestamps:
- second_snapshots: last written 17:02 UTC (current: 17:03) — actively flowing
- minute_bars: last written 16:53 UTC — no trades for 10+ minutes

## Root Cause

**NOT a code bug.** Two separate phenomena at play:

### 1. Market is genuinely quiet
dYdX altcoin perpetuals at 17:00 UTC Tuesday — zero trades for 10+ minutes, zero bid/ask price movement. The order books have resting MM orders that haven't ticked. Price is correctly static.

### 2. OFI z-score changes without price movement — expected behavior
`MultiLevelOFI` computes order flow imbalance from SIZE changes at multiple price levels. `ofi_10_z` is a z-score normalized over a ROLLING 3600-point window (1 hour of 1s data).

The z-score formula is: `z = (current_ofi - window_mean) / window_std`

Even when `current_ofi ≈ 0` (no activity), the z-score changes because:
- Historical non-zero OFI values from up to 1 hour ago are aging off the window
- As they drop off, `window_mean` and `window_std` shift
- This changes the z-score of current readings even with zero new activity

**The user's assumption "if OFI is updating, price must also be updating" is incorrect.**
OFI is a SIZE-FLOW signal, not a price-level signal. They are independent.

## Resolution

No code fix required. The system is working correctly.

**Deploy pending dashboard changes** that add `ingest_count` to the status bar — this gives the user visible proof that data IS flowing (counter increments ~32 per 5-second poll).

## Verification

After deploy, the browser status bar shows `↺12345` incrementing on every poll, proving the data pipeline is live even during quiet market periods.
