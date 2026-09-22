---
title: 'Story 23.2: kernel/ shared kernel'
type: 'refactor'
created: '2026-09-22'
status: 'in-review'
baseline_revision: '7bd64952fd'
review_loop_iteration: 0
followup_review_recommended: true
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
