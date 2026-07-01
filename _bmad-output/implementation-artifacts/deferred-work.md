## Deferred from: code review of 1-1-verify-the-data-integrity-gate-end-to-end (2026-07-01)

- `classify_liquidity`'s `min_oi_usd` parameter name (`troll/dydx_collector/open_interest.py:109`) and the `liquidity_min_oi_usd` config field are stale — the function is `volume24H`-based, not open-interest-based, since an earlier fix (pre-existing, not introduced by Story 1.1). Renaming is an API-surface change (config field + call sites) unrelated to Story 1.1's ACs; pick up as a small standalone cleanup story.
