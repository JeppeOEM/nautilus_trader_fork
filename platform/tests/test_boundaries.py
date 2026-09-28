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

Every Python module under `platform/` belongs to exactly one bounded context, by its top-level
package (`capture/`, `kernel/`, `observability/`, ...; `platform/scripts` and `platform/tests` are
contexts of their own); a module outside every context package fails. Each import is judged by
the contexts of both ends:

- a cross-context edge must be in `GRAPH` (AD-D2);
- a `_private` name is never imported across contexts;
- `observability` imports only the standard library, and `kernel` no context;
- `research` imports nothing from `data_api`, `views` or `ranking`; its context edges beyond
  `kernel`/`observability` are the candle store's query service, exactly the names in
  `RESEARCH_CANDLES_SERVICES` (Story 27.1: `research.application.frames` reads bars from the store,
  never a third seconds-to-bars fold; Story 27.2: `inspection` reads the day verdicts), and the
  archive's pure gap heuristics, exactly the names in `RESEARCH_ARCHIVE_SERVICES` (Story 27.2: the
  catalog inspection classifies gaps with the one rule, never a second one or the unbounded
  catalog readers);
- a `domain/` module or a venue `policies.py` imports only the standard library, numpy (pure array
  arithmetic, e.g. `research.domain`'s analysis values -- Story 27.1), `kernel` and
  `nautilus_trader.model`/`core`;
- `kernel/` holds exactly spine AD-D3's modules and stays pure: no in-repo import beyond itself,
  no store, no config loader, no module-level mutable state (Story 23.2);
- every venue REST URL and request lives in `kernel.venue_http`, and every venue dispatch on an
  instrument id's suffix goes through `kernel.venues` (Story 23.2);
- `ranking/` holds no module-level mutable runtime state, and exactly one module defines the
  pct-change/volatility formula `price_stats_from_series` (Story 25.2);
- `bots/` holds no module-level mutable runtime state either, and no module in `platform/` but
  `bots/infrastructure/nautilus_host.py` imports `TradingNode` or `nautilus_trader.live`
  (Story 25.3);
- `collection_control/` holds no module-level mutable runtime state, and capture reaches it only
  from its composition roots and the one venue loader, `capture.infrastructure.config` (Story
  25.4);
- no class anywhere in `platform/` subclasses `CaptureService` (or its old name `Collector`):
  venue variance is policy values and composition-root loops (Story 26.2).

One exemption only: `platform/tests` is cross-cutting and may import anything. The DDD migration
is finished (Story 26.3 deleted its last re-export shims and, with them, every legacy-module map,
dated deviation table and shim exemption this guard carried): the full AD-D2 graph is judged, and
a deviation is fixed at the import, never listed. The only per-module allowances left are the
permanent composition-root whitelist below and the non-venue HTTP clients.
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
from _source_tree import unknown_or_done


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
        # Query services only, held to `RESEARCH_CANDLES_SERVICES` below (Story 27.1):
        # `research.application.frames.bars` reads the candle store, never a third fold.
        (RESEARCH, CANDLES),
        # Pure functions only, held to `RESEARCH_ARCHIVE_SERVICES` below (Story 27.2):
        # `research.application.inspection` classifies gaps with the archive's one heuristic.
        (RESEARCH, ARCHIVE),
        (DATA_API, VIEWS),
        (DATA_API, ALERTING),
        # No (BOTS, RESEARCH), deliberately (Story 27.8): a paper bot runs a research strategy
        # (`CandlePatternStrategy`), but the bots host loads it by string path through Nautilus's
        # own resolver, `StrategyFactory.create(ImportableStrategyConfig(...))` -- the mechanism a
        # backtest uses -- so bots depends on no research code, only on a path in
        # `nautilus_host.STRATEGIES`. The image side is covered by `test_images.py`'s
        # `_STRING_PATH_IMPORTS`; `test_no_bots_module_imports_research` holds the absence.
        # No (BOT_TUI, VIEWS): since Story 25.1a bot_tui shows no ranking or market data, only
        # bots:*, collector:status and archive:status (Story 25.1b), so it reads no views model.
        # An operator harness drives an interface adapter in-process (e.g. `bench_candles`
        # times `/api/candles` through FastAPI's TestClient), exactly as an HTTP client would.
        (SCRIPTS, DATA_API),
    }
)

# A composition root wires an adapter into a port its own context declares, so it is the one module
# allowed to import the implementing context -- and only that one. Each entry names the module and
# the single extra context it may reach; anything else it imports is judged normally. This is not a
# widening of `_exempt` (which never covers a cross-context edge): it is a per-module whitelist.
COMPOSITION_ROOTS: dict[str, frozenset[str]] = {
    # The three venue entrypoints open their own `candles_<venue>.db` and hand capture the
    # `SecondSink` adapter plus the retention loop (Story 24.1).
    # dYdX's also builds collection control's `ControlService`/`StatusPublisher` and their adapters
    # and hands their loops to capture through `add_loops` (Story 25.4; Story 26.2).
    "capture.venues.dydx.__main__": frozenset({CANDLES, COLLECTION_CONTROL}),
    "capture.venues.bybit.__main__": frozenset({CANDLES}),
    "capture.venues.hyperliquid.__main__": frozenset({CANDLES}),
    # ...and the tests that drive exactly that wiring: one per venue, because `CaptureService`
    # never starts the retention loop itself and a venue that forgot it would fail silently.
    "capture.venues.dydx.tests.test_candle_feed": frozenset({CANDLES}),
    "capture.venues.bybit.tests.test_candle_wiring": frozenset({CANDLES}),
    "capture.venues.hyperliquid.tests.test_candle_wiring": frozenset({CANDLES}),
    # The data_api route tests seed the upstream store through its only writer -- the candle store
    # (`CandleStore`) and ranking's `metrics_store.write` -- so the route under test reads a real
    # store; `data_api` itself reaches neither context (Story 24.2).
    "data_api.tests.test_candles": frozenset({CANDLES}),
    "data_api.tests.test_screener_columns": frozenset({CANDLES}),
    "data_api.tests.test_data_api": frozenset({RANKING}),
    "data_api.tests.test_metrics": frozenset({RANKING}),
    # The retention run reads the dYdX collection plan through collection control's plan store
    # (`TomlPlanStore`) for its dropped-instrument and delta rules (Stories 25.1, 25.4).
    "archive.prune_catalog": frozenset({COLLECTION_CONTROL}),
    # AD-D17's one venue loader returns the `CollectionPlan` aggregate, so it builds one: it
    # imports `collection_control.domain` only. Everything else in capture sees the plan as ids and
    # the `capture.application.ports.PlanDiff` protocol.
    "capture.infrastructure.config": frozenset({COLLECTION_CONTROL}),
}


class Import(NamedTuple):
    src: str  # importing module
    src_ctx: str
    dst: str  # imported module
    name: str | None  # imported name, None for the module itself
    dst_ctx: str
    line: int


def _context_of(module: str) -> str | None:
    """Return an in-repo module's context, by its top-level package; None outside every one."""
    top = module.split(".")[0]
    return top if top in CONTEXTS else None


_MODULES = python_modules()
_KNOWN = set(_MODULES)


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
            dst_ctx = _context_of(dst) or "?"
            found.append(Import(module, src_ctx, dst, ref.name, dst_ctx, ref.line))
    return found


_IMPORTS = _all_imports()


def _exempt(imp: Import) -> bool:
    """From the cross-cutting `platform/tests`: the one exemption (Story 26.2)."""
    return imp.src_ctx == TESTS


def _composition_root_edge(imp: Import) -> bool:
    """Whether this import is a whitelisted composition root reaching its one allowed context."""
    return imp.dst_ctx in COMPOSITION_ROOTS.get(imp.src, frozenset())


def _judged() -> list[Import]:
    return [imp for imp in _IMPORTS if imp.src_ctx != imp.dst_ctx and not _exempt(imp)]


def _site(imp: Import) -> str:
    what = f"{imp.dst}.{imp.name}" if imp.name else imp.dst
    return f"{_MODULES[imp.src].relative_to(PLATFORM_DIR)}:{imp.line} -> {what}"


def _is_private(imp: Import) -> bool:
    return imp.name is not None and imp.name.startswith("_") and not imp.name.startswith("__")


def _private_key(imp: Import) -> tuple[str, str]:
    return (imp.src, f"{imp.dst}.{imp.name}")


def test_every_module_is_in_a_context() -> None:
    unmapped = sorted(module for module in _MODULES if _context_of(module) is None)
    assert unmapped == [], (
        "modules outside every context package: move each into its context (spine AD-D1)"
    )


def test_cross_context_edges_follow_the_graph() -> None:
    illegal = sorted(
        _site(imp) + f"  [{imp.src_ctx} -> {imp.dst_ctx}]"
        for imp in _judged()
        if (imp.src_ctx, imp.dst_ctx) not in GRAPH and not _composition_root_edge(imp)
    )
    assert illegal == [], "edges outside spine AD-D2's graph (fix the import, never the graph)"


def test_every_composition_root_still_wires_its_context() -> None:
    """A root that no longer reaches its context is a stale whitelist entry: delete it."""
    reached = {(imp.src, imp.dst_ctx) for imp in _judged()}
    unused = sorted(
        f"{module} -> {ctx}"
        for module, contexts in COMPOSITION_ROOTS.items()
        for ctx in contexts
        if (module, ctx) not in reached
    )
    assert unused == [], "no import needs these composition-root entries: delete them"
    assert set(COMPOSITION_ROOTS) <= _KNOWN, "a composition root naming no module"


@pytest.mark.parametrize("root", sorted(COMPOSITION_ROOTS))
def test_a_composition_root_may_reach_only_its_own_named_contexts(root: str) -> None:
    """Every root is checked, not whichever the dict happens to yield first."""
    for allowed in COMPOSITION_ROOTS[root]:
        assert _composition_root_edge(Import(root, CAPTURE, f"{allowed}.x", "y", allowed, 1))
    for denied in CONTEXTS - COMPOSITION_ROOTS[root] - _SHARED:
        assert not _composition_root_edge(Import(root, CAPTURE, f"{denied}.x", "y", denied, 1))


def test_a_module_that_is_not_a_composition_root_gets_no_such_exemption() -> None:
    assert not _composition_root_edge(
        Import("capture.application.capture_service", CAPTURE, "candles.x", "y", CANDLES, 1)
    )


def test_no_private_name_crosses_a_context() -> None:
    crossing = sorted(
        _site(imp) + f"  [{imp.src_ctx} -> {imp.dst_ctx}]" for imp in _judged() if _is_private(imp)
    )
    assert crossing == [], "a `_private` name imported across contexts: make it public in its owner"


def test_graph_gives_kernel_and_observability_no_outgoing_edge() -> None:
    assert {edge for edge in GRAPH if edge[0] in _SHARED} == set()


def _stdlib(target: str) -> bool:
    return target.split(".")[0] in sys.stdlib_module_names


def test_observability_imports_only_the_standard_library() -> None:
    foreign = sorted(
        f"{module}:{ref.line} -> {ref.target}"
        for module, path in _MODULES.items()
        if _context_of(module) == OBSERVABILITY and ".tests" not in f".{module}"
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
    assert (RESEARCH, DATA_API) not in GRAPH


# Packages research never imports (AD-D1 research row, Story 24.4): the read models and ranking.
# Rolling metrics come from ranking's published output, never code.
_RESEARCH_FORBIDDEN_PACKAGES = frozenset({VIEWS, RANKING})


# What a non-test research module may take from candles (Story 27.1): the read-only query services
# (`verified_status`: the catalog inspection's day status, Story 27.2) and the bar sizes the store
# keeps. Tests seed a real store through `CandleStore`, as the views rule below also leaves test
# modules out.
RESEARCH_CANDLES_SERVICES: dict[str, frozenset[str]] = {
    "candles.application.queries": frozenset(
        {"open_store", "window", "oldest_t", "newest_t", "bucket_starts", "verified_status"}
    ),
    "candles.domain.fold": frozenset({"BAR_SECONDS"}),
}

# What a non-test research module may take from archive (Story 27.2): the pure gap heuristics over
# timestamps research read itself, bounded -- never `likely_outages`/`coverage`, which read a whole
# data type of an instrument unbounded (MEM-01).
RESEARCH_ARCHIVE_SERVICES: dict[str, frozenset[str]] = {
    "archive.application.diagnostics": frozenset({"find_gaps"}),
}

# Research's allowlisted context -> its table.
_RESEARCH_SERVICE_TABLES: dict[str, dict[str, frozenset[str]]] = {
    CANDLES: RESEARCH_CANDLES_SERVICES,
    ARCHIVE: RESEARCH_ARCHIVE_SERVICES,
}


def _research_imports_of(ctx: str) -> list[Import]:
    return [
        imp
        for imp in _IMPORTS
        if imp.src.split(".")[0] == RESEARCH
        and ".tests" not in f".{imp.src}"
        and imp.dst_ctx == ctx
    ]


@pytest.mark.parametrize("ctx", sorted(_RESEARCH_SERVICE_TABLES))
def test_research_takes_only_the_listed_services(ctx: str) -> None:
    table = _RESEARCH_SERVICE_TABLES[ctx]
    beyond = sorted(
        _site(imp)
        for imp in _research_imports_of(ctx)
        if imp.name not in table.get(imp.dst, frozenset())
    )
    assert beyond == [], f"research reads {ctx} only through its RESEARCH_*_SERVICES table"


@pytest.mark.parametrize("ctx", sorted(_RESEARCH_SERVICE_TABLES))
def test_every_listed_research_service_is_still_used(ctx: str) -> None:
    used = {(imp.dst, imp.name) for imp in _research_imports_of(ctx)}
    unused = sorted(
        f"{module}.{name}"
        for module, names in _RESEARCH_SERVICE_TABLES[ctx].items()
        for name in names
        if (module, name) not in used
    )
    assert unused == [], "no research module needs these any more: delete them from the table"


def test_every_research_context_edge_has_a_service_table() -> None:
    """A research edge beyond kernel/observability is reachable only through a listed table."""
    edges = {dst for src, dst in GRAPH if src == RESEARCH} - _SHARED
    assert edges == set(_RESEARCH_SERVICE_TABLES)


def test_research_imports_no_views_or_ranking() -> None:
    reaching = sorted(
        _site(imp)
        for imp in _IMPORTS
        if imp.src.split(".")[0] == RESEARCH
        and imp.dst.split(".")[0] in _RESEARCH_FORBIDDEN_PACKAGES
    )
    assert reaching == [], "research consumes the catalog and ranking's published output only"


# The non-test modules outside `alerting.infrastructure` that may import it (AD-D2: infrastructure
# is imported only by a composition root). The Story 24.3 `data_api.alerts` shim was deleted in
# Story 25.1.
ALERTING_INFRASTRUCTURE_IMPORTERS = frozenset({"data_api.alert_wiring"})


def _is_test_module(module: str) -> bool:
    return _context_of(module) == TESTS or ".tests." in f".{module}."


def test_alerting_infrastructure_is_imported_only_by_its_composition_root() -> None:
    importers = {
        imp.src
        for imp in _IMPORTS
        if imp.dst.startswith("alerting.infrastructure")
        and not imp.src.startswith("alerting.infrastructure")
        and not _is_test_module(imp.src)
    }
    assert importers - ALERTING_INFRASTRUCTURE_IMPORTERS == set(), (
        "only data_api.alert_wiring constructs the alerting adapters (AD-D2)"
    )
    assert ALERTING_INFRASTRUCTURE_IMPORTERS - importers == set(), "stale entries: delete them"
    assert "data_api.alert_wiring" in _KNOWN


# nautilus_trader's value types are domain-safe; its runtime, persistence and adapters are not.
_DOMAIN_SAFE_EXTERNAL = ("nautilus_trader.model", "nautilus_trader.core")
# Pure array arithmetic with no I/O, no clock and no process state: a domain module may vectorise
# its own fold with it (`candles.domain.fold`) or hold its analysis values in arrays
# (`research.domain`, Story 27.1). Matched on the top-level package, so a lookalike
# distribution (`numpydoc`, ...) is still foreign.
_DOMAIN_SAFE_ROOTS = frozenset({"numpy"})


def _is_domain_module(module: str) -> bool:
    """
    Tell a `domain/` module (`capture.domain.*` and every context's) or a venue's
    `capture/venues/<v>/policies.py` (Story 26.1: pure values, AD-D6; moved there in Story 26.2).
    """
    parts = module.split(".")
    policies = parts[:2] == [CAPTURE, "venues"] and len(parts) == 4 and parts[3] == "policies"
    return "domain" in parts[1:] or policies


def _domain_violations(module: str, targets: list[str]) -> list[str]:
    """Return what a domain module imports beyond the stdlib, `kernel` and Nautilus value types."""
    bad = []
    for target in targets:
        in_repo = _in_repo(target)
        if in_repo is not None:
            ctx = _context_of(in_repo)
            # Another domain module of the same context: a venue's policies import
            # `capture.domain` (its verdicts and policy protocols).
            if ctx != KERNEL and not (ctx == _context_of(module) and _is_domain_module(in_repo)):
                bad.append(target)
        elif (
            not _stdlib(target)
            and not target.startswith(_DOMAIN_SAFE_EXTERNAL)
            and target.split(".")[0] not in _DOMAIN_SAFE_ROOTS
        ):
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
    assert not _is_domain_module("capture.venues.dydx.__main__")
    assert not _is_domain_module("kernel.venues.x.policies")
    assert _domain_violations(
        "capture.venues.dydx.policies",
        [
            "dataclasses",
            "nautilus_trader.model.data",
            "numpy",
            "asyncio",
            "redis",
            "nautilus_trader.live",
            "numpydoc",
        ],
    ) == ["redis", "nautilus_trader.live", "numpydoc"]


def test_research_domain_is_domain_code_admitting_numpy_not_pandas() -> None:
    """Story 27.1: `research/domain` is pure numpy arithmetic; pandas lives in its application."""
    assert _is_domain_module("research.domain.returns")
    assert not _is_domain_module("research.application.frames")
    assert "research.domain.returns" in _KNOWN
    assert _domain_violations(
        "research.domain.returns", ["numpy", "kernel.performance_metrics", "pandas", "pyarrow"]
    ) == ["pandas", "pyarrow"]


def _not_test(module: str) -> bool:
    return ".tests." not in f".{module}."


_CAPTURE_MODULES = {
    module: path
    for module, path in _MODULES.items()
    if _context_of(module) == CAPTURE and _not_test(module)
}
# Forbidden in a capture aggregate or policy (AD-D6): it reports through verdicts and events, and
# the `CaptureService` alone logs, ledgers, awaits and reads the clock.
_IMPURE_IMPORTS = ("logging", "asyncio", "time", "observability")


def _impurities(module: str, tree: ast.Module, targets: list[str]) -> list[str]:
    found = [t for t in targets if t.split(".")[0] in _IMPURE_IMPORTS]
    found += [
        f"{type(node).__name__} at line {node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef | ast.Await)
    ]
    return found


def test_capture_domain_and_policies_never_log_ledger_await_or_read_the_clock() -> None:
    """Story 26.1: policies and aggregates are pure and synchronous (spine AD-D6)."""
    impure = {
        module: found
        for module, path in _CAPTURE_MODULES.items()
        if _is_domain_module(module)
        and (
            found := _impurities(
                module,
                ast.parse(path.read_text()),
                [ref.target for ref in imports_of(module, path, _KNOWN)],
            )
        )
    }
    assert impure == {}
    assert any(module.endswith(".policies") for module in _CAPTURE_MODULES), "rule saw no policy"


def test_the_purity_rule_catches_each_kind() -> None:
    tree = ast.parse("import logging\nasync def f():\n    await g()\n")
    assert _impurities("m", tree, ["logging", "kernel.fold"]) == [
        "logging",
        "AsyncFunctionDef at line 2",
        "Await at line 3",
    ]


_SITE_LITERAL = re.compile(r"(collector|archive_gaps)\.[a-z_]+")
_SITES_MODULE = "capture.application.sites"
# The application service: the one module that calls `error_ledger.record` in capture (AD-D6).
_LEDGER_CALLER = "capture.application.capture_service"


def _ledger_calls(tree: ast.Module) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "record"
        and isinstance(node.value, ast.Name)
        and node.value.id == "error_ledger"
    ]


def _site_literals(tree: ast.Module) -> list[str]:
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and _SITE_LITERAL.fullmatch(node.value)
    ]


def test_the_collector_is_captures_only_ledger_caller() -> None:
    callers = {
        module: lines
        for module, path in _CAPTURE_MODULES.items()
        if (lines := _ledger_calls(ast.parse(path.read_text()))) and module != _LEDGER_CALLER
    }
    assert callers == {}, "report through `CaptureService._ledger` (a `Ledger` handed to adapters)"
    assert len(_ledger_calls(ast.parse(_MODULES[_LEDGER_CALLER].read_text()))) == 1


def test_every_capture_ledger_site_is_named_once_in_sites() -> None:
    literal_sites = {
        module: literals
        for module, path in _CAPTURE_MODULES.items()
        if module != _SITES_MODULE and (literals := _site_literals(ast.parse(path.read_text())))
    }
    assert literal_sites == {}, "name every site through `capture.application.sites`"
    sites_tree = ast.parse(_MODULES[_SITES_MODULE].read_text())
    declared = [
        target.id
        for node in sites_tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    ]
    values = _site_literals(sites_tree)
    assert len(values) == len(set(values)) == len(declared), "one constant per distinct site"
    used = {
        node.attr
        for path in _CAPTURE_MODULES.values()
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "sites"
    }
    assert sorted(set(declared) - used) == [], "a site no code reports: delete it"


# Capture's composition roots (the venue entrypoints) are the only non-test importers of its
# adapters and its loader (spine AD-D2). The adapters import each other and the application's ports
# only.
_CAPTURE_ROOTS = frozenset(
    {
        "capture.venues.dydx.__main__",
        "capture.venues.bybit.__main__",
        "capture.venues.hyperliquid.__main__",
    }
)
_CAPTURE_INFRASTRUCTURE = "capture.infrastructure"


def test_capture_infrastructure_is_imported_only_by_its_composition_roots() -> None:
    importers = sorted(
        f"{module} -> {ref.target}"
        for module, path in _CAPTURE_MODULES.items()
        if module not in _CAPTURE_ROOTS and not module.startswith(_CAPTURE_INFRASTRUCTURE)
        for ref in imports_of(module, path, _KNOWN)
        if ref.target.startswith(_CAPTURE_INFRASTRUCTURE)
    )
    assert importers == []
    assert set(_CAPTURE_ROOTS) <= _KNOWN, "a capture composition root naming no module"


def test_the_capture_service_imports_no_capture_infrastructure() -> None:
    """The application service sees the adapters as ports only; the roots inject them."""
    assert not any(
        ref.target.startswith(_CAPTURE_INFRASTRUCTURE)
        for ref in imports_of(_LEDGER_CALLER, _MODULES[_LEDGER_CALLER], _KNOWN)
    )


# The capture service and its pre-Story-26.2 class name, kept so a revived `Collector` still trips.
_CAPTURE_SERVICE_NAMES = frozenset({"CaptureService", "Collector"})


def _base_names(node: ast.ClassDef) -> set[str]:
    """Return the bare names a class derives from: `X`, `mod.X` and `X[...]` all name X."""
    names = set()
    for base in node.bases:
        target = base.value if isinstance(base, ast.Subscript) else base
        name = getattr(target, "id", None) or getattr(target, "attr", None)
        if isinstance(name, str):
            names.add(name)
    return names


def _capture_service_aliases(tree: ast.Module) -> set[str]:
    """
    Return the names a module binds to the service: the two real names, any `import ... as` of
    them and any plain rebinding (`Base = CaptureService`), so an alias cannot hide a subclass.
    """
    names = set(_CAPTURE_SERVICE_NAMES)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names |= {a.asname for a in node.names if a.name in names and a.asname}
    changed = True
    while changed:  # a rebinding of a rebinding
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and _bare_name(node.value) in names:
                bound = {t.id for t in node.targets if isinstance(t, ast.Name)} - names
                names |= bound
                changed = changed or bool(bound)
    return names


def _bare_name(node: ast.expr) -> str | None:
    name = getattr(node, "id", None) or getattr(node, "attr", None)
    return name if isinstance(name, str) else None


def _dynamic_subclass(node: ast.AST, names: set[str]) -> bool:
    """`type("X", (CaptureService,), {...})`: a subclass built without a `class` statement."""
    if not (isinstance(node, ast.Call) and _bare_name(node.func) == "type" and len(node.args) == 3):
        return False
    bases = node.args[1]
    return isinstance(bases, ast.Tuple) and any(_bare_name(b) in names for b in bases.elts)


def _capture_service_subclasses(tree: ast.Module) -> list[str]:
    names = _capture_service_aliases(tree)
    return [
        f"{getattr(node, 'name', 'type()')} at line {node.lineno}"
        for node in ast.walk(tree)
        if (isinstance(node, ast.ClassDef) and _base_names(node) & names)
        or _dynamic_subclass(node, names)
    ]


def test_no_class_anywhere_subclasses_the_capture_service() -> None:
    """
    Story 26.2 (spine AD-D6, parent AD-1): a venue is a composition root over policy values, never
    a subclass, so no venue -- and no test -- can override a core method or the gate itself.
    """
    subclasses = {
        module: found
        for module, path in _MODULES.items()
        if (found := _capture_service_subclasses(ast.parse(path.read_text())))
    }
    assert subclasses == {}, "wire the venue in its `__main__.py`, never subclass the service"


def test_the_subclass_rule_catches_each_base_form() -> None:
    tree = ast.parse(
        "class A(CaptureService): pass\nclass B(capture_service.CaptureService): pass\n"
        "class C(Collector, Mixin): pass\nclass D(Generic[T]): pass\n"
        "class E(DydxCollector): pass\n"
        "from capture.application.capture_service import CaptureService as Core\n"
        "class F(Core): pass\nBase = Core\nclass G(Base): pass\n"
        'H = type("H", (CaptureService,), {})\n'
    )
    assert _capture_service_subclasses(tree) == [
        "A at line 1",
        "B at line 2",
        "C at line 3",
        "F at line 7",
        "G at line 9",
        "type() at line 10",
    ]


def test_every_import_resolves_to_a_context() -> None:
    """An import whose either end lies outside every context package is a placement gap."""
    unplaced = sorted(_site(imp) for imp in _IMPORTS if "?" in (imp.src_ctx, imp.dst_ctx))
    assert unplaced == []


def test_checker_places_modules_by_top_level_package_only() -> None:
    assert _context_of("some_package.a_module_nobody_placed") is None
    assert _context_of("observability.anything") == OBSERVABILITY
    assert _context_of("capture.venues.dydx.client") == CAPTURE
    assert _context_of("kernel") == KERNEL


def test_story_expiry_reads_each_board_status() -> None:
    """`_source_tree.unknown_or_done`, which `research/tests/test_research_reads.py` expires on."""
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


def test_exemption_covers_only_platform_tests() -> None:
    root = Import(
        "capture.infrastructure.config", CAPTURE, "collection_control.x", "x", COLLECTION_CONTROL, 2
    )
    across = Import("capture.application.capture_service", CAPTURE, "candles.x", "x", CANDLES, 1)
    interface = Import("data_api.app", DATA_API, "data_api.alert_wiring", "x", ALERTING, 1)
    guard = Import("tests.test_x", TESTS, "candles.domain.fold", "_x", CANDLES, 1)
    assert not _exempt(root)
    assert not _exempt(across)
    assert not _exempt(interface)
    assert _exempt(guard)


# --- the shared kernel (spine AD-D3, Story 23.2) --------------------------------------------------

KERNEL_MODULES = frozenset(
    {
        "__init__",
        "archive_markers",
        "candle_patterns",
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


def _import_time_call(node: ast.stmt, extra: frozenset[str] = frozenset()) -> str | None:
    """
    Return the callee of an unsanctioned import-time call, bare or bound to a name; `extra` names
    calls a caller's rule sanctions on top of the kernel's.
    """
    if (bare := _bare_call_name(node)) is not None:
        return None if bare in _SANCTIONED_BARE_CALLS | extra else bare
    bound = _bound_call_name(node)
    sanctioned = _SANCTIONED_VALUE_CALLS | _SANCTIONED_BARE_CALLS | extra
    return None if bound is None or bound in sanctioned else bound


def _own_state_site(node: ast.stmt, extra: frozenset[str] = frozenset()) -> str | None:
    """Return this statement's own impurity label, ignoring anything nested inside it."""
    if isinstance(node, ast.AugAssign):
        return f"line {node.lineno} (augmented assignment)"
    if isinstance(node, ast.Assign | ast.AnnAssign) and _is_mutable_value(node.value):
        return f"line {node.lineno}"
    if (callee := _import_time_call(node, extra)) is not None:
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


def _state_sites(statements: list[ast.stmt], extra: frozenset[str] = frozenset()) -> list[str]:
    """Return module- or class-level mutable bindings, including ones under `if`/`try`/`with`."""
    found = []
    for node in statements:
        site = _own_state_site(node, extra)
        if site is not None:
            found.append(site)
        else:
            found += _state_sites(_nested_statements(node), extra)
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


# Venue REST: every URL and request is built in `kernel.venue_http` (AD-D3).
# HTTP clients that talk to no venue, so `kernel.venue_http` does not own them.
NON_VENUE_HTTP_CLIENTS: dict[str, str] = {
    "observability.notify": "ntfy / Telegram / webhook alert transport",
    "research.rank_history": "the local data_api HTTP API",
    "research.application.ranking_history": "the local data_api HTTP API (Story 27.1)",
    "research.watchlist": "the local data_api HTTP API",
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
    assert _VENUE_HTTP_OFFENDERS == {}, (
        "build venue URLs and requests with kernel.venue_http (AD-D3)"
    )


def test_non_venue_http_clients_are_still_needed() -> None:
    unused = sorted(
        module
        for module in NON_VENUE_HTTP_CLIENTS
        if module not in _MODULES or not _urllib_calls(ast.parse(_MODULES[module].read_text()))
    )
    assert unused == [], "these clients no longer make HTTP requests: delete their entries"


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


# --- views: the read models both interfaces show (spine AD-D11, Story 24.2) ----------------------

# The only names a non-test `views` module may take from candles or ranking: their query services
# (AC #3 of Story 24.2). An upper bound on the spine's illustrative list, naming exactly what the
# read models need -- `latest`/`verified_status` are unused by views, so unlisted. A store adapter
# (`candles.infrastructure`) is never reachable: `open_store` hands out the read-only connection.
VIEWS_QUERY_SERVICES: dict[str, frozenset[str]] = {
    "candles.application.queries": frozenset(
        {"window", "oldest_t", "candle_dicts_for_window", "open_store"}
    ),
    "candles.application.forming": frozenset({"forming_bar", "bars_from_rows"}),
    "candles.domain.candle": frozenset({"Candle", "is_valid_candle"}),
    "ranking.application.queries": frozenset({"history", "nearest"}),
}
# Packages views never imports (AD-D2): the interfaces and capture.
_VIEWS_FORBIDDEN_PACKAGES = frozenset({DATA_API, BOT_TUI, CAPTURE})
# What an interface adapter never imports directly: every read goes through views (AC #3).
_INTERFACE_FORBIDDEN_PACKAGES = frozenset({RANKING, CAPTURE, CANDLES})


def _is_test_module(module: str) -> bool:
    return ".tests" in f".{module}"


def _views_upstream_imports() -> list[Import]:
    """Every import a non-test `views` module takes from the candles or ranking context."""
    return [
        imp
        for imp in _IMPORTS
        if imp.src.split(".")[0] == VIEWS
        and not _is_test_module(imp.src)
        and imp.dst_ctx in (CANDLES, RANKING)
    ]


def test_views_takes_only_the_listed_query_services_from_candles_and_ranking() -> None:
    beyond = sorted(
        _site(imp)
        for imp in _views_upstream_imports()
        if imp.name not in VIEWS_QUERY_SERVICES.get(imp.dst, frozenset())
    )
    assert beyond == [], (
        "views calls only the candles/ranking query services (VIEWS_QUERY_SERVICES)"
    )


def test_every_listed_views_query_service_is_still_used() -> None:
    used = {(imp.dst, imp.name) for imp in _views_upstream_imports()}
    unused = sorted(
        f"{module}.{name}"
        for module, names in VIEWS_QUERY_SERVICES.items()
        for name in names
        if (module, name) not in used
    )
    assert unused == [], "no views module needs these any more: delete them from the table"


def test_views_imports_no_interface_or_capture() -> None:
    reaching = sorted(
        _site(imp)
        for imp in _IMPORTS
        if imp.src.split(".")[0] == VIEWS
        and not _is_test_module(imp.src)
        and imp.dst.split(".")[0] in _VIEWS_FORBIDDEN_PACKAGES
    )
    assert reaching == [], "views imports only kernel, observability and the query services"


def test_no_interface_module_imports_a_context_views_should_read_for_it() -> None:
    reaching = sorted(
        _site(imp)
        for imp in _IMPORTS
        if imp.src.split(".")[0] in (DATA_API, BOT_TUI)
        and not _is_test_module(imp.src)
        and imp.dst.split(".")[0] in _INTERFACE_FORBIDDEN_PACKAGES
    )
    assert reaching == [], "data_api and bot_tui format and transport; views reads (AD-D11)"


# A `snapshots:raw` payload (or any `DydxSecondSnapshot.to_dict()`) is parsed only by its own type,
# `DydxSecondSnapshot.from_dict` (spine AD-D3): reading one of these keys off a dict is hand-parsing.
_SNAPSHOT_FIELDS = frozenset(
    {
        "bid_prices",
        "bid_sizes",
        "ask_prices",
        "ask_sizes",
        "buy_volume",
        "sell_volume",
        "buy_count",
        "sell_count",
        "open_price",
        "high_price",
        "low_price",
        "close_price",
    }
)


def _snapshot_key_reads(tree: ast.AST) -> list[int]:
    """Lines reading a snapshot field by key: `x["bid_prices"]` or `x.get("bid_prices", ...)`."""
    lines = []
    for node in ast.walk(tree):
        key = None
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
            key = node.slice.value if isinstance(node.slice, ast.Constant) else None
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            key = node.args[0].value
        if key in _SNAPSHOT_FIELDS:
            lines.append(node.lineno)
    return lines


def test_snapshot_key_rule_sees_subscripts_and_gets_not_writes_or_attributes() -> None:
    tree = ast.parse(
        "a = s['bid_prices']\nb = s.get('close_price')\nd = {'bid_prices': []}\n"
        "e = s.bid_prices\nf = s['instrument_id']\ns['ask_sizes'] = 1\ng = s.get('ask_prices', [])\n"
    )
    assert _snapshot_key_reads(tree) == [1, 2, 7]


_SNAPSHOT_INDEXERS = {
    module: lines
    for module, path in _judged_sources().items()
    if (lines := _snapshot_key_reads(ast.parse(path.read_text())))
}


def test_no_module_outside_the_kernel_indexes_a_snapshot_payload_by_key() -> None:
    assert _SNAPSHOT_INDEXERS == {}, (
        "decode with DydxSecondSnapshot.from_dict and read attributes (AD-D3)"
    )


# --- ranking, bots and collection control: no module-level runtime state (spine AD-D10, Stories
# 25.2/25.3/25.4); ranking: one formula (Story 25.2) --------------------------------------------

# On top of the kernel's sanctioned import-time calls, a ranking module may bind a module logger:
# `logging.getLogger` returns the process-wide logger registry's entry, not state the module owns.
_RANKING_SANCTIONED_CALLS = frozenset({"getLogger"})
# ...and a bots module may also declare a dataclass default: `Decimal(...)` builds an immutable
# value, and `field(default_factory=...)` is a declaration whose factory runs per instance -- both
# are how the frozen config value objects spell their defaults, neither is state the module owns.
_BOTS_SANCTIONED_CALLS = _RANKING_SANCTIONED_CALLS | {"Decimal", "field"}
# Collection control's modules bind only their module logger (its plan and service state live on
# the instances `build_capture_from_file` makes; its frozen tables are the kernel's sanctioned builders).
_COLLECTION_CONTROL_SANCTIONED_CALLS = _RANKING_SANCTIONED_CALLS
_STATE_RULED_CONTEXTS = {
    RANKING: _RANKING_SANCTIONED_CALLS,
    BOTS: _BOTS_SANCTIONED_CALLS,
    COLLECTION_CONTROL: _COLLECTION_CONTROL_SANCTIONED_CALLS,
}


def _module_scope_nodes(tree: ast.Module) -> list[ast.AST]:
    """Every node that runs at import time: the module and class bodies, not function bodies."""
    found: list[ast.AST] = []
    pending: list[ast.AST] = list(tree.body)
    while pending:
        node = pending.pop()
        found.append(node)
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            pending.extend(ast.iter_child_nodes(node))
    return found


def _is_main_guard(node: ast.stmt) -> bool:
    """`if __name__ == "__main__":` -- the entrypoint's body, which an import never runs."""
    test = node.test if isinstance(node, ast.If) else None
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and any(isinstance(c, ast.Constant) and c.value == "__main__" for c in test.comparators)
    )


def _ranking_state(
    module: str, tree: ast.Module, sanctioned: frozenset[str] = _RANKING_SANCTIONED_CALLS
) -> list[str]:
    """Module/class-level mutable state, `global` statements and import-time environment reads."""
    tree = ast.Module(body=[n for n in tree.body if not _is_main_guard(n)], type_ignores=[])
    bad = [
        f"{module}: mutable module state at {site}" for site in _state_sites(tree.body, sanctioned)
    ]
    env_names = _ENV_NAMES | _env_aliases(tree)
    bad += [
        f"{module}:{n.lineno}: `global` statement"
        for n in ast.walk(tree)
        if isinstance(n, ast.Global)
    ]
    for node in _module_scope_nodes(tree):
        name = node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", None)
        if isinstance(node, ast.Attribute | ast.Name) and name in env_names:
            bad.append(f"{module}:{node.lineno}: reads the environment at import time")
    return bad


def _context_sources(context: str) -> dict[str, Path]:
    return {
        module: path
        for module, path in _MODULES.items()
        if module.split(".")[0] == context and ".tests" not in f".{module}"
    }


@pytest.mark.parametrize("context", sorted(_STATE_RULED_CONTEXTS))
def test_context_holds_no_module_level_runtime_state(context: str) -> None:
    """
    Every piece of ranking state lives on the board or engine `__main__` builds, every piece of
    bots state on the bot, ledger, supervisor or store instances its `__main__` builds, every piece
    of collection-control state on the plan and service instances `build_capture_from_file` makes
    (AD-D10).
    """
    sources = _context_sources(context)
    assert len(sources) > 10, f"the {context} context's modules were not found"
    stateful = sorted(
        entry
        for module, path in sources.items()
        for entry in _ranking_state(
            module, ast.parse(path.read_text()), _STATE_RULED_CONTEXTS[context]
        )
    )
    assert stateful == [], f"{context}/ holds no module-level mutable runtime state (AD-D10)"


def test_ranking_state_rule_catches_each_kind() -> None:
    tree = ast.parse(
        "import logging\nimport os\nlogger = logging.getLogger(__name__)\n"
        "URL = os.environ.get('X')\n_S = {}\nK = os.environ['K']\n"
        "def f():\n    global _S\n    return os.environ.get('Y')\n"
        "class C:\n    seen = []\n"
        "if __name__ == '__main__':\n    main()\n"
    )
    assert _ranking_state("m", tree) == [
        "m: mutable module state at line 4 (unsanctioned import-time call: get)",
        "m: mutable module state at line 5",
        "m: mutable module state at line 11",
        "m:8: `global` statement",
        "m:6: reads the environment at import time",
        "m:4: reads the environment at import time",
    ]


def test_bots_state_rule_sanctions_only_dataclass_defaults() -> None:
    tree = ast.parse(
        "from dataclasses import dataclass, field\nfrom decimal import Decimal\n"
        "@dataclass(frozen=True)\nclass C:\n    a: Decimal = Decimal('1')\n"
        "    b: dict = field(default_factory=dict)\n_CACHE = {}\n_X = make()\n"
    )
    assert _ranking_state("m", tree, _BOTS_SANCTIONED_CALLS) == [
        "m: mutable module state at line 7",
        "m: mutable module state at line 8 (unsanctioned import-time call: make)",
    ]
    assert len(_ranking_state("m", tree)) == 4  # ranking sanctions neither


def _definitions_of(name: str) -> list[str]:
    return sorted(
        str(path.relative_to(PLATFORM_DIR))
        for path in _MODULES.values()
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name
    )


def test_the_pct_change_and_volatility_formula_is_defined_exactly_once() -> None:
    """SSOT-02 / AD-D10: views and research read ranking's published values, never recompute."""
    assert _definitions_of("price_stats_from_series") == ["ranking/domain/metrics.py"]


# --- bots: research strategies by string path only (Story 27.8) ---------------------------------


def _research_imports(module: str, path: Path) -> list[str]:
    """Every static import of `research` and every `import_module`/`__import__` of it."""
    static = [
        f"{module}:{ref.line} -> {ref.target}"
        for ref in imports_of(module, path, _KNOWN)
        if ref.target.split(".")[0] == RESEARCH
    ]
    dynamic = [
        f"{module}:{node.lineno} -> {ast.unparse(node.args[0])} (dynamic)"
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Call)
        and _call_name(node) in ("import_module", "__import__")
        and node.args
        and RESEARCH in ast.unparse(node.args[0])
    ]
    return static + dynamic


def test_no_bots_module_imports_research() -> None:
    """
    The bots host names research strategies only as `StrategyFactory` string paths, so bots has
    no research edge (see `GRAPH`): neither a static import nor an `import_module` of it, in any
    bots module, tests included.
    """
    found = [
        ref
        for module, path in _MODULES.items()
        if module.split(".")[0] == BOTS
        for ref in _research_imports(module, path)
    ]
    assert found == [], "bots loads a research strategy by string path only (Story 27.8)"


def test_the_research_import_rule_catches_each_form(tmp_path: Path) -> None:
    source = tmp_path / "m.py"
    source.write_text(
        "from research.strategies.candle_pattern_strategy import CandlePatternStrategy\n"
        "import research.strategies.ofi_strategy\n"
        "import importlib\n"
        "importlib.import_module('research.strategies.candle_pattern_strategy')\n"
        "PATH = 'research.strategies.candle_pattern_strategy:CandlePatternStrategy'\n"
    )
    assert [ref.split(" -> ")[0] for ref in _research_imports("m", source)] == [
        "m:1",
        "m:2",
        "m:4",
    ]


# --- bots: Nautilus's live runtime behind one module (spine AD-D2, AD-8, Story 25.3) -------------

# The one module in `platform/` -- tests included -- that may import the live runtime.
TRADING_NODE_HOSTS = frozenset({"bots.infrastructure.nautilus_host"})
_TRADING_NODE_NAMES = frozenset({"TradingNode", "TradingNodeConfig"})


def _is_live_runtime(target: str, name: str | None) -> bool:
    parts = target.split(".")
    return (
        parts[:2] == ["nautilus_trader", "live"]
        or (parts == ["nautilus_trader"] and name == "live")
        or name in _TRADING_NODE_NAMES
    )


def _dynamic_live_imports(module: str, path: Path) -> list[str]:
    """`importlib.import_module("nautilus_trader.live...")`/`__import__(...)` with a literal."""
    return [
        f"{module}:{node.lineno} -> {node.args[0].value} (dynamic)"
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Call)
        and _call_name(node) in ("import_module", "__import__")
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and str(node.args[0].value).startswith("nautilus_trader.live")
    ]


def _trading_node_refs(module: str, path: Path) -> list[str]:
    """Every import (or bound-module attribute read) of `TradingNode` or `nautilus_trader.live`."""
    static = [
        f"{module}:{ref.line} -> {ref.target}.{ref.name}" if ref.name else f"{module}:{ref.line}"
        for ref in imports_of(module, path, _KNOWN)
        if _is_live_runtime(ref.target, ref.name)
    ]
    return static + _dynamic_live_imports(module, path)


def test_only_the_nautilus_host_imports_trading_node() -> None:
    found = {
        module: refs
        for module, path in _MODULES.items()
        if (refs := _trading_node_refs(module, path))
    }
    strays = sorted(
        ref for module, refs in found.items() if module not in TRADING_NODE_HOSTS for ref in refs
    )
    assert strays == [], "only bots/infrastructure/nautilus_host.py builds a TradingNode (AD-8)"
    assert set(found) == TRADING_NODE_HOSTS, "the host no longer imports TradingNode: update this"


def test_trading_node_rule_catches_each_import_form(tmp_path: Path) -> None:
    source = tmp_path / "m.py"
    source.write_text(
        "from nautilus_trader.live.node import TradingNode\n"
        "from nautilus_trader.config import TradingNodeConfig\n"
        "import nautilus_trader.live.node as live\n"
        "from nautilus_trader.config import CacheConfig\n"
        "from nautilus_trader import live as runtime\n"
        "import importlib\n"
        "importlib.import_module('nautilus_trader.live.node')\n"
        "importlib.import_module('nautilus_trader.model')\n"
    )
    assert _trading_node_refs("m", source) == [
        "m:1 -> nautilus_trader.live.node.TradingNode",
        "m:2 -> nautilus_trader.config.TradingNodeConfig",
        "m:3",
        "m:5 -> nautilus_trader.live",
        "m:7 -> nautilus_trader.live.node (dynamic)",
    ]
