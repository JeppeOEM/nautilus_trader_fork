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
`research.application.backtest_report` over real `NodeRunner` runs on the notebooks' fixture
archive (TEST-01/03, no Nautilus mocks): the folder a run is saved into, its tearsheet's panels,
the strategy copy, the record read back equal to the `RunResult`, the index and its listing, the
NaN/infinity refusals, the benchmark, and the engine-less save.
"""

import inspect
import json
import math
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from pathlib import Path

import pytest
from kernel.clocks import NS_PER_DAY
from kernel.clocks import NS_PER_S

from nautilus_trader.examples.strategies.ema_cross import EMACross
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from research.application import backtest_report
from research.application.backtest_report import list_backtest_reports
from research.application.backtest_report import load_backtest_record
from research.application.backtest_report import save_backtest_report
from research.application.backtest_runner import NodeRunner
from research.application.ports import RunResult
from research.application.ports import RunSpec
from research.domain.report import MetricReport
from research.domain.trades import ClosedTrade
from research.domain.trades import TradeLedger
from research.strategies import backtest_ofi
from research.tests.fixture_catalog import DATA_END_NS
from research.tests.fixture_catalog import DATA_START_NS
from research.tests.fixture_catalog import FixturePaths


_IID = "BTC-USD-PERP.HYPERLIQUID"
_EMA = "nautilus_trader.examples.strategies.ema_cross:EMACross"


def _iso(ns: int) -> str:
    return datetime.fromtimestamp(ns // NS_PER_S, tz=UTC).replace(tzinfo=None).isoformat()


def _spec(fixture: FixturePaths) -> RunSpec:
    return RunSpec(
        catalog_path=fixture.catalog_path,
        instrument_ids=(_IID,),
        start=_iso(DATA_START_NS),
        end=_iso(DATA_END_NS),
        strategy_path=_EMA,
        config_path=_EMA + "Config",
        params={"trade_size": "0.01", "fast_ema_period": 2, "slow_ema_period": 3},
        data="bars:1-MINUTE",
    )


@pytest.fixture(scope="module")
def saved(
    fixture_archive: FixturePaths, tmp_path_factory: pytest.TempPathFactory
) -> tuple[RunSpec, RunResult, Path]:
    """One EMACross bars run on the fixture, saved through `NodeRunner(report_root=...)`."""
    root = tmp_path_factory.mktemp("reports")
    spec = _spec(fixture_archive)
    return spec, NodeRunner(report_root=root).run(spec), root


def _html(folder: Path) -> str:
    return (folder / backtest_report.TEARSHEET_FILE).read_text(encoding="utf-8")


def test_a_saved_run_is_one_folder_named_by_strategy_and_utc_time(saved: tuple) -> None:
    _, result, root = saved
    assert result.report_dir is not None
    assert result.report_dir.parent == root
    assert result.report_dir.name.startswith("EMACross_")
    stamp = result.report_dir.name.removeprefix("EMACross_")
    assert datetime.strptime(stamp + "+0000", "%Y-%m-%dT%H-%M-%SZ%z").tzinfo is not None
    assert sorted(p.name for p in result.report_dir.iterdir()) == [
        "record.json",
        "strategy.py",
        "tearsheet.html",
    ]


def test_the_tearsheet_shows_every_panel(saved: tuple) -> None:
    _, result, _ = saved
    html = _html(result.report_dir)
    titles = [
        *backtest_report.NAUTILUS_PANEL_TITLES,
        backtest_report.RUN_CONFIG_TITLE,
        backtest_report.PNL_BY_HOUR_TITLE,
        backtest_report.PNL_BY_WEEKDAY_TITLE,
        f"{backtest_report.BARS_TITLE}: {_IID}-1-MINUTE-LAST-INTERNAL",
    ]
    assert [t for t in titles if json.dumps(t)[1:-1] not in html] == []


def test_every_extra_panel_was_drawn_not_just_titled(saved: tuple) -> None:
    """A title alone proves nothing: Nautilus titles a panel it then skips (the Known limit)."""
    _, result, _ = saved
    html = _html(result.report_dir)
    assert "Strategy file sha256" in html  # the run configuration table's cells
    assert "Exit hour (UTC)" in html  # the PnL-by-hour renderer's own axis title
    assert "Exit weekday (UTC)" in html  # the PnL-by-weekday renderer's own axis title
    assert '"type":"candlestick"' in html  # bars with fills


def test_strategy_copy_is_the_module_file_verbatim(saved: tuple) -> None:
    _, result, _ = saved
    source = Path(inspect.getsourcefile(EMACross) or "")
    copy = result.report_dir / backtest_report.STRATEGY_FILE
    assert copy.read_bytes() == source.read_bytes()


def test_the_record_reads_back_equal_to_the_run(saved: tuple) -> None:
    spec, result, _ = saved
    record = load_backtest_record(result.report_dir)
    assert MetricReport.from_metrics(record["metrics"]) == result.metrics
    trades = TradeLedger(tuple(ClosedTrade(**t) for t in record["trades"]))
    assert trades == result.trades
    assert record["equity"]["values"] == result.equity.values.tolist()
    assert record["pnl_by_day"] == result.pnl_by_day
    assert record["spec"]["params"] == dict(spec.params)
    assert record["run"]["config_id"] == result.config_id
    assert record["run"]["run_id"] == result.run_id
    assert record["run"]["instance_id"] == result.instance_id


def test_an_undefined_nautilus_statistic_is_null_and_every_other_value_kept(saved: tuple) -> None:
    _, result, _ = saved
    stored = load_backtest_record(result.report_dir)["nautilus_stats"]["returns"]
    for name, value in result.nautilus_stats["returns"].items():
        assert stored[name] == (None if math.isnan(value) else value), name


def test_the_benchmark_is_the_bars_buy_and_hold(saved: tuple) -> None:
    _, result, _ = saved
    benchmark = load_backtest_record(result.report_dir)["benchmark"]
    assert benchmark["bar_type"] == f"{_IID}-1-MINUTE-LAST-INTERNAL"
    assert len(benchmark["returns"]["values"]) == 1  # the fixture's two UTC days: one return


def test_the_index_lists_every_save_in_order(saved: tuple) -> None:
    spec, result, root = saved
    again = save_backtest_report(result, spec, root)
    listed = list_backtest_reports(root)
    assert listed["folder"].tolist() == [str(result.report_dir), str(again)]
    assert listed["config_id"].tolist() == [result.config_id] * 2
    assert listed["metrics.avg_win"].dtype == "float64"  # undefined: NaN, not an object None


def test_a_taken_name_gets_a_suffix(tmp_path: Path) -> None:
    now = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)
    first = backtest_report._new_folder(tmp_path, "EMACross", now)
    second = backtest_report._new_folder(tmp_path, "EMACross", now)
    assert (first.name, second.name) == ("EMACross_2026-10-08T12-00-00Z", f"{first.name}-2")


def test_a_saved_without_engine_report_has_nautilus_panels_and_no_bars(
    saved: tuple, tmp_path: Path
) -> None:
    spec, result, _ = saved
    folder = save_backtest_report(result, spec, tmp_path)
    html = _html(folder)
    assert [t for t in backtest_report.NAUTILUS_PANEL_TITLES if t not in html] == []
    assert backtest_report.BARS_TITLE not in html
    assert "saved without the engine" in load_backtest_record(folder)["benchmark"]["omitted"]


@pytest.mark.parametrize(
    ("change", "where"),
    [
        ({"params": {"threshold": math.nan}}, "params.threshold"),
        ({"wall_seconds": math.inf}, "wall_seconds"),
    ],
)
def test_a_non_finite_value_refuses_the_save_and_leaves_no_folder(
    saved: tuple, tmp_path: Path, change: dict, where: str
) -> None:
    spec, result, _ = saved
    with pytest.raises(ValueError, match=where):
        save_backtest_report(replace(result, **change), spec, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_an_infinite_nautilus_statistic_is_refused(saved: tuple, tmp_path: Path) -> None:
    spec, result, _ = saved
    stats = {**result.nautilus_stats, "returns": {"Sharpe Ratio (252 days)": math.inf}}
    with pytest.raises(ValueError, match="Sharpe Ratio"):
        save_backtest_report(replace(result, nautilus_stats=stats), spec, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_a_root_inside_the_catalog_is_refused(saved: tuple) -> None:
    spec, result, _ = saved
    with pytest.raises(ValueError, match="inside the catalog"):
        save_backtest_report(result, spec, Path(spec.catalog_path) / "reports")


def test_a_record_of_another_schema_or_with_nan_is_refused(tmp_path: Path) -> None:
    (tmp_path / "record.json").write_text(json.dumps({"schema": 0}))
    with pytest.raises(ValueError, match="schema-1"):
        load_backtest_record(tmp_path)
    (tmp_path / "record.json").write_text('{"schema": 1, "x": NaN}')
    with pytest.raises(ValueError, match="NaN"):
        load_backtest_record(tmp_path)


def test_a_missing_root_raises_and_an_empty_one_lists_nothing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        list_backtest_reports(tmp_path / "absent")
    assert list_backtest_reports(tmp_path).empty


@pytest.mark.parametrize(
    ("answers", "expected"),
    [
        ({"rev-parse": "abc1234567", "status": ""}, "abc1234567"),
        ({"rev-parse": "abc1234567", "status": " M x.py"}, "abc1234567-dirty"),
        ({"rev-parse": "abc1234567", "status": None}, "abc1234567-unknown-status"),
        ({"rev-parse": None, "status": None}, "unknown"),
    ],
)
def test_the_git_revision_carries_a_dirty_flag(
    monkeypatch: pytest.MonkeyPatch, answers: dict[str, str], expected: str
) -> None:
    monkeypatch.setattr(backtest_report, "_git", lambda *args: answers[args[0]])
    assert backtest_report.git_revision() == expected


def _bar(day: int, close: str) -> Bar:
    ts = DATA_START_NS - DATA_START_NS % NS_PER_DAY + day * NS_PER_DAY + 3_600 * NS_PER_S
    price = Price.from_str(close)
    return Bar(
        BarType.from_str(f"{_IID}-1-HOUR-LAST-INTERNAL"),
        price,
        price,
        price,
        price,
        Quantity.from_str("1"),
        ts,
        ts,
    )


def test_buy_and_hold_never_bridges_a_missing_day() -> None:
    returns = backtest_report.buy_and_hold([_bar(0, "100.0"), _bar(1, "110.0"), _bar(3, "99.0")])
    assert returns.tolist() == pytest.approx([0.1])
    assert returns.index[0].value == DATA_START_NS - DATA_START_NS % NS_PER_DAY + NS_PER_DAY


def test_the_default_ofi_run_refuses_a_report_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="NodeRunner path"):
        backtest_ofi.run(catalog_path=str(tmp_path), report_root=tmp_path)


def test_each_sweep_point_is_saved_with_its_own_params(
    fixture_archive: FixturePaths, tmp_path: Path
) -> None:
    grid = [{"slow_ema_period": 3}, {"slow_ema_period": 4}]
    results = NodeRunner(report_root=tmp_path).sweep(_spec(fixture_archive), grid)
    stored = [load_backtest_record(r.report_dir)["spec"]["params"] for r in results]
    assert [p["slow_ema_period"] for p in stored] == [3, 4]
    assert len(list_backtest_reports(tmp_path)) == 2


def test_a_report_that_could_not_be_saved_refuses_the_run_before_it_starts(
    fixture_archive: FixturePaths, tmp_path: Path
) -> None:
    spec = replace(_spec(fixture_archive), params={**_spec(fixture_archive).params, "tags": {1}})
    with pytest.raises(ValueError, match=r"spec\.params\.tags"):
        NodeRunner(report_root=tmp_path).run(spec)
    assert list(tmp_path.iterdir()) == []


def test_a_failed_index_append_leaves_neither_line_nor_folder(
    saved: tuple, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec, result, _ = saved
    first = save_backtest_report(result, spec, tmp_path)
    index = tmp_path / backtest_report.INDEX_FILE
    before = index.read_bytes()
    real = backtest_report._fsync_dir

    def failing_on_root(path: Path) -> None:
        if path == tmp_path:
            raise OSError("disk gone")
        real(path)

    monkeypatch.setattr(backtest_report, "_fsync_dir", failing_on_root)
    with pytest.raises(OSError, match="disk gone"):
        save_backtest_report(result, spec, tmp_path)
    assert index.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([first.name, index.name])
