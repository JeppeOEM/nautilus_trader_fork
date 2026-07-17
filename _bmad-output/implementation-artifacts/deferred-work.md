## Deferred from: code review of 1-1-verify-the-data-integrity-gate-end-to-end (2026-07-01)

- `classify_liquidity`'s `min_oi_usd` parameter name (`troll/dydx_collector/open_interest.py:109`) and the `liquidity_min_oi_usd` config field are stale — the function is `volume24H`-based, not open-interest-based, since an earlier fix (pre-existing, not introduced by Story 1.1). Renaming is an API-surface change (config field + call sites) unrelated to Story 1.1's ACs; pick up as a small standalone cleanup story.

## Deferred from: code review of 1-2-default-the-ranking-table-to-volume-sort (2026-07-02)

- `_VOLUME_24H` has no staleness/failure indicator, unlike `_LIVE_FAST`'s `stale` flag — if `_volume_loop_task`'s poll starts failing silently, the default sort and `Vol24h` column keep serving arbitrarily old data with no visual cue. Not required by any AC in Story 1.2; future hardening.
- `parse_volume_24h` (`troll/ml_signals/dashboard.py`) duplicates `dydx_collector/open_interest.py`'s `classify_liquidity` volume-parsing one-liner (`float(market.get("volume24H") or 0)`) with no shared constant or parity test tying the two together. AD-4 only bars reusing network-I/O code across the module boundary — the pure parsing logic could be factored into a shared utility. Minor, not blocking.
- Rankings poll interval is `setInterval(pollRankings,2000)` (2s) in `troll/ml_signals/dashboard.py`, while the Story 1.2 spec text says "1s poll" (AC2). Pre-existing code, untouched by Story 1.2's diff — reconcile cadence or spec wording in a follow-up.

## Deferred from: code review of 1-3-expose-the-live-watchlist-as-a-queryable-backtest-consumable-coin-set (2026-07-15)

- `fetch_watchlist` (`troll/ml_signals/watchlist.py:36-38`) has no error handling around `urlopen`/`json.load` — a strategy script calling it while the dashboard is down gets a raw `URLError`/`JSONDecodeError` rather than a clear diagnostic. Mirrors `_fetch_volume_24h_json`'s identical shape, but that helper's only caller (`_volume_loop_task`) wraps it in `try/except Exception`; `fetch_watchlist` is a new external-facing entry point with no equivalent safety net elsewhere. Worth a small hardening pass alongside other deferred staleness/error-signaling items.
- `_is_fresh` (`troll/ml_signals/dashboard.py:151-154`) treats a `_LIVE_FAST` entry with a future timestamp as fresh indefinitely — if the system clock ever moves backward between write and read, `now_ns - entry["ts"]` goes negative and still satisfies `<= _WATCHLIST_STALE_NS`. Same clock-comparison shape already used by `_STALE_BOOK_NS`/`_CROSSED_RESYNC_NS` elsewhere in the codebase (not introduced by this story), extremely low real-world likelihood, self-correcting on the next legitimate update.

## Deferred from: code review of 1-4-persist-and-query-historical-coin-ranking (2026-07-16)

- Persisted `ts` reflects `compute_all()`'s start time (`now_ns` captured before the per-instrument thread-pool sweep), but the merged `rank`/`volume24h` reflect live `_LIVE_FAST`/`_VOLUME_24H` state read after `compute_all()` returns — a skew of unknown but bounded magnitude between the stored timestamp and the actual live-state capture time. Pre-existing characteristic of `_slow_loop_task`'s design inherited by rank/volume24h, not introduced by this story (every persisted column already shared this same ts-vs-actual-capture skew before Story 1.4). `troll/ml_signals/dashboard.py:1224-1229`.
