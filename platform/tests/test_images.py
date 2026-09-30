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
Every image ships the packages its entrypoints import (spine AD-D12).

An entrypoint is what a container of a compose service runs: the service's `command:` (or its
dockerfile's `CMD` when it has none), every `python3 -m <module>` the `Makefile` runs in that
service, and every step a module launches as its own process (`archive.nightly`'s chain, and the
`archive` service's `archive.scheduler`, which runs that chain plus its consolidate and backup
steps). Nightly maintenance is scheduled by that service, not a host cron line (Story 25.1b):
`test_the_deploy_checklist_has_no_crontab_line_and_names_the_removal_check` holds the docs to it.

For each entrypoint the transitive closure of in-repo imports is computed with `ast` (followed
through every intermediate module, however deep) and its top-level packages must all be in that
service's dockerfile `COPY platform/<pkg> ./<pkg>` set: a missing one is an `ImportError` at
container start or, worse, at the first call of a lazily imported path. The `Makefile` test
lists are held to the same rule: a test directory runs inside its image, so its package must be
copied there.
"""

import ast
import re
import shlex
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
from _source_tree import PLATFORM_DIR
from _source_tree import imports_of
from _source_tree import python_modules


_MODULES = python_modules()
_KNOWN = set(_MODULES)
_COMPOSE = PLATFORM_DIR / "docker-compose.yml"
_MAKEFILE = PLATFORM_DIR / "Makefile"
_CRON_DOC = PLATFORM_DIR / "docs" / "DEPLOY_CHECKLIST.md"


class Entrypoint(NamedTuple):
    service: str
    module: str
    source: str  # where the entrypoint is declared, for the failure message


class _Service(NamedTuple):
    dockerfile: str
    command: str | None


def _compose_services() -> dict[str, _Service]:
    """
    `services:` of docker-compose.yml: each service's `build.dockerfile` and `command:`. Parsed by
    indentation (no YAML dependency in the image); only services built from a dockerfile here.
    """
    services: dict[str, dict[str, str]] = {}
    current: str | None = None
    in_services = False
    for line in _COMPOSE.read_text().splitlines():
        if line and not line[0].isspace():
            in_services = line.startswith("services:")
            continue
        if not in_services or not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        key, _, value = line.strip().partition(":")
        if indent == 2:
            current = key
            services[current] = {}
        elif current is not None and key in ("build", "dockerfile", "command") and indent in (4, 6):
            services[current][key] = value.strip()
    # A `build:` without an explicit `dockerfile:` (compose's default `Dockerfile`) would drop
    # the service from the check; this repo has none, and a new one must be checked too.
    unchecked = sorted(n for n, f in services.items() if "build" in f and "dockerfile" not in f)
    assert unchecked == [], f"compose services built without an explicit dockerfile: {unchecked}"
    return {
        name: _Service(fields["dockerfile"], fields.get("command"))
        for name, fields in services.items()
        if "dockerfile" in fields
    }


def _dockerfile(service: _Service) -> Path:
    # Build context is the repo root (`context: ..`); the dockerfile path is relative to it.
    return PLATFORM_DIR.parent / service.dockerfile


def _copied_packages(dockerfile: Path) -> set[str]:
    return set(
        re.findall(r"^COPY\s+platform/(\w+)\s+\./\1\s*$", dockerfile.read_text(), re.MULTILINE)
    )


def _dockerfile_cmd(dockerfile: Path) -> str:
    match = re.search(r"^CMD\s+(\[.*\])\s*$", dockerfile.read_text(), re.MULTILINE)
    assert match, f"{dockerfile.name} has no exec-form CMD"
    return " ".join(ast.literal_eval(match.group(1)))


# uvicorn options that take no value; every other option consumes the next token.
_UVICORN_FLAGS = frozenset(
    {
        "--reload",
        "--factory",
        "--proxy-headers",
        "--no-proxy-headers",
        "--access-log",
        "--no-access-log",
        "--use-colors",
        "--no-use-colors",
        "--server-header",
        "--no-server-header",
        "--date-header",
        "--no-date-header",
    }
)


def _uvicorn_app(tokens: list[str]) -> str:
    """Return the first non-option token after `uvicorn`: the `<module>:<attribute>` app."""
    rest = tokens[1:]
    while rest:
        token = rest.pop(0)
        if not token.startswith("-"):
            assert re.fullmatch(r"[\w.]+:[\w.]+", token), f"uvicorn app {token!r} is not mod:attr"
            return token
        if "=" not in token and token not in _UVICORN_FLAGS and rest:
            rest.pop(0)
    raise AssertionError(f"uvicorn command {shlex.join(tokens)!r} names no app")


def _module_of(command: str) -> str | None:
    """
    Return the in-repo module a command runs: `python3 -m <mod>` or `uvicorn <mod>:<app>`. For
    `-m <package>` that is the package's `__main__` -- what Python actually executes; the package's
    `__init__` alone would leave the entrypoint's whole closure unchecked.
    """
    tokens = shlex.split(command)
    if "-m" in tokens:
        module = tokens[tokens.index("-m") + 1]
        if module.split(".")[0] not in _TOP_PACKAGES:
            return None
        return f"{module}.__main__" if f"{module}.__main__" in _KNOWN else module
    if tokens and tokens[0] == "uvicorn":
        return _uvicorn_app(tokens).split(":")[0]
    return None


def _checked_command(service: str, raw: str) -> str:
    """
    Return a compose `command:` in the one form this parser reads (a plain one-line string), or
    fail loudly: a folded/literal block (`>`/`|`), a list, or an empty value would otherwise be
    mis-parsed into a wrong entrypoint and a silently wrong check.
    """
    value = raw.strip()
    unterminated = value[0] in "'\"" and value[-1] != value[0] if value else False
    unsupported = not value or value[0] in ">|[-" or unterminated
    assert not unsupported, (
        f"docker-compose.yml {service}: `command: {raw}` is in a form test_images.py does not "
        "parse (folded/literal block, list or empty) -- write it as a one-line string, or teach "
        "the parser that form"
    )
    return value


_TOP_PACKAGES = {module.split(".")[0] for module in _KNOWN}


def _makefile_recipes() -> dict[str, list[str]]:
    """Target -> its recipe lines, backslash continuations joined."""
    recipes: dict[str, list[str]] = {}
    target: str | None = None
    joined = _MAKEFILE.read_text().replace("\\\n", " ")
    for line in joined.splitlines():
        match = re.match(r"^([A-Za-z][\w-]*):(?!=)", line)
        if match:
            target = match.group(1)
            recipes[target] = []
        elif line.startswith("\t") and target is not None:
            recipes[target].append(line.strip())
    return recipes


# `docker compose run` options that take a value (the service name follows the options). An
# option missing here would make its value the "service", which `_entrypoints` then rejects as
# unknown rather than silently skipping the recipe.
_RUN_VALUE_OPTIONS = frozenset(
    {
        "-e",
        "--env",
        "--env-from-file",
        "-u",
        "--user",
        "-w",
        "--workdir",
        "-v",
        "--volume",
        "--entrypoint",
        "-l",
        "--label",
        "--name",
        "-p",
        "--publish",
        "--pull",
        "--cap-add",
        "--cap-drop",
    }
)


def _compose_run(line: str) -> tuple[str, list[str]] | None:
    """(service, command tokens) of a `$(COMPOSE) ... run ... <service> <command>` recipe line."""
    if "$(COMPOSE)" not in line:
        return None
    tokens = shlex.split(line)
    if "run" not in tokens:
        return None
    rest = tokens[tokens.index("run") + 1 :]
    # A make variable before the service name expands to options (e.g. `$(TEST_SOURCE_MOUNT)`).
    while rest and (rest[0].startswith("-") or rest[0].startswith("$(")):
        option = rest.pop(0)
        if option in _RUN_VALUE_OPTIONS:
            rest.pop(0)
    return (rest[0], rest[1:]) if rest else None


def _makefile_runs() -> Iterator[tuple[str, str, list[str]]]:
    for target, lines in _makefile_recipes().items():
        for line in lines:
            run = _compose_run(line)
            if run is not None:
                yield target, run[0], run[1]


def _step_module(step: ast.Call) -> str:
    """
    Return the dotted module one `Step(name, module("<dotted>", ...))` runs.

    Read from the `module(...)` call, not from the step's name: since Story 24.1 a step's name is
    its ledger suffix and its place in the frozen summary line, and one of them (`build_candles`)
    runs a module in another context (`candles.rebuild`). A step written any other way fails here
    rather than dropping out of the image check unseen.
    """
    assert len(step.args) >= 2, f"nightly Step(...) with no command: {ast.unparse(step)}"
    inner = step.args[1]
    shape = f"nightly step {ast.unparse(step.args[0])}"
    assert isinstance(inner, ast.Call), (
        f"{shape} does not build its argv with module(...): teach this parser the new shape, "
        "or its image closure goes unchecked"
    )
    assert getattr(inner.func, "id", None) == "module", f"{shape} calls something else than module"
    dotted = inner.args[0] if inner.args else None
    assert isinstance(dotted, ast.Constant), f"{shape} runs a non-literal module"
    assert isinstance(dotted.value, str), f"{shape} runs a non-string module"
    return dotted.value


def _step_modules(module: str) -> list[str]:
    """Return the modules `module` runs as child processes (its `Step(...)` calls)."""
    tree = ast.parse(_MODULES[module].read_text())
    steps = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Step"
    ]
    assert steps, f"{module} no longer builds its chains from Step(...) calls"
    return [_step_module(step) for step in steps]


def _nightly_steps() -> list[str]:
    return _step_modules("archive.nightly")


def _scheduler_steps() -> list[str]:
    """Return the `archive` service's children: its own chains' steps plus the saga's."""
    return _step_modules("archive.scheduler") + _nightly_steps()


# Modules that launch other in-repo modules as separate processes, which `ast` imports miss.
_CHILD_PROCESSES = {"archive.nightly": _nightly_steps, "archive.scheduler": _scheduler_steps}


def _entrypoints() -> list[Entrypoint]:
    services = _compose_services()
    found: list[Entrypoint] = []
    for name, service in services.items():
        command = (
            _checked_command(name, service.command)
            if service.command is not None
            else _dockerfile_cmd(_dockerfile(service))
        )
        module = _module_of(command)
        assert module is not None, f"compose service {name}: no in-repo module in {command!r}"
        found.append(Entrypoint(name, module, f"docker-compose.yml {name}"))
    for target, run_service, argv in _makefile_runs():
        # Checked before the module: a mis-parsed option value (an option not in
        # `_RUN_VALUE_OPTIONS`) must fail here, not drop the recipe from the check.
        assert run_service in services, f"make {target}: unknown compose service {run_service}"
        module = _module_of(shlex.join(argv))
        if module is not None:
            found.append(Entrypoint(run_service, module, f"make {target}"))
    for entry in list(found):
        for child in _CHILD_PROCESSES.get(entry.module, list)():
            found.append(Entrypoint(entry.service, child, f"{entry.source} -> {entry.module}"))
    return sorted(set(found))


_ENTRYPOINTS = _entrypoints()


def _with_parents(module: str) -> list[str]:
    """Return `module` and its parent packages: importing `a.b.c` runs each `__init__`."""
    parts = module.split(".")
    return [".".join(parts[:cut]) for cut in range(1, len(parts) + 1)]


def _in_repo(target: str) -> str | None:
    parts = target.split(".")
    for cut in range(len(parts), 0, -1):
        candidate = ".".join(parts[:cut])
        if candidate in _KNOWN:
            return candidate
    return None


def _dynamic_imports(module: str) -> list[str]:
    """
    Modules `module` loads by name, `importlib.import_module("<literal>")`, which the `ast`
    import scan cannot see (`archive.tools.measure_lag` loads the venue clients this way). A
    non-literal name cannot be checked at all, so it fails here rather than passing unseen.
    """
    tree = ast.parse(_MODULES[module].read_text())
    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        callee = ast.unparse(node.func)
        if callee not in ("importlib.import_module", "import_module"):
            continue
        first = node.args[0]
        literal = first.value if isinstance(first, ast.Constant) else None
        assert isinstance(literal, str), (
            f"{module}: import_module({ast.unparse(first)}) names no literal module, so its "
            "image closure cannot be checked -- load a literal name, or an explicit table of them"
        )
        names.append(literal)
    return names


# Modules a module loads through a `"<module>:<Class>"` string path handed to Nautilus's resolver
# (`StrategyFactory.create(ImportableStrategyConfig(...))`), which neither the `ast` import scan
# nor `_dynamic_imports` can see: the import happens inside `nautilus_trader`. The bots host builds
# every non-dummy paper strategy this way (Story 27.8), so `research` must ship in its image, and
# the catalog replay `bots.signal_replay` runs `DummyStrategy` through `BacktestNode` the same way
# (Story 31.9). `test_every_bots_string_path_is_in_the_table` keeps this table equal to the literals.
_STRING_PATH_IMPORTS: dict[str, tuple[str, ...]] = {
    "bots.infrastructure.nautilus_host": ("research.strategies.candle_pattern_strategy",),
    "bots.signal_replay": ("bots.strategies.dummy",),
}
_STRING_PATH = re.compile(r"[a-z_][\w.]*:[A-Za-z_]\w*")


def import_closure(module: str) -> set[str]:
    """Every in-repo module `module` imports, transitively (function-level imports included)."""
    seen: set[str] = set()
    pending = [module]
    while pending:
        current = pending.pop()
        for name in _with_parents(current):
            if name in _KNOWN and name not in seen:
                seen.add(name)
                targets = [ref.target for ref in imports_of(name, _MODULES[name], _KNOWN)]
                targets.extend(_dynamic_imports(name))
                targets.extend(_STRING_PATH_IMPORTS.get(name, ()))
                pending.extend(
                    resolved for target in targets if (resolved := _in_repo(target)) is not None
                )
    return seen


def _missing(entry: Entrypoint) -> tuple[set[str], Path]:
    dockerfile = _dockerfile(_compose_services()[entry.service])
    needed = {module.split(".")[0] for module in import_closure(entry.module)}
    return needed - _copied_packages(dockerfile), dockerfile


def test_every_service_and_make_target_contributes_entrypoints() -> None:
    services = _compose_services()
    covered = {entry.service for entry in _ENTRYPOINTS}
    assert set(services) <= covered, "a compose service with no parsed entrypoint"
    assert {entry.module for entry in _ENTRYPOINTS} >= {
        "capture.venues.dydx.__main__",
        "capture.venues.bybit.__main__",
        "capture.venues.hyperliquid.__main__",
        "data_api.app",
        "bots.__main__",
        "ranking.__main__",
        "archive.nightly",
        "archive.rebuild_seconds",
        "archive.scheduler",
        "archive.backup_catalog",
        "archive.consolidate_catalog",
        "bot_tui.app",
    }, "the parser lost an entrypoint it found when this test was written"


@pytest.mark.parametrize("entry", _ENTRYPOINTS, ids=lambda e: f"{e.service}:{e.module}")
def test_image_copies_every_package_the_entrypoint_imports(entry: Entrypoint) -> None:
    missing, dockerfile = _missing(entry)
    assert missing == set(), (
        f"{entry.source}: {entry.module} imports {sorted(missing)}, which "
        f"{dockerfile.name} does not COPY"
    )


# pytest options whose value is the next token (`--deselect <node id>`), not a path to collect.
_PYTEST_VALUE_OPTIONS = frozenset(
    {
        "--deselect",
        "--ignore",
        "--ignore-glob",
        "--rootdir",
        "--junitxml",
        "--basetemp",
        "-c",
        "-k",
        "-m",
        "-p",
        "-o",
        "-W",
    }
)


def _pytest_paths(line: str) -> list[str]:
    tokens = shlex.split(line)
    args = tokens[tokens.index("pytest") + 1 :]
    values = {i + 1 for i, token in enumerate(args) if token in _PYTEST_VALUE_OPTIONS}
    return [
        t for i, t in enumerate(args) if i not in values and not t.startswith("-") and "=" not in t
    ]


@pytest.mark.parametrize("target", ["test", "test-live-paper"])
def test_makefile_test_lists_run_inside_an_image_that_ships_them(target: str) -> None:
    runs = [(svc, cmd) for tgt, svc, cmd in _makefile_runs() if tgt == target and "pytest" in cmd]
    assert len(runs) == 1, f"make {target}: expected one pytest run"
    service, command = runs[0]
    copied = _copied_packages(_dockerfile(_compose_services()[service]))
    paths = _pytest_paths(shlex.join(command))
    tops = {path.split("/")[0] for path in paths}
    assert {"tests", "observability"} <= tops, f"make {target} must run tests and observability"
    assert tops <= copied, f"make {target} runs {sorted(tops - copied)}, absent from its image"


def _section(text: str, heading: str) -> str:
    """Return the body of the `## <heading>...` section of a Markdown document."""
    match = re.search(
        rf"^## {re.escape(heading)}.*?$(.*?)(?=^## |\Z)", text, re.DOTALL | re.MULTILINE
    )
    assert match, f"{_CRON_DOC.name} has no section starting '## {heading}'"
    return match.group(1)


def test_the_deploy_checklist_has_no_crontab_line_and_names_the_removal_check() -> None:
    """
    Nightly maintenance is the `archive` service's (Story 25.1b): the checklist installs no crontab
    line anywhere, and its §1 names the check proving the old one is gone.
    """
    text = _CRON_DOC.read_text()
    # Five schedule fields (`7 3 * * *`, `*/5 ...`) then a command, or a `CRON_TZ=` setting.
    schedule = re.compile(r"^\s*(CRON_TZ=|([\d*/,-]+\s+){5}\S)")
    cron = [line for line in text.splitlines() if schedule.match(line)]
    assert cron == [], f"{_CRON_DOC.name} still carries a crontab line: {cron}"
    section = _section(text, "1.")
    assert "crontab -l | grep -E 'make (nightly|consolidate|backup-catalog)'" in section
    assert "archive" in _compose_services(), "the archive service schedules the maintenance"


@pytest.mark.parametrize("raw", [">", "|", "", "- python3", '["python3", "-m", "x"]'])
def test_unsupported_command_forms_fail_loudly(raw: str) -> None:
    with pytest.raises(AssertionError, match="does not parse"):
        _checked_command("svc", raw)


def test_uvicorn_app_is_the_first_non_option_token() -> None:
    assert (
        _module_of("uvicorn --host 127.0.0.1 --reload data_api.app:app --port 9") == "data_api.app"
    )
    assert _module_of("uvicorn data_api.app:app --host 127.0.0.1") == "data_api.app"


def test_closure_follows_imports_transitively() -> None:
    # The dYdX root imports none of these itself; each is reached through the service it builds.
    closure = import_closure("capture.venues.dydx.__main__")
    assert "capture.domain.sampler" in closure
    assert "capture.application.trade_backfill" in closure


def test_an_option_value_is_not_a_collected_path() -> None:
    assert _pytest_paths("python3 -m pytest a/tests --deselect a/tests/t.py::x -q b") == [
        "a/tests",
        "b",
    ]


def test_running_a_package_checks_its_main_module() -> None:
    assert _module_of("python3 -m bots") == "bots.__main__"
    assert _module_of("python3 -m capture.venues.bybit") == "capture.venues.bybit.__main__"
    assert _module_of("python3 -m ranking") == "ranking.__main__"
    assert _module_of("python3 -m archive.nightly --venue DYDX") == "archive.nightly"
    assert "bots.infrastructure.nautilus_host" in import_closure("bots.__main__")


def test_closure_follows_a_literal_import_module_call() -> None:
    assert {
        "capture.venues.bybit.client",
        "capture.venues.hyperliquid.client",
        "capture.venues.dydx.client",
    } <= import_closure("archive.tools.measure_lag")


def _docstrings(tree: ast.Module) -> set[int]:
    """Return the `id`s of the module's, classes' and functions' docstring constants."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        first = node.body[0] if node.body else None
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            found.add(id(first.value))
    return found


def _bound_names(node: ast.stmt) -> set[str]:
    """Return the names one top-level statement binds (a definition, assignment or import)."""
    if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
        return {node.name}
    if isinstance(node, ast.ImportFrom | ast.Import):
        return {alias.asname or alias.name for alias in node.names}
    targets = node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", None)]
    return {target.id for target in targets if isinstance(target, ast.Name)}


def _top_level_names(module: str) -> set[str]:
    """Return the names `module` defines at top level: what a resolver's `getattr` can find."""
    return {
        name for node in ast.parse(_MODULES[module].read_text()).body for name in _bound_names(node)
    }


def _is_string_path(text: str) -> bool:
    """`"<in-repo module>:<a name it defines>"` -- not a Redis key such as `"bots:status"`."""
    if not _STRING_PATH.fullmatch(text):
        return False
    module, _, name = text.partition(":")
    return module in _KNOWN and name in _top_level_names(module)


def string_path_modules(module: str) -> set[str]:
    """Return the in-repo modules named by a `"<module>:<Name>"` literal (docstrings aside)."""
    tree = ast.parse(_MODULES[module].read_text())
    docstrings = _docstrings(tree)
    return {
        node.value.partition(":")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and _is_string_path(node.value)
    }


def test_every_bots_string_path_is_in_the_table() -> None:
    found = {
        module: tuple(sorted(paths))
        for module in _MODULES
        if module.split(".")[0] == "bots" and (paths := string_path_modules(module))
    }
    assert found == _STRING_PATH_IMPORTS, (
        "a bots module loads an in-repo module by string path: name it in _STRING_PATH_IMPORTS "
        "so the image closure follows it"
    )


def test_the_bots_closure_follows_the_string_path_strategy() -> None:
    closure = import_closure("bots.__main__")
    assert "research.strategies.candle_pattern_strategy" in closure
    assert "research.application.backtest_runner" not in closure, "only the strategy is loaded"


def test_a_string_path_names_a_defined_attribute_of_an_in_repo_module() -> None:
    assert _is_string_path("research.strategies.candle_pattern_strategy:CandlePatternStrategy")
    assert not _is_string_path("bots:status"), "a Redis channel, not a string path"
    assert not _is_string_path("research.strategies.candle_pattern_strategy:Missing")
    assert not _is_string_path("nautilus_trader.config:ImportableStrategyConfig")
