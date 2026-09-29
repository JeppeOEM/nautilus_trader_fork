# Verification Report (Epic 31)

**Purpose.** Prove that every datapoint the platform captures for Bybit and Hyperliquid, and every
signal derived from it, is correct, so bots can trade on it. Each row below is checked against an
**independent oracle**: the reference recorder of Story 31.1 (`python3 -m verification.recorder`,
the `verification/` context), a client sharing no code with the collectors, which writes every raw
WebSocket frame and REST response verbatim with its local receive time
(`docs/DATA_DICTIONARY.md` §1.15). A verdict is `VERIFIED` (exact agreement, numbers shown),
`DEVIATION` (a difference with a known mechanism, fixed or registered in
`docs/DATA_INTEGRITY_AUDIT.md`) or `OPEN` (not explained yet, registered with a follow-up story).
"Unexplained" must be 0 for `VERIFIED`. dYdX is out of scope.

## Soak

Code revision: `55a123a8bb` (Story 31.1). The collector-side code (capture, archive, ranking, data_api) the soak runs is the same at `b37be5775a` and `55a123a8bb`: the only change Story 31.1 made to it is the move of the dYdX URL helpers to `kernel/dydx_http.py`, which the Bybit and Hyperliquid services never import, so either revision reproduces the collectors. The recorders' code is not one revision: each recorder interval below names the commit that reproduces its bytes -- 10:20:27Z to 10:57:11Z the story's uncommitted first change set (no commit reproduces it exactly), 10:57:19Z to 11:20:00Z `55a123a8bb`, 11:20:02Z to 11:35:31Z `2799fb0ae7`, from 11:35:33Z the third review pass's commit (the one that adds the "Recorder gap 3" row). A comparator run cites the interval its window falls in.

| | |
|---|---|
| Soak start (UTC) | **2026-09-29T10:20:25Z** (every `verify-*` container started 10:20:25.19-10:20:25.23Z; the recorders' first `open` lines: Hyperliquid 10:20:27.409Z, Bybit linear 10:20:27.462Z, spot 10:20:27.463Z) |
| Starting state | empty `platform/data/` (only `dydx_config.toml`, `chart_indicators.toml`, `screener_columns.toml`) |
| Stack | `make verify-up`: compose project `verify` (`docker-compose.yml` + `docker-compose.verify.yml`) -- `bybit_collector`, `hyperliquid_collector`, `archive`, `ranking_engine`, `data_api` (127.0.0.1:29100), `redis` (127.0.0.1:26379), `dozzle` (127.0.0.1:28080), `reference_recorder_bybit`, `reference_recorder_hyperliquid`; no dYdX collector |
| Host | the dev box (local Docker), not the VPS |
| Code revision | the Story 31.1 commit on branch `epic-30` (parent `b37be5775a`); the stack was started from that change set before it was committed, and the recorders were rebuilt from its review revision (see below) |
| Soak end | ends at Story 31.2's soak restart (stopped and wiped; see below) |
| Recorder gap | 2026-09-29T10:57:11.44Z to 10:57:19.90Z: both recorders stopped cleanly (SIGTERM, `shutdown` close lines, no truncated file) and were rebuilt with the Story 31.1 review patches (code `55a123a8bb`); the collectors and the rest of the stack kept running. Both `connection` channels carry the gap |
| Recorder gap 2 | 2026-09-29T11:20:00.77Z to 11:20:02.49Z: both recorders stopped cleanly (`shutdown` close lines) and were recreated with the Story 31.1 follow-up review patches and the config-directory mount (`--no-deps`: the collectors and the rest of the stack kept running), code `2799fb0ae7`. Both `connection` channels carry the gap |
| Recorder gap 3 | 2026-09-29T11:35:31.61Z to 11:35:33.75Z: both recorders stopped cleanly (`shutdown` close lines) and were recreated with the Story 31.1 third review pass's patches (`--no-deps`, as above), code: the commit that adds this row. Both `connection` channels carry the gap. `verify-archive` still runs with the earlier environment; the override's blanked `RCLONE_REMOTE`/`RCLONE_BUCKET` apply at its next recreation (backup is disabled, so nothing differs meanwhile) |

**Soak restart (Story 31.2).** Story 31.2 changes what the collectors write (arrival-judged stale
filter, time-bounded and seeded dedup, the coverage record `data/coverage/<venue>.jsonl`), so the
soak above is stopped and wiped (`make verify-down`, `make verify-wipe` in the checkout that ran
it) and restarted with `make verify-up` on the fixed code. Every verdict row is computed on data
from the restarted soak only. Conservation needs a full closed UTC day with an unchanged plan
after the restart: the collectors' first day has no archived row to anchor a `restart` run
before each instrument's first verdict (audit D-76).

- Soak restart (Story 31.2): **2026-09-29T12:59:19Z** (`make verify-up` returned; all 9 `verify-*`
  containers up), from the main checkout `nautilus_trader_fork/platform` (branch `troll`), on the
  Story 31.2 commit's code (the working tree built into the image at the restart is that
  commit's code). The Story 31.1 soak in `../nautilus_trader_fork-epic30` was stopped and wiped
  (`make verify-down`, `make verify-wipe` there) at 12:36Z. A first restart at 12:37:06Z ran the
  pre-review change set; it was stopped and wiped again at 12:59Z so the soak runs only the
  reviewed code. Its data is gone and no verdict uses it.
- Live check at the restart (not a verdict): over the closed window 12:59:51Z-13:01:54Z (start +
  30 s to now - 180 s, so every trade had been flushed), every reference WS trade id was archived
  exactly once and nothing extra was archived: BTCUSDT-LINEAR 2608, ETHUSDT-LINEAR 3663,
  BTCUSDT-SPOT 1779, ETHUSDT-SPOT 465, SOL-USD-PERP.HYPERLIQUID 319. The same check on the
  12:37 pre-review soak (12:37:38Z-12:42Z) also matched exactly (5231, 7326, 2253, 662, 681).
- First day conservation can judge: **2026-09-30** (the restart day is partial, audit D-76).

**Instruments recorded** (each recorder reads its collector's own `config.toml`, so the recorded
set is the collected set):

| Venue | Instruments | Recorder endpoints |
|---|---|---|
| Bybit | `BTCUSDT-LINEAR.BYBIT`, `ETHUSDT-LINEAR.BYBIT`, `BTCUSDT-SPOT.BYBIT`, `ETHUSDT-SPOT.BYBIT` | `linear` (orderbook.50, publicTrade, tickers), `spot` (orderbook.50, publicTrade); REST instruments-info, open-interest (linear), tickers (linear; the collector's own open-interest source), recent-trade, orderbook |
| Hyperliquid | `SOL-USD-PERP.HYPERLIQUID` | `ws` (l2Book, trades, activeAssetCtx); REST metaAndAssetCtxs, l2Book |

Note: the epic preamble names Hyperliquid **BTC/ETH** perps, but Hyperliquid's committed plan
collects **SOL only** since the venue cutover (Story 29.3, operator decision 2026-09-26: BTC/ETH
moved to Bybit). The verdicts below therefore cover Hyperliquid SOL; adding BTC/ETH back is one
edit of `capture/venues/hyperliquid/config.toml`, which both the collector and the recorder pick
up within 30 s when the file is written in place (after a replacement -- `git pull`, `sed -i` --
restart the collector; the recorder mounts the directory and follows the new file).

## Verdicts

Every row is `pending` until its story runs. Repro commands are filled in by the story that
delivers the comparator (`python -m verification.<tool> --venue V --day D`).

| Data type | Instrument | Verdict | Unexplained | Numbers | Repro |
|---|---|---|---|---|---|
| Drops and conservation (trade ids, rejected seconds) | all 5 | pending — tool built (Story 31.2); verdict pending the first full clean closed UTC day after the soak restart (Story 31.11 computes it) | | | `python3 -m verification.conservation --venue V --day D` (`docs/DATA_DICTIONARY.md` §1.16) |
| Derived signals vs reference implementations | all 5 | **VERIFIED** after fixes (DEVIATIONs D-77..D-88 and D-90 fixed in code; pinned Known limits D-89) | 0 DIFFERENT, 0 undefined mismatch in every family | Real rows: 300 stored rows per instrument, Bybit ts_event 2026-09-29T13:01:25.5Z..13:06:24.5Z, Hyperliquid 13:01:23.5Z..13:06:22.5Z (restarted soak, revision 5e324bbb8e; `verification/tests/fixtures/snapshots/README.md`). Fixture-only float noise / compared: mid 247/1500, spread 0/1500 (after D-88), volume_delta 80/1500, CVD 227/645, count OFI L10 w300 896/1500, depth-within-bps sums 6323/24109 defined, candle volume 31/80; microprice, OBI, USD OFI and the stdevs agree EXACT or within 1e-9. All inputs (12 seeds x 160 rows, 12 sparse 40-day series, 8 seeds x 360 rows, the 5 fixtures): e.g. count OFI 12206 float noise / 20520, candle volume 3675 / 34428, board fields 14650 / 55445, research simple returns 3 exact + 5148 within 1e-9 / 9576 (4425 both undefined). Six planted defects each caught (DIFFERENT > 0). Scope: the real fixtures are 300 gap-free two-sided rows each, so gaps, one-sided and crossed books, 1D/1W boundaries and the 1h/24h windows are covered by the seeded generators and golden cases only (31.11's longer soak is where real rows reach them). Families and tolerances: DATA_DICTIONARY §2.13 | `python3 -m pytest -o addopts="" --rootdir=. verification/tests/test_reference_signals.py verification/tests/test_reference_series.py -q -s` |
| Trades id-by-id + second fold | BTCUSDT-LINEAR | pending — tool built (Story 31.4); verdict pending Story 31.11 (smoke below is `--stage live`, provisional) | smoke: 0 | Smoke 2026-09-29T13:00-15:00Z (restarted soak; tool at f7c9e3ea38 + the Story 31.4 change set): seen 279,155, matched 279,155, missing/extra/duplicated 0, every field mismatch 0, conflict 0; seconds exact 7,200/7,200; latency ts_init − recv_ns min −137 / p50 −1 / p99 33 / max 161 ms; wire NO_AGGRESSOR 0 | `python3 -m verification.trades --venue V --day D --stage rebuilt` (`docs/DATA_DICTIONARY.md` §1.17) |
| | ETHUSDT-LINEAR | pending — as above | smoke: 0 | Smoke as above: seen 349,574, matched 349,574, all failing counts 0; seconds exact 7,200/7,200; latency −139 / −1 / 20 / 149 ms; NO_AGGRESSOR 0 | as above |
| | BTCUSDT-SPOT | pending — as above; smoke **DEVIATION** D-91 (OPEN) | smoke: 8 ids, 3 seconds (all D-91) | Smoke as above: seen 131,534, matched 131,534, missing/extra/duplicated 0, **mismatch_price 8** (sub-tick prints stored rounded, D-91), other mismatches 0; seconds exact 7,197, **off_grid 3** (the same prints); latency −296 / −2 / 4 / 120 ms; NO_AGGRESSOR 0 | as above |
| | ETHUSDT-SPOT | pending — as above | smoke: 0 | Smoke as above: seen 29,429, matched 29,429, all failing counts 0; seconds exact 7,200/7,200; latency −271 / −1 / 7 / 52 ms; NO_AGGRESSOR 0 | as above |
| | SOL-USD-PERP.HYPERLIQUID | pending — as above | smoke: 0 | Smoke as above: seen 11,464, matched 11,464, all failing counts 0; seconds exact 7,200/7,200; latency −154 / 21 / 280 / 394 ms; NO_AGGRESSOR 0 | as above |
| Stored book (1 s snapshot) vs rebuilt reference book | BTCUSDT-LINEAR | pending — Story 31.5 | | | |
| | ETHUSDT-LINEAR | pending — Story 31.5 | | | |
| | BTCUSDT-SPOT | pending — Story 31.5 | | | |
| | ETHUSDT-SPOT | pending — Story 31.5 | | | |
| | SOL-USD-PERP.HYPERLIQUID | pending — Story 31.5 | | | |
| Mark and index price | BTCUSDT-LINEAR, ETHUSDT-LINEAR | pending — Story 31.6 | | | |
| | SOL-USD-PERP.HYPERLIQUID | pending — Story 31.6 | | | |
| | BTCUSDT-SPOT, ETHUSDT-SPOT | n/a (spot has none) | | | |
| Funding rate | BTCUSDT-LINEAR, ETHUSDT-LINEAR | pending — Story 31.6 | | | |
| | SOL-USD-PERP.HYPERLIQUID | pending — Story 31.6 | | | |
| | BTCUSDT-SPOT, ETHUSDT-SPOT | n/a (spot has none) | | | |
| Open interest | BTCUSDT-LINEAR, ETHUSDT-LINEAR | pending — Story 31.6 | | | |
| | SOL-USD-PERP.HYPERLIQUID | pending — Story 31.6 | | | |
| | BTCUSDT-SPOT, ETHUSDT-SPOT | n/a (spot has none) | | | |
| Instrument definitions | all 5 | pending — Story 31.6 | | | |
| Catalog integrity and backtest-read parity | all 5 | pending — Story 31.7 | | | |
| Candles and klines, every timeframe | all 5 | pending — Story 31.8 | | | |
| Live, backtest and display parity (incl. the bot's signals) | all 5 | pending — Story 31.9 | | | |
| Fault injection: every loss accounted for | Bybit, Hyperliquid | pending — Story 31.10 | | | |

**Trades smoke (Story 31.4), how it was run.** The soak's first closed day is 2026-09-30, so the
smoke ran the tool's per-hour pieces (`verification.application.trades.check_hours`, the function
`check_day` runs over all 24 hours) on today's two complete hours, 13:00-15:00Z, read-only against
the running stack, `--stage live`. Hour 12 (the soak began 12:59:19Z) and hour 15 (still being
written) were excluded: their edges are boundary artefacts, not findings. The Bybit day would
also fail on its coverage record, which does not exist yet (D-92); the smoke reports it as
MISSING. `main` over the whole day refuses it, as it must (the hour 15 raw file is still open).

## Reference recorder footprint

Measured on the dev box over the first **1,026.7 s** of the soak: from the recorders' first
`open` line (10:20:27.4Z) to 10:37:34.1Z, one continuous connection per endpoint, no reconnect,
the durable ledger holding only each recorder's `process_start` line (no
`verification.recorder.*` entry, and none from any other service either).

| | Bybit recorder | Hyperliquid recorder | Method |
|---|---|---|---|
| Bytes on disk in the window | 6,633,105 B (15 files) | 438,161 B (7 files) | `find data/verification/raw/<venue> -type f -printf '%s\n'`, summed |
| **Extrapolated bytes/day** | **~558 MB/day** (6,460.8 B/s) | **~37 MB/day** (426.8 B/s) | window bytes / 1,026.66 s × 86,400, linear |
| Largest channels | `linear.orderbook.50` 48 %, `spot.orderbook.50` 28 %, `linear.publicTrade` 12 %, `linear.tickers` 8 % | `rest.metaAndAssetCtxs` 63 %, `l2Book` 15 %, `activeAssetCtx` 11 %, `trades` 10 % | per-channel `find` sums |
| Retention at `VERIFY_RETAIN_DAYS` = 7 | ~3.9 GB | ~0.26 GB | bytes/day × 7 |
| CPU, average | **4.9 %** of one core (50.89 s CPU) | **0.32 %** (3.29 s CPU) | cgroup `cpu.stat` `usage_usec` / 1,028.9 s since container start |
| CPU, `docker stats --no-stream` | 3.98 % (4.78 % at 10:30:40Z) | 0.15 % (0.16 %) | one instantaneous sample |
| Memory, `docker stats --no-stream` | **70.9 MiB** | **61.6 MiB** | cgroup usage minus page cache |
| Memory, cgroup `memory.current` / `memory.peak` | 77.9 / 79.2 MiB | 63.6 / 64.5 MiB | `/sys/fs/cgroup/system.slice/docker-<id>.scope` |
| Process `VmRSS` (= `VmHWM`) | 136.8 MiB | 127.5 MiB | `/proc/1/status` in the container; counts the shared pyarrow/aiohttp library pages the cgroup numbers do not |

For scale, the collectors over the same 1,028.9 s: `bybit_collector` 8.9 % of one core
(91.07 s), 203 MiB cgroup memory; `hyperliquid_collector` 1.5 % (15.58 s), 158 MiB.

Caveats: bytes/day is a linear extrapolation of one European-morning quarter-hour of a Tuesday;
Bybit's book traffic scales with market activity, so the owed 24 h figure is the day total the
recorder logs itself at every UTC hour rollover (`raw/<venue> bytes per UTC day: {...}` in its
log). Hyperliquid's volume is two-thirds its 30 s `metaAndAssetCtxs` poll, which covers every
listed asset whatever the plan holds.

### After the review patches (recorders rebuilt 10:57:19Z)

The review added a linear `tickers` REST poll per symbol and raised linear `recent-trade`'s `limit`
from 60 to the venue maximum 1000. Re-measured over the first 214 s of the 11:00Z hour
(11:00:00Z to 11:03:34Z, `find ... -name '2026-09-29T11.jsonl.zst'`):

| | Bybit recorder | Hyperliquid recorder |
|---|---|---|
| Bytes in the window | 1,721,007 B | 95,734 B |
| **Extrapolated bytes/day** | **~695 MB/day** (8,042 B/s) | **~39 MB/day** (447 B/s) |
| Of which `linear.rest.recent-trade` | 310,736 B, ~125 MB/day (18 %) | -- |
| Of which `linear.rest.tickers` | ~1 KB per poll | -- |

So `recent-trade` at `limit=1000` is the main cost of the patches (~+125 MB/day); at 7 days'
retention Bybit needs ~4.9 GB. Treat these as one short window's rates: the recorder's own
hourly `bytes per UTC day` log line is the measure for a whole day.
