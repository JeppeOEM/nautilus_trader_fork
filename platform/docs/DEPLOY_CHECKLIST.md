# Deploy checklist: collector nightly maintenance

Operator actions for the collector host (the VPS, `nifelheim`) that no code can do for itself.
Record every measurement in `docs/DATA_INTEGRITY_AUDIT.md` (the row named next to it).

## 1. Remove the old cron line: the `archive` service schedules maintenance (Story 25.1b)

Nightly maintenance is no longer a host crontab line. The compose service `archive`
(`python3 -m archive.scheduler`, started by `make up` with no profile, `restart: always`) runs it,
so a reboot or redeploy at 03:07 no longer loses the night, and `archive:status` (the web UI's
maintenance status, the TUI's Collector pane) shows whether it ran.

After the first deploy that contains Story 25.1b (`make up`, or `make redeploy`, which starts
`archive`), delete the old line with `crontab -e`, then confirm it is gone:

```bash
crontab -l | grep -E 'make (nightly|consolidate|backup-catalog)'   # must print nothing
docker compose -f platform/docker-compose.yml ps archive              # Up
```

A line left in place would run the whole sequence a second time each night: harmless (every
step is idempotent and the two share the maintenance lock), but it wastes the night's memory
headroom and doubles the backup traffic.

What the service runs (schedule in `platform/archive/config.toml`, times UTC):

- **Nightly, at `nightly_at` (03:07).** For each closed day due, oldest first, and each venue in
  `venues`, the `archive.nightly` saga (Story 25.1). Then one `consolidate_catalog --apply` over
  every closed day and data type, then `archive.backup_catalog` when `backup_enabled = true`
  (committed `false` until off-site storage exists, Story 26.1b). Every step is its own child
  process (MEM-01), so no job runs inside a collector. `;` semantics: one venue's FAILED saga
  never skips the next venue, the consolidate or the backup.
  - The saga runs `rebuild_seconds` -> `consolidate_catalog --days 2` -> `build_candles` (which
    runs `python -m candles.rebuild` since Story 24.1; the step keeps its name) ->
    `compare_klines` -> `prune_catalog`, as separate processes (`python -m archive.<step>`).
  - The rebuild writes its result into the saga's temp dir, and the saga hands the reconcile
    `--rebuilt-by <run id>`, one `--rebuilt <iid>` per instrument the rebuild rebuilt and one
    `--not-rebuilt <iid>` per instrument it refused. Only the rebuilt are reconciled: any other
    instrument-day is never judged (`reconcile.not_rebuilt`). A missing result file stops the saga
    before consolidating. The run id is on the summary line.
  - A step's exit 2 is "findings" (some instruments refused, mismatched or not comparable, all
    ledgered, and none of them releases trades to the prune): the chain continues. Any other
    non-zero exit stops that venue's chain.
  - The full `consolidate_catalog` after the sagas covers every closed day and every data type. It
    is the step that keeps reporting an old refused day, which the saga's `--days 2` no longer sees,
    so one bad old day cannot block every night.
- **Catch-up.** Each venue's last day of unbroken success (`last_success_day`) and the last
  completed scheduled run (`last_run_day`) are kept in `platform/data/archive/state.json`. That
  file is a scheduler cursor, not a data verdict: `verified_days` stays the only day status.
  - After downtime the next run covers every missed closed day, oldest first, up to
    `catch_up_max_days` (7). Older days are ledgered once (`archive.catch_up_capped`, naming them)
    and left to `make nightly`.
  - A venue whose saga FAILED keeps its watermark, so the next night's run covers that day again
    plus the new one. There is no tight retry loop: `last_run_day` advances whatever the outcome.
  - With no state file (first start), every slot already past is taken as handled by the host cron
    it replaces: the first run is the next slot, covering that slot's yesterday only, so a daytime
    cutover never re-runs last night in the day. A night missed across the cutover is one "Run now"
    (web) or `make nightly VENUE=... DAY=...` away.
  - A step still running after `step_timeout_minutes` (360) is killed and fails with exit 124
    (`archive.step_timeout`), so a hung rclone or rebuild never stalls later jobs.
- **Intraday, every `intraday_consolidate_hours` (4).** At slots 00:07, 04:07, ... it runs
  `consolidate_catalog --apply --closed-hours`, which merges the current UTC day's closed hours of
  the small types (mark/index price, funding, open interest, instrument status). A file reaching
  the current hour is never touched. The nightly consolidate later merges those hourly files into
  the day's one file.
- **Run now.** The web UI's "Run now" (or a `{"command": "run_now", "day": "YYYY-MM-DD" | null}`
  message on `archive:control`) queues one closed day's full sequence (null: yesterday). It runs
  after any running job; the same day queued twice runs once.
- **Locking.** Each catalog-rewriting step (`rebuild_seconds`, `consolidate_catalog`,
  `prune_catalog`, and the manual `repair_catalog`/migration tools) takes the catalog maintenance
  lock (`<catalog>/.consolidate.lock`) itself, for its own duration, not the whole chain.
  - Before each job the service *probes* the lock without holding it. While a manual run holds it,
    the service waits, up to `lock_wait_minutes` (60), ledgering `archive.lock_wait` once. Past the
    bound it ledgers `archive.lock_timeout` and records that job as a failed `lock_timeout` step.
  - A manual run started in the gap between the probe and a step's own attempt makes that step exit
    1 (lock held), as before. Rerun the day afterwards (`make nightly`, or Run now).
  - A report-only run (`make prune-dry`, a rebuild or consolidation without `--apply`, a repair
    report) writes nothing and takes no lock, so it can run beside the service.
  - The dYdX collector no longer prunes anything itself (Story 25.1): dropped coins' files and
    per-coin delta retention are the nightly prune step's, under that lock, from the plan file
    `--dydx-plan` names (the `archive` service mounts `data/dydx_config.toml` read-only).
- **Capture lock (Story 25.1).** Every collector holds a shared `flock` on
  `<catalog>/.capture-<VENUE>.lock` for its whole life. The pid and start time inside are
  informational only. The file is never deleted, and the kernel releases a killed collector's lock.
  - `python -m archive.repair_catalog --apply` refuses a venue whose collector holds it
    (`repair.capture_running`, exit 1): stop that collector first. While a tool holds it
    exclusively, a starting collector waits (`collector.capture_lock_wait`, once) and starts when it
    is released.
  - The `archive` service never takes it: its steps write closed days and closed hours only.
  - No archive tool changes a row of the current UTC day. A whole-file write, merge or delete of a
    file whose span reaches it is refused (`<tool>.open_day` in the ledger when one is met).
  - The rebuild rewrites the midnight files (the one crossing midnight, and one starting after it
    that holds yesterday's last rows) with every row of today verified identical, so the 03:07 run
    never refuses `rebuild.open_day`.
- **Retention granularity (Story 25.1).** Plan retention (`non_config_retain_hours`, per-coin
  `retain_hours`) runs only in the nightly, on closed UTC days.
  - The effective floor is "the day closes, plus the next nightly", about 24 h worst case. The old
    15-minute in-collector loop pruned intra-day.
  - A dropped instrument is now any DYDX leaf not in the plan, so a delisted market's leftovers age
    out too, not only known indexer markets.
  - An empty (0-byte) plan file is refused (prune exit 1), never read as "collect nothing".
- **Stops.** A redeploy or `docker compose stop archive` mid-run kills the running step at compose's
  grace period. Every step is crash-safe (temp-then-rename, covering-file recovery), and that
  venue's watermark does not advance, so the next run repeats the day.

### Re-running a missed or failed day

The service retries a failed day by itself on the next night (see Catch-up above). By hand:
Run now on the web UI (all venues, one day), or `make nightly VENUE=BYBIT DAY=2026-09-20` for one
venue. Every step is idempotent for a closed day (the rebuild changes 0 rows the second time;
`compare_klines` overwrites that day's `verified_days` row -- a rerun re-proves a verified day), so
a day missed by the service (e.g. past `catch_up_max_days`), or failed at any step, is simply run
again after fixing the cause. Run the venues' missed days oldest first. Expect the prune report (`kept <iid> <day>: unverified|failed`)
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

## Story 25.2: the ranking context and the two preference TOMLs (2026-09-26)

One-off, on `nifelheim`, the first deploy that contains Story 25.2. `ml_signals/` was deleted,
so the two web-UI preference files it held (`chart_indicators.toml`, `screener_columns.toml`,
both rewritten by `data_api` from the UI) moved to `platform/data/` and are mounted at
`/app/preferences/` (`CHART_INDICATOR_CONFIG_PATH`/`SCREENER_COLUMNS_CONFIG_PATH` keep their
names; key sets unchanged). The ranking process now runs as `python3 -m ranking` (same compose
service `ranking_engine`, same env vars, same `metrics.db`).

1. Keep the live selections before pulling: the UI has rewritten the tracked files in place, so
   `git pull` would refuse (or overwrite them):
   ```bash
   cd ~/nautilus_trader_fork/platform
   docker compose stop data_api   # no UI save may land between the copy and the checkout
   cp ml_signals/chart_indicators.toml ml_signals/screener_columns.toml /tmp/
   git checkout -- ml_signals/chart_indicators.toml ml_signals/screener_columns.toml
   git pull
   cp /tmp/chart_indicators.toml /tmp/screener_columns.toml data/
   ls ml_signals 2>/dev/null   # only __pycache__ may remain; then rm -r ml_signals
   ```
2. `make build && make up` (rebuilds the thin images: `ranking` is copied, `ml_signals` no longer
   is), then check: the rankings page updates, the ranking-mode switch round-trips, a coin's
   saved chart indicators and the Technicals columns are still there, and `GET /api/errors`
   shows no new `ranking_engine.*` site.

## Story 25.3: the bots context (2026-09-26)

One-off, on `nifelheim`, the first deploy that contains Story 25.3. `live_paper/` became the bots
context `platform/bots/` (`python3 -m bots`, same compose service `live-paper`, same env vars,
same `./data/live_paper` mount and every `bots:*` key unchanged); `live_paper/` is now only
re-export shims. The paper config moved with the code: `live_paper/config.toml` →
`bots/config.toml`, mounted at `/app/bots/config.toml`. The archive tools' old
`collector_core.*`/`dydx_collector.normalize_snapshot_schema` shim paths were deleted -- use
`python -m archive.<tool>` (the cron line runs `make nightly` and is unaffected; since Story 25.1b
there is no cron line at all -- the `archive` service schedules the saga, section 1).

1. If the VPS copy of `live_paper/config.toml` has local edits, keep them before pulling:
   ```bash
   cd ~/nautilus_trader_fork/platform
   cp live_paper/config.toml /tmp/bots-config.toml
   git checkout -- live_paper/config.toml
   git pull
   diff /tmp/bots-config.toml bots/config.toml   # re-apply any local edits to bots/config.toml
   ```
2. Confirm nothing outside the repo still runs a deleted archive shim path -- each would now fail
   with `ModuleNotFoundError` instead of a deprecation warning:
   ```bash
   crontab -l | grep -nE 'collector_core\.|normalize_snapshot_schema' || echo "crontab clean"
   ```
   Repoint any hit to `python -m archive.<tool>` (or `archive.tools.<tool>`) before the next run.
3. Two startup changes to know before restarting (neither touches the compose defaults):
   - A `REDIS_URL` naming a non-zero Redis database (`/2` or `?db=2`) now refuses to start: the
     Nautilus Cache can only use database 0, so it used to split silently from the `bots:*` bus.
     Compose's `redis://127.0.0.1:${REDIS_PORT:-6379}` is unaffected; check any override with
     `docker compose --profile live-paper config | grep REDIS_URL`.
   - A host run (`python3 -m bots` without `FILLS_DB_PATH`) now defaults to
     `platform/data/live_paper/fills.db`, the same file compose mounts, not the old in-package
     `platform/live_paper/data/fills.db`. If that old file exists, move it over first or its
     history is not read.
4. `docker compose --profile live-paper build live-paper && make up-live-paper`, then check:
   `bot_tui`'s Bots pane shows every bot heartbeating, a `bots:control` stop/start round-trips,
   and `GET /api/errors` shows at most a couple of `bots.status_build` at boot (the known
   first-quote race, ledgered since this story) and no other new `bots.*` site. The new sites
   (`bots.fill_lost`, `bots.incidents_write`, `bots.status_build`, `bots.control_message`,
   `bots.control_action`, `bots.history_refresh`, `bots.redis`) were each a log-only failure
   before, so any may appear; a rising count is a real failure. Full checklist:
   `bots/DEPLOY_CHECKLIST.md` (its own copy of this rollout moved here in Story 26.3).

## Story 25.4: collection control, the one venue loader (2026-09-26)

One-off, on `nifelheim`, the first deploy that contains Story 25.4. dYdX's control plane moved out
of `DydxCollector` into `platform/collection_control/` (same compose service `collector`, same
`collector:status`/`collector:control` channels, same `./data/dydx_config.toml` bind mount), and
every venue's `config.toml` now goes through one strict loader
(`collector_core.config.load_venue_config`, `capture.infrastructure.config` since Story 26.2, which
the command below uses). The expired `ranking_engine/` shims were deleted.

1. Check `data/dydx_config.toml` **before** restarting the collector: the file now refuses start
   (fail closed, `ValueError` naming the key or ids, in the `collector` logs) where the old loader
   ignored a problem. `git pull && make build` (the collector image now copies
   `collection_control` and no longer `ranking_engine`), then parse the mounted file in the new
   image without starting anything:
   ```bash
   cd ~/nautilus_trader_fork/platform
   docker compose run --rm --no-deps collector python3 -c "from pathlib import Path; \
   from capture.infrastructure.config import load_venue_config; \
   c, p = load_venue_config(Path('/app/dydx_collector/config.toml'), 'DYDX'); \
   print(len(p.collected), 'instruments,', sorted(p.excluded), c)"
   ```
   Or check by hand: only the known keys (`network`, the core thresholds, `config_reload_seconds`,
   `open_interest_poll_seconds`, `liquidity_check_seconds`, `liquidity_min_oi_usd`,
   `non_config_retain_hours`, `instruments`, `exclude`; no `environment`); each `[[instruments]]`
   entry holds only `id`, `store_order_book_deltas` (true/false) and `retain_hours`; at most 30
   instruments; no id both in `instruments` and in `exclude`.
2. The core thresholds are now **honoured** for dYdX: any of `stale_book_seconds`,
   `crossed_resync_seconds`, `stale_trade_seconds`, `seen_trade_ids`, `feed_stale_seconds` or
   `book_crosscheck_seconds` present in `data/dydx_config.toml` now takes effect (it was silently
   ignored before), and a `trade_feeds` other than 1 refuses start (dYdX opens one trade feed).
   Remove any you did not mean to set.
3. `make up`, then check: `bot_tui`'s Collector pane lists every planned coin, a
   `:start`/`p` (unpin)/`x` (stop) round-trips and rewrites `data/dydx_config.toml`, and
   `GET /api/errors` shows no `collector.subscribe_failed`. A `collector.unplanned_message` right
   after a stop/unpin is the in-flight messages of the unsubscribed coin, counted and dropped; a
   steady one is an open DATA-02 question.

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
2. **Flip:** set `trade_feeds = 2` in `capture/venues/bybit/config.toml` **and**
   `capture/venues/hyperliquid/config.toml` (dYdX has no second feed; both files lived in
   `bybit_collector/`/`hyperliquid_collector/` before Story 26.2), then
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
   `python -m archive.tools.migrate_open_interest --catalog /app/catalog` (report), then again
   with `--apply --backup-dir <dir>` (22.3, audit D-40).
5. `cd platform && make redeploy-all` (rebuilds the thin images with `capture` -- `collector_core`
   before Story 26.2 -- and `observability`, and starts every service; 22.1, 22.4, 22.5, 22.7, 22.10,
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
  only that venue and survives a reload (22.1, 22.10). ~~`make tui` columns stay aligned and
  `/` + `.bybit` narrows to Bybit rows~~ `[amended 2026-09-26: Story 25.1a -- the TUI Coins
  pane was deleted, rankings are web-only; nothing to check in the TUI]`.
- The web rankings page's Volume / Volatility control: clicking the other mode returns 202
  (`PUT /api/rankings/mode`), and the pressed button flips only once `rankings:live` carries
  the new mode (`redis-cli subscribe rankings:live` shows `"mode"` change within a heartbeat);
  switch back afterwards so the VPS keeps its usual mode. With `ranking_engine` stopped the
  same click shows an inline 503 `no ranking_engine subscribed to ranking:control` (25.1a).
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
  `python3 -m archive.tools.measure_lag --venue bybit --seconds 10800` and
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

### 5.4 Nightly maintenance, backup, and the trade-feed flip

- Nightly maintenance is the `archive` service (section 1, Story 25.1b): remove any old host
  crontab line as section 1 says and confirm `archive` is Up. After its first nightly run, copy
  that night's `consolidate: ...` line (`docker compose logs archive`) into D-36 as the measured
  nightly run (22.11, 22.13), and check `GET /api/archive/status` shows every step with its exit.
- Object storage (optional until storage exists; Story 26.1b): the off-site backup is the explicit
  setting `backup_enabled` in `platform/archive/config.toml`, committed `false`, so until storage
  exists no backup step runs, `archive:status` says `"backup": "disabled"` (web panel and TUI:
  `backup off`) and the catalog has no copy off the host (D-33 stays OPEN). When storage exists:
  choose Cloudflare R2 or Backblaze B2, create a bucket, `rclone config` on the host (credentials
  stay in `~/.config/rclone`, which the `archive` service mounts read-only; set
  `RCLONE_CONFIG_DIR` in `platform/.env` if it lives elsewhere), set `RCLONE_REMOTE` and
  `RCLONE_BUCKET` (bare values) in `platform/.env`, run `make backup-catalog` once after a
  consolidation (it runs rclone inside the image; the host needs no rclone), confirm with
  `rclone lsf $RCLONE_REMOTE:$RCLONE_BUCKET/catalog/data --max-depth 2`, then set
  `backup_enabled = true` and `make up` (the service refuses to start, `archive.config_invalid`,
  if either rclone value is missing, and `restart: always` then repeats the refusal: check
  `docker compose logs archive`), check the status says `"backup": "enabled"`, and update
  D-33 to say the backup is scheduled. Also answer D-33's open question: was the 2026-09-19 17:53
  VPS catalog reset deliberate? (22.11)
- Trade gap closure: run section 3 above in full (before figure with `trade_feeds = 1`, flip
  to 2, 24 h and one-week numbers into D-47/D-48; 22.14).

## 4. Things not to do

- Do not run `repair_catalog` on a day `rebuild_seconds` has rebuilt: its `ohlc_outside_book`
  detector compares exchange-timed trades with the mid-second book and would clear real trades.
- Do not run `rebuild_seconds --include-open-day` while that venue's collector is running.

## 6. Day-long clean-run check (story 23.3)

Before the first `make redeploy-all` that ships this story: `mkdir -p platform/data/errors` on
the VPS, owned by the container user (`chown 1000:1000 platform/data/errors` if created as
another user) -- every service bind-mounts it `rw` as `/app/errors_dir` and writes its own
`<service>.jsonl` there from its first line, so the directory must exist and be writable before
the containers start, the same as `platform/data/catalog` and the other `platform/data/*` stores.

`python3 -m archive.crosscheck_errors` reads every service's durable ledger under
`platform/data/errors/` together with the archived catalog, in the collector image (the
`errors_dir`/`catalog` mounts are already present):

```bash
docker compose run --rm --no-deps collector python3 -m archive.crosscheck_errors \
  --catalog /app/catalog --errors-dir /app/errors_dir \
  --fail-on collector.book_crosscheck collector.book_sequence collector.pending_deltas \
            ranking_engine.volume24h process_start
```

(`docker compose run` has no `--network` flag -- and needs none here: the cross-check is pure
local file and Parquet I/O against the two mounts, with no venue or Redis access at all.)

(No `--since`/`--until` needed for the usual "did the last 24 hours run clean" check -- that is
the tool's default window; pass both as ISO-8601 UTC instants, e.g. `--since
2026-09-21T00:00:00Z --until 2026-09-22T00:00:00Z`, to pin an exact day for the record instead.)
Exit 0 closes, in one command, the day-long evidence these operator actions were each left
waiting on:

- **22.5 #1** — `collector.book_crosscheck` is 0 for Bybit and Hyperliquid over the whole window
  (already in the tool's default `--fail-on` list).
- **22.1 #2** — the "Instrument gaps" section prints `(none)` *and* the "Instruments" section
  lists every expected collected instrument with a plausible `rows=` count and `first=`/`last=`
  spanning the window: no `[collector.*]`-caused gap, snapshots 1 s apart throughout. `(none)`
  on its own is not sufficient -- the tool only diffs gaps *between* two observed rows (see the
  module's own `Known limits`), so an instrument with zero rows for the whole window (dead the
  entire time) also prints no gap; that is exactly what `rows=0`, or a `last=` well short of
  the window end, catches. Cross-check the instrument list itself against `config.toml`/
  `dydx_config.toml` — an id that was never collected has no partition and so no row here at
  all. An instrument *delisted* mid-window shows rows then stops, and its trailing absence can
  never be ledger-explained; recognise it from the venue's own listing rather than chasing it
  as a gap.
- **22.10 #4** — `ranking_engine.volume24h`'s printed count is 0 (in `--fail-on` above, so a
  nonzero count fails the exit code instead of only showing in the printed report).
- **22.12 #5** — `collector.late_trade`, `collector.pending_deltas` and `collector.book_sequence`
  are printed per service for Bybit and Hyperliquid; `pending_deltas`/`book_sequence` are
  already `--fail-on` defaults, while `late_trade` is deliberately **not** in the `--fail-on`
  list above: a steady nonzero rate does not fail the exit code on its own (it means
  `hold_back_seconds` is too short, not a loss). It is printed, read by the operator every run,
  and root-caused by hand in `DATA_INTEGRITY_AUDIT.md` (D-63).
- **A nonzero `restarts=` on any service is itself a finding**, not an explanation. Every gap a
  crash-loop causes prints `restart`, so without `process_start` in `--fail-on` (as above) 40
  OOM-kills would still exit 0 — the exact inversion DATA-07 forbids ("a restart-tolerant
  pipeline does not make a crash-looping one acceptable", audit D-06). Root-cause the restarts;
  do not drop the token to make the command pass.

Read the sites a gap is `explained:` by — do not trust the word. The match is time proximity
within 300 s of one of the gap's own edges, with no instrument id on the ledger line, so a site
that fires continuously (a steadily nonzero `collector.late_trade`) explains every gap whose
edge it brackets. `explained: collector.late_trade` on a 40-minute hole is a DATA-02 question,
not a closed one.

Expect `UNEXPLAINED` gaps on the first real run, from three sampler skip paths that today emit
no snapshot row **and** no ledger entry: empty top-of-book, stale book and no book at all
(then `collector_core/collector.py` ~:1208, ~:1218, ~:1342; since Story 26.1 the empty-top path is
ledgered `collector.empty_top`, and the service is `capture/application/capture_service.py` since
Story 26.2). That is the check working — an
unledgered skip is a DATA-07 finding. The resolution is to give those three paths their own
ledger sites (the DDD spine assigns that to the `SecondSampler` story), never to relax the
check or widen the matcher.

The command also exits 1, with a message on stderr, when it found **no ledger file at all**
(missing `errors_dir` mount, `ERROR_LEDGER_DIR` unset) or **no second-snapshot instrument**
(wrong `--catalog`, or a `--venue` matching nothing). A run that checked nothing must never
read as a clean day.

Add `--venue bybit`/`--venue hyperliquid`/`--venue dydx` to narrow which instruments' gaps are
checked (and so which collector's ledger explains them) to one venue. It never narrows the
"Services"/`--fail-on` check -- a site belonging to a different service, e.g.
`ranking_engine.volume24h`, is still checked under `--venue bybit`.

## 8. Venue cutover: Bybit and Hyperliquid proven, then dYdX stopped (Story 29.3)

Collection moves off dYdX (operator decision 2026-09-26). Bybit keeps `BTCUSDT-LINEAR.BYBIT`,
`ETHUSDT-LINEAR.BYBIT`, `BTCUSDT-SPOT.BYBIT` and `ETHUSDT-SPOT.BYBIT`; Hyperliquid collects
`SOL-USD-PERP.HYPERLIQUID` instead of BTC/ETH (both reversible by config:
`platform/capture/venues/{bybit,hyperliquid}/config.toml`). The dYdX compose service `collector`
is behind the `dydx` profile: `make up`, `redeploy`, `redeploy-all` and `redeploy-no-paper` never
start it, `make up-dydx` starts it and `make down-dydx` removes it. Nothing here deletes data.
The dropped instruments (every `.DYDX` id, and Hyperliquid's BTC/ETH) fall under the nightly's
normal retention only: their raw trades go once a day is `verified` and older than 7 days, and
a dYdX coin's opted-in order book deltas once older than its `retain_hours` in the dYdX plan
(`platform/data/dydx_config.toml`; none: kept). Their 1 s snapshots, mark/index, funding, open
interest and instrument definitions are the permanent archive and stay, **as long as the dYdX
plan's `instruments` list is left as it is**. While `DYDX` is in `archive/config.toml`'s `venues`,
the `DYDX` saga's prune reads that plan: every type except `trade_tick` of a `.DYDX` id the plan
no longer lists is deleted once older than its `non_config_retain_hours` (`archive/prune_catalog.py`,
plan retention). So never trim the dYdX plan to "clean up" after the cutover.

**Partial days are expected to fail reconciliation.** `compare_klines` compares our 1 m candles
with the venue's candles for the whole UTC day, minute for minute, so a day an instrument was
collected for only part of fails with a `reconcile.kline_mismatch` for every minute it was not
collected. There are three such days. The redeploy's day D fails for `SOL-USD-PERP.HYPERLIQUID`
(it starts that day) and for `BTC-USD-PERP.HYPERLIQUID`/`ETH-USD-PERP.HYPERLIQUID` (they stop).
The day of check 6's `make down-dydx` fails for every `.DYDX` id. For each failed day, confirm
that its mismatches lie only on the uncollected side of the switch time: the detail is in
`data/errors/archive.jsonl`, `grep '"reconcile.kline_mismatch"' data/errors/archive.jsonl | grep
'<id>'`, where each line names the id and the minute. Known limit: a failed day keeps its raw trades
(the prune reports it `kept <id> <day>: failed` every night and never deletes it). Nothing is
lost. For the `.DYDX` ids the day needs a decision by hand before `DYDX` retires (see below).
`HYPERLIQUID` stays in the nightly, so day D's `kept ... failed` lines for
`BTC-USD-PERP.HYPERLIQUID`/`ETH-USD-PERP.HYPERLIQUID` recur every night and their raw trades stay,
with no step that clears them. The upgrade path is a collection-window record per instrument that
the comparison clips to.

Run the checks in order, from `platform/` on the VPS. Stop at the first one that fails and fix
its cause (DATA-02); dYdX keeps running until check 6. URLs are the VPS's `data_api`
(`127.0.0.1:9100`; from the desktop, through the `troll-web` tunnel).

1. **Redeploy the new configs, dYdX still running.** Before it, note the time for check 4:
   `date +%s%N` (the redeploy's `since_ns`). Then `git pull` and `make redeploy-all`.
   Expected: `docker ps --format '{{.Names}} {{.Status}}' | grep collector` lists
   `bybit-collector` and `hyperliquid-collector` with a fresh `Up`, and `dydx-collector` still
   `Up` with its old uptime (`redeploy-all` no longer names it, so it keeps its old image). In
   Dozzle, `hyperliquid-collector` logs `Started: 1 subscribed` and `bybit-collector`
   `Started: 4 subscribed`. `redeploy-all` also restarts `live-paper`; run it when no paper bot
   holds a position you care about.
2. **Rankings, within 10 minutes.** Open `http://127.0.0.1:9100/`. Expected: the five ids
   `BTCUSDT-LINEAR.BYBIT`, `ETHUSDT-LINEAR.BYBIT`, `BTCUSDT-SPOT.BYBIT`, `ETHUSDT-SPOT.BYBIT` and
   `SOL-USD-PERP.HYPERLIQUID` are listed without the market-data-stale marker (⚠), and Vol24h is
   filled for all five (`ranking`'s Bybit source covers spot too: USD-quoted spot turnover). In the filter panel, the condition
   `Exchange (venue)` `=` `BYBIT` leaves only the four Bybit rows, and `=` `HYPERLIQUID` only the
   SOL row (and `=` `DYDX` only dYdX's, which are still fresh). The same from the API:

   ```bash
   r=$(curl -sf http://127.0.0.1:9100/api/rankings) || echo "rankings not published yet: retry"
   test -n "$r" && echo "$r" | python3 -c 'import json, sys
   r = json.load(sys.stdin)
   for i in r["items"]: print(i["instrument_id"], i.get("volume24h"))
   print("stale:", r["stale_instrument_ids"])'
   ```

   Expected: all five ids printed, each followed by a number (never `None`), and none of the
   five in `stale:`. A 503 before `ranking_engine`'s first message after the redeploy is not a
   failure; retry after a minute.
3. **Chart, each of the five.** Open `http://127.0.0.1:9100/chart/<id>` (e.g.
   `/chart/SOL-USD-PERP.HYPERLIQUID`) at 1 m. Expected: 1 m candles load, and the right-most bar
   is forming (it moves with the live price and closes on the minute). The REST half, per id:
   `curl -s "http://127.0.0.1:9100/api/candles/SOL-USD-PERP.HYPERLIQUID?before_ns=$(date +%s%N)&bar_seconds=60&limit=5"`
   returns non-empty `items`.
4. **No new collector error site, over one hour.** One hour after the redeploy, with `SINCE` the
   `date +%s%N` noted in check 1:

   ```bash
   curl -s "http://127.0.0.1:9100/api/errors?since_ns=$SINCE" | python3 -c 'import json, sys
   s = json.load(sys.stdin)["services"]
   for n in ("bybit_collector", "hyperliquid_collector"):
       print(n, s[n].get("since") if n in s else "MISSING: no ledger file for this service")'
   ```

   Expected: `{}` for both. `MISSING` fails the check: the service wrote no ledger file at all
   (DATA-07's `ERROR_LEDGER_DIR` mount), so a clean hour cannot be told from an unrecorded one. Otherwise, each site listed must already have fired in the hour
   before the redeploy, and its count must be root-caused (DATA-02). To check, run the same
   command with `since_ns` set to `SINCE` minus 3600000000000. A site's pre-redeploy count is that
   run's count minus this run's count. A site whose pre-redeploy count is 0 is new, and a new site
   on either service fails the check.
5. **The nightly verifies the new set.** The redeploy's own UTC day D is partial for
   `SOL-USD-PERP.HYPERLIQUID` (collected only from the redeploy), so judge the first full day,
   D+1, at the 03:07 UTC run of D+2 (the `archive` service runs the `BYBIT` and `HYPERLIQUID`
   sagas: rebuild, consolidate, candles, reconcile, prune). Expected, with `DAY` = D+1:
   `docker compose logs archive | grep -E "compare_klines (BYBIT|HYPERLIQUID) $DAY"` prints
   `instruments pass 4/4` for BYBIT and `instruments pass 1/1` for HYPERLIQUID; and

   ```bash
   docker compose exec archive python3 -c 'import os, sqlite3, sys
   for v in ("bybit", "hyperliquid"):
       path = f"/app/candles_dir/candles_{v}.db"
       if not os.path.exists(path):
           print(v, "MISSING: no candle store"); continue
       db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
       q = "SELECT instrument_id, status, mismatches FROM verified_days WHERE day = ?"
       for row in db.execute(q, (sys.argv[1],)): print(v, row)' "$DAY"
   ```

   prints the five ids, each `pass` with `0` mismatches (`verified_days` in
   `platform/data/candles/candles_<venue>.db`, the only day status). The ledger is flat for the
   five: the same `archive` service also runs the `DYDX` saga, so judge by id, not by site count:
   `grep -E '"reconcile\.(kline_mismatch|not_rebuilt|error)"' data/errors/archive.jsonl | grep
   "$DAY" | grep -E 'BYBIT|HYPERLIQUID'` prints nothing. Day D itself fails for the three
   Hyperliquid ids (see "Partial days" above); check its mismatches as described there.
6. **Only then stop dYdX.** `make down-dydx`. Expected: `docker ps -a --filter
   name=dydx-collector --format '{{.Names}}'` prints nothing (the container is removed, not only
   stopped, so a reboot cannot restart it). With the check-2 command: within about 30 s
   (`ranking/domain/board.py`'s `STALE_NS`) every `.DYDX` id leaves `items` and is listed under
   `stale:` (the page marks the feed stale instead of showing its last values as live), and
   after one hour (`RECENTLY_STALE_WINDOW_NS`) it is gone from `stale:` too. Never hidden early:
   a dYdX id that vanishes from both before it was listed stale fails the check (DATA-01).
   `ranking`'s dYdX volume poll keeps running; a venue with no fresh rows publishes none. In
   `make tui`'s Collector pane, the DYDX section turns stale (`~`) and its actions go nowhere,
   because no collector consumes `collector:control` any more. Expected: do not pin or start
   dYdX coins from it. The nightly after this day fails the down day for every `.DYDX` id (see
   "Partial days" above).
7. **A fresh boot starts Bybit and Hyperliquid only.** First check that `platform/.env` does not
   set `COMPOSE_PROFILES` to anything containing `dydx` (compose reads it, and it would enable the
   profile for every `make` target): `grep COMPOSE_PROFILES .env` prints nothing or no `dydx`.
   The same holds for the operator's shell: `env | grep COMPOSE_PROFILES` prints nothing or no
   `dydx` (an exported variable enables the profile just like `.env`, and overrides it).
   Then `sudo reboot`. After the reboot, before any `make`, `docker ps --format '{{.Names}}' |
   grep collector` prints `bybit-collector` and `hyperliquid-collector` (`restart: always`
   brought them back) and no `dydx-collector`. Then from `platform/` run `make up`. The same
   command must still print no `dydx-collector`.

Record when done (UTC), in this line: cutover run on `____-__-__`; last dYdX day collected
`____-__-__` (the UTC day of check 6's `make down-dydx`, itself partial).

**Retiring `DYDX` from the nightly.** `platform/archive/config.toml` keeps `DYDX` in `venues`
after the cutover. Its saga keeps verifying and pruning the archived dYdX days: the trade
retention deletes a `trade_tick` day only once it is `verified` and older than 7 days
(`archive/nightly.py`'s `_TRADE_RETENTION_DAYS`), and the plan retention deletes a coin's
order book deltas once older than its `retain_hours`. Let `L` be the last dYdX day collected, as
recorded above, and `R` the largest finite `retain_hours` in the dYdX plan, in whole days rounded
up (0 when no coin stores deltas with a finite window). Drop `DYDX` from `venues` only when all
three of these hold:

1. Today (UTC) is more than `max(8, R + 1)` days after `L`, so every dYdX day has passed both
   retention windows and been judged by the prune (the saga is the only job that prunes `.DYDX`
   files: once `DYDX` is dropped, whatever is left stays).
2. The last nightly's `archive` log has no `kept <.DYDX id> <day>: unverified` line. Every such
   day must first get its `verified_days` row, by a manual `make nightly VENUE=DYDX DAY=<day>`.
   The command below lists the dYdX days that are not `pass`:

   ```bash
   test -f data/candles/candles_dydx.db && docker compose exec archive python3 -c 'import sqlite3
   db = sqlite3.connect("file:/app/candles_dir/candles_dydx.db?mode=ro", uri=True)
   for row in db.execute("SELECT day, instrument_id, status FROM verified_days"
                         " WHERE status != ? ORDER BY day", ("pass",)): print(row)' \
     || echo "no dYdX candle store: nothing was ever verified; do not retire DYDX yet"
   ```

3. Every `kept <.DYDX id> <day>: failed` line is either the down day `L` (expected, see "Partial
   days") or a day whose mismatches have been root-caused (DATA-02).

Failed days keep their raw trades, and without the `DYDX` saga nothing revisits them. The files
stay and nothing is deleted. The drop is a commit, and it also changes
`tests/test_compose_profiles.py`'s `DYDX` assertion. Then run `docker compose restart archive`.

## Deferred operator actions

VPS steps a story needed that the operator chose to run later, in one batch at the next deploy
(decision 2026-09-26: stories record their VPS steps here instead of parking `awaiting-operator`,
so the bmad-loop chain never stops for them and every story still gets its independent review).
Run the entries oldest first; tick each one and add the date when done. A story that is still
parked on the board says so in its entry: run `bmad-loop confirm <story-key>` after its steps.

### 25-1b `archive` service (commit 35e155e004; marked done on the board 2026-09-26 at the operator's request, VPS steps still owed)

- [ ] On the VPS, pull this commit and run `make up` from `platform/` (it creates `platform/data/archive`
      and the rclone config dir, builds the collector image with rclone, and starts the new `archive`
      service).
- [ ] On the VPS, delete the old nightly crontab line (`crontab -e`), then confirm
      `crontab -l | grep -E 'make (nightly|consolidate|backup-catalog)'` prints nothing (section 1).
- [ ] When off-site storage exists: configure rclone (the remote in `~/.config/rclone`, or set
      `RCLONE_CONFIG_DIR` in `.env`; `RCLONE_REMOTE` and `RCLONE_BUCKET` in `platform/.env`), run
      `make backup-catalog` once and check that it exits 0, then set `backup_enabled = true` in
      `platform/archive/config.toml` and `make up` (section 5.4; replaced 2026-09-26 by Story 26.1b,
      which made the backup an explicit setting, off until then).
- [ ] After the first 03:07 UTC slot, open the dashboard (or `docker compose logs archive`) and confirm
      `archive:status` shows `last_run` with every venue's saga and `consolidate_catalog` (plus
      `backup_catalog`, once `backup_enabled = true`) at exit 0 or 2.

### 26-1 capture gate as aggregates (LiveBook / TradeIntake / FeedGroup / SecondSampler; commit: this story's)

- [ ] On the VPS, pull this commit and run `make up` from `platform/` (rebuilds the collector image
      and restarts `collector`, `bybit_collector` and `hyperliquid_collector`; no config, env var,
      compose service or bind mount changed).
- [ ] After 10 minutes, check `GET /api/errors` (or the three services' ledger files under
      `platform/data/errors/`) against the hour before the deploy: no new site other than
      `collector.empty_top`, and no rising `collector.resync`, `collector.book_sequence`,
      `collector.pending_deltas` or `collector.process` counts. `collector.resync` now also counts
      dYdX's forced resyncs (they were only a `dydx_collector.critical` log line before), and the
      CRITICAL `steady_state_crossed_book` line now comes from the `collector_core.critical` logger
      (`capture.critical` once Story 26.2 is deployed too, see its entry below).
- [ ] Confirm rows are still arriving for every venue (the web chart's live candles, or
      `snapshots:raw` in `redis-cli SUBSCRIBE snapshots:raw`), and that the dYdX incident reports
      still trigger on a crossed-book resync (`platform/data/incident_reports/`, when one occurs).

### 26-1b off-site backup as an explicit setting (commit: this story's)

- [ ] On the VPS, pull this commit and run `make up` from `platform/` (rebuilds the collector image
      and recreates `archive`, `data_api` and `bot_tui`; the committed `archive/config.toml` now
      carries `backup_enabled = false`; no env var or compose service changed, but the key is
      required, so a locally edited `config.toml` without it refuses start, `archive.config_invalid`).
- [ ] Confirm `docker compose logs archive` shows the one start WARNING `off-site backup disabled:
      the catalog has no copy off this host`, and that `GET /api/archive/status` (or the web
      maintenance panel / the TUI's archive line: `backup off`) says `"backup": "disabled"`.
- [ ] After the next 03:07 UTC slot, confirm `last_run` has no `backup_catalog` step and
      `platform/data/errors/archive.jsonl` gained no new `archive.backup_not_configured` line.

### 26-2-capture-package-and-venue-packages-with-entrypoints (`capture/` package, `python3 -m capture.venues.<v>`; commit 8c894e4aae)

- [ ] Before pulling, on the VPS: `git -C platform status --short -- bybit_collector/config.toml
      hyperliquid_collector/config.toml`. Both files moved to `platform/capture/venues/{bybit,
      hyperliquid}/config.toml` and the compose mounts now read the new paths. If either shows a
      local edit, save it (`git stash`), pull, and re-apply the edit to the new path
      (`git stash show -p | git apply` with the path adjusted, or by hand) before redeploying: a
      compose bind-mount source that does not exist is silently created as an empty *directory*,
      and the collector then refuses start on an unreadable config. dYdX's plan is unaffected
      (`platform/data/dydx_config.toml` still mounts at `/app/dydx_collector/config.toml`).
- [ ] Order: `git pull`; `make build-base` only if `nautilus_trader`/`crates` changed since the
      last base build (this story changes neither); then **one** `make redeploy-all` from
      `platform/`. It now rebuilds and restarts all three collectors -- `collector`,
      `bybit_collector` and `hyperliquid_collector` (before this story it restarted only
      `collector`, leaving the other two on the old image) -- plus `archive`, `ranking_engine`,
      `data_api`, `live-paper` and the `bot_tui` image.
- [ ] Service names are unchanged on purpose (decision recorded in `docker-compose.yml` above the
      `collector` service): `collector`/`dydx-collector` was **not** renamed `dydx_collector`,
      so Dozzle, `make logs`/`test`/`nightly`, the `~/.zshrc` helpers and the ledger file
      `data/errors/collector.jsonl` keep their names. `docker compose ps` must list the same
      containers as before, with no orphan.
- [ ] Dozzle, first 10 minutes, all three collectors: each logs `Started: N subscribed`, and
      `docker compose ps` shows their commands as `python3 -m capture.venues.{dydx,bybit,
      hyperliquid}`. Logger names changed
      with the move (the messages did not): the service's lines now come from
      `capture.application.capture_service` (was `collector_core.collector`), the
      `steady_state_crossed_book` CRITICAL from `capture.critical` (was `collector_core.critical`),
      the clients' from `capture.venues.<v>.client` (was `<venue>_collector.client`). A saved
      Dozzle filter on an old logger name needs the new one. The logs cannot show a stale
      old-path import (Python hides a `DeprecationWarning` raised outside `__main__`), so prove
      none exists instead: `docker compose exec collector python3 -W error::DeprecationWarning -c
      "import capture.venues.dydx.__main__, capture.venues.bybit.__main__,
      capture.venues.hyperliquid.__main__"` exits 0 (importing a `__main__` module by name runs
      no capture: its `if __name__ == "__main__":` guard is false).
- [ ] After 10 minutes, `GET /api/errors` (or the three services' `platform/data/errors/*.jsonl`)
      is flat against the hour before the deploy: no new site, and no rising
      `collector.open_interest_poll` (the dYdX and Bybit REST polls now run through
      `CaptureService.poll_loop`; the ledger site and detail strings are unchanged), `collector.resync`,
      `collector.process` or `collector.subscribe_failed`.
- [ ] Confirm rows still arrive for every venue (the web chart's live candles, or `redis-cli
      SUBSCRIBE snapshots:raw`), and that `custom_open_interest/` gains dYdX and Bybit linear rows
      at the poll cadence (`open_interest_poll_seconds`, 300 s).

### 26-3-closeout-shims-gone-spines-reconciled (last shims gone, `bots.dockerfile`; commit: see `git log --grep 26-3-closeout`)

Nothing moves on the host: every `platform/data/` path, compose service name, env var name,
ledger file and Redis key is unchanged. The four capture re-export shim packages are deleted (a
stale `python3 -m collector_core...`/`dydx_collector...` anywhere outside the repo now fails with
`ModuleNotFoundError`; in the `collector` container a bare `import dydx_collector` still resolves
to the empty namespace directory the plan mount creates, but any submodule of it does not), the live-paper image's dockerfile is renamed `platform/bots.dockerfile`,
and three container-side mount targets changed, so the affected containers must be recreated,
not just restarted.

- [ ] Before pulling, confirm nothing outside the repo (crontab, `~/.zshrc` helpers, ad-hoc
      scripts) names an old package path, a moved container path or the renamed dockerfile:
      `crontab -l | grep -nE 'collector_core|dydx_collector|bybit_collector|hyperliquid_collector|/app/live_paper|live_paper\.dockerfile' || echo clean`,
      and the same `grep -nE` over `~/.zshrc` and any operator scripts. Compose service names
      such as `bybit_collector` and the dYdX mount `/app/dydx_collector/config.toml` are
      unaffected; `python3 -m`/import paths, `/app/live_paper/...` (now `/app/data/live_paper/...`
      and `/app/strategy_source/strategy.py`) and `-f platform/live_paper.dockerfile` (now
      `bots.dockerfile`) need repointing.
- [ ] `git pull`; `make build-base` only if `nautilus_trader`/`crates` changed since the last base
      build (this story changes neither); then `make redeploy-all` from `platform/`. It rebuilds
      the collector image (now without the shim packages) and recreates `collector`,
      `bybit_collector`, `hyperliquid_collector`, `archive`, `ranking_engine`, `data_api` and
      `live-paper` (built from `bots.dockerfile`), and rebuilds the `bot_tui` image.
- [ ] `collector` still reads the dYdX plan at the unchanged mount `/app/dydx_collector/config.toml`
      (the image no longer carries a placeholder there, so a missing mount now fails at start
      instead of running an empty plan): in Dozzle, the first `collector` lines after start show
      the plan's instruments subscribing (`Started: N subscribed` with the same N as before).
- [ ] `live-paper`: fills now mount at `/app/data/live_paper` (host `platform/data/live_paper`
      unchanged, `FILLS_DB_PATH` follows it). After the recreate, `bot_tui`'s Bots pane shows
      every bot heartbeating and a bot's History view still lists its pre-deploy fills (same
      `fills.db`), and `GET /api/errors` shows no `bots.fill_lost` or `bots.history_refresh`.
- [ ] `bot_tui` (`make tui`): open a bot and press `v`; the strategy source still displays (the
      read-only mount moved to `/app/strategy_source/strategy.py`).
- [ ] After 10 minutes, `GET /api/errors` is flat against the hour before the deploy for all
      services (no new site).

### 29-1 Symbol and Exchange on the web rankings (commit: this story's)

- [ ] On the VPS, pull this commit and run `make redeploy-no-paper` from `platform/`. It rebuilds
      and restarts `ranking_engine`, which now publishes `symbol` on every `rankings:live` rank
      entry, and `data_api`, which serves the new rankings page. No collector, config, env var,
      compose service or bind mount changed.
- [ ] Open the web rankings page. Every row shows Symbol and Exchange between Rank and Instrument
      (e.g. `BTC` / `BYBIT · perp`), and clicking Symbol puts the same coin's rows from different
      exchanges next to each other.
- [ ] After 10 minutes, `GET /api/errors` is flat against the hour before the deploy (no new site).

### 29-2 Collector pane shows every venue's plan (commit: this story's)

- [ ] On the VPS, pull this commit and run `make redeploy-all` from `platform/` (`capture/`
      changed, so the three collectors must restart; `redeploy-no-paper` restarts none of them).
      It rebuilds the collector image, restarts `collector`, `bybit_collector` and
      `hyperliquid_collector` and rebuilds `bot_tui`: Bybit's and Hyperliquid's collectors now
      publish their static plan on `collector:status`, and every venue's aggregate gains `venue`,
      `cap`, `accepts_commands`, `min_liquidity_usd` and `last_apply`. No config key, env var,
      compose service or bind mount changed. `redeploy-all` also rebuilds and restarts
      `live-paper`: run it when no paper bot holds a position you care about, or restart only
      what this story changed: `docker compose up -d --build collector bybit_collector
      hyperliquid_collector` then `docker compose --profile tui build bot_tui`.
- [ ] `make tui`, then `:data`. Pub/sub keeps no history, so a section appears only at its
      collector's next publish: each publishes on startup and then every 30 minutes. Open the TUI
      within a minute of the restart, or wait up to 30 minutes. Expect three sections, `BYBIT`, `DYDX` and `HYPERLIQUID`, each headed
      `<VENUE>: N collected +P pending · cap C` with a `last apply` line. On a Bybit row, `p` shows
      `BYBIT: static plan: edit platform/capture/venues/bybit/config.toml` in the footer and opens
      no prompt; `p` on a dYdX row still opens the type-to-confirm prompt (`esc` to cancel).
- [ ] After 10 minutes, `GET /api/errors` is flat against the hour before the deploy (no new
      `collector.status_loop` site on Bybit or Hyperliquid).

### 29-3 Venue cutover (commit: this story's)

- [ ] Run §8 "Venue cutover" checks 1 to 7 in order: `make redeploy-all` with dYdX still running;
      the rankings, chart and `GET /api/errors` checks for the five new ids; the nightly marks the
      first full day `verified` for all five; only then `make down-dydx`, with dYdX's rankings rows
      ageing out as stale and then gone; a fresh boot plus `make up` starts Bybit and Hyperliquid
      only.
- [ ] Record the cutover date and the last dYdX day collected in §8's record line.
- [ ] For each partial day (day D for the three Hyperliquid ids, the down day for every `.DYDX`
      id), confirm the failed reconciliation's mismatches lie only on the uncollected side of the
      switch time (§8 "Partial days").
- [ ] Include `SOL-USD-PERP.HYPERLIQUID` in the owed >= 3 h Hyperliquid lag run (audit D-63,
      `python -m archive.tools.measure_lag --venue hyperliquid`): `hold_back_seconds` and
      `stale_book_seconds` in `capture/venues/hyperliquid/config.toml` were measured on BTC/ETH/PURR.
- [ ] Later, once §8's retirement check passes, drop `DYDX` from `platform/archive/config.toml`'s
      `venues` (a commit, then `docker compose restart archive`).
