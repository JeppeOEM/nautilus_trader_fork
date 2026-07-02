## Deferred from: code review of 1-1-verify-the-data-integrity-gate-end-to-end (2026-07-01)

- `classify_liquidity`'s `min_oi_usd` parameter name (`troll/dydx_collector/open_interest.py:109`) and the `liquidity_min_oi_usd` config field are stale — the function is `volume24H`-based, not open-interest-based, since an earlier fix (pre-existing, not introduced by Story 1.1). Renaming is an API-surface change (config field + call sites) unrelated to Story 1.1's ACs; pick up as a small standalone cleanup story.

## Deferred from: code review of 1-2-default-the-ranking-table-to-volume-sort (2026-07-02)

- `_VOLUME_24H` has no staleness/failure indicator, unlike `_LIVE_FAST`'s `stale` flag — if `_volume_loop_task`'s poll starts failing silently, the default sort and `Vol24h` column keep serving arbitrarily old data with no visual cue. Not required by any AC in Story 1.2; future hardening.
- `parse_volume_24h` (`troll/ml_signals/dashboard.py`) duplicates `dydx_collector/open_interest.py`'s `classify_liquidity` volume-parsing one-liner (`float(market.get("volume24H") or 0)`) with no shared constant or parity test tying the two together. AD-4 only bars reusing network-I/O code across the module boundary — the pure parsing logic could be factored into a shared utility. Minor, not blocking.
- Rankings poll interval is `setInterval(pollRankings,2000)` (2s) in `troll/ml_signals/dashboard.py`, while the Story 1.2 spec text says "1s poll" (AC2). Pre-existing code, untouched by Story 1.2's diff — reconcile cadence or spec wording in a follow-up.
