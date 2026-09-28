# Review — versions lens (re-run after Story 26.3)

**Target:** `ARCHITECTURE-SPINE.md` (platform/ DDD spine), working tree on branch `troll` at `de73ac8295` plus the uncommitted Story 26.3 reconciliation (`updated: '2026-09-28'`).
**Lens:** every named technology/version in the spine checked against what the repo pins today (`platform/requirements.txt`, `pyproject.toml`, `uv.lock`, `platform/frontend/package.json` + `package-lock.json`, `platform/{collector,data_api,bots}.dockerfile`, `.docker/nautilus_trader.dockerfile`, `platform/docker-compose.yml`), and against what the built images actually contain. Secondary: a spot-check of `path:line` citations in `[ADOPTED]` rules and the AD-D1 table.
**Method:** file reads; `docker run --entrypoint python3 platform-collector:latest` (built 2026-09-28 06:26) and `nautilus-trader-base:1.229.0` (built 2026-09-05) for installed versions and `pip check`; `lint_spine.py` (0 findings). Prior gate: `reviews/review-versions.md` (2026-09-21).

**Verdict: findings.** Every Stack row still matches the file it was taken from, and all three prior mechanical fixes (M-3 deployed interpreter row, `uvicorn[standard]`, floors labelled) landed. But one pinned row (pandas) contradicts nautilus_trader's own constraint and the deployed image fails `pip check` on it, one row is unpinned (Dozzle `:latest`), the Parquet engine the catalog-compatibility claim rests on (pyarrow) is not in the table and is not locked in the image, and the Stack preamble still names the deleted `troll-requirements.txt`. Citations: 40 checked, 39 exact, 1 imprecise.

---

## 1. Stack table — row by row (spine lines 368-395)

| Row (spine line) | Spine says | Repo today | Deployed image | Verdict |
| --- | --- | --- | --- | --- |
| Python (376) | 3.12–3.14 / 3.13 (`python:3.13-slim`, digest-pinned) | `pyproject.toml:25` `>=3.12,<3.15`, classifiers `:11-13`; `.docker/nautilus_trader.dockerfile:3-4` (comment "python:3.13-slim", `FROM python@sha256:a0779d…`), site-packages `python3.13` `:78,83` | 3.13.13 | confirmed |
| nautilus_trader / base image (377) | 1.229.0 | `pyproject.toml:3`; `FROM nautilus-trader-base:1.229.0` in `collector.dockerfile:3`, `data_api.dockerfile:17`, `bots.dockerfile:3` | base tag 1.229.0 | confirmed |
| fastapi (378) | 0.141.1 | `requirements.txt:19` | — | confirmed |
| uvicorn[standard] (379) | 0.52.4 | `requirements.txt:20` | — | confirmed |
| httpx (380) | 0.28.1 | `requirements.txt:24`; `uv.lock` 0.28.1 | — | confirmed |
| redis client, floor (381) | >=8.0.1 | `requirements.txt:4` (paired-version comment `:3`) | 8.1.0 | confirmed (floor) |
| redis broker (382) | redis:8-alpine | `docker-compose.yml:36` | — | confirmed |
| aiohttp, floor (383) | >=3.14.1 | `requirements.txt:5`; **but** `pyproject.toml:103` test group `aiohttp==3.14.0`, `uv.lock` 3.14.0 | 3.14.3 (local interpreter 3.14.1) | row correct; repo inconsistent — L-2 |
| urwid (384) | 4.0.6 | `requirements.txt:13` | — | confirmed |
| **pandas (385)** | **3.0.4** | `requirements.txt:2` `pandas==3.0.4`; **but** `pyproject.toml:31` `pandas>=2.3.3,<3.0.0` (nautilus_trader runtime dependency), `pandas-stubs>=2.3.3,<3.0.0` `:97`, `uv.lock` 2.3.3 | base 2.3.3 → platform images 3.0.4; `pip check`: *"nautilus-trader 1.229.0 has requirement pandas<3.0.0,>=2.3.3, but you have pandas 3.0.4."*; local test interpreter 2.3.3 | **contradicts the nautilus_trader pin** — H-1 |
| plotly (386) | 6.8.0 | `requirements.txt:1`; `pyproject.toml:86` `>=6.8.0,<7.0.0`; `uv.lock` 6.8.0 | — | confirmed |
| tomli_w, floor (387) | >=1.0.0 | `requirements.txt:9` | — | confirmed |
| pytest / pytest-asyncio (388) | >=7.4.4,<8.0.0 / 0.23.8 | `requirements.txt:16-17`; `pyproject.toml:105,107`; `uv.lock` 7.4.4 / 0.23.8 | — | confirmed |
| React / react-dom (389) | 19.3.0 | `package.json:18-19`; lock 19.3.0 | — | confirmed |
| react-router (390) | 8.3.1 | `package.json:20`; lock 8.3.1 | — | confirmed |
| @tanstack/react-query (391) | 5.102.8 | `package.json:16` | — | confirmed |
| lightweight-charts (392) | 5.2.1 | `package.json:17` | — | confirmed |
| Vite / TypeScript (393) | 8.3.0 / ~6.0.2 | `package.json:32,31`; `package-lock.json` resolves vite 8.3.0, typescript 6.0.3 (inside `~6.0.2`; `npm ci` at `data_api.dockerfile:10` builds from the lock) | — | confirmed |
| Node build stage (394) | node:24-slim | `data_api.dockerfile:7` (tag, no digest; no `.nvmrc`/`engines`) | — | confirmed |
| **Dozzle (395)** | **amir20/dozzle:latest** | `docker-compose.yml:412` | — | **unpinned** — M-1 |
| *(missing)* pyarrow | — | `pyproject.toml:33` `pyarrow>=24.0.0` (floor), `uv.lock` 24.0.0 | **25.0.1** in base and platform images (`.docker/nautilus_trader.dockerfile:77` `uv pip install --system dist/*.whl` re-resolves outside `uv.lock`) | **missing row; not locked at deploy** — M-2 |
| *(not applicable)* textual | — | not a dependency; `bot_tui` uses urwid | — | n/a |

Stale file references across the spine: `troll-requirements.txt` survives once, in the Stack preamble (line 371) — L-1. `live_paper.dockerfile` appears only where the spine records the rename (lines 297, 448) — correct. AD-D12's "the three dockerfiles" (line 295) now means `collector`, `data_api`, `bots` — correct. Historical `troll/` / `live_paper` citations inside `~~struck~~` or `[amended … was …]` text are intentional (AD-D13 keeps historical citations).

## 2. Citation spot-check (`[ADOPTED]` rules, AD-D1 table, Capability map)

40 citations read at the cited line; 39 show exactly what the spine claims.

| Spine claim | Line content | Verdict |
| --- | --- | --- |
| `_at_fixed_precision` `capture/venues/dydx/client.py:54` | `def _at_fixed_precision(price: Price) -> Price:` | ok |
| `exact_text` `capture/domain/trade_history.py:98` | `def exact_text(text: str, precision: int) -> str:` | ok |
| `_at_exact_millis` `capture/venues/hyperliquid/client.py:77` | `def _at_exact_millis(trade: TradeTick) -> TradeTick:` | ok |
| `classify_liquidity` `collection_control/domain/liquidity.py:78` | `def classify_liquidity(` | ok |
| `Bot.id == order_id_tag` `bots/infrastructure/nautilus_host.py:273` | `order_id_tag=bot.bot_id,` | ok |
| `VENUES` `nautilus_host.py:121` | `VENUES: MappingProxyType[str, VenueSpec] = …` | ok |
| `MAX_INCIDENTS` = 50 `bots/domain/bot.py:34` | `MAX_INCIDENTS: int = 50` | ok |
| incidents key `bots/application/ports.py:42` | `return f"bots:incidents:{bot_id}"` | ok |
| incidents writer `bots/application/supervise.py:159-180` | `key = incidents_key(self.bot.id)` … read-modify-write | ok |
| incidents reader `bot_tui/bot_incidents_state.py:92` | `raw = await client.get(f"bots:incidents:{bot_id}")` | ok |
| `SecondSink` `capture/application/ports.py:152` | `class SecondSink(Protocol):` | ok |
| `SecondSink.apply` call `capture_service.py:866-886` | `_apply_to_candle_store` … `self._second_sink.apply(iid, rows)` | ok |
| `CandleSink` `candles/application/sink.py:25` | `class CandleSink:` | ok |
| injection `capture/venues/dydx/__main__.py:117` | `second_sink=CandleSink(store),` | ok |
| `VerifiedDaysStore` `candles/infrastructure/verified_days.py:30` | `class VerifiedDaysStore:` | ok |
| entrypoint injection `archive/compare_klines.py:177` | `verified = VerifiedDaysStore(args.db) if …` | ok |
| port use `archive/application/reconcile_day.py:63,195` | import of `VerifiedDays`; docstring "goes through the `VerifiedDays` port" | ok |
| `views/preferences.py:84,120` | `save_chart_indicators`, `save_screener_columns` | ok |
| callers `data_api/routes/indicators.py:161`, `rankings.py:242` | `preferences.save_chart_indicators(…)`, `preferences.save_screener_columns(…)` | ok |
| `SecondOHLC` `kernel/second_snapshot.py:103` | `class SecondOHLC(NamedTuple):` | ok |
| `book_time_source`/`hold_back_seconds` `capture/application/config.py:56,60` | both fields, defaults `"arrival"` / `0.0` | ok |
| `LiveBook.hold`/`drain` `live_book.py:188,198`; overflow `:225` | `def hold(`, `def drain(`, `def check_overflow(` | ok |
| `_drain_pending_deltas` `capture_service.py:699`; overflow `:711-733` | `def _drain_pending_deltas(…)`; `_check_pending_overflow` → `book.check_overflow` | ok |
| `TradeIntake` late/ahead `trade_intake.py:156-211` | `def fold(… ahead_ns)` documenting `late`/`ahead` | ok |
| `_venue_second_loop` `capture_service.py:1150` | `async def _venue_second_loop(self)` | ok |
| OI `poll_loop` `capture_service.py:1746-1780` | `async def poll_loop(` … `self._buffer[(type(item), iid)].append(item)` | ok |
| `EMPTY_TOP` site `capture/application/sites.py:29` | `EMPTY_TOP = "collector.empty_top"` | ok |
| **`EmptyTop` rejection `capture/domain/live_book.py:281`, "`SecondSampler` rejects"** | `:281` is `def snapshot_top(` (a `LiveBook` method); the rejection is `return EMPTY_TOP, None` at `:301`; the type is `capture/domain/verdicts.py:38` | **imprecise** — L-3 |
| `write_data()` callers `archive/application/backfill_bars.py:516`, `repair.py:148` | `catalog.write_data(run)`, `catalog.write_data([cleared])` | ok |
| `test_boundaries.py:228` / `:235` / `:671` / `:677` | `test_every_module_is_in_a_context`, `test_cross_context_edges_follow_the_graph`, `test_every_import_resolves_to_a_context`, `test_checker_places_modules_by_top_level_package_only` | ok |
| `test_namespace.py:68` / `:73` / `:89` / `:118` | namespace-dir check, stdlib-wins check, `test_no_module_is_a_migration_shim`, one-Arrow-registration check | ok |
| `test_legacy_names.py:59-81` | `ALLOWANCES = (` … closing `)` at `:81` | ok |
| compose `DYDX_PLAN_PATH` `:65`, mount `:82` | `DYDX_PLAN_PATH: "/app/dydx_collector/config.toml"`; `- ./data/dydx_config.toml:/app/dydx_collector/config.toml:rw` | ok |
| `scripts/capture_hl_ws.py:41,118` | `if venue == "bybit":`; `--venue choices=("hyperliquid", "bybit")` | ok |
| hot-path baseline values | `tests/fixtures/hotpath_baseline.json`: 0.7128 / 89.154 / 104.9495 / 2720, recorded 2026-09-28 | ok |

---

## 3. Findings

### Critical

None.

### High

**H-1 — The pandas pin (3.0.4) contradicts nautilus_trader's own `pandas<3.0.0`; the deployed images fail `pip check` and tests run on a different pandas major than production.**
`platform/requirements.txt:2` pins `pandas==3.0.4`; `pyproject.toml:31` declares nautilus_trader's runtime dependency `pandas>=2.3.3,<3.0.0` and `uv.lock` locks 2.3.3. The base image carries 2.3.3; `pip install -r requirements.txt` in each thin layer upgrades it to 3.0.4, and `pip check` in `platform-collector:latest` prints "nautilus-trader 1.229.0 has requirement pandas<3.0.0,>=2.3.3, but you have pandas 3.0.4." Meanwhile the documented test invocation (plain `python3 -m pytest` on the system interpreter) runs pandas 2.3.3, so `make test`-outside-Docker never exercises the major version the collectors, archive and data_api actually run. The spine presents the row as a clean, reality-checked pin and never mentions the conflict, even though it lists `pyproject.toml` as one of the row sources. Pandas sits on the catalog read path (`ParquetDataCatalog` → DataFrame), so this is a correctness exposure, not cosmetics. The spine should not silently choose; it should state the conflict and defer the resolution.
*Fix:* spine line 385 → `| pandas | 3.0.4 (\`platform/requirements.txt:2\`; overrides nautilus_trader's own \`pandas>=2.3.3,<3.0.0\`, \`pyproject.toml:31\`, \`uv.lock\` 2.3.3 — every platform image fails \`pip check\` on it, the local test interpreter runs 2.3.3) |`, and add a Deferred bullet: "**pandas major-version split.** Platform images run pandas 3.0.4 against nautilus_trader 1.229.0's `<3.0.0` constraint; local tests run 2.3.3. Resolve by pinning `pandas==2.3.3` in `platform/requirements.txt` (matches `uv.lock`) or by proving 3.x on the catalog read path and running the test suite inside the image; revisit before the next base-image rebuild."

### Medium

**M-1 — Dozzle is unpinned; the preamble says every row is a pin or floor.**
Line 395 `amir20/dozzle:latest` (`docker-compose.yml:412`). `:latest` is neither a pin nor a floor, so the row falsifies the line 370 preamble, and a `docker compose pull` can change the log viewer under the operator.
*Fix:* either pin a tag in compose (outside this review's edit scope) and write it in the row, or change line 395 to `| Dozzle (log viewer) | unpinned: \`amir20/dozzle:latest\` (\`docker-compose.yml:412\`) |` and the preamble (line 370) to "every row is the pin or floor already in the repo, except Dozzle, which the repo leaves unpinned".

**M-2 — pyarrow, the Parquet engine behind the catalog-compatibility guarantee, is missing from the Stack, and the images do not honour `uv.lock` for it.**
AD-6/AD-D18 and the whole "written via the catalog's own `write_data()`" guarantee rest on pyarrow's writer; Epic 30's zstd/delta work depends on its version. `pyproject.toml:33` is a floor (`>=24.0.0`) and `uv.lock` locks 24.0.0, but the base image installs 25.0.1 because `.docker/nautilus_trader.dockerfile:77` (`uv pip install --system dist/*.whl`) re-resolves the wheel's dependencies from the index rather than from the lock. The spine names every lighter library but not this one.
*Fix:* add a row after line 377: `| pyarrow (via nautilus_trader, floor) | >=24.0.0 (\`pyproject.toml:33\`; \`uv.lock\` 24.0.0; base image 25.0.1 — \`.docker/nautilus_trader.dockerfile:77\` resolves outside the lock) |`.

### Low

**L-1 — Stale `troll-requirements.txt` in the Stack preamble, and a stale reality-check date.**
Lines 370-372: "(`platform/requirements.txt`, then `troll-requirements.txt`; …), reality-checked 2026-09-21 by the Reviewer Gate". The file no longer exists (only in old `.bmad-loop` worktrees).
*Fix:* "every row is the pin or floor already in the repo (`platform/requirements.txt`, formerly `troll/troll-requirements.txt`; `platform/frontend/package.json` + `package-lock.json`; the dockerfiles; `pyproject.toml`/`uv.lock`), reality-checked 2026-09-21 and re-checked 2026-09-28 (`reviews/review-versions-2026-09-28.md`)".

**L-2 — aiohttp has two disagreeing constraints in the repo.**
Row line 383 `>=3.14.1` is `requirements.txt:5`, but `pyproject.toml:103` pins the test group to `aiohttp==3.14.0` (`uv.lock` 3.14.0). The images do not install the test group (3.14.3 deployed), so no image breaks, but the two files say different things.
*Fix:* append to line 383: "(`platform/requirements.txt:5`; nautilus_trader's own test group pins `==3.14.0`, `pyproject.toml:103`, not installed in the images)".

**L-3 — `EmptyTop` citation points at a method header and names the wrong actor.**
Line 486: "`SecondSampler` rejects the book as `EmptyTop` (`capture/domain/live_book.py:281`)". `:281` is `LiveBook.snapshot_top`'s `def`; the rejection is `return EMPTY_TOP, None` at `:301`, the type is `capture/domain/verdicts.py:38`, and `CaptureService` handles it at `capture/application/capture_service.py:997`.
*Fix:* "`LiveBook.snapshot_top` rejects the book as `EmptyTop` (`capture/domain/live_book.py:301`, type `capture/domain/verdicts.py:38`) and `CaptureService` warns and ledgers `collector.empty_top` (`capture_service.py:997`, `capture/application/sites.py:29`)".

---

## 4. What was not checked, and why

- Whether any pinned version has a newer release: out of scope (brownfield, no bumps).
- `platform-data_api:latest` / `platform-live-paper:latest` installed versions: the `docker run --entrypoint python3` probe did not start on those images; they build from the same `requirements.txt` over the same base, so the collector image's result (pandas 3.0.4, pyarrow 25.0.1) is expected to hold. The `platform-{bybit,hyperliquid}_collector` images are stale (2026-09-21) and were not inspected.
- The base image predates the current `uv.lock` (built 2026-09-05); a rebuild today would re-resolve pyarrow again, which is M-2's point rather than a separate finding.
