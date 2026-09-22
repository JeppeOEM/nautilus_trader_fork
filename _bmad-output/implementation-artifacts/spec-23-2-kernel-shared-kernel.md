---
title: 'Story 23.2: kernel/ shared kernel'
type: 'refactor'
created: '2026-09-22'
status: 'done'
baseline_revision: '2d7dd5ab6e'
final_revision: ''
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/_bmad-output/implementation-artifacts/23-2-kernel-shared-kernel.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-23-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md'
  - '{project-root}/platform/CLAUDE.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** The shared types (`DydxSecondSnapshot`, `SecondOHLC`, `OpenInterest`), the one trade fold, three separate `InstrumentId` parsers, the 300 s skew margin (`ARRIVAL_MARGIN_NS`) and five related constants, the venue REST transport, the catalog read helpers and a zstd `write_table` patch that is duplicated all live in capture/views packages. So every context imports a writer or a reader to reach them, and two copies can drift.

**Approach:**
- Create `platform/kernel/` with exactly the AD-D3 members.
- Move the code verbatim wherever possible, preserving class names, Arrow schemas and payload bytes.
- Leave pure re-export shims at the old paths and repoint every in-repo caller.
- Prove the move is byte-safe with fixtures recorded **before** the move.
- Extend the 23.1 guards so the kernel stays pure.
- Update the images, the Makefile and the docs in the same change.

## Boundaries & Constraints

**Always:**
- **Kernel membership.** `kernel/` holds exactly `__init__.py`, `second_snapshot.py`, `open_interest.py`, `fold.py`, `indicators.py`, `performance_metrics.py`, `venues.py`, `clocks.py`, `archive_markers.py`, `venue_http.py`, `catalog_files.py`, `parquet_compat.py`, plus `tests/`.
- **Kernel purity.** The kernel imports no in-repo context (no `observability` either) and holds:
  - no module-level mutable state: tables are `MappingProxyType`/`frozenset`/tuple;
  - no `global` statement;
  - no store: no `sqlite3` or `redis`;
  - no config loader: no `tomllib`, no `os.environ`/`getenv`;
  - no ledger call.

  The sanctioned import-time effects are `register_arrow` (once per class), and the zstd patch, which runs only when `parquet_compat.apply_zstd_default()` is called.
- **Frozen published language (AD-D12):**
  - The class names `DydxSecondSnapshot`/`OpenInterest`, their Arrow schemas and metadata, and the catalog directory names stay unchanged.
  - The `snapshots:raw` JSON bytes stay unchanged.
  - The `_archive_gaps/<iid>.jsonl` line format stays unchanged: the key order is `instrument_id, from_ns, to_ns, reason, count`, written with `json.dumps` defaults.
  - REST request URLs, methods, bodies and User-Agent headers stay unchanged per call site.
  - No config key, env var, compose service or schema changes.
- **Shims follow the 23.1 shape.**
  - A whole-module shim is `from <new> import <name>` lines, plus `warnings.warn(..., DeprecationWarning, skip_file_prefixes=("<frozen importlib",))`, plus `REMOVE_AFTER = "24-2-views-read-models-and-reader-side-revalidation-removed"`.
  - A name moved out of a module that stays is served through a `_MOVED_NAMES` table plus `MOVED_NAMES_REMOVE_AFTER` (same key) plus a warning `__getattr__`.
  - A name whose successor changed shape goes in `_REPLACED_NAMES` and raises, naming its successor.
  - No shim defines anything. No in-repo module imports an old path; `test_namespace` enforces this.
- **Behaviour-preserving moves.**
  - The read helpers keep their exact file-selection margins: the symmetric 60 s widening becomes `kernel.clocks.READ_SPAN_MARGIN_NS`.
  - `bybit_category` keeps `ValueError` compatibility, because `MalformedInstrumentId` subclasses it.
- **Every cross-context constant is tied to `MAX_TS_INIT_SKEW_NS`** as an expression of it, or by an assertion that it is less than or equal.
- **No `DeprecationWarning` from `platform/` code** in the test run (TEST-04).

**Block If:**
- A frozen contract (a schema, a payload, a directory name, the marker format, request bytes) would have to change to satisfy an AC.
- `register_arrow` cannot be kept to exactly one registration per class.

**Never:**
- Never modify `nautilus_trader/` or `crates/`. Never write `sprint-status.yaml`. Never add a dependency.
- Never loosen a guard to make it pass: no blanket `filterwarnings`, no silent skip, and never add to a legacy table an entry that a moved import does not strictly need.
- No catalog object construction and no write path in `kernel.catalog_files`.
- Don't move `ranking_engine/engine.py`'s own URL maps. They stay until 25.2 and are listed in the boundary test.
- Don't chase the `snapshots:raw` parsers in ranking or bot_tui (25.2 / later).
- Don't move `archive_gaps`' file I/O into the kernel. It stays in `collector_core/archive_gaps.py` (archive, 25.1).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| pre-move catalog row | fixture parquet written by the pre-move code | read back through `ParquetDataCatalog` post-move with equal field values; the post-move write of the same object has an identical Arrow schema (with metadata) and table, under an identical dir name | — |
| `snapshots:raw` batch | fixture objects | `json.dumps([DydxSecondSnapshot.to_dict(s) ...])` byte-equal to the recorded JSON | — |
| id table | `BTC-USD-PERP.DYDX`, `BTCUSDT-LINEAR.BYBIT`, `BTCUSD-INVERSE.BYBIT`, `BTCUSDT-SPOT.BYBIT`, `BTC-USD-PERP.HYPERLIQUID`, `BTC` | `venue_of`/`market_kind`/`venue_kind`/`bybit_category` per shape; `BTC` gives `MalformedInstrumentId` from `venue_of`, `"unknown"` from `market_kind` | `bybit_category` of a non-Bybit or `-PERP` id raises `MalformedInstrumentId` |
| file stem | `2026-06-30T17-17-34-103475440Z_2026-06-30T17-18-00-000000000Z` | `CatalogFileSpan(start_ns, end_ns)` equal to the old `_stamp_to_ns` pair | a stem the catalog did not write raises `ValueError` (as before) |
| gap line | `ArchiveGap` round-trip | `decode(encode(g)) == g`; `encode` byte-equal to the legacy `json.dumps` line | a malformed line raises `ValueError` naming the text |
| zstd patch twice | `apply_zstd_default()` called by `collector` and `backfill_bars` in either order | `pq.write_table` wrapped once; `compression` defaults to zstd and an explicit value wins | — |
| stale caller | a module imports `collector_core.fold` | `test_namespace` fails naming the site | — |

</intent-contract>

## Code Map

- `platform/collector_core/{second_snapshot,open_interest,fold,venue_http}.py`, `common/venues.py`, `ml_signals/{venue,indicators,performance_metrics}.py` -- move to `kernel/` and become shims
- `platform/ml_signals/catalog_stats.py:74-199` -- `SecondOHLC`, `_OHLC_COLUMNS`, `_FILE_MARGIN_NS`, `query_second_ohlc`, `second_ohlc_arrays`, `_stamp_to_ns`, `data_file_ranges` go to the kernel. The views, archive and ranking functions stay and import from the kernel.
- `platform/collector_core/archive_gaps.py` -- the format and constants go to `kernel.archive_markers`/`clocks`. `record_gap`/`load_gaps`/`in_gap` stay here over them (the ledger call stays here).
- `platform/collector_core/build_candles.py:77` `_files_by_day` → `kernel.catalog_files.files_by_day`. The `_stamp_to_ns` users are `collector.py:302`, `build_candles.py:85`, `compare_klines.py:550`, `consolidate_catalog.py:152`, `prune_catalog.py:182`, `rebuild_seconds.py:167,228`.
- `platform/collector_core/collector.py:224-253` (skew constants, zstd patch), `backfill_bars.py:137-150` (zstd patch), `rebuild_seconds.py:104` (`_TS_INIT_MARGIN_NS`), `prune_catalog.py:193`, `collector.py:1725,1760` (backfill refusal)
- The REST sites:
  - `collector_core/{trade_backfill,compare_klines}.py` build `Request`s from the URL maps.
  - `bybit_collector/{book_snapshot,open_interest}.py`, `hyperliquid_collector/book_snapshot.py` and `dydx_collector/open_interest.py` each have their own maps or `urlopen`.
  - `ranking_engine/engine.py:214-220` stays until 25.2.
- The venue dispatch by id suffix:
  - `collector_core/compare_klines.py:407,481,519,627`, `build_candles.py:55`, `consolidate_catalog.py:145`, `prune_catalog.py:103,211,233`
  - `live_paper/venues.py:79-80`
  - `bot_tui/coins_pane.py:70` is display truncation, not a dispatch, and stays.
- Callers: the inventory is in the Explore report. There are ~60 import sites across `ml_signals`, `data_api`, `ranking_engine`, `live_paper`, `bot_tui`, the venue collectors and the tests. Find them with `grep -rn "collector_core\.\(second_snapshot\|open_interest\|fold\|venue_http\)\|common\.venues\|ml_signals\.\(venue\|indicators\|performance_metrics\)\|ml_signals import performance_metrics\|catalog_stats import \(SecondOHLC\|_stamp_to_ns\|data_file_ranges\|second_ohlc_arrays\|query_second_ohlc\)\|_catalog_stats\.\(query_second_ohlc\|data_file_ranges\)\|catalog_stats\.\(query_second_ohlc\|data_file_ranges\)" platform --include=*.py`.
- Tests that move to `kernel/tests/`: `collector_core/tests/{test_fold,test_open_interest}.py`, `common/tests/test_venues.py`, `ml_signals/tests/{test_venue,test_indicators,test_performance_metrics}.py`.
- `platform/tests/{test_boundaries,test_namespace,test_images}.py`, `_source_tree.py` -- the 23.1 guards.
- Build and docs: `platform/{collector,data_api,live_paper}.dockerfile`, `Makefile` (`test`, `test-live-paper`), `platform/CLAUDE.md` ("Adding a venue" step 5 and the citations), `ARCHITECTURE.md`, `docs/DATA_DICTIONARY.md` §1.7/§1.8/§2.1, and the parent spine's Deferred "Writer→reader imports".

## Tasks & Acceptance

**Execution:**
- [x] `platform/kernel/tests/fixtures/` -- **first, before moving anything.**
  - Use the pre-move code in the collector image to write one `DydxSecondSnapshot` and one `OpenInterest` through `ParquetDataCatalog.write_data` into `fixtures/pre_move_catalog/`.
  - Record `snapshots:raw.json` (the collector's exact `json.dumps` of two snapshots, one without trades) and one legacy `_archive_gaps` line.
  - Commit the recording script's parameters in the test's docstring.
- [x] `platform/kernel/{__init__,second_snapshot,open_interest,fold,indicators,performance_metrics}.py`
  - Move verbatim with `git mv`, then update the docstrings.
  - Add `SecondOHLC` to `second_snapshot.py`.
- [x] `platform/kernel/venues.py` -- merge the three parsers.
  - Contents: `VENUE_KINDS` (`MappingProxyType`), `MalformedInstrumentId`, `venue_of`, `venue_kind`, `market_kind`, `has_venue(iid, venue)` (never raises), `market_suffix(iid)` and `bybit_category`.
  - `bybit_category` works over `market_kind`: `-LINEAR` gives `linear`, `-INVERSE` gives `inverse`, `-SPOT` gives `spot`, and anything else raises `MalformedInstrumentId`.
  - Repoint the venue-dispatch sites in the Code Map to `venue_of`/`has_venue`/`market_suffix`.
- [x] `platform/kernel/clocks.py`
  - `NS_PER_S`, `NS_PER_MS`, `NS_PER_DAY`, and `MAX_TS_INIT_SKEW_NS = 300 * NS_PER_S`.
  - `READ_SPAN_MARGIN_NS = 60 * NS_PER_S`, asserted ≤ MAX.
  - `TwoClocks(ts_event, ts_init)`: a frozen dataclass with `skew_ns` and `within_skew()`.
  - `CatalogFileSpan(start_ns, end_ns)` with `from_stem`, `from_path`, `covers(ts_event, margin=MAX_TS_INIT_SKEW_NS)` and `overlaps(lo, hi, margin)`.
  - The six `_stamp_to_ns` users use `CatalogFileSpan`. `ARRIVAL_MARGIN_NS` users use `MAX_TS_INIT_SKEW_NS`. `rebuild_seconds._TS_INIT_MARGIN_NS` is defined as `MAX_TS_INIT_SKEW_NS`.
- [x] `platform/kernel/archive_markers.py`
  - `ArchiveGap(iid, from_ns, to_ns, reason, count)`, `GAPS_DIRNAME`, `encode`, `decode` (raises `ValueError`), `path_for(catalog, iid)`, and `in_gap`.
  - `collector_core/archive_gaps.py` keeps `record_gap`/`load_gaps` over these.
  - `ARRIVAL_MARGIN_NS`, `GAPS_DIRNAME` and `in_gap` are served through `_MOVED_NAMES`.
- [x] `platform/kernel/venue_http.py`
  - The URL maps (read-only mappings), `DYDX_NETWORKS`, `USER_AGENT`, `TIMEOUT_S`, `HttpJson`, `http_json`, plus the request builders `get_request(url, user_agent=USER_AGENT)` and `post_json_request(url, body, user_agent=USER_AGENT)`, and `dydx_indexer_url(network, path)`.
  - Every REST site in the Code Map except `ranking_engine` builds its request and URL through it, with its own User-Agent kept.
  - `collector_core/venue_http.py` becomes a shim.
- [x] `platform/kernel/catalog_files.py`
  - Contents: `data_file_ranges`, `second_ohlc_arrays`, `query_second_ohlc`, `files_by_day` and `snapshot_files(catalog, iid)`, over `CatalogFileSpan`.
  - The catalog directory names come from `nautilus_trader.persistence.funcs.class_to_filename`, not a literal.
  - `catalog_stats` serves the moved names through `_MOVED_NAMES`, with `_stamp_to_ns` in `_REPLACED_NAMES`. `build_candles._files_by_day` is removed and `rebuild_seconds` imports `files_by_day`.
- [x] `platform/kernel/parquet_compat.py`
  - `apply_zstd_default()`: idempotent, with the wrapper identified by an attribute on the installed function (not a mutable module flag).
  - `collector.py` and `backfill_bars.py` call it at import, in place of their own patches.
- [x] Shims: `collector_core/{second_snapshot,open_interest,fold,venue_http}.py`, `common/venues.py` and `ml_signals/{venue,indicators,performance_metrics}.py`. Repoint every caller, including the tests and the `monkeypatch.setattr(<module>, "query_second_ohlc", ...)` targets.
- [x] `platform/kernel/tests/` -- port the moved tests and add:
  - the fixture test;
  - the id table test;
  - `CatalogFileSpan`/`TwoClocks` tests;
  - the marker round-trip and legacy-bytes tests;
  - the `catalog_files` equivalence tests against a tmp catalog;
  - the `parquet_compat` idempotence tests;
  - `venue_http` builder tests (URL, method, body, headers).
- [x] `platform/tests/test_skew_constants.py`
  - Asserts, against `MAX_TS_INIT_SKEW_NS`: `READ_SPAN_MARGIN_NS` ≤ MAX; `_TS_INIT_MARGIN_NS` == MAX; `_MAX_CATCH_UP_SECONDS*NS` ≤ MAX; `max(hold_back_seconds over the three config.toml)*NS + _VENUE_AHEAD_NS` ≤ MAX; and that the backfill refusal and the fetch floor use MAX.
  - Also the tighter chain: catch-up + 1 s + hold-back + venue-ahead ≤ `READ_SPAN_MARGIN_NS`.
  - It skips, with the reason, in an image without `collector_core`.
- [x] `platform/tests/test_boundaries.py`
  - Remap the moved modules: shims map to `KERNEL`, `archive_gaps` to `ARCHIVE`, and the moved tests' entries are deleted.
  - Drop the kernel symbols from `LEGACY_SYMBOL_TO_CONTEXT`, the `(KERNEL, OBSERVABILITY)` edge and the eight `_stamp_to_ns` private entries. Bump `THIS_STORY`.
  - Add these tests:
    - kernel membership is exact;
    - kernel purity (AST: no in-repo non-kernel import, no forbidden stdlib/store/config import, no `os.environ`/`getenv`, no `global`, no top-level mutable literal or `dict`/`list`/`set`/`defaultdict`/`deque` call);
    - no literal venue REST URL outside the kernel, with `LEGACY_VENUE_URLS_UNTIL = {"ranking_engine.engine": "25-2-ranking-context-rankingboard-replaces-module-globals"}` expiring like the other tables;
    - no `urllib.request.Request`/`urlopen` outside the kernel, same legacy table;
    - no `endswith` on an id-suffix literal (`".UPPER"`/`"-UPPER"`/`f".{…}"`) outside the kernel.
- [x] `platform/tests/test_namespace.py`
  - Exactly one `_SCHEMAS` key per kernel `Data` class `__name__`, after importing both the kernel modules and every shim.
  - A `kernel` membership self-check.
  - The shim identity checks are already generic.
- [x] `platform/{collector,data_api,live_paper}.dockerfile` -- `COPY platform/kernel ./kernel`.
- [x] `Makefile` -- both lists: add `kernel/tests`; remove `common/tests` (moved).
- [x] `platform/CLAUDE.md`, `ARCHITECTURE.md`, `docs/DATA_DICTIONARY.md`, parent spine -- re-cite the kernel paths. "Adding a venue" step 5 cites `kernel/venues.py`. Amend the Deferred "Writer→reader imports" entry `[amended 2026-09-22: Story 23.2]`: ledger 23.1; types/clocks/read helpers 23.2; `candle_store` pending 24.1.

**Acceptance Criteria:**
- Given the tree after this story, when `make test`'s list runs in the collector image with the read-only source mount, then `kernel/tests`, `test_boundaries`, `test_images`, `test_namespace`, `test_skew_constants` and `test_hotpath` pass, there are no new failures against the 10 pre-existing ones, and no `DeprecationWarning` comes from `platform/` code.
- Given `ls platform/kernel`, when it is listed, then it shows exactly the AD-D3 modules plus `__init__.py` and `tests/`.
- Given the three images built from the checkout, when `test_images` and `make test-live-paper`'s list run, then they pass.
- Given `grep -rn "_stamp_to_ns\|ARRIVAL_MARGIN_NS\|_files_by_day" platform --include=*.py`, when it runs, then the only hits are shim tables and the kernel docstrings.

## Spec Change Log

## Review Triage Log

### 2026-09-22 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 12 (high 0, medium 2, low 10)
- defer: 0
- reject: 5
- addressed_findings:
  - `[medium]` `[patch]` `bybit_category` gained `inverse` (as the story asked), which silently enabled a trade backfill, a kline reconciliation and a book check for inverse ids that were never wire-verified (a `"inverse": 1000` depth had been guessed). `trade_backfill`, `compare_klines` and `bybit_collector.book_snapshot` now refuse a non-verified category before any request (`Known limit:` naming the upgrade path); three tests.
  - `[medium]` `[patch]` The skew chain (catch-up + 1 s + hold-back + venue-ahead ≤ `READ_SPAN_MARGIN_NS`) was checked only against the committed `config.toml`s, but deployed configs are bind-mounted. `Collector.__init__` now refuses a too-large `hold_back_seconds` (`_check_skew_budget`); boundary test at the exact limit.
  - `[low]` `[patch]` `kernel/venue_http.py` dropped the `# type: ignore[attr-defined]` that every former `get_dydx_http_url` importer carried (no `.pyi` entry). Restored.
  - `[low]` `[patch]` `archive_markers.decode` accepted an inverted span or a float/bool integer (truncated), either of which makes a marker protect no row. It now refuses both; parametrised test.
  - `[low]` `[patch]` `market_kind` had become stricter than `common.venues.market_kind` for a dashless symbol (`PERP.X`: `unknown` instead of `perp`), a behaviour change in a move. Old contract restored; `market_suffix`/`bybit_category` stay strict; test.
  - `[low]` `[patch]` The kernel purity guard missed augmented assignments, bindings under `if`/`try`/`with`, class-level containers and memoising caches. All four are now flagged; self-test.
  - `[low]` `[patch]` The venue-HTTP guard missed `from urllib.request import Request [as R]` and a dYdX URL built with `get_dydx_http_url` outside the kernel. Both are now flagged (`ranking_engine` stays on its 25.2 entry); self-test.
  - `[low]` `[patch]` The id-suffix guard missed `endswith((".A", ".B"))`, and `compare_klines.instruments_on_day` still dispatched on a venue suffix through a glob. The guard now reads tuples, and `instruments_on_day` filters leaves with `has_venue`; new test.
  - `[low]` `[patch]` `test_post_move_write_matches_the_pre_move_files` left the zstd patch installed for the rest of the session (order-dependent compression). It is now undone through `monkeypatch`.
  - `[low]` `[patch]` The moved-name `__getattr__` in `catalog_stats`/`archive_gaps` picked its target module with a two-way string comparison, so a third target would resolve wrong. It now uses an explicit module table that raises on a typo; a dynamic `import_module` is avoided because `test_images` rejects non-literal imports.
  - `[low]` `[patch]` The notebook `dydx_catalog_pandas.ipynb` still imported `ml_signals.indicators`, which the `.py`-only shim scan cannot see. Repointed to `kernel.indicators`, prose too.
  - `[low]` `[patch]` Stale citations: `ml_signals.performance_metrics` in `live_paper`/`bot_tui` docstrings; `ARCHITECTURE.md`'s writer→reader paragraph (wrong line numbers, pre-kernel imports); `ml_signals/BACKTESTING.md`; `platform/CLAUDE.md` DATA-05's fold path. All updated.

### 2026-09-22 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 17 (high 0, medium 1, low 16)
- defer: 1 (high 0, medium 0, low 1)
- reject: 4
- addressed_findings:
  - `[medium]` `[patch]` `record_gap` could write an inverted span (a backward wall-clock step between a lost trade's arrival and the flush puts `now` before its `ts_init`); the previous pass's stricter `decode` would then refuse the line and wedge every rebuild of that instrument until the file was hand-edited. The writer now records the ordered span and ledgers `archive_gaps.inverted_span`; `encode` refuses an inverted `ArchiveGap` so the kernel can never produce a line it cannot read; `test_archive_gaps.py` (new) and a kernel test.
  - `[low]` `[patch]` `bybit_url`/`dydx_indexer_url` joined a path without a leading `/` onto the host (`api.bybit.comv5/...`, a registrable domain). Both refuse it (`ValueError`); parametrised test.
  - `[low]` `[patch]` `CatalogFileSpan.from_stem` accepted an inverted stem, so `overlaps`/`covers` answered for a span that can hold no row. It now raises `ValueError` like any other name the catalog did not write; tests for the inverted and the equal-bound stem.
  - `[low]` `[patch]` The skew budget (`_check_skew_budget`, `READ_SPAN_MARGIN_NS` comment, `test_skew_constants`) described the sum of two opposite-direction skews as one row's trailing skew. The sum is kept (the spec's chain, and conservative for the symmetric widening) but the docstrings and the error text now state the two directions and that the sum is a ceiling on either.
  - `[low]` `[patch]` `prune_catalog._leaf_statuses` gained an untested `MalformedInstrumentId` branch (a leaf that is not an id is kept, never pruned). Test added: kept as `unverified`, reported, survives `--apply`.
  - `[low]` `[patch]` The kernel purity guard skipped bindings inside module-level `for`/`while`/`match` bodies and tuple unpacks (`A, B = [], []`). All are now walked; self-test.
  - `[low]` `[patch]` The venue-HTTP guard missed `import urllib.request as ur` / `from urllib import request` followed by `ur.Request(...)`, although its docstring claimed "however imported or aliased". Module aliases are collected; self-test.
  - `[low]` `[patch]` The id-suffix guard missed `'.' + v`, `f'{a}.{v}'` and the unbound `str.endswith(i, '.X')`. All three are read (a separator directly before a formatted part; `f'{stem}.parquet'` stays clean); self-test and the Known limit updated.
  - `[low]` `[patch]` The venue-URL guard ran a regex over raw source, so a comment or docstring citing the venue's documentation would fail it. It now judges string literals (plain and f-string parts) through the AST and skips docstrings; self-test.
  - `[low]` `[patch]` `test_namespace` judged whether a shim's package is shipped by `find_spec` of its top-level name (`common`), which any same-named third-party package satisfies. `_shipped` now looks up the full dotted name and treats an absent parent as not shipped.
  - `[low]` `[patch]` Stale citation: `platform/CLAUDE.md` DATA-06 still named `ARRIVAL_MARGIN_NS`. Re-cited to `kernel.clocks.MAX_TS_INIT_SKEW_NS`.
  - `[low]` `[patch]` User-facing docs still pointed at the shim with dead line numbers: the frontend docs page (`frontend/src/pages/docs/data.ts`, nine refs; `kbData.ts`) and `DATA_INTEGRITY_AUDIT.md` D-40/D-46. Repointed to `kernel/indicators.py` with the current lines; the audit rows keep their history and name the kernel successor.
  - `[low]` `[patch]` `DATA_DICTIONARY.md` did not tell an operator that a malformed or inverted marker line now refuses the instrument's rebuild until hand-fixed. Added to the rebuild step.
  - `[low]` `[patch]` Verbatim-moved docstrings were stale as the kernel's canonical text: `kernel/indicators.py` cited the retired `dashboard.py` twice; `DydxSecondSnapshot` claimed "microstructure signals" and an `ofi` field. Both now describe the raw-inputs contract (SIGNAL-01) and the real consumers.
  - `[low]` `[patch]` Six shims carried the copy-pasted claim that a copied class "would register a second Arrow class", true only of the two `Data` shims. Reworded to the identity/drift reason that holds for all.
  - `[low]` `[patch]` `kernel/__init__.py` claimed no import-time effects; it now names the two sanctioned ones (`register_arrow` per class, the zstd wrapper on `apply_zstd_default()`).
  - `[low]` `[patch]` `kernel/indicators.py:298` carried the RUF002 `–`/`×` from the moved text; ASCII now, so the new file lints clean.

### 2026-09-22 — Review pass (second follow-up, operator-resumed)
- intent_gap: 0
- bad_spec: 0
- patch: 2 (high 0, medium 0, low 2)
- defer: 1 (high 0, medium 0, low 1)
- reject: 5
- addressed_findings:
  - `[low]` `[patch]` Three `kernel/indicators.py` line refs in `frontend/src/pages/docs/data.ts` (`spread`, `mid_price`, `volume_delta`) were off by 8 lines (`:436`/`:445`/`:454` vs. the actual `:444`/`:453`/`:462`) -- a doc-repointing slip from the earlier passes' line-number sweep. Corrected to the current lines.
  - `[low]` `[patch]` `platform/tests/test_boundaries.py`'s `test_kernel_is_pure` walked module-level bindings for mutable state but never looked at a bare `Expr(Call(...))` statement, so a kernel module could add an unsanctioned import-time side effect (network, file, registry mutation) and the guard would never see it -- the two calls that *are* legitimate (`register_arrow` in `second_snapshot.py`/`open_interest.py`) were passing not because they were recognised as sanctioned but because the check didn't look at calls at all. Added `_bare_call_name`/`_SANCTIONED_BARE_CALLS` (only `register_arrow`) to `_state_sites`, so any other bare call at kernel module scope -- including one hidden inside `if`/`try`/`for`/`match`, per the existing recursion -- now fails the purity test; self-test extended.
  - Not addressed here (already covered): the two hunters independently re-surfaced the `CatalogFileSpan`-caller propagation gap (`consolidate_catalog.py:164-165,188`, `compare_klines.py:558`, `kernel/catalog_files.py:63,75,96` -- an inverted-span filename would raise uncaught instead of being skipped) that the **first** follow-up pass already investigated, confirmed pre-existing (the former `catalog_stats._stamp_to_ns` raised at the same sites; `prune_catalog` already caught it alone) and logged to `deferred-work.md`. Re-verified the claim still holds (git-blame + `prune_catalog._parsed_files` still the lone catcher) and left the existing entry untouched per the review step's "do not modify existing entries" rule, rather than duplicating it.
  - `reject` (5): a same-shape "negative `hold_back_seconds`" claim against `collector.py`'s `_check_skew_budget` -- false positive, `collector_core/config.py:134-137` already rejects `hold_back_seconds < 0` at load time, before `_check_skew_budget` ever sees it; the four `CatalogFileSpan`-caller findings above (already deferred, see above).

Verified against this file's own Verification section and Acceptance Criteria: re-ran `kernel/tests`, `test_boundaries.py`, `test_namespace.py`, `test_skew_constants.py` (228 passed, including the new self-test) and the full `make test` list on the host (`python3 -m pytest ... -W default`): **1384 passed, 10 failed** -- the same 10 pre-existing failures as every prior pass (dydx `test_collector_trade_ohlc` x5, `test_ofi_strategy` x3 + consistency x1, `test_rankings` redis x1), no `DeprecationWarning` from `platform/` code. `ruff format --check` + `ruff check` clean on `tests/test_boundaries.py`; `mypy --ignore-missing-imports --disallow-incomplete-defs` clean on it. No dockerfile, schema, payload, directory name or env var changed, so the images and `test_images` result from the earlier passes stand.

### 2026-09-22 — Review pass (fourth, re-driven attempt)

- intent_gap: 0
- bad_spec: 0
- patch: 5 (high 0, medium 1, low 4)
- defer: 1 (high 0, medium 0, low 1)
- reject: 19
- addressed_findings:
  - `[medium]` `[patch]` `live_paper/DEPLOY_CHECKLIST.md`'s "Build & static checks" still told the operator to verify the live-paper image has "ml_signals + live_paper copied in" and to lint `platform/ml_signals/`. This story changed `live_paper.dockerfile` to `COPY platform/kernel` and no `ml_signals`, so a pre-deploy run of the checklist fails on a step that is now wrong. Every other doc was re-cited by the earlier passes; this one was missed. Re-pointed to `kernel` + `observability`.
  - `[low]` `[patch]` `tests/test_skew_constants.py`'s `_hold_back_seconds` recursed `dict.values()` only. `tomllib` parses an array-of-tables into a `list`, so a `hold_back_seconds` under `[[section]]` is skipped and the whole "every skew margin is tied to `MAX_TS_INIT_SKEW_NS`" chain passes vacuously — the guard silently stops guarding. The three venue configs do not use array tables today but `live_paper/config.toml` does (`[[bots]]`), so the shape is live in this project. Lists are walked now, with a self-test.
  - `[low]` `[patch]` `kernel/__init__.py` claimed `platform/tests/test_boundaries.py` "enforces all of it". The purity rule is an AST check: it catches mutable literals, the known mutable factories and unsanctioned bare calls, but cannot see `X = SomeMutableClass()` or state captured in a closure. Restated as a `Known limit:` naming the ceiling and the upgrade path (bind the check to runtime types), matching the honest wording the venue-dispatch rule in the same tree already carries.
  - `[low]` `[patch]` `kernel/catalog_files.py`'s `_OHLC_COLUMNS` became `SecondOHLC._fields` in this story (it was an independent literal list in `catalog_stats.py` at the baseline), and both readers substitute `None`/`0.0` for a column absent from the file — a tolerance for genuinely pre-OHLC files. So renaming a `SecondOHLC` field would make every candle read return all-`None` OHLC and `0.0` volume across the whole catalog, with no error. Two tests now bind the projection to `DydxSecondSnapshot.schema()`'s names and to the column literals `second_ohlc_arrays` reads.
  - `[low]` `[patch]` `kernel/clocks.py`'s `_stamp_ns` read the fractional field positionally with no length check, so a stem carrying fewer than nine digits parsed to a silently wrong instant — while `CatalogFileSpan.from_stem`'s docstring, hardened by the earlier passes, promises `ValueError` for any name the catalog did not write. Verified `unix_nanos_to_iso8601` always emits exactly nine digits and never trims, so the check refuses only names the catalog never wrote; one existing test used a `-0Z` shorthand stem and was corrected to the real nine-digit form.
  - `[defer]` The catalog read helpers list files and then open them in a second step with no guard for a file removed in between, so a `data_api` request overlapping the nightly consolidate/prune 500s instead of skipping one file. Verified pre-existing and moved verbatim (`2d7dd5ab6e:platform/ml_signals/catalog_stats.py:157-166` has the same shape); logged to `deferred-work.md` for 25.1, where the skip-vs-ledger policy belongs.
  - `reject` (19): the two hunters re-surfaced, from no prior context, most of what the first three passes already settled — `bybit_category`'s `inverse` support and its three per-call-site refusals (the *fix* the first pass applied, and what AC3 asks for), `market_kind`'s deliberate dash-less-symbol contract (first-pass patch, with the test that pins it), the `CatalogFileSpan` caller-propagation gap and the stricter marker decode (both already in `deferred-work.md`, left untouched per this step's "do not modify existing entries"), and `record_gap`'s swap-and-ledger, whose docstring already names the backward-wall-clock-step root cause DATA-02 asks for. Also rejected: the shims being callerless (that is MR2's whole design — they are the out-of-tree contract until 24.2), `covers()` and `TwoClocks` being thin (both are spec-mandated members of AC4), `parquet_compat`'s idempotence docstring (its claim is scoped to `apply_zstd_default` calls, which is exactly what it delivers), `second_ohlc_arrays`' file-order precondition and the bare `assert` at its tail (both verbatim pre-existing, and the downstream `argsort` holds today), the day-boundary and symmetric-widening reads (the known, tracked D-31..D-34 attribution issue and a deliberately preserved margin), and four cosmetics (a bare `KeyError` from the URL maps, the three URL builders' argument shapes, a test that re-asserts `MalformedInstrumentId` is a `ValueError` on purpose, and docstring lines over 100 where `E501` is disabled project-wide).

## Design Notes

- **Why the skew coupling test lives in `platform/tests`.** `kernel/tests` belongs to the kernel context, and the kernel imports no context (AD-D2). Asserting capture's and archive's constants from there would itself be an illegal edge. So the test's consumer-side half sits in the cross-cutting guards (the `TESTS` context may import anything), and `kernel/tests/test_clocks.py` holds the kernel-internal half. This is a deliberate refinement of the story text "by a kernel test".
- **Why `_stamp_to_ns` is replaced, not served.** Its successor is `CatalogFileSpan.from_stem` (a whole stem → a span), which has a different shape. Serving a bare stamp parse would keep a second parser alive.
- **`archive_gaps` → `ARCHIVE`.** Once its format lives in the kernel, what remains is archive's file I/O plus a ledger call, which is 25.1's move. It stays inside `collector_core`, so the capture → archive_gaps edges remain intra-package-exempt.
- **Fixture evidence.** The comparison is on Arrow schema (with metadata) plus table content, not raw Parquet bytes, because the writer embeds `created_by` and the pyarrow version. The directory name and the row values are also compared.

## Verification

**Commands:**
- `docker run --rm --network host -v "$REPO":/src:ro -v "$PWD":/work/platform -w /work/platform -e PLATFORM_SOURCE_DIR=/src/platform -e HOME=/tmp -e USER=collector -u 1000:1000 story-23-2/collector:latest python3 -m pytest -o addopts="" --rootdir=. <make test list> -q` -- expected: only the 10 pre-existing failures
- `docker build -f platform/<x>.dockerfile --network host -t story-23-2/<x> .` for collector, data_api and live_paper -- expected: success (never `make up` from the worktree)
- `uvx ruff check` + `uvx ruff format --check` on the changed files -- expected: clean on the changed lines

## Auto Run Result

Status: done

**Summary.** This story extracts `platform/kernel/`, the one shared-kernel package every other `platform/` context may import (AD-D3): `second_snapshot`/`open_interest`/`fold`/`indicators`/`performance_metrics` (moved verbatim from `collector_core`/`ml_signals`), `venues` (the single `InstrumentId` parser, merging three prior copies), `clocks` (`TwoClocks`, `CatalogFileSpan`, and the one `MAX_TS_INIT_SKEW_NS` every other skew margin is now defined against or asserted under), `archive_markers` (the `ArchiveGap` value object plus its encode/decode), `venue_http` (every stdlib venue REST request) and `catalog_files`/`parquet_compat` (read-only catalog helpers, the one zstd `write_table` patch). Every old import path is a pure `DeprecationWarning` re-export shim (`REMOVE_AFTER = "24-2-..."`), every in-repo caller is repointed, and `test_boundaries.py`/`test_namespace.py`/`test_skew_constants.py` mechanically enforce kernel purity, shim identity and expiry, and the skew-constant coupling from here on. No schema, payload, catalog directory name, marker byte format, request bytes, config key, env var or compose service changed in any pass.

**How this attempt ran.** The orchestrator deferred the previous attempt on one ground only: its spec carried `baseline_revision: '7bd64952fd'`, inherited through a cherry-pick, which did not match the baseline recorded for that worktree. The code was never faulted — it had already been through three review passes (12 + 17 + 2 patches). Per the operator's instruction in the story's Dev Notes, this run recorded the worktree's starting commit (`2d7dd5ab6e`), cherry-picked the six preserved commits (clean, reproducing the recorded 143 files / +4293/-1689 exactly), re-stamped the frontmatter, and then **verified rather than re-derived**: a full implementation-verification pass against every task and AC, then a fourth independent review.

**Files changed (this attempt, on top of the cherry-picked diff):**
- `platform/kernel/tests/test_venue_http.py` — dropped an unused `# type: ignore[index]` that `warn_unused_ignores = true` makes a pre-commit mypy failure (the one new mypy finding the story introduced against baseline).
- `platform/live_paper/DEPLOY_CHECKLIST.md` — build/static-check steps re-pointed from `ml_signals` to `kernel` + `observability`, matching the dockerfile this story changed.
- `platform/tests/test_skew_constants.py` — the config walk reads TOML arrays-of-tables, plus a self-test; without it the skew-coupling chain could pass vacuously.
- `platform/kernel/__init__.py` — the purity guard's ceiling stated as a `Known limit:` with upgrade path instead of "enforces all of it".
- `platform/kernel/tests/test_catalog_files.py` — two tests binding `_OHLC_COLUMNS` (now `SecondOHLC._fields`) to the snapshot's Arrow schema and to the literals `second_ohlc_arrays` reads.
- `platform/kernel/clocks.py`, `platform/kernel/tests/test_clocks.py` — `_stamp_ns` refuses a fractional field that is not nine digits (it was read positionally, so a shorter one parsed to a silently wrong instant); one test stem corrected from a `-0Z` shorthand to the real form.

**Review.** Two fresh hunters (adversarial + edge-case), no prior context, over the whole diff since `2d7dd5ab6e`: **5 patched** (1 medium, 4 low), **1 deferred**, **19 rejected**. The rejected set is largely the earlier passes' own findings re-surfaced by reviewers who could not see the triage log — the `bybit_category` inverse support and its three per-call-site refusals, `market_kind`'s deliberate dash-less contract, and two items already in `deferred-work.md` and left untouched per this step's rule. Full reasoning in the triage log above.

**Verification (this attempt):**
- Full `make test` list on the host with `-W default`: **1387 passed**, with the same **10 pre-existing failures** as every prior pass (dydx `test_collector_trade_ohlc` ×5, `test_ofi_strategy` ×3 + consistency ×1, `test_rankings` redis ×1). An 11th, `tests/test_hotpath.py::test_wall_time_per_message_is_within_twice_the_baseline`, appears only when the whole suite runs in one process on this loaded host; it passes alone (5 passed) and passed in-image in the prior pass, so it is contention, not a regression.
- `kernel/tests` + `test_boundaries` + `test_namespace` + `test_skew_constants` + `test_images`: **262 passed**.
- **No `DeprecationWarning` from `platform/` code** under `-W default` (TEST-04).
- All three images (`story-23-2/{collector,data_api,live_paper}`) build from this checkout; the full list re-run inside `story-23-2/collector` gave the identical 1384/10; `make test-live-paper` in `story-23-2/live_paper` gave 404 passed (`test_node.py` deselected — a host-dependent hang proven pre-existing against the baseline tree, owned by Story 25.3).
- `ruff format --check` + `ruff check` (pinned 0.15.16) over the changed files: clean; **zero new findings** against baseline across the 117-file changed set, and `platform/kernel/` is entirely lint-clean. `mypy` 1.20.2 as pre-commit runs it: no error in any changed `platform/` file.
- FORK-01: the diff touches only `platform/`, `_bmad-output/` and the root `CLAUDE.md` (citation updates). Nothing under `nautilus_trader/` or `crates/`.

**Residual risks:**
- The ~157 `ResourceWarning: unclosed database` under `-W default` are the pre-existing candle-store leak already ledgered by Story 23.1's review, unrelated to this move.
- The skew budget is enforced as the sum of both skew directions (the spec's chain), 5 s stricter than the binding per-direction limit; deliberate and documented.
- The shims expire at 24.2: `test_namespace` fails the run once that story is `done` while any shim remains.
- `from_stem`'s inverted-stem and nine-digit refusals and `encode`'s inverted-span refusal are stricter than the code they replaced. No current writer can produce any of them (`unix_nanos_to_iso8601` never trims; the catalog never writes an inverted span), but the pre-rollout check against the live collector's historical `_archive_gaps` files remains the open deferred item from the third pass.
- Two deferred items carry forward to 25.1: the `CatalogFileSpan` caller-propagation policy and the list-then-open race in the read helpers.
