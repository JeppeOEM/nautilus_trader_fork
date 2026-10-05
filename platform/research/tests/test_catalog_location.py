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
"""The runners' default catalog root is `$CATALOG_PATH`, else a cwd-independent path (DW-200)."""

from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from research.strategies import backtest_candle_pattern
from research.strategies import backtest_dydx
from research.strategies import backtest_ofi
from research.strategies import backtest_snapshot
from research.strategies.catalog_location import default_catalog_path


_PLATFORM = Path(__file__).resolve().parents[2]


def test_catalog_path_env_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CATALOG_PATH", "/app/catalog")
    assert default_catalog_path() == "/app/catalog"


@pytest.mark.parametrize("env", [None, ""])
@pytest.mark.parametrize("cwd", ["platform", "repo", "elsewhere"])
def test_without_env_the_default_is_platform_data_catalog_from_any_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    env: str | None,
    cwd: str,
) -> None:
    if env is None:
        monkeypatch.delenv("CATALOG_PATH", raising=False)
    else:
        monkeypatch.setenv("CATALOG_PATH", env)  # empty counts as unset
    monkeypatch.chdir({"platform": _PLATFORM, "repo": _PLATFORM.parent, "elsewhere": tmp_path}[cwd])

    resolved = Path(default_catalog_path())

    assert resolved.is_absolute()
    assert resolved == _PLATFORM / "data" / "catalog"


class _Intercepted(Exception):
    """Raised by the stub once it has seen the path, so the run stops before any I/O."""


# Each runner's first collaborator that receives the catalog path, and the call that reaches it.
_RUNNERS: dict[str, tuple[ModuleType, str, Callable[[], object]]] = {
    "dydx": (
        backtest_dydx,
        "ParquetDataCatalog",
        lambda: backtest_dydx.run(symbols=["BTC-USD-PERP.DYDX"]),
    ),
    "snapshot": (backtest_snapshot, "ParquetDataCatalog", lambda: backtest_snapshot.run()),
    "ofi": (backtest_ofi, "run_snapshot_backtest", lambda: backtest_ofi.run()),
    "candle_pattern": (backtest_candle_pattern, "RunSpec", lambda: backtest_candle_pattern.run()),
}


@pytest.mark.parametrize("runner", list(_RUNNERS))
def test_every_runner_uses_the_default_resolved_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    runner: str,
) -> None:
    # Set after import: a string default fixed at import time (two runners' old cwd-relative
    # one) could not see it; only a call-time default_catalog_path() can.
    sentinel = str(tmp_path / "sentinel-catalog")
    monkeypatch.setenv("CATALOG_PATH", sentinel)
    module, collaborator, call = _RUNNERS[runner]
    seen: list[object] = []

    def _record(*args: object, **kwargs: object) -> None:
        seen.append(kwargs["catalog_path"] if "catalog_path" in kwargs else args[0])
        raise _Intercepted

    monkeypatch.setattr(module, collaborator, _record)

    with pytest.raises(_Intercepted):
        call()

    assert seen == [sentinel]


@pytest.mark.parametrize(
    "call",
    [
        lambda path: backtest_snapshot.run(symbol="NOPE-USD-PERP.DYDX", catalog_path=path),
        # Through the shared `snapshot_backtest.run` (OFIStrategy, the candle-pattern runner).
        lambda path: backtest_ofi.run(symbol="NOPE-USD-PERP.DYDX", catalog_path=path),
    ],
    ids=["snapshot", "ofi"],
)
def test_runners_name_a_symbol_missing_from_the_catalog(
    tmp_path: Path,
    call: Callable[[str], object],
) -> None:
    with pytest.raises(ValueError) as info:
        call(str(tmp_path))
    assert "'NOPE-USD-PERP.DYDX' not found" in str(info.value)
    assert str(tmp_path) in str(info.value)
