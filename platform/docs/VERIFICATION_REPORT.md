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

## Verdicts (Story 31.11)

> **Precondition unmet: no full clean closed UTC day exists yet.** The epic asked for one full
> closed UTC day of soak with an unchanged plan. As of 2026-10-01 there is none (audit **D-138**,
> OPEN):
> - 2026-09-29 ran only 12:59:19Z-20:42:04Z, then the dev box suspended (D-128).
> - 2026-09-30 was recorded only 04:39:15Z-20:15:40Z. Inside that it carries the 31.10 chaos
>   windows (09:50:48-11:05:25Z, 16:29:07-16:50:02Z, 17:06:48-17:07:48Z, each with its 90 s / 180 s
>   margins), the stack stop 11:23:05-16:00:12Z, host stalls 19:22-19:37Z and two more host
>   suspends (19:37:54-20:02:19Z and 20:15:40Z-2026-10-01 04:46:52Z).
> - 2026-10-01 lost 06:08Z to about 15:35Z: the machine was shut down and the whole verify stack
>   restarted at boot. So 2026-10-02 is the first day that can be clean.
>
> So every verdict below is **windowed**, and each row names its window:
> - **W29**: 2026-09-29 12:59:19Z-20:42:04Z. The day-level runs of `archive.verify_day`,
>   classified per second; the conservation window is 13:00-20:42Z.
> - **W30**: 2026-09-30's clean conservation windows: 05:00-09:49, 11:09-11:22, 16:02-16:27,
>   16:54-17:05, 17:11-19:00 and 19:00-20:02Z. For the other tools, the first nightly
>   `verify_day` of 2026-09-30 was classified over 05:00-09:49, 11:09-11:22, 16:07-16:27 (Bybit)
>   or 16:17-16:27 (Hyperliquid), 16:54-17:05 and 17:11-19:22Z.
>
> The **full-day verdict** is left to the nightly `verify_day` step. DEPLOY_CHECKLIST 31-11 reads
> `verification_days` for the first full clean day and fills the "Full day" column, which closes
> D-138. A partial day is never called VERIFIED without its window.

**Verdict classes.**
- **VERIFIED:** 0 unexplained inside the window. Explained losses are allowed and are named,
  because they are accounted for.
- **DEVIATION:** a residue with a known mechanism and an audit row.
- **OPEN:** a residue whose mechanism is not proven, or a decision still owed.

Every failing count outside the window is classified as a partial-day artefact: before the soak,
before capture's first row (D-76), a missing raw hour, an excluded period, or a suspend (D-128).
No such count is ignored.

**Revisions.**
- **Data:** the 2026-09-29 data was captured by collectors at `5e324bbb8e` (Story 31.2).
- **2026-09-30 before 16:29:07Z:** the 31.10 round-1 change set (09:50:48Z on).
- **2026-09-30 from 16:29:07Z:** the 31.10 final code (`557e50a285`).
- **Tools:** every tool ran at `557e50a285` plus this story's change set (the `verify-archive`
  image, built 2026-09-30 19:07Z). The verify `data_api` and `ranking_engine` images were rebuilt
  from the same tree at 19:04Z, the owed 31-9 item. Those two redeploys are not in the scenario
  log; neither touched a collector or a recorder.
- **Re-run, 2026-10-01** (Story 31.11's final tree, `557e50a285` plus the change set). The verify
  `data_api`, `ranking_engine` and `archive` images were rebuilt and recreated with `--no-deps` at
  15:47:50-15:48:03Z, and `archive` again at 16:11:48Z with the D-145 fix. Each redeploy is a
  `deploy` line in `data/verification/chaos/scenarios.jsonl`. Then:
  - every W30 conservation window and the W29 window (R-win) gave identical reports, except that
    `truncated_neighbour_files` is now empty (hour 19 of 2026-09-30 was still open at the first
    run);
  - `verify_day` for 2026-09-29 (R-day) reproduced every count and every per-instrument figure of
    the first run on both venues, after the D-145 repair below. The only differences are not
    failing: one more stored definition `ts_init` (the 15:36Z restart), and the rehearsal digest
    of Bybit's snapshot leaf, whose set includes the midnight-crossing file that the 2026-09-30
    rebuild rewrote (the day's parity digests are unchanged).
  - **D-145, found by this re-run.** Before the repair, the 2026-09-29 `candles` and `catalog`
    candle checks had gone from 0 to 602-603 failing buckets per instrument. The 2026-09-30
    nightly's `build_candles --day 2026-09-30` had rebuilt 2026-09-29 from the one snapshot file
    crossing its midnight, leaving that day only its 20:41-20:42Z bars. Fixed in
    `candles/application/rebuild.py`, and the verify stores rebuilt for 2026-09-29. The VPS needs
    the same store rebuild (DEPLOY_CHECKLIST 31-11).

**Repro.** All commands run from `platform/` with
`VERIFY_DATA_DIR=data/verification CATALOG_PATH=data/catalog BYBIT_COLLECTOR_CONFIG=capture/venues/bybit/config.toml HYPERLIQUID_COLLECTOR_CONFIG=capture/venues/hyperliquid/config.toml`.
- **R-day:** one venue-day, all six tools, each tool's full report kept:
  `docker exec verify-archive python3 -m archive.verify_day --catalog /app/catalog --candles-dir /app/candles_dir --venue V --day D --result-file /app/verify_data/scratch/verify_V_D.json --reports-dir /app/verify_data/scratch/reports-D/V`
- **R-win:** one conservation window:
  `python3 -m verification.conservation --venue V --start S --end E --json`

| Data type | Instrument | Verdict | W29 (2026-09-29) | W30 (2026-09-30) | Full day |
|---|---|---|---|---|---|
| Drops and conservation (every trade id, every second) | BTCUSDT-LINEAR | **VERIFIED** (windowed) | 13:00-20:42Z: seen = archived 807,787; seconds 27,712 rows + 8 `stale` of 27,720; backfilled 38; **unexplained 0**. Day: 47,788 failing = 46,765 s + 1,023 ids, all before capture's first row 12:59:24Z | 6 windows: seen 513,946, archived 506,122, backfilled 3,604, `ledgered_unrecoverable` 7,824 (D-139 silent feed 16:00-16:07Z, the suspend); seconds 28,163 rows + `stale` 931 + `catch_up_cap` 1,446 of 30,540; **unexplained 0** | pending (D-138) |
| | ETHUSDT-LINEAR | **VERIFIED** (windowed) | seen = archived 1,081,448; rows 27,712 + 8 `stale`; backfilled 11; unexplained 0. Day: 47,789 = 46,765 + 1,024, all before 12:59:24Z | seen 541,888, archived 534,974, backfilled 4,022, unrecoverable 6,914; rows 28,163 + 931 + 1,446; unexplained 0 | pending |
| | BTCUSDT-SPOT | **VERIFIED** (windowed) | seen 388,949, archived 388,888; rows 27,719 + 1 `stale`; unrecoverable 61 (D-125, 18:00:31Z); 41 `archived_not_seen` (the recorder's own 17:28:35-37Z reconnect, D-143); unexplained 0. Day: 46,825 = 46,765 + 60, all before 12:59:24Z | seen 292,544, archived 284,269, backfilled 279, unrecoverable 8,275; rows 28,143 + 951 + 1,446; unexplained 0 | pending |
| | ETHUSDT-SPOT | **VERIFIED** (windowed) | seen 119,490, archived 119,476; rows 27,719 + 1; backfilled 40, unrecoverable 14 (D-125); unexplained 0. Day: 46,829 = 46,765 + 64, all before 12:59:24Z | seen 73,632, archived 72,263, backfilled 407, unrecoverable 1,369; rows 28,143 + 951 + 1,446; unexplained 0 | pending |
| | SOL-USD-PERP.HYPERLIQUID | **VERIFIED** (windowed) | seen = archived 33,569; rows 27,720 of 27,720; unexplained 0. Day: 46,765 = 46,762 s + 3 ids, all before capture's first row 12:59:21Z (the WS subscribe snapshot) | seen 32,920, archived 31,963, backfilled 8, unrecoverable 957 (D-139: 16:00-16:17Z); rows 28,029 + `stale` 1,063 + `catch_up_cap` 1,448; unexplained 0 | pending |
| Derived signals vs reference implementations | all 5 | **VERIFIED** (Story 31.3; fixture rows of the restarted soak) | unchanged: see the smoke table's row | | n/a (no day input) |
| Trades id-by-id + second fold (`--stage rebuilt`) | BTCUSDT-LINEAR | **VERIFIED** (windowed) | matched 808,317 of 809,340 seen; seconds exact 27,751; every mismatch class 0. Day: 1,082 failing = 1,023 ids + 59 `missing_row`, all 12:57:54-12:59:22Z, before the first row | 0 in the clean windows. Day: 2,258 = restart edge 15:59-16:00:17Z (1,177) + the 19:36Z host stall (1,080) | pending |
| | ETHUSDT-LINEAR | **VERIFIED** (windowed) | matched 1,082,397 of 1,083,421; exact 27,751; 0. Day: 1,078 = 1,024 + 54, hour 12 | 0. Day: 1,492 = restart edge 1,283 + stall 207 | pending |
| | BTCUSDT-SPOT | **DEVIATION D-91** (OPEN) + verifier edge **D-143** (OPEN) | matched 389,284 of 389,405; exact 27,731; **`mismatch_price` 32 + `off_grid` 22 s** (sub-tick prints stored rounded, D-91); **`extra_unexplained` 41 + `archive_differs` 3 s** at 17:28:35-37Z (captured live while the recorder was disconnected, D-143); 61 `missing_explained` + 2 `explained_loss` (D-125). Day: 162 = 64 before the first row + 98 above | **6 = D-91** (06:14:37, 16:19:04, 19:17:18Z). Day: 373, the rest at the restart edge, the stall, the 20:03Z resume and chaos | pending |
| | ETHUSDT-SPOT | **VERIFIED** (windowed) | matched 119,706 of 119,784; exact 27,756; 14 `missing_explained` + 2 `explained_loss` (D-125); 0. Day: 80, hour 12 | 0. Day: 182 = edge 93 + stall 63 + resume 26 | pending |
| | SOL-USD-PERP.HYPERLIQUID | **VERIFIED** (windowed), verifier edge **D-143** on W30 | matched 33,602 of 33,629; exact 27,759; 24 `missing_explained` (`stale`); 0. Day: 23, before 12:59:20Z | **16 = D-143** (15 extras + 1 second at 07:41:01.8-03.4Z, the recorder's own "Expired" reconnect). Day: 151, the rest at the stall and the 20:02Z resume | pending |
| Book top-20 vs rebuilt book | BTCUSDT-LINEAR | **DEVIATION D-102 / D-141** (OPEN) | exact 27,703 of 27,751; REST failing 0 (agree_key 0, agree_bracket 351, between_pushes 111) so the reference is validated; `u_breaks` 0; **42 `content_differs`**. Each one is a book message applied after its second closed, clustered at 17:35:46, 18:39-18:44, 19:06-19:07, 19:17, 19:40Z. In ~40 % of them the recorder also received it late (D-141). 11 of the 42 hold a frozen book inside a capture-only feed stall (D-142). Day: 46 = 42 + 4 `missing_row` before the first row | **1** (17:33:06-07Z, both recorder streams paused 1.3-1.5 s: D-141). Day: 60, the rest in excluded periods | pending |
| | ETHUSDT-LINEAR | **DEVIATION D-102 / D-141** (OPEN) | exact 27,701 of 27,751; REST 3 / 330 / 129; 46 `content_differs`, as BTCUSDT-LINEAR. Day: 51 = 46 + 5 | **1** (17:33:06Z, D-141). Day: 60 | pending |
| | BTCUSDT-SPOT | **DEVIATION D-102 / D-141** (OPEN) | exact 27,699 of 27,758; REST 100 / 295 / 67; 55, including 15:42:06-07Z (the original D-102) and 18:00:31Z (D-125). Day: 60 = 55 + 5 | **2** (17:33:06-07Z). Day: 53 | pending |
| | ETHUSDT-SPOT | **DEVIATION D-102 / D-141** (OPEN) | exact 27,701 of 27,758; REST 69 / 316 / 77; 53 + 1 `boundary_early`. Day: 59 = 54 + 5 | **2**. Day: 51 | pending |
| | SOL-USD-PERP.HYPERLIQUID | **OPEN D-140** + DEVIATION D-101 | exact 27,402 of 27,759; REST agree_key 53, between_pushes 409, disagree 0. **337 s in 61 one-push runs, 16:02:00-18:54:22Z** (best prices equal, level sizes differ between the two connections, D-140); 8 s at capture's resubscribe replies 15:51:55-56Z and 18:51:04-09Z (D-101); 1 at 19:07:16Z (host stall) | **235** (one run at 07:34:47-50Z, then about 45 one-push runs 16:54-19:14Z, D-140). Day: 362 | pending |
| Mark price | BTCUSDT-LINEAR | **VERIFIED** (windowed) | exact 22,001 (+1 agree_state); failing 0; REST agree_key 925 | 0 in the windows. Day: 112, all in excluded periods | pending |
| | ETHUSDT-LINEAR | **VERIFIED** (windowed) | exact 21,075; failing 0 | 0. Day: 87 | pending |
| | SOL-USD-PERP.HYPERLIQUID | **DEVIATION D-112** (OPEN) | exact 6,445; **5 failing**: each value was stored 1.05-2.95 s from the recorder's receipt, so it fails the 1 s match bound (19:06:34-19:07:19Z, the host stall). No value lost | 0. Day: 58 | pending |
| Index price | BTCUSDT-LINEAR | **VERIFIED** (windowed); read **OPEN D-113** | exact 49,276; failing 0. The catalog cannot read it back: see the catalog row | 0. Day: 201 | pending |
| | ETHUSDT-LINEAR | as above | exact 48,781; failing 0 | 0. Day: 206 | pending |
| | SOL-USD-PERP.HYPERLIQUID | **DEVIATION D-112** (OPEN); read OPEN D-113 | exact 7,510; **8 failing** (117.525 at 17:50:52Z, 118.975 at 18:39:37-38Z, the 19:06-19:07Z stall); each value stored 1.05-2.95 s off | 0. Day: 73 | pending |
| Funding rate | BTCUSDT-LINEAR | **VERIFIED** (windowed); D-103/D-104 documented | exact 235; failing 0 | 0. Day: 1 | pending |
| | ETHUSDT-LINEAR | as above | exact 201; failing 0 | 0. Day: 4 | pending |
| | SOL-USD-PERP.HYPERLIQUID | **VERIFIED** (windowed); D-108 documented | exact 1,781; failing 0 | 0. Day: 39 | pending |
| Open interest | BTCUSDT-LINEAR | **VERIFIED** (windowed); D-105 documented | agree_state 92 of 92; failing 0. Day: 2 `poll_gaps` at the day's edges (00:00-13:04:26Z, 20:40:26-24:00Z) | 0. Day: 3 `poll_gaps` (00:00-04:42, 19:28-20:03, 20:13-24:00Z: the suspends) | pending |
| | ETHUSDT-LINEAR | as above | agree_state 92 of 92; failing 0. Day: 2 edge gaps | 0. Day: 3 gaps | pending |
| | SOL-USD-PERP.HYPERLIQUID | **DEVIATION D-112** (OPEN) | **9 failing**, the same 19:06-19:07Z timing class (5626185.10 stored +2.35 s) | **1** (OI 5664889.82: reference 17:49:09.541Z, stored 17:49:10.632Z, +1.09 s). Day: 89 | pending |
| Mark, index, funding, open interest | BTCUSDT-SPOT, ETHUSDT-SPOT | **VERIFIED** n/a | 0 fabricated rows | 0 fabricated | pending |
| Instrument definitions | all 5 | **VERIFIED** (windowed); D-106 documented | agree 925, differs 0 on every instrument | 0 | pending |
| Catalog integrity and backtest-read parity | BTCUSDT-LINEAR | **OPEN D-113** (index read) + verifier edge **D-144** (OPEN) | structure 0 failing except 2 index files `open_failed` (D-113); parity: trades 808,317 (`45d309a2`) and snapshots 27,751 (`012b35b0`) stored = query = received; candle fold exact 330, float_noise 273 (D-115), 0 different; rehearsal `not_exercised` (D-116: the step runs after consolidation) | **602 `beyond_margin`**: exactly the trades with `ts_init - ts_event` > 60 s, all reconnect backfills (04:37:12-04:38:19Z, archived 04:39:20Z at the resume). The three legs are digest-identical, so the data is right (D-144) | pending |
| | ETHUSDT-LINEAR | as above | D-113 2 files; 1,082,397 (`5224bf6d`) / 27,751 (`b52d340d`); fold 325 / 278 | **605** (527 resume backfill + 78 from the 16:06:47Z silent-feed backfill, D-144) | pending |
| | BTCUSDT-SPOT | **VERIFIED** (windowed) | 389,325 (`3fe11e91`) / 27,758 (`4825ef01`); fold 334 / 269; failing 0 | 0 | pending |
| | ETHUSDT-SPOT | **VERIFIED** (windowed) | 119,706 (`e03afed3`) / 27,758 (`5bd30533`); fold 344 / 259; failing 0 | 0 | pending |
| | SOL-USD-PERP.HYPERLIQUID | **OPEN D-113** (index read) | 2 index files `open_failed`; 33,602 (`4c08b6a4`) / 27,759 (`b29601f5`); fold 318 / 284 + 1 `both_undefined` | D-113 (2 files) | pending |
| Candles, every timeframe (stored, served, reference) | BTCUSDT-LINEAR | **VERIFIED** (windowed); klines OPEN D-51 | 0 failing inside the soak. Fold and served 0 failing on every width; **`partial_mismatch` 0** (the rebuilt data_api serves the D-119 fix; the 31.8 smoke's old image had 48 / 17 / 11); reference exact on every traded bucket. Day: 46,765 = 46,759 before the soak + 6 before the first row | 0 in the windows. Day: 9 `ref_different` buckets from the 16:00Z restart edge and the 19:2x stall | pending |
| | ETHUSDT-LINEAR | as above | 0 inside the soak (as above) | 0. Day: 9 | pending |
| | BTCUSDT-SPOT | **DEVIATION D-127 / D-91** (OPEN) | **`ref_different` 55** (15 / 10 / 6 / 4 / 2 / 1 / 7 / 5 / 5 at 1m / 5m / 15m / 1h / 4h / 1d / 10m / 30m / 45m: the 22 sub-tick seconds); `ref_recorder_gap` 17:28:35-37Z and `ref_explained` 18:00:31Z (D-125) do not fail | D-91 / D-127 seconds 06:14:37, 16:19:04, 19:17:18Z; 16:07:36Z is the end of the D-139 `trades_unrecoverable` window. Day: 42 | pending |
| | ETHUSDT-SPOT | **VERIFIED** (windowed) | 0; `ref_explained` 18:00:31Z (D-125) | 0. Day: 9 | pending |
| | SOL-USD-PERP.HYPERLIQUID | **VERIFIED** (windowed); klines OPEN D-126 | 0 inside the soak. Day: 46,762 = 46,759 + 3 | 0 (07:41:02Z is `ref_recorder_gap`). Day: 9 | pending |
| Live, backtest and display parity (incl. the bot's signals) | all 5 | **OPEN** D-135 (replay order), D-133/D-134 (Bybit bot inputs), D-129 (gating), D-113 | unchanged since Story 31.9: see the smoke table's row | | n/a (the 31.9 fleet run) |
| Fault injection: every loss accounted for | Bybit, Hyperliquid | **VERIFIED** for 18 judged windows; `network_cut` not run (sudo, DEPLOY_CHECKLIST 31-10); **OPEN** D-137 | | 9 windows per venue, 0 unexplained (Story 31.10) | n/a |

**What the windowed verdicts say.** Inside the windows, every trade the venue sent is either
archived or accounted for: conservation finds 0 unexplained on all 5 instruments over about
13.9 h of 2026-09-29 and 2026-09-30. Every archived trade and every second's trade fold equals the
venue's, except BTCUSDT-SPOT's sub-tick prints (D-91). Mark, index, funding, open interest and
definitions equal the venue on both Bybit linears. On Hyperliquid the only residue is the 1 s match
bound (D-112).

The stored book is the open front:
- **Bybit:** about 45 seconds a day per instrument hold a book that misses messages delivered late,
  often late to both clients (D-102/D-141).
- **Hyperliquid:** about 2-6 % of seconds hold `l2Book` level sizes that differ from the other
  connection's (D-140).

**Explained losses inside W30 that the conservation pass does not excuse.** At the 16:00Z stack
start, capture's first WS connections stayed silent for 373.7 s (Bybit linear; spot until
about 16:07:36Z) and 989.6 s (Hyperliquid), while the recorders' own watchdogs reconnected after
30 s. Capture never forces a reconnect of a silent feed, so those seconds have no row and those
trades are `unrecoverable` (D-139, OPEN).

**OPEN decisions carried from Story 31.9** (DEPLOY_CHECKLIST 31-9; none blocks a verdict above,
each recorded with its recommended option):
- **D-113**, `IndexPriceUpdate` has no catalog decoder. Recommended: (1) upgrade Nautilus to a
  release that decodes it, the same upgrade as D-133. The catalog row's `open_failed` must then
  read 0. Meanwhile index prices are read only through `kernel.catalog_files.query_index_prices`.
- **D-129**, `DummyStrategy` gating and OFI reset. Recommended: (1) adopt the Known limit's
  upgrade path (skip and log a stale, crossed or gapped book; reset the OFI past `OFI_GAP_NS`).
- **D-133 / D-134**, Bybit bot quotes and spot book from the pinned adapter. Recommended: (1)
  upgrade Nautilus to a release whose Bybit handler keeps the quote and book topics apart,
  together with D-113 and D-132. Until then no Bybit dummy-bot result is trustworthy.
- **D-135**, the bot replay's per-row delta order. Recommended: (1) stamp a row's deltas
  `ts_init + i` ns, `CLEAR` first, then re-run `bots.signal_replay` and `verification.bot_parity`.

**The permanent gate.** From this story on, every nightly saga ends with `verify_day`
(`docs/DATA_DICTIONARY.md` §1.24). On a stack with reference recorders it runs the six tools over
the closed day and keeps one verdict per type in `archive:status` `verification_days`; every
non-passed type is ledgered at `archive.verify_day`.

The first nightly to run it was the verify stack's, at 2026-10-01 04:47-05:18Z, for 2026-09-30.
Its results:
- **Bybit:** `findings` (conservation 2,696, trades 4,305, book 224, derivs 617, catalog 1,211,
  candles 113; 44 missing raw hours). Run time 1,640.7 s, peak child RSS 1,534 MB.
- **Hyperliquid:** `findings` (2 / 151 / 362 / 260 / 2 / 20; 11 missing hours). Run time 61.2 s.
- **DYDX:** `no reference data`, exit 0.

Every one of those counts is classified in the W30 column above. The watermarks advanced to
2026-09-30 and no step failed (`failed steps: []`). Pruning ran before the step, so it was not
held. The collectors lost no second to the step's load on the dev box: the only `stale` runs after
the resume are 04:47-04:48Z, before the nightly started.

## Story smokes (31.2-31.10): the evidence behind each tool

Each row is the tool's first run, as the story that delivered it ran it, on a partial window.
The verdicts are the section above. Repro commands are each tool's own
(`python -m verification.<tool> --venue V --day D`).

| Data type | Instrument | Verdict | Unexplained | Numbers | Repro |
|---|---|---|---|---|---|
| Drops and conservation (trade ids, rejected seconds) | all 5 | pending — tool built (Story 31.2); verdict pending the first full clean closed UTC day after the soak restart (Story 31.11 computes it) | | | `python3 -m verification.conservation --venue V --day D` (`docs/DATA_DICTIONARY.md` §1.16) |
| Derived signals vs reference implementations | all 5 | **VERIFIED** after fixes (DEVIATIONs D-77..D-88 and D-90 fixed in code; pinned Known limits D-89) | 0 DIFFERENT, 0 undefined mismatch in every family | Real rows: 300 stored rows per instrument, Bybit ts_event 2026-09-29T13:01:25.5Z..13:06:24.5Z, Hyperliquid 13:01:23.5Z..13:06:22.5Z (restarted soak, revision 5e324bbb8e; `verification/tests/fixtures/snapshots/README.md`). Fixture-only float noise / compared: mid 247/1500, spread 0/1500 (after D-88), volume_delta 80/1500, CVD 227/645, count OFI L10 w300 896/1500, depth-within-bps sums 6323/24109 defined, candle volume 31/80; microprice, OBI, USD OFI and the stdevs agree EXACT or within 1e-9. All inputs (12 seeds x 160 rows, 12 sparse 40-day series, 8 seeds x 360 rows, the 5 fixtures): e.g. count OFI 12206 float noise / 20520, candle volume 3675 / 34428, board fields 14650 / 55445, research simple returns 3 exact + 5148 within 1e-9 / 9576 (4425 both undefined). Six planted defects each caught (DIFFERENT > 0). Scope: the real fixtures are 300 gap-free two-sided rows each, so gaps, one-sided and crossed books, 1D/1W boundaries and the 1h/24h windows are covered by the seeded generators and golden cases only (31.11's longer soak is where real rows reach them). Families and tolerances: DATA_DICTIONARY §2.13 | `python3 -m pytest -o addopts="" --rootdir=. verification/tests/test_reference_signals.py verification/tests/test_reference_series.py -q -s` |
| Trades id-by-id + second fold | BTCUSDT-LINEAR | pending — tool built (Story 31.4); verdict pending Story 31.11 (smoke below is `--stage live`, provisional) | smoke: 0 | Smoke 2026-09-29T13:00-15:00Z (restarted soak; tool at f7c9e3ea38 + the Story 31.4 change set): seen 279,155, matched 279,155, missing/extra/duplicated 0, every field mismatch 0, conflict 0; seconds exact 7,200/7,200; latency ts_init − recv_ns min −137 / p50 −1 / p99 33 / max 161 ms; wire NO_AGGRESSOR 0 | `python3 -m verification.trades --venue V --day D --stage rebuilt` (`docs/DATA_DICTIONARY.md` §1.17) |
| | ETHUSDT-LINEAR | pending — as above | smoke: 0 | Smoke as above: seen 349,574, matched 349,574, all failing counts 0; seconds exact 7,200/7,200; latency −139 / −1 / 20 / 149 ms; NO_AGGRESSOR 0 | as above |
| | BTCUSDT-SPOT | pending — as above; smoke **DEVIATION** D-91 (OPEN) | smoke: 8 ids, 3 seconds (all D-91) | Smoke as above: seen 131,534, matched 131,534, missing/extra/duplicated 0, **mismatch_price 8** (sub-tick prints stored rounded, D-91), other mismatches 0; seconds exact 7,197, **off_grid 3** (the same prints); latency −296 / −2 / 4 / 120 ms; NO_AGGRESSOR 0 | as above |
| | ETHUSDT-SPOT | pending — as above | smoke: 0 | Smoke as above: seen 29,429, matched 29,429, all failing counts 0; seconds exact 7,200/7,200; latency −271 / −1 / 7 / 52 ms; NO_AGGRESSOR 0 | as above |
| | SOL-USD-PERP.HYPERLIQUID | pending — as above | smoke: 0 | Smoke as above: seen 11,464, matched 11,464, all failing counts 0; seconds exact 7,200/7,200; latency −154 / 21 / 280 / 394 ms; NO_AGGRESSOR 0 | as above |
| Book top-20 vs rebuilt book | BTCUSDT-LINEAR | pending — tool built (Story 31.5); verdict pending Story 31.11 | smoke: 0 (the 4 `missing_row` are the soak's first seconds, D-76; the tool still counts them failing, so the smoke exits 1) | Smoke 2026-09-29 12:59:19Z-16:00Z (restarted soak; tool at 46ba96b7bc + the Story 31.5 change set): REST agree_key 0, agree_bracket 124, between_pushes 56, unaligned 1, disagree_key 0, persistent_disagreement 0 -> reference validated; replay 452,507 frames, 1 snapshot, u_breaks 0, zero_level_messages 0; seconds exact **10,834/10,834** verified, missing_row 4 (12:59:21-24Z, before capture's first row: D-76), reference_unavailable 4,202 at the review rerun (rows after 16:00Z, beyond the closed raw hours; it grows with the live catalog); boundary_late 0 at the 500 ms margin. Frame counts here are all book frames of the instrument in 12:59:19-16:00Z (the delta-only counts over 12:59:19-16:12Z are in D-96/D-98); the 181 polls are 12:59:19-16:00Z | `python3 -m verification.book --venue V --day D` (`docs/DATA_DICTIONARY.md` §1.18) |
| | ETHUSDT-LINEAR | pending — as above | smoke: 0 (5 `missing_row`, D-76; failing in the tool, exit 1) | Smoke as above: REST agree_key 2, agree_bracket 108, between_pushes 70, unaligned 1, failing 0 -> validated; replay 478,062 frames, u_breaks 0, zero_level 0; exact **10,834/10,834**, missing_row 5 (12:59:20-24Z, D-76) | as above |
| | BTCUSDT-SPOT | pending — as above; smoke **DEVIATION** D-102 (OPEN) | smoke: 2 seconds (D-102), 5 `missing_row` (D-76) | Smoke as above: REST agree_key 31, agree_bracket 116, between_pushes 33, unaligned 1, failing 0 -> validated; replay 377,663 frames, u_breaks 0, zero_level 0 (spot `u` strictly +1, D-98); exact 10,832/10,834, **content_differs 2** (15:42:06-07Z: capture applied 32 spot book messages after their second closed, D-102) | as above |
| | ETHUSDT-SPOT | pending — as above; smoke **DEVIATION** D-102 (OPEN) | smoke: 2 seconds (D-102), 5 `missing_row` (D-76) | Smoke as above: REST agree_key 25, agree_bracket 112, between_pushes 43, unaligned 1, failing 0 -> validated; replay 294,165 frames, u_breaks 0, zero_level 0; exact 10,832/10,834, **content_differs 2** (15:42:06-07Z, 29 late messages, D-102) | as above |
| | SOL-USD-PERP.HYPERLIQUID | pending — as above; smoke **DEVIATION** D-101 (OPEN) | smoke: 2 seconds (D-101) | Smoke as above: REST agree_key 14 (all 14 key matches equal), between_pushes 166, unaligned 1, failing 0 -> validated; replay 2,016 `l2Book`, time_regress 0, subscribe_replies 2 (the recorder's startup and its 15:58:34Z reconnect, D-101); exact 10,825/10,827, **content_differs 2** (15:51:55-56Z: capture's own resubscribe reply after the venue expired its connection at 15:51:54.7Z, D-101) | as above |
| Mark and index price | BTCUSDT-LINEAR | pending — tool built (Story 31.6); verdict pending Story 31.11 | smoke: 0 | Smoke 2026-09-29, raw 12:59:19Z-17:00Z (restarted soak; tool at 1d8204ee09 + the Story 31.6 change set). **Mark:** REST agree_key 481, unaligned 1 (before the recorder's first snapshot) -> validated; rows exact **12,253**, value_mismatch/unmatched/off_grid/ts_rule/duplicate 0; every frame carrying `markPrice` stored (updates stored 12,253, not_stored 0); label 2 = definition; the other rows are `reference_unavailable` (after 17:00:05Z, beyond the closed raw hours, and the collector's startup row inside the recorder's startup gap with no recorded frame of its key; it grows with the live catalog: 4,254 at the 18:30Z rerun). **Index:** REST agree_key 481, unaligned 1; exact **30,354**, failing 0; updates stored 30,354, not_stored 0; label 2 | `python3 -m verification.derivs --venue V --day D` (`docs/DATA_DICTIONARY.md` §1.19) |
| | ETHUSDT-LINEAR | pending — tool built (Story 31.6); verdict pending Story 31.11 | smoke: 0 | Smoke as above. **Mark:** REST agree_key 480, agree_bracket 1, unaligned 1; exact **12,169**, failing 0; updates stored 12,169, not_stored 0; label 2. **Index:** REST agree_key 476, agree_bracket 5, unaligned 1; exact **29,421**, failing 0; updates stored 29,421, not_stored 0; label 2 | as above |
| | SOL-USD-PERP.HYPERLIQUID | pending — tool built (Story 31.6); verdict pending Story 31.11 | smoke: 0 | Smoke as above. **Mark:** REST agree 482 -> validated; exact **3,628** + agree_state 1 (every change of the `markPx` string, the adapter's filter; the agree_state row is the collector's startup row, equal to a recorded frame but not a change), failing 0; updates stored 3,628, not_stored 0; label 4 = definition; `ts_event == ts_init` on every row. **Index (`oraclePx`):** REST agree 482; exact **4,085** + agree_state 1, failing 0; updates stored 4,085, not_stored 0; label 4. The 18:45Z rerun over hour 17 too: **not_stored 1** (17:50:52Z, 117.525), a false fail of the 1 s match bound: capture stored the value +1,048 ms after the recorder's receipt, one push later (D-112, OPEN) | as above |
| | BTCUSDT-SPOT, ETHUSDT-SPOT | n/a (spot has none); smoke: **0 fabricated** rows under `mark_price_update`/`index_price_update` for either spot id | | | as above |
| Funding rate | BTCUSDT-LINEAR | pending — tool built (Story 31.6); verdict pending Story 31.11; **DEVIATION** D-103 (null `interval`/`next_funding_ns` on delta rows, documented), D-104 (`next_time_only`, Known limit) | smoke: 0 | Smoke as above: REST agree_key 481, unaligned 1; exact **99** + the collector's startup row (its 100th: `reference_unavailable` at 18:30Z, `agree_state` at the 18:45Z rerun), failing 0 (the whole triple compared: rate, interval, next time); updates stored 99, unchanged 0, next_time_only 0, not_stored 0 | as above |
| | ETHUSDT-LINEAR | as above | smoke: 0 | Smoke as above: REST agree_key 481, unaligned 1; exact **62** + the collector's startup row (its 63rd, as BTCUSDT's), failing 0; updates stored 62, next_time_only 0, not_stored 0 | as above |
| | SOL-USD-PERP.HYPERLIQUID | pending — tool built (Story 31.6); verdict pending Story 31.11; D-108 (scientific-notation text, documented) | smoke: 0 | Smoke as above: REST agree 482; exact **1,411** + agree_state 1, failing 0 (`interval` 60, `next_funding_ns` null on every row); updates stored 1,411, not_stored 0 | as above |
| | BTCUSDT-SPOT, ETHUSDT-SPOT | n/a (spot has none); smoke: **0 fabricated** | | | as above |
| Open interest | BTCUSDT-LINEAR | pending — tool built (Story 31.6); verdict pending Story 31.11; **DEVIATION** D-105 (`ts_event` is the poll's local clock, documented) | smoke: 0 (the 2 `poll_gaps` are the partial day: 00:00Z-13:04:26Z before the soak's first poll, D-76, and 17:55Z-24:00Z after the run; the tool counts them failing, so the smoke exits 1) | Smoke as above: REST agree_key 481, unaligned 1 (REST `openInterest` = the WS state at the response's `time` in every aligned poll); rows agree_state **48** of 48 judged (each equal to the WS state within 2 s before `ts_event`), unmatched 0, ts_rule 0; period 300 s, rows 300.4-301.2 s apart inside the soak | as above |
| | ETHUSDT-LINEAR | as above | smoke: 0 (2 partial-day `poll_gaps`, as above) | Smoke as above: REST agree_key 481, unaligned 1; agree_state **48** of 48, failing 0 | as above |
| | SOL-USD-PERP.HYPERLIQUID | pending — tool built (Story 31.6); verdict pending Story 31.11 | smoke: 0 | Smoke as above: REST agree 474, between_pushes 8 (REST values no WS frame of the day held: open interest moves on every fill and the WS push samples it) -> validated; exact **4,875**, failing 0; updates stored 4,875, not_stored 0 | as above |
| | BTCUSDT-SPOT, ETHUSDT-SPOT | n/a (spot has none); smoke: **0 fabricated** under `custom_open_interest`/`funding_rate_update` | | | as above |
| Instrument definitions | all 5 | pending — tool built (Story 31.6); verdict pending Story 31.11; SOL **DEVIATION** D-106 (`lot_size` 1 is Nautilus's default, not venue-declared, not compared) | smoke: 0 | Smoke as above: each instrument agree **481**, differs 0, before_first_definition 1 (the recorder's first poll preceded the collector's start), no venue change; one stored definition each (the collector's 12:59:21-23Z start). Every field compared: price/size precision, price/size increment, lot size (Bybit), min quantity, multiplier | as above |
| Catalog integrity and backtest-read parity | all 5 | pending — tool built (Story 31.7); verdict pending Story 31.11 (it must run the tool **before** the nightly consolidates D, or the rehearsal reads `not_exercised`, D-116); smoke **OPEN** D-113 (no Nautilus decoder for `IndexPriceUpdate`: every index file `open_failed`), candle **DEVIATION** D-115 (`float_noise`, documented) | smoke: 0 except D-113 (Bybit 434 index files, Hyperliquid 216) | Smoke 2026-09-29 12:59-19:30Z (a hard-linked copy of the restarted soak's catalog and an sqlite `backup` of each candle store taken together at 19:30:08Z; tool at 9626ad42c7 + the Story 31.7 change set). **Structure:** every type one schema class; bad_name, overlap, name_span, unsorted, empty, null_ts, open_count, unknown_type, duplicate_ts_event 0 everywhere; open_failed 0 except `index_price_update` (D-113). Bybit trade files 391 per instrument (BTCUSDT-LINEAR 755,047 rows, ETHUSDT-LINEAR 1,007,872, BTCUSDT-SPOT 357,187, ETHUSDT-SPOT 107,380), snapshots 391 (23,428 / 23,428 / 23,430 / 23,435); Hyperliquid SOL trades 391 files / 30,701, snapshots 391 / 23,436. **Rehearsal:** leaves_failed 0, days_refused 0; Bybit trades 1,564 -> 1,564 -> 4 files, 2,227,486 rows `identical`, snapshots 1,564 -> 4, 93,721 `identical`, index 434 -> 28 -> 2, mark 433 -> 27 -> 2, funding 317 -> 14 -> 2, open interest 88 -> 14 -> 2 `identical`, definitions `not_exercised`; Hyperliquid every data type `identical` (trades 391 -> 1, snapshots 391 -> 1, index 216 -> 11 -> 1, mark 216 -> 10 -> 1, OI 216 -> 12 -> 1, funding 9 -> 8 -> 1). **Parity** (stored = query = received, count and digest, `beyond_margin` 0): BTCUSDT-LINEAR trades 755,047 `4aa620fd…`, snapshots 23,428 `c99e0c92…`; ETHUSDT-LINEAR 1,007,872 `cda6acfd…`, 23,428 `63d7357b…`; BTCUSDT-SPOT 357,187 `2992d293…`, 23,430 `bcf0d6b6…`; ETHUSDT-SPOT 107,380 `bb56447f…`, 23,435 `2b96ce6f…`; SOL 30,701 `4ca12a31…`, 23,436 `92f19f4c…`; read_mismatch 0. **Candles** (all six widths): exact / float_noise, 0 different, undefined_mismatch, missing, extra, unknown_width: BTCUSDT-LINEAR 259 / 252, ETHUSDT-LINEAR 276 / 235, BTCUSDT-SPOT 271 / 237, ETHUSDT-SPOT 253 / 258, SOL 256 / 252 (D-115). Runtime 257 s (Bybit) / 38 s (Hyperliquid); peak RSS 1.56 GB / 0.71 GB | `python3 -m verification.catalog --venue V --day D` (`docs/DATA_DICTIONARY.md` §1.20) |
| Candles and klines, every timeframe | BTCUSDT-LINEAR | pending — tool built (Story 31.8); verdict pending Story 31.11; klines **OPEN** D-51 (D-124) | smoke: 0 inside the soak (the 46,765 unexplained seconds -- 00:00-12:59:18Z before the soak plus 12:59:19-24Z before capture's first row -- and the day's 60 missing raw files are the partial day, D-128; the tool counts them failing, so the smoke exits 1) | Smoke 2026-09-29, raw 12:59:19Z-20:42:04Z (then the host suspend, D-128); tool at `04ef9b081e` + the Story 31.8 change set; served bars from a data_api run from the same tree. Rows 27,751; seconds explained `catch_up_cap` 11,876 (the suspend), `stale` 8; `trades_unobserved` `stale` 69 (D-124), `unexplained` 1,023 (the recorder's first REST `recent-trade` page and WS trades before 12:59:25Z, all before capture's first row). per width traded/untraded/no_data: 1m 464/0/976, 5m 94/0/194, 15m 32/0/64, 1h 9/0/15, 4h 3/0/3, 1d 1/0/0, 10m 48/0/96, 30m 17/0/31, 45m 11/0/21. Every width: catalog fold 0 different/undefined_mismatch/missing/extra, unknown_width 0; served 0 served_differs/missing/extra, partial_ok on every traded bucket (float_noise 224 / 32 / 11 / 4 / 2 / 0 / 15 / 3 / 3, D-115); reference exact on every traded bucket. 1W (week of 2026-09-28) `week_open`. Old data_api (127.0.0.1:29100, pre-fix image): `partial_mismatch` 48 / 17 / 11 at 10m / 30m / 45m (flag absent, D-119), everything else identical. **Klines:** 1,440 compared, 459 matched (31.9 %), ours missing 976 (outside the soak), theirs missing 0, both-sided 5 (12:59, 20:42 soak edges; 18:44, 19:06, 19:07 stale-gate orphans, D-124); in the soak 459/464 (98.9 %) | `python3 -m verification.candles --venue V --day D` (`docs/DATA_DICTIONARY.md` §1.21); klines: `verified_days` + `archive.<step>` ledgers (§6) |
| | ETHUSDT-LINEAR | as above | smoke: 0 inside the soak (46,765 unexplained seconds and 60 missing raw files, D-128) | Smoke as above: rows 27,751; `catch_up_cap` 11,876, `stale` 8; `trades_unobserved` `stale` 100 (D-124), `unexplained` 1,024 (before capture's first row); buckets as BTCUSDT-LINEAR; every failing class 0 on every width (float_noise 233 / 32 / 12 / 0 / 0 / 1 / 14 / 8 / 3); `week_open`. Old data_api: `partial_mismatch` 48 / 17 / 11. **Klines:** 1,440, 459 matched (31.9 %), 976 / 0 / 5 (12:59, 20:42 edges; 18:44, 19:06, 19:07, D-124); in the soak 459/464 (98.9 %) | as above |
| | BTCUSDT-SPOT | pending — as above; smoke **OPEN** D-127 (D-91) | smoke: 55 buckets `ref_different` (D-127: 22 off-grid seconds of sub-tick prints, D-91), plus the partial day (46,765 seconds, D-128) | Smoke as above: rows 27,758; `catch_up_cap` 11,876, `stale` 1; `trades_unobserved` `catch_up_cap` 1, `unexplained` 60; buckets as BTCUSDT-LINEAR; catalog fold and served classes 0 failing on every width (float_noise 219 / 34 / 13 / 2 / 1 / 0 / 15 / 3 / 7); reference **ref_different** 15 / 10 / 6 / 4 / 2 / 1 / 7 / 5 / 5 (D-127), `ref_recorder_gap` 1 per width to 1h and at 10m-45m (17:28:35-37Z, inside the recorder's own 17:28:30-42Z reconnect), `ref_explained` 1 per width to 15m and at 10m/30m (18:00:31-32Z, `trades_unrecoverable:depth`, D-125); `week_open`. Old data_api: `partial_mismatch` 48 / 17 / 11. **Klines:** 1,440, 461 matched (32.0 %), 976 / 0 / 3 (12:59, 20:42 edges; 18:00, D-125); in the soak 461/464 (99.4 %) | as above |
| | ETHUSDT-SPOT | pending — as above | smoke: 0 inside the soak (46,765 seconds, D-128) | Smoke as above: rows 27,758; `catch_up_cap` 11,876, `stale` 1; `trades_unobserved` `stale` 1 (D-124), `unexplained` 64; every failing class 0 on every width (float_noise 208 / 38 / 8 / 3 / 2 / 0 / 18 / 8 / 5); `ref_explained` 1 per width (18:00:31-32Z, D-125); `week_open`. Old data_api: `partial_mismatch` 48 / 17 / 11. **Klines:** 1,440, 460 matched (31.9 %), 976 / 0 / 4 (12:59, 20:42 edges; 18:00, D-125; 18:44, D-124); in the soak 460/464 (99.1 %) | as above |
| | SOL-USD-PERP.HYPERLIQUID | pending — as above; klines **OPEN** D-126 | smoke: 0 inside the soak (46,762 unexplained seconds -- before the soak and 12:59:19-21Z before capture's first book -- and 15 missing raw files, D-128) | Smoke as above: rows 27,759; `catch_up_cap` 11,878, `no_book` 1; `trades_unobserved` `catch_up_cap` 2, `no_book` 1, `unexplained` 31 (the WS `trades` subscribe snapshot, before capture's first row); buckets 1m 463/1/976 (20:42Z observed two seconds without a trade), the rest as Bybit's; every failing class 0 on every width (float_noise 229 / 43 / 11 / 1 / 0 / 0 / 18 / 7 / 3); reference exact on every traded bucket; `week_open`. Old data_api: `partial_mismatch` 48 / 17 / 11. **Klines:** 1,440, 460 matched (31.9 %), 977 / 0 / 3 (12:59 edge; 13:10, 15:27 sweep extremes, D-126); in the soak 460/464 (99.1 %) | as above |
| Live, backtest and display parity (incl. the bot's signals) | all 5 | pending — tools built (Story 31.9); verdict pending Story 31.11; replay conversion **OPEN** D-135 (the catalog reader reorders one snapshot row's equal-`ts_init` deltas across a Parquet row-group boundary, so that one replay book misses a level: `replay_input` 1 per bot, bot_parity exits 1); the Bybit bot result is **not a parity proof** while D-133/D-134 are OPEN (the live inputs are wrong at the adapter, and the comparator can only classify, not correct, them); Bybit bot inputs **OPEN** D-133 (every live quote built from a depth-50 book message's first entries) and D-134 (spot: the depth-1 quote stream replayed into the book), both in the pinned adapter (FORK-01), which the comparator files as `quote_cadence`/`book_source`; `DummyStrategy` gating D-129 and D-113 (index decoder) operator decisions owed; D-132 (Sandbox stale trades, documented); fixed in code (undeployed): D-130 (`metrics.db` ts), D-131 (`/api/snapshots` mid) | bot_parity **FAIL, 5 unexplained** after the review patches (Bybit 4 bots, Hyperliquid 1: each exactly 1 `replay_input` `levels`, the same cycle 07:00:05.75Z, D-135 OPEN; every other count 0: truncated 0, live_only / replay_only 0, cold_start 0, gap 0, microprice-evidence failures 0) -- Bybit: classified, **not proven** (D-133/D-134); SSOT trace 0 DIFFERENT (fixture), 0 differed (live); OFI parity 0 mismatch | **Bot signals** (verify paper fleet 2026-09-30 06:43:49.754-07:50:17.754Z, 66.5 min, one dummy bot per instrument, 3,988 book cycles + 67 bars paired per bot, every cycle paired, `book_skipped` 0 both sides; exact-equal share / max abs diff; classes): BTCUSDT-LINEAR microprice 0.00 % / 49.93, ofi 0.00 % / 293.99, obi 0.35 % / 0.926, mlofi 0.02 % / 555.2, trend 13.79 % / 2.9e-11 (cycles `book_source` 3,821, `book_timing` 158, `quote_cadence` 76; trend `bar_source` 61, `carried_state` 3,435); ETHUSDT-LINEAR 0.00 % / 2.550, 0.00 % / 3,967.9, 0.44 % / 0.986, 0.02 % / 3,109.8, 100 % (cycles 3,816 / 157 / 82); BTCUSDT-SPOT 0.44 % / 59.71, 0.00 % / 23.97, 0.00 % / 0.789, 0.02 % / 18.60, 100 % (cycles `book_source` 3,988, `quote_cadence` 67); ETHUSDT-SPOT 1.58 % / 2.239, 0.00 % / 579.7, 0.00 % / 0.901, 0.02 % / 1,219.6, 100 % (3,988 / 67); SOL-USD-PERP.HYPERLIQUID 0.02 % / 0.1648, 0.00 % / 22,492, 56.30 % / 0.389, 28.01 % / 92,513, 39.36 % / 3.7e-9 (cycles `book_timing` 1,733, `quote_cadence` 2,312, `book_source` 10; trend `bar_source` 61, `carried_state` 2,398). **Replay input** (new, every replay timer cycle against the stored row active at T by `ts_init`): levels equal on 3,987 of 3,988 cycles per bot, the replay's microprice equal to its fed top's (`REL_TOL`) on 3,988 of 3,988, so every `quote_cadence` above now carries that evidence; the one mismatch per bot is D-135. Decision disagreements **0**, action disagreements 0 on every bot, but no entry fired on either side (every decision `none` 3,740 / `not_ready` 315: the trend stayed inside [0.4, 0.6]), so the decision rule's `long`/`short` branches are not yet exercised. Replay 16.5 s / 718 MiB peak RSS (all 5); comparator 5.9 s / 230 MiB (Bybit), 2.5 s / 211 MiB (Hyperliquid); re-run after the review patches (a fresh replay segment, same logs and catalog): replay 16.5 s / 724 MiB, comparator 7.9 s / 220 MiB (Bybit), 2.7 s / 212 MiB (Hyperliquid), every exact-equal share and class count above unchanged. **Display chain (SSOT trace)**: fixture 24 tests (20 run, 4 live skipped), no DIFFERENT field; `/api/snapshots` mid bit-equal to `kernel.indicators.mid_price` on all 600 fixture rows. Live (`VERIFY_STACK=1`, 24 passed, ~125 s): `rankings:live` stateless fields 182 matched the newest batch, 108 the batch before (in flight), 10 instruments not yet seen twice, **0 differed**; 15 traced rows reached the catalog integer-for-integer after 25-35 s; `/api/rankings` == bus; the newest `metrics.db` rows matched `rankings:live` around them for all 5 instruments (the old image, D-130). The verify data_api/ranking images were built 2026-09-29 12:59 (before 31.2/31.3): spreads unrounded, passing as float noise. **OFI backtest parity**: fixture 4 runs (BTC, SOL x z-window 300/20): 299 exact vs `ofi_readings`, 1 carried baseline, 0 mismatch; max rel diff vs the reference 1.3e-14..8.4e-14. Soak 2026-09-29 (rows 12:59:23-20:42:03Z only, D-128; catalog read-only): BTCUSDT-LINEAR 27,751 rows, 27,749 exact, 2 carried; BTCUSDT-SPOT 27,758 / 27,757 / 1; ETHUSDT-LINEAR 27,751 / 27,749 / 2; ETHUSDT-SPOT 27,758 / 27,757 / 1; SOL 27,759 / 27,758 / 1; 0 mismatch; max rel diff 2.5e-12 / 1.3e-12 / 1.7e-12 / 7.5e-12 / 5.3e-13; `ts_init` order == `ts_event` order everywhere; 126 s, 1.16 GB peak RSS (the linears' second carried baseline is a 7 s gap at 19:06:28Z) | `python3 -m bots.signal_replay ...` + `python3 -m verification.bot_parity --venue V --live-dir D --replay-dir D`; `VERIFY_STACK=1 ... test_ssot_trace.py`; `VERIFY_SOAK_CATALOG=... VERIFY_SOAK_DAY=... test_ofi_parity.py` (`docs/DATA_DICTIONARY.md` §1.22) |
| Fault injection: every loss accounted for | Bybit (BTCUSDT/ETHUSDT linear + spot), Hyperliquid (SOL) | pending — tool built and run (Story 31.10); verdict pending Story 31.11; every run scenario **PASS** on both venues; `network_cut` **not run** (needs sudo, DEPLOY_CHECKLIST 31-10); failed-flush loss **OPEN** D-137 (explained, but avoidable); fixed: D-61/D-75 restart backfill, D-136 status-bus retry | **0** unexplained trades and seconds in all 18 judged windows (9 per venue); 0 `archived_twice`/`duplicate_rows`/`row_and_reason`; every window matches its table row | Verify stack 2026-09-30, code `153629f4f6` + the Story 31.10 change set. Round 1 (collectors deployed 09:50:48Z, before the review fixes: only the restart backfill of ids whose first subscribe failed and D-136 missing): 11 scenarios 09:58-11:05Z. Round 2 (final code deployed 16:29:07Z, logged `deploy`): `redis_stop` 16:35:20Z, `sigkill_flush` Bybit 16:43:02Z / Hyperliquid 16:50:02Z, `redis_stop` 17:06:48Z. Per window (Bybit, 4 instruments summed; Hyperliquid SOL): **sigkill_flush** Bybit `restart` 89 s, backfilled 93, unrecoverable 0 (round 2: 268 s, 176, 67 BTCUSDT-SPOT `depth`); Hyperliquid `restart` 7 / 6 s, `no_book` 2, backfilled 0 (the WS `trades` subscribe snapshot re-delivers the gap: 259 of 259 reference trades archived); **graceful_restart** Bybit `restart` 28 s, backfilled 613, unrecoverable 174 (BTCUSDT-SPOT: past the spot REST depth, noted `depth`); Hyperliquid `restart` 5 s, `no_book` 1; **pause_15s** Bybit `stale` 60 s, backfilled 243, unrecoverable 26; Hyperliquid `stale` 15 s, backfilled 3; **pause_45s** Bybit `catch_up_cap` 60 + `stale` 120 s, backfilled 1,467, unrecoverable 530; Hyperliquid 15 + 30 s, backfilled 3; **catalog_readonly** Bybit `write_failed` 245 s, 1,542 trades ledgered lost (D-137); Hyperliquid 60 s, 25 trades; **redis_stop** (x3) no second reason, no trade touched, `collector.snapshot_publish` + `collector.control_redis` only. Non-vacuous: e.g. Bybit sigkill window BTCUSDT-LINEAR 5,181 reference trades seen, 5,181 archived. `network_cut` refused both venues (`sudo -n` not permitted), no rule inserted. D-136 re-test (`redis_stop` 17:06:48Z on the final code): both collectors' first `collector:status` publish after the restart, over the pooled connection the restart killed, arrived (Bybit 17:13:09Z, Hyperliquid 17:20:05Z, captured by a `SUBSCRIBE`); no `collector.status_loop` entry, the ledgered failures confined to the outage (`collector.snapshot_publish`/`collector.control_redis` 17:06:48-17:07:48Z). Evaluate cost (all 9 windows): 291 s / 184 MB peak RSS (Bybit; about 30 s per window, each rescanning its raw hours), 4.8 s / 147 MB (Hyperliquid) | `python3 -m verification.chaos --scenario S --venue V`; `python3 -m verification.chaos --evaluate --venue V` (`VERIFY_DATA_DIR`, `CATALOG_PATH`, `ERROR_LEDGER_DIR`; `docs/DATA_DICTIONARY.md` §1.23) |

**Trades smoke (Story 31.4), how it was run.** The soak's first closed day is 2026-09-30, so the
smoke ran the tool's per-hour pieces (`verification.application.trades.check_hours`, the function
`check_day` runs over all 24 hours) on today's two complete hours, 13:00-15:00Z, read-only against
the running stack, `--stage live`. Hour 12 (the soak began 12:59:19Z) and hour 15 (still being
written) were excluded: their edges are boundary artefacts, not findings. The Bybit day would
also fail on its coverage record, which does not exist yet (D-92); the smoke reports it as
MISSING. `main` over the whole day refuses it, as it must (the hour 15 raw file is still open).

**Book smoke (Story 31.5), how it was run.** `verification.book.main([--venue V --day
2026-09-29 --raw-dir <copy> --catalog data/catalog], clock=<2026-10-01T00:00Z>)`, read-only against
the running stack, with the raw root a directory of symlinks to the recorder's closed hour files
12..15 only (the hour-16 file was still being written and would be refused as truncated). The day
is partial, so its verdict fails on the inputs by design: 80 (Bybit) / 40 (Hyperliquid) raw book
files of hours 00-11 and 16-23 missing, and Bybit's coverage record absent (D-92); the rows after
16:00Z are `reference_unavailable` (no raw to close them). `collector.book_sequence` ledger
entries in `data/errors/bybit_collector.jsonl` over the window: 0. Runtime: 95 s (Bybit, four
instruments), 4 s (Hyperliquid).

**Derivs smoke (Story 31.6), how it was run.** `verification.derivs.main([--venue V --day
2026-09-29 --raw-dir <copy> --catalog data/catalog], clock=<2026-10-01T00:00Z>)` with
`BYBIT_COLLECTOR_CONFIG`/`HYPERLIQUID_COLLECTOR_CONFIG` the committed `config.toml`s
(`open_interest_poll_seconds` 300) and `ERROR_LEDGER_DIR` a scratch directory, read-only against
the running stack at ~18:05Z and, after the Story 31.6 review patches, again at 18:30Z (the numbers above are the rerun's), with the raw root a directory of symlinks to the recorder's closed
hour files 12..16 only (the hour-17 file was still being written). The day is partial, so its
verdict fails on the inputs by design: 76 (Bybit) / 38 (Hyperliquid) raw files of hours 00-11 and
17-23 missing, and Bybit's open interest has two `poll_gaps` at the day's edges (before the soak's
first poll, D-76, and after the run). Rows after 16:59:55Z are `reference_unavailable` (the unread hour 17, widened 5 s) unless a
recorded frame inside that margin judges them; so is a collector startup row inside the
recorder's own startup gap that no recorded frame matches. The only ledger lines at `verification.derivs.refused` are from the first development run,
which refused Hyperliquid's funding file on `"-9.368E-7"` -- the verifier's reader, since fixed
(D-108). Every other failing count was 0. Runtime: 6.6 s (Bybit, four instruments), 1.5 s
(Hyperliquid).
After the follow-up review's patches (D-111) the smoke was rerun at 18:45Z over the closed hours 12..17: every Bybit value failing count 0 (BTCUSDT mark exact 15,016, index 36,021, funding 147; ETHUSDT 14,706, 35,102, 107; OI agree_state 59 each, the same two partial-day `poll_gaps`), definitions agree 601 each, spot 0 fabricated; Hyperliquid SOL mark exact 4,424, funding 1,411, OI 5,787, failing 0, and index exact 5,054 with **not_stored 1** -- the match-bound edge D-112 (OPEN), not a capture loss. Runtime: 8.1 s (Bybit), 2.1 s (Hyperliquid).

**Catalog smoke (Story 31.7), how it was run.** The soak's first closed day is 2026-09-30 and
the verify stack was still writing 2026-09-29, so at 19:30:08Z (8 s after a minute's flush, when
capture's Parquet files and its candle store had both taken the same seconds) every file of the
plan's five instruments under every type directory was hard-linked into a scratch catalog (5,844
files in 0.25 s) and each `candles_<venue>.db` was copied with sqlite's online `backup`; then
`verification.catalog.main([--venue V --day 2026-09-29 --catalog <copy> --candles <copy>
--scratch-dir <scratch>], clock=<2026-10-01T00:00Z>)` with the committed `config.toml`s and
`ERROR_LEDGER_DIR` unset. The live catalog and store were never opened for writing. The day is
partial (12:59:19Z-19:30Z): the copy and the store backup end at the same flush, so the store holds
no bar the copy lacks -- every candle bucket of every width compared, none `missing` or `extra`;
the 19:00Z hour and the 16:00Z/1d buckets are partial on both sides alike (`seconds_observed`
equal). The rehearsal's nightly stage ran as of the clock (D closed); its intraday stage as of
D's last instant, so it merged the small types' hours the live `archive` service had not merged
yet. The first development run (a 12:59-15:00Z prototype copy) read every mark file
`open_failed` through the `files=` PyArrow path -- a verifier edge, fixed (D-114); the one
remaining failing class is D-113.


**Candles smoke (Story 31.8), how it was run.** From 05:21Z on 2026-09-30 (2026-09-29
closed and the nightly's rebuild, consolidation, candle rebuild and reconcile done at 04:39-04:41Z),
from `platform/`, read-only against the live verify data -- nothing copied, nothing written:
`CATALOG_PATH=data/catalog VERIFY_DATA_DIR=data/verification CANDLES_DIR=data/candles
BYBIT_COLLECTOR_CONFIG=capture/venues/bybit/config.toml
HYPERLIQUID_COLLECTOR_CONFIG=capture/venues/hyperliquid/config.toml ERROR_LEDGER_DIR=<scratch>
/usr/bin/time -v python3 -m verification.candles --venue V --day 2026-09-29 --json`, twice per
venue: (a) against the verify stack's own data_api (`127.0.0.1:29100`, the default), whose image
predates this story, and (b) with `--data-api http://127.0.0.1:29199`, a data_api started from the
Story 31.8 working tree (`python3 -m uvicorn data_api.app:app --host 127.0.0.1 --port 29199` with
`CATALOG_PATH=data/catalog CANDLES_DB_DIR=data/candles`, the store opened `mode=ro`,
`ERROR_LEDGER_DIR`/`METRICS_DB_PATH`/`ALERTS_PATH` in a scratch directory and `REDIS_URL` pointing at
no server -- the candle route needs no Redis; the buses only logged reconnects). The verify
collectors, recorders and data_api were never stopped or restarted. The numbers above are run (b);
(a) differs only by `partial_mismatch` 48 / 17 / 11 per instrument at 10m / 30m / 45m (D-119). The
day is partial (soak from 12:59:19Z; host suspended ~20:42:04Z-04:39Z, D-128), so every run exits 1
on the partial day's unexplained seconds and missing raw files by design; inside the soak every
failing count is 0 except BTCUSDT-SPOT's sub-tick prints (D-127 / D-91). The week of 2026-09-28 is
open, so every 1W check is `week_open` (the truncated-week fix, D-118, is pinned by tests only so
far). Runtime and peak RSS (run b): Bybit 300 s / 519 MiB, Hyperliquid 5.0 s / 194 MiB (run a:
297 s / 518 MiB and 4.9 s / 194 MiB); Bybit's time is the raw decode (a served-free rerun of the
per-instrument reference took 291 s).

**Kline pass rates (D-51), 2026-09-29.** From the verify stack's nightly of 2026-09-30 04:40Z
(`archive.compare_klines --rebuilt-by`, run ids `139d6c022a9342f6b228dd4143d32a18` Bybit and
`44579e9811b54ef8b245bbd74d8c813e` Hyperliquid): `verified_days` (`status`, `mismatches`) and the
4,901 `reconcile.kline_mismatch` lines of `docker logs verify-archive` (the steps ledgered to stdout
only, D-120). Soak window 12:59:19Z-20:42:04Z; minutes compared = the union of traded minutes.

| Instrument | Compared | Matched | Pass rate (day) | Ours missing | Theirs missing | Both-sided | In the soak (12:59-20:42) | Both-sided minutes and cause |
|---|---|---|---|---|---|---|---|---|
| BTCUSDT-LINEAR | 1,440 | 459 | 31.9 % | 976 | 0 | 5 | 459/464 (98.9 %) | 12:59, 20:42 soak edges; 18:44, 19:06, 19:07 stale-gate orphans (D-124) |
| ETHUSDT-LINEAR | 1,440 | 459 | 31.9 % | 976 | 0 | 5 | 459/464 (98.9 %) | as BTCUSDT-LINEAR |
| BTCUSDT-SPOT | 1,440 | 461 | 32.0 % | 976 | 0 | 3 | 461/464 (99.4 %) | 12:59, 20:42 edges; 18:00 spot socket closed by the venue, REST depth (D-125) |
| ETHUSDT-SPOT | 1,440 | 460 | 31.9 % | 976 | 0 | 4 | 460/464 (99.1 %) | 12:59, 20:42 edges; 18:00 (D-125); 18:44 (D-124) |
| SOL-USD-PERP.HYPERLIQUID | 1,440 | 460 | 31.9 % | 977 | 0 | 3 | 460/464 (99.1 %) | 12:59 edge; 13:10, 15:27 sweep extremes (D-126) |
| **BYBIT** | 5,760 | 1,839 | 31.9 %, instruments pass 0/4 | 3,904 | 0 | 17 | 1,839/1,856 (99.1 %) | |
| **HYPERLIQUID** | 1,440 | 460 | 31.9 %, 0/1 | 977 | 0 | 3 | 460/464 (99.1 %) | |

Every ours-missing minute is outside the soak (00:00-12:58 and 20:43-23:59; Hyperliquid's 977th is
20:42, whose two observed seconds held no trade). Each both-sided minute was checked against the
recorder's merged trades of the minute (the tool's own `channel_trades`/`merge_reference`), our
stored 1m bar, the catalog seconds, the coverage record and the venue kline re-fetched on
2026-09-30 (Bybit `/v5/market/kline` interval 1, Hyperliquid `candleSnapshot` 1m): in every Bybit
minute the venue kline equals the recorder's fold of the whole minute exactly, and our bar differs
from it by exactly the trades of the seconds named (the stale-rejected seconds' trades, which have
no row, D-124; the 18:00:31-32Z trades the spot reconnect could not backfill, D-125; the seconds
before capture's first row or after the suspend at the edges). In the two Hyperliquid minutes our
bar equals the fold of the venue's trades while the venue's kline leaves out the last level of a
multi-level sweep (D-126, OPEN). The scratch scripts and their output are not kept; the commands are
the tool's own functions (`verification.application.candles._reference`, `_causes`,
`verification.domain.trade_check.merge_reference`) over the same inputs.

**Parity smoke (Story 31.9), how it was run.** The paper fleet (`bots/config.verify.toml`, one
`dummy` bot per verify instrument, signal logs on) was built and started with `--no-deps` next to
the running verify stack (collectors and recorders untouched); every bot's latest `start` is
2026-09-30T06:43:49.754Z. After 66.5 min it was stopped (`docker stop verify-live-paper` at
07:50:27Z; it stays stopped: it is in `VERIFY_SERVICES`, so the next `make verify-up` restarts it),
and after 3 minutes (the collectors' 60 s flush past the window) from `platform/`:
`/usr/bin/time -v python3 -m bots.signal_replay --config bots/config.verify.toml --catalog
data/catalog --live-log data/verification/bot_signals/live --out
data/verification/bot_signals/replay` (the catalog only read) and `python3 -m
verification.bot_parity --venue BYBIT|HYPERLIQUID --catalog data/catalog --live-dir
data/verification/bot_signals/live --replay-dir data/verification/bot_signals/replay [--json]`
(both exited 0, 0 unexplained, before the review patches). A preliminary run over the first 28 min
gave the same classes. **Re-run after the Story 31.9 review patches** (same live logs, catalog
still only read; the replay appended a fresh segment to `data/verification/bot_signals/replay`,
checked to start after the file's previous end and to reach the window end, and the comparator read
it): both venues exit **1**, one `replay_input` `levels` per bot and nothing else failing. Every
bot's mismatch is the same cycle (07:00:05.754Z, the row of 07:00:04Z sampled at 07:00:05.501Z):
the replay's book lacks the row's fifth ask level (BTCUSDT-LINEAR 82949.7, SOL 118.09, ...). Root
cause, proven on the derived catalog (D-135): the throwaway file holds the row's 41 deltas in
order (`CLEAR` first), but they straddle a Parquet row-group boundary (5,000 rows; the row starts at
delta 39,975 of each bot's identical-geometry file) and `ParquetDataCatalog.query`'s `ORDER BY
ts_init` returns equal-`ts_init` rows unordered across it: the fifth ask's `ADD` comes back before
the `CLEAR`, which then wipes it. The comparator's new replay-input check is what caught it; before
the patches this cycle paired as an ordinary fed-levels difference. **Bybit
verdict:** the four Bybit bots' classes are a classification, **not a parity proof**: while D-133
and D-134 are OPEN the live side's quotes and (on spot) book are wrong at the pinned adapter, so
"0 other unexplained" there means only that every difference has a named class, not that live and
backtest agree on correct inputs. **What
the classes mean here:** on Hyperliquid the live book equals a stored row within `[T-5 s, T+1 s]`
on 99.8 % of cycles (`book_timing`, or equal outright: the replay sees the same book about 1 + `hold_back` s later)
and quotes (`bbo`) move between rows (`quote_cadence`). On Bybit linear the live book at T is the
venue's own book at T -- checked against the reference recorder's rebuilt `orderbook.50` book
(a scratch script over `verification.domain.reference_book`, outside the tool): 99.6 % (BTCUSDT) /
99.0 % (ETHUSDT) of cycles at the recorder's last receipt at or before T, 99.9 % within 100 ms --
while a row holds the book at the exchange second's end, so `book_source` is the sampling instant
(the live timer's phase, .754 s), not a different source. On Bybit spot it is not: the live book
is the venue's on only 11.2 % / 20.9 % of cycles and has 10 levels a side on 1.7 % / 1.6 % (D-134),
and on every Bybit bot the live quotes are the adapter's first-entry quotes (D-133: the live
microprice sits a median 46.1 / 17.0 / 69.1 / 17.8 ticks from its own book's mid, Hyperliquid 0.5;
a simulation of the adapter's rule over the recorder's frames reproduces it on 99.3 % / 98.5 % of
linear cycles). So the Bybit rows above measure a live-side adapter defect as much as live/backtest
parity, and the comparator cannot tell: D-133's follow-up adds a live-input-vs-venue check. The
`bar_source` cycles are one bar each on BTCUSDT-LINEAR (06:53Z) and SOL (07:10Z), both closes
decided by which process received a boundary trade first (bar membership is by arrival on both
sides): the collector received the BTCUSDT trade at 83020.1 (`ts_event` 06:52:59.896Z) at
59.998 s, before the bar closed, the live node after it (live close 83020.2, replay 83020.1); the
SOL trade at 117.97 (`ts_event` 07:09:59.632Z) reached the collector at 07:10:00.087Z, after the
close, and the live node before it (live 117.97, replay 117.96). The SGD trend then carries a
< 4e-9 difference (`carried_state`). The Sandbox's `Skipping stale trade` stream during the run is D-132. The SSOT
live variant (`VERIFY_STACK=1 CATALOG_PATH=data/catalog python3 -m pytest -o addopts=""
--rootdir=. verification/tests/test_ssot_trace.py`) ran against the same stack; the OFI soak
variant with `VERIFY_SOAK_CATALOG=data/catalog VERIFY_SOAK_DAY=2026-09-29`, read-only.

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
