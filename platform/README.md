# Multi-Venue Market Data Collector

Continuously records dYdX, Bybit and Hyperliquid market data to one local `ParquetDataCatalog`: 1-second book/trade snapshots (`DydxSecondSnapshot` — top-20 levels plus that second's trade OHLC/volume/counts), mark/index prices, funding rates, open interest and instrument definitions. Individual `TradeTick`s are folded into the second's snapshot and **not** stored raw, and `order_book_deltas` are a per-instrument opt-in (`store_order_book_deltas`) that is off by default and exists **for dYdX only** — `bybit_collector`/`hyperliquid_collector` take a flat `instruments = ["..."]` list with no per-instrument options. See `docs/DATA_INTEGRITY_AUDIT.md` D-45. Coverage is not uniform across venues either: a Bybit **spot** id yields trades and book only — no mark/index price, no funding rate (`bybit_collector/client.py:124-125` subscribes the ticker for `LINEAR` alone) and no open interest (Bybit spot has none). The catalog is Nautilus-native — load it directly into backtests with zero conversion.

All three collectors are the same class: `collector_core.collector.Collector` (the write gate and every invariant check), subclassed by a thin per-venue package (`dydx_collector/`, `bybit_collector/`, `hyperliquid_collector/`) that supplies the venue's client and at most a few hook overrides. `make up` starts one container per venue from one image.

---

## Prerequisites

- Docker + Docker Compose
- The `nautilus-trader-base` image built once (see below)

---

## Quick reference

Run all `make` commands from `platform/`:

| Command | What it does |
|---|---|
| `make build-base` | Build the base Nautilus image (~15 min, once only) |
| `make up` | Build collector image and start collecting |
| `make down` | Stop containers (catalog data is preserved) |
| `make logs` | Tail collector logs |
| `make web` | Open Dozzle log viewer in browser |
| `make tui` | Open the terminal UI: bots and collector control (see below) |
| `make prune` | Delete `order_book_deltas` older than 14 days |
| `make prune-dry` | Preview what `prune` would delete |
| `make consolidate` | Merge each closed UTC day into one Parquet file per (type, instrument), by hand |
| `make nightly VENUE=...` | One venue-day's nightly saga, by hand (the `archive` service runs it nightly) |
| `make backup-catalog` | `rclone sync` closed-day catalog files to object storage, by hand |

---

## First-time setup: build the base image

The base image compiles Nautilus from source. It takes ~15 minutes but only needs to be rebuilt when `nautilus_trader` core changes.

From `platform/`:

```bash
make build-base
```

---

## Configure instruments

Each venue has its own `config.toml`; `docker-compose.yml` mounts each one into its own container:

| Venue | File on the host | Mounted as |
|---|---|---|
| dYdX | `platform/data/dydx_config.toml` | `/app/dydx_collector/config.toml` (`rw` — the control plane writes it back) |
| Bybit | `platform/bybit_collector/config.toml` | `/app/bybit_config.toml` (`:ro`) |
| Hyperliquid | `platform/hyperliquid_collector/config.toml` | `/app/hyperliquid_config.toml` (`:ro`) |

The thresholds every venue shares are the fields of `collector_core/config.py`'s `CoreConfig` (`environment`, `catalog_path`, `flush_interval_seconds`, `snapshot_interval_seconds`, `stale_book_seconds`, `crossed_resync_seconds`, `stale_trade_seconds`, `seen_trade_ids`, `feed_stale_seconds`, `book_crosscheck_seconds`, `book_time_source`, `hold_back_seconds`, `trade_feeds`); `instruments` (and, for dYdX, `exclude`, `liquidity_min_oi_usd` and `non_config_retain_hours`) are the venue's collection plan (`collection_control`'s `CollectionPlan`), and a venue adds keys only where it needs them (Bybit's `open_interest_poll_seconds`, dYdX's `config_reload_seconds`/`open_interest_poll_seconds`/`liquidity_check_seconds`). One name does not carry over: dYdX's TOML key for the network is `network`, not `environment` — and an `environment = ...` line in dYdX's file refuses start.

**One loader, strict for every venue** (Story 25.4). Every venue's file goes through `collector_core.config.load_venue_config` (one `VENUE_SCHEMAS` row per venue), which rejects an unknown key outright rather than letting a misspelt threshold fall back to a default, and refuses a plan that breaks an invariant (more than 30 dYdX instruments, an id both collected and excluded, a repeated id). Before Story 25.4 dYdX had its own key-by-key loader that ignored unknown keys and the core thresholds; they are now honoured.

dYdX (`platform/data/dydx_config.toml`) — its plan (`instruments`, `exclude`) is hot-reloaded every `config_reload_seconds`, no restart needed; the thresholds are read at start:

```toml
network = "mainnet"
catalog_path = "catalog"          # relative to the container's /app — maps to ./catalog on the host
flush_interval_seconds = 60
config_reload_seconds = 30        # hot-reload of the plan: edit instruments/exclude while running
open_interest_poll_seconds = 300
instruments = [
    { id = "BTC-USD-PERP.DYDX" },
    { id = "ETH-USD-PERP.DYDX", store_order_book_deltas = true, retain_hours = 48 },
]
```

Bybit and Hyperliquid take a plain list of ids and are read once at startup (no hot-reload):

```toml
environment = "mainnet"
catalog_path = "/app/catalog"
instruments = ["BTCUSDT-LINEAR.BYBIT", "BTCUSDT-SPOT.BYBIT"]
```

Add any dYdX perpetual in `<BASE>-USD-PERP.DYDX` format, any Bybit linear/spot id in `<SYMBOL>-LINEAR.BYBIT`/`<SYMBOL>-SPOT.BYBIT` format, and any Hyperliquid perp in `<BASE>-USD-PERP.HYPERLIQUID` format.

---

## Deploy

From `platform/`:

```bash
make up        # build collector image (seconds) and start everything
make logs      # tail live collector output
make web       # open Dozzle log viewer (http://localhost:8080)
```

The catalog appears at `platform/data/catalog/` on the host, owned by your user (uid 1000). Under `docker-compose.yml` all three collectors mount that one root (and the `platform/data/candles/` directory, with a separate `candles_<venue>.db` per collector via `CANDLES_DB_PATH`), so `data_api` and every backtest read every venue from a single catalog. Every durable store lives under `platform/data/` (DDD spine AD-D13): `catalog/`, `candles/`, `metrics/`, `incident_reports/`, `live_paper/`, `bot_tui_logs/`, `archive/` (the `archive` service's scheduler cursor, below) and the dYdX plan file `dydx_config.toml`. The sharing is the compose mounts, not the config: Bybit's and Hyperliquid's `catalog_path` is the container-absolute `/app/catalog`, so running either outside Docker needs that value changed.

**Web dashboard:** `make up` starts `data_api`, which serves the React UI on `http://localhost:9100` — rankings, per-coin candlestick/indicator charts, 31-day metrics history, and docs. Reads directly from the catalog — no collector restart needed.

---

## Remote access via Tailscale + SSH tunnel

The dashboard, Redis, and Dozzle all bind to `127.0.0.1` only (see `docker-compose.yml`) —
nothing is reachable from the public internet or even the tailnet directly. Access is via
an SSH tunnel over Tailscale, so the only thing ever exposed is SSH itself.

**One-time VPS setup:**

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up          # follow the auth link, note the VPS's tailscale IP (tailscale ip -4)
make enable-boot           # docker starts on reboot
make enable-firewall       # default-deny inbound + rate-limited SSH + tailscale0
make harden-ssh            # key-only SSH auth -- confirm your key works first!
```

**From your PC**, with Tailscale running locally too:

```bash
ssh -N -L 9100:127.0.0.1:9100 -L 8080:127.0.0.1:8080 you@<vps-tailscale-ip>
```

Leave that running, then open `http://localhost:9100` (dashboard) or
`http://localhost:8080` (Dozzle logs) in your own browser. `-N` means the SSH session
does nothing but hold the tunnel open — no shell needed.

**Feed-health alerts:** since nobody is watching the dashboard continuously behind
the tunnel, set `WATCHDOG_NTFY_URL` (e.g. `https://ntfy.sh/<your-private-topic>`) as
an environment variable on the `collector` service in `docker-compose.yml` to get a
push notification if every subscribed instrument's order book goes stale for 30s+
(see OBS-01 in `CLAUDE.md`). Without it, the same condition is only logged.

---

## Stop / restart

```bash
make down   # stop containers, catalog persists
make up     # restart (rebuilds collector image automatically)
```

---

## Terminal UI (`bot_tui`)

```bash
make tui    # rebuilds the thin bot_tui layer, then runs it interactively (never a daemon)
```

A keyboard-only control surface for the bots and the collector, with two panes:

- **Bots** (the start pane, `:bots`): one row per bot (the `bots` context, `python3 -m bots`) from `bots:status`, with
  per-row stale markers. `s` starts/stops the highlighted bot (stopping asks you to type
  `stop`), Enter opens its detail view (trades, PnL, strategy source `v`, incidents `i`,
  dashboard link `o`).
- **Collector** (`:data`): every collected dYdX instrument from `collector:status`. `p`
  unpins, `x` stops collecting (both ask for confirmation); `:start <ID>` and `:pintop` add
  coins. Every action is written through to the collector's `config.toml`.

`:help` lists every key; `esc` goes back one view, `:q` quits. It reads only `bots:*` and
`collector:status` and publishes only `bots:control` and `collector:control`. Rankings, the
ranking-mode switch (volume / volatility) and the single-coin view are in the web UI's
rankings page (its home page, `/`) only (Story 25.1a). Operating the bots: `docs/BOT_OPERATIONS.md`.

---

## Rebuild the base image

Only needed when `nautilus_trader` Python/Rust core changes (e.g. after a `git pull` that touches `crates/` or `nautilus_trader/`):

```bash
make build-base   # ~15 min
make up           # rebuild collector layer on top and restart
```

---

## Inspect the catalog

```python
from nautilus_trader.persistence.catalog import ParquetDataCatalog

catalog = ParquetDataCatalog("platform/data/catalog")
catalog.instruments()
catalog.trade_ticks(instrument_ids=["BTC-USD-PERP.DYDX"])
catalog.order_book_deltas(instrument_ids=["BTC-USD-PERP.DYDX"])
```

### Backfill historical venue bars (Bybit / Hyperliquid)

```bash
# from platform/ -- report-only: plans the windows, contacts nothing, writes nothing
PYTHONPATH=. python -m archive.backfill_bars --catalog /app/catalog \
    --instrument BTCUSDT-LINEAR.BYBIT --start 2026-09-01 --end 2026-09-18

# same command + --apply actually fetches and writes
PYTHONPATH=. python -m archive.backfill_bars --catalog /app/catalog \
    --instrument BTCUSDT-LINEAR.BYBIT --instrument BTC-USDC-PERP.HYPERLIQUID \
    --start 2026-09-01 --end 2026-09-18 --bar-spec 1-MINUTE-LAST --apply
```

Fetches the venues' own klines and writes them as `EXTERNAL` `Bar`s (close-stamped on both venues)
through `ParquetDataCatalog.write_data()`. dYdX is not supported -- its bars come from its own 1 s
archive. **Report-only unless `--apply`.** Re-runs are idempotent at the request level: an already
archived range plans zero windows and issues zero REST calls.

- `--bar-spec` (default `1-MINUTE-LAST`): `1/3/5/15/30-MINUTE`, `1/2/4/12-HOUR`, `1-DAY`, all
  `-LAST`. Anything else exits non-zero before the first request.
- `--environment` (default `mainnet`): picks which venue endpoint is queried (`mainnet`, `testnet`,
  plus `demo` on Bybit). It does **not** verify the catalog's own environment -- nothing records
  one -- so it cannot stop a mainnet/testnet mix under a single instrument id.
- Hyperliquid only serves ~the last 5000 candles; older windows are reported as missing, never
  silently marked covered. See `docs/DATA_INTEGRITY_AUDIT.md` D-52/D-53.

---

## Nightly maintenance

Every collector flushes once a minute, so the shared catalog gains ~1,440 Parquet files per
(data type, instrument) per day -- for dYdX, Bybit and Hyperliquid alike. Inodes, directory
listings, reads and backups all scale with the file count, not the bytes (audit D-36). The raw
trade archive also has to be folded back into each closed day, proven against the venue's klines,
pruned and, once off-site storage exists, backed up. All of it is scheduled by our own code, not
a host crontab.

### The `archive` service (Story 25.1b)

`make up` starts the compose service **`archive`** (`python3 -m archive.scheduler`, the collector
image, `restart: always`, no profile). It is the one place nightly maintenance is scheduled:

- **Nightly** at `nightly_at` UTC: for each closed day due, oldest first, and each venue, the
  nightly saga (`make nightly`'s, below), then one `consolidate_catalog --apply` (every closed day,
  every type: it keeps reporting an old refused day the saga's `--days 2` skips), then, only when
  `backup_enabled = true`, the off-site backup (`archive.backup_catalog`, `make backup-catalog`'s,
  below). Each step is its own child process (MEM-01). One venue's failure never skips the next
  venue, the consolidate or the backup.
- **Catch-up** after downtime: every missed closed day per venue, up to `catch_up_max_days`. A day
  whose saga FAILED is retried by the next night's run. A reboot or redeploy at 03:07 loses nothing.
- **Intraday** every `intraday_consolidate_hours`: the current UTC day's *closed hours* of the small
  types (mark/index price, funding, open interest, instrument status) are merged into one file per
  hour (`consolidate_catalog --apply --closed-hours`). A file reaching the current hour is never
  touched.
- It waits (bounded by `lock_wait_minutes`) while a manual run holds the catalog maintenance lock,
  probing it without holding it, and never runs two of its own jobs at once.

**Config:** `platform/archive/config.toml`, mounted read-only (an edit applies on
`docker compose restart archive`). Every key is required; an unknown one refuses start.

| Key | Default | Meaning |
|---|---|---|
| `nightly_at` | `"03:07"` | UTC time of the nightly run, and the minute of the intraday grid |
| `venues` | `["DYDX", "BYBIT", "HYPERLIQUID"]` | Venues whose saga runs, in this order within a day |
| `catch_up_max_days` | `7` | Missed days caught up after downtime; older ones are ledgered (`archive.catch_up_capped`) |
| `lock_wait_minutes` | `60` | How long a job waits for the maintenance lock before it fails as `lock_timeout` |
| `intraday_consolidate_hours` | `4` | Period of the closed-hour merge (divides 24) |
| `step_timeout_minutes` | `360` | A step still running after this is killed and fails with exit 124 (`archive.step_timeout`) |
| `backup_enabled` | `false` | Whether every full run ends with the off-site backup (Story 26.1b; a strict `true`/`false`) |

**The off-site backup is optional and off by default** (committed `backup_enabled = false`) until
off-site storage exists. While it is off, the catalog has **no copy off this host** (audit D-33,
still OPEN): the service logs one WARNING at start (`off-site backup disabled: the catalog has no
copy off this host`), and every status says `"backup": "disabled"`, which the web panel shows as
`backup off` (warn colour) and the TUI's archive line as `backup off`. No backup step runs, so a
night is never FAILED by the missing target. With `backup_enabled = true` the target is
`RCLONE_REMOTE`/`RCLONE_BUCKET` in `platform/.env` (below), not this file, and the service refuses
to start (exit 1, `archive.config_invalid`) while either one is unset or blank, rather than fail
every night. Under compose's `restart: always` that refusal repeats on every restart: each one
ledgers `archive.config_invalid` again and no `archive:status` is published, so the web panel shows
"status unavailable" and the TUI line goes stale -- set both values or `backup_enabled = false`.

**State:** `platform/data/archive/state.json` holds `last_run_day` (the last completed scheduled
run), each venue's `last_success_day` (the last day of unbroken no-FAILED sagas) and the last run
and last intraday run. It is a scheduler cursor, written atomically, and never a data verdict:
`verified_days` stays the only day status, and reconcile and prune never read this file.

**Status:** after every step, on start and every 30 s the service publishes `archive:status`
(`next_run`, `next_intraday`, `running`, `last_run`, `last_intraday`, each run with its steps'
venue, name, exit code and duration, then `backup`: `"enabled"` or `"disabled"`;
`docs/DATA_DICTIONARY.md` §1.13). The web UI shows it next to
the error bar (`GET /api/archive/status`), and the TUI shows it at the bottom of the Collector pane.
Logs: `docker compose logs -f archive` (Dozzle: `archive`). Every tolerated failure is in the
error ledger (`archive.*`, `nightly.*`, `consolidate.*` sites; `data/errors/archive.jsonl`).

**Run now:** the web UI's "Run now" button (behind a confirm) posts `POST /api/archive/run`, which
publishes `{"command": "run_now", "day": "YYYY-MM-DD" | null}` on `archive:control` (null:
yesterday). The service queues that closed day's full sequence (all venues, consolidate, and the
backup when enabled) after any running job; a day already queued is dropped, and a bad message is ledgered
(`archive.control_rejected`).

### Manual tools

The `make` targets below stay as manual tools: re-running a day, a day past the catch-up cap, or a
first fold of existing history (run `make consolidate` once by hand on a new deploy). They share
the maintenance lock with the service. Re-running a missed day and the first-run measurements still
owed are in [`docs/DEPLOY_CHECKLIST.md`](docs/DEPLOY_CHECKLIST.md), whose §1 also says how to remove
the old host cron line.

**`make nightly VENUE=<DYDX|BYBIT|HYPERLIQUID> [DAY=YYYY-MM-DD]`** (default: yesterday, UTC;
`archive.nightly` in the collector image, the archive context's saga since Story 25.1) runs, each as its own process: `rebuild_seconds --apply` (the closed day's snapshot
trade columns re-derived from the raw trade archive on exchange time; rows inside an archive-gap
marker keep their live values and are counted `in gap`) -> `consolidate_catalog --apply --venue
--days 2` (below) -> `build_candles` (`python -m candles.rebuild --day --workers 1`; the step keeps
its name) -> `compare_klines --rebuilt-by <run id>` (every traded minute against the venue's own
1 m klines, exact; verdict in `verified_days`; only the instruments the rebuild names are passed as
`--rebuilt` and compared, one it refused is passed as `--not-rebuilt`, and any instrument not
rebuilt gets no verdict) -> `prune_catalog --apply` (raw trades released 7 days after
their day reconciled `pass`; for DYDX also the plan's dropped-instrument and per-coin delta
retention, `--dydx-plan`). A step's exit 2 is "findings" (per-instrument refusals, mismatches or
uncomparable instruments, all ledgered): the chain continues; any other failure stops it -- a
rebuild whose result file is missing counts as failed. One summary line per venue:

```text
nightly <VENUE> <DAY>: rebuild_seconds ok <s>s, consolidate_catalog ok <s>s, build_candles ok <s>s, compare_klines ok|findings <s>s, prune_catalog ok <s>s; peak child RSS <M> MB; outcome ok|findings|FAILED; run id <id>
```

Run standalone, `python -m archive.rebuild_seconds --day D --apply` logs `run id <id>; pass
--rebuilt-by <id> to compare_klines`: `python -m archive.compare_klines` compares and records
nothing without `--rebuilt-by` (`reconcile.not_rebuilt`, exit 1), and with it compares only the
instruments named by `--rebuilt IID` (repeatable). Known limit: standalone, that id
is the operator's attestation -- `rebuilt` is never persisted, so the tool cannot check it.
Every archive tool is `python -m archive.<tool>` (`rebuild_seconds`, `consolidate_catalog`,
`prune_catalog`, `repair_catalog`, `compare_klines`, `nightly`, `backfill_bars`,
`crosscheck_errors`, `tools.measure_lag`, `tools.migrate_open_interest`,
`tools.normalize_snapshot_schema`); the old `collector_core.*`/`dydx_collector.*` paths were
removed in Story 25.3. Each collector holds
`<catalog>/.capture-<VENUE>.lock` while it runs, and `repair_catalog --apply` refuses that venue
until it is stopped.

Details of each step: `docs/DATA_DICTIONARY.md` §6.

**`make consolidate`** (`archive.consolidate_catalog --apply` in the collector image; with
`CONSOLIDATE_ARGS="--closed-hours"` it runs the service's intraday merge by hand) walks
every `data/<type>/<instrument>/` leaf and merges each closed UTC day's files into one; today's
files are never read or rewritten, and a batch that crosses midnight stays as it is. `bar` leaves
are skipped: `backfill_bars` plans from their file intervals, so merging two runs across a missing
range would seal that hole as covered (DATA-05); they are only a few files per window anyway.
Only one run at a time (a lock file in the catalog root); a second one exits 1 without starting.
An unreadable file refuses its own day only -- every other day and leaf still runs. A day whose
files disagree on schema -- columns *or* Arrow metadata such as `price_precision` -- is refused,
as is a merged file whose row count does not match its sources; nothing is deleted then. Extra
flags pass through `CONSOLIDATE_ARGS` (`make consolidate CONSOLIDATE_ARGS="--days 3"`). Every run
ends with one line:

```text
consolidate: <D> day(s) consolidated, <R> refused, <L> leaf/leaves failed; files <before> -> <after>; <MB before> -> <MB after> MB; <S> s wall; peak RSS <M> MB
```

Peak RSS is the job's own `ru_maxrss`, measured inside
the container -- `/usr/bin/time -v make consolidate` would only measure the `docker compose` client.
The exit code is 1 when any day was refused or leaf failed; the reason is logged above the summary line and in
the `consolidate.*` error-ledger entries.

**`make backup-catalog`** runs `python3 -m archive.backup_catalog` in the `archive` service's
container -- the same module the service runs after every nightly run when `backup_enabled =
true`, with `rclone` from the image
(the host needs none) -- and syncs `data/catalog/data` to
`$RCLONE_REMOTE:$RCLONE_BUCKET/catalog/data`:

- Consolidate first: without it the upload is millions of tiny objects (per-request cost and
  hours of listing), with it a few hundred new files a night.
- Today's (UTC) files and `*.tmp` leftovers are excluded -- only closed, finished files leave the box.
- Objects the sync would overwrite or delete (the minute files a consolidation replaced) are moved
  under `catalog-replaced/<UTC timestamp>/` in the same bucket (`--backup-dir`), never deleted, so a
  wiped local catalog (D-33) cannot erase the only backup. Prune that prefix **only by hand**,
  after checking the remote `catalog/data` is intact -- never with an automatic expiry rule: after
  a local wipe that prefix holds the only copy of the history.
- It refuses (exit 1, nothing uploaded, one error-ledger entry) when `RCLONE_REMOTE` or
  `RCLONE_BUCKET` is unset (`archive.backup_not_configured`), or when `rclone` is missing or the
  local `catalog/data` is missing or holds no closed-day Parquet file (`archive.backup_failed`; a
  freshly wiped catalog holding only today's files must not be mirrored). A failed sync is
  `archive.backup_failed` too. In the service each shows as a FAILED `backup_catalog` step. The
  manual run ignores `backup_enabled`: without a target it still exits 1 with
  `archive.backup_not_configured`.

Setup, once off-site storage exists:

1. On the host, run `rclone config` (a one-time step; the nightly runs use the image's rclone) to
   create the remote. It lives in the operator's `~/.config/rclone/rclone.conf`, **never in this
   repo**, and is mounted read-only into the `archive` service (`RCLONE_CONFIG_DIR` in
   `platform/.env` when it lives elsewhere).
2. Set `RCLONE_REMOTE` and `RCLONE_BUCKET` in `platform/.env` (gitignored; see `.env-example`):
   bare values, no quotes, the remote without its trailing `:`.
3. Run `make backup-catalog` once by hand and check the remote's `catalog/data`.
4. Set `backup_enabled = true` in `platform/archive/config.toml`, then `make up` (the service
   re-reads `.env` only when recreated). Its status now says `"backup": "enabled"`.

Known limit:
the read-only mount cannot store a refreshed OAuth token, so use a remote with static keys (R2,
B2). Cheap targets: Cloudflare R2
(no egress fees) or Backblaze B2; both have a free tier covering the first few GB, but prices and
free allowances change -- check the current pricing pages before choosing.

`candles_*.db` needs no backup: it is derived from the catalog, and `make build-candles` rebuilds it.

---

## Run a backtest

```bash
# from the repo root (default catalog path is platform/data/catalog)
PYTHONPATH=platform python -m research.strategies.backtest_dydx
```

Streams trade ticks from the catalog and aggregates bars internally at a configurable wall-clock interval. Adjust `bar_interval` (e.g. `"1-SECOND"`, `"1-MINUTE"`, `"5-MINUTE"`), `buy_threshold`, `sell_threshold` by passing args to `run()`.

By default `run()` backtests every coin in the live Watchlist (requires `data_api` running -- if it's not reachable, `run()` raises a clear error rather than a raw connection traceback) and returns a `dict[str, BacktestResult]` keyed by symbol. Pass `symbols=["BTC-USD-PERP.DYDX", ...]` to backtest an explicit coin-set instead. A Watchlist coin with no matching catalog instrument yet is skipped (logged as a warning, not a crash) rather than aborting the whole run -- check the logs if the returned dict has fewer entries than expected.

Strategies are referenced by `ImportableStrategyConfig` string path, `research.strategies.<module>:<Class>` (the `research/` context since Story 24.4). See [`research/BACKTESTING.md`](research/BACKTESTING.md) for the other backtest runners, how to build a new strategy, and which data feed to subscribe to; the notebooks are in `research/notebooks/`.

---

## What gets collected

| Data type | Nautilus type | Source |
|---|---|---|
| Trades | `TradeTick` | WebSocket trades channel |
| Order book | `OrderBookDelta` | WebSocket orderbook channel |
| Bars | `Bar` | WebSocket candles channel |
| Mark price | `MarkPriceUpdate` | WebSocket markets channel |
| Index price | `IndexPriceUpdate` | WebSocket markets channel |
| Funding rate | `FundingRateUpdate` | WebSocket markets channel |
| Instruments | `CryptoPerpetual` | REST on startup |
| Open interest | `OpenInterest` | REST poll every 5 min |
