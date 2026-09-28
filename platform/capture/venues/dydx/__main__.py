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
dYdX capture's composition root: `python3 -m capture.venues.dydx` (Story 26.2; was the
`DydxCollector` subclass of the Story 22.1 core).

Owns its own asyncio loop through `run_forever`; no TradingNode/Strategy/DataEngine involved --
see client.py for why. `build_capture` wires dYdX's values into the one `CaptureService`: the
client, its policy values (per-level message-id tagging and the uncross ladder, `policies.py`,
DATA-04), the indexer trade history, the archive and live-stream adapters, the candle store, the
REST open-interest poll and the `[WS_RAW]` flush. What is collected is the collection-control
context's (Story 25.4, `collection_control/`): the plan in `config.toml` (`CollectionPlan`: the
`[[instruments]]` list, `exclude`, the 30-instrument cap under dYdX's 32-per-connection WS limit),
changed only by an explicit `collector:control` command or a hand edit of the file, and applied
through `CaptureService.apply`, whose `Applied` result is the fact `collector:status` reports.
`build_capture_from_file` wires that context's three loops (plan reload, `collector:status`,
`collector:control`) in through `add_loops`. No retention loop over the catalog: that is the
nightly `archive.prune_catalog`'s (Story 25.1).
"""

import asyncio
import functools
import logging
import os
import re
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Iterable
from pathlib import Path

from candles.application.prune import loop as candle_prune_loop
from candles.application.sink import CandleSink
from candles.infrastructure.sqlite_store import store_from_env
from collection_control.application.control import ControlService
from collection_control.application.reload import reload_loop
from collection_control.application.status import StatusPublisher
from collection_control.infrastructure.markets import DydxMarkets
from collection_control.infrastructure.plan_store import TomlPlanStore
from collection_control.infrastructure.redis import RedisControlChannel
from collection_control.infrastructure.redis import RedisStatusBus
from observability import incidents
from observability.incidents import IncidentConfig
from observability.incidents import IncidentRule

from capture.application import sites
from capture.application.capture_service import CaptureService
from capture.application.capture_service import run_forever
from capture.domain.policies import CapturePolicies
from capture.infrastructure.config import load_venue_config
from capture.infrastructure.parquet_writer import ParquetArchiveWriter
from capture.infrastructure.redis_stream import RedisLiveStream
from capture.infrastructure.redis_stream import redis_url_from_env
from capture.venues.dydx.client import DydxClient
from capture.venues.dydx.config import CONFIG_PATH
from capture.venues.dydx.config import DydxConfig
from capture.venues.dydx.open_interest import fetch_open_interest
from capture.venues.dydx.policies import DydxLevelTagger
from capture.venues.dydx.policies import DydxUncrossPolicy
from capture.venues.dydx.trade_history import DydxTradeHistory
from nautilus_trader.core import nautilus_pyo3


VENUE = "DYDX"

# The control-plane loops a composition root builds from the capture service it wires them into
# (`build_capture_from_file`); a capture test builds a service with none.
ControlPlane = Callable[[CaptureService], tuple[Callable[[], Awaitable[None]], ...]]


def _no_control_plane(_capture: CaptureService) -> tuple[Callable[[], Awaitable[None]], ...]:
    return ()


def build_capture(
    config: DydxConfig,
    plan_ids: Iterable[str],
    *,
    store_deltas: Iterable[str] = (),
    control_plane: ControlPlane = _no_control_plane,
) -> CaptureService:
    """
    Wire dYdX onto the shared write gate: the core owns ingest/flush/sample/write, the applied
    set and every rule; this function supplies dYdX's values and loops (see the module
    docstring). `plan_ids` are the plan's collected ids, `store_deltas` the ids whose raw deltas
    are archived; the control plane arrives as `control_plane`'s loops.
    """
    # This process owns dYdX's candle store (one file per venue, `CANDLES_DB_PATH`), so it opens
    # it, hands capture the sink port and runs the retention loop.
    store = store_from_env(config.catalog_path)
    capture = CaptureService(
        config,
        lambda on_data, _ledger: DydxClient(on_data=on_data, network=config.network),
        (
            # Raw-WS debug feed (Story 5.1) for the incident reports (INCIDENTS below) -- a
            # permanent feature, not scoped to any one investigation. Rust's file logger only
            # flushes its BufWriter to disk on an explicit Sync event -- without this, [WS_RAW]
            # lines sit in memory forever.
            functools.partial(incidents.raw_log_flush_loop, nautilus_pyo3.logging_sync_to_disk),
            candle_prune_loop(store),
        ),
        venue=VENUE,
        plan=plan_ids,
        archive=ParquetArchiveWriter(config.catalog_path),
        live_stream=RedisLiveStream(redis_url_from_env()),
        second_sink=CandleSink(store),
        policies=CapturePolicies(
            crossed=DydxUncrossPolicy(int(config.crossed_resync_seconds * 1e9)),
            tagger=DydxLevelTagger(),
        ),
        trade_history=DydxTradeHistory(config.environment),
        store_deltas=store_deltas,
    )
    capture.add_loops(
        # The indexer's markets endpoint carries every market: all of them are kept, as before.
        functools.partial(
            capture.poll_loop,
            lambda: fetch_open_interest(config.network),
            config.open_interest_poll_seconds,
            site=sites.OPEN_INTEREST_POLL,
            failure="failed to poll open interest",
            plan_only=False,
        ),
        *control_plane(capture),
    )
    return capture


# ---------------------------------------------------------------------------
# Incident reports (Story 5.1; the handler moved to `observability.incidents` in Story 23.1).
# Everything dYdX-specific about them is this one config: the id shape, the report title, where
# the Rust [WS_RAW] debug log lives and what one of its lines carries for an instrument.
# ---------------------------------------------------------------------------

INCIDENTS = IncidentConfig(
    report_title="dYdX Collector Incident Report",
    # `ticker` is what a [WS_RAW] line's "id" field carries for the instrument.
    iid_pattern=re.compile(r"\b(?P<iid>(?P<ticker>[A-Z0-9]+-USD)-PERP\.DYDX)\b"),
    evidence_needle='"id":"{ticker}"',
    # Deliberately /tmp: an ephemeral rolling buffer inside a single-purpose container (the
    # compose `collector` service), not a shared multi-tenant host -- no symlink/race risk.
    raw_log_dir=Path("/tmp/nautilus_logs"),  # noqa: S108
    raw_log_name="ws_raw_debug",
    # Bind-mounted (compose `./data/incident_reports`): must survive container restarts.
    report_dir=Path("/app/incident_reports"),
    # Tried in order; texts are the collector's own warning lines.
    rules=(
        IncidentRule("Crossed book", "crossed_book"),
        IncidentRule("Stale book", "stale_book"),
        IncidentRule("_second_loop tick arrived", "second_loop_lag", with_instrument=False),
        IncidentRule("Resyncing", "resync"),
    ),
)


def build_capture_from_file(config_path: Path = CONFIG_PATH) -> CaptureService:
    """
    Build the capture from its config file -- the composition root (DDD spine AD-D2/AD-D17): load
    the venue config and the plan through the one loader, and wire collection control's loops --
    plan reload, `collector:status`, `collector:control` -- into a fresh capture service. Called
    per `run_forever` attempt, so a restart starts from the plan the file holds now.
    """
    config, plan = load_venue_config(config_path, VENUE)
    if not isinstance(config, DydxConfig):
        raise TypeError(f"the DYDX loader returned {type(config).__name__}, not DydxConfig")
    redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")

    def control_plane(capture: CaptureService) -> tuple[Callable[[], Awaitable[None]], ...]:
        markets = DydxMarkets(config.network)
        status = StatusPublisher(capture, RedisStatusBus(redis_url), markets, accepts_commands=True)
        store = TomlPlanStore(config_path, VENUE)
        control = ControlService(plan, store, capture, status, markets)
        return (
            functools.partial(reload_loop, control, config.config_reload_seconds),
            functools.partial(status.loop, lambda: control.plan, config.liquidity_check_seconds),
            functools.partial(control.control_loop, RedisControlChannel(redis_url)),
        )

    return build_capture(
        config,
        plan.collected,
        store_deltas=plan.delta_store_ids,
        control_plane=control_plane,
    )


async def main() -> None:

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    incidents.prune_stale_raw_logs(INCIDENTS)
    logging.getLogger().addHandler(incidents.IncidentHandler(INCIDENTS))
    # Rust's `log` crate is a no-op until a logger is installed -- without this, any
    # `log::warn!`/`log::error!` inside the Rust WS client (including the exact path that
    # reports a failed `call_soon_threadsafe` scheduling, i.e. a delta silently never
    # reaching `_on_data`) is completely invisible: not filtered out, never emitted at all.
    # This was never wired up because this collector never touches TradingNode/Kernel
    # (the usual place nautilus_trader calls it). WARNING+ only -- surfaces hidden
    # failures without adding Rust-side INFO/DEBUG noise on top of the Python logging above.
    # init_logging() returns a LogGuard that MUST be kept alive for the process lifetime --
    # LogGuard's Drop impl (crates/common/src/logging/logger.rs:1343) treats the LAST guard
    # being dropped as subsystem shutdown: it sets a global bypass flag, disables the log
    # crate's max level, and joins/closes the logging thread. Discarding the return value
    # (as this call used to) means Python garbage-collects the guard within microseconds of
    # this call returning -- Rust-side logging was silently DEAD immediately after every
    # single startup, this whole time. `_log_guard` must stay a live reference for `main()`'s
    # entire lifetime (it does, since this coroutine runs until shutdown).
    _log_guard = nautilus_pyo3.init_logging(
        trader_id=nautilus_pyo3.TraderId("COLLECTOR-001"),
        instance_id=nautilus_pyo3.UUID4(),
        level_stdout=nautilus_pyo3.LogLevel.WARNING,
        # Permanent raw-WS debug feed (Story 5.1), not scoped to any one investigation --
        # feeds the incident-report subsystem below. DEBUG+ goes to a file, not stdout --
        # component_levels/log_components_only can only make Logger's filtering MORE
        # restrictive than the global stdout/fileout level, never less (see
        # Logger::enabled() in crates/common/src/logging/logger.rs), so there is no way
        # to raise just handler.rs's [WS_RAW] debug! line above stdout=WARNING without a
        # separate, permissive file sink.
        level_file=nautilus_pyo3.LogLevel.DEBUG,
        directory=str(INCIDENTS.raw_log_dir),
        file_name=INCIDENTS.raw_log_name,
        # Bounded rolling buffer, not a growing archive: [WS_RAW] is ~1MB/s. IncidentHandler
        # (above) auto-snapshots the relevant INCIDENTS.lookback_ns (10s) + a
        # INCIDENTS.lookahead_s (2s) window into a permanent incident report the
        # moment something WARNING+ worthy happens -- this buffer only needs to outlast that
        # ~12s window by a safety margin, not a human noticing and checking manually (that
        # was the old, much larger 500MB-nominal design this replaces). 20MB x 1 backup is
        # ~40MB nominal / ~40s -- over 3x the required window.
        #
        # Smaller than the old 250MB x 2 (~750MB nominal, and the underlying trigger for a
        # disk-full incident on nifelheim once restarts orphaned old rotations -- see
        # prune_stale_raw_logs). Rotating every ~20s at 20MB does mean nautilus_trader's
        # file writer's unconditional `eprintln!("Rotated log file...")` on every rotation
        # (crates/common/src/logging/writer.rs's rotate_file(), not routed through the
        # `log` crate, so log level can't silence it) fires more often -- purely docker-logs
        # noise nothing in this codebase reads (scan_raw_window globs every rotated
        # file, never depends on which one is "current"), traded deliberately for a much
        # smaller worst-case disk footprint.
        file_rotate=(20_000_000, 1),
    )

    # run_forever owns the restart loop, signal handling and per-process quarantine; it
    # must not install a second Rust logger over the WS_RAW file sink above.
    await run_forever(build_capture_from_file, init_rust_logging=False)


if __name__ == "__main__":
    asyncio.run(main())
