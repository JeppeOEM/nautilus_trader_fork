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
Each venue entrypoint is a composition root: it must inject the candle sink and start candles'
retention loop (Story 24.1). `Collector` no longer does either, and a venue that forgets would
write no bars and prune nothing, silently -- so the wiring is asserted per venue, not once.
"""

from pathlib import Path

import pytest
from candles.application.sink import CandleSink
from collector_core.ports import SecondSink

from bybit_collector.collector import BybitCollector
from bybit_collector.config import BybitConfig


_IID = "BTCUSDT-LINEAR.BYBIT"


def _pruned_store(loops: object) -> object:
    """
    Return the store `candles.application.prune.loop`'s closure captured, or None if absent.

    Matching only on `__name__` would pass a venue that started *another* venue's prune loop (or two
    venues sharing one store object) -- the copy-paste mistake this test exists to catch. The
    captured cell is the only evidence of which file the loop actually prunes.
    """
    for loop in loops:  # type: ignore[attr-defined]
        if getattr(loop, "__name__", "") != "prune_loop":
            continue
        names = loop.__code__.co_freevars
        return loop.__closure__[names.index("store")].cell_contents
    return None


def _collector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BybitCollector:
    monkeypatch.setenv("CANDLES_DB_PATH", str(tmp_path / "candles.db"))
    return BybitCollector(
        BybitConfig(environment="mainnet", catalog_path=str(tmp_path), instruments=(_IID,))
    )


def test_the_entrypoint_injects_a_sink_that_satisfies_the_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink = _collector(tmp_path, monkeypatch)._second_sink
    assert isinstance(sink, CandleSink)
    port: SecondSink = sink
    assert port.watermarks() == {}


def test_the_entrypoint_starts_the_candle_retention_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    collector = _collector(tmp_path, monkeypatch)
    assert _pruned_store(collector._extra_loops) is not None


def test_the_retention_loop_prunes_this_venues_own_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loop must hold the same store the sink writes, not another venue's."""
    collector = _collector(tmp_path, monkeypatch)
    sink = collector._second_sink
    assert isinstance(sink, CandleSink)
    assert _pruned_store(collector._extra_loops) is sink._store
