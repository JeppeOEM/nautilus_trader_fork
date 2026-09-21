# Deploy checklist: collector nightly maintenance

Operator actions for the collector host (the VPS, `nifelheim`) that no code can do for itself.
Record every measurement in `docs/DATA_INTEGRITY_AUDIT.md` (the row named next to it).

## 1. Install the nightly cron line (story 22.13)

After `make redeploy-all` has shipped story 22.13, replace the old
`make consolidate; make backup-catalog` line with this one (host crontab, `crontab -e`). The
times are UTC: put `CRON_TZ=UTC` on the line above if the box's clock is not UTC.

```bash
CRON_TZ=UTC
7 3 * * * cd /path/to/troll && { for v in DYDX BYBIT HYPERLIQUID; do make nightly VENUE=$v >> nightly.log 2>&1; done; make consolidate >> consolidate.log 2>&1; make backup-catalog >> backup.log 2>&1; }
```

- One `make nightly` per venue, for yesterday (UTC). Each runs `rebuild_seconds` ->
  `consolidate_catalog --days 2` -> `build_candles` -> `compare_klines` -> `prune_catalog` as
  separate processes. A step's exit 2 is "findings" (some instruments refused, mismatched or not
  comparable -- all ledgered, and none of them releases trades to the prune): the chain continues.
  Any other non-zero exit stops that venue's chain.
- The standalone `make consolidate` after the nightlies covers every closed day and every data
  type; it is the one that keeps reporting an old refused day, which the nightly's `--days 2` no
  longer sees (so one bad old day cannot block every night).
- `;` between the steps, never `&&`: one venue's failure must not skip the next venue or the
  backup.
- Each job runs in its own container (`docker compose run --rm collector ...`), never inside a
  running collector (MEM-01).
- **Locking:** each catalog-rewriting step (`rebuild_seconds`, `consolidate_catalog`,
  `prune_catalog`) takes the catalog maintenance lock separately, for its own duration -- not the
  whole chain. A manual run of one of them overlapping the cron job makes that step exit 1 (lock
  held), which stops that venue's chain for the night; rerun the day afterwards. Pre-existing
  limitation: dYdX's in-collector `_prune_loop` (dropped coins' files, per-coin delta retention)
  takes no lock at all.

### Re-running a missed or failed day

`make nightly VENUE=BYBIT DAY=2026-09-20`. Every step is idempotent for a closed day (the rebuild
changes 0 rows the second time; `compare_klines` overwrites that day's `verified_days` row), so a
day missed by cron, or failed at any step, is simply run again after fixing the cause. Run the
venues' missed days oldest first. Expect the prune report (`kept <iid> <day>: unverified|failed`)
to list every old unverified or failed day on **every** night until it passes or is dealt with by
hand -- that repetition is the reminder, not noise.

## 2. First-run measurements owed

None of these can be taken off the VPS; each is **NOT measured** until recorded.

| Measurement | How | Record in |
|---|---|---|
| Raw trade footprint per venue-day (MB and files, after consolidation) | `du -sh catalog/data/trade_tick/*.<VENUE>` and `find ... -name '*.parquet' \| wc -l` the morning after the first nightly | D-45 |
| First full nightly run per venue: wall seconds per step, peak child RSS, outcome | the `nightly <VENUE> <DAY>: ...` summary line in `nightly.log` | D-45 / D-51 |
| Files before -> after consolidation | the `consolidate: ...` line inside the same log | D-36 |
| Per-venue pass rate (instruments and minutes) and every mismatch's cause | the `compare_klines <VENUE> <DAY>: ...` line and the `reconcile.kline_mismatch` lines; root-cause each (DATA-02: a gap -> 22.14, anything else is a finding) | D-51 (and a new row per new cause) |
| Hyperliquid trade arrival lag over a whole day (tail vs the 10 s `stale_trade_seconds` filter) | `ts_init - ts_event` over one day's `trade_tick` files | D-59 |
| RSS headroom: peak child RSS vs the collectors' memory on the 3.7 GB box | `free -m` during the run, next to the summary line | D-06 |
| The first day that verifies `pass` for a venue | `verified_days` in `candles_<venue>.db` | closes D-31 / D-44 for trades |

## 3. Trade gap closure measurement (story 22.14)

Record every number in `docs/DATA_INTEGRITY_AUDIT.md` D-47 (Bybit, dYdX), D-48 (Hyperliquid)
and D-60 (Bybit spot depth).

1. **Before**, with `trade_feeds = 1` (as shipped): after `make redeploy-all`, let one full UTC
   day run and record the per-venue `compare_klines` pass rate (instruments and minutes) from
   that night's `compare_klines <VENUE> <DAY>: ...` line, and the day's count of
   `collector.trade_backfill` entries per venue (`GET /api/errors`, or `grep -c` in the
   collector logs) with their `backfilled` / `unrecoverable` totals.
2. **Flip:** set `trade_feeds = 2` in `bybit_collector/config.toml` **and**
   `hyperliquid_collector/config.toml` (dYdX has no second feed), then
   `make redeploy-all`. Check the first flush logs a `Trade feed arbitration (cumulative)` line
   per venue.
3. **After 24 h:** copy the last `Trade feed arbitration (cumulative)` line per venue (first
   copies, only-this-feed per feed, both), the count of `collector.trade_backfill` entries and
   their totals, any one-sided-outage notifications, and the next night's `compare_klines` pass
   rate per venue into the audit, next to the "before" numbers.
4. **After a week:** re-record the pass rates and name every remaining mismatch's cause
   (DATA-02): a `reconcile.kline_mismatch` in a minute covered by a `collector.trade_backfill`
   entry with `unrecoverable` seconds is the venue's depth (D-60/D-48); one in a minute the
   stale-book gate skipped is a rebuild orphan (collector docstring `Known limit:`); anything
   else is a new finding and gets its own audit row.

## 4. Things not to do

- Do not run `repair_catalog` on a day `rebuild_seconds` has rebuilt: its `ohlc_outside_book`
  detector compares exchange-timed trades with the mid-second book and would clear real trades.
- Do not run `rebuild_seconds --include-open-day` while that venue's collector is running.
