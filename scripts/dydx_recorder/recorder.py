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

from nautilus_trader.adapters.dydx import DYDX
from nautilus_trader.adapters.dydx.config import DydxDataClientConfig
from nautilus_trader.adapters.dydx.factories import DydxLiveDataClientFactory
from nautilus_trader.common.config import CUSTOM_ENCODINGS
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.core.nautilus_pyo3 import DydxNetwork
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import TraderId
from scripts.common_recorder.strategy import RecorderStrategy
from scripts.common_recorder.strategy import RecorderStrategyConfig
from scripts.dydx_recorder.config import _map_network
from scripts.dydx_recorder.config import build_streaming_config
from scripts.dydx_recorder.config import load_dydx_recorder_config


# WHY: must be a valid v4 UUID -- UUID4.from_str rejects human labels (Pitfall 4);
# fixed so the feather dir is stable across restarts (D-03). DISTINCT from the
# Bybit recorder's instance id so the two recorders' feather dirs never collide
# under the shared catalog root (T-07-08).
RECORDER_INSTANCE_ID = "3c4d5e6f-7a8b-49c0-a1d2-e3f405162738"

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "recorder.toml"

# WHY: NautilusKernel._setup_streaming() serializes the full TradingNodeConfig
# (including data_clients) via config.json(). msgspec_encoding_hook has no branch
# for the pyo3-native DydxNetwork enum, so encoding raises TypeError. Register it
# via the official CUSTOM_ENCODINGS extension point rather than patching the
# framework hook (T-07-06). NOTE: DydxNetwork.MAINNET.name == "mainnet" (lowercase
# — the pyo3 enum reflects the Rust serde rename, Pitfall 3).
CUSTOM_ENCODINGS[DydxNetwork] = lambda value: value.name


def main(config_path: str) -> None:
    """
    Build and run the dYdX recorder `TradingNode`.

    Parameters
    ----------
    config_path : str
        The path to the dYdX `recorder.toml` configuration file.

    """
    recorder_cfg, instrument_ids = load_dydx_recorder_config(config_path)

    streaming = build_streaming_config(recorder_cfg)

    config_node = TradingNodeConfig(
        trader_id=TraderId(recorder_cfg.trader_id),
        instance_id=UUID4.from_str(RECORDER_INSTANCE_ID),
        logging=LoggingConfig(
            log_level="INFO",
            log_level_file="INFO",
            log_directory="logs",
            log_file_name="dydx_recorder",
            use_pyo3=True,
        ),
        streaming=streaming,
        data_clients={
            # dYdX has no product-type axis (dYdX v4 has no spot market), so DO NOT
            # pass product_types as the Bybit recorder does.
            DYDX: DydxDataClientConfig(
                environment=_map_network(recorder_cfg.environment),
                instrument_provider=InstrumentProviderConfig(load_ids=frozenset(instrument_ids)),
            ),
        },
        timeout_connection=20.0,
        timeout_disconnection=10.0,
        timeout_post_stop=1.0,
    )

    strategy_cfg = RecorderStrategyConfig(
        instrument_ids=instrument_ids,
        # RESEARCH A1: ALL dYdX perps are linear, so mark/index/funding are
        # subscribed for every configured instrument.
        linear_instrument_ids=instrument_ids,
        # RESEARCH A1: dYdX is full-depth L2 with no depth knob — the adapter
        # ignores the depth argument. A dummy fixed depth satisfies the shared
        # strategy's per-instrument depth requirement for subscribe_order_book_deltas.
        instrument_depths={instrument_id: 50 for instrument_id in instrument_ids},
        # Per-instrument bar intervals carried from the parsed entries so the
        # strategy can issue the bar subscriptions.
        instrument_bar_intervals={
            entry.id: entry.bar_intervals for entry in recorder_cfg.instruments
        },
        # CRITICAL: must equal the streaming root used in build_streaming_config so
        # conversion finds feather under {root}/live/{instance_id}/ (Pitfall 5/A4).
        catalog_path=recorder_cfg.streaming_path,
        instance_id_str=RECORDER_INSTANCE_ID,
        conversion_interval_minutes=recorder_cfg.conversion_interval_minutes,
        rotation_interval_minutes=recorder_cfg.rotation_interval_minutes,
        # D-06 gap-visibility threshold for the on_start restart-gap WARNING.
        restart_gap_threshold_seconds=recorder_cfg.restart_gap_threshold_seconds,
        # REL-03 heartbeat/stale-stream visibility.
        heartbeat_interval_seconds=recorder_cfg.heartbeat_interval_seconds,
        stale_threshold_default_seconds=recorder_cfg.stale_threshold_default_seconds,
        stale_threshold_seconds=recorder_cfg.stale_threshold_seconds,
        # HOT-01 / D-08: lifetime hot-add WARNING threshold (informational).
        max_hot_added_instruments=recorder_cfg.max_hot_added_instruments,
        # HOT-01 / D-01: the absolute toml path `_on_config_reload` re-reads each
        # poll to diff against the running subscription set. Pass the same path the
        # recorder was launched with so the reload sees the operator's live edits.
        reload_config_path=str(config_path),
    )

    node = TradingNode(config=config_node)
    # RESEARCH anti-pattern: reuse the shared RecorderStrategy verbatim via
    # composition — NO dYdX strategy subclass. dYdX differs from Bybit only in the
    # factory/enum/config swap and the all-perps-are-linear mapping above.
    strategy = RecorderStrategy(config=strategy_cfg)
    node.trader.add_strategy(strategy)
    node.add_data_client_factory(DYDX, DydxLiveDataClientFactory)
    node.build()

    # HOT-01 / Pattern 3: inject the live dYdX data client into the strategy so its
    # config-reload ADD branch can load a brand-new instrument at runtime via
    # `client.instrument_provider`.
    #
    # WHY the private `_clients` registry: `Actor.request_instrument()` is the
    # public path for runtime instrument loading, but the live data client does
    # NOT implement it (Pitfall 1). The verified fallback reaches the client's
    # `instrument_provider` directly. There is no public `DataEngine.get_client`;
    # the registry is the private `_clients` dict (engine.pyx), reached via the
    # public `node.kernel.data_engine` property. This wiring stays entirely in
    # scripts/dydx_recorder/ — no core edit.
    data_client = node.kernel.data_engine._clients[ClientId(DYDX)]
    strategy.set_data_client(data_client)

    # DYDX-01: inject the dYdX config loader so the hot-reload path re-reads
    # recorder.toml. The loader is INJECTED (not imported by the common strategy)
    # to keep common_recorder venue-agnostic.
    strategy.set_config_loader(load_dydx_recorder_config)

    try:
        # WHY: raise_exception=True so an on_start failure (e.g. missing
        # instruments, D-05/D-06) propagates out of main() and exits the process
        # non-zero -- systemd/journald must surface this. The default (False) only
        # logs and swallows the error, which would otherwise look like a clean exit.
        node.run(raise_exception=True)
    finally:
        node.dispose()


if __name__ == "__main__":
    config_arg = sys.argv[1] if len(sys.argv) > 1 else str(_DEFAULT_CONFIG_PATH)
    main(config_arg)
