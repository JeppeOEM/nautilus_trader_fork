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

import pandas as pd
import pytest

from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.writer import RotationMode


def test_load_recorder_config_parses_linear_and_spot_instrument_ids(tmp_path, sample_toml):
    # Arrange
    from scripts.bybit_recorder.config import load_recorder_config

    toml_path = tmp_path / "recorder.toml"
    toml_path.write_text(sample_toml)

    # Act
    _, instrument_ids = load_recorder_config(toml_path)

    # Assert
    assert instrument_ids == [
        InstrumentId.from_str("BTCUSDT-LINEAR.BYBIT"),
        InstrumentId.from_str("ETHUSDT-SPOT.BYBIT"),
    ]


def test_load_recorder_config_parses_depth_and_bar_intervals(tmp_path, sample_toml):
    # Arrange
    from scripts.bybit_recorder.config import load_recorder_config

    toml_path = tmp_path / "recorder.toml"
    toml_path.write_text(sample_toml)

    # Act
    recorder_cfg, _ = load_recorder_config(toml_path)

    # Assert
    assert all(entry.depth == 50 and entry.bar_intervals == ["1-MINUTE"] for entry in recorder_cfg.instruments)


def test_build_streaming_config_uses_scheduled_dates_daily_rotation(tmp_path, sample_toml):
    # Arrange
    from scripts.bybit_recorder.config import build_streaming_config
    from scripts.bybit_recorder.config import load_recorder_config

    toml_path = tmp_path / "recorder.toml"
    toml_path.write_text(sample_toml)
    recorder_cfg, _ = load_recorder_config(toml_path)

    # Act
    streaming_config = build_streaming_config(recorder_cfg)

    # Assert
    assert streaming_config.rotation_mode == RotationMode.SCHEDULED_DATES
    assert streaming_config.rotation_interval == pd.Timedelta(days=1)
    assert streaming_config.rotation_timezone == "UTC"
    assert streaming_config.include_types == [TradeTick]


def test_load_recorder_config_rejects_malformed_instrument_id(tmp_path, sample_toml):
    # Arrange
    from scripts.bybit_recorder.config import load_recorder_config

    malformed_toml = sample_toml.replace('id = "BTCUSDT-LINEAR.BYBIT"', 'id = "not a valid id"')
    toml_path = tmp_path / "recorder.toml"
    toml_path.write_text(malformed_toml)

    # Act / Assert
    with pytest.raises(ValueError):
        load_recorder_config(toml_path)
