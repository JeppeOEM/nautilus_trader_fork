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
    instruments: tuple[str, ...]
    start: str
    end: str

    @classmethod
    def from_env(cls) -> "Params":
        """
        Read `CATALOG_PATH`, `CANDLES_DIR`, `METRICS_DB_PATH`, `ERRORS_DIR`, `INSTRUMENTS`
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
            instruments=instruments or DEFAULT_INSTRUMENTS,
            # An empty value counts as unset (`START= jupyter lab ...`).
            start=os.environ.get("START") or (today - timedelta(days=1)).isoformat(),
            end=os.environ.get("END") or today.isoformat(),
        )
