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

import pytest

from nautilus_trader.core.nautilus_pyo3 import DydxNetwork
from nautilus_trader.model.identifiers import InstrumentId


_VALID_DYDX_TOML = """
[recorder]
trader_id = "DYDX-COLLECTOR-001"
catalog_path = "catalog"
streaming_path = "catalog/streaming"
conversion_interval_minutes = 60
environment = "mainnet"

[recorder.stale_threshold_seconds]
trade = 90
quote = 600
deltas = 60

[[instruments]]
id = "BTC-USD-PERP.DYDX"
bar_intervals = ["1-MINUTE"]

[[instruments]]
id = "ETH-USD-PERP.DYDX"
bar_intervals = ["1-MINUTE"]
"""


def test_load_dydx_recorder_config_parses_flat_perp_instrument_ids(tmp_path):
    # Arrange
    from scripts.dydx_recorder.config import load_dydx_recorder_config

    toml_path = tmp_path / "recorder.toml"
    toml_path.write_text(_VALID_DYDX_TOML)

    # Act
    recorder_cfg, instrument_ids = load_dydx_recorder_config(toml_path)

    # Assert
    assert len(recorder_cfg.instruments) == 2
    assert instrument_ids == [
        InstrumentId.from_str("BTC-USD-PERP.DYDX"),
        InstrumentId.from_str("ETH-USD-PERP.DYDX"),
    ]


def test_load_dydx_recorder_config_rejects_unsupported_bar_interval(tmp_path):
    # DYDX-06: a bar interval not in the supported dYdX resolution set must fail
    # fast at config load, naming the offending interval and the supported set.
    from scripts.dydx_recorder.config import _DYDX_VALID_INTERVALS
    from scripts.dydx_recorder.config import load_dydx_recorder_config

    bad_toml = _VALID_DYDX_TOML.replace(
        'bar_intervals = ["1-MINUTE"]',
        'bar_intervals = ["2-MINUTE"]',
        1,
    )
    toml_path = tmp_path / "recorder.toml"
    toml_path.write_text(bad_toml)

    # Act / Assert
    with pytest.raises(ValueError, match="2-MINUTE") as excinfo:
        load_dydx_recorder_config(toml_path)

    # The error must list the supported set so the operator can correct the TOML.
    message = str(excinfo.value)
    assert str(sorted(_DYDX_VALID_INTERVALS)) in message


@pytest.mark.parametrize(
    "interval",
    [
        "1-MINUTE",
        "5-MINUTE",
        "15-MINUTE",
        "30-MINUTE",
        "1-HOUR",
        "4-HOUR",
        "1-DAY",
    ],
)
def test_load_dydx_recorder_config_accepts_every_supported_interval(tmp_path, interval):
    # DYDX-06: each supported resolution must load without error.
    from scripts.dydx_recorder.config import load_dydx_recorder_config

    good_toml = _VALID_DYDX_TOML.replace(
        'bar_intervals = ["1-MINUTE"]',
        f'bar_intervals = ["{interval}"]',
    )
    toml_path = tmp_path / "recorder.toml"
    toml_path.write_text(good_toml)

    # Act
    recorder_cfg, _ = load_dydx_recorder_config(toml_path)

    # Assert
    assert all(entry.bar_intervals == [interval] for entry in recorder_cfg.instruments)


def test_load_dydx_recorder_config_rejects_malformed_instrument_id(tmp_path):
    # InstrumentId.from_str boundary (V5): a malformed id fails fast at load.
    from scripts.dydx_recorder.config import load_dydx_recorder_config

    bad_toml = _VALID_DYDX_TOML.replace('id = "BTC-USD-PERP.DYDX"', 'id = "NOTANID"')
    toml_path = tmp_path / "recorder.toml"
    toml_path.write_text(bad_toml)

    # Act / Assert
    with pytest.raises(ValueError):
        load_dydx_recorder_config(toml_path)


@pytest.mark.parametrize(
    ("env_string", "expected"),
    [
        ("mainnet", DydxNetwork.MAINNET),
        ("MAINNET", DydxNetwork.MAINNET),
        ("testnet", DydxNetwork.TESTNET),
        ("TESTNET", DydxNetwork.TESTNET),
    ],
)
def test_map_network_is_case_insensitive(env_string, expected):
    # DYDX-02 / Pitfall 3: environment string maps case-insensitively to DydxNetwork.
    from scripts.dydx_recorder.config import _map_network

    assert _map_network(env_string) is expected


def test_load_dydx_recorder_config_defaults_environment_to_mainnet(tmp_path):
    # Missing environment defaults to mainnet (matches 24/7 archival intent).
    from scripts.dydx_recorder.config import _map_network
    from scripts.dydx_recorder.config import load_dydx_recorder_config

    no_env_toml = _VALID_DYDX_TOML.replace('environment = "mainnet"\n', "")
    toml_path = tmp_path / "recorder.toml"
    toml_path.write_text(no_env_toml)

    # Act
    recorder_cfg, _ = load_dydx_recorder_config(toml_path)

    # Assert
    assert recorder_cfg.environment == "mainnet"
    assert _map_network(recorder_cfg.environment) is DydxNetwork.MAINNET


def test_load_dydx_recorder_config_parses_stale_thresholds(tmp_path):
    # DYDX-05: relaxed quote / tight deltas thresholds round-trip into the config.
    from scripts.dydx_recorder.config import load_dydx_recorder_config

    toml_path = tmp_path / "recorder.toml"
    toml_path.write_text(_VALID_DYDX_TOML)

    # Act
    recorder_cfg, _ = load_dydx_recorder_config(toml_path)

    # Assert
    assert recorder_cfg.stale_threshold_seconds["quote"] == 600
    assert recorder_cfg.stale_threshold_seconds["deltas"] == 60


@pytest.mark.parametrize("bad_value", [0, -1])
def test_load_dydx_recorder_config_rejects_non_positive_threshold(tmp_path, bad_value):
    # DYDX-05 / T-07-05: a non-positive stale threshold is rejected at load
    # (delegated to the shared _validate_positive_thresholds).
    from scripts.dydx_recorder.config import load_dydx_recorder_config

    bad_toml = _VALID_DYDX_TOML.replace("quote = 600", f"quote = {bad_value}")
    toml_path = tmp_path / "recorder.toml"
    toml_path.write_text(bad_toml)

    # Act / Assert
    with pytest.raises(ValueError, match="must be positive"):
        load_dydx_recorder_config(toml_path)


def test_dydx_config_has_no_depth_or_product_split():
    # dYdX is full-depth L2 with no spot market: the config surface must not carry
    # a depth knob or a linear/spot product axis (RESEARCH anti-pattern).
    from scripts.dydx_recorder.config import DydxInstrumentEntry
    from scripts.dydx_recorder.config import DydxRecorderConfig

    entry_fields = set(DydxInstrumentEntry.__struct_fields__)
    assert "depth" not in entry_fields
    assert "product_type" not in entry_fields
    assert not hasattr(DydxRecorderConfig, "linear_instrument_ids")
