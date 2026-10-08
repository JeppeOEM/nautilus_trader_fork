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
"""measure_lag's pure parts on real TradeTicks (the live run is manual, see its docstring)."""

import os
from pathlib import Path

import pytest

from archive.tools.measure_lag import PLAN_ENV
from archive.tools.measure_lag import LagRecorder
from archive.tools.measure_lag import _default_instruments
from archive.tools.measure_lag import _plan_path
from archive.tools.measure_lag import main
from archive.tools.measure_lag import percentile
from archive.tools.measure_lag import report
from archive.tools.measure_lag import suggest_hold_back
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


_MS = 1_000_000


@pytest.fixture(autouse=True)
def _no_plan_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the plan env vars a compose shell (`make test` in the image) sets."""
    for env_var in PLAN_ENV.values():
        monkeypatch.delenv(env_var, raising=False)


def _trade(n: int, lag_ms: int) -> TradeTick:
    ts_event = 1_789_000_000_000_000_000
    return TradeTick(
        InstrumentId.from_str("BTCUSDT-LINEAR.BYBIT"),
        Price.from_str("100.0"),
        Quantity.from_str("1.0"),
        AggressorSide.BUYER,
        TradeId(str(n)),
        ts_event,
        ts_event + lag_ms * _MS,
    )


def _recording(stale_ms: int = 10_000) -> LagRecorder:
    recorder = LagRecorder(stale_ms * _MS)
    recorder.recording = True
    return recorder


def test_percentile_is_nearest_rank() -> None:
    values = list(range(1, 1001))
    assert (percentile(values, 0.5), percentile(values, 0.999), percentile(values, 1.0)) == (
        500,
        999,
        1000,
    )


def test_hold_back_is_trade_p999_rounded_up_to_half_a_second() -> None:
    assert [suggest_hold_back(ms * _MS) for ms in (0, 1, 500, 501, 1700)] == [
        0.0,
        0.5,
        0.5,
        1.0,
        2.0,
    ]


def test_recorder_keeps_lag_per_kind_and_counts_replayed_trades() -> None:
    recorder = _recording()
    for n, lag in enumerate((120, 80, 20_000)):
        recorder(_trade(n, lag))
    assert (recorder.lags["TradeTick"], recorder.replayed_trades) == ([120 * _MS, 80 * _MS], 1)


def test_recorder_ignores_the_warm_up_and_objects_without_both_clocks() -> None:
    recorder = LagRecorder(10_000 * _MS)
    recorder(_trade(1, 100))  # still warming up
    recorder.recording = True
    recorder(object())
    assert dict(recorder.lags) == {}


def test_report_suggests_the_hold_back_from_trades() -> None:
    recorder = _recording()
    for n in range(1000):
        recorder(_trade(n, 100 if n < 998 else 1_200))
    assert report(recorder)[-1].endswith(": 1.5")


def test_default_instruments_are_read_from_the_venue_package_config() -> None:
    """The tool moved two levels down (`archive/tools/`): the config path must still resolve."""
    assert _default_instruments("bybit")  # the committed Bybit config lists its instruments


def test_dydx_default_instruments_come_from_the_mounted_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DYdX commits no config: its instruments are the plan `DYDX_PLAN_PATH` names (Story 26.3)."""
    plan = tmp_path / "dydx_config.toml"
    plan.write_text(
        'instruments = [\n    { id = "BTC-USD-PERP.DYDX" },\n'
        '    { id = "ETH-USD-PERP.DYDX", store_order_book_deltas = true },\n]\n'
    )
    monkeypatch.setenv("DYDX_PLAN_PATH", str(plan))
    assert _default_instruments("dydx") == ["BTC-USD-PERP.DYDX", "ETH-USD-PERP.DYDX"]


def test_the_committed_dydx_plan_yields_instrument_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """The plan compose mounts (`platform/data/dydx_config.toml`) lists tables, never bare ids."""
    # `make test` runs from the image's copy, which has no `data/`: read the mounted checkout's.
    platform_dir = os.environ.get("PLATFORM_SOURCE_DIR") or Path(__file__).resolve().parents[2]
    plan = Path(platform_dir) / "data" / "dydx_config.toml"
    monkeypatch.setenv("DYDX_PLAN_PATH", str(plan))
    ids = _default_instruments("dydx")
    assert ids
    assert all(isinstance(iid, str) and iid.endswith(".DYDX") for iid in ids)


def test_a_missing_dydx_plan_is_a_usage_error_not_an_empty_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DYDX_PLAN_PATH", str(tmp_path / "absent.toml"))
    with pytest.raises(SystemExit) as exited:
        main(["--venue", "dydx", "--seconds", "1"])
    assert exited.value.code == 2
    assert "DYDX_PLAN_PATH" in capsys.readouterr().err


# Bybit and Hyperliquid read their plan from the env var compose sets (DW-240), like dYdX.
_ENV_VENUES = pytest.mark.parametrize(
    ("venue", "env_var", "instrument_id"),
    [
        ("bybit", "BYBIT_COLLECTOR_CONFIG", "X-LINEAR.BYBIT"),
        ("hyperliquid", "HYPERLIQUID_COLLECTOR_CONFIG", "X-USD-PERP.HYPERLIQUID"),
    ],
)


@_ENV_VENUES
def test_the_env_named_plan_is_the_one_measured(
    venue: str,
    env_var: str,
    instrument_id: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = tmp_path / "config.toml"
    plan.write_text(f'instruments = ["{instrument_id}"]\n')
    monkeypatch.setenv(env_var, str(plan))
    assert _default_instruments(venue) == [instrument_id]


_ENV_VARS = pytest.mark.parametrize(
    ("venue", "env_var"),
    [("bybit", "BYBIT_COLLECTOR_CONFIG"), ("hyperliquid", "HYPERLIQUID_COLLECTOR_CONFIG")],
)


@_ENV_VARS
def test_an_empty_env_var_falls_back_to_the_committed_config(
    venue: str,
    env_var: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    committed = _default_instruments(venue)
    monkeypatch.setenv(env_var, "")
    assert committed
    assert _default_instruments(venue) == committed


def test_an_empty_dydx_plan_path_falls_back_to_the_frozen_container_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DYDX_PLAN_PATH", "")
    assert _plan_path("dydx") == Path("/app/dydx_collector/config.toml")


@_ENV_VARS
def test_an_env_var_naming_a_missing_plan_is_a_usage_error_naming_it(
    venue: str,
    env_var: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    absent = tmp_path / "absent.toml"
    monkeypatch.setenv(env_var, str(absent))
    with pytest.raises(SystemExit) as exited:
        main(["--venue", venue, "--seconds", "1"])
    assert exited.value.code == 2
    err = capsys.readouterr().err
    assert env_var in err
    assert str(absent) in err


def test_an_unreadable_dydx_plan_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = tmp_path / "dydx_config.toml"
    plan.write_text("instruments = [\n")
    monkeypatch.setenv("DYDX_PLAN_PATH", str(plan))
    with pytest.raises(SystemExit) as exited:
        main(["--venue", "dydx", "--seconds", "1"])
    assert exited.value.code == 2
