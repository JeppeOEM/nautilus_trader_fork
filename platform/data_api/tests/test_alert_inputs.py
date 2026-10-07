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
Story 33.8: `data_api.alert_inputs` -- the indicator reader over the chart's own values page (the
candle page stubbed with hand-built bars, the replay real), the drawings reader over a real
`chart_drawings.toml`, and the executor seam on a real event loop.
"""

import asyncio
import os
import threading
import tomllib
from pathlib import Path
from typing import Any

import pytest
from alerting.application.ports import Failed
from alerting.application.ports import IndicatorReading
from alerting.application.ports import IndicatorRef
from alerting.application.ports import Missing
from observability import error_ledger
from views import chart_series
from views import preferences
from views.indicator_picker import ReplayWindow
from views.indicator_picker import indicator_id
from views.indicator_picker import replay_entry

from data_api.alert_inputs import INDICATOR_READ_BARS
from data_api.alert_inputs import ChartIndicatorReader
from data_api.alert_inputs import DrawingFileReader
from data_api.alert_inputs import _series_values
from data_api.alert_inputs import executor_submit


_IID = "BTCUSDT-LINEAR.BYBIT"
_BAR_MS = 60_000
_CLOSES = [100.0 + (i % 7) * 1.5 - (i % 3) for i in range(40)]


def _candles(count: int = len(_CLOSES)) -> list[dict]:
    return [
        {"t": i * _BAR_MS, "o": c, "h": c + 2, "l": c - 1, "c": c, "v": 1.0}
        for i, c in enumerate(_CLOSES[:count])
    ]


def _reader(monkeypatch: pytest.MonkeyPatch, candles: list[dict], seen: list | None = None) -> Any:
    def page(*args: Any, **kwargs: Any) -> tuple[list[dict], bool]:
        if seen is not None:
            seen.append((args, kwargs))
        return candles, False

    monkeypatch.setattr(chart_series, "candle_page", page)
    return ChartIndicatorReader(
        catalog_path=lambda: "catalog",
        candles_dir=lambda: "candles",
        recent_rows=lambda *_: [],
        recent_liquidations=lambda *_: [],
    )


def _rsi(period: int = 14, source: str = "close") -> IndicatorRef:
    return IndicatorRef.of(
        {"name": "RelativeStrengthIndex", "params": {"period": period}, "source": source}
    )


def test_the_reading_is_the_charts_value_at_the_closed_bar_and_the_bar_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list = []
    reader = _reader(monkeypatch, _candles(), seen)
    closed_t = 39 * _BAR_MS
    result = reader.read(_IID, 60, closed_t, [_rsi()])
    window = ReplayWindow(_IID, 60, None, None)
    expected = replay_entry(_candles(), "RelativeStrengthIndex", {"period": 14}, window)["value"]
    assert result == {
        _rsi(): IndicatorReading(
            prev={"value": expected[-2]}, cur={"value": expected[-1]}, outputs=frozenset({"value"})
        )
    }
    ((args, _kwargs),) = seen  # one page for the batch: before the closed bar's end, 300 bars
    assert args[:4] == (_IID, (closed_t + _BAR_MS) * 1_000_000, INDICATOR_READ_BARS, 60)


def test_one_page_answers_every_series_of_the_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list = []
    reader = _reader(monkeypatch, _candles(), seen)
    result = reader.read(_IID, 60, 39 * _BAR_MS, [_rsi(), _rsi(7), _rsi(21)])
    assert len(seen) == 1
    assert all(isinstance(r, IndicatorReading) for r in result.values())
    assert len({r.cur["value"] for r in result.values()}) == 3  # three distinct series


def test_a_page_whose_newest_bar_is_not_the_closed_bar_is_no_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = _reader(monkeypatch, _candles(39))  # the closed bar is not in the store yet
    result = reader.read(_IID, 60, 39 * _BAR_MS, [_rsi()])
    assert isinstance(result[_rsi()], Failed)


def test_a_name_gone_from_the_catalog_is_missing_and_a_replay_error_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = _reader(monkeypatch, _candles())
    gone = IndicatorRef.of({"name": "Retired", "params": {}, "source": "close"})
    broken = _rsi(0)  # RSI(period=0) refuses to build: that series' error, not the batch's
    result = reader.read(_IID, 60, 39 * _BAR_MS, [gone, broken, _rsi()])
    assert result[gone] == Missing("indicator Retired is no longer in the catalog")
    assert isinstance(result[broken], Failed)
    assert isinstance(result[_rsi()], IndicatorReading)


def test_the_series_key_is_the_charts_indicator_id() -> None:
    assert indicator_id("RelativeStrengthIndex", _rsi(14, "hl2").params, "hl2") == (
        "RelativeStrengthIndex_period=14:hl2"
    )


def test_a_series_never_takes_the_outputs_of_another_whose_id_it_starts() -> None:
    row = {
        "values": {"Depth_bps=10.bid": 1.0, "Depth_bps=10.5.bid": 2.0, "Depth_bps=10.5.ask": 3.0}
    }
    assert _series_values(row, "Depth_bps=10.") == {"bid": 1.0}
    assert _series_values(row, "Depth_bps=10.5.") == {"bid": 2.0, "ask": 3.0}


_LINE = {"time": 1000, "price": 100.0}


def _save(path: Path, items: list[dict]) -> None:
    preferences.save_chart_drawings({_IID: items} if items else {}, path)


def test_the_drawings_reader_returns_a_trendlines_stored_anchors(tmp_path: Path) -> None:
    path = tmp_path / "chart_drawings.toml"
    anchors = [_LINE, {"time": 2000, "price": 200.0}]
    _save(
        path,
        [
            {"id": "t1", "kind": "trendline", "anchors": anchors},
            {"id": "h1", "kind": "hline", "price": 5.0},
        ],
    )
    reader = DrawingFileReader(lambda: path)
    assert reader.trendline(_IID, "t1") == anchors
    assert reader.trendline(_IID, "h1") == Missing("drawing h1 is a hline, not a trendline")
    assert reader.trendline(_IID, "x") == Missing("drawing x no longer exists")
    assert reader.trendline("ETHUSDT-LINEAR.BYBIT", "t1") == Missing("drawing t1 no longer exists")
    assert DrawingFileReader(lambda: tmp_path / "absent.toml").trendline(_IID, "t1") == Missing(
        "drawing t1 no longer exists"
    )


def test_the_drawings_reader_rereads_only_a_changed_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "chart_drawings.toml"
    _save(
        path, [{"id": "t1", "kind": "trendline", "anchors": [_LINE, {"time": 2000, "price": 1.0}]}]
    )
    loads: list[Path] = []
    real_load = preferences.load_chart_drawings

    def counting_load(p: Path) -> dict[str, list[dict[str, Any]]]:
        loads.append(p)
        return real_load(p)

    monkeypatch.setattr(preferences, "load_chart_drawings", counting_load)
    reader = DrawingFileReader(lambda: path)
    reader.trendline(_IID, "t1")
    reader.trendline(_IID, "t1")
    assert len(loads) == 1  # unchanged (mtime_ns, size): the cached parse
    _save(path, [])
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert reader.trendline(_IID, "t1") == Missing("drawing t1 no longer exists")
    assert len(loads) == 2


def test_the_drawings_reader_sees_an_atomic_save_with_the_same_mtime_and_size(
    tmp_path: Path,
) -> None:
    # Two saves within one mtime tick that leave the same size: only the inode (the atomic replace
    # writes a new file) tells them apart.
    path = tmp_path / "chart_drawings.toml"
    _save(
        path, [{"id": "t1", "kind": "trendline", "anchors": [_LINE, {"time": 2000, "price": 1.0}]}]
    )
    before = path.stat()
    reader = DrawingFileReader(lambda: path)
    assert reader.trendline(_IID, "t1") == [_LINE, {"time": 2000, "price": 1.0}]
    _save(
        path, [{"id": "t1", "kind": "trendline", "anchors": [_LINE, {"time": 2000, "price": 2.0}]}]
    )
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = path.stat()
    assert (after.st_mtime_ns, after.st_size) == (before.st_mtime_ns, before.st_size)
    assert after.st_ino != before.st_ino
    assert reader.trendline(_IID, "t1") == [_LINE, {"time": 2000, "price": 2.0}]


def test_a_corrupt_drawings_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "chart_drawings.toml"
    path.write_text("[x\n")
    with pytest.raises(tomllib.TOMLDecodeError):  # the engine ledgers it as a read failure
        DrawingFileReader(lambda: path).trendline(_IID, "t1")


@pytest.mark.asyncio
async def test_executor_submit_runs_the_job_off_the_loop_and_done_on_it() -> None:
    loop_thread: list[int] = []
    finished = asyncio.Event()
    results: list[Any] = []

    def job() -> int:
        loop_thread.append(threading.get_ident())
        return 7

    def done(result: int) -> None:
        results.append((result, threading.get_ident()))
        finished.set()

    executor_submit(job, done)
    await asyncio.wait_for(finished.wait(), 5)
    assert results == [(7, threading.get_ident())]  # `done` on the loop's thread
    assert loop_thread != [threading.get_ident()]  # `job` on an executor thread


@pytest.mark.asyncio
async def test_a_failing_done_is_ledgered(monkeypatch: pytest.MonkeyPatch) -> None:
    error_ledger.reset()
    ran = asyncio.Event()

    def done(_result: Any) -> None:
        ran.set()
        raise RuntimeError("engine bug")

    executor_submit(lambda: 1, done)
    await asyncio.wait_for(ran.wait(), 5)
    await asyncio.sleep(0)
    assert error_ledger.counts() == {"alerting.engine.input": 1}
    error_ledger.reset()


@pytest.mark.asyncio
async def test_a_raising_job_hands_its_exception_to_done() -> None:
    # `done` runs for every submitted job, so the engine's in-flight flag is always cleared.
    finished = asyncio.Event()
    results: list[Any] = []

    def job() -> int:
        raise RuntimeError("read blew up")

    def done(result: Any) -> None:
        results.append(result)
        finished.set()

    executor_submit(job, done)
    await asyncio.wait_for(finished.wait(), 5)
    assert [type(r) for r in results] == [RuntimeError]
