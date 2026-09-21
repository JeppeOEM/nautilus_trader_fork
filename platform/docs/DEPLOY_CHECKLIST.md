# Deploy checklist: collector nightly maintenance

Operator actions for the collector host (the VPS, `nifelheim`) that no code can do for itself.
Record every measurement in `docs/DATA_INTEGRITY_AUDIT.md` (the row named next to it).

## 1. Install the nightly cron line (story 22.13)

After `make redeploy-all` has shipped story 22.13, replace the old
`make consolidate; make backup-catalog` line with this one (host crontab, `crontab -e`). The
times are UTC: put `CRON_TZ=UTC` on the line above if the box's clock is not UTC.

```bash
CRON_TZ=UTC
7 3 * * * cd /path/to/platform && { for v in DYDX BYBIT HYPERLIQUID; do make nightly VENUE=$v >> nightly.log 2>&1; done; make consolidate >> consolidate.log 2>&1; make backup-catalog >> backup.log 2>&1; }
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


## Rename `troll/` → `platform/` and the store move (2026-09-21, DDD spine AD-D13)

One-off, on `nifelheim`, the first time the renamed tree is deployed. Nothing else changed:
service names, env vars, container paths and the catalog layout are identical.

1. From the old checkout: `cd ~/nautilus_trader_fork/troll && make down` (all services, incl.
   any `live-paper` profile).
2. `cd .. && git pull`. Git renames the tracked tree to `platform/`; the **gitignored** data
   stays behind under `troll/`. Move it into the new store root:
   ```bash
   mkdir -p platform/data
   mv troll/dydx_collector/catalog          platform/data/catalog
   mv troll/dydx_collector/candles          platform/data/candles
   mv troll/dydx_collector/metrics          platform/data/metrics
   mv troll/dydx_collector/incident_reports platform/data/incident_reports
   mv troll/live_paper/data                 platform/data/live_paper
   mv troll/bot_tui_logs                    platform/data/bot_tui_logs
   mv troll/config.toml                     platform/data/dydx_config.toml   # the live dYdX plan file
   rmdir -p troll/dydx_collector troll/live_paper 2>/dev/null; ls troll   # should be empty, then rm -r troll
   ```
   Ownership stays uid 1000 (the collectors' `user:`); `chown -R 1000:1000 platform/data` if in doubt.
3. Crontab: change the nightly line's working directory from `.../troll` to `.../platform`
   (`make nightly VENUE=...`, `make consolidate`, `make backup-catalog`).
4. `~/.zshrc` on the desktop: the `troll-web`/`troll-tui`/`troll-logs`/`troll-down`/
   `troll-redeploy` helpers keep their names but any `cd .../troll` or `make -C .../troll`
   inside them becomes `platform`.
5. `cd platform && make build && make up`, then the usual 10-minute Dozzle check on all three
   collectors and `GET /api/errors`.
6. `platform/` must never gain an `__init__.py` (`platform` is a stdlib module);
   `tests/test_namespace.py` guards it and runs in `make test`.

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

## 5. Epic 22 rollout on nifelheim (consolidated 2026-09-21)

Every VPS-side operator action the eleven Epic 22 stories (22.1–22.5, 22.7, 22.10–22.14) still
owed when they were closed on 2026-09-21, in the order to run them. What could be verified on
the dev box that day was (see each story's `operator_actions` and audit D-64); nothing here
was. Story paths in the old text said `troll/`; the tree is `platform/` now (section above).
Record numbers where each line says, never in a story file.

### 5.1 One-time steps before the collectors come back up

1. `cd ~/nautilus_trader_fork && git pull` onto the merged `troll` branch; do the store move in
   the "Rename `troll/` → `platform/`" section above if not done yet.
2. `platform/config.toml` (dYdX): set `snapshot_interval_seconds = 1.0` if it still says `0.5`
   (22.2).
3. Bybit and Hyperliquid `config.toml` are bind-mounted from the repo, so
   `book_time_source = "venue"` and `hold_back_seconds` take effect on the next start (22.12).
   Leave `trade_feeds = 1` for now (step 5.4).
4. Stop the three collectors (`docker compose stop collector bybit_collector
   hyperliquid_collector`), then in the collector image run
   `python -m collector_core.migrate_open_interest --catalog /app/catalog` (report), then again
   with `--apply --backup-dir <dir>` (22.3, audit D-40).
5. `cd platform && make redeploy-all` (rebuilds the thin images with `collector_core`,
   `observability` when 23.1 lands, and starts every service; 22.1, 22.4, 22.5, 22.7, 22.10,
   22.13). Confirm the paper path starts with none of the new credential env vars exported
   (they default to empty in `docker-compose.yml`; 22.7).

### 5.2 First 10 minutes (Dozzle, one pass covers 22.1, 22.2, 22.4, 22.5, 22.10)

- All containers stay up; `dydx-collector` healthy; `bot_tui` Collector pane populates;
  `pin_top_liquid` works; an incident report lands on a WARNING (22.2).
- No `[collector.*]` error-ledger lines; at most one `Dropped subscribe-time trade history`
  per (re)subscribe; no `collector.book_sequence` on Bybit; no stale-book warning naming
  "feed dead" on Hyperliquid (22.1, 22.5).
- `DydxSecondSnapshot` rows for `BTCUSDT-LINEAR.BYBIT`, `BTCUSDT-SPOT.BYBIT` and
  `BTC-USD-PERP.HYPERLIQUID` land 1 s apart; spot has no mark/index/funding/OI rows
  (`/api/candles/<id>` or a catalog query; 22.1, 22.4).
- `GET /api/candles/BTCUSDT-SPOT.BYBIT` returns `venue=BYBIT, market=spot`; the screener and
  chart badge show and filter both Bybit markets (22.4).
- `redis-cli subscribe rankings:live` shows DYDX, BYBIT (-LINEAR and -SPOT) and HYPERLIQUID
  rows, each with non-zero USD `volume24h`; `/api/rankings` lists the `.BYBIT`/`.HYPERLIQUID`
  ids; the web rankings page has every venue chip selected by default, deselecting one hides
  only that venue and survives a reload; `make tui` columns stay aligned and `/` + `.bybit`
  narrows to Bybit rows (22.1, 22.10).
- `GET /api/errors`: `ranking_engine.volume24h` is not growing (a growing count means a
  collected instrument has no USD volume from its venue; 22.10).
- `docker stats dydx-ranking-engine` flat over the 10 minutes, then about 3x the instrument
  count over an hour (epic 13 baseline; the engine now polls volume 4x a minute; 22.10).
- No double Rust-logging init: the `[WS_RAW]` file sink still writes (22.2).

### 5.3 First day

- `collector.book_crosscheck`: zero confirmed on Bybit and Hyperliquid over at least one hour
  each, and `collector.book_crosscheck_unaligned` absent (22.5; the aligned check, audit D-64).
- `collector.late_trade`, `collector.pending_deltas`, `collector.book_sequence` for Bybit and
  Hyperliquid in `/api/errors`: a steady late-trade rate means `hold_back_seconds` is too
  short; any `pending_deltas` or `book_sequence` entry is a DATA-02 finding for D-63 (22.12).
- Lag measurement, once per venue, in the collector image with `--network host`:
  `python3 -m collector_core.measure_lag --venue bybit --seconds 10800` and
  `--venue hyperliquid --seconds 10800`; record the per-kind distributions in D-63, then set
  each venue's `hold_back_seconds` to its TradeTick p99.9 rounded up to 0.5 s (or 0.0 with the
  reason in the comment), replace the provisional dev-box comment, and redeploy (22.12).
- After the first full UTC day: `du -sh platform/data/catalog/data/trade_tick/*.<VENUE>` summed
  per venue into D-45 (22.13); `make nightly VENUE=DYDX|BYBIT|HYPERLIQUID DAY=YYYY-MM-DD` by
  hand for that day, summary lines and before/after file counts into D-45/D-51 and section 2
  above, peak RSS inside the box's free memory (MEM-01; 22.13); `make consolidate` once by
  hand and its `consolidate: ...` line into D-36 as **measured** (22.11).
- `compare_klines` pass rate per venue (instruments and minutes) into D-63/D-51 with every
  mismatch root-caused: a missing trade is a 22.14 gap, a book-related one is a new finding.
  Never add a tolerance (22.12, 22.13).
- One full day of Hyperliquid trade arrival lag (`ts_init - ts_event`) into D-59 to confirm
  the 10 s stale-trade filter drops no live trades (22.13).

### 5.4 Cron, backup, and the trade-feed flip

- Install the nightly cron line from section 1 (CRON_TZ=UTC if the box is not on UTC); it
  replaces story 22.11's consolidate-only line. After the first nightly run, copy that night's
  `consolidate: ...` line into D-36 as the measured nightly run (22.11, 22.13).
- Object storage: choose Cloudflare R2 or Backblaze B2, create a bucket, `rclone config` on
  the host (credentials stay in `~/.config/rclone`), set `RCLONE_REMOTE` and `RCLONE_BUCKET`
  (bare values) in `platform/.env`, run `make backup-catalog` once after a consolidation,
  confirm with `rclone lsf $RCLONE_REMOTE:$RCLONE_BUCKET/catalog/data --max-depth 2`, then
  update D-33 to say the backup is scheduled. Also answer D-33's open question: was the
  2026-09-19 17:53 VPS catalog reset deliberate? (22.11)
- Trade gap closure: run section 3 above in full (before figure with `trade_feeds = 1`, flip
  to 2, 24 h and one-week numbers into D-47/D-48; 22.14).

## 4. Things not to do

- Do not run `repair_catalog` on a day `rebuild_seconds` has rebuilt: its `ohlc_outside_book`
  detector compares exchange-timed trades with the mid-second book and would clear real trades.
- Do not run `rebuild_seconds --include-open-day` while that venue's collector is running.
