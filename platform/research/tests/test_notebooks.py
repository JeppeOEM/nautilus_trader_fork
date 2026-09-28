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
Every numbered notebook runs, and every notebook is a reviewed pair (Story 27.2).

Each `research/notebooks/<nn>_<name>.py` (jupytext percent format, the source of truth) is executed
with `runpy` against the session fixture archive (`fixture_catalog`) under
`warnings.simplefilter("error")` (TEST-04) with plotly headless, and must finish in under 60 s. The
pairing check holds every `.ipynb` to its `.py` twin: no stored output or execution count, and the
same cells as jupytext reads from the `.py`. It needs jupytext (installed in the collector image
through `platform/requirements.txt` and in the repo `.venv`); a host python without it skips that
one test, never the runs. The fixture's planted defects are asserted against
`research.application.inspection` here too, so what the notebook claims to show is checked.
"""

import json
import math
import runpy
import time
import warnings
from datetime import UTC
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.io as pio
import pytest
from kernel.clocks import NS_PER_S

from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.data import TradeTick
from research.application import inspection
from research.application.frames import CatalogFrames
from research.application.ports import window_ns
from research.tests.fixture_catalog import DATA_END_NS
from research.tests.fixture_catalog import DATA_START_NS
from research.tests.fixture_catalog import FixturePaths
from research.tests.source_tree import SOURCE_TREE


NOTEBOOKS_DIR = Path(__file__).resolve().parents[1] / "notebooks"
RUN_SECONDS_LIMIT = 60.0

# `.ipynb` with no `.py` twin -> the story whose `done` retires it (it is deleted by then). Empty
# since Story 27.7 deleted `candlestick_pattern_scanner.ipynb`, the last legacy notebook.
LEGACY_NOTEBOOKS_UNTIL: dict[str, str] = {}


def _iso(ns: int) -> str:
    """Return a naive-UTC ISO timestamp (the `START`/`END` form) for a whole second in ns."""
    if ns % NS_PER_S:
        raise ValueError(f"{ns} is not a whole second: START/END would silently truncate it")
    return datetime.fromtimestamp(ns // NS_PER_S, tz=UTC).replace(tzinfo=None).isoformat()


# The OFI parameters that trade on the fixture's ten minutes (as `test_backtest_runner.py`'s): no
# warm-up, two-snapshot OFI sums z-scored over five, and minute EMAs that warm within the window.
_FIXTURE_OFI = {
    "trade_size": "0.01",
    "warmup_seconds": 0,
    "ofi_window": 2,
    "ofi_zscore_window": 5,
    "ofi_threshold": 0.5,
    "trend_ema_fast": 2,
    "trend_ema_slow": 3,
}

# Per-notebook environment on top of `FixturePaths.env()`, applied by `_run` (so the parametrized
# run and each notebook's namespace test see the same inputs). A notebook's defaults are sized for
# the real archive; these shrink them to the fixture through its Parameters cell
# (`_params.setting`: JSON in `NOTEBOOK_<NAME>`), never by editing the notebook.
NOTEBOOK_ENV: dict[str, dict[str, str]] = {
    # The fixture's data window (not its two whole days), a venue-timed instrument clear of the dYdX
    # BTC outage, OFI parameters that trade, a 2 x 2 grid of them, two folds, and a statistic
    # defined on ten minutes of trades (every return statistic needs two UTC days of PnL).
    "04_backtest_evaluation.py": {
        "START": _iso(DATA_START_NS),
        "END": _iso(DATA_END_NS),
        "NOTEBOOK_INSTRUMENT": json.dumps("BTC-USD-PERP.HYPERLIQUID"),
        "NOTEBOOK_PARAMS": json.dumps(_FIXTURE_OFI),
        "NOTEBOOK_GRID": json.dumps({"ofi_threshold": [0.5, 1.0], "ofi_window": [2, 3]}),
        "NOTEBOOK_N_FOLDS": json.dumps(2),
        "NOTEBOOK_SELECT_BY": json.dumps("expectancy"),
    },
    # The same run and 2 x 2 grid as 04, 200 paths, and minute returns in blocks of three (the
    # fixture's ten minutes hold ten returns; they straddle UTC midnight, so Nautilus's daily-binned
    # Sharpe has its two bins and every section draws).
    "05_monte_carlo.py": {
        "START": _iso(DATA_START_NS),
        "END": _iso(DATA_END_NS),
        "NOTEBOOK_INSTRUMENT": json.dumps("BTC-USD-PERP.HYPERLIQUID"),
        "NOTEBOOK_PARAMS": json.dumps(_FIXTURE_OFI),
        "NOTEBOOK_GRID": json.dumps({"ofi_threshold": [0.5, 1.0], "ofi_window": [2, 3]}),
        "NOTEBOOK_N_PATHS": json.dumps(200),
        "NOTEBOOK_RETURN_PERIOD_S": json.dumps(60),
        "NOTEBOOK_BLOCK_LEN": json.dumps(3),
    },
    # The fixture's ten minutes (not its two whole days, so the grids are the data), 1 m and 5 m
    # bars plus a size the store does not keep (skipped with a line), an EMA that warms within the
    # minutes, horizons the ten bars can reach, and a hit window of a few bars.
    "06_candlestick_scanner.py": {
        "START": _iso(DATA_START_NS),
        "END": _iso(DATA_END_NS),
        "NOTEBOOK_TIMEFRAMES": json.dumps([60, 300, 45]),
        "NOTEBOOK_EMA_LEN": json.dumps(3),
        "NOTEBOOK_HORIZONS": json.dumps([1, 2, 3]),
        "NOTEBOOK_WINDOW_BARS": json.dumps(5),
    },
}


def _numbered() -> list[Path]:
    return sorted(NOTEBOOKS_DIR.glob("[0-9]*_*.py"))


def test_there_is_a_numbered_notebook_to_run() -> None:
    assert [p.name for p in _numbered()][:1] == ["01_catalog_inspection.py"]


def _run(notebook: Path, fixture: FixturePaths, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Run one notebook as the test harness does and return its namespace."""
    for name, value in (fixture.env() | NOTEBOOK_ENV.get(notebook.name, {})).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("NOTEBOOK_HEADLESS", "1")
    # A Jupyter kernel starts in the notebooks directory, so `_params` imports from there.
    monkeypatch.syspath_prepend(str(NOTEBOOKS_DIR))
    # `_params` switches plotly's default renderer; restore it for the rest of the session.
    monkeypatch.setattr(pio.renderers, "default", pio.renderers.default)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        return runpy.run_path(str(notebook), run_name="__main__")


@pytest.mark.parametrize("notebook", _numbered(), ids=lambda path: path.name)
def test_notebook_runs_against_the_fixture(
    notebook: Path, fixture_archive: FixturePaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = time.monotonic()
    _run(notebook, fixture_archive, monkeypatch)
    assert time.monotonic() - started < RUN_SECONDS_LIMIT


def test_the_catalog_inspection_notebook_shows_every_planted_defect(
    fixture_archive: FixturePaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the notebook itself computed (its namespace), not a re-run of `inspection`."""
    shown = _run(NOTEBOOKS_DIR / "01_catalog_inspection.py", fixture_archive, monkeypatch)
    defects = fixture_archive.defects
    overview = shown["overview"].set_index("instrument_id")
    assert overview[inspection.LIKELY_OUTAGE].to_dict() == {
        iid: int(iid == defects.outage_instrument) for iid in fixture_archive.instruments
    }
    assert overview[inspection.QUIET_MARKET].to_dict() == {
        iid: int(iid == defects.quiet_instrument) for iid in fixture_archive.instruments
    }
    assert overview["crossed"].to_dict() == {
        iid: int(iid == defects.crossed_instrument) for iid in fixture_archive.instruments
    }
    assert set(overview["provisional"]) == {1}
    assert set(overview["verified"]) == {1}
    assert set(overview["fold_days_disagreeing"]) == {0}
    assert bool(overview["precision_uniform"].all())
    assert shown["ledger"].state == inspection.LEDGER_RECORDS


def _cells(cells: list) -> list[tuple[str, str]]:
    return [(cell.cell_type, cell.source) for cell in cells]


def _pairing_problems(jupytext: object, nbformat: object) -> list[str]:
    problems = [
        f"{py.name}: no .ipynb twin (run `make notebooks`)"
        for py in _numbered()
        if not py.with_suffix(".ipynb").exists()
    ]
    for ipynb in sorted(NOTEBOOKS_DIR.glob("*.ipynb")):
        if ipynb.name in LEGACY_NOTEBOOKS_UNTIL:
            continue
        twin = ipynb.with_suffix(".py")
        if not twin.exists():
            problems.append(f"{ipynb.name}: no .py twin")
            continue
        notebook = nbformat.read(str(ipynb), as_version=4)  # type: ignore[attr-defined]
        code = [cell for cell in notebook.cells if cell.cell_type == "code"]
        if any(cell.get("outputs") or cell.get("execution_count") is not None for cell in code):
            problems.append(f"{ipynb.name}: stored outputs or execution counts")
        if _cells(notebook.cells) != _cells(jupytext.read(str(twin)).cells):  # type: ignore[attr-defined]
            problems.append(f"{ipynb.name}: cells differ from {twin.name} (run `make notebooks`)")
    return problems


def test_every_notebook_is_an_output_free_twin_of_its_py() -> None:
    jupytext = pytest.importorskip(
        "jupytext",
        reason="the pairing check reads the .py with jupytext: installed in the collector image "
        "(platform/requirements.txt) and the repo .venv, absent from a bare host python",
    )
    nbformat = pytest.importorskip("nbformat", reason="installed with jupytext")
    assert _pairing_problems(jupytext, nbformat) == []


def test_every_legacy_exemption_is_still_needed() -> None:
    needed = {
        name
        for name in LEGACY_NOTEBOOKS_UNTIL
        if (NOTEBOOKS_DIR / name).exists()
        and not (NOTEBOOKS_DIR / name).with_suffix(".py").exists()
    }
    assert sorted(set(LEGACY_NOTEBOOKS_UNTIL) - needed) == [], "no longer needed: delete them"


@pytest.mark.parametrize(("name", "story"), sorted(LEGACY_NOTEBOOKS_UNTIL.items()))
def test_legacy_notebook_exemption_expires_with_its_story(name: str, story: str) -> None:
    reason = SOURCE_TREE.unknown_or_done(story, SOURCE_TREE.story_statuses())
    assert reason is None, f"{name}: {reason} -- the notebook should be gone"


# --- The fixture's defects, as inspection reports them ---------------------------------------


def _window(fixture: FixturePaths) -> tuple[int, int]:
    return window_ns(fixture.start, fixture.end)


def _gaps(fixture: FixturePaths, iid: str) -> list[tuple[str, int, int]]:
    start, end = _window(fixture)
    frames = CatalogFrames(fixture.catalog_path, fixture.candles_dir)
    report = inspection.gap_report(
        frames.seconds(iid, start=start, end=end)["ts_event"],
        frames.trades(iid, start=start, end=end)["ts_event"],
        inspection.ts_events(frames.objects(MarkPriceUpdate, iid, start=start, end=end)),
    )
    return list(zip(report["kind"], report["start_ns"], report["end_ns"], strict=True))


def test_the_fixture_outage_is_the_one_likely_outage(fixture_archive: FixturePaths) -> None:
    defects = fixture_archive.defects
    assert _gaps(fixture_archive, defects.outage_instrument) == [
        (inspection.LIKELY_OUTAGE, *defects.outage)
    ]


def test_the_fixture_quiet_market_is_not_an_outage(fixture_archive: FixturePaths) -> None:
    defects = fixture_archive.defects
    assert _gaps(fixture_archive, defects.quiet_instrument) == [
        (inspection.QUIET_MARKET, *defects.quiet)
    ]


def test_no_other_instrument_has_a_gap(fixture_archive: FixturePaths) -> None:
    defects = fixture_archive.defects
    planted = {defects.outage_instrument, defects.quiet_instrument}
    for iid in sorted(set(fixture_archive.instruments) - planted):
        assert _gaps(fixture_archive, iid) == [], iid


def test_the_fixture_days_are_verified_then_provisional(fixture_archive: FixturePaths) -> None:
    defects = fixture_archive.defects
    days = inspection.day_status(
        fixture_archive.candles_dir,
        fixture_archive.instruments,
        inspection.utc_days(*_window(fixture_archive)),
    )
    assert {(row.day, row.status) for row in days.itertuples()} == {
        (defects.verified_day, "verified"),
        (defects.provisional_day, "provisional"),
    }
    assert len(days) == 2 * len(fixture_archive.instruments)


def test_the_fixture_crossed_second_is_counted_and_blank(fixture_archive: FixturePaths) -> None:
    defects = fixture_archive.defects
    start, end = _window(fixture_archive)
    frames = CatalogFrames(fixture_archive.catalog_path, fixture_archive.candles_dir)
    seconds = frames.seconds(defects.crossed_instrument, start=start, end=end)
    sanity = inspection.snapshot_sanity(seconds)
    assert (sanity["crossed"], sanity["crossed_ts"]) == (1, [defects.crossed_ts])
    mid = inspection.mid_series(seconds, start, end)
    crossed_second = pd.Timestamp(defects.crossed_ts // NS_PER_S * NS_PER_S, unit="ns", tz="UTC")
    assert math.isnan(mid.loc[crossed_second])
    assert int(mid.notna().sum()) == len(seconds) - 1  # every other sampled second plotted


def test_the_fixture_folds_agree_with_the_duplicate_counted(fixture_archive: FixturePaths) -> None:
    defects = fixture_archive.defects
    start, end = _window(fixture_archive)
    frames = CatalogFrames(fixture_archive.catalog_path, fixture_archive.candles_dir)
    for iid in fixture_archive.instruments:
        table = inspection.fold_agreement(
            frames.objects(TradeTick, iid, start=start, end=end),
            frames.seconds(iid, start=start, end=end),
        )
        assert table["agrees"].all(), iid
        expected_duplicates = 1 if iid == defects.duplicate_instrument else 0
        assert int(table["duplicate_trades"].sum()) == expected_duplicates, iid


def test_the_fixture_ledger_counts(fixture_archive: FixturePaths) -> None:
    defects = fixture_archive.defects
    ledger = inspection.ledger_window(fixture_archive.errors_dir, *_window(fixture_archive))
    assert ledger.state == inspection.LEDGER_RECORDS
    counts = {(r.service, r.site): r.count for r in ledger.counts.itertuples()}
    assert counts == defects.ledger_counts
    restarts = dict(zip(ledger.restarts["service"], ledger.restarts["restarts"], strict=True))
    assert restarts == defects.ledger_restarts
