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
`warnings.simplefilter("error")` (TEST-04), less the two upstream pandas-3 deprecations named in
`UPSTREAM_PANDAS4_DEPRECATIONS` (by exact message), with plotly headless, and must finish in under
60 s. The
pairing check holds every `.ipynb` to its `.py` twin: no stored output or execution count, and the
same cells as jupytext reads from the `.py`. It needs jupytext (installed in the collector image
through `platform/requirements.txt` and in the repo `.venv`); a host python without it skips that
one test, never the runs. The fixture's planted defects are asserted against
`research.application.inspection` here too, so what the notebook claims to show is checked.
"""

import json
import math
import re
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
from research.application.liquidations import read_liquidations
from research.application.liquidations import replay_cascade
from research.application.ports import window_ns
from research.tests.fixture_catalog import CASCADE_INSTRUMENT
from research.tests.fixture_catalog import DATA_END_NS
from research.tests.fixture_catalog import DATA_START_NS
from research.tests.fixture_catalog import LIQUIDATION_BACKGROUND
from research.tests.fixture_catalog import LIQUIDATION_BURST
from research.tests.fixture_catalog import FixturePaths
from research.tests.source_tree import SOURCE_TREE


NOTEBOOKS_DIR = Path(__file__).resolve().parents[1] / "notebooks"
RUN_SECONDS_LIMIT = 60.0

# The two deprecations pandas 3 raises (as `pandas.errors.Pandas4Warning`) from inside compiled
# nautilus_trader 1.229.0, which FORK-01 forbids fixing here. The harness ignores exactly these
# messages and turns every other warning into an error (TEST-04). Under pandas 2.3.3 (the host
# interpreter) neither is raised. `platform/Makefile`'s `PANDAS4_WARNINGS` holds the same two
# messages for the image test runs; `platform/tests/test_pandas_pin.py` holds the two equal.
UPSTREAM_PANDAS4_DEPRECATIONS = (
    # `pd.Timestamp.utcnow()`: nautilus_trader/backtest/engine.pyx:1418, :1601 and
    # nautilus_trader/backtest/node.py:347 (every BacktestEngine/BacktestNode run).
    "Timestamp.utcnow is deprecated and will be removed in a future version. "
    "Use Timestamp.now('UTC') instead.",
    # `floor(freq="d")`: nautilus_trader/data/aggregation.pyx:1626, :1634, :1646, :1786
    # (`find_closest_smaller_time`, every time-bar subscription) and
    # nautilus_trader/data/engine.pyx:2041 (date-range requests).
    "'d' is deprecated and will be removed in a future version, please use 'D' instead.",
)
# pandas 2.3.3 has no `Pandas4Warning` class (and raises neither message), so the host falls back
# to its base class; under pandas 3 the filter is exactly the Makefile's category.
_PANDAS4_WARNING: type[Warning] = getattr(pd.errors, "Pandas4Warning", DeprecationWarning)

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


# Every indicator and strategy size shrunk to fit ten one-minute bars (07 and 08 share it; a key a
# notebook does not read is ignored): short periods, Ichimoku 2/3/4 displaced by one bar, a narrow
# Keltner channel, RSI and fuzzy-candle thresholds the fixture's bars reach, and a TWAP that
# finishes inside one bar.
_FIXTURE_PERIODS = {
    "period": 3,
    "period_k": 3,
    "period_d": 2,
    "fast": 2,
    "slow": 3,
    "signal": 2,
    "k": 0.5,
    "k_multiplier": 0.2,
    "atr_period": 3,
    "atr_multiple": 1.0,
    "rsi_period": 3,
    "rsi_buy": 0.5,
    "rsi_sell": 0.5,
    "twap_horizon_secs": 6.0,
    "twap_interval_secs": 3.0,
    "Swings.period": 2,
    "IchimokuCloud.tenkan": 2,
    "IchimokuCloud.kijun": 3,
    "IchimokuCloud.senkou": 4,
    "IchimokuCloud.displacement": 1,
    "Signal.rsi.low": 0.49,
    "Signal.rsi.high": 0.51,
    "Signal.fuzzy_candle.period": 3,
    "Signal.fuzzy_candle.min_size": 1,
}
# The cascade detector sized to the fixture's ten minutes (Story 33.14): a 10 s window over a 120 s
# baseline, so the background (one liquidation every 5 s, two in a window) warms it within the
# window and reads under the threshold, while the 30 s burst at 400 s is the one episode
# (`test_the_fixture_holds_one_cascade_episode`); exits and re-entries that fit in what is left.
_FIXTURE_DETECTOR = ("window_s", "baseline_s", "intensity_threshold", "decay_ratio")
_FIXTURE_CASCADE = {
    "window_s": 10,
    "baseline_s": 120,
    "intensity_threshold": 3.0,
    "decay_ratio": 0.5,
    "entry_timeout_s": 30,
    "cooldown_s": 30,
    "max_hold_s": 120,
}
# The OFI runs of 08 on the cascade instrument (Story 33.13): the OFI sizes that trade, and the
# detector sized as `_FIXTURE_CASCADE`'s, so the gates see the planted episode.
_FIXTURE_OFI_CASCADE = {
    **_FIXTURE_OFI,
    # The cumulative-delta gate on over a short window, so the forced-flow filter can change a
    # decision on the fixture's burst.
    "cum_delta_threshold": 0.0,
    "cum_delta_seconds": 30,
    "cascade_window_s": 10,
    "cascade_baseline_s": 120,
    "cascade_intensity_threshold": 3.0,
    "cascade_decay_ratio": 0.5,
}
NOTEBOOK_ENV.update(
    {
        # Every indicator of the catalog replayed over the ten 1 m bars (a venue-timed instrument
        # clear of the dYdX outage) with the sizes above, and the snapshot indicators over their
        # 600 s with small windows.
        "07_indicator_atlas.py": {
            "START": _iso(DATA_START_NS),
            "END": _iso(DATA_END_NS),
            "NOTEBOOK_INSTRUMENT": json.dumps("BTC-USD-PERP.HYPERLIQUID"),
            "NOTEBOOK_PERIODS": json.dumps(_FIXTURE_PERIODS),
            "NOTEBOOK_OBI_LEVELS": json.dumps(5),
            "NOTEBOOK_OFI_WINDOW": json.dumps(5),
        },
        # The thirteen bar strategies on the same ten minute bars with the sizes above, the four
        # cascade runs on the fixture's planted liquidation cascade (`_FIXTURE_CASCADE`), then the
        # first one across every execution model.
        "08_strategy_gallery.py": {
            "START": _iso(DATA_START_NS),
            "END": _iso(DATA_END_NS),
            "NOTEBOOK_INSTRUMENT": json.dumps("BTC-USD-PERP.HYPERLIQUID"),
            "NOTEBOOK_PERIODS": json.dumps(_FIXTURE_PERIODS),
            "NOTEBOOK_DATA": json.dumps("bars:1-MINUTE"),
            "NOTEBOOK_CASCADE_INSTRUMENT": json.dumps(CASCADE_INSTRUMENT),
            "NOTEBOOK_CASCADE_PARAMS": json.dumps(_FIXTURE_CASCADE),
            "NOTEBOOK_OFI_PARAMS": json.dumps(_FIXTURE_OFI_CASCADE),
        },
        # The liquidation study (Story 33.13) of the cascade instrument over the fixture's data
        # window with the detector sized as `_FIXTURE_CASCADE`'s: the burst is one episode.
        "09_liquidations.py": {
            "START": _iso(DATA_START_NS),
            "END": _iso(DATA_END_NS),
            "NOTEBOOK_INSTRUMENT": json.dumps(CASCADE_INSTRUMENT),
            "NOTEBOOK_STUDY": json.dumps({key: _FIXTURE_CASCADE[key] for key in _FIXTURE_DETECTOR}),
        },
    }
)


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
        for message in UPSTREAM_PANDAS4_DEPRECATIONS:
            warnings.filterwarnings("ignore", re.escape(message), _PANDAS4_WARNING)
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


def test_the_fixture_holds_one_cascade_episode(fixture_archive: FixturePaths) -> None:
    """The planted liquidations replay to one falling cascade inside the burst (Story 33.14)."""
    rows = read_liquidations(
        fixture_archive.catalog_path, CASCADE_INSTRUMENT, DATA_START_NS, DATA_END_NS
    )
    assert len(rows) == len(LIQUIDATION_BACKGROUND) + len(LIQUIDATION_BURST)
    detector = {"window_s": 10, "baseline_s": 120, "intensity_threshold": 3.0, "decay_ratio": 0.5}
    assert {key: _FIXTURE_CASCADE[key] for key in detector} == detector
    episodes = replay_cascade(
        rows, 10, 120, 3.0, 0.5, DATA_END_NS, start_ns=DATA_START_NS, precisions=(2, 3)
    )
    burst = range(
        DATA_START_NS + LIQUIDATION_BURST.start * NS_PER_S,
        DATA_START_NS + LIQUIDATION_BURST.stop * NS_PER_S,
    )
    assert [(e.direction, e.start_ns in burst, e.end_ns is not None) for e in episodes] == [
        (-1, True, True)
    ]


def test_the_strategy_gallery_runs_the_cascades_and_shows_their_sample(
    fixture_archive: FixturePaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the 08 notebook ran and printed (its namespace): four cascade runs and their sample."""
    shown = _run(NOTEBOOKS_DIR / "08_strategy_gallery.py", fixture_archive, monkeypatch)
    cascades = shown["cascades"]
    assert [o.gallery.label for o in cascades] == [
        f"Cascade follow short only ({CASCADE_INSTRUMENT}, liquidations)",
        f"Cascade follow both sides ({CASCADE_INSTRUMENT}, liquidations)",
        f"Cascade fade short only ({CASCADE_INSTRUMENT}, liquidations)",
        f"Cascade fade both sides ({CASCADE_INSTRUMENT}, liquidations)",
    ]
    assert all(o.error is None for o in cascades)
    sample = shown["sample"]
    assert (sample.has_feed, sample.days, sample.episodes) == (True, 2, 1)
    follow = cascades[0].result
    assert follow is not None
    assert len(follow.fills) >= 1  # the planted episode is traded


def test_the_strategy_gallery_runs_the_four_ofi_rows_beside_their_sample(
    fixture_archive: FixturePaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What 08 ran and printed for OFI (Story 33.13): the baseline and three variants, one sample."""
    shown = _run(NOTEBOOKS_DIR / "08_strategy_gallery.py", fixture_archive, monkeypatch)
    runs = shown["ofi_runs"]
    assert [o.gallery.label for o in runs] == [
        f"OFI {name} ({CASCADE_INSTRUMENT}, seconds_liquidations)"
        for name in ("baseline", "forced-flow filter", "cascade fade", "cascade follow")
    ]
    assert all(o.error is None for o in runs)
    sample = shown["ofi_sample"]
    assert (sample.has_feed, sample.days, sample.episodes) == (True, 2, 1)


def test_the_liquidations_notebook_reports_the_planted_episode(
    fixture_archive: FixturePaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What 09 computed (its namespace): the fixture's liquidations and its one burst episode."""
    shown = _run(NOTEBOOKS_DIR / "09_liquidations.py", fixture_archive, monkeypatch)
    study = shown["study"]
    assert study.liquidations == len(LIQUIDATION_BACKGROUND) + len(LIQUIDATION_BURST)
    assert len(study.episodes) >= 1
    assert study.episodes["direction"].tolist() == [-1]
    assert study.cross_venue.reason is not None  # Hyperliquid has no liquidation feed
