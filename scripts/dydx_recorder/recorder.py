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
from concurrent.futures import ThreadPoolExecutor
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
from scripts.common_recorder.shutdown_watchdog import ShutdownWatchdog
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
        # RESEARCH A1: dYdX is full-depth L2 with no depth knob — 0 means full
        # depth in Nautilus, matching DydxInstrumentEntry.depth so config-reload
        # diffs never see a spurious depth change.
        instrument_depths=dict.fromkeys(instrument_ids, 0),
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

    # MEM-01: register a single-thread executor so the strategy's periodic +
    # on_stop ParquetDataCatalog conversion runs OFF the asyncio event loop. The
    # dYdX WebSocket runtime schedules every parsed delta onto the loop via
    # call_soon_threadsafe into an unbounded ready-queue with no backpressure; any
    # loop-blocking conversion lets that queue flood and balloon RAM (the OOM-on-
    # Ctrl+C crash). One worker thread is enough — conversions never overlap (the
    # 60-min timer interval dwarfs a conversion) and the work is filesystem-bound.
    # max_workers=1 also serializes conversions so two never read the same finalized
    # feather set concurrently.
    conversion_executor = ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="dydx-recorder-convert",
    )
    strategy.register_executor(node.kernel.loop, conversion_executor)

    # MEM-03: inject the kernel event loop so the heartbeat mem-watch probe can
    # count pending asyncio tasks. The heartbeat fires on a Nautilus LiveTimer
    # tokio worker thread (crates/common/src/live/timer.rs), NOT the asyncio loop
    # thread, so asyncio.get_running_loop() raises there and pending_tasks logged
    # as "n/a" every sample (cycle-3b root cause). Handing the strategy the explicit
    # loop lets asyncio.all_tasks(loop) return a real count from the worker thread.
    strategy.set_loop(node.kernel.loop)

    # MEM-02: inject a shutdown hook so the strategy's on_stop halts the dYdX
    # WebSocket producer IMMEDIATELY, ahead of the kernel's own (delayed) client
    # disconnect. on_stop is the first step of kernel.stop_async(); the kernel does
    # not disconnect the data client until after on_stop AND a post-stop residual
    # sleep, and the dYdX adapter then sleeps another second before closing its WS.
    # During that ~3-4s window the dYdX Rust WS handler (separate runtime, unbounded
    # mpsc, no subscription gate, no backpressure) keeps calling
    # loop.call_soon_threadsafe() for every full-depth L2 delta across all
    # instruments; the loop saturates the bounded data queue and then spawns
    # UNBOUNDED create_task(put) coroutines -> RAM balloons on Ctrl+C (the OOM
    # crash). Closing the WS here closes the Rust mpsc receiver, so the handler loop
    # exits and the flood stops at the source. The call is idempotent with the
    # kernel's later disconnect (the Rust disconnect take()s the handler task and
    # the adapter _disconnect guards on is_closed()).
    loop = node.kernel.loop

    # MEM-04: recorder-side force-exit safety valve. A CONFIRMED race in nautilus
    # CORE (off-limits) can drop the data-queue shutdown sentinel under the dYdX
    # full-depth L2 flood, wedging the event loop so node.run() never returns; and
    # the kernel replaces the loop-level SIGINT handler with a no-op after the first
    # Ctrl+C, so the user "CANNOT on multiple ctrl c even close the process AT ALL"
    # (see debug session findings A/B/C). This watchdog runs on its OWN daemon
    # thread, independent of the (possibly wedged) asyncio loop: it ARMS at the
    # first shutdown signal (via the shutdown hook below) and, if the node has not
    # finished disposing within the configured grace period, calls os._exit() so
    # the process can ALWAYS be terminated within a bounded time. It does NOT fire
    # on a healthy shutdown — `mark_completed()` in the finally disarms it once
    # node.run() returns. The watchdog cannot fix the core race; it guarantees the
    # symptom "cannot close it" is eliminated.
    # MEM-04 (cycle 4c): DURABLE force-exit record. stderr is ephemeral (lost unless
    # the process runs under journald), so on a force-exit the watchdog ALSO appends
    # a timestamped line to a dedicated sibling file derived from the same logs
    # directory the TradingNodeConfig uses (log_directory="logs" above). This file
    # PERSISTS across restarts so the operator can correlate a watchdog kill — and
    # the data gap it implies — with the catalog AFTER the fact. It is a DEDICATED
    # file (NOT dydx_recorder.log, which the pyo3 writer owns) to avoid file-handle
    # contention with Nautilus's own log rotation/management. The write is bare
    # stdlib (open/write/flush/fsync) — deliberately NOT the pyo3 pipeline, which
    # may itself be wedged at the exact moment of a force-exit.
    watchdog_record_file = Path("logs") / "dydx_recorder_watchdog.log"
    watchdog_record_file.parent.mkdir(parents=True, exist_ok=True)

    watchdog = ShutdownWatchdog(
        grace_seconds=float(recorder_cfg.shutdown_watchdog_grace_seconds),
        # Bare stderr line: the asyncio-routed logger may itself be wedged when the
        # watchdog fires, so write directly to stderr (flushed) before os._exit.
        log=lambda message: print(message, file=sys.stderr, flush=True),
        record_file=watchdog_record_file,
    )
    watchdog.start()

    def _shutdown_hook() -> None:
        # MEM-04: arm the force-exit watchdog at the FIRST shutdown signal. on_stop
        # is the first step of kernel.stop_async() (fired right after the first
        # Ctrl+C), so arming here starts the bounded grace countdown exactly when
        # graceful shutdown begins. Arming is idempotent; a second Ctrl+C will not
        # shorten the deadline.
        watchdog.arm()

        # MEM-02: halt the dYdX WS producer immediately, ahead of the kernel's own
        # delayed client disconnect, to collapse the call_soon_threadsafe flood
        # window that balloons RAM on Ctrl+C.
        ws_client = getattr(data_client, "_ws_client", None)
        if ws_client is None or ws_client.is_closed():
            return
        # on_stop runs on the loop thread; schedule the pyo3 disconnect coroutine
        # so the WS closes on the next loop turn (one turn, not the kernel's
        # multi-second delayed path).
        loop.create_task(ws_client.disconnect())

    strategy.set_shutdown_hook(_shutdown_hook)

    try:
        # WHY: raise_exception=True so an on_start failure (e.g. missing
        # instruments, D-05/D-06) propagates out of main() and exits the process
        # non-zero -- systemd/journald must surface this. The default (False) only
        # logs and swallows the error, which would otherwise look like a clean exit.
        node.run(raise_exception=True)
    finally:
        # MEM-04: DISARM the watchdog FIRST, before draining the executor or
        # disposing the node. Reaching this finally at all already proves
        # node.run() returned (i.e. loop.run_until_complete completed) — which is
        # precisely the ONLY failure mode this watchdog is evidenced to guard
        # against: the documented nautilus-core data-queue sentinel-loss race that
        # wedges the loop so node.run() never returns. Once node.run() returns,
        # that race did NOT occur, so the watchdog has done its job and must stand
        # down immediately.
        #
        # WHY ordering matters (data-integrity): the watchdog's grace deadline
        # starts at the FIRST Ctrl+C (armed in the on_stop hook). The on_stop hook
        # also triggers a final ParquetDataCatalog conversion offloaded to
        # conversion_executor. For a large backlog (conversion_interval_minutes=60,
        # full L2 depth, multiple instruments) that conversion can legitimately run
        # LONGER than the grace period. If we disarmed only AFTER
        # conversion_executor.shutdown(wait=True), a perfectly healthy but slow
        # final conversion would trip the watchdog and os._exit() MID-CONVERSION,
        # truncating an in-progress parquet/feather write. Disarming here scopes the
        # watchdog correctly to the loop-wedge race and never force-kills a
        # legitimately slow drain/dispose.
        watchdog.mark_completed()
        watchdog.stop()

        # MEM-01: drain the conversion worker before disposing the node so a
        # final on_stop conversion (offloaded above) can complete and no thread is
        # leaked. wait=True blocks only THIS (main) thread post-run — the event
        # loop is already stopping — so it cannot reintroduce the loop-blocking
        # behavior the executor was added to avoid.
        conversion_executor.shutdown(wait=True)
        node.dispose()


if __name__ == "__main__":
    config_arg = sys.argv[1] if len(sys.argv) > 1 else str(_DEFAULT_CONFIG_PATH)
    main(config_arg)
