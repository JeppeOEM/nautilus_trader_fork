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
  `nautilus_trader.model`/`core`;
- `kernel/` holds exactly spine AD-D3's modules and stays pure: no in-repo import beyond itself,
  no store, no config loader, no module-level mutable state (Story 23.2);
- every venue REST URL and request lives in `kernel.venue_http`, and every venue dispatch on an
  instrument id's suffix goes through `kernel.venues` (Story 23.2).

Two exemptions only. An edge whose both ends sit in one *unmoved* legacy package (e.g. inside
`collector_core`) is not judged: it becomes judged the moment one end moves out. `platform/tests`
is cross-cutting and may import anything. Every other deviation today is listed in
`LEGACY_EDGES_UNTIL`/`LEGACY_PRIVATE_IMPORTS_UNTIL` with the story that retires it; an entry
fails once that story is `done` on the sprint board, and fails when no import needs it any
more, so both tables can only shrink.
"""

import ast
import re
import sys
from pathlib import Path
from typing import NamedTuple

import pytest
from _source_tree import PLATFORM_DIR
from _source_tree import imports_of
from _source_tree import python_modules
from _source_tree import story_statuses
from _source_tree import unknown_or_done


THIS_STORY = "23-2-kernel-shared-kernel"

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
    # --- collector_core: capture, except the kernel shims and archive/candles modules it still hosts
    "collector_core": CAPTURE,
    # the marker file I/O over `kernel.archive_markers` (archive's, Story 25.1)
    "collector_core.archive_gaps": ARCHIVE,
    "collector_core.backfill_bars": ARCHIVE,
    "collector_core.book_check": CAPTURE,
    "collector_core.build_candles": CANDLES,
    "collector_core.collector": CAPTURE,
    "collector_core.compare_klines": ARCHIVE,
    "collector_core.config": CAPTURE,
    "collector_core.consolidate_catalog": ARCHIVE,
    # Story 23.3; moves with archive in 25.1.
    "collector_core.crosscheck_errors": ARCHIVE,
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
    "collector_core.tests.test_crosscheck_errors": ARCHIVE,
    "collector_core.tests.test_measure_lag": ARCHIVE,
    "collector_core.tests.test_migrate_open_interest": ARCHIVE,
    "collector_core.tests.test_nightly": ARCHIVE,
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
    # --- common: a shim package over the one venue-id parser (kernel.venues, Story 23.2)
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
    "ml_signals.tests.test_metrics_computer": RANKING,
    "ml_signals.tests.test_ofi_strategy": RESEARCH,
    "ml_signals.tests.test_ofi_strategy_indicator_consistency": RESEARCH,
    "ml_signals.tests.test_rank_history": RANKING,
    "ml_signals.tests.test_snapshot_backtest_node": RESEARCH,
    "ml_signals.tests.test_snapshot_strategy": RESEARCH,
    "ml_signals.tests.test_timeframe_backtest": RESEARCH,
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
    # catalog_stats: AD-D1's three-way split (its kernel read helpers moved in Story 23.2; the
    # module `__getattr__` only serves those moved names, with a DeprecationWarning).
    ("ml_signals.catalog_stats", "__getattr__"): KERNEL,
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
_VIEWS_STORY = "24-2-views-read-models-and-reader-side-revalidation-removed"
LEGACY_PRIVATE_IMPORTS_UNTIL: dict[tuple[str, str], str] = {
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


# --- the shared kernel (spine AD-D3, Story 23.2) --------------------------------------------------

KERNEL_MODULES = frozenset(
    {
        "__init__",
        "archive_markers",
        "catalog_files",
        "clocks",
        "fold",
        "indicators",
        "open_interest",
        "parquet_compat",
        "performance_metrics",
        "second_snapshot",
        "venue_http",
        "venues",
    }
)


def _kernel_sources() -> dict[str, Path]:
    """Return the kernel's own (non-test) modules."""
    return {
        module: path
        for module, path in _MODULES.items()
        if module.split(".")[0] == KERNEL and ".tests" not in f".{module}"
    }


def test_kernel_holds_exactly_the_ad_d3_modules() -> None:
    found = {path.stem for path in _kernel_sources().values()}
    assert found == KERNEL_MODULES, "kernel membership is AD-D3's list, nothing more (no grab-bag)"
    assert all(path.parent == PLATFORM_DIR / KERNEL for path in _kernel_sources().values())


# A store or a config loader never enters the kernel (AD-D3): these imports are how one would.
_KERNEL_FORBIDDEN_IMPORTS = ("sqlite3", "redis", "tomllib", "shelve", "dbm", "observability")
# Calls whose result at module level is mutable runtime state.
_MUTABLE_FACTORIES = frozenset(
    {"dict", "list", "set", "defaultdict", "deque", "OrderedDict", "Counter", "bytearray"}
)
_MUTABLE_LITERALS = (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp, ast.SetComp)


_CACHE_DECORATORS = frozenset({"cache", "lru_cache", "cached_property"})
# The kernel's own two sanctioned import-time effects are `register_arrow` (once per `Data`
# class) and `apply_zstd_default()` -- but the latter is only ever *defined* here, never
# *called* here (its callers are `collector.py`/`backfill_bars.py`), so at kernel module scope
# the only bare call an import may legitimately run is `register_arrow`.
_SANCTIONED_BARE_CALLS = frozenset({"register_arrow"})
# A call whose *result* is bound to a module-level name is an import-time effect too (`_C =
# Session()`, `_D = open(...).read()`), so it is judged by the same rule. These three are the
# kernel's frozen-table and name-derivation builders: pure, no I/O, no registry mutation.
_SANCTIONED_VALUE_CALLS = frozenset({"MappingProxyType", "frozenset", "class_to_filename"})


def _call_name(call: ast.expr) -> str | None:
    callee = call.func if isinstance(call, ast.Call) else None
    return getattr(callee, "id", None) or getattr(callee, "attr", None)


def _bare_call_name(node: ast.stmt) -> str | None:
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        return _call_name(node.value)
    return None


def _bound_call_name(node: ast.stmt) -> str | None:
    """Return the callee of `NAME = f(...)`: an import-time effect whose result is kept."""
    if isinstance(node, ast.Assign | ast.AnnAssign) and isinstance(node.value, ast.Call):
        return _call_name(node.value)
    return None


def _is_mutable_value(value: ast.expr | None) -> bool:
    if isinstance(value, ast.Tuple | ast.List) and any(map(_is_mutable_value, value.elts)):
        return True  # `A, B = [], []` and `X = ({},)`
    return _is_mutable_scalar(value)


def _is_mutable_scalar(value: ast.expr | None) -> bool:
    callee = value.func if isinstance(value, ast.Call) else None
    name = getattr(callee, "id", None) or getattr(callee, "attr", None)
    return isinstance(value, _MUTABLE_LITERALS) or name in _MUTABLE_FACTORIES


def _decorator_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for decorator in getattr(node, "decorator_list", []):
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        name = getattr(target, "id", None) or getattr(target, "attr", None)
        if isinstance(name, str):
            names.add(name)
    return names


_COMPOUND = (ast.If, ast.Try, ast.With, ast.AsyncWith, ast.For, ast.AsyncFor, ast.While)


def _import_time_call(node: ast.stmt) -> str | None:
    """Return the callee of an unsanctioned import-time call, bare or bound to a name."""
    if (bare := _bare_call_name(node)) is not None:
        return None if bare in _SANCTIONED_BARE_CALLS else bare
    bound = _bound_call_name(node)
    sanctioned = _SANCTIONED_VALUE_CALLS | _SANCTIONED_BARE_CALLS
    return None if bound is None or bound in sanctioned else bound


def _own_state_site(node: ast.stmt) -> str | None:
    """Return this statement's own impurity label, ignoring anything nested inside it."""
    if isinstance(node, ast.AugAssign):
        return f"line {node.lineno} (augmented assignment)"
    if isinstance(node, ast.Assign | ast.AnnAssign) and _is_mutable_value(node.value):
        return f"line {node.lineno}"
    if (callee := _import_time_call(node)) is not None:
        return f"line {node.lineno} (unsanctioned import-time call: {callee})"
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        return (
            f"line {node.lineno} (memoising cache)"
            if _decorator_names(node) & _CACHE_DECORATORS
            else None
        )
    return None


def _nested_statements(node: ast.stmt) -> list[ast.stmt]:
    """Return statements a class or compound statement still runs at import time."""
    if isinstance(node, ast.ClassDef):
        return list(node.body)
    if isinstance(node, _COMPOUND):
        nested = [*node.body, *getattr(node, "orelse", []), *getattr(node, "finalbody", [])]
        return nested + [stmt for handler in getattr(node, "handlers", []) for stmt in handler.body]
    if isinstance(node, ast.Match):
        return [stmt for case in node.cases for stmt in case.body]
    return []


def _state_sites(statements: list[ast.stmt]) -> list[str]:
    """Return module- or class-level mutable bindings, including ones under `if`/`try`/`with`."""
    found = []
    for node in statements:
        site = _own_state_site(node)
        if site is not None:
            found.append(site)
        else:
            found += _state_sites(_nested_statements(node))
    return found


_ENV_NAMES = frozenset({"environ", "getenv"})


def _env_aliases(tree: ast.AST) -> set[str]:
    """Return local names bound to `os.environ`/`os.getenv` by a `from os import ... as ...`."""
    return {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "os"
        for alias in node.names
        if alias.name in _ENV_NAMES
    }


def _mutable_module_state(tree: ast.Module) -> list[str]:
    """Bindings to mutable state at module or class level (tables must be frozen)."""
    return _state_sites(tree.body)


def _kernel_impurities(module: str, path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    bad = [f"{module}: mutable module state at {site}" for site in _mutable_module_state(tree)]
    env_names = _ENV_NAMES | _env_aliases(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            bad.append(f"{module}:{node.lineno}: `global` statement")
        name = node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", None)
        if isinstance(node, ast.Attribute | ast.Name) and name in env_names:
            bad.append(f"{module}:{node.lineno}: reads the environment (a config loader)")
    for ref in imports_of(module, path, _KNOWN):
        in_repo = _in_repo(ref.target)
        if in_repo is not None and in_repo.split(".")[0] != KERNEL:
            bad.append(f"{module}:{ref.line}: imports context module {ref.target}")
        elif ref.target.split(".")[0] in _KERNEL_FORBIDDEN_IMPORTS:
            bad.append(f"{module}:{ref.line}: imports {ref.target} (a store/loader/ledger)")
    return bad


def test_kernel_is_pure() -> None:
    impure = sorted(
        entry
        for module, path in _kernel_sources().items()
        for entry in _kernel_impurities(module, path)
    )
    assert impure == [], "the kernel holds no state, store, config or context import (AD-D3)"


def test_kernel_purity_rule_catches_each_kind() -> None:
    tree = ast.parse("import types\nA = {}\nB = list()\nC = types.MappingProxyType({})\nD = (1,)\n")
    assert _mutable_module_state(tree) == ["line 2", "line 3"]
    hidden = ast.parse(
        "import functools\nif True:\n    E = {}\nN = 0\nN += 1\n"
        "class K:\n    seen = []\n"
        "@functools.lru_cache\ndef f(): pass\n"
    )
    assert _mutable_module_state(hidden) == [
        "line 3",
        "line 5 (augmented assignment)",
        "line 7",
        "line 9 (memoising cache)",
    ]
    looped = ast.parse(
        "for _ in ():\n    F = {}\nwhile False:\n    G = []\nelse:\n    H = set()\n"
        "match 1:\n    case 1:\n        I = dict()\nJ, K = [], ()\nL = ({},)\nM = (1, (2,))\n"
    )
    assert _mutable_module_state(looped) == [
        "line 2",
        "line 4",
        "line 6",
        "line 9",
        "line 10",
        "line 11",
    ]
    called = ast.parse("register_arrow(X)\nsome_side_effect()\nif True:\n    another_one()\n")
    assert _mutable_module_state(called) == [
        "line 2 (unsanctioned import-time call: some_side_effect)",
        "line 4 (unsanctioned import-time call: another_one)",
    ]
    bound = ast.parse(
        "T = MappingProxyType({})\nF = frozenset({1})\nN = class_to_filename(C)\n"
        "S: Session = Session()\nD = open('f').read()\n"
    )
    assert _mutable_module_state(bound) == [
        "line 4 (unsanctioned import-time call: Session)",
        "line 5 (unsanctioned import-time call: read)",
    ]


def test_kernel_purity_rule_follows_an_environ_alias() -> None:
    """`from os import environ as E` is the same config loader as `os.environ`."""
    assert _env_aliases(ast.parse("from os import environ as E\nfrom os import getenv\n")) == {
        "E",
        "getenv",
    }
    assert _env_aliases(ast.parse("from typing import environ\n")) == set()


# Venue REST: every URL and request is built in `kernel.venue_http` (AD-D3). `ranking_engine`'s
# own volume polls keep their duplicate maps until its move -- the sites that story removes.
LEGACY_VENUE_HTTP_UNTIL: dict[str, str] = {
    "ranking_engine.engine": "25-2-ranking-context-rankingboard-replaces-module-globals",
}
# HTTP clients that talk to no venue, so `kernel.venue_http` does not own them.
NON_VENUE_HTTP_CLIENTS: dict[str, str] = {
    "observability.notify": "ntfy / Telegram / webhook alert transport",
    "ml_signals.rank_history": "the local data_api HTTP API",
    "ml_signals.watchlist": "the local data_api HTTP API",
}
_VENUE_URL = re.compile(r"https?://[^\s\"']*(?:dydx|bybit|hyperliquid)", re.IGNORECASE)


def _docstrings(tree: ast.AST) -> set[int]:
    """`id()` of every docstring constant: a citation of the venue's docs is not a request."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            head = node.body[0] if node.body else None
            if isinstance(head, ast.Expr) and isinstance(head.value, ast.Constant):
                found.add(id(head.value))
    return found


def _string_expr_text(node: ast.AST) -> str | None:
    """Return a string expression's literal text: a constant, an f-string, or a `+` chain."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        # A formatted part contributes nothing readable, but joining the literal parts keeps a
        # URL split across one (`f"https://api.{env}.bybit.com"`) matchable.
        parts = (v.value for v in node.values if isinstance(v, ast.Constant))
        return "".join(part for part in parts if isinstance(part, str))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _string_expr_text(node.left), _string_expr_text(node.right)
        return None if left is None or right is None else left + right
    return None


def _venue_url_literals(tree: ast.AST) -> list[int]:
    """Lines whose string expression holds a venue URL, however split; not comments/docstrings."""
    docs = _docstrings(tree)
    lines = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.expr) or id(node) in docs:
            continue
        text = _string_expr_text(node)
        if text is not None and _VENUE_URL.search(text):
            lines.add(node.lineno)
    return sorted(lines)


def test_venue_url_rule_reads_literals_not_comments_or_docstrings() -> None:
    tree = ast.parse(
        '"""See https://docs.dydx.trade/x for the wire format."""\n'
        "# https://api.bybit.com/v5 is the base\n"
        "def f():\n    'https://api.hyperliquid.xyz/info in a docstring'\n"
        "    a = 'https://api.bybit.com/v5'\n    b = f'https://indexer.dydx.trade/{a}'\n"
        "    return 'https://example.com/none'\n"
    )
    assert _venue_url_literals(tree) == [5, 6]


def test_venue_url_rule_reads_a_url_split_across_parts() -> None:
    """A URL assembled from an f-string hole or a `+` chain is still one venue URL."""
    tree = ast.parse(
        "env = 'api'\n"
        "a = f'https://{env}.bybit.com/v5'\n"
        "b = 'https://api.' + 'dydx.trade/v4'\n"
        "c = 'https://example.' + 'com/none'\n"
    )
    assert _venue_url_literals(tree) == [2, 3]


def _judged_sources() -> dict[str, Path]:
    """Production modules outside the kernel (tests may hold URLs and ids as fixtures)."""
    return {
        module: path
        for module, path in _MODULES.items()
        if module.split(".")[0] != KERNEL
        and ".tests" not in f".{module}"
        and module.split(".")[0] != TESTS
    }


_URLLIB_REQUEST_NAMES = frozenset({"Request", "urlopen"})


def _urllib_aliases(tree: ast.AST) -> set[str]:
    """Local names bound to `urllib.request.Request`/`urlopen` by a `from` import (any alias)."""
    return {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "urllib.request"
        for alias in node.names
        if alias.name in _URLLIB_REQUEST_NAMES
    }


def _urllib_module_aliases(tree: ast.AST) -> set[str]:
    """Local names bound to the `urllib.request` module (`import ... as`, `from urllib import`)."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.asname for a in node.names if a.name == "urllib.request" and a.asname}
        elif isinstance(node, ast.ImportFrom) and node.module == "urllib":
            found |= {a.asname or a.name for a in node.names if a.name == "request"}
    return found


def _urllib_calls(tree: ast.AST) -> list[int]:
    """Lines calling `urllib.request.Request`/`urlopen` (however imported or aliased)."""
    aliases, modules = _urllib_aliases(tree), _urllib_module_aliases(tree)
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in aliases:
            lines.append(node.lineno)
        elif isinstance(func, ast.Attribute) and func.attr in _URLLIB_REQUEST_NAMES:
            owner = func.value
            if (
                func.attr == "urlopen"
                or "urllib" in ast.unparse(func)
                or (isinstance(owner, ast.Name) and owner.id in modules)
            ):
                lines.append(node.lineno)
        elif isinstance(func, ast.Name) and func.id == "urlopen":
            lines.append(node.lineno)
    return lines


def _dydx_url_builders(tree: ast.AST) -> list[int]:
    """Lines importing nautilus's `get_dydx_http_url`: the dYdX indexer's base URL."""
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and any(alias.name == "get_dydx_http_url" for alias in node.names)
    ]


def test_venue_http_rule_sees_aliases_and_the_dydx_url() -> None:
    tree = ast.parse(
        "from urllib.request import Request as R\nimport urllib.request\n"
        "from nautilus_trader.core.nautilus_pyo3 import get_dydx_http_url\n"
        "R('u')\nurllib.request.urlopen('u')\nRequest('not urllib')\n"
        "import urllib.request as ur\nfrom urllib import request as rq\nfrom urllib import request\n"
        "ur.Request('u')\nrq.Request('u')\nrequest.Request('u')\nother.Request('not urllib')\n"
    )
    assert _urllib_calls(tree) == [4, 5, 10, 11, 12]
    assert _dydx_url_builders(tree) == [3]


def _venue_http_offenders() -> dict[str, list[str]]:
    offenders: dict[str, list[str]] = {}
    for module, path in _judged_sources().items():
        tree = ast.parse(path.read_text())
        sites = [f"URL line {line}" for line in _venue_url_literals(tree)]
        sites += [f"dYdX URL line {line}" for line in _dydx_url_builders(tree)]
        if module not in NON_VENUE_HTTP_CLIENTS:
            sites += [f"request line {line}" for line in _urllib_calls(tree)]
        if sites:
            offenders[module] = sites
    return offenders


_VENUE_HTTP_OFFENDERS = _venue_http_offenders()


def test_every_venue_rest_request_is_built_in_the_kernel() -> None:
    illegal = {m: s for m, s in _VENUE_HTTP_OFFENDERS.items() if m not in LEGACY_VENUE_HTTP_UNTIL}
    assert illegal == {}, "build venue URLs and requests with kernel.venue_http (AD-D3)"


def test_venue_http_exemptions_are_still_needed() -> None:
    assert sorted(set(LEGACY_VENUE_HTTP_UNTIL) - set(_VENUE_HTTP_OFFENDERS)) == []
    unused = sorted(
        module
        for module in NON_VENUE_HTTP_CLIENTS
        if module not in _MODULES or not _urllib_calls(ast.parse(_MODULES[module].read_text()))
    )
    assert unused == [], "these clients no longer make HTTP requests: delete their entries"


@pytest.mark.parametrize(("module", "story"), sorted(LEGACY_VENUE_HTTP_UNTIL.items()))
def test_legacy_venue_http_expires_with_its_story(module: str, story: str) -> None:
    reason = unknown_or_done(story, story_statuses())
    assert reason is None, f"{reason}: move {module}'s venue requests onto kernel.venue_http"


# An instrument-id suffix test: `.endswith(".BYBIT")`, `.endswith("-LINEAR")`, `.endswith(f".{v}")`.
_ID_SUFFIX = re.compile(r"^[.-][A-Z]")


def _suffix_dispatches(tree: ast.AST) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "endswith":
            continue
        # `i.endswith(x)` and the unbound spelling `str.endswith(i, x)`
        unbound = isinstance(func.value, ast.Name) and func.value.id == "str"
        args = node.args[1:] if unbound else node.args
        arg = args[0] if args else None
        candidates = arg.elts if isinstance(arg, ast.Tuple) else [arg]  # endswith((".A", ".B"))
        if any(_is_id_suffix(candidate) for candidate in candidates):
            lines.append(node.lineno)
    return lines


def _joins_a_suffix(value: ast.expr) -> bool:
    """`'.' + v` (a constant separator on the left of a concatenation)."""
    return (
        isinstance(value, ast.BinOp)
        and isinstance(value.op, ast.Add)
        and isinstance(value.left, ast.Constant)
        and isinstance(value.left.value, str)
        and value.left.value.endswith((".", "-"))
    )


def _formats_a_suffix(value: ast.expr) -> bool:
    """`f'.{v}'` or `f'{a}.{v}'`: a separator part directly before a formatted part."""
    if not isinstance(value, ast.JoinedStr):
        return False
    return any(
        isinstance(part, ast.Constant)
        and str(part.value).endswith((".", "-"))
        and isinstance(following, ast.FormattedValue)
        for part, following in zip(value.values, value.values[1:], strict=False)
    )


def _is_id_suffix(arg: ast.expr | None) -> bool:
    if arg is None:
        return False
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return bool(_ID_SUFFIX.match(arg.value))
    return _joins_a_suffix(arg) or _formats_a_suffix(arg)


def test_suffix_rule_sees_literals_formats_and_tuples() -> None:
    tree = ast.parse(
        "i.endswith('.BYBIT')\ni.endswith(f'.{v}')\ni.endswith(('.A', '.B'))\n"
        "p.endswith('.parquet')\ni.endswith('.' + v)\ni.endswith(f'{a}.{v}')\n"
        "str.endswith(i, '.BYBIT')\np.endswith(f'{stem}.parquet')\ni.endswith(f'{a}-{v}')\n"
        "q.endswith(v + '.')\n"
    )
    assert _suffix_dispatches(tree) == [1, 2, 3, 5, 6, 7, 9]


def test_venue_dispatch_parses_ids_only_through_kernel_venues() -> None:
    """
    Known limit: this catches the suffix-test shape every former parser used (`endswith`, bound or
    unbound, over a literal, a `'.' + v` join or an f-string), not an arbitrary hand-rolled
    `rpartition`/`split`/slice; reviewers keep `kernel.venues` the only parser otherwise.
    """
    offending = sorted(
        f"{path.relative_to(PLATFORM_DIR)}:{line}"
        for module, path in _judged_sources().items()
        for line in _suffix_dispatches(ast.parse(path.read_text()))
    )
    assert offending == [], "use kernel.venues.venue_of / has_venue / market_kind (AD-D3)"


def test_suffix_rule_catches_literal_and_formatted_suffixes() -> None:
    tree = ast.parse(
        'a.endswith(".BYBIT")\nb.endswith(f".{v}")\nc.endswith(".parquet")\nd.endswith("x")\n'
    )
    assert _suffix_dispatches(tree) == [1, 2]
