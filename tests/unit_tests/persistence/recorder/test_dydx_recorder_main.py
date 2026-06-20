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
Regression tests for the MEM-04 watchdog teardown ordering in ``main()``'s
``finally`` block (scripts/dydx_recorder/recorder.py).

THE BUG (cycle 4): the watchdog grace deadline starts at the FIRST Ctrl+C (armed
in the on_stop hook). The on_stop hook also triggers a final ParquetDataCatalog
conversion offloaded to ``conversion_executor``. For a large backlog that final
conversion can legitimately run LONGER than the grace period. If
``watchdog.mark_completed()`` is only called AFTER
``conversion_executor.shutdown(wait=True)`` and ``node.dispose()``, a perfectly
healthy but slow shutdown trips the watchdog and ``os._exit()`` MID-CONVERSION,
truncating an in-progress parquet/feather write — a data-integrity bug introduced
by the watchdog fix itself.

THE FIX: ``watchdog.mark_completed()`` (and ``stop()``) must be the FIRST thing in
the ``finally`` block — disarming the watchdog the instant ``node.run()`` returns,
which already proves the loop-wedge race (the only thing the watchdog guards) did
not happen. These tests lock that ordering in so it cannot silently regress.
"""

from unittest.mock import MagicMock

import pytest

import scripts.dydx_recorder.recorder as recorder_module
from nautilus_trader.model.identifiers import InstrumentId


@pytest.fixture
def patched_main(monkeypatch):
    """
    Patch every heavy collaborator of ``main()`` and wire the teardown-relevant
    objects onto a single ``parent`` mock so cross-object call ORDER is recorded in
    ``parent.mock_calls``.

    Returns the parent recorder mock; ``parent.watchdog``, ``parent.node`` and
    ``parent.executor`` expose the objects whose ordering we assert.
    """
    parent = MagicMock()

    # --- config loading -------------------------------------------------------
    recorder_cfg = MagicMock()
    recorder_cfg.trader_id = "DYDX-COLLECTOR-001"
    recorder_cfg.shutdown_watchdog_grace_seconds = 20
    recorder_cfg.instruments = []
    instrument_ids = [InstrumentId.from_str("BTC-USD-PERP.DYDX")]
    monkeypatch.setattr(
        recorder_module,
        "load_dydx_recorder_config",
        lambda _path: (recorder_cfg, instrument_ids),
    )
    monkeypatch.setattr(recorder_module, "build_streaming_config", lambda _cfg: MagicMock())
    monkeypatch.setattr(recorder_module, "_map_network", lambda _env: MagicMock())

    # --- node + strategy ------------------------------------------------------
    node = parent.node
    # node.kernel.data_engine._clients[ClientId(DYDX)] must be subscriptable.
    node.kernel.data_engine._clients = MagicMock()
    monkeypatch.setattr(recorder_module, "TradingNode", lambda config: node)
    monkeypatch.setattr(recorder_module, "RecorderStrategy", lambda config: MagicMock())
    monkeypatch.setattr(recorder_module, "TradingNodeConfig", lambda **_kw: MagicMock())
    monkeypatch.setattr(recorder_module, "RecorderStrategyConfig", lambda **_kw: MagicMock())
    monkeypatch.setattr(recorder_module, "LoggingConfig", lambda **_kw: MagicMock())
    monkeypatch.setattr(recorder_module, "DydxDataClientConfig", lambda **_kw: MagicMock())
    monkeypatch.setattr(recorder_module, "InstrumentProviderConfig", lambda **_kw: MagicMock())
    monkeypatch.setattr(recorder_module, "TraderId", lambda _v: MagicMock())
    monkeypatch.setattr(recorder_module, "UUID4", MagicMock())

    # --- executor + watchdog (the ordering-relevant objects) ------------------
    monkeypatch.setattr(recorder_module, "ThreadPoolExecutor", lambda **_kw: parent.executor)
    monkeypatch.setattr(recorder_module, "ShutdownWatchdog", lambda **_kw: parent.watchdog)

    return parent


def test_main_disarms_watchdog_before_executor_drain_and_dispose(patched_main):
    # MEM-04 regression: mark_completed() MUST be called before the executor drain
    # and node.dispose() so a legitimately slow final conversion cannot trip the
    # force-exit mid-write.
    parent = patched_main

    # Act
    recorder_module.main("ignored.toml")

    # Assert — extract the ordered names of the teardown-relevant calls.
    names = [c[0] for c in parent.mock_calls]

    def _idx(name: str) -> int:
        assert name in names, f"{name} was not called; calls were {names}"
        return names.index(name)

    mark_completed_idx = _idx("watchdog.mark_completed")
    stop_idx = _idx("watchdog.stop")
    executor_shutdown_idx = _idx("executor.shutdown")
    dispose_idx = _idx("node.dispose")

    # The disarm happens before BOTH the (possibly slow) executor drain and dispose.
    assert mark_completed_idx < executor_shutdown_idx
    assert mark_completed_idx < dispose_idx
    assert stop_idx < executor_shutdown_idx
    assert stop_idx < dispose_idx


def test_main_disarms_watchdog_even_when_node_run_raises(patched_main):
    # The finally must still disarm the watchdog first if node.run() raises (e.g.
    # an on_start failure with raise_exception=True) — otherwise a crashing startup
    # would leave the armed watchdog to force-exit during cleanup.
    parent = patched_main
    parent.node.run.side_effect = RuntimeError("on_start failed")

    # Act / Assert
    with pytest.raises(RuntimeError, match="on_start failed"):
        recorder_module.main("ignored.toml")

    names = [c[0] for c in parent.mock_calls]
    assert "watchdog.mark_completed" in names
    assert names.index("watchdog.mark_completed") < names.index("executor.shutdown")
    assert names.index("watchdog.mark_completed") < names.index("node.dispose")
