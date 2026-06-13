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

import sys
from pathlib import Path

from nautilus_trader.adapters.bybit import BYBIT
from nautilus_trader.adapters.bybit import BybitDataClientConfig
from nautilus_trader.adapters.bybit import BybitEnvironment
from nautilus_trader.adapters.bybit import BybitLiveDataClientFactory
from nautilus_trader.adapters.bybit import BybitProductType
from nautilus_trader.common.config import CUSTOM_ENCODINGS
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import TraderId
from scripts.bybit_recorder.config import build_streaming_config
from scripts.bybit_recorder.config import load_recorder_config
from scripts.bybit_recorder.strategy import RecorderStrategy
from scripts.bybit_recorder.strategy import RecorderStrategyConfig


# WHY: must be a valid v4 UUID -- UUID4.from_str rejects human labels (Pitfall 4);
# fixed so the feather dir is stable across restarts (D-03).
RECORDER_INSTANCE_ID = "8f1b9c2e-1d3a-4b6c-8e7f-0a1b2c3d4e5f"

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "recorder.toml"

# WHY: NautilusKernel._setup_streaming() serializes the full TradingNodeConfig
# (including data_clients) via config.json(). msgspec_encoding_hook has no
# branch for pyo3-native adapter enums like BybitProductType/BybitEnvironment,
# so encoding raises TypeError. Register them via the official CUSTOM_ENCODINGS
# extension point rather than patching the framework hook.
CUSTOM_ENCODINGS[BybitProductType] = lambda value: value.name
CUSTOM_ENCODINGS[BybitEnvironment] = lambda value: value.name


def main(config_path: str) -> None:
    """
    Build and run the Bybit recorder `TradingNode`.

    Parameters
    ----------
    config_path : str
        The path to the `recorder.toml` configuration file.

    """
    recorder_cfg, instrument_ids = load_recorder_config(config_path)

    streaming = build_streaming_config(recorder_cfg)

    config_node = TradingNodeConfig(
        trader_id=TraderId(recorder_cfg.trader_id),
        instance_id=UUID4.from_str(RECORDER_INSTANCE_ID),
        logging=LoggingConfig(
            log_level="INFO",
            log_level_file="INFO",
            log_directory="logs",
            log_file_name="bybit_recorder",
            use_pyo3=True,
        ),
        streaming=streaming,
        data_clients={
            BYBIT: BybitDataClientConfig(
                environment=BybitEnvironment.MAINNET,
                product_types=(BybitProductType.LINEAR, BybitProductType.SPOT),
                instrument_provider=InstrumentProviderConfig(load_ids=frozenset(instrument_ids)),
            ),
        },
        timeout_connection=20.0,
        timeout_disconnection=10.0,
        timeout_post_stop=1.0,
    )

    strategy_cfg = RecorderStrategyConfig(
        instrument_ids=instrument_ids,
        # CRITICAL: must equal the catalog_path used in build_streaming_config so
        # conversion finds feather under {root}/live/{instance_id}/ (Pitfall 5/A4).
        catalog_path=recorder_cfg.streaming_path,
        instance_id_str=RECORDER_INSTANCE_ID,
        conversion_interval_minutes=recorder_cfg.conversion_interval_minutes,
    )

    node = TradingNode(config=config_node)
    node.trader.add_strategy(RecorderStrategy(config=strategy_cfg))
    node.add_data_client_factory(BYBIT, BybitLiveDataClientFactory)
    node.build()

    try:
        # WHY: raise_exception=True so an on_start failure (e.g. missing
        # instruments, D-05/D-06) propagates out of main() and exits the
        # process non-zero -- systemd/journald must surface this (T-01-10/A3).
        # The default (False) only logs and swallows the error, which would
        # otherwise look like a clean exit.
        node.run(raise_exception=True)
    finally:
        node.dispose()


if __name__ == "__main__":
    config_arg = sys.argv[1] if len(sys.argv) > 1 else str(_DEFAULT_CONFIG_PATH)
    main(config_arg)
