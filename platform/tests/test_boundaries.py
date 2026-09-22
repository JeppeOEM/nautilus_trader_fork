# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  You may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------
"""
The DDD migration's import-boundary guard (spine AD-D1, AD-D2, AD-D16).

Every Python module under `platform/` belongs to exactly one bounded context: a module inside a
context package (`observability/`, later `kernel/`, `candles/`, ...) belongs to it by name, and
every legacy module is mapped to its *target* context by `LEGACY_MODULE_TO_CONTEXT` (longest
dotted prefix wins; an unmapped module fails). A module split across contexts is refined per
symbol by `LEGACY_SYMBOL_TO_CONTEXT`. Each import is judged by the target contexts of both ends,
so the graph holds from the first move, not only once a module has been relocated:

- a cross-context edge must be in `GRAPH` (AD-D2);
- a `_private` name is never imported across contexts;
- `observability` imports only the standard library, and `kernel` no context;
- `research` imports nothing from `data_api`, legacy or not;
- a `domain/` module or a venue `policies.py` imports only the standard library, `kernel` and
  `nautilus_trader.model`/`core`.

Two exemptions only. An edge whose both ends sit in one *unmoved* legacy package (e.g. inside
`collector_core`) is not judged: it becomes judged the moment one end moves out. `platform/tests`
is cross-cutting and may import anything. Every other deviation today is listed in
`LEGACY_EDGES_UNTIL`/`LEGACY_PRIVATE_IMPORTS_UNTIL` with the story that retires it; an entry
fails once that story is `done` on the sprint board, and fails when no import needs it any
more, so both tables can only shrink.
"""

import ast
import sys
from pathlib import Path
from typing import NamedTuple

import pytest
from _source_tree import PLATFORM_DIR
from _source_tree import imports_of
from _source_tree import python_modules
from _source_tree import story_statuses
from _source_tree import unknown_or_done


THIS_STORY = "23-1-observability-context-and-migration-guardrails"

KERNEL = "kernel"
OBSERVABILITY = "observability"
CAPTURE = "capture"
COLLECTION_CONTROL = "collection_control"
ARCHIVE = "archive"
CANDLES = "candles"
RANKING = "ranking"
BOTS = "bots"
ALERTING = "alerting"
RESEARCH = "research"
VIEWS = "views"
DATA_API = "data_api"
BOT_TUI = "bot_tui"
SCRIPTS = "scripts"  # operator surfaces (AD-D1's `platform/scripts/` row)
TESTS = "tests"  # `platform/tests`: the cross-cutting guards themselves

# A top-level package with one of these names *is* that context (a moved or interface package).
CONTEXTS = frozenset(
    {
        KERNEL,
        OBSERVABILITY,
        CAPTURE,
        COLLECTION_CONTROL,
        ARCHIVE,
        CANDLES,
        RANKING,
        BOTS,
        ALERTING,
        RESEARCH,
        VIEWS,
        DATA_API,
        BOT_TUI,
        SCRIPTS,
        TESTS,
    }
)

# AD-D2: every context may import kernel and observability; kernel imports no context and
# observability imports only the standard library (so neither has an outgoing edge). The
# labelled query-service edges are plain edges until those services exist.
_SHARED = frozenset({KERNEL, OBSERVABILITY})
GRAPH: frozenset[tuple[str, str]] = frozenset(
    {(ctx, shared) for ctx in CONTEXTS - _SHARED for shared in _SHARED}
    | {
        (COLLECTION_CONTROL, CAPTURE),
        (ARCHIVE, CANDLES),
        (VIEWS, CANDLES),
        (VIEWS, RANKING),
        (DATA_API, VIEWS),
        (DATA_API, ALERTING),
        (BOT_TUI, VIEWS),
        # An operator harness drives an interface adapter in-process (e.g. `bench_candles`
        # times `/api/candles` through FastAPI's TestClient), exactly as an HTTP client would.
        (SCRIPTS, DATA_API),
    }
)

# Legacy module -> target context (spine AD-D1 "Today" column; AD-D3 for kernel membership).
# Longest dotted prefix wins. A test module belongs to the context of the code it tests, so it
# moves with that code.
LEGACY_MODULE_TO_CONTEXT: dict[str, str] = {
    # --- collector_core: capture, except the kernel/archive/candles modules it still hosts
    "collector_core": CAPTURE,
    "collector_core.archive_gaps": KERNEL,
    "collector_core.backfill_bars": ARCHIVE,
    "collector_core.book_check": CAPTURE,
    "collector_core.build_candles": CANDLES,
    "collector_core.collector": CAPTURE,
    "collector_core.compare_klines": ARCHIVE,
    "collector_core.config": CAPTURE,
    "collector_core.consolidate_catalog": ARCHIVE,
    "collector_core.feed": CAPTURE,
    "collector_core.fold": KERNEL,
    "collector_core.integrity": CAPTURE,
    "collector_core.measure_lag": ARCHIVE,
    "collector_core.migrate_open_interest": ARCHIVE,
    "collector_core.nightly": ARCHIVE,
    "collector_core.open_interest": KERNEL,
    "collector_core.prune_catalog": ARCHIVE,
    "collector_core.rebuild_seconds": ARCHIVE,
    "collector_core.repair_catalog": ARCHIVE,
    "collector_core.second_snapshot": KERNEL,
    "collector_core.trade_backfill": CAPTURE,
    "collector_core.venue_http": KERNEL,
    "collector_core.tests": CAPTURE,
    "collector_core.tests.test_backfill_bars": ARCHIVE,
    "collector_core.tests.test_compare_klines": ARCHIVE,
    "collector_core.tests.test_consolidate_catalog": ARCHIVE,
    "collector_core.tests.test_fold": KERNEL,
    "collector_core.tests.test_measure_lag": ARCHIVE,
    "collector_core.tests.test_migrate_open_interest": ARCHIVE,
    "collector_core.tests.test_nightly": ARCHIVE,
    "collector_core.tests.test_open_interest": KERNEL,
    "collector_core.tests.test_prune_catalog": ARCHIVE,
    "collector_core.tests.test_rebuild_seconds": ARCHIVE,
    # --- venue collectors: capture; dYdX also hosts its control plane and one archive tool.
    # `dydx_collector.collector` stays capture as one module until the control-plane split (25.4).
    "bybit_collector": CAPTURE,
    "hyperliquid_collector": CAPTURE,
    "dydx_collector": CAPTURE,
    "dydx_collector.config": COLLECTION_CONTROL,
    "dydx_collector.normalize_snapshot_schema": ARCHIVE,
    "dydx_collector.open_interest": CAPTURE,
    "dydx_collector.tests.test_build_candles": CANDLES,
    "dydx_collector.tests.test_collector_control": COLLECTION_CONTROL,
    "dydx_collector.tests.test_config": COLLECTION_CONTROL,
    "dydx_collector.tests.test_normalize_snapshot_schema": ARCHIVE,
    "dydx_collector.tests.test_repair_catalog": ARCHIVE,
    # --- common: the one venue-id parser (kernel)
    "common": KERNEL,
    # --- ml_signals: no package default, so a new module there must be placed deliberately
    "ml_signals.__init__": RESEARCH,  # the package itself; see `_context_of`
    "ml_signals.book_features": VIEWS,
    "ml_signals.candle_store": CANDLES,
    "ml_signals.candles": CANDLES,
    "ml_signals.catalog_stats": VIEWS,  # split per symbol below
    "ml_signals.chart_data": VIEWS,
    "ml_signals.chart_indicator_config": VIEWS,
    "ml_signals.chart_indicators": VIEWS,
    "ml_signals.custom_indicators": VIEWS,
    "ml_signals.error_ledger": OBSERVABILITY,  # Story 23.1 shim
    "ml_signals.footprint": VIEWS,
    "ml_signals.indicators": KERNEL,
    "ml_signals.metrics_computer": RANKING,
    "ml_signals.performance_metrics": KERNEL,
    "ml_signals.rank_history": RANKING,
    "ml_signals.ranking_columns": VIEWS,
    "ml_signals.run_backtest": RESEARCH,
    "ml_signals.screener_columns_config": VIEWS,
    "ml_signals.strategies": RESEARCH,
    "ml_signals.venue": KERNEL,
    "ml_signals.watchlist": RESEARCH,
    "ml_signals.tests.__init__": RESEARCH,
    "ml_signals.tests.conftest": RESEARCH,
    "ml_signals.tests.test_ad8_boundary": RESEARCH,
    "ml_signals.tests.test_book_features": VIEWS,
    "ml_signals.tests.test_candle_store": CANDLES,
    "ml_signals.tests.test_candles": CANDLES,
    "ml_signals.tests.test_catalog_stats": VIEWS,
    "ml_signals.tests.test_chart_data": VIEWS,
    "ml_signals.tests.test_chart_indicator_config": VIEWS,
    "ml_signals.tests.test_chart_indicators": VIEWS,
    "ml_signals.tests.test_custom_indicators": VIEWS,
    "ml_signals.tests.test_footprint": VIEWS,
    "ml_signals.tests.test_indicators": KERNEL,
    "ml_signals.tests.test_metrics_computer": RANKING,
    "ml_signals.tests.test_ofi_strategy": RESEARCH,
    "ml_signals.tests.test_ofi_strategy_indicator_consistency": RESEARCH,
    "ml_signals.tests.test_performance_metrics": KERNEL,
    "ml_signals.tests.test_rank_history": RANKING,
    "ml_signals.tests.test_snapshot_backtest_node": RESEARCH,
    "ml_signals.tests.test_snapshot_strategy": RESEARCH,
    "ml_signals.tests.test_timeframe_backtest": RESEARCH,
    "ml_signals.tests.test_venue": KERNEL,
    "ml_signals.tests.test_watchlist": RESEARCH,
    "ml_signals.tests.test_watchlist_multi_coin_backtest": RESEARCH,
    # --- ranking_engine / live_paper: one context each
    "ranking_engine": RANKING,
    "live_paper": BOTS,
    # --- data_api: the interface adapter, hosting alerting and two views modules until they move
    "data_api": DATA_API,
    "data_api.alerts": ALERTING,
    "data_api.routes.alerts": ALERTING,
    "data_api.live_candles": VIEWS,
    "data_api.redis_bus": VIEWS,
    "data_api.tests.test_live_candles": VIEWS,
    # --- bot_tui: the terminal interface adapter
    "bot_tui": BOT_TUI,
}

# Modules split across contexts: (module, top-level name) -> context. Every top-level function and
# class of a split module is listed (asserted), so its move is fully planned.
LEGACY_SYMBOL_TO_CONTEXT: dict[tuple[str, str], str] = {
    # catalog_stats: AD-D1's three-way split, with AD-D3's kernel `catalog_files` read helpers.
    ("ml_signals.catalog_stats", "SecondOHLC"): KERNEL,
    ("ml_signals.catalog_stats", "_stamp_to_ns"): KERNEL,
    ("ml_signals.catalog_stats", "data_file_ranges"): KERNEL,
    ("ml_signals.catalog_stats", "second_ohlc_arrays"): KERNEL,
    ("ml_signals.catalog_stats", "query_second_ohlc"): KERNEL,
    ("ml_signals.catalog_stats", "find_gaps"): ARCHIVE,
    ("ml_signals.catalog_stats", "_overlapping_intervals"): ARCHIVE,
    ("ml_signals.catalog_stats", "_load"): ARCHIVE,
    ("ml_signals.catalog_stats", "likely_outages"): ARCHIVE,
    ("ml_signals.catalog_stats", "coverage"): ARCHIVE,
    ("ml_signals.catalog_stats", "price_series"): RANKING,
    ("ml_signals.catalog_stats", "price_stats_from_series"): RANKING,
    ("ml_signals.catalog_stats", "price_stats"): RANKING,
    ("ml_signals.catalog_stats", "query_second_snapshots"): VIEWS,
    ("ml_signals.catalog_stats", "overview_table"): VIEWS,
    ("ml_signals.catalog_stats", "list_instruments"): VIEWS,
    # dydx open interest: `classify_liquidity` is the collection plan's admission rule.
    ("dydx_collector.open_interest", "classify_liquidity"): COLLECTION_CONTROL,
    ("dydx_collector.open_interest", "_fetch_markets_json"): CAPTURE,
    ("dydx_collector.open_interest", "fetch_open_interest"): CAPTURE,
    ("dydx_collector.open_interest", "parse_open_interest"): CAPTURE,
}

# Cross-context edges the tree still has, (importer context, imported context) -> the story whose
# `done` retires the edge. The sites named are the ones the retiring story removes.
LEGACY_EDGES_UNTIL: dict[tuple[str, str], str] = {
    # archive_gaps (the kernel marker format) ledgers its own read failures; the kernel holds no
    # ledger call once `archive_markers` is pure encode/decode (AD-D3).
    (KERNEL, OBSERVABILITY): "23-2-kernel-shared-kernel",
    # collector.py's direct candle_store calls (and the capture tests reading the store back);
    # capture reaches candles only through the SecondSink port.
    (CAPTURE, CANDLES): "24-1-candles-context-behind-the-secondsink-port",
    # data_api routes (and their tests) read candle_store / ml_signals.candles directly instead
    # of through views' query services.
    (DATA_API, CANDLES): "24-2-views-read-models-and-reader-side-revalidation-removed",
    # data_api reads ranking_engine.metrics_store instead of the ranking query service via views.
    (DATA_API, RANKING): "24-2-views-read-models-and-reader-side-revalidation-removed",
    # data_api/live_candles (and its test) read data_api.settings for the catalog/candle paths.
    (VIEWS, DATA_API): "24-2-views-read-models-and-reader-side-revalidation-removed",
    # data_api/alerts.py uses data_api.redis_bus's queue helpers; AlertEngine becomes a
    # BarObserver wired by the composition root, with no alerting -> views import.
    (ALERTING, VIEWS): "24-3-alerting-context-as-forming-bar-observer",
    # dYdX `_prune_loop` calls `prune_catalog.prune_instrument`; archive's RetentionPolicy becomes
    # the only code that deletes a catalog file.
    (CAPTURE, ARCHIVE): "25-1-archive-context-archiveday-one-deleter-one-rewriter",
    # repair_catalog (and the archive tests) read `catalog_stats.query_second_snapshots`, a views
    # read; archive reads through kernel.catalog_files.
    (ARCHIVE, VIEWS): "25-1-archive-context-archiveday-one-deleter-one-rewriter",
    # The capture tests read their own written snapshots back with `query_second_snapshots`
    # (a views read); they move with capture into capture/tests.
    (CAPTURE, VIEWS): "26-2-capture-package-and-venue-packages-with-entrypoints",
}

# Cross-context imports of a `_private` name: (importing module, "module._name") -> story.
_STAMP = "ml_signals.catalog_stats._stamp_to_ns"
_KERNEL_STORY = "23-2-kernel-shared-kernel"
_VIEWS_STORY = "24-2-views-read-models-and-reader-side-revalidation-removed"
LEGACY_PRIVATE_IMPORTS_UNTIL: dict[tuple[str, str], str] = {
    # The catalog file-stem parse, public in kernel.clocks as `CatalogFileSpan` (23.2).
    ("collector_core.build_candles", _STAMP): _KERNEL_STORY,
    ("collector_core.collector", _STAMP): _KERNEL_STORY,
    ("collector_core.compare_klines", _STAMP): _KERNEL_STORY,
    ("collector_core.consolidate_catalog", _STAMP): _KERNEL_STORY,
    ("collector_core.prune_catalog", _STAMP): _KERNEL_STORY,
    ("collector_core.rebuild_seconds", _STAMP): _KERNEL_STORY,
    ("collector_core.tests.test_collector", _STAMP): _KERNEL_STORY,
    ("collector_core.tests.test_prune_catalog", _STAMP): _KERNEL_STORY,
    # data_api route tests seed a candle store with the candle tests' private row helpers; they
    # go when data_api stops touching the candle store directly (views move).
    ("data_api.tests.test_candles", "ml_signals.tests.test_candle_store._DAY0_MS"): _VIEWS_STORY,
    ("data_api.tests.test_candles", "ml_signals.tests.test_candle_store._second"): _VIEWS_STORY,
    (
        "data_api.tests.test_screener_columns",
        "ml_signals.tests.test_candle_store._second",
    ): _VIEWS_STORY,
}


class Import(NamedTuple):
    src: str  # importing module
    src_ctx: str
    dst: str  # imported module
    name: str | None  # imported name, None for the module itself
    dst_ctx: str
    line: int


def _context_of(module: str) -> str | None:
    """
    Target context of an in-repo module; None when it is unmapped. The legacy map wins (an
    interface package such as `data_api` still hosts other contexts' modules); otherwise a
    module inside a context package belongs to that context.
    """
    ctx = LEGACY_MODULE_TO_CONTEXT.get(f"{module}.__init__") if module in _PACKAGE_INITS else None
    ctx = ctx or _longest(module)
    top = module.split(".")[0]
    if ctx is None and top in CONTEXTS:
        return top
    return ctx


def _longest(module: str) -> str | None:
    parts = module.split(".")
    for cut in range(len(parts), 0, -1):
        ctx = LEGACY_MODULE_TO_CONTEXT.get(".".join(parts[:cut]))
        if ctx is not None:
            return ctx
    return None


def _symbol_context(module: str, name: str | None) -> str | None:
    if name is not None and (module, name) in LEGACY_SYMBOL_TO_CONTEXT:
        return LEGACY_SYMBOL_TO_CONTEXT[(module, name)]
    return _context_of(module)


_MODULES = python_modules()
_KNOWN = set(_MODULES)
_PACKAGE_INITS = {name for name, path in _MODULES.items() if path.name == "__init__.py"}
# The legacy packages: every top-level package that is not itself a context. `data_api` and
# `bot_tui` keep their names (interface adapters) and are judged like any context package.
LEGACY_PACKAGES = frozenset(
    {"collector_core", "dydx_collector", "bybit_collector", "hyperliquid_collector", "common"}
    | {"ml_signals", "ranking_engine", "live_paper"}
)


def _in_repo(target: str) -> str | None:
    """Return the in-repo module an import target names (itself or its longest known prefix)."""
    parts = target.split(".")
    for cut in range(len(parts), 0, -1):
        candidate = ".".join(parts[:cut])
        if candidate in _KNOWN:
            return candidate
    return None


def _all_imports() -> list[Import]:
    found: list[Import] = []
    for module, path in _MODULES.items():
        src_ctx = _context_of(module) or "?"
        for ref in imports_of(module, path, _KNOWN):
            dst = _in_repo(ref.target)
            if dst is None:
                continue
            dst_ctx = _symbol_context(dst, ref.name) or "?"
            found.append(Import(module, src_ctx, dst, ref.name, dst_ctx, ref.line))
    return found


_IMPORTS = _all_imports()


def _exempt(imp: Import) -> bool:
    """Within one unmoved legacy package, or from the cross-cutting `platform/tests`."""
    src_top, dst_top = imp.src.split(".")[0], imp.dst.split(".")[0]
    return imp.src_ctx == TESTS or (src_top == dst_top and src_top in LEGACY_PACKAGES)


def _judged() -> list[Import]:
    return [imp for imp in _IMPORTS if imp.src_ctx != imp.dst_ctx and not _exempt(imp)]


def _site(imp: Import) -> str:
    what = f"{imp.dst}.{imp.name}" if imp.name else imp.dst
    return f"{_MODULES[imp.src].relative_to(PLATFORM_DIR)}:{imp.line} -> {what}"


def _is_private(imp: Import) -> bool:
    return imp.name is not None and imp.name.startswith("_") and not imp.name.startswith("__")


def _private_key(imp: Import) -> tuple[str, str]:
    return (imp.src, f"{imp.dst}.{imp.name}")


def test_every_module_is_mapped_to_a_context() -> None:
    unmapped = sorted(module for module in _MODULES if _context_of(module) is None)
    assert unmapped == [], (
        "modules with no target context: add each to LEGACY_MODULE_TO_CONTEXT (spine AD-D1) or "
        "move it into its context package"
    )


def test_every_map_entry_names_a_real_module_and_context() -> None:
    stale = sorted(
        prefix
        for prefix in LEGACY_MODULE_TO_CONTEXT
        if prefix.removesuffix(".__init__") not in _KNOWN
        and not any(m.startswith(prefix + ".") for m in _KNOWN)
    )
    assert stale == [], "map entries naming no module: delete them"
    assert set(LEGACY_MODULE_TO_CONTEXT.values()) <= CONTEXTS
    assert set(LEGACY_SYMBOL_TO_CONTEXT.values()) <= CONTEXTS


def _top_level_definitions(module: str) -> set[str]:
    tree = ast.parse(_MODULES[module].read_text())
    kinds = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    return {node.name for node in tree.body if isinstance(node, kinds)}


def test_split_modules_map_every_function_and_class() -> None:
    split = {module for module, _ in LEGACY_SYMBOL_TO_CONTEXT}
    for module in sorted(split):
        mapped = {name for owner, name in LEGACY_SYMBOL_TO_CONTEXT if owner == module}
        missing = sorted(_top_level_definitions(module) - mapped)
        unknown = sorted(mapped - _top_level_definitions(module))
        assert missing == [], f"{module}: place each in LEGACY_SYMBOL_TO_CONTEXT"
        assert unknown == [], f"{module}: these names no longer exist, delete their entries"


def test_cross_context_edges_follow_the_graph() -> None:
    illegal = sorted(
        _site(imp) + f"  [{imp.src_ctx} -> {imp.dst_ctx}]"
        for imp in _judged()
        if (imp.src_ctx, imp.dst_ctx) not in GRAPH
        and (imp.src_ctx, imp.dst_ctx) not in LEGACY_EDGES_UNTIL
    )
    assert illegal == [], "edges outside spine AD-D2's graph (fix the import, never the table)"


def test_no_private_name_crosses_a_context() -> None:
    crossing = sorted(
        _site(imp) + f"  [{imp.src_ctx} -> {imp.dst_ctx}]"
        for imp in _judged()
        if _is_private(imp) and _private_key(imp) not in LEGACY_PRIVATE_IMPORTS_UNTIL
    )
    assert crossing == [], "a `_private` name imported across contexts: make it public in its owner"


def test_every_legacy_entry_is_still_needed() -> None:
    judged = _judged()
    used_edges = {
        (imp.src_ctx, imp.dst_ctx) for imp in judged if (imp.src_ctx, imp.dst_ctx) not in GRAPH
    }
    used_private = {_private_key(imp) for imp in judged if _is_private(imp)}
    assert sorted(set(LEGACY_EDGES_UNTIL) - used_edges) == [], "no import needs these: delete them"
    assert sorted(set(LEGACY_PRIVATE_IMPORTS_UNTIL) - used_private) == [], (
        "no import needs these: delete them"
    )


def _story_order(key: str) -> tuple[int, int]:
    epic, story = key.split("-")[:2]
    return (int(epic), int(story))


_LEGACY_STORIES = sorted(
    set(LEGACY_EDGES_UNTIL.values()) | set(LEGACY_PRIVATE_IMPORTS_UNTIL.values())
)


@pytest.mark.parametrize("story", _LEGACY_STORIES)
def test_legacy_entries_expire_with_their_story(story: str) -> None:
    reason = unknown_or_done(story, story_statuses())
    entries = sorted(
        str(key)
        for table in (LEGACY_EDGES_UNTIL, LEGACY_PRIVATE_IMPORTS_UNTIL)
        for key, until in table.items()
        if until == story
    )
    assert reason is None, f"{reason}: retire {entries} (fix the imports, then delete the entries)"
    assert _story_order(story) > _story_order(THIS_STORY), f"{story} is not a later story"


def test_graph_gives_kernel_and_observability_no_outgoing_edge() -> None:
    assert {edge for edge in GRAPH if edge[0] in _SHARED} == set()
    assert {edge for edge in LEGACY_EDGES_UNTIL if edge[0] == OBSERVABILITY} == set()


def _stdlib(target: str) -> bool:
    return target.split(".")[0] in sys.stdlib_module_names


def test_observability_imports_only_the_standard_library() -> None:
    foreign = sorted(
        f"{module}:{ref.line} -> {ref.target}"
        for module, path in _MODULES.items()
        if _context_of(module) == OBSERVABILITY
        and ".tests" not in f".{module}"
        and module != "ml_signals.error_ledger"  # the shim re-exports observability; judged above
        for ref in imports_of(module, path, _KNOWN)
        if not _stdlib(ref.target) and _context_of(_in_repo(ref.target) or "") != OBSERVABILITY
    )
    assert foreign == [], "observability/ imports only the standard library (spine AD-D16)"


# Venue tokens: an exchange's name, or a venue suffix of a Nautilus InstrumentId.
_VENUE_TOKENS = ("dydx", "bybit", "hyperliquid")


def test_observability_holds_no_venue_token() -> None:
    """The incident handler takes its id pattern, title and raw-log location from the entrypoint."""
    tainted = sorted(
        f"{path.relative_to(PLATFORM_DIR)}: {token}"
        for module, path in _MODULES.items()
        if module.split(".")[0] == OBSERVABILITY and ".tests" not in f".{module}"
        for token in _VENUE_TOKENS
        if token in path.read_text().lower()
    )
    assert tainted == [], "observability/ is generic (spine AD-D16): pass venue specifics in"


def test_research_imports_nothing_from_data_api() -> None:
    reaching = sorted(
        _site(imp)
        for imp in _IMPORTS
        if imp.src_ctx == RESEARCH
        and (imp.dst_ctx == DATA_API or imp.dst.split(".")[0] == DATA_API)
    )
    assert reaching == [], "research reads rankings over HTTP, never by importing data_api"
    assert (RESEARCH, DATA_API) not in LEGACY_EDGES_UNTIL


# nautilus_trader's value types are domain-safe; its runtime, persistence and adapters are not.
_DOMAIN_SAFE_EXTERNAL = ("nautilus_trader.model", "nautilus_trader.core")


def _is_domain_module(module: str) -> bool:
    parts = module.split(".")
    policies = len(parts) == 4 and parts[1] == "venues" and parts[3] == "policies"
    return "domain" in parts[1:] or policies


def _domain_violations(module: str, targets: list[str]) -> list[str]:
    """Return what a domain module imports beyond the stdlib, `kernel` and Nautilus value types."""
    bad = []
    for target in targets:
        in_repo = _in_repo(target)
        if in_repo is not None:
            ctx = _context_of(in_repo)
            if ctx != KERNEL and not (ctx == module.split(".")[0] and _is_domain_module(in_repo)):
                bad.append(target)
        elif not _stdlib(target) and not target.startswith(_DOMAIN_SAFE_EXTERNAL):
            bad.append(target)
    return bad


def test_domain_and_policy_modules_import_only_stdlib_kernel_and_value_types() -> None:
    offending = {
        module: _domain_violations(module, [ref.target for ref in imports_of(module, path, _KNOWN)])
        for module, path in _MODULES.items()
        if _is_domain_module(module)
    }
    assert {m: v for m, v in offending.items() if v} == {}, "domain code stays pure (AD-D2)"


def test_domain_rule_recognises_policy_files_and_foreign_imports() -> None:
    assert _is_domain_module("capture.venues.dydx.policies")
    assert _is_domain_module("capture.domain.live_book")
    assert not _is_domain_module("capture.venues.dydx.client")
    assert _domain_violations(
        "capture.venues.dydx.policies",
        ["dataclasses", "nautilus_trader.model.data", "asyncio", "redis", "nautilus_trader.live"],
    ) == ["redis", "nautilus_trader.live"]


def test_every_import_resolves_to_a_mapped_context() -> None:
    """A split module's symbol or an import target the map cannot place is a map gap."""
    unplaced = sorted(_site(imp) for imp in _IMPORTS if "?" in (imp.src_ctx, imp.dst_ctx))
    assert unplaced == []


def test_checker_flags_unmapped_modules_and_expired_stories() -> None:
    assert _context_of("ml_signals.a_module_nobody_placed") is None
    assert _context_of("observability.anything") == OBSERVABILITY
    assert _context_of("data_api.alerts") == ALERTING
    board = {"24-1-x": "done", "24-2-y": "ready-for-dev", "24-3-z": "superseded"}
    assert unknown_or_done("24-1-x", board) is not None
    assert unknown_or_done("24-9-typo", board) is not None
    assert unknown_or_done("24-3-z", board) is not None
    assert unknown_or_done("24-2-y", board) is None


def test_tree_walk_rejects_a_module_and_a_package_of_one_name(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b.py").write_text("")
    (tmp_path / "a" / "b").mkdir()
    (tmp_path / "a" / "b" / "__init__.py").write_text("")
    with pytest.raises(RuntimeError, match=r"both module a\.b"):
        python_modules(tmp_path)


def test_exemption_covers_only_one_unmoved_package_and_platform_tests() -> None:
    inside = Import("collector_core.collector", CAPTURE, "collector_core.nightly", "x", ARCHIVE, 1)
    across = Import("collector_core.collector", CAPTURE, "ml_signals.candles", "x", CANDLES, 1)
    interface = Import("data_api.alerts", ALERTING, "data_api.redis_bus", "x", VIEWS, 1)
    guard = Import("tests.test_x", TESTS, "ml_signals.candles", "_x", CANDLES, 1)
    assert _exempt(inside)
    assert not _exempt(across)
    assert not _exempt(interface)
    assert _exempt(guard)
