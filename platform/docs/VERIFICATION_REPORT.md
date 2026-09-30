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
| Fault injection: every loss accounted for | Bybit, Hyperliquid | pending — Story 31.10 | | | |

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
