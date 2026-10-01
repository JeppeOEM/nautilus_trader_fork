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
The dYdX entrypoint's `ws_raw_sink` switch (operator decision 2026-09-30): the Rust `[WS_RAW]`
file sink, its flush loop and the incident reports' raw window all follow the plan file's one
key, off by default, read once before the Rust logger exists.
"""

import dataclasses
import functools
from pathlib import Path

import pytest
from observability import incidents

from capture.application.capture_service import CaptureService
from capture.venues.dydx.__main__ import INCIDENTS
from capture.venues.dydx.__main__ import build_capture
from capture.venues.dydx.__main__ import rust_logging_kwargs
from capture.venues.dydx.__main__ import ws_raw_sink_enabled
from capture.venues.dydx.config import DydxConfig
from capture.venues.dydx.tests.test_collector_trade_ohlc import _make_config
from nautilus_trader.core import nautilus_pyo3


_PLAN = """
network = "testnet"
liquidity_min_oi_usd = 1000
non_config_retain_hours = 4

[[instruments]]
id = "BTC-USD-PERP.DYDX"
"""


def test_default_is_off_and_installs_no_file_sink() -> None:
    assert DydxConfig.ws_raw_sink is False
    assert rust_logging_kwargs(False) == {"level_stdout": nautilus_pyo3.LogLevel.WARNING}


def test_on_installs_the_bounded_debug_file_sink_the_incident_scan_reads() -> None:
    kwargs = rust_logging_kwargs(True)
    assert kwargs["level_stdout"] == nautilus_pyo3.LogLevel.WARNING
    assert kwargs["level_file"] == nautilus_pyo3.LogLevel.DEBUG
    # The same directory and name `scan_raw_window` globs, or the reports would find nothing.
    assert (kwargs["directory"], kwargs["file_name"]) == (
        str(INCIDENTS.raw_log_dir),
        INCIDENTS.raw_log_name,
    )
    assert kwargs["file_rotate"] == (20_000_000, 1)


@pytest.mark.parametrize(
    ("suffix", "expected"),
    [("", False), ("ws_raw_sink = false\n", False), ("ws_raw_sink = true\n", True)],
)
def test_switch_is_read_from_the_plan_file(tmp_path: Path, suffix: str, expected: bool) -> None:
    path = tmp_path / "c.toml"
    path.write_text(suffix + _PLAN)  # top level, above the instrument tables
    assert ws_raw_sink_enabled(path) is expected


@pytest.mark.parametrize("suffix", ["ws_raw_sink = 1\n", "ws_raw_sink = [\n"])
def test_a_broken_switch_or_file_stays_off_with_a_warning(
    tmp_path: Path, suffix: str, caplog: pytest.LogCaptureFixture
) -> None:
    """A read failure here is not a crash: the loader reports the real error per attempt."""
    path = tmp_path / "c.toml"
    path.write_text(suffix + _PLAN)  # top level, above the instrument tables
    with caplog.at_level("WARNING"):
        assert ws_raw_sink_enabled(path) is False
        assert ws_raw_sink_enabled(tmp_path / "missing.toml") is False
    assert sum("raw WS sink stays off" in r.message for r in caplog.records) == 2


def _flush_loops(capture: CaptureService) -> list[functools.partial[None]]:
    return [
        loop
        for loop in capture._extra_loops
        if isinstance(loop, functools.partial) and loop.func is incidents.raw_log_flush_loop
    ]


@pytest.mark.parametrize("enabled", [False, True])
def test_flush_loop_runs_only_with_the_sink(tmp_path: Path, enabled: bool) -> None:
    """With nothing written to a raw file there is nothing to sync every 2 s."""
    config = dataclasses.replace(_make_config(tmp_path / "catalog"), ws_raw_sink=enabled)
    capture = build_capture(config, ())
    assert len(_flush_loops(capture)) == (1 if enabled else 0)
