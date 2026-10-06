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
`/app/preferences/` (`CHART_INDICATOR_CONFIG_PATH`/`SCREENER_COLUMNS_CONFIG_PATH` kept their
names; key sets unchanged) -- since Story 32.5 those two paths are one mounted directory,
`./data/preferences/` -> `/app/preferences/`, see the Deferred operator actions entry `32-5`. The ranking process now runs as `python3 -m ranking` (same compose
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
   (`archive.tools.normalize_snapshot_schema` itself was deleted in Story 30.2: its job is part of
   `archive.tools.migrate_snapshot_ints`, entry `30-2` under "Deferred operator actions".)
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

## 7. Capture CPU budget (Story 28.1)

Story 28.1 gives the collectors CPU priority and a memory ceiling in `docker-compose.yml`, and
makes each collector report its own hot-path figures once per periodic flush. What changed:
`cpu_shares: 1024` on `collector`, `bybit_collector` and `hyperliquid_collector`; `cpu_shares: 256`
on `archive`, `ranking_engine`, `data_api`, `bot_tui`, `live-paper` and `dozzle`; `redis` keeps the
default 1024 (it is on capture's publish path). There is no `cpus:` cap anywhere: the weights only
act while the cores are contended, and nothing is limited on an idle box. Each collector has a
`mem_limit` of its measured peak x 1.5 (`bybit_collector` 362m, `hyperliquid_collector` 248m,
`collector` 1690m; the evidence is the comment above each one). `tests/test_compose_cpu_budget.py`
pins all of it.

Known limit: the memory limits are sized for today's plans (Bybit 4 instruments, Hyperliquid 1,
dYdX ~25), and `collector:control` can grow a plan at runtime without a redeploy. Rough scaling is
~16 MiB per Bybit instrument, so the Bybit limit is reached at about 11 instruments; past it the
collector is OOM-killed and `restart: always` restarts it, a visible gap with `OOMKilled=true`.
Upgrade path: re-measure (`docker stats --no-stream` and the container's
`/sys/fs/cgroup/memory.peak`, x 1.5) and raise `mem_limit` **before** growing a plan; Story 28.2's
scale burst gives per-instrument figures.

**Redeploy, in this order** (from `platform/`):

1. `make build-base` only if `nautilus_trader` core or its dependencies changed since the last base
   build (this story changes neither), then `docker compose build collector` (the collector image
   carries the new capture code).
2. The collectors first, so capture is back before anything else restarts:
   `docker compose up -d bybit_collector hyperliquid_collector` (and `make up-dydx` only where the
   dYdX collector runs). `up -d` recreates each container whose `cpu_shares`/`mem_limit` changed.
3. Then the batch services: `docker compose up -d archive ranking_engine data_api dozzle`, plus
   `docker compose --profile live-paper up -d live-paper` where the paper fleet runs. `bot_tui`
   picks up its weight at its next `make tui`.
4. Confirm the weights and limits took: `docker inspect -f '{{.Name}} {{.HostConfig.CpuShares}}
   {{.HostConfig.Memory}} {{.HostConfig.MemorySwap}}' $(docker compose ps -q)` (collectors `1024` and their limit in bytes,
   batch services `256`, redis `0` = the default 1024; a collector's `MemorySwap` equals its
   `Memory`, i.e. no swap).

**Acceptance check (one hour after the redeploy):**

- `uptime`: the 1-, 5- and 15-minute load averages stay at or below the number of cores
  (`nproc`) over the hour.
- Dozzle, each collector over the same hour: zero `_second_loop tick arrived ... late` lines.
- OOM: `docker inspect -f '{{.Name}} OOMKilled={{.State.OOMKilled}} restarts={{.RestartCount}}'
  bybit-collector hyperliquid-collector` (and `dydx-collector` where it runs) prints
  `OOMKilled=false` for each. A `true` means that collector outgrew its `mem_limit`: raise it from
  a fresh measurement (the Known limit in `docker-compose.yml`), never live with the restarts
  (DATA-07).
- Rollback, if a collector OOM-loops on its new limit during the hour: raise that service's
  `mem_limit` and `memswap_limit` together in `docker-compose.yml` (or remove both lines) and
  `docker compose up -d <service>`; capture resumes with a coverage `restart` run, no data is
  deleted. The `cpu_shares` weights never need a rollback: they only act under contention.

**Reading the hot-path figures** (`docs/DATA_DICTIONARY.md` §1.25): every collector logs one
`hotpath: window_s=... queue_depth_max=... messages_processed=... wakes=... lag_max_ms=...
lag_p99_ms=... writes=... write_data_ms=... write_data_max_ms=...` INFO line per periodic flush
(every 60 s, at :02) in Dozzle, and keeps the
latest record in Redis: `docker exec dydx-redis redis-cli GET capture:hotpath:bybit` (or
`:hyperliquid`, `:dydx`); `docker exec -it dydx-redis redis-cli SUBSCRIBE capture:hotpath`
streams every venue's. A rising `queue_depth_max` is an ingest backlog (audit D-07), a
`lag_max_ms` in the seconds a stalled sample loop (D-10), a `write_data_max_ms` near the flush
interval a slow catalog write (the slowest batch; `write_data_ms` is the last one); a stale `ts` in the key is a collector that stopped reporting.
`collector.hotpath_publish` in `GET /api/errors` means the record could not be published (the log
line is still written, Parquet is unaffected).

## 8. Venue cutover: Bybit and Hyperliquid proven, then dYdX stopped (Story 29.3)

Collection moves off dYdX (operator decision 2026-09-26). Bybit keeps `BTCUSDT-LINEAR.BYBIT`,
`ETHUSDT-LINEAR.BYBIT`, `BTCUSDT-SPOT.BYBIT` and `ETHUSDT-SPOT.BYBIT`; Hyperliquid collects
`SOL-USD-PERP.HYPERLIQUID` instead of BTC/ETH (both reversible by config:
`platform/capture/venues/{bybit,hyperliquid}/config.toml`). The dYdX compose service `collector`
is behind the `dydx` profile: `make up`, `redeploy`, `redeploy-all` and `redeploy-no-paper` never
start it, `make up-dydx` starts it and `make down-dydx` removes it. Nothing here deletes data.
Since 2026-09-30 the dYdX collector starts **without** its Rust `[WS_RAW]` raw-frame file sink
(`DydxConfig.ws_raw_sink`, default `false`): the sink formatted and wrote every WebSocket frame
inside the collector process (~1 MB/s at 25 markets), and its only reader is the incident
reports' 12 s raw window. `make up-dydx` therefore comes up lean; an incident report written
while it is off says "Raw WS sink disabled" instead of attaching evidence. To get the window back
for an investigation, add `ws_raw_sink = true` to `platform/data/dydx_config.toml` and restart the
collector (`make up-dydx`); remove the line and restart to switch it off again. The key is read
once at process start, not by the 30 s plan reload.
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
   because no collector consumes dYdX's `collector:control` messages any more; once its last
   status is over an hour old the pane refuses them with the reason (`no collector:status for
   over 60 min`, Story 29.4). Expected: do not pin or start dYdX coins from it. The nightly after this day fails the down day for every `.DYDX` id (see
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

### 27-8-candle-pattern-strategy-backtests-and-live-paper (`candle_pattern` paper bots; commit: see `git log --grep 27-8-candle`)

The live-paper image now also copies `research/` (a `candle_pattern` bot's strategy is loaded from
it by string path), and `bot_tui` reads each bot's strategy source from
`/app/strategy_source/<Class>.py`: two new read-only mounts replace
`/app/strategy_source/strategy.py`.
The checked-in `platform/bots/config.toml` is unchanged: every bot there stays `dummy`, so the
redeploy alone changes no bot's behaviour.

- [ ] `git pull`, then rebuild the live-paper image (its `COPY` set changed) and recreate it:
      `docker compose -f platform/docker-compose.yml --profile live-paper build live-paper` and
      `make up-live-paper` from `platform/`. In Dozzle the `live-paper` start shows every bot
      `RUNNING` exactly as before.
- [ ] Restart `bot_tui` (`make tui`; its mounts changed). Open any dummy bot and press `v`:
      `DummyStrategy`'s source shows (a "could not read /app/strategy_source/DummyStrategy.py"
      line means the new mount is missing).
- [ ] Add one paper bot to `platform/bots/config.toml` (a new `bot_id`; the other bots keep
      theirs), e.g. `strategy = "candle_pattern"` on `BTC-USD-PERP.DYDX` with a
      `[bots.params]` table as in `bots/README.md` ("Choosing a bot's strategy"), then
      `make up-live-paper`. The start must not fail naming that bot (a typo'd params key does).
- [ ] In `bot_tui`, the new bot heartbeats with strategy `CandlePatternStrategy`, and `v`
      shows the source mounted at `/app/strategy_source/CandlePatternStrategy.py` (the host's
      `platform/research/strategies/candle_pattern_strategy.py`). Leave it running until it logs a fill (a pattern has to
      fire and pass the trend filter, which can take hours on 1-minute bars); confirm the fill in
      its trades blotter and its position side, and that `GET /api/errors` shows no new `bots.*`
      site. The strategy's own failures are not ledgered (a strategy module imports only `kernel`
      and `nautilus_trader`), so also search the `live-paper` log in Dozzle for
      `CandlePatternStrategy` `ERROR`/`WARN` lines: a refused or lost stop, "no stop price", a
      stop "still open while flat", or a `trade_size` refused at start.
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

### 29-4 Runtime collection control for Bybit and Hyperliquid (commit: this story's)

- [ ] Before redeploying, check the two plan files are writable by the collectors' uid 1000 (their
      config mounts are `:rw` now, and the plan store rewrites them in place):
      `stat -c '%u %n' capture/venues/bybit/config.toml capture/venues/hyperliquid/config.toml`
      prints `1000` for both, else `chown 1000 <file>`. A file uid 1000 cannot write makes every
      command fail its save (`collector.control` "plan save failed", plan unchanged).
- [ ] If the `dydx` profile is still up (`docker compose ps collector` lists it), move it onto
      this image first: `make up-dydx` right after `make redeploy-all`, before any command is
      sent from a new `bot_tui`. `redeploy-all` builds the image but never restarts `collector`,
      and a pre-29.4 dYdX collector ignores `venue` and does not check an id's venue, so it
      would add a `:start SOLUSDT-LINEAR.BYBIT` to `data/dydx_config.toml` as well.
- [ ] `make redeploy-all` (rebuilds and restarts `bybit_collector` and `hyperliquid_collector`).
      Expected in `make tui`'s Collector pane within a minute: the BYBIT and HYPERLIQUID headers
      read `· no cap`, and their `p`/`x`/`:start` are no longer refused as a static plan.
- [ ] On each venue, `:start` one new coin (e.g. `:start SOLUSDT-LINEAR.BYBIT`, `:start
      ETH-USD-PERP.HYPERLIQUID`): its row appears `pending`, then turns collected within a few
      seconds, and the section's last-apply line lists it as subscribed. `GET /api/errors` shows no
      new `collector.subscribe_failed` or `collector.control` for it.
- [ ] `x` (stop) that coin on each venue: its row drops from the pane at once, and on the web
      rankings (filter by Exchange) its row turns stale within about 30 s and is gone an hour later
      (never hidden early, DATA-01). Its catalog files stay (`ls data/catalog/data/*/<id>*`).
- [ ] Confirm dYdX ignores the new commands: `docker compose logs collector` (when the `dydx`
      profile is up) shows no WARNING for the Bybit/Hyperliquid ids.
- [ ] The commands rewrote the committed files: `git status` on the VPS lists
      `platform/capture/venues/bybit/config.toml` and `.../hyperliquid/config.toml` modified (their
      comments are gone). Commit the new plan back from the desktop (copy the ids into the
      committed file, comments kept, and update `tests/test_committed_config.py` if the decided
      set changed), then bring the VPS checkout to it (`git checkout -- <file>` then `git pull`,
      or `git pull` after a commit of the VPS change). Each plan file is a single-file bind
      mount, which follows the inode: `git checkout`/`git pull`, `sed -i` or an editor that saves
      by rename *replace* the file, the running collector never sees the new one, and its later
      command saves land in the orphaned inode and are lost. So after any such replacement run
      `docker compose restart bybit_collector hyperliquid_collector` (and `collector` for
      `data/dydx_config.toml`); only an in-place write (`cat new > file`, an editor writing in
      place) reaches the 30 s hot reload. Do this before every `git pull` on the VPS, which
      otherwise refuses to overwrite the modified file. A replaced file is owned by whoever ran
      git, so re-run the uid check of this entry's first item after it (`chown 1000 <file>` if
      needed), or every later command fails its save.
- [ ] A Hyperliquid coin that would take the collector past the venue's 1000 channels per IP
      (`HYPERLIQUID_MAX_WS_CHANNELS`, counting only this collector's own channels) stays `pending`
      with a `collector.subscribe_failed` ledger entry per retry naming the limit: that is the
      expected refusal, not a fault. With `live-paper` also on Hyperliquid from the same IP, the
      real budget is smaller than the collector counts.
- [ ] Watch the first reconnect of each collector after a larger plan change: the Rust clients
      replay every held subscription unpaced (a `Known limit:` in both clients). `GET /api/errors`
      shows no subscribe errors and the pane no lasting `pending` rows afterwards.

### 29-5 Market browser: search a venue's coins by name and add them (commit: this story's)

- [ ] `make redeploy-all` (rebuilds and restarts `ranking_engine`, which now publishes
      `markets:live`, and `bybit_collector`/`hyperliquid_collector`, whose aggregates now carry
      `last_refusal`; it also rebuilds `bot_tui`'s image). If the `dydx` profile is up, `make up-dydx`
      right after, so dYdX's aggregate carries `last_refusal` too.
- [ ] Within a minute, `redis-cli subscribe markets:live` shows one message per venue with a fresh
      volume source (`BYBIT`, `DYDX`, `HYPERLIQUID`), each `{"venue", "ts", "markets": [...]}` with
      only `instrument_id`/`symbol` per entry. `GET /api/errors` shows no `ranking_engine.markets`.
- [ ] `make tui`, `:data`, then `/` and type `sol`: every venue's SOL markets are listed, grouped
      per venue under `<VENUE>: N collected +P pending · no cap · M matches` (dYdX: `· cap 30`), and
      the ids already collected read `collected`. Before the first message (within a minute of the
      restart) the browser reads `waiting for markets:live…`.
- [ ] One add on Bybit: `Enter`, highlight an uncollected `...-LINEAR.BYBIT` id, `a`, type `add` +
      `Enter`. The footer reads `sent: start <ID>`, the row reads `pending` and then `collected`
      within a few seconds, the Collector pane's BYBIT header count goes up by one without
      reopening the TUI, and `GET /api/errors` shows no new `collector.control` or
      `collector.subscribe_failed` for it. Then `x` that coin on the Collector pane if it is not
      wanted (its row drops; catalog files stay).
- [ ] `a` on a `collected` row shows `cannot add <ID>: already collected` in the footer and sends
      nothing (`docker compose logs bybit_collector` shows no new command).
- [ ] The new coin rewrote `platform/capture/venues/bybit/config.toml`: handle the VPS checkout as
      in 29-4's "The commands rewrote the committed files" item.

### 30-1-compact-parquet-encoding-for-consolidated-and-rewritten-files (compact write settings + `archive.tools.recompress`; commit: see `git log --grep 30-1-compact`)

Every archive merge and rewrite now writes `compact_write_options` (zstd 16, delta-packed
timestamps; `docs/DATA_DICTIONARY.md` §6). New files get it from the next nightly on; the files
consolidated before the deploy keep the old encoding until this one-off run.

- [ ] `git pull`, then `make up` from `platform/` (rebuilds the collector image the `archive`
      service runs; no config, env var, compose service or mount changed).
- [ ] Report first (no lock, nothing written; one level-16 encode per file, so it takes minutes
      on the whole catalog), in the `archive` service's image and mounts so failures are ledgered
      as `archive`: `docker compose run --rm --no-deps archive python3 -m
      archive.tools.recompress --catalog /app/catalog`. Note each type's bytes before -> after.
- [ ] Outside the `archive` service's nightly and intraday slots (`archive/config.toml`), run it
      for real: the same command with `--apply` (add `--venue V` / `--type T` to split it into
      smaller runs). Exit 0 expected; exit 1 means another maintenance run held the lock (rerun
      later), exit 2 means files failed: each is a `recompress.error` line in
      `platform/data/errors/archive.jsonl`, left as it was -- investigate before rerunning.
- [ ] Rerun the report: every closed file must count as "already compact" and 0 remain in scope.
- [ ] Record the `--apply` run's per-type and total bytes before -> after (and its wall time and
      peak RSS) in `docs/DATA_INTEGRITY_AUDIT.md` row D-68, and set its status to FIXED.
- [ ] After the next nightly, check `archive:status`: `consolidate_catalog` exits 0, and
      `GET /api/errors` shows no new `consolidate.*` or `rebuild.verify` site (the read-back now
      compares every value, so a refused merge would surface there).

### 30-2 second snapshot as exact integers (commit: this story's -- `git log --grep 30-2-second-snapshot`)

The `DydxSecondSnapshot` Parquet layout and the `snapshots:raw` Redis payload change together, from
floats to exact integer units. There is no float read path: every
reader refuses a float-layout file (`LegacySnapshotLayoutError`, naming the migration) and every
consumer refuses a float payload (layout: `docs/DATA_DICTIONARY.md` §1.7). So the collectors
switch layout at a UTC midnight (no day mixes layouts), the closed float days are migrated, and
only then do the readers start again.
`snapshots:raw` is Redis pub/sub, not a stream: there is nothing queued to drain or trim.

- [ ] Take a catalog backup first (`make backup-catalog` if off-site storage is configured, else a
      local copy of `platform/data/catalog` on a disk with room for it).
- [ ] Before 00:00 UTC: `git pull`, then build without restarting (`docker compose build` from
      `platform/`). Stop the readers: `docker compose stop ranking_engine data_api live-paper
      archive bot_tui` (`data_api` also runs the alerting; `bot_tui` reads no snapshot field but
      is rebuilt with the rest).
- [ ] Just before 00:00 UTC stop the three collectors (`docker compose stop collector
      bybit_collector hyperliquid_collector`; dYdX's only if it runs, `make up-dydx` profile), and
      start them right after 00:00 UTC on the new image (`docker compose up -d bybit_collector
      hyperliquid_collector`, plus `make up-dydx` if dYdX is collected). From here on the new day
      is written in the integer layout only.
- [ ] Report first (no lock, nothing written; every closed float file is converted and checked
      in memory): `docker compose run --rm --no-deps archive python3 -m
      archive.tools.migrate_snapshot_ints --catalog /app/catalog`. Note each venue's files, rows,
      snapped values and bytes before -> after. Any `migrate_snapshot_ints.off_grid` or
      `.error` line in `platform/data/errors/archive.jsonl` is investigated before applying (an
      off-grid value is a real digit finer than the instrument definition, never rounded; an
      error names e.g. a row older than every stored definition). The report does the whole
      conversion and decode-back check in memory, so it costs as much CPU as `--apply` (about
      10 s per instrument-day on the dev box): on the 2 vCPU VPS run it per `--venue` and expect
      it to take as long as the apply will.
- [ ] Apply: the same command with `--apply` (optionally one `--venue V` at a time). Exit 0
      expected; 1 = the lock is held (rerun later); 2 = refused/failed files, each ledgered and
      left as it was. With the collectors stopped before 00:00 UTC no float file reaches today, so
      the report's open-day count is 0; a non-zero count means a float file holds today's rows
      (the collectors ran past midnight on the old image): readers refuse it, so keep them
      stopped, or accept its window failing loudly, until the `--apply` rerun the next day takes
      it.
- [ ] Rerun the report: every closed file counts as "already integer", 0 in scope.
- [ ] Start the readers: `docker compose up -d archive ranking_engine data_api live-paper bot_tui`.
      Check the web chart (Lines mode shows bid/ask) and the rankings update, and
      `GET /api/errors` shows no `ranking_engine.snapshot_entry`, `live_candles.decode` or
      `views.*` site rising.
- [ ] For every day the nightly or a manual rebuild ledgered `rebuild.legacy_layout` (a day that
      was still float when the rebuild ran), rerun `python -m archive.rebuild_seconds --day D
      --apply` for it (or let the next nightly's missed-day catch-up do it).
- [ ] Record the `--apply` run's per-venue and total files, rows, snapped values and bytes before
      -> after in `docs/DATA_INTEGRITY_AUDIT.md` row D-69 and set its status to FIXED.

### 31-2 Every drop counted, ledgered and explainable (commit: the `story 31-2-every-drop-counted-ledgered-and-explainable` commit on `troll`)

The collectors now write the coverage record `<catalog>/../coverage/<venue>.jsonl`
(`docs/DATA_DICTIONARY.md` §1.16) through a new bind mount, `./data/coverage:/app/coverage`, on
`collector`, `bybit_collector` and `hyperliquid_collector`. They also judge trade staleness on
arrival, seed the dedup window from the archive at start, and ledger every site that only logged
before (audit D-70..D-73). No config key or env var changed. If the directory is missing, Docker
creates it root-owned: the uid-1000 collectors then cannot append, and every flush ledgers
`collector.coverage_write` while the lines pile up in memory (bounded at 10 000, then lost).

- [ ] On the VPS, before the redeploy: `mkdir -p platform/data/coverage && sudo chown 1000:1000
      platform/data/coverage` (`make up` creates the directory, but as the invoking user, so the
      `chown` is what matters on a box where that user is not uid 1000).
- [ ] `git pull`, then `make up` from `platform/`. It rebuilds the collector image and recreates
      `bybit_collector` and `hyperliquid_collector` with the new mount. If dYdX is collected,
      also run `make up-dydx`, which creates `data/coverage` too and recreates `collector`.
- [ ] After the first minute: `ls -l platform/data/coverage/` shows `bybit.jsonl` and
      `hyperliquid.jsonl` (plus `dydx.jsonl` if it runs) owned by 1000. Each start writes a
      `restart` run per instrument (the seconds since the last archived row), matched by one
      `collector.restart_gap` ledger line naming each instrument and span. After that the file
      grows only when a second has no row or a trade is dropped/backfilled: expect a few lines
      after a restart and near nothing in steady state (`wc -l` over an hour).
- [ ] Check `GET /api/errors` (or `platform/data/errors/<service>.jsonl`) against the hour before
      the deploy. `collector.second_rejected` is expected right after start (`no_book` until each
      book's first snapshot), then only for real rejections. `collector.stale_trade` must be rare
      (a venue replay at subscribe/reconnect). A steady rate means trades arrive more than
      `stale_trade_seconds` old and is a finding. `collector.coverage_write`,
      `collector.crash` and `collector.ohlc_outside_book` must be absent. Investigate any
      `collector.unknown_message` (its detail names the type), because these messages used to be
      dropped silently. Do the same for `collector.open_interest_poll` lines naming malformed
      rows: on dYdX the poll is venue-wide, so every market without an `openInterest` is named
      each round.
- [ ] Confirm that `collector.dedup_seed` shows no dedup-seed read failure
      (`archived trade ids could not be read for the dedup seed`) after the restart.

### 31-3 Derived signals against independent reference implementations (commit: the `feat(31-3)` commits on `troll`)

Changes what `ranking_engine` publishes and stores (`price` is the mid or null, `cvd` null on an
empty window, `spread` rounded to the row's precision, `pct_1h`/`pct_24h` null when a gap would
shorten them, `volatility` over 24 h, no mark-price backfill), what `data_api` serves (1W panes on
Monday-anchored 1W buckets, `micro` null when undefined, `/api/metrics/nearest` bounded at 120 s,
1W forming bars on Monday, the picker's custom-indicator replay capped at 7 days) and the
web labels/docs (audit D-77..D-90). No config key, env var,
schema or mount changed.

- [ ] `git pull`, then from `platform/` rebuild and recreate `ranking_engine` and `data_api`
      (`docker compose up -d --build ranking_engine data_api`); `data_api`'s image rebuild also
      rebuilds the frontend (its Node build stage).
- [ ] After the first minute: the rankings page shows "Vol 24h σ (trade closes)" and "Vol 1h σ
      (mids)" headers; a coin with no two-sided book yet shows "—" for Price and CVD, never 0 or a
      trade close; `GET /api/errors` shows no new `ranking_engine.*` or `views.*` site.
- [ ] On the chart, switch to 1W: the bars and the indicator panes start on Mondays (00:00 UTC)
      and share one time axis.

### 31-9 Live, backtest and display parity, incl. the bot's signals (commit: this story's)

Changes what `ranking_engine` stamps (`metrics.db` `ts` is taken when the board is read, D-130) and
how `data_api` computes `/api/snapshots` `mid` (`kernel.indicators.mid_price`, bit-equal, D-131);
adds the opt-in bot signal log (`BOT_SIGNAL_LOG_DIR`, unset on the live stack: nothing changes
there), the verify paper fleet and the `bots.signal_replay` / `verification.bot_parity` tools. No
config key, schema or mount of the production stack changed.

- [ ] On the VPS, `git pull`, then from `platform/` rebuild and recreate `ranking_engine` and
      `data_api` (`docker compose up -d --build ranking_engine data_api`). Check `GET /api/errors`
      shows no new `ranking_engine.*` or `views.*` site after the first minute.
- [x] Verify stack (dev box), before Story 31.11's runs: rebuild and recreate only the verify
      `ranking_engine` and `data_api` (`REDIS_PORT=26379 DATA_API_PORT=29100 DOZZLE_PORT=28080
      docker compose -p verify -f docker-compose.yml -f docker-compose.verify.yml up -d --build
      --no-deps ranking_engine data_api`; never the collectors or recorders). Their images date from
      2026-09-29 12:59, before Stories 31.2/31.3/31.9, so the SSOT live variant still accounts the
      old `slow_loop_reads_its_clock_first` stamping and unrounded spreads. Then re-run
      `VERIFY_STACK=1 CATALOG_PATH=data/catalog python3 -m pytest -o addopts="" --rootdir=.
      verification/tests/test_ssot_trace.py` and, if it passes with no pre-D-130 row, delete the
      accounted row (it is kept only while the old image runs).
      **Done by Story 31.11** (rebuilt 2026-09-30 19:04Z, not logged, and again 2026-10-01
      15:47:50Z, a `deploy` line in `data/verification/chaos/scenarios.jsonl`; neither touched a
      collector or recorder): the row is deleted, the live
      variant passes, and the publish-timing residue it surfaced is the accounted
      `board_changed_before_its_publish` (audit D-130).
- [ ] **Decide: Bybit bot quotes and spot book (audit D-133, D-134, OPEN, FORK-01).** With the
      depth-50 book and quotes subscribed together (`DummyStrategy` does), the pinned Nautilus Bybit
      data client builds every quote from each book message's first entries, not the best level
      (live microprice a median 46.1 / 17.0 / 69.1 / 17.8 ticks from the book mid on BTCUSDT-LINEAR /
      ETHUSDT-LINEAR / BTCUSDT-SPOT / ETHUSDT-SPOT; Hyperliquid 0.5), and on spot it also replays the
      depth-1 quote stream into the book (10 levels a side on only 1.7 % / 1.6 % of spot cycles).
      The Sandbox fills on those quotes. Options: (1) upgrade Nautilus to a release whose Bybit
      handler emits quotes only from the quote topic and books only from the book topic (check the
      changelog; report upstream if none) -- **recommended**, together with D-113 below and the Sandbox
      stale-trade limit D-132;
      (2) run Bybit bots without the book-delta subscription until then (changes what
      `DummyStrategy` computes, so it is your call); (3) keep Bybit bots out of any paper or live
      evaluation. Until one is taken, no Bybit dummy-bot result is trustworthy. Record the choice in
      D-133/D-134.
- [ ] **Decide: `DummyStrategy` gating and OFI reset (audit D-129; D-82's deferred half).** It feeds
      an ungated book (no stale, crossed or gap check) and never `clear_prev_state()`s its OFI across
      a gap over `OFI_GAP_NS`. Measured over the verify fleet's 66.5 min against its catalog replay
      (`verification.bot_parity`, 5 bots, 3,988 book cycles each): 0 `gap` cycles, 0
      `book_skipped`, 0 decision disagreements, 0 unexplained apart from D-135's one replay-input
      cycle per bot (a replay-side conversion defect, not gating) -- no divergence observed, but no entry
      fired on either side in that window (every decision `none`/`not_ready`). Options: (1) adopt the
      upgrade path in `bots/strategies/dummy.py`'s `Known limit:` (skip and log a stale, crossed or
      gapped book; reset the OFI when consecutive fed books are more than `OFI_GAP_NS` apart, as
      `SnapshotStrategy`) -- **recommended**, so live, backtest and the ranking agree by construction;
      (2) keep it as the documented Known limit. Record the choice in D-129.
- [ ] **Decide: `IndexPriceUpdate` has no Nautilus catalog decoder (audit D-113, OPEN).** A stored
      index price never reaches a backtest, a bounded `catalog.query` or `BacktestDataConfig`
      (`verification.catalog` reads every index file `open_failed`). Options: (1) upgrade Nautilus to
      a release that decodes `IndexPriceUpdate` (the same upgrade as D-133) -- then the catalog
      verdict's `open_failed` must read 0; (2) accept it as a documented Known limit: index prices are
      read only through `kernel.catalog_files.query_index_prices`, never by a backtest. Record the
      choice in D-113; Story 31.11 records the verdict.
- [ ] **Decide: the bot replay's per-row delta order (audit D-135, OPEN).** `ParquetDataCatalog`'s
      `ORDER BY ts_init` returns one stored row's 41 equal-`ts_init` deltas unordered where they
      straddle a row-group boundary, so that replay book misses a level (1 of 3,988 cycles per bot
      on the 2026-09-30 run; `verification.bot_parity` fails it as `replay_input`). The fix changes
      the Story 31.9 conversion contract (`ts_event = ts_init =` the row's `ts_init`, `CLEAR` + every
      level). Options: (1) stamp a row's deltas `ts_init + i` ns, `CLEAR` first -- **recommended**
      (smallest change, the book completes 40 ns after the row's `ts_init`); (2) order-independent
      deltas (update/add every stored level, delete the previous row's levels not in this one);
      (3) wait for an upstream stable tie-break (FORK-01). Record the choice in D-135, then re-run
      `bots.signal_replay` and `verification.bot_parity` on the kept logs: `replay_input` must be 0.
- [ ] The verify `live-paper` now mounts only `data/verification/bot_signals/live` (not the whole
      `data/verification`, the recorders'); `make verify-up` creates it. On the next start of the
      fleet, recreate the container (`--no-deps live-paper`) so the new mount applies.
- [ ] Leave `verify-live-paper` stopped until needed (Story 31.9 stopped it at 2026-09-30 07:50:27Z
      after its 66.5 min run). It is in `VERIFY_SERVICES`, so the next `make verify-up` restarts it
      and its signal logs keep growing (~86 MB per bot-day, the Known limit of the signal log).

### 31-10 Fault injection: every loss accounted for (commit: this story's)

Changes what the collectors do after a restart (each instrument's trade baseline is seeded from the
archive and the restart gap is backfilled, `restart: archived baseline`, D-61) and how the
collectors' and the archive's status buses publish after a Redis restart (one retry on a
`ConnectionError`, D-136); adds `verification.chaos` and the windowed `verification.conservation`
(`--start/--end`). No config key, schema or mount of the production stack changed.

- [ ] **`network_cut` on the verify stack (needs sudo; the agent has none).** From `platform/`, with
      the verify stack up and the recorders recording: `sudo -v` first (the tool uses `sudo -n` and
      refuses otherwise, inserting nothing) and `docker stop verify-live-paper` (a uid-1000 venue
      client the rule would cut too; the tool refuses while it runs), then `VERIFY_DATA_DIR=data/verification
      CATALOG_PATH=data/catalog ERROR_LEDGER_DIR=data/errors python3 -m verification.chaos
      --scenario network_cut --venue BYBIT`, wait 6 min, the same with `--venue HYPERLIQUID`. After
      the next full hour plus 10 min, run `--evaluate --venue BYBIT` and `--evaluate --venue
      HYPERLIQUID`: exit 0 expected (Bybit `backfilled` > 0, Hyperliquid `unrecoverable` > 0, D-48).
      Check afterwards that `sudo iptables -S OUTPUT | grep verify-chaos` (and `ip6tables`) prints
      nothing. A mismatch is a finding: register it in DATA_INTEGRITY_AUDIT.md.
- [ ] On the VPS, `git pull`, then from `platform/` rebuild and recreate the collectors and the
      archive (`docker compose up -d --build bybit_collector hyperliquid_collector archive`). Check
      `GET /api/errors` after the first minute: each collector ledgers one `collector.restart_gap`
      and one `collector.trade_backfill` per feed request with reason `restart: archived
      baseline` (Bybit: `linear` and `spot`; each instrument backfilled once), and no
      `collector.dedup_seed`.
- [ ] **Decide D-137 (OPEN): a failed catalog flush discards a batch it could still write.** One
      failed :02 flush cost 1,542 Bybit trades and ~60 s of rows per instrument on the verify stack
      (ledgered and explained, never silent). Recommended: a follow-up story that keeps a failed
      batch and retries it at the next flush, bounded by the buffer's memory cap, noting
      `write_failed` only for what is finally dropped. Alternative: keep today's behaviour as a
      documented `Known limit:`.
- [ ] Story 31.11: exclude every window in `data/verification/chaos/scenarios.jsonl` from the clean
      soak (2026-09-30 09:50:48-11:05:25Z, 16:29:07-16:50:02Z and 17:06:48-17:07:48Z, each plus its 90 s / 180 s
      margins), and the verify stack's own stop 11:23-16:00Z (not a scenario: the run was stopped).

### 31-11 The verification run, the report and the permanent nightly gate (commit: this story's -- `git log --grep 31-11-verification-run`)

What this story changes:
- **The nightly saga gains a last step, `archive.verify_day`** (`docs/DATA_DICTIONARY.md` §1.24,
  §6). It is in the `archive` image.
- **The archive scheduler persists and publishes `verification_days`** (`state.json`,
  `archive:status`). An old `state.json` loads with none.
- **Production impact: none.** The production compose file is unchanged, so on the VPS the step
  finds no `VERIFY_DATA_DIR` and records `no reference data`, exit 0, for every venue. It runs no
  verifier, ledgers nothing, and touches no pruning or watermark.
- **Verify stack:** only `docker-compose.verify.yml` gives the `archive` service the recorders'
  data, the coverage record, the venue configs and the loopback data_api. `make verify-up` creates
  `verification/raw` and `verification/scratch`.
- **Where it runs: the verify stack (the reference recorders, the `verify-` collectors and
  `verify_day`) is a dev-box tool, not a production service.** Nothing of it is deployed to the
  VPS: the production compose file never sets `VERIFY_DATA_DIR`, so production's nightly records
  `no reference data` and stops. It runs on the desktop (`make verify-up`, its own checkout and
  `data/`), where every verdict in `docs/VERIFICATION_REPORT.md` was produced.
  `Known limit:` its cost is linear in the plan's instrument count. Measured on 4 Bybit + 1
  Hyperliquid instruments: the recorder ~1.2 % of a core and ~200 MB/day of raw files per Bybit
  instrument (RAM flat, ~80 MiB); `verify_day` ~7 min of wall time per Bybit instrument (RAM
  flat, ~1.5 GB peak: each tool judges one instrument at a time, in its own child). The six
  tools and their instruments run strictly in sequence, so more cores do not help. One tool
  passes `TOOL_TIMEOUT_S` (50 min) at about 7 Bybit instruments, and the nightly's
  `step_timeout_minutes` (360) is passed at about 50; past either the day is `refused`, never
  judged. The recorded set is, by invariant, the verify stack's collector plan
  (`verification/domain/plan_file.py`), so keep that stack's venue configs to a handful of
  representative instruments. Upgrade path: a per-instrument worker pool in the tools (memory
  then scales as workers x one instrument's working set) and a per-run verification budget.
  Why never the VPS: a 1.5 GB child next to the collectors on a 3.7 GB host is the OOM DATA-07
  forbids, and a multi-hour step holds the maintenance lock.

- [ ] **VPS rollout of Epic 31's capture, archive and display fixes.** On the VPS, `git pull`, then
      from `platform/`:
      `docker compose up -d --build bybit_collector hyperliquid_collector archive ranking_engine data_api`.
      This one rebuild covers 31-2 (coverage record, stale filter, dedup seed: do 31-2's
      `mkdir`/`chown` of `data/coverage` first), 31-3 (`ranking_engine`/`data_api`), 31-5 (D-96
      zero-level snapshot re-baseline, D-97 the REST cross-check's missing-level direction:
      collectors), 31-8 (D-118 1W, D-119 the served `partial` flag, D-120 the nightly steps'
      durable ledgers, D-123: `archive`/`data_api`), 31-9 (D-130, D-131) and 31-10 (D-61 restart
      backfill, D-136 status retry). Run each of those entries' checks after the first minute. The
      next nightly's summary line ends `verify_day ok`, and `archive:status` shows
      `"verification_days": {"DYDX": {...: {"verification": "no reference data", ...}}, ...}`.
- [ ] **Rebuild each venue's candle store on the VPS after that rollout (audit D-145).** Until the
      new `archive` image runs, every nightly `build_candles --day D` rebuilt D-1 from the one
      snapshot file crossing D's midnight, so a closed day older than the newest one can hold only
      its last minute of bars. After the rollout above, from `platform/`, for each of `bybit` and
      `hyperliquid` (and `dydx` if its collector ran):
      `docker compose exec archive python3 -m candles.rebuild --catalog /app/catalog --db /app/candles_dir/candles_<venue>.db --venue <VENUE> --workers 1`
      (the whole catalog span; today is skipped by the tool). Check: for an instrument, the 1d bar
      of each closed day has `seconds_observed` near 86,400 (`sqlite3 data/candles/candles_<venue>.db
      "select date(t/1000,'unixepoch'), seconds_observed from candles where bar_seconds=86400"`).
- [ ] **Decide where the reference recorders run (audit D-138).** Measured on the dev box:
      - **Recorders:** Bybit 71-83 MiB and 4-5.5 % of one core; Hyperliquid 53-62 MiB and
        0.1-0.3 %.
      - **Raw disk:** Bybit ~840 MB/day (70.2 MB over 2026-09-30 17:00-19:00Z), Hyperliquid
        ~40 MB/day. At `VERIFY_RETAIN_DAYS` = 7 that is ~5.9 GB + 0.3 GB.
      - **The nightly `verify_day`:** Bybit 1,640.7 s at a peak child RSS of 1,534 MB (on 8
        cores), Hyperliquid 61 s.

      nifelheim has 2 vCPU and 3.7 GB, is already oversubscribed, and has OOM-restarted its own
      services. Options:
      1. **Recommended: recorders on the desktop.** Keep the verify stack (recorders, collectors,
         `verify_day`) on the desktop. It proves the code the VPS runs, on the same venues and
         instruments, but not the VPS's own catalog. The production nightly stays `no reference
         data`.
      2. **Recorders on the VPS, `verify_day` on the desktop.** The recorders are cheap on the VPS
         (~140 MiB, ~6 % of a core, ~6 GB disk). Each closed day's raw files, catalog day and
         coverage are synced to the desktop, and `verify_day` runs there against the production
         data. This needs a sync job no story has built yet.
      3. **Both on the VPS.** Not recommended: a 1.5 GB child on a 3.7 GB host next to the
         collectors risks exactly the OOM DATA-07 forbids.

      Record the choice in D-138.
- [ ] **Keep the verify host awake for a full clean day (audit D-138; needs sudo).** The dev box
      suspended overnight three times in two days, so no full clean UTC day exists yet.
      - Before the next soak day, run `sudo systemctl mask sleep.target suspend.target
        hibernate.target hybrid-sleep.target` (undo with `unmask`), or disable automatic suspend
        in the desktop's power settings.
      - Leave the verify stack untouched for one whole UTC day: no chaos run, no redeploy, no
        `make verify-down`.
- [ ] **Read the first full clean day's verdict and close D-138.** After the nightly of the first
      such day (2026-10-02 at the earliest: 2026-10-01 lost 06:08-15:35Z to a machine shutdown; the
      scheduler runs at 03:07Z):
      1. Run `python3 -c "import json; print(json.dumps(json.load(open('data/archive/state.json'))['verification_days'], indent=1))"`,
         or `GET /api/archive/status`.
      2. For each venue-day that is not `verified`, rerun
         `docker exec verify-archive python3 -m archive.verify_day --catalog /app/catalog
         --candles-dir /app/candles_dir --venue V --day D --result-file
         /app/verify_data/scratch/verify_V_D.json --reports-dir
         /app/verify_data/scratch/reports-D/V` to get each tool's full report.
      3. Classify every failing count as `docs/VERIFICATION_REPORT.md` does.
      4. Fill the report's "Full day" column and set D-138 to CLOSED with the day.
      5. Expect `findings` while D-91, D-102/D-141, D-140, D-112, D-113, D-143 and D-144 are open,
         even on a clean day: each fails its tool by design.
- [ ] **The 31-9 decisions are still owed** (D-113, D-129, D-133/D-134, D-135). They are shown OPEN
      in `docs/VERIFICATION_REPORT.md` with the recommended options of entry 31-9 above. No verdict
      waits on them.
- [ ] **Decide the new OPEN rows' follow-ups:**
      - D-139: capture forces a reconnect of a silent feed. Recommended as a follow-up story: it
        cost 6-16 min of seconds at the 2026-09-30 restart.
      - D-140: store capture's Hyperliquid push `time`, then re-measure.
      - D-141: a durable late-book-message count, then size `hold_back_seconds`.
      - D-143 and D-144: verifier follow-ups. Each needs a planted-defect test; never loosen the
        pass rule without one.

### 28-1-capture-hotpath-metrics-cpu-priority-and-vps-profile (commit: this story's)

Rebuilds the collector image (new capture code: the per-flush hot-path figures) and applies the
compose CPU weights and collector memory limits (section 7). No config key, schema, mount or
archive format changed. The VPS profile below fills audit D-146; Story 28.2 does not wait for it
(it cites the profile only if it exists).

- [ ] On the VPS, `git pull`, then run section 7's redeploy in its order (collectors first, then the
      batch services) and its weight/limit `docker inspect` check.
- [ ] Section 7's one-hour acceptance check: `uptime` load at or below `nproc` over the hour, zero
      `_second_loop tick arrived ... late` lines in Dozzle, `OOMKilled=false` for every collector.
      Note each collector's `hotpath:` figures from the hour (or
      `redis-cli GET capture:hotpath:<venue>`).
- [ ] Profile every running collector for 5 minutes (host tool, never a project dependency):
      `sudo py-spy record --pid <pid> --duration 300 --format speedscope -o <venue>.speedscope.json`,
      with `<pid>` from `docker inspect -f '{{.State.Pid}}' <container>`. In the same 5 minutes
      record `uptime`, `free -h` and `docker stats --no-stream`.
- [ ] When Dozzle shows a `_second_loop tick arrived ... late` line, take
      `sudo py-spy dump --pid <pid>` of that collector at once (repeat a few times while it is late),
      with `uptime` and `docker stats --no-stream` alongside.
- [ ] Commit everything under `platform/.planning/debug/capture-profile-2026-<MM-DD>/` with a
      `README.md` that ranks each venue's top-10 self-time frames as (a) our Python, (b) Nautilus
      Cython/Rust or (c) interpreter/asyncio, and states whether the box was contended during the
      profile (load vs cores, the batch services' CPU in `docker stats`).
- [ ] Update audit D-146 (`docs/DATA_INTEGRITY_AUDIT.md`) with the README's path, the ranking and
      the contention verdict, and add the VPS `lag_max_ms`/`lag_p99_ms` to D-10.
- [ ] Note: Story 28.2 does not wait for this entry; it cites the profile only if it exists.

### 28-2-capture-python-overhead-removed-baseline-lowered (commit: this story's)

Rebuilds the collector image (new capture and kernel code: the columnar `DydxSecondSnapshot` flush
encoder and the ingest hand-off without a per-message `wait_for`). No config key, schema, mount or
archive format changed: the Parquet files are byte-identical (audit D-65, Story 28.2). uvloop was
measured and not kept (its per-callback handle raised the scale burst's queued peak above the
baseline), so the collectors still run asyncio's default loop and there is no `event loop:` line to
look for.

- [ ] Before redeploying, note each collector's `write_data_max_ms` from its `hotpath:` Dozzle
      lines over an hour (or `redis-cli GET capture:hotpath:<venue>`), with `uptime` alongside.
- [ ] On the VPS, `git pull`, then rebuild and redeploy the collectors (`make redeploy-all`; dYdX
      only if it is deployed, `make up-dydx`). Check each collector logs `Started:` and writes
      `custom_dydx_second_snapshot` files at the next minute.
- [ ] Over the hour after the redeploy, compare `capture:hotpath` `write_data_max_ms` and
      `lag_max_ms` against the figures noted before; expect the write to fall several-fold (the
      dev box: 304 -> 49 ms for 30 instruments). Record both sets in audit D-65's Story 28.2 record.
- [ ] When D-146's VPS profile lands (entry `28-1-capture-hotpath-metrics-cpu-priority-and-vps-profile`),
      re-check its README's ranking against the dev-box figures in D-65 and replace the projection's
      "x 3, assumed" with the profile's measured dev-box-to-VPS ratio.

### 32-5 preferences directory and server-side chart drawings (commit: this story's)

`chart_indicators.toml` and `screener_columns.toml` moved to `platform/data/preferences/` (`git mv`)
and `docker-compose.yml` now mounts that one directory at `/app/preferences/` instead of one bind
mount per file; `chart_drawings.toml` (new, committed empty) joins them. `data_api` derives all
three paths from `CHART_PREFERENCES_DIR` and refuses to start while the removed
`CHART_INDICATOR_CONFIG_PATH`/`SCREENER_COLUMNS_CONFIG_PATH` are set. `GET /api/candles/{iid}`
now answers 404 for an instrument with no definition in the catalog and carries its
`price_precision`/`size_precision`.

- [ ] On the VPS, stop the writer first so no UI save lands between the copy and the pull:
      `cd ~/nautilus_trader_fork/platform && docker compose stop data_api`.
- [ ] Keep the live files: `cp data/chart_indicators.toml data/screener_columns.toml /tmp/`, then
      `git checkout -- data/chart_indicators.toml data/screener_columns.toml` (the UI rewrote the
      tracked copies in place, so the pull would refuse them) and `git pull`.
- [ ] Put them in the new directory: `cp /tmp/chart_indicators.toml /tmp/screener_columns.toml
      data/preferences/` (this overwrites the committed copies; `chart_drawings.toml` stays as
      pulled), then `rm -f data/chart_indicators.toml data/screener_columns.toml`.
- [ ] Check the host's compose environment does not set `CHART_INDICATOR_CONFIG_PATH` or
      `SCREENER_COLUMNS_CONFIG_PATH` anywhere (`platform/.env`, an override file): `data_api`
      refuses to start naming `CHART_PREFERENCES_DIR` if it does.
- [ ] `make up`, then check: a coin's saved indicators and the Technicals columns are still there,
      a horizontal line drawn in the browser before the deploy appears once (it is imported from
      that browser's `localStorage` on the first load) and survives a reload in another browser,
      and `GET /api/candles/<a collected id>` carries both precision fields.

### 33-1-bybit-liquidations-captured-over-a-second-socket-into-one-shared-liquidation-type (commit: this story's)

The Bybit collector opens a second, generic WebSocket to the public linear stream for
`allLiquidation.{symbol}` of every collected LINEAR id (`capture/venues/bybit/liquidations.py`),
archives the rows to `data/custom_liquidation/<iid>/`, publishes them on the new Redis channel
`liquidations:raw`, appends `liquidations` (`connected`/`reconnecting`/`down`) as the last key of
the Bybit `collector:status` aggregate and writes `liquidations_unrecoverable` lines to
`data/coverage/bybit.jsonl` (per id; one short line per id at every start, from its subscribe to
Bybit's ack, is expected) (`docs/DATA_DICTIONARY.md` §1.26). The nightly `verify_day` also runs
`verification.liquidations` for BYBIT and keeps its summary under `liquidations` in
`archive:status` `verification_days`. No config key, env var, compose service or bind mount
changed; the coverage record's existing mount already carries the new kind.

- [ ] On the VPS, pull this commit and rebuild/restart the Bybit collector and the archive service:
      `cd ~/nautilus_trader_fork/platform && make up` (or `docker compose up -d --build
      bybit_collector archive`).
- [ ] Within a minute, confirm the Bybit aggregate on `collector:status` ends in
      `"liquidations": "connected"`: `redis-cli SUBSCRIBE collector:status` and wait for the next
      publish (or restart `bot_tui` to see the fresh one), and that `docker compose logs
      bybit_collector | grep "liquidation socket"` shows `None -> connected`.
- [ ] Check the ledger shows no `collector.liquidation_feed`, `collector.liquidation_publish` or
      `collector.unencodable` line since the restart (`GET /api/errors`, or
      `platform/data/errors/bybit_collector.jsonl`); a `collector.unencodable` naming a liquidation
      is a precision finer than the definition and is a DATA-04 finding, not noise.
- [ ] After the first liquidation (minutes on any active hour), confirm rows arrive:
      `redis-cli SUBSCRIBE liquidations:raw` shows a JSON array of rows with integer
      `price_units`/`size_units`, and `ls data/catalog/data/custom_liquidation/` lists the ids
      after the next flush (60 s).
- [ ] After the next nightly run, confirm `archive:status` `verification_days.BYBIT.<day>` carries
      a `liquidations` object and that the day's `verification` did not change because of it. It
      holds `report` (`reported`, or `refused` with a `reason`), `applicable`, `coverage_present`
      (`false` means the unrecoverable seconds are unknown, not 0), the day's `total`, `matched`,
      `share` and `unrecoverable_seconds`, and per instrument only counts (`total`, `matched`,
      `unmatched`, `unrecoverable_seconds`) -- no unmatched ids. The full report (the first 20
      unmatched ids per instrument) comes from a by-hand run: `python3 -m archive.verify_day ...
      --reports-dir DIR` writes `DIR/liquidations.json`, or run `python3 -m
      verification.liquidations --venue BYBIT --day <day> --json --catalog <catalog>` directly.

### 33-3-per-bar-order-flow-and-liquidation-aggregates-in-the-candle-store-folded-once (commit: this story's)

The candle store gains ten nullable INTEGER columns (`buy_v`, `sell_v`, `buy_n`, `sell_n`, `pv`,
`liq_long_v`, `liq_short_v`, `liq_n`, `price_precision`, `size_precision`) and the
`liquidations_applied` and `liquidation_feed_since` tables (`docs/DATA_DICTIONARY.md` §2.15). Each collector migrates its own
`candles_<venue>.db` in place when it opens it (`ALTER TABLE ... ADD COLUMN`): every existing row
reads **null** in the new columns (unknown, never 0) until its day is rebuilt. New seconds fill
them live; the open day's pre-deploy part is filled by that day's nightly `candles.rebuild --day D`
(after midnight); older days only by a full rebuild. The data_api serves the new keys on
`/api/candles` and `/ws/live` (null where not filled) and the CVD indicator gains an `anchor`
param; `verification.candles`/`catalog` fail a day whose stored bars are still null. No config key,
env var, compose service or bind mount changed.

- [ ] On the VPS, pull this commit and rebuild/restart the collectors, the archive and the data_api:
      `cd ~/nautilus_trader_fork/platform && make up` (or `docker compose up -d --build
      bybit_collector hyperliquid_collector archive data_api`). Restart the collectors before (or
      together with) the data_api: a data_api reading a not-yet-migrated file serves the new keys
      as null, which is correct, but the collectors are what migrate.
- [ ] Confirm the migration ran: `sqlite3 data/candles/candles_bybit.db "PRAGMA table_info(candles)"`
      lists the ten columns after `seconds_observed`, and `.tables` shows `liquidations_applied` and
      `liquidation_feed_since` (same for `candles_hyperliquid.db`). After the Bybit collector's
      start `SELECT * FROM liquidation_feed_since` holds each Bybit linear id whose last day had a
      liquidation (its startup catch-up records the start); until a row exists for an id, its live
      bars' `liq_*` stay null, never 0 (audit D-160). Check the ledger shows no `collector.candle_store` line
      since the restart (`GET /api/errors`); a `CandleOverflowError` there is a DATA-02 finding.
- [ ] Fill the history: once the collectors run the new code, rebuild every closed day of each
      venue (the open day is left to the nightly; one writer per instrument-day, so `--workers 1`
      as the nightly runs it):
      `docker compose exec archive python3 -m candles.rebuild --catalog /app/catalog --db /app/candles_dir/candles_bybit.db --venue BYBIT --workers 1`
      then the same with `candles_hyperliquid.db` and `--venue HYPERLIQUID` (dYdX only if its
      store is still served: `make up-dydx` deployments).
- [ ] Verify: `sqlite3 data/candles/candles_bybit.db "SELECT COUNT(*) FROM candles WHERE buy_v IS
      NULL AND seconds_observed > 0 AND t < strftime('%s','now','start of day') * 1000"` is 0 (and
      for `candles_hyperliquid.db`); Bybit linear bars carry `liq_n` (0 or more) only where the
      bar **starts at or after** that id's feed start (`SELECT since_ns FROM
      liquidation_feed_since WHERE instrument_id = '<id>'`, which after the rebuild equals the
      `MIN(ts_event)` of its `data/custom_liquidation/<id>/` files or an earlier live one) and
      `liq_n IS NULL` on every bar starting before it -- the bar straddling it null at every width,
      1m to 1D alike: `SELECT bar_seconds, COUNT(*) FROM candles WHERE instrument_id = '<id>' AND
      t * 1000000 < <since_ns> AND liq_n IS NOT NULL GROUP BY bar_seconds` returns no row --
      history archived before the Story 33.1 feed is unknown, never 0 (audit D-160) -- and every
      Hyperliquid and spot bar `liq_n IS NULL`; `curl -s "localhost:9100/api/candles/BTCUSDT-LINEAR.BYBIT?before_ns=$(date +%s%N)&limit=3"`
      shows the ten keys after `partial`; and the next nightly's `verify_day` keeps `candles` and
      `catalog` at 0 failing for the rebuilt days.

### DW-182 archive-gap markers decode under the strict reader (Story 23.2; commit: 3f8328d048)

Story 23.2 made `kernel.archive_markers.decode` refuse an inverted span (`from_ns > to_ns`), and the
pre-23.2 `record_gap` could write one when the wall clock stepped backward mid-gap. A marker file
already on the VPS may therefore hold a line the new reader refuses:
`archive.infrastructure.gap_markers.load_gaps` raises on it, so the nightly rebuild refuses that
instrument-day until the line is repaired.

- [ ] On the VPS, before the first nightly run on a tree at or after Story 23.2 (and once now if that
      run already happened), run this read-only check in the `archive` image. It decodes every
      non-blank line of every `_archive_gaps/*.jsonl`, prints `path:line: error` for each refused
      one (line numbers as `load_gaps` counts them), prints the total, and exits 1 if any was
      refused (no `_archive_gaps` directory = 0 lines, exit 0; a missing catalog root itself
      fails with exit 1 naming the path, so a wrong mount never passes as "0 checked"):

```bash
cd ~/nautilus_trader_fork/platform && docker compose run --rm --no-deps -T archive python3 - <<'PY'
import sys
from pathlib import Path
from kernel.archive_markers import GAPS_DIRNAME
from kernel.archive_markers import decode
catalog = Path("/app/catalog")
if not catalog.is_dir():
    sys.exit(f"{catalog} is not a directory: is the catalog mounted?")
root = catalog / GAPS_DIRNAME
checked = refused = 0
for path in sorted(root.glob("*.jsonl")):
    try:
        lines = path.read_text().splitlines()
    except ValueError as e:  # not UTF-8: the reader refuses the whole file
        refused += 1
        print(f"{path}: unreadable: {e}")
        continue
    for number, text in enumerate(lines, start=1):
        if not text.strip():
            continue
        checked += 1
        try:
            decode(text)
        except ValueError as e:
            refused += 1
            print(f"{path}:{number}: {e}")
print(f"{checked} archive-gap marker line(s) checked, {refused} refused")
sys.exit(1 if refused else 0)
PY
```

- [ ] For each inverted line: stop that venue's writer and the archive service, by the
      instrument id's venue suffix (`.DYDX` -> `collector`, `.BYBIT` -> `bybit_collector`,
      `.HYPERLIQUID` -> `hyperliquid_collector`: `docker compose stop <service> archive`), and run
      no manual archive tool meanwhile (`make nightly`/`consolidate`/`prune`: `prune` appends
      `pruned` markers to these same files). Copy the file out of the catalog first
      (`mkdir -p ~/archive-gaps-backup && cp data/catalog/_archive_gaps/<iid>.jsonl
      ~/archive-gaps-backup/<iid>.jsonl.$(date +%F)`), then edit the line swapping the `from_ns`
      and `to_ns` values, keeping the key order `instrument_id`, `from_ns`, `to_ns`, `reason`,
      `count`. That is exactly what the post-23.2 `record_gap` writes for the same input (it
      ledgers `archive_gaps.inverted_span` and records the ordered span, which still covers the
      rows the marker protects). Re-run the check until it exits 0, then `docker compose start`
      the services you stopped. Any other refused line or file (not an inverted span) is a
      DATA-02 question: record it in `docs/DATA_INTEGRITY_AUDIT.md` rather than delete it.
- [ ] Record the result here with the date: lines checked, and each repaired line's original text
      and file (a repaired line is a backward wall-clock step on that collector's host, the same
      evidence an `archive_gaps.inverted_span` ledger entry carries today).

### DW-154 live chart walkthrough (§A8.2) for Epics 18 and 32 (operator decision 2026-10-05; run after the Epic 33 chart stories)

Every chart story so far is verified only in jsdom against a mocked lightweight-charts. Run this once
Epic 33's chart stories (33.5, 33.9, 33.10, 33.12) are deployed, so one pass covers them too.

- [ ] Open the deployed web app's chart page in a real browser and walk the §A8.2 operation checklist
      (`_bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md`, "A8.2 Operation"):
      pan/zoom, fit/latest, the pane-resize hit zone, every drawing tool's mouse mechanics (hline,
      trendline, Fibonacci, long/short, measure, Anchored VP, Anchored VWAP), replay, and every volume
      profile's real drawing (VRVP right-axis anchoring, the time-anchored session/FRVP/Auto Anchored/
      TPO widths, FRVP edge grab, SVP HD's zoom response), plus the footprint toggle.
- [ ] Record here, with the date, each behaviour that differs from §A8.2; each one becomes a
      deferred-work entry.
