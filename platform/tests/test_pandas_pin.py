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
The pandas 3 override holds, and only upstream calls raise its two ignored deprecations (DW-244).

`platform/requirements.txt` pins pandas 3 over nautilus_trader's own `<3.0.0`, a deliberate,
proven override documented beside the pin. These guards hold it: the pin and the upstream cap it
overrides (the cap lifting fails the guard, so the override note is revisited); the two places
that ignore nautilus_trader's own deprecations by message (`Makefile`'s `PANDAS4_WARNINGS`, the
notebook harness's `UPSTREAM_PANDAS4_DEPRECATIONS`) name the same messages, and both image test
runs pass them; no platform source makes either call, so those filters hide no platform call the
scan can see; and, inside an image, the installed pandas is the pin and `pip check` reports the
one sanctioned conflict against the installed nautilus_trader's own cap, and nothing else. A host
run from the checkout (pandas 2.3.3, from `uv.lock`) skips that last guard: only an image installs
the pin.

Known limit: the scan reads literals only, so a day alias reaching pandas through a variable, an
f-string or `**kwargs` is not seen, and a third-party library's own call is out of its reach. Its
warning would then be ignored like nautilus_trader's. Upgrade path: none needed while the platform
passes frequencies as literals; a wrapper that builds frequency strings joins `_FREQ_CALLS`.
"""

import ast
import importlib.metadata
import re
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path

import pandas as pd
import pytest
from tests._source_tree import PLATFORM_DIR
from tests._source_tree import REPO_ROOT
from tests._source_tree import python_modules


REQUIREMENTS = PLATFORM_DIR / "requirements.txt"
PYPROJECT = REPO_ROOT / "pyproject.toml"
# nautilus_trader 1.229.0's own requirement, overridden by the pin.
UPSTREAM_PANDAS_REQUIREMENT = "pandas>=2.3.3,<3.0.0"
# The same cap as nautilus_trader's installed wheel metadata spells it (`Requires-Dist`).
INSTALLED_PANDAS_REQUIREMENT = "pandas (>=2.3.3,<3.0.0)"
_PIN = re.compile(r"^pandas==(3\.\d+\.\d+)$", re.MULTILINE)
# Any requirement line naming pandas (with extras, a marker or another specifier included).
_PANDAS_LINE = re.compile(r"^\s*pandas\b(?!-)", re.MULTILINE | re.IGNORECASE)
MAKEFILE = PLATFORM_DIR / "Makefile"
NOTEBOOK_HARNESS = PLATFORM_DIR / "research" / "tests" / "test_notebooks.py"
_PANDAS4_ERROR = "error::pandas.errors.Pandas4Warning"
_IGNORED_PANDAS4 = re.compile(r"ignore:(.*):pandas\.errors\.Pandas4Warning")
# Characters make or the shell would rewrite inside the recipe's double quotes, plus the ":" that
# pytest splits a -W spec on: a message holding one would not reach pytest as written.
_UNSAFE_IN_MESSAGE = frozenset(':"$`\\')
# Inside an image the tests are a copy under /app, which holds no docker-compose.yml (see
# `_source_tree`); a checkout always has one beside its tests/, wherever PLATFORM_SOURCE_DIR points.
# A container run of the mounted checkout itself (`pytest /src/platform/tests`) still installed the
# image's pin, so docker's own marker counts too.
IN_IMAGE = (
    not (Path(__file__).resolve().parents[1] / "docker-compose.yml").is_file()
    or Path("/.dockerenv").exists()
)

# Where pandas reads a frequency alias: a positional argument of these, or `freq=`/`rule=`/`window=`.
_FREQ_CALLS = frozenset(
    {
        "floor",
        "ceil",
        "round",
        "resample",
        "rolling",
        "snap",
        "asfreq",
        "to_offset",
        "to_period",
        "date_range",
        "bdate_range",
        "period_range",
        "timedelta_range",
        "interval_range",
        "Period",
        "PeriodIndex",
    },
)
_FREQ_KEYWORDS = frozenset({"freq", "rule", "window"})
# The lowercase day alias pandas 3 deprecates, bare or with a multiple ("d", "1d", "7d").
_DAY_ALIAS = re.compile(r"^\d*d$")


def _pinned_pandas() -> str:
    text = REQUIREMENTS.read_text()
    pins = _PIN.findall(text)
    assert len(pins) == 1, f"{REQUIREMENTS} must pin exactly one pandas==3.x, found {pins}"
    lines = [line.strip() for line in text.splitlines() if _PANDAS_LINE.match(line)]
    assert lines == [f"pandas=={pins[0]}"], (
        f"{REQUIREMENTS} must name pandas only in its one pin, found {lines}"
    )
    return pins[0]


def _sanctioned_pip_check_line(version: str) -> str:
    nautilus = importlib.metadata.version("nautilus_trader")
    return (
        f"nautilus-trader {nautilus} has requirement pandas<3.0.0,>=2.3.3, "
        f"but you have pandas {version}."
    )


def test_requirements_pin_pandas_3_over_the_upstream_cap() -> None:
    _pinned_pandas()
    dependencies = tomllib.loads(PYPROJECT.read_text())["project"]["dependencies"]
    assert UPSTREAM_PANDAS_REQUIREMENT in dependencies, (
        f"{PYPROJECT} no longer declares {UPSTREAM_PANDAS_REQUIREMENT} among its runtime "
        "dependencies: nautilus_trader moved its "
        "pandas bound, so revisit the override note beside the pin in requirements.txt, the "
        "sanctioned `pip check` line here and the Makefile's PANDAS4_WARNINGS"
    )


def _func_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None


def _is_day_alias(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and _DAY_ALIAS.match(node.value) is not None
    )


def _passes_day_alias(call: ast.Call) -> bool:
    if any(kw.arg in _FREQ_KEYWORDS and _is_day_alias(kw.value) for kw in call.keywords):
        return True
    return _func_name(call) in _FREQ_CALLS and any(_is_day_alias(arg) for arg in call.args)


def _deprecated_calls(path: Path) -> list[str]:
    hits = []
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        # A reference counts as a call: `default_factory=pd.Timestamp.utcnow` calls it later.
        utcnow = isinstance(node, ast.Attribute) and node.attr == "utcnow"
        alias = isinstance(node, ast.Call) and _passes_day_alias(node)
        if isinstance(node, ast.expr) and (utcnow or alias):
            hits.append(f"{path.relative_to(PLATFORM_DIR)}:{node.lineno}")
    return hits


def test_no_platform_source_makes_an_ignored_upstream_call() -> None:
    hits = [hit for path in python_modules().values() for hit in _deprecated_calls(path)]
    assert not hits, (
        "these calls raise a deprecation the image runs ignore by message as nautilus_trader's own "
        "(`.utcnow()`: use `Timestamp.now('UTC')`/`datetime.now(UTC)`; the 'd' alias: use 'D'), so "
        f"they would never be reported: {hits}"
    )


def _harness_messages() -> set[str]:
    """`UPSTREAM_PANDAS4_DEPRECATIONS` of the notebook harness, read from source (no import)."""
    for node in ast.parse(NOTEBOOK_HARNESS.read_text()).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "UPSTREAM_PANDAS4_DEPRECATIONS"
            for target in node.targets
        ):
            return set(ast.literal_eval(node.value))
    raise AssertionError(f"{NOTEBOOK_HARNESS} defines no UPSTREAM_PANDAS4_DEPRECATIONS")


def _recipe(target: str) -> str:
    joined = MAKEFILE.read_text().replace("\\\n", " ")
    match = re.search(rf"^{re.escape(target)}:.*\n((?:\t.*\n)+)", joined, re.MULTILINE)
    assert match, f"Makefile has no `{target}` target"
    return match.group(1)


def _pandas4_filters() -> list[str]:
    """Return the `-W` values of the Makefile's `PANDAS4_WARNINGS`, in order, as the shell passes."""
    joined = MAKEFILE.read_text().replace("\\\n", " ")
    match = re.search(r"^PANDAS4_WARNINGS :=(.*)$", joined, re.MULTILINE)
    assert match, f"{MAKEFILE} defines no PANDAS4_WARNINGS"
    tokens = shlex.split(match.group(1))
    assert len(tokens) % 2 == 0, f"PANDAS4_WARNINGS must be -W pairs only, got {tokens}"
    assert set(tokens[::2]) == {"-W"}, f"PANDAS4_WARNINGS must be -W pairs only, got {tokens}"
    return tokens[1::2]


def test_the_makefile_and_the_notebook_harness_ignore_the_same_messages() -> None:
    first, *ignores = _pandas4_filters()
    # A later -W wins, so the error comes first and every other filter only narrows it.
    assert first == _PANDAS4_ERROR, f"PANDAS4_WARNINGS must open with -W {_PANDAS4_ERROR}"
    matches = [_IGNORED_PANDAS4.fullmatch(spec) for spec in ignores]
    assert all(matches), f"every later filter must be ignore:<msg>:Pandas4Warning, got {ignores}"
    makefile = {match.group(1) for match in matches if match}
    assert len(makefile) == len(ignores), f"PANDAS4_WARNINGS repeats a message: {ignores}"
    assert makefile == _harness_messages()
    assert not any(_UNSAFE_IN_MESSAGE & set(message) for message in makefile)


def test_both_image_test_runs_pass_the_pandas4_filters() -> None:
    for target in ("test", "test-live-paper"):
        # Straight after pytest: before the service name, compose would read them as its own.
        assert "python3 -m pytest $(PANDAS4_WARNINGS) " in _recipe(target), (
            f"make {target} does not pass PANDAS4_WARNINGS to pytest"
        )
    assert "docker-compose.test.yml" in _recipe("test"), "make test runs under the runtime limit"


def test_the_image_installs_the_pin_with_one_sanctioned_conflict() -> None:
    if not IN_IMAGE:
        pytest.skip(
            f"host run from the checkout: pandas here is {pd.__version__} (uv.lock's), not the "
            f"pinned {_pinned_pandas()}; this guard runs inside the images (`make test`, "
            "`make test-live-paper`)"
        )
    pinned = _pinned_pandas()
    assert pd.__version__ == pinned
    requires = importlib.metadata.requires("nautilus_trader") or []
    assert INSTALLED_PANDAS_REQUIREMENT in requires, (
        f"the installed nautilus_trader requires {[r for r in requires if 'pandas' in r]}: revisit "
        "the override note beside the pin and the sanctioned `pip check` line"
    )
    checked = subprocess.run(
        [sys.executable, "-m", "pip", "check"],
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [line for line in checked.stdout.splitlines() if line.strip()]
    assert lines == [_sanctioned_pip_check_line(pinned)], (
        f"`pip check` must report only the sanctioned nautilus/pandas conflict, got: {lines}"
        + (f"\nstderr: {checked.stderr}" if checked.stderr else "")
    )
