# Review — versions / reality-check lens

**Target:** `ARCHITECTURE-SPINE.md` (platform/ DDD spine, 2026-09-21)
**Lens:** every committed decision reality-checked against the repo (brownfield: the spine claims it binds no new technology and that every Stack row is an existing pin), every concrete code claim checked against the real code, stdlib assumptions checked by experiment.
**Method:** all file:line evidence below is from the working tree at `ed15aea026` (branch `troll`). The namespace experiment ran under `/tmp/claude-1000/…/scratchpad/ns` on CPython 3.13.13. No web lookups were needed — every claim was settleable from the repo or the interpreter.

**Verdict:** the Stack table and nearly all named identifiers are real and correctly pinned; the spine's reality checks are sound except that it did not re-read the tree after story 22.12 merged mid-run (its own `.memlog.md` records the merge but the body still says "not merged"), it generalises one env-var name that does not exist for dYdX, it "resolves" a parent-Deferred item the code already closed, and two line-range citations mis-attribute capture code to `collection_control`.

---

## 1. Stack table — row by row

| Row | Spine says | Evidence | Verdict |
| --- | --- | --- | --- |
| Python | 3.12–3.14 | `pyproject.toml:25` `requires-python = ">=3.12,<3.15"`, classifiers `:11-13`. Deployed interpreter is `python:3.13-slim` pinned by digest (`.docker/nautilus_trader.dockerfile:3-4`, site-packages path `:78,83` is `python3.13`) | confirmed (supported range); the *deployed* version (3.13, digest-pinned) is not stated — see M-3 |
| nautilus_trader / base image | 1.229.0 | `pyproject.toml:3`; `troll/collector.dockerfile:3`, `troll/data_api.dockerfile:17`, `troll/live_paper.dockerfile:3` all `FROM nautilus-trader-base:1.229.0` | confirmed |
| fastapi | 0.141.1 | `troll/troll-requirements.txt:19` | confirmed |
| uvicorn | 0.52.4 | `troll-requirements.txt:20` `uvicorn[standard]==0.52.4` | confirmed (extra `[standard]` omitted from the row) |
| httpx | 0.28.1 | `troll-requirements.txt:24` | confirmed |
| redis (client) | >=8.0.1 | `troll-requirements.txt:4` | confirmed — a floor, not a pin (M-3) |
| redis (broker image) | redis:8-alpine | `troll/docker-compose.yml:20` | confirmed; paired-version comment at `troll-requirements.txt:3` |
| aiohttp | >=3.14.1 | `troll-requirements.txt:5` | confirmed — floor |
| urwid | 4.0.6 | `troll-requirements.txt:13` | confirmed |
| pandas | 3.0.4 | `troll-requirements.txt:2` | confirmed |
| plotly | 6.8.0 | `troll-requirements.txt:1` | confirmed |
| tomli_w | >=1.0.0 | `troll-requirements.txt:9` | confirmed — floor |
| pytest / pytest-asyncio | >=7.4.4,<8.0.0 / 0.23.8 | `troll-requirements.txt:16-17`; identical to `pyproject.toml:105,107` | confirmed |
| React / react-dom | 19.3.0 | `troll/frontend/package.json:18-19` | confirmed |
| react-router | 8.3.1 | `package.json:20` | confirmed |
| @tanstack/react-query | 5.102.8 | `package.json:16` | confirmed |
| lightweight-charts | 5.2.1 | `package.json:17` | confirmed |
| Vite / TypeScript | 8.3.0 / ~6.0.2 | `package.json:32,31` | confirmed |
| Node (build stage) | node:24-slim | `troll/data_api.dockerfile:7`; `package-lock.json` present (`npm ci` at `:10`) | confirmed |
| "binds no new technology" | — | Structural Seed and AD-D2/AD-D5 rely only on stdlib `typing.Protocol`, `ast`, `tracemalloc`, `importlib.util` (checked: all present on 3.13.13, incl. `typing.runtime_checkable`, `tracemalloc.start/take_snapshot/get_traced_memory`) | confirmed |
| rust-toolchain | not cited | `rust-toolchain.toml` = 1.96.0 stable; spine has no Rust row and needs none | n/a |

## 2. Deployment claims

| Claim | Evidence | Verdict |
| --- | --- | --- |
| nine compose services | `docker-compose.yml`: redis `:17`, collector `:29`, bybit_collector `:63`, hyperliquid_collector `:87`, ranking_engine `:110`, data_api `:143`, bot_tui `:179`, live-paper `:227`, dozzle `:287` | confirmed |
| host network / 127.0.0.1 | `network_mode: host` `:59,83,106,139,175,224,279`; uvicorn `--host 127.0.0.1` `:149` | confirmed |
| `data_api`/`ranking_engine` read-only mounts | `:137` and `:163-165` `:ro` | confirmed |
| `live-paper` and `bot_tui` profile-gated | `profiles: ["tui"]` `:189`, `["live-paper"]` `:232` | confirmed |
| three thin images over one base; `data_api` also runs a Node stage | dockerfiles above; `data_api.dockerfile:7-12` | confirmed |
| `make test` list is the one AD-D12 says each move must update | `troll/Makefile:143-145` (`live_paper/tests` is a separate `test-live-paper` target `:224-226`) | confirmed; note `test-live-paper` is a second list to update |
| `make consolidate` / `backup-catalog` (22.11) | `Makefile:155-157`, `:188-193` | confirmed |
| Parent Deferred "`data_api` image lacks `collector_core`/`common`" still open | `data_api.dockerfile:27-30` copies neither; imports at `data_api/app.py:50`, `live_candles.py:43`, `routes/snapshots.py:44-45`, `routes/indicators.py:50`, `routes/indicator_series.py:47`, `routes/candles.py:35` | confirmed open; "moot once `kernel/` is copied" holds because both imported symbols (`DydxSecondSnapshot`, `market_kind`) are kernel members under AD-D3 |

## 3. Code-fact claims

| Claim | Evidence | Verdict |
| --- | --- | --- |
| `class_to_filename` derives the catalog dir from `cls.__name__`, prefixed for non-Nautilus classes | `nautilus_trader/persistence/funcs.py:39-51` (`convert_to_snake_case(... cls.__name__)`, `CUSTOM_DATA_PREFIX` when `not is_nautilus_class(cls)`) | confirmed |
| catalog holds `custom_dydx_second_snapshot` | `ls troll/dydx_collector/catalog/data/` → `custom_dydx_second_snapshot` (also `custom_dydx_minute_rollup`, `custom_dydx_open_interest`, `crypto_perpetual`, `funding_rate_update`, `index_price_update`, `instrument_status`, `mark_price_update`, `order_book_deltas`) | confirmed; note the same `__name__` pin applies to `OpenInterest` (→ `custom_open_interest`) and the legacy `custom_dydx_open_interest` dir still exists locally — M-4 |
| `DydxSecondSnapshot`, `OpenInterest` class names | `collector_core/second_snapshot.py:54`, `collector_core/open_interest.py:34` | confirmed |
| a dir without `__init__.py` does not shadow stdlib `platform`; `find_spec("platform").origin` is the stdlib file | experiment (cwd on `sys.path[0]`, `platform/kernel/mod.py` present, no `__init__.py`): `find_spec('platform').origin = …/lib/python3.13/platform.py`, `platform.system` present. With `platform/__init__.py` added: origin becomes the local file, `platform.system` gone. Also with `PYTHONSAFEPATH=1`: stdlib | confirmed — with a corollary the spine does not state: `import platform.kernel.mod` fails (`ModuleNotFoundError: 'platform' is not a package`), i.e. nothing under `platform/` is ever importable with a `platform.` prefix — M-2 |
| `typing.Protocol` ports, `ast`-walking boundary test, `tracemalloc` hot-path test are stdlib-only | interpreter check above | confirmed |
| Redis channels `snapshots:raw`, `rankings:live`, `ranking:control`, `bots:status`, `bots:control`, `bots:history:*`, `collector:status`, `collector:control` | `ranking_engine/engine.py`, `data_api/live_candles.py`, `data_api/alerts.py` (snapshots:raw); `ranking_engine/engine.py`, `ml_signals/ranking_columns.py`, `data_api/redis_bus.py`, `data_api/ws/live.py` (rankings:live); `ranking_engine/engine.py`, `bot_tui/ranking_state.py` (ranking:control); `live_paper/{bot_status,config,trade_history,node}.py` (bots:status/control/history); `dydx_collector/collector.py`, `bot_tui/{collector_state,collector_pane,app}.py` (collector:status/control) | confirmed, all eight |
| stores `candles_<venue>.db`, `metrics.db`, `fills.db`, `alerts.toml`, `verified_days` | `docker-compose.yml:38,74,98` (`candles_{dydx,bybit,hyperliquid}.db`); `metrics.db` in compose, `ranking_engine/engine.py`, `ml_signals/rank_history.py`, `data_api/settings.py`; `fills.db` in `live_paper/{fills_store,trade_history,node}.py`; `alerts.toml` in `data_api/alerts.py`; `verified_days` in `ml_signals/candle_store.py`, `collector_core/{prune_catalog,compare_klines}.py` | confirmed |
| env vars `CATALOG_PATH`, `CANDLES_DB_PATH`, `REDIS_URL` | `ranking_engine/engine.py:64-65`, `data_api/settings.py`, `data_api/live_candles.py`; `collector_core/collector.py` (`CANDLES_DB_PATH`); `live_paper/node.py`, `dydx_collector/collector.py`, `data_api/redis_bus.py` (`REDIS_URL`) | confirmed |
| env var `<VENUE>_COLLECTOR_CONFIG` | only `BYBIT_COLLECTOR_CONFIG` (`bybit_collector/collector.py:43`, compose `:72`) and `HYPERLIQUID_COLLECTOR_CONFIG` (`hyperliquid_collector/collector.py:37`, compose `:96`). No `DYDX_COLLECTOR_CONFIG` anywhere; dYdX config is a **bind mount** `./config.toml:/app/dydx_collector/config.toml:rw` (compose `:50`) resolved relative to the package file | **wrong as a generic pattern** — H-2 |
| client contract `fetch_instruments`, `connect`, `disconnect`, `subscribe`, `unsubscribe`, optional `subscribe_global`, `fetch_book_levels`, `resync_orderbook`, `feed_states` | contract docstring `collector_core/collector.py:46-60`; `hasattr` gates `:734,868,1727,1749,1753`; implementations `dydx_collector/client.py:96-141` (no `feed_states`/`fetch_book_levels`), `bybit_collector/client.py:128-219` (all), `hyperliquid_collector/client.py:130-185` (no `resync_orderbook`) | confirmed |
| `resync_orderbook` exposed only by venues whose book can drift | dYdX and Bybit expose it; Hyperliquid (full-snapshot) does not; core `:706` documents the full-snapshot case | confirmed |
| `error_ledger.record(site, detail, exc)` | `ml_signals/error_ledger.py:23` `record(site, detail="", exc=None)` | confirmed |
| `GET /api/errors`, frontend `ErrorBar` | `data_api/app.py:155`; `frontend/src/components/ErrorBar.tsx`, used in `App.tsx` | confirmed |
| `Collector._notify` / `_watchdog_transition` | module-level functions `collector_core/collector.py:388`, `:353`, not methods | confirmed (naming nit, L-5) |
| `core_config_from_dict` rejects unknown keys pattern | `collector_core/config.py:84` | confirmed |
| `Step`/`StepResult` saga | `collector_core/nightly.py:60,68`, `run_steps` `:160` | confirmed |
| `fold_trades` + `SecondTradeFields` (integer `raw` fold) | `collector_core/fold.py:89`, `:52` | confirmed |
| `fold_arrays` is candles' own fold | `ml_signals/candle_store.py:138` | confirmed |
| `mark_verified`, `seconds_observed`/`partial` | `candle_store.py:341`; `:10,40,59` | confirmed |
| `trade_backfill.exact_text` | `collector_core/trade_backfill.py:130` (Decimal quantize, no float) | confirmed |
| `_at_fixed_precision` dYdX-only | `dydx_collector/client.py:50` | confirmed |
| Bybit backfill depth 1000 linear / 60 spot; Hyperliquid last 10; dYdX recoverable (paged `limit=1000`) | `trade_backfill.py:27-47`, `_DYDX_PAGE`/`_BYBIT_LIMIT` `:92,94`; `hyperliquid_collector/client.py:38` | confirmed |
| WS id == REST id on every venue, wire-verified 2026-09-21 | `trade_backfill.py:40-41` ("Bybit linear BTCUSDT 999/999 and spot ETHUSDT 60/60, Hyperliquid 19/19, dYdX 3/3 -- same ids") | confirmed |
| `trade_feeds = 2` dual sockets | `hyperliquid_collector/{collector.py:46,client.py:36,100}`; `TRADES_FEED = Feed("main-trades", "main", trades_only=True)` `client.py:70` | confirmed |
| `Feed(name, group, trades_only)`, `REST_FEED_NAME`, trades-only feeds never count toward book staleness | `collector_core/feed.py:33-42`, docstring `:21` | confirmed |
| `seen_trade_ids` bounded window (DATA-06) | `collector_core/config.py:41` (window size 2000) | confirmed |
| 22.12: `book_time_source`, `hold_back_seconds`, `measure_lag.py` | `collector_core/config.py:53,57,110`; `collector_core/collector.py:32,594`; `collector_core/measure_lag.py` | confirmed present — but the spine tags them `[ASSUMPTION … not merged]` — H-1 |
| 22.12 merged? | `git log`: `70847351c8 2026-09-21 Merge bmad-loop/…/22-12-… into troll`; `ed15aea026` 22-12 operator actions. `.memlog.md` last-but-one entry records the merge | **spine body is stale** — H-1 |
| 22.14 story header `Status: ready-for-dev` vs `sprint-status.yaml` `awaiting-operator` | `implementation-artifacts/22-14-….md:3`; `sprint-status.yaml:293` | confirmed — and the same mismatch exists for 22.12 (`22-12-….md:3` vs `sprint-status.yaml:294`), which the spine does not mention — L-2 |
| Parent Deferred "dYdX OI poll stamps WS feed liveness" | `dydx_collector/collector.py:356-366`: `_open_interest_loop` now appends to `self._buffer` directly, comment "Straight to the buffer, not _on_data … (story 22.14)" | **already closed in code**; the spine lists it as an open item it resolves — H-3 |
| Parent Deferred "empty top-of-book skip is silent" | `collector_core/collector.py:1209-1210` `if book.best_bid_price() is None or …: return None` — no log, no ledger | confirmed still open |
| Parent Deferred "writer→reader imports", private `_stamp_to_ns` | `collector_core/collector.py:139-142` imports `ml_signals.candle_store`, `error_ledger`, `catalog_stats._stamp_to_ns`, `query_second_ohlc`; `_stamp_to_ns` also imported by `build_candles.py:42`, `consolidate_catalog.py:72`, `prune_catalog.py:51`, `compare_klines.py:91`, `rebuild_seconds.py:80` (six files, parent recorded two) | confirmed open, wider than the parent says — L-3 |
| Parent Deferred "reader-side crossed-book skip", `_SNAPSHOT_GAP_THRESHOLD_MS` | `data_api/routes/snapshots.py:75` (`= 2500`), `:129-133` (empty-top `continue`, `if bp >= ap: continue`) | confirmed, exact lines |
| Parent Deferred items "buffer durability, gate-version skew, rejection-rate observability, ranking history retention, `bots:history` TTL, Beta pin" exist under those names | parent spine `:297,298,299,301,302,305` | confirmed |
| Parent Consistency Conventions include a Redis channel row and "paired versions" | parent `:177,185,188` | confirmed |
| `ranking_engine/engine.py`'s "twenty module globals" | 39 module-level assignments; 15 hold mutable runtime state (`_LAST_SEEN:117`, `_VOLATILITY:120`, `_OFI_INDS:126`, `_OFI_RAW_INDS:127`, `_OBI_INDS:128`, `_LAST_FED:136`, `_SECOND_ROLLING:145`, `_SLOW_METRICS:160`, `_PRICE_SERIES:168`, `_BACKFILLED:174`, `_ACTIVE_MODE:178`, `_VOLUME_24H:190`, `_VENUE_VOLUMES:196`, `_PUBLISHER:1005`, `_PUBLISH_LOCK:1013`) plus one `global` statement | approximately right (L-4) |
| `parse_bybit_volume_24h` / `parse_hyperliquid_volume_24h` pure parsers | `ranking_engine/engine.py:329,389` | confirmed |
| `Bot.id == order_id_tag`, `VENUES` table | `live_paper/node.py:190` `order_id_tag=bot.bot_id`; `VENUES` is at `live_paper/venues.py:90`, not in a `nautilus_host.py` (target name) | confirmed (today-location nit, L-6) |
| `venue_of`, `MalformedInstrumentId` (ml_signals) and `venue_kind`, `market_kind` (common) to merge | `ml_signals/venue.py:9,5`; `common/venues.py:11,19` | confirmed |
| `classify_liquidity` | `dydx_collector/open_interest.py:40` | confirmed (`LiquidityTier` is a target name, does not exist today) |
| AD-D1 "Today" module list (all ~45 files across `collector_core`, `dydx_collector`, `bybit_collector`, `hyperliquid_collector`, `ml_signals`, `data_api`, `common`, `ranking_engine`, `live_paper`, `bot_tui`) | existence check: none missing | confirmed |
| `dydx_collector/collector.py:194-625` = "control plane" | `:194` is `class DydxCollector`; but `:268-355` are `_clear_book_state`, `_apply_deltas`, `_uncross_step`, `_handle_crossed_book`, `_resync_book` (capture hooks) and `:356` `_open_interest_loop`; control-plane methods start at `:369` (`_subscribe`) through `:595` `_prune_loop` (ends ~`:631`) | **partially wrong** — M-1 |
| `dydx_collector/collector.py:677-843` = incident handler | incident block is `:648-870` (constants from `:648`, `_classify_incident:683` … `_IncidentHandler:806-848`, `_prune_stale_ws_raw_logs:849`) | approximately right (start/end off) — M-1 |
| `epics.md` Epic 22 at lines 2209-2530 | `:2209` is the Epic 22 heading; the file has 2517 lines, no later `##` heading | end anchor wrong (L-1) |
| sources/companions exist | `ddd-redesign-seed-2026-09-21.md`, sibling frontend spine, `troll/{ARCHITECTURE,CLAUDE}.md`, `docs/{DATA_DICTIONARY,DATA_INTEGRITY_AUDIT,DEPLOY_CHECKLIST}.md` | confirmed |
| DESIGN-01 is the YAGNI rule in tension with AD-D4 | `troll/CLAUDE.md:69` | confirmed |
| tooling references to `troll/` that the rename must touch | `pyproject.toml:417` `known-first-party = ["nautilus_trader"]` only; no `troll` in `pyproject.toml`/`.pre-commit-config.yaml` | confirmed nothing extra to update there |

---

## 4. Findings

### Critical

None. Nothing in the spine binds a technology that does not exist, is unpinned, or is unverifiable; the stdlib mechanics it leans on behave as assumed.

### High

**H-1 — Story 22.12 is presented as unmerged; it merged during the run and the code confirms the model.**
Capability map row "22.12 … `[ASSUMPTION: modelled from the story file; not merged]`" and Deferred bullet "Story 22.12 details … reconcile against the merged code before the capture story" contradict `git log` (`70847351c8`, `ed15aea026`, both 2026-09-21) and the spine's own `.memlog.md` ("story 22.12 merged into troll as 70847351c8 during the run; code matches its story"). The AD-D6 `BookTimeSource (arrival | venue, 22.12)` and AD-D13 "after story 22.12 has merged" sentences also read as future.
*Fix:* in the Capability map replace the tag with `[ADOPTED]` and cite `collector_core/config.py:53,57` (`book_time_source`, `hold_back_seconds`), `collector_core/collector.py:594`, `collector_core/measure_lag.py`; delete the Deferred bullet (or reword it to "22.12 merged as `70847351c8`; the model was reconciled against it on 2026-09-21"); in AD-D13 change "after story 22.12 has merged" to "22.12 merged (`70847351c8`); the rename ships alone once bmad-loop is idle".

**H-2 — `<VENUE>_COLLECTOR_CONFIG` is frozen as a generic env-var pattern, but dYdX has no such variable; its config contract is a bind-mount path.**
Only `BYBIT_COLLECTOR_CONFIG` (`bybit_collector/collector.py:43`, compose `:72`) and `HYPERLIQUID_COLLECTOR_CONFIG` (`hyperliquid_collector/collector.py:37`, compose `:96`) exist. dYdX reads `dydx_collector/config.toml` next to the package file and compose bind-mounts `./config.toml:/app/dydx_collector/config.toml:rw` (`:50`). That path is a file contract tied to the *package directory*, so the `collection_control` move (which relocates the dYdX config loader) would silently break it unless AD-D12 freezes it — and it is exactly the kind of contract AD-D12 exists to freeze.
*Fix:* in AD-D12 replace "`<VENUE>_COLLECTOR_CONFIG`" with "`BYBIT_COLLECTOR_CONFIG`, `HYPERLIQUID_COLLECTOR_CONFIG`, and the dYdX config bind-mount target `/app/dydx_collector/config.toml` (compose `:50`, rw — the collector writes it back)". Add to the `collection_control` context row (AD-D1) a note that the dYdX loader's default path is package-relative today and must become an env var (`DYDX_COLLECTOR_CONFIG`, same pattern as the other two) *before* the move, as its own small story.

**H-3 — The parent-Deferred item "dYdX OI poll stamps WS feed liveness" is already closed in code; the spine claims to resolve it.**
`dydx_collector/collector.py:356-366` appends polled OI straight to `self._buffer` with a comment naming stories 22.5 and 22.14. The Capability map row "Parent Deferred: dYdX OI poll stamps WS feed liveness → `FeedGroup` treats REST-sourced data as `REST_FEED_NAME`" presents this as an open contradiction the DDD shape fixes.
*Fix:* mark the row `[ADOPTED]` ("closed by 22.14 in `dydx_collector/collector.py:356-366`; `REST_FEED_NAME` at `collector_core/feed.py:42`; the DDD shape keeps it closed by construction"), and add a Deferred note that the parent spine's entry (`:291`) needs a strike-through amendment, since the parent is binding and still lists it as open.

### Medium

**M-1 — Two `dydx_collector/collector.py` line-range citations attribute capture code to `collection_control`.**
AD-D1 `collection_control` "Today" = `dydx_collector/collector.py:194-625`. That span is the whole `DydxCollector` class and includes the venue book hooks `_clear_book_state:268`, `_apply_deltas:283`, `_uncross_step:328`, `_handle_crossed_book:331`, `_resync_book:346` and `_open_interest_loop:356` — under AD-D6 those are `capture/venues/dydx/policies.py` (LevelTagger, uncross ladder) material, not a control plane. The `observability` row's `:677-843` "incident handler" is the block `:648-870` (`_IncidentHandler` itself is `:806-848`).
*Fix:* `capture` row add `dydx_collector/collector.py:268-366` (book hooks + OI loop); `collection_control` row cite `:369-631` (`_subscribe` … `_prune_loop`); `observability` row cite `:648-870`.

**M-2 — The namespace-directory rule is correct but under-specified; the corollary must be stated or a story will write `from platform.kernel import …` and hit `'platform' is not a package`.**
Experiment: with no `platform/__init__.py`, `find_spec("platform")` is the stdlib module *and* `import platform.kernel.mod` raises `ModuleNotFoundError`. So `platform/` is not a usable namespace package at all while the stdlib module exists; contexts are only importable as top-level packages (`kernel`, `capture`, …) with `platform/` itself on `sys.path` — exactly how `troll/` works today (no `troll/__init__.py`; containers `COPY troll/<pkg> /app/<pkg>` and run from `/app`, `Makefile:145` runs pytest from the package root). The guard test as written ("asserts `find_spec("platform").origin` is the stdlib file from the repo root") is only meaningful when the *repo root* is on `sys.path`; inside the image `platform/` does not exist, and from `platform/` itself the check trivially passes.
*Fix:* in AD-D13 add: "No import may use a `platform.` prefix; contexts are top-level packages with `platform/` on `sys.path` (unchanged from `troll/` today: `/app` in images, `cd platform` locally). `test_namespace.py` runs the `find_spec` check in a subprocess with `cwd=<repo root>` and the repo root first on `sys.path`, so the guard exercises the one configuration that can shadow." Also note `ruff`'s isort `known-first-party` (`pyproject.toml:417`) stays `nautilus_trader`-only, so no tooling change is needed for the rename.

**M-3 — "Every row is the pin already in the repo" overstates four floors and omits the deployed interpreter.**
`redis>=8.0.1`, `aiohttp>=3.14.1`, `tomli_w>=1.0.0`, `pytest>=7.4.4,<8.0.0` are floors/ranges (`troll-requirements.txt:4,5,9,16`), not pins; resolution happens at image build. The Python row gives pyproject's supported range (3.12–3.14) while every image actually runs `python:3.13-slim` pinned by digest (`.docker/nautilus_trader.dockerfile:3-4`).
*Fix:* keep the values but change the preamble to "every row is the constraint already in the repo (pins where pinned, floors where the repo uses floors)"; add a row "Python (deployed base image) | 3.13-slim, digest-pinned (`.docker/nautilus_trader.dockerfile:4`)"; write `uvicorn[standard]`.

**M-4 — The `__name__` → catalog-directory pin is stated for `DydxSecondSnapshot` only; it applies equally to `OpenInterest`, and a legacy directory exists.**
`class_to_filename` (`funcs.py:39-51`) derives the dir for *any* custom `Data` class, so `kernel/open_interest.py`'s `OpenInterest` must also keep its `__name__` (→ `custom_open_interest`). The local catalog still holds `custom_dydx_open_interest` (pre-22.3 type; `collector_core/migrate_open_interest.py` exists to migrate it) and no `custom_open_interest`, so AD-D12's "every catalog directory name is frozen" must name both dirs, or a reader will drop the legacy one.
*Fix:* AD-D3: "`DydxSecondSnapshot` and `OpenInterest` keep their class names — `class_to_filename` derives `custom_dydx_second_snapshot` / `custom_open_interest` from `__name__`". AD-D12: add "including the legacy `custom_dydx_open_interest` directory until `migrate_open_interest` has run on every catalog root".

### Low

**L-1 — `epics.md (Epic 22, lines 2209-2530)`** — file is 2517 lines; Epic 22 runs `:2209` to EOF. *Fix:* "lines 2209-2517 (EOF)".

**L-2 — Deferred says only 22.14's story header reads `ready-for-dev`;** 22.12's does too (`22-12-….md:3` vs `sprint-status.yaml:294` `awaiting-operator`). *Fix:* "22.12 and 22.14 story-file headers".

**L-3 — `_stamp_to_ns` private-import count.** The parent recorded two importers; today there are six (`collector_core/{collector:141,build_candles:42,consolidate_catalog:72,prune_catalog:51,compare_klines:91,rebuild_seconds:80}.py`). *Fix:* say "six `collector_core` modules" in the Capability-map row so the kernel-clock story sizes the change correctly.

**L-4 — "twenty module globals" in AD-D10.** Count is 39 module-level assignments, 15 mutable runtime-state objects (list in table §3) plus one `global` statement. *Fix:* "fifteen mutable module globals (`engine.py:117-196,1005-1013`)".

**L-5 — `Collector._notify` / `_watchdog_transition`** are module-level functions (`collector_core/collector.py:388,353`), not `Collector` methods. *Fix:* drop the `Collector.` prefix in the `observability` row.

**L-6 — `bots/infrastructure/nautilus_host.py (VENUES)`** in the 22.6 row is a target path; today `VENUES` is `live_paper/venues.py:90`. *Fix:* add "(today `live_paper/venues.py:90`)" so the row has a "Today" anchor like the others.

**L-7 — `LiquidityTier`, `VolumeReading`, `TwoClocks`, `SampleVerdict`, `HoldBack`, `ArchiveDay`** are target-only names (none exists in code). They are correctly presented as targets everywhere except the inherited AD-7 row, which lists `LiquidityTier` beside the existing `classify_liquidity` without distinction. *Fix:* "`classify_liquidity` (today `dydx_collector/open_interest.py:40`), `LiquidityTier` (new)".

**L-8 — `make test` is not the only test list.** `Makefile:224-226` `test-live-paper` is a second list the `bots/` move must update. *Fix:* AD-D12 "the `Makefile` test lists (`test`, `test-live-paper`)".

---

## 5. What was not checked, and why

- Whether any pinned version still exists on PyPI/npm or has a newer release: out of scope by instruction (brownfield, no bumps); the pins are the repo's, not the spine's.
- Behaviour of the venues' REST endpoints beyond what `trade_backfill.py:27-47` documents: the spine cites those numbers from that docstring, which itself records a 2026-09-21 wire check; no independent re-verification was attempted.
