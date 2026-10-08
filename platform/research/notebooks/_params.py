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
Every notebook's Parameters cell (Story 27.2): the one place a notebook's inputs and their defaults
live, so the same file runs against the test fixture (`research/tests/test_notebooks.py` sets the
environment) and against the real archive on the user's machine (nothing set: `platform/data/`).

Importing this module puts `platform/` on `sys.path` (a Jupyter kernel starts in
`research/notebooks/`, where the platform packages are not importable); it is appended, so an
environment that already imports them (the collector image's `/app`) keeps its own.
"""

import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import plotly.io as pio
from plotly.io.base_renderers import ExternalRenderer


PLATFORM_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = PLATFORM_DIR / "data"
DEFAULT_INSTRUMENTS = ("BTC-USD-PERP.DYDX", "ETH-USD-PERP.DYDX")
HEADLESS_RENDERER = "headless"
SETTING_PREFIX = "NOTEBOOK_"

if str(PLATFORM_DIR) not in sys.path:
    sys.path.append(str(PLATFORM_DIR))


class _HeadlessRenderer(ExternalRenderer):
    """
    Serialise the figure and show nothing: `fig.show()` in a headless run still proves the figure
    is valid JSON, without a browser, IPython or kaleido. Plotly's own `json` renderer is a
    mimetype renderer, which raises without IPython (the collector image `make test` runs in has
    none) and prints the whole figure when IPython is present.
    """

    def render(self, fig: dict) -> None:
        pio.to_json(fig, validate=False)


@dataclass(frozen=True)
class Params:
    """
    A notebook's inputs. Invariant: `start`/`end` are always set (MEM-01: every read in a notebook
    is bounded by them) and `instruments` is never empty.
    """

    catalog_path: str
    candles_dir: str
    metrics_db_path: str
    errors_dir: str
    backtest_reports_dir: str
    instruments: tuple[str, ...]
    start: str
    end: str

    @classmethod
    def from_env(cls) -> "Params":
        """
        Read `CATALOG_PATH`, `CANDLES_DIR`, `METRICS_DB_PATH`, `ERRORS_DIR`,
        `BACKTEST_REPORTS_DIR` (where a backtest's report folder is saved,
        `research.application.backtest_report`: beside the archive, never in it), `INSTRUMENTS`
        (comma-separated) and `START`/`END` (ISO dates or timestamps, naive = UTC), each defaulting
        to the `platform/data/` layout, the two dYdX majors and yesterday 00:00 -> today 00:00 UTC.
        `NOTEBOOK_HEADLESS=1` switches plotly to a renderer that builds figures but shows none.
        """
        today = datetime.now(UTC).date()
        listed = os.environ.get("INSTRUMENTS")
        # Each id once, in order: a repeated id would overwrite its own per-instrument results.
        instruments = tuple(
            dict.fromkeys(i.strip() for i in (listed or "").split(",") if i.strip())
        )
        if listed is not None and listed.strip() and not instruments:
            raise ValueError(f"INSTRUMENTS={listed!r} names no instrument")
        if os.environ.get("NOTEBOOK_HEADLESS") == "1":
            pio.renderers[HEADLESS_RENDERER] = _HeadlessRenderer()
            pio.renderers.default = HEADLESS_RENDERER
        return cls(
            catalog_path=os.environ.get("CATALOG_PATH", str(DATA_DIR / "catalog")),
            candles_dir=os.environ.get("CANDLES_DIR", str(DATA_DIR / "candles")),
            metrics_db_path=os.environ.get(
                "METRICS_DB_PATH", str(DATA_DIR / "metrics" / "metrics.db")
            ),
            errors_dir=os.environ.get("ERRORS_DIR", str(DATA_DIR / "errors")),
            backtest_reports_dir=os.environ.get(
                "BACKTEST_REPORTS_DIR", str(DATA_DIR / "backtest_reports")
            ),
            instruments=instruments or DEFAULT_INSTRUMENTS,
            # An empty value counts as unset (`START= jupyter lab ...`).
            start=os.environ.get("START") or (today - timedelta(days=1)).isoformat(),
            end=os.environ.get("END") or today.isoformat(),
        )


def _refuse_constant(constant: str) -> object:
    """`json.loads`' `parse_constant`: JSON has no NaN or infinity, Python's parser accepts them."""
    raise ValueError(f"{constant} is not a finite JSON number")


def setting[T](name: str, default: T) -> T:
    """
    Return a notebook constant the test harness may shrink to fit its fixture (Story 27.5): the
    JSON in the environment variable `NOTEBOOK_<name>` when it is set and non-empty, else
    `default`, so a notebook never reads the environment itself and needs no runtime branch.

    Invariant: the value returned has `default`'s type -- a `bool` only for a `bool` default, an
    `int` also accepted (as a float) for a `float` default -- else `ValueError` naming the variable;
    a blank value is unset, and invalid JSON (e.g. an unquoted string) or a non-finite number
    (`NaN`, `Infinity`) raises `ValueError` too. A default must be a JSON type (`str`, `int`,
    `float`, `bool`, `list`, `dict`): a `tuple` or `None` default could never be overridden.
    Known limit: only the top-level type is checked (a list's items, a dict's values are not); the
    consumer validates the rest (`RunSpec`, `evaluation.grid_points`, `walk_forward.folds` all do);
    upgrade path: a schema per setting if a notebook ever takes nested input the consumer cannot
    check.
    """
    variable = f"{SETTING_PREFIX}{name}"
    raw = (os.environ.get(variable) or "").strip()
    if not raw:
        return default
    try:
        value = json.loads(raw, parse_constant=_refuse_constant)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"{variable}={raw!r} is not JSON (a string must be quoted: '\"BTC-USD-PERP.DYDX\"')"
        ) from error
    except ValueError as error:
        raise ValueError(f"{variable}={raw!r}: {error}") from error
    if isinstance(default, bool) or isinstance(value, bool):
        matches = isinstance(default, bool) and isinstance(value, bool)
    elif isinstance(default, float):
        matches = isinstance(value, int | float)
        value = float(value) if matches else value
    else:
        matches = type(value) is type(default)
    if not matches:
        raise ValueError(
            f"{variable}={raw!r} is a {type(value).__name__}, the default is a "
            f"{type(default).__name__}"
        )
    return value
