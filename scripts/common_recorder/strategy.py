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
Exchange-agnostic recorder strategy shared by every venue recorder (DYDX-01).

``RecorderStrategy`` validates configured instruments, subscribes to all recorded
feeds, periodically converts streamed feather data into the `ParquetDataCatalog`,
and applies live hot-reload diffs. It imports NOTHING from any venue recorder
package: the only venue coupling — the hot-reload config loader — is INJECTED via
``set_config_loader`` (parallel to ``set_data_client``) so this module never
imports a venue config module.
"""

import asyncio
import logging
from datetime import time

import pandas as pd

from nautilus_trader.common.component import TimeEvent
from nautilus_trader.common.config import PositiveInt
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import FundingRateUpdate
from nautilus_trader.model.data import IndexPriceUpdate
from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog
from nautilus_trader.persistence.writer import RotationMode
from nautilus_trader.persistence.writer import StreamingFeatherWriter
from nautilus_trader.trading.strategy import Strategy


logger = logging.getLogger(__name__)

# The six auto-written native types streamed by the recorder (REC-02..REC-04,
# REC-06). FundingRateUpdate is intentionally absent: it is deduped on
# value-change and persisted via a separate writer in Plan 02 (D-01 / Pitfall 1).
_RECORDED_TYPES = [
    TradeTick,
    QuoteTick,
    OrderBookDeltas,
    Bar,
    MarkPriceUpdate,
    IndexPriceUpdate,
]

# Sub-directory (within the feather writer path) for the strategy-owned funding
# writer — kept separate from the kernel "*" writer's root files (Pitfall 1).
_FUNDING_WRITER_SUBDIR = "funding"

# Module-level fallback config loader (DYDX-01). The PRIMARY wiring is the
# per-instance `set_config_loader` injected by each venue's recorder.py after
# build (parallel to set_data_client). This module-level default is a back-compat
# convenience a venue recorder package may register so a strategy built WITHOUT an
# explicit per-instance loader (e.g. in unit tests via the venue re-export shim)
# still resolves the venue loader at reload time. It is set by the venue package,
# NEVER imported here, so common_recorder stays venue-agnostic.
_default_config_loader = None


def set_default_config_loader(loader) -> None:
    """
    Register a module-level fallback config loader for hot-reload (DYDX-01).

    A venue recorder package may call this at import time so a ``RecorderStrategy``
    built without an explicit per-instance ``set_config_loader`` still resolves the
    venue's ``load_recorder_config`` during ``_on_config_reload``. The per-instance
    loader injected via ``set_config_loader`` always takes precedence.

    Parameters
    ----------
    loader : callable | None
        A callable ``(path) -> tuple[RecorderConfig, list[InstrumentId]]``.

    """
    global _default_config_loader
    _default_config_loader = loader


def _read_self_rss_mb() -> float | None:
    """
    Return this process's resident set size in MiB, or ``None`` if unavailable.

    Reads ``/proc/self/status`` (Linux-only, stdlib-only — no psutil dependency)
    so the heartbeat memory sample adds no new runtime requirement. Returns
    ``None`` on any non-Linux platform or read error so the heartbeat never fails
    just because the RSS probe is unsupported (MEM-03 observability).
    """
    try:
        with open("/proc/self/status") as status:
            for line in status:
                if line.startswith("VmRSS:"):
                    # Format: "VmRSS:\t   123456 kB"
                    kib = int(line.split()[1])
                    return kib / 1024.0
    except (OSError, ValueError, IndexError):
        return None
    return None


def _pending_task_count(loop: asyncio.AbstractEventLoop | None = None) -> int | None:
    """
    Return the number of asyncio tasks alive on ``loop``, or ``None``.

    The ThrottledEnqueuer overflow hypothesis (MEM-03) predicts this count climbs
    in lockstep with RSS during steady-state overload, because a saturated data
    queue spawns one ``create_task(queue.put(...))`` per excess datum. Sampling it
    alongside RSS distinguishes "memory held by pending put() tasks" from other
    growth.

    CRITICAL (cycle 3b root cause): the heartbeat is fired by a Nautilus
    ``LiveTimer``, whose Python callback runs on the GLOBAL TOKIO RUNTIME worker
    thread (``crates/common/src/live/timer.rs`` ``rt.spawn`` -> ``callback.call``),
    NOT on the asyncio event-loop thread. ``asyncio.get_running_loop()`` therefore
    raises ``RuntimeError`` ("no running event loop") on that worker thread, which
    the old ``except RuntimeError: return None`` swallowed -> ``pending_tasks=n/a``
    on every sample (confirmed by the cycle-3b live run). The fix is to pass an
    EXPLICIT loop reference (captured at build time, see ``set_loop`` /
    ``_resolve_loop``): ``asyncio.all_tasks(loop)`` only reads the loop's task
    registry and does not require the loop to be running on the calling thread, so
    it returns a real integer from the tokio worker thread.

    Parameters
    ----------
    loop : asyncio.AbstractEventLoop | None
        The event loop whose tasks to count. When ``None`` (no loop wired and no
        running loop discoverable) the probe degrades to ``None`` rather than
        raising, so the heartbeat never fails just because the loop is unknown.

    """
    if loop is None:
        # Best-effort fallback: a running loop on THIS thread (e.g. unit tests that
        # call from within an asyncio loop). On the live tokio worker thread this
        # still raises -> None, which is why the caller passes an explicit loop.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
    try:
        return len(asyncio.all_tasks(loop))
    except RuntimeError:
        # all_tasks can raise if the loop is closed/disposed mid-shutdown; degrade
        # gracefully so the heartbeat line stays well-formed.
        return None


class RecorderStrategyConfig(StrategyConfig, frozen=True):
    """
    Configuration for ``RecorderStrategy`` instances.

    Parameters
    ----------
    instrument_ids : list[InstrumentId]
        The configured instrument identifiers to validate and subscribe to.
    linear_instrument_ids : list[InstrumentId]
        The LINEAR-only instrument identifiers — mark/index price subscriptions
        are gated to these (D-04); spot instruments never receive them.
    instrument_depths : dict[InstrumentId, int]
        The per-instrument order book depth used for `subscribe_order_book_deltas`.
    instrument_bar_intervals : dict[InstrumentId, list[str]]
        The per-instrument bar interval strings (e.g. ``["1-MINUTE"]``) used to
        build `BarType` values for `subscribe_bars`.
    catalog_path : str
        The path to the `ParquetDataCatalog` root used for conversion. Must equal
        the streaming root used by `StreamingConfig` (Pitfall 5 / A4).
    instance_id_str : str
        The fixed UUID4 string shared with the `TradingNode` instance_id (D-03).
    conversion_interval_minutes : PositiveInt, default 60
        How often the in-process conversion timer fires (REL-01/D-01).
    rotation_interval_minutes : PositiveInt, default 1440
        How often the strategy-owned funding writer rotates to a new file. Must
        match the kernel "*" writer's `rotation_interval` (set via
        `build_streaming_config`) so `_convert_stream` finds a consistent set of
        rotated-out (finalized) feather files across all converted types.
    restart_gap_threshold_seconds : PositiveInt, default 60
        On `on_start`, if the gap since the last recorded `ts_init` for an
        instrument exceeds this threshold, log a WARNING so restart-induced gaps
        are visible in journald (REL-02 gap visibility, D-06).
    heartbeat_interval_seconds : PositiveInt, default 30
        How often the heartbeat timer fires (REL-03). Each firing logs an INFO
        heartbeat and a WARNING for any stream whose idle time exceeds its
        per-data-type stale threshold. The ``config-reload`` timer (HOT-01) reuses
        this same cadence (D-02).
    max_hot_added_instruments : PositiveInt, default 50
        The maximum number of instruments that may be hot-added over the
        recorder's lifetime before a WARNING is logged (HOT-01 / D-08).
        Informational only — never blocks further hot-adds.
    reload_config_path : str | None, default None
        The absolute path to ``recorder.toml`` that ``_on_config_reload`` re-reads
        each poll to diff against the running subscription set (HOT-01 / D-01).
        When ``None`` the reload callback is a logged no-op (the timer is still
        registered); the wiring of this value from ``recorder.py`` lands in
        Plan 02.
    stale_threshold_seconds : dict[str, int], default {}
        Per-stream-label stale thresholds (in seconds), keyed by the labels used
        in `_last_seen` (e.g. ``"trade"``, ``"quote"``, ``"deltas"``, ``"bar"``,
        ``"mark"``, ``"index"``, ``"funding"``). A stream label not present in
        this dict falls back to `stale_threshold_default_seconds`.
    stale_threshold_default_seconds : PositiveInt, default 90
        The fallback stale threshold (in seconds) for any stream label not
        present in `stale_threshold_seconds`.

    """

    instrument_ids: list[InstrumentId]
    linear_instrument_ids: list[InstrumentId]
    instrument_depths: dict[InstrumentId, int]
    instrument_bar_intervals: dict[InstrumentId, list[str]]
    catalog_path: str
    instance_id_str: str
    conversion_interval_minutes: PositiveInt = 60
    rotation_interval_minutes: PositiveInt = 1440
    restart_gap_threshold_seconds: PositiveInt = 60
    heartbeat_interval_seconds: PositiveInt = 30
    max_hot_added_instruments: PositiveInt = 50
    reload_config_path: str | None = None
    stale_threshold_seconds: dict[str, int] = {}
    stale_threshold_default_seconds: PositiveInt = 90


class RecorderStrategy(Strategy):
    """
    Validate configured instruments, subscribe to trade ticks, and periodically
    convert streamed feather data into the `ParquetDataCatalog` (CONF-03, REC-01,
    REL-01).

    Parameters
    ----------
    config : RecorderStrategyConfig
        The configuration for the instance.

    """

    def __init__(self, config: RecorderStrategyConfig) -> None:
        super().__init__(config)

        # Per-instrument last-seen funding rate, used by `on_funding_rate` to
        # drop unchanged values (D-01 dedup gate, Pitfall 1). Keyed by
        # InstrumentId so dedup is per-instrument (T-2-02).
        self._last_funding_rate: dict[InstrumentId, object] = {}

        # Per-stream last-seen timestamp (ns), keyed by (stream, instrument_id)
        # (REL-03). Updated in every `on_*` handler; consulted by `_heartbeat`
        # to detect a stream that has gone quiet beyond its stale threshold.
        self._last_seen: dict[tuple[str, InstrumentId], int] = {}

        # Strategy-owned StreamingFeatherWriter for deduped FundingRateUpdate
        # rows (Task 1 resolved path) -- lazily created on first persisted
        # funding rate so `self.cache`/`self.clock` are available (set during
        # `register`, not `__init__`).
        self._funding_writer: StreamingFeatherWriter | None = None

        # --- Hot-reload (HOT-01) bookkeeping (RESEARCH Pattern 2) ---------------
        # What params each instrument is currently subscribed at -- drives the
        # depth/bar-interval swap detection in `_diff_config` (depth, bar_intervals).
        self._subscribed_params: dict[InstrumentId, tuple[int, frozenset[str]]] = {}
        # Product type ("linear"/"spot") per currently-subscribed instrument, so a
        # REMOVAL can gate the linear-only mark/index/funding unsubscribes
        # (Pitfall 5) without re-deriving from a config that no longer lists the id.
        self._product_types: dict[InstrumentId, str] = {}
        # Instrument ids that failed to load/validate for the CURRENT toml snapshot
        # (D-07/D-11) -- not re-attempted until the toml changes. Consumed by the
        # Plan 02 ADDITION branch.
        self._failed_instrument_ids: set[InstrumentId] = set()
        # Stable signature of the last-seen parsed config; `_failed_instrument_ids`
        # is reset whenever this changes (D-07: only re-attempt on toml change).
        self._last_config_signature: int | None = None
        # Count of instruments hot-added over the process lifetime (D-08 threshold).
        self._hot_added_count: int = 0
        # Ids whose runtime load was scheduled on a prior poll and is still
        # in-flight (D-11). Distinguishes "load scheduled, awaiting resolution"
        # from "load resolved empty" so a load is fired at most once per snapshot
        # and an unresolved id can be declared venue-unknown on the NEXT poll.
        self._pending_loads: set[InstrumentId] = set()
        # Reference to the live venue data client, injected at build time by
        # `set_data_client` (recorder.py, Pattern 3). The ADDITION branch reaches
        # `client.instrument_provider` to load a brand-new instrument at runtime.
        self._data_client = None
        # Injected venue config loader for hot-reload (DYDX-01). Wired by the
        # venue's recorder.py via `set_config_loader` so this common strategy
        # never imports a venue config module. None until injected.
        self._config_loader = None
        # Injected venue shutdown hook (MEM-02). Wired by the venue's recorder.py
        # via `set_shutdown_hook` (parallel to set_config_loader) so this common
        # strategy never imports a venue module. Called FIRST in `on_stop` to halt
        # the venue WebSocket producer immediately, BEFORE the kernel's own delayed
        # disconnect. For dYdX this closes the WS so the Rust handler stops flooding
        # `loop.call_soon_threadsafe` during the multi-second teardown window — the
        # mechanism behind the Ctrl+C RAM escalation. None until injected.
        self._shutdown_hook = None
        # Reference to the kernel asyncio event loop, injected at build time by
        # `set_loop` (recorder.py, parallel to set_data_client). REQUIRED for the
        # MEM-03 pending-task probe: the heartbeat fires on a Nautilus LiveTimer
        # tokio worker thread (NOT the asyncio loop thread), so the probe cannot
        # discover the loop via asyncio.get_running_loop() — it must be handed an
        # explicit loop reference to call asyncio.all_tasks(loop). None until
        # injected (the probe then degrades to "n/a").
        self._event_loop = None

    def set_data_client(self, client) -> None:
        """
        Inject a reference to the live venue data client (HOT-01 / Pattern 3).

        The ADDITION branch (`_load_and_subscribe_addition`) reaches the client's
        `instrument_provider` to load a brand-new instrument at runtime, because
        some adapters do not implement `request_instrument` (RESEARCH Pitfall 1).
        Called once from `recorder.py` after `node.build()`.

        Parameters
        ----------
        client : object
            The live venue data client instance from the built node.

        """
        self._data_client = client

    def set_config_loader(self, loader) -> None:
        """
        Inject the venue-specific hot-reload config loader (DYDX-01).

        Parallel to ``set_data_client``: each venue's ``recorder.py`` injects its
        ``load_recorder_config`` callable after ``node.build()`` so this common
        strategy never imports a venue config module. ``_on_config_reload`` calls
        the injected loader to re-read ``recorder.toml`` each poll. When no loader
        is injected (and no module-level default is registered), the reload
        callback is a logged no-op.

        Parameters
        ----------
        loader : callable
            A callable ``(path) -> tuple[RecorderConfig, list[InstrumentId]]``.

        """
        self._config_loader = loader

    def set_shutdown_hook(self, hook) -> None:
        """
        Inject a venue-specific shutdown hook run first in ``on_stop`` (MEM-02).

        Parallel to ``set_data_client``/``set_config_loader``: each venue's
        ``recorder.py`` injects a zero-arg callable after ``node.build()`` so this
        common strategy never imports a venue module. ``on_stop`` invokes the hook
        BEFORE flushing/converting, so the venue can halt its WebSocket producer
        immediately — ahead of the kernel's own delayed client disconnect.

        For dYdX the hook schedules the WS client's ``disconnect()`` on the loop.
        The dYdX Rust WS handler runs on a separate runtime and schedules every
        parsed datum onto the asyncio loop via ``call_soon_threadsafe`` with no
        subscription gate and no backpressure; it only stops once the WS closes.
        The kernel does not disconnect the data client until AFTER ``on_stop`` and a
        post-stop residual sleep (and the dYdX adapter then waits another second
        before closing), so without this hook the producer floods the loop's
        unbounded ready-queue for several seconds on Ctrl+C, ballooning RAM
        (MEM-02). Closing the WS here collapses that window.

        Parameters
        ----------
        hook : callable
            A zero-arg callable invoked from ``on_stop`` on the event-loop thread.

        """
        self._shutdown_hook = hook

    def set_loop(self, loop) -> None:
        """
        Inject the kernel asyncio event loop for the MEM-03 task-count probe.

        Parallel to ``set_data_client``/``set_shutdown_hook``: each venue's
        ``recorder.py`` injects ``node.kernel.loop`` after ``node.build()`` so the
        ``_mem_watch_message`` heartbeat probe can count pending asyncio tasks.

        WHY this is required: the heartbeat timer is fired by a Nautilus
        ``LiveTimer`` whose Python callback runs on the GLOBAL TOKIO RUNTIME worker
        thread (``crates/common/src/live/timer.rs`` ``rt.spawn`` -> ``callback.call``),
        not on the asyncio event-loop thread. ``asyncio.get_running_loop()`` raises
        ``RuntimeError`` on that worker thread, so the probe must be handed an
        explicit loop reference. ``asyncio.all_tasks(loop)`` only reads the loop's
        task registry, so it works from the timer worker thread.

        Parameters
        ----------
        loop : asyncio.AbstractEventLoop
            The kernel event loop (``node.kernel.loop``).

        """
        self._event_loop = loop

    def _resolve_loop(self):
        """
        Resolve the asyncio event loop for the MEM-03 pending-task probe.

        Returns the loop injected via ``set_loop`` (wired by ``recorder.py`` from
        ``node.kernel.loop`` after build), or ``None`` when it was never injected
        (the probe then degrades to ``n/a`` rather than raising).

        NOTE: the registered executor (``register_executor``) also holds the kernel
        loop as ``ActorExecutor._loop``, but the strategy's ``_executor`` is a
        Cython ``cdef object`` attribute NOT exposed across the Python boundary
        (``getattr`` returns ``None``), so it cannot serve as a fallback. The
        explicit ``set_loop`` injection is therefore the single source of truth.
        """
        return self._event_loop

    def _resolve_config_loader(self):
        """
        Resolve the active hot-reload config loader (DYDX-01).

        Prefers the per-instance loader injected via ``set_config_loader``; falls
        back to the module-level default a venue package may have registered (so a
        strategy built without an explicit loader still works via the venue
        re-export shim). Returns ``None`` when neither is wired.
        """
        return self._config_loader or _default_config_loader

    def _subscribe_instrument(
        self,
        instrument_id: InstrumentId,
        depth: int,
        bar_intervals: list[str],
        is_linear: bool,
    ) -> None:
        """
        Subscribe ALL recorded feeds for a single instrument (shared by `on_start`
        and the HOT-01 ADDITION branch).

        Issues exactly the same per-instrument subscribe calls, in the same order,
        that `on_start` historically issued inline: trade ticks, quote ticks,
        order-book deltas (`BookType.L2_MBP` at `depth`), one `subscribe_bars` per
        interval (venue-native `LAST-EXTERNAL` kline, D-02), and — only when
        `is_linear` — mark/index prices and funding rates (D-04 linear-only
        gating, Pitfall 5).

        Pure subscribe issuance: this helper deliberately does NOT touch
        `_subscribed_params`, `_product_types`, `_hot_added_count`, or `_last_seen`
        — `on_start` and the ADDITION branch own that bookkeeping around it.

        Parameters
        ----------
        instrument_id : InstrumentId
            The instrument to subscribe to.
        depth : int
            The order book depth for `subscribe_order_book_deltas`.
        bar_intervals : list[str]
            The bar interval strings (e.g. ``["1-MINUTE"]``) to subscribe.
        is_linear : bool
            Whether the instrument is a linear perpetual (gates mark/index/funding).

        """
        self.subscribe_trade_ticks(instrument_id)
        self.subscribe_quote_ticks(instrument_id)
        self.subscribe_order_book_deltas(
            instrument_id,
            book_type=BookType.L2_MBP,
            depth=depth,
            managed=False,
        )
        for interval in bar_intervals:
            # Venue-native EXTERNAL kline stream (D-02) — NOT derived from
            # trades. LAST price type, EXTERNAL aggregation source.
            self.subscribe_bars(
                BarType.from_str(f"{instrument_id}-{interval}-LAST-EXTERNAL"),
            )
        if is_linear:
            self.subscribe_mark_prices(instrument_id)
            self.subscribe_index_prices(instrument_id)
            self.subscribe_funding_rates(instrument_id)

    def on_start(self) -> None:
        """
        Actions to be performed on strategy start.

        Raises
        ------
        RuntimeError
            If any configured instrument is missing from the cache. The message
            lists ALL missing instrument ids (D-06). This intentionally does NOT
            call `self.stop()` (D-05) — the process must exit non-zero so systemd
            surfaces the failure (T-01-06).

        """
        missing: list[str] = []
        for instrument_id in self.config.instrument_ids:
            if self.cache.instrument(instrument_id) is None:
                missing.append(str(instrument_id))

        if missing:
            raise RuntimeError(f"Missing instruments: {', '.join(sorted(missing))}")

        # Surface restart-induced gaps in the logs before subscribing (D-06) — a
        # failure here is logged but must not prevent subscriptions.
        self._log_restart_gaps()

        # D-04 linear-only gating: mark/index/funding exist for derivatives only.
        # The adapter merely warns + early-returns for spot, so gate on the
        # configured linear set (Pitfall 4/5) — passed into the shared helper.
        linear_ids_set = set(self.config.linear_instrument_ids)
        for instrument_id in self.config.instrument_ids:
            self._subscribe_instrument(
                instrument_id,
                depth=self.config.instrument_depths[instrument_id],
                bar_intervals=self.config.instrument_bar_intervals[instrument_id],
                is_linear=instrument_id in linear_ids_set,
            )

        self.clock.set_timer(
            name="convert-stream",
            interval=pd.Timedelta(minutes=self.config.conversion_interval_minutes),
            callback=self._convert_stream,
        )

        self.clock.set_timer(
            name="heartbeat",
            interval=pd.Timedelta(seconds=self.config.heartbeat_interval_seconds),
            callback=self._heartbeat,
        )

        # HOT-01: seed the running-state bookkeeping from the startup config so
        # the FIRST config-reload diffs against the real subscribed set (Pattern 2).
        linear_ids = set(self.config.linear_instrument_ids)
        for instrument_id in self.config.instrument_ids:
            self._subscribed_params[instrument_id] = (
                self.config.instrument_depths[instrument_id],
                frozenset(self.config.instrument_bar_intervals[instrument_id]),
            )
            self._product_types[instrument_id] = "linear" if instrument_id in linear_ids else "spot"

        # HOT-01 reload trigger (D-01/D-02): a dedicated named timer reusing the
        # heartbeat cadence. The callback re-reads recorder.toml and applies the
        # diff (removals + param-swaps here; additions land in Plan 02).
        self.clock.set_timer(
            name="config-reload",
            interval=pd.Timedelta(seconds=self.config.heartbeat_interval_seconds),
            callback=self._on_config_reload,
        )

    def _log_restart_gaps(self) -> None:
        """
        Log a WARNING per instrument when the gap since the last recorded
        `ts_init` exceeds `restart_gap_threshold_seconds` (REL-02 gap visibility,
        D-06).

        A restart with the fixed `instance_id` resumes recording, but any wall-clock
        time the process was down is an unrecoverable gap in the data. Logging it at
        startup makes restart-induced gaps visible/alertable via journald.

        Reads the most-recent `TradeTick` `ts_init` per instrument from the catalog.
        The per-instrument body is wrapped in try/except so a catalog-read error for
        one instrument cannot suppress warnings for others or block startup
        (consistent with the per-type swallow in `_run_conversion`).
        """
        try:
            # WHY: opening the catalog touches the filesystem; a transient I/O
            # error here must not propagate out of _log_restart_gaps() and then
            # out of on_start(), which would abort the recorder before any
            # subscriptions are made (WR-02 / the on_start + docstring "logged
            # but must not prevent subscriptions" guarantee). With no catalog
            # there are no prior ts_init values to compare, so return early.
            catalog = ParquetDataCatalog(self.config.catalog_path)
        except Exception as exc:
            # MEM-05: route via self.log (pyo3) so it lands in the visible log file.
            self.log.exception("Failed to open catalog for restart-gap check", exc)
            return

        now_ns = self.clock.timestamp_ns()

        for instrument_id in self.config.instrument_ids:
            try:
                trades = catalog.trade_ticks(instrument_ids=[str(instrument_id)])
                if not trades:
                    continue

                last_ts_init = max(tick.ts_init for tick in trades)
                gap_s = (now_ns - last_ts_init) / 1e9
                if gap_s > self.config.restart_gap_threshold_seconds:
                    # MEM-05: route via self.log (pyo3) — the stdlib `logger` here
                    # was invisible in the use_pyo3 log file (the whole point of
                    # this gap WARNING). Message built by an extracted helper so the
                    # content is unit-testable without the immutable self.log.
                    self.log.warning(
                        self._restart_gap_message(instrument_id, gap_s, last_ts_init),
                    )
            except Exception as exc:
                self.log.exception(
                    f"Failed to check restart gap for {instrument_id}",
                    exc,
                )

    def _restart_gap_message(
        self,
        instrument_id: InstrumentId,
        gap_s: float,
        last_ts_init: int,
    ) -> str:
        """
        Build the REL-02 restart-gap WARNING line (MEM-05).

        Factored out of ``_log_restart_gaps`` so the WARNING content is
        unit-testable without touching the immutable Cython ``self.log`` object
        (it cannot be patched/spied — cycle-3b). ``self.log`` takes a pre-formatted
        string, not printf-style args, so the formatting is done here.
        """
        last_data = pd.Timestamp(last_ts_init, unit="ns")
        return f"Resuming after gap of {gap_s:.1f}s for {instrument_id} (last data: {last_data})"

    def _stale_threshold_s(self, stream: str) -> float:
        """
        Return the stale threshold (in seconds) for `stream`.

        Looks up `stream` in `self.config.stale_threshold_seconds`; falls back to
        `self.config.stale_threshold_default_seconds` for any stream label not
        present in that dict (REL-03 / Pitfall 3 — per-data-type thresholds, no
        blanket short threshold).

        Parameters
        ----------
        stream : str
            The stream label, e.g. ``"trade"``, ``"quote"``, ``"deltas"``,
            ``"bar"``, ``"mark"``, ``"index"``, ``"funding"``.

        Returns
        -------
        float

        """
        return float(
            self.config.stale_threshold_seconds.get(
                stream,
                self.config.stale_threshold_default_seconds,
            ),
        )

    def _heartbeat(self, event: TimeEvent) -> None:
        """
        Periodic heartbeat/stale-check timer callback (REL-03).

        Logs an INFO heartbeat summarizing the number of active streams, and a
        WARNING for any stream whose idle time exceeds its per-data-type stale
        threshold (`_stale_threshold_s`) -- giving the operator early visibility
        into a dead stream even while the WebSocket connection stays up.

        """
        now_ns = self.clock.timestamp_ns()

        for (stream, instrument_id), last_ns in self._last_seen.items():
            idle_s = (now_ns - last_ns) / 1e9
            threshold_s = self._stale_threshold_s(stream)
            if idle_s > threshold_s:
                # MEM-05: route via self.log (pyo3) so the gap WARNING actually
                # lands in logs/<recorder>.log. The stdlib `logger` was NOT captured
                # by the node's use_pyo3 backend, so this stale-stream signal — the
                # user's stated TOP PRIORITY ("visible where there are holes in the
                # data") — was effectively invisible. Message built by an extracted
                # helper so the content is unit-testable (self.log is immutable).
                self.log.warning(
                    self._stale_stream_message(stream, instrument_id, idle_s, threshold_s),
                )

        # MEM-05: heartbeat INFO via self.log (pyo3). Below stderr's WARNING
        # threshold, the stdlib `logger.info` line was lost ENTIRELY before this.
        self.log.info(f"Heartbeat: {len(self._last_seen)} active streams")

        # MEM-03: emit RSS + asyncio task-count through `self.log` (the Nautilus
        # pyo3-routed logger) — NOT the stdlib `logger` above, which earlier log
        # audits proved is NOT captured by the node's use_pyo3 log file. This is
        # the only heartbeat line that actually lands in logs/<recorder>.log, so
        # it is the runtime-evidence probe for the memory-growth investigation:
        # a normal (no-Ctrl+C) run yields an rss_mb + pending_tasks time-series
        # that confirms (or refutes) steady-state ThrottledEnqueuer overflow.
        self.log.info(self._mem_watch_message())

    def _stale_stream_message(
        self,
        stream: str,
        instrument_id: InstrumentId,
        idle_s: float,
        threshold_s: float,
    ) -> str:
        """
        Build the REL-03 stale-stream WARNING line (MEM-05).

        Factored out of ``_heartbeat`` so the WARNING content is unit-testable
        without touching the immutable Cython ``self.log`` object (it cannot be
        patched/spied — cycle-3b). ``self.log`` takes a pre-formatted string, not
        printf-style args, so the formatting is done here.
        """
        return (
            f"Stale stream: {stream} {instrument_id} idle {idle_s:.1f}s "
            f"(> {threshold_s:.0f}s threshold)"
        )

    def _mem_watch_message(self) -> str:
        """
        Build the MEM-03 memory-watch heartbeat line.

        Samples this process's RSS and the asyncio pending-task count and formats
        them into a single ``mem-watch:`` line. Factored out of ``_heartbeat`` so
        the probe content is unit-testable without touching the immutable Cython
        ``self.log`` object. ``n/a`` is substituted for any sample that is
        unavailable (non-Linux RSS, or no running loop) so the line is always
        well-formed.
        """
        rss_mb = _read_self_rss_mb()
        # Pass the EXPLICIT kernel loop (cycle-3b root cause): the heartbeat runs on
        # a tokio worker thread where asyncio.get_running_loop() raises, so without
        # this the count is always "n/a".
        pending_tasks = _pending_task_count(self._resolve_loop())
        rss_str = f"{rss_mb:.1f}" if rss_mb is not None else "n/a"
        tasks_str = str(pending_tasks) if pending_tasks is not None else "n/a"
        return (
            f"mem-watch: rss_mb={rss_str} pending_tasks={tasks_str} "
            f"active_streams={len(self._last_seen)}"
        )

    def _config_signature(self, parsed_cfg) -> int:
        """
        Return a stable signature of the parsed instrument entries (HOT-01 / D-07).

        Used to detect when ``recorder.toml`` has actually changed so
        `_failed_instrument_ids` is reset only on a real edit (D-07: "only
        re-attempt if recorder.toml changes again"), not on every poll. The
        signature folds in each instrument's id, depth, bar intervals, and product
        type — i.e. exactly the fields the diff branches act on.

        Parameters
        ----------
        parsed_cfg : RecorderConfig
            The freshly parsed recorder configuration.

        Returns
        -------
        int

        """
        return hash(
            tuple(
                sorted(
                    (
                        str(e.id),
                        getattr(e, "depth", None),
                        tuple(e.bar_intervals),
                        getattr(e, "product_type", None),
                    )
                    for e in parsed_cfg.instruments
                ),
            ),
        )

    def _diff_config(self, parsed_cfg) -> tuple[list, list, list]:
        """
        Diff a freshly-parsed config against the running subscription set (HOT-01).

        Compares the parsed instrument entries against `self._subscribed_params`
        (seeded in `on_start`, mutated by every applied diff) and returns three
        lists of parsed `InstrumentEntry` items:

        - additions: ids present in `parsed_cfg` but NOT currently subscribed.
        - removals: ids currently subscribed but NOT in `parsed_cfg` (carries the
          PARSED entry where available; for an id absent from the new config a
          synthetic entry is not needed — removal only needs the running params +
          product type, tracked separately).
        - param_changes: ids in BOTH whose ``(depth, frozenset(bar_intervals))``
          differs from the running params.

        Parameters
        ----------
        parsed_cfg : RecorderConfig
            The freshly parsed recorder configuration.

        Returns
        -------
        tuple[list, list, list]
            ``(additions, removals, param_changes)`` of `InstrumentEntry` items.
            Removals carry the REMOVED instrument's id (not present in the parsed
            config) so callers resolve params/product-type from the running
            bookkeeping.

        """
        parsed_by_id = {entry.id: entry for entry in parsed_cfg.instruments}
        running_ids = set(self._subscribed_params)

        additions = [entry for entry in parsed_cfg.instruments if entry.id not in running_ids]
        removals = [
            instrument_id for instrument_id in running_ids if instrument_id not in parsed_by_id
        ]

        param_changes = []
        for instrument_id, entry in parsed_by_id.items():
            if instrument_id not in running_ids:
                continue
            if self._subscribed_params[instrument_id] != (
                entry.depth,
                frozenset(entry.bar_intervals),
            ):
                param_changes.append(entry)

        return additions, removals, param_changes

    def _unsubscribe_instrument(
        self,
        instrument_id: InstrumentId,
        bar_intervals,
        is_linear: bool,
    ) -> None:
        """
        Unsubscribe ALL feeds for a removed instrument and prune its state (D-06).

        Mirrors `on_start`'s subscribe set in reverse: trade, quote, order-book
        deltas, and one `unsubscribe_bars` per current interval. Mark/index/funding
        are linear-only (Pitfall 5), so they are gated on ``is_linear``. After
        unsubscribing, every ``(stream, instrument_id)`` entry is pruned from
        `_last_seen` (Pitfall 4 — otherwise `_heartbeat` perpetually WARNs "stale"
        for a deliberately-removed instrument), and the running bookkeeping
        (`_subscribed_params`, `_product_types`) is cleared.

        Parameters
        ----------
        instrument_id : InstrumentId
            The instrument being removed from `recorder.toml`.
        bar_intervals : Iterable[str]
            The bar intervals the instrument is currently subscribed at.
        is_linear : bool
            Whether the instrument is a linear perpetual (gates mark/index/funding).

        """
        self.unsubscribe_trade_ticks(instrument_id)
        self.unsubscribe_quote_ticks(instrument_id)
        self.unsubscribe_order_book_deltas(instrument_id)
        for interval in bar_intervals:
            self.unsubscribe_bars(BarType.from_str(f"{instrument_id}-{interval}-LAST-EXTERNAL"))
        if is_linear:
            self.unsubscribe_mark_prices(instrument_id)
            self.unsubscribe_index_prices(instrument_id)
            self.unsubscribe_funding_rates(instrument_id)

        # D-06 / Pitfall 4: drop every _last_seen entry for this instrument so it
        # no longer appears in heartbeat/stale-stream checks.
        for stream in ("trade", "quote", "deltas", "bar", "mark", "index", "funding"):
            self._last_seen.pop((stream, instrument_id), None)

        self._subscribed_params.pop(instrument_id, None)
        self._product_types.pop(instrument_id, None)

    def _apply_param_change(
        self,
        instrument_id: InstrumentId,
        old_depth: int,
        old_intervals,
        new_depth: int,
        new_intervals,
    ) -> None:
        """
        Apply a depth and/or bar-interval change as a clean swap (D-05).

        Order-book DEPTH change: ``unsubscribe_order_book_deltas`` FIRST, then
        ``subscribe_order_book_deltas`` at the new depth. The order is load-bearing
        (RESEARCH Pitfall 2): the DataEngine dedups order-book subscriptions by
        ``instrument_id`` only, so subscribing before unsubscribing silently drops
        the new depth and the feed stays at the old granularity.

        BAR-INTERVAL change: subscribe ONLY the added intervals and unsubscribe
        ONLY the removed ones (the set delta) — trade/quote/mark/index/funding are
        untouched (D-05).

        Parameters
        ----------
        instrument_id : InstrumentId
            The instrument whose params changed.
        old_depth : int
            The currently-subscribed order book depth.
        old_intervals : Iterable[str]
            The currently-subscribed bar intervals.
        new_depth : int
            The new order book depth from the parsed config.
        new_intervals : Iterable[str]
            The new bar intervals from the parsed config.

        """
        if new_depth != old_depth:
            # Pitfall 2: unsubscribe-then-subscribe; order matters.
            self.unsubscribe_order_book_deltas(instrument_id)
            self.subscribe_order_book_deltas(
                instrument_id,
                book_type=BookType.L2_MBP,
                depth=new_depth,
                managed=False,
            )

        old_set = set(old_intervals)
        new_set = set(new_intervals)
        for interval in old_set - new_set:
            self.unsubscribe_bars(BarType.from_str(f"{instrument_id}-{interval}-LAST-EXTERNAL"))
        for interval in new_set - old_set:
            self.subscribe_bars(BarType.from_str(f"{instrument_id}-{interval}-LAST-EXTERNAL"))

        self._subscribed_params[instrument_id] = (new_depth, frozenset(new_intervals))

    def _check_hot_added_threshold(self) -> None:
        """
        Log a WARNING when the lifetime hot-add count exceeds the D-08 knob.

        Informational only — `max_hot_added_instruments` NEVER blocks further
        hot-adds (D-08); it surfaces runaway config thrash. Called by the ADDITION
        branch after each successful hot-add; isolated here so the threshold
        behavior is unit-testable independently of the load.

        """
        if self._hot_added_count > self.config.max_hot_added_instruments:
            # MEM-05: route via self.log (pyo3) so this lands in the visible log.
            # Message built by an extracted helper so the content is unit-testable
            # without the immutable self.log.
            self.log.warning(self._hot_added_threshold_message())

    def _hot_added_threshold_message(self) -> str:
        """
        Build the D-08 hot-add-threshold WARNING line (MEM-05).

        Factored out of ``_check_hot_added_threshold`` so the WARNING content is
        unit-testable without touching the immutable Cython ``self.log`` object.
        """
        return (
            f"Hot-added instrument count {self._hot_added_count} exceeds "
            f"max_hot_added_instruments {self.config.max_hot_added_instruments} "
            f"(informational; not blocked)"
        )

    def _load_and_subscribe_addition(self, entry) -> None:
        """
        Load a brand-new instrument at runtime and subscribe it (HOT-01 / D-09).

        Two-phase, driven across successive `_on_config_reload` polls because the
        reload timer callback is synchronous while the provider's `load_async` is a
        coroutine on the live event loop (RESEARCH Open Question 2 (RESOLVED) / A3):

        - **Poll N (schedule):** the id is unknown to both the provider and the
          cache, so `provider.load(id)` is fired (the sync wrapper schedules
          `load_async` on the running loop, fire-and-forget) and the id is added
          to `_pending_loads`. Nothing is subscribed this cycle — confirmation is
          deferred (D-10 "wait until confirmed").
        - **Poll N+1 (resolve):** if `provider.find(id)` is non-None the load
          completed → add the instrument to the cache (the on_start-style
          presence guard, D-10), repopulate the client's WS/HTTP precision caches
          additively (`_cache_instruments`, A1 / Pitfall 3), increment the
          lifetime hot-add count (with the D-08 threshold WARNING), then issue the
          full subscribe set via the shared `_subscribe_instrument` helper. If the
          id is in `_pending_loads` but `find(id)` is STILL None, treat it as
          venue-unknown (D-11): log a single ERROR and mark it failed so it is not
          re-attempted until the config signature changes (D-07).

        The whole per-id body is wrapped in try/except so one bad id cannot abort
        the other additions or fault the component (mirrors the per-step swallow
        in `_run_conversion`); on exception the id is marked failed and dropped
        from `_pending_loads`.

        Parameters
        ----------
        entry : InstrumentEntry
            The parsed config entry for the newly-added instrument.

        """
        instrument_id = entry.id

        # D-07: a failed id is not re-attempted until the toml signature changes.
        if instrument_id in self._failed_instrument_ids:
            return

        if self._data_client is None:
            # No live client wired (e.g. mis-wired build) — cannot load. Warn once
            # per poll and skip; never fault the component.
            # MEM-05: route via self.log (pyo3) so it lands in the visible log.
            self.log.warning(
                f"No data client injected; cannot hot-load {instrument_id}",
            )
            return

        try:
            provider = self._data_client.instrument_provider
            instrument = provider.find(instrument_id)

            if instrument is None and self.cache.instrument(instrument_id) is None:
                # Phase 1: schedule the runtime load exactly once per snapshot.
                if instrument_id not in self._pending_loads:
                    provider.load(instrument_id)
                    self._pending_loads.add(instrument_id)
                    # MEM-05: route via self.log (pyo3) for visible-log capture.
                    self.log.info(f"Hot-loading new instrument {instrument_id} (scheduled)")
                    return

                # Phase 2 (still unresolved): the load came back empty — the venue
                # did not recognize the id (D-11). Fail it for this snapshot.
                self.log.error(
                    f"Failed to load new instrument {instrument_id} "
                    f"(venue did not recognize it); skipping",
                )
                self._failed_instrument_ids.add(instrument_id)
                self._pending_loads.discard(instrument_id)
                return

            # Phase 2 (resolved): confirm in cache + repopulate precision caches.
            if instrument is not None:
                self.cache.add_instrument(instrument)
            self._data_client._cache_instruments()
            self._pending_loads.discard(instrument_id)

            # D-08: count the hot-add and surface a WARNING when over threshold
            # (never blocks the subscribe).
            self._hot_added_count += 1
            self._check_hot_added_threshold()

            # Subscribe the full feed set via the shared helper (Pitfall 5 linear
            # gating from the parsed product_type) and record running bookkeeping.
            is_linear = entry.product_type == "linear"
            self._subscribe_instrument(
                instrument_id,
                depth=entry.depth,
                bar_intervals=entry.bar_intervals,
                is_linear=is_linear,
            )
            self._subscribed_params[instrument_id] = (
                entry.depth,
                frozenset(entry.bar_intervals),
            )
            self._product_types[instrument_id] = entry.product_type
            self.log.info(f"Hot-added instrument {instrument_id}")
        except Exception as exc:
            self.log.exception(f"Failed to hot-add instrument {instrument_id}", exc)
            self._failed_instrument_ids.add(instrument_id)
            self._pending_loads.discard(instrument_id)

    def _on_config_reload(self, event: TimeEvent) -> None:
        """
        Periodic config-reload timer callback (HOT-01 / D-01).

        Re-reads `recorder.toml` (via the INJECTED venue config loader, DYDX-01),
        diffs it against the running subscription set, and applies REMOVALS (D-06),
        PARAM-CHANGES (D-05), and ADDITIONS via the two-phase runtime instrument
        load (`_load_and_subscribe_addition`, Pattern 3 / D-09).

        The WHOLE body is wrapped in `try/except Exception` (mirrors the
        `_run_conversion` swallow): a malformed/half-written toml read mid-edit
        (TOCTOU, Security V7 / T-06-01) must be logged once and skipped so the
        recorder keeps running on its last-good config — a bad reload must never
        fault the strategy component.

        """
        try:
            if self.config.reload_config_path is None:
                self.log.debug("No reload_config_path configured; skipping config reload")
                return

            loader = self._resolve_config_loader()
            if loader is None:
                # No venue config loader wired (DYDX-01). The timer still fires;
                # without a loader there is nothing to diff against, so skip.
                self.log.debug("No config loader injected; skipping config reload")
                return

            parsed_cfg, _ = loader(self.config.reload_config_path)

            # D-07: reset the per-snapshot failed set only when the toml actually
            # changes, so a failed load is re-attempted on edit but not every poll.
            signature = self._config_signature(parsed_cfg)
            if signature != self._last_config_signature:
                self._failed_instrument_ids.clear()
                self._last_config_signature = signature

            additions, removals, param_changes = self._diff_config(parsed_cfg)

            for instrument_id in removals:
                is_linear = self._product_types.get(instrument_id) == "linear"
                _, bar_intervals = self._subscribed_params.get(
                    instrument_id,
                    (0, frozenset()),
                )
                self._unsubscribe_instrument(instrument_id, bar_intervals, is_linear)

            for entry in param_changes:
                old_depth, old_intervals = self._subscribed_params[entry.id]
                self._apply_param_change(
                    entry.id,
                    old_depth,
                    old_intervals,
                    entry.depth,
                    entry.bar_intervals,
                )

            # ADDITIONS: two-phase runtime instrument load + subscribe (D-09/D-10).
            # Each call is self-contained (try/except inside) so one bad id never
            # aborts the others.
            for entry in additions:
                self._load_and_subscribe_addition(entry)

            # MEM-05: route via self.log (pyo3) so reload activity is visible.
            self.log.info(
                f"Config reload: {len(additions)} added, {len(removals)} removed, "
                f"{len(param_changes)} changed",
            )
        except Exception as exc:
            # T-06-01 / Security V7: never let a bad reload fault the component.
            self.log.exception("Config reload failed", exc)
            return

    def on_trade_tick(self, tick: TradeTick) -> None:
        """
        Actions to be performed when a trade tick is received.

        No manual persistence is performed here — trades auto-flow to the
        `StreamingFeatherWriter` via the kernel's "*" msgbus subscription (REC-07).

        """
        self._last_seen[("trade", tick.instrument_id)] = self.clock.timestamp_ns()
        logger.debug("Received %s", tick)

    def on_quote_tick(self, tick: QuoteTick) -> None:
        """
        Actions to be performed when a quote tick is received.

        No manual persistence — quotes auto-flow to the `StreamingFeatherWriter`
        via the kernel's "*" msgbus subscription (REC-02 / REC-07).

        """
        self._last_seen[("quote", tick.instrument_id)] = self.clock.timestamp_ns()
        logger.debug("Received %s", tick)

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        """
        Actions to be performed when order book deltas are received.

        No manual persistence — deltas auto-flow to the `StreamingFeatherWriter`
        via the kernel's "*" msgbus subscription (REC-03 / REC-07).

        """
        self._last_seen[("deltas", deltas.instrument_id)] = self.clock.timestamp_ns()
        logger.debug("Received %s", deltas)

    def on_bar(self, bar: Bar) -> None:
        """
        Actions to be performed when a bar is received.

        No manual persistence — bars auto-flow to the `StreamingFeatherWriter`
        via the kernel's "*" msgbus subscription (REC-04 / REC-07).

        """
        self._last_seen[("bar", bar.bar_type.instrument_id)] = self.clock.timestamp_ns()
        logger.debug("Received %s", bar)

    def on_mark_price(self, mark_price: MarkPriceUpdate) -> None:
        """
        Actions to be performed when a mark price update is received.

        No manual persistence — mark prices auto-flow to the
        `StreamingFeatherWriter` via the kernel's "*" msgbus subscription
        (REC-06 / REC-07).

        """
        self._last_seen[("mark", mark_price.instrument_id)] = self.clock.timestamp_ns()
        logger.debug("Received %s", mark_price)

    def on_index_price(self, index_price: IndexPriceUpdate) -> None:
        """
        Actions to be performed when an index price update is received.

        No manual persistence — index prices auto-flow to the
        `StreamingFeatherWriter` via the kernel's "*" msgbus subscription
        (REC-06 / REC-07).

        """
        self._last_seen[("index", index_price.instrument_id)] = self.clock.timestamp_ns()
        logger.debug("Received %s", index_price)

    def on_funding_rate(self, funding_rate: FundingRateUpdate) -> None:
        """
        Actions to be performed when a funding rate update is received.

        Drops the update if the rate is unchanged from the last persisted value
        for this instrument (D-01 dedup gate, T-2-02) -- the live ~100ms ticker
        pushes the same rate repeatedly between funding intervals. Only a
        value-change is persisted via the strategy-owned funding writer
        (Task 1 resolved path).

        Last-seen is recorded BEFORE the dedup early-return (REL-03 / Pitfall 4)
        so a rarely-changing funding stream is not falsely flagged stale by
        `_heartbeat` just because most updates are deduped rather than persisted.

        """
        self._last_seen[("funding", funding_rate.instrument_id)] = self.clock.timestamp_ns()

        last_rate = self._last_funding_rate.get(funding_rate.instrument_id)
        if last_rate is not None and last_rate == funding_rate.rate:
            logger.debug("Dropping unchanged funding rate for %s", funding_rate.instrument_id)
            return

        self._last_funding_rate[funding_rate.instrument_id] = funding_rate.rate
        self._persist_funding_rate(funding_rate)

    def _persist_funding_rate(self, funding_rate: FundingRateUpdate) -> None:
        """
        Persist a deduped `FundingRateUpdate` via the strategy-owned
        `StreamingFeatherWriter` (Task 1 resolved path).

        This writer is SEPARATE from the kernel's "*" writer, which deliberately
        EXCLUDES `FundingRateUpdate` from `include_types` (D-01 / Pitfall 1) to
        avoid flooding the catalog with the ~100ms funding ticker. Because
        `class_to_filename(FundingRateUpdate)` resolves to the native
        `funding_rate_update` table, the rows written here convert and read back
        natively via `catalog.funding_rates(...)` exactly like the kernel-written
        types.

        """
        if self._funding_writer is None:
            self._funding_writer = StreamingFeatherWriter(
                path=f"{self.config.catalog_path}/live/{self.config.instance_id_str}",
                cache=self.cache,
                clock=self.clock,
                fs_protocol="file",
                include_types=[FundingRateUpdate],
                # WHY: must rotate like the kernel "*" writer (Pitfall 1/REL-02) so
                # `_convert_stream` finds rotated-out, finalized funding feather
                # files to convert (Pitfall 2).
                rotation_mode=RotationMode.SCHEDULED_DATES,
                rotation_interval=pd.Timedelta(minutes=self.config.rotation_interval_minutes),
                rotation_time=time(0, 0, 0),
                rotation_timezone="UTC",
            )

        self._funding_writer.write(funding_rate)
        self._funding_writer.flush()

    def _flush_funding_writer(self) -> None:
        """
        Flush the strategy-owned funding writer ON THE EVENT-LOOP THREAD.

        Must be called on the loop thread (never from the conversion worker) so it
        never races `on_funding_rate` -> `_persist_funding_rate.write/flush`, which
        also run on the loop thread (MEM-01). Flushing here makes any deduped
        funding rows persisted since the last tick visible in the feather file
        before the off-thread conversion reads finalized files.
        """
        if self._funding_writer is not None:
            try:
                # WHY: a flush error must not block conversion of already
                # finalized files, nor propagate out of on_stop() (CR-01 /
                # T-3-01).
                self._funding_writer.flush()
            except Exception as exc:
                # MEM-05: route via self.log (pyo3) so the failure is visible.
                self.log.exception("Failed to flush funding writer", exc)

    def _run_conversion(self) -> None:
        """
        Convert streamed feather data for this instance into the `ParquetDataCatalog`.

        MEM-01: this body runs on a WORKER THREAD (via `self.run_in_executor`), NOT
        on the asyncio event loop. The live dYdX WebSocket runtime schedules every
        parsed delta onto the loop via `call_soon_threadsafe` into an unbounded
        ready-queue with no backpressure, so any loop-blocking work here (opening the
        catalog, reading/converting feather files) would let that queue flood and
        balloon RAM until OOM. Offloading keeps the loop free to drain WS messages.
        It reads only ROTATED-OUT (finalized, immutable) feather files and writes
        parquet -- it never touches loop-thread mutable state (the funding writer is
        flushed separately on the loop thread BEFORE offload via
        `_flush_funding_writer`), so it is safe to run off-thread.

        Only feather files that have already been ROTATED OUT (i.e. a newer file
        for the same instrument/bar-type already exists, so the file will never
        receive new rows) are converted. `convert_stream_to_data` re-reads a
        feather file in full and recomputes its `(start, end)` interval, so
        converting the still-open (most-recently-created) file on a later cycle
        -- once it has grown -- would overlap the interval already written for it
        and raise "non-disjoint intervals" (Pitfall 2). Restricting conversion to
        finalized files makes each file convert exactly once and keeps repeat
        conversions idempotent (the file's content, and therefore its interval,
        never changes again).

        A transient conversion error is logged and swallowed PER TYPE so it does not
        crash the recorder nor block the remaining types.
        """
        try:
            # WHY: opening the catalog touches the filesystem; a transient I/O
            # error here (e.g. during SIGTERM shutdown) must not propagate out
            # of the worker and fault the component (CR-01 / T-3-01). Without a
            # catalog there is nothing to convert into, so return early.
            catalog = ParquetDataCatalog(self.config.catalog_path)
        except Exception as exc:
            # MEM-05: route via self.log (pyo3). NOTE this runs on the conversion
            # WORKER thread; the pyo3 logger is thread-safe (the mem-watch probe
            # already logs via self.log from a tokio worker thread).
            self.log.exception("Failed to open catalog for conversion", exc)
            return

        for data_cls in [*_RECORDED_TYPES, FundingRateUpdate]:
            try:
                # WHY: subdirectory default is "backtest"; live runs write under
                # live/ (Pitfall 3). Per-type try/except so one type's transient
                # error does not block the others (A2 / Pitfall 2).
                self._convert_finalized_feather_files(catalog, data_cls)
            except Exception as exc:
                self.log.exception(
                    f"Failed to convert {data_cls.__name__} stream to catalog",
                    exc,
                )

    def _convert_stream(self, event: TimeEvent) -> None:
        """
        Periodic conversion timer callback (REL-01).

        MEM-01: flush the funding writer on the loop thread, then OFFLOAD the
        catalog conversion to the registered worker executor via
        `self.run_in_executor` so it never blocks the event loop. When no executor
        is registered, `run_in_executor` runs `_run_conversion` inline (back-compat
        for tests); production wiring in `recorder.py` registers a single-thread
        executor so the offload is real.
        """
        self._flush_funding_writer()
        self.run_in_executor(self._run_conversion)

    def on_stop(self) -> None:
        """
        Actions to be performed on strategy stop (REL-02, Pattern 1).

        On SIGTERM the live runner calls `kernel.stop_async()`, which fires this
        hook (via `_trader.stop()`) BEFORE the kernel closes its `"*"`
        `StreamingFeatherWriter` (`_close_writer()`), and `Strategy._stop()` runs
        `on_stop()` BEFORE cancelling this strategy's timers. So the kernel
        writer's feather files are still on disk and the strategy-owned funding
        writer is still open here — a final flush+convert via `_run_conversion()`
        catches any feather files already finalized mid-session by a
        `SCHEDULED_DATES` rotation that the periodic timer had not yet converted.

        `_convert_finalized_feather_files` deliberately SKIPS each identifier's
        still-active file, so THIS session's active-file tail is not converted
        here; it becomes convertible on the NEXT restart's first conversion cycle
        when a new process creates a new file that finalizes this one (D-02/D-03 —
        no data loss, accepted one-cycle parquet-visibility delay).

        This hook never references, flushes, or closes the kernel `"*"`
        streaming writer: the strategy cannot reach it and the kernel closes it
        itself afterwards (Pitfall 1 / Anti-Pattern).

        MEM-01: `on_stop` MUST NOT block the event loop. `kernel.stop_async()`
        fires this hook (via `_trader.stop()`) BEFORE it disconnects the live data
        client, so the dYdX WebSocket is still producing here. The old code ran the
        full synchronous catalog conversion inline on the loop; while it blocked,
        the WS runtime kept scheduling deltas onto the loop's unbounded ready-queue
        via `call_soon_threadsafe`, ballooning RAM until OOM and making Ctrl+C
        appear to hang. Instead we flush the funding writer on the loop thread and
        OFFLOAD the conversion to the worker executor (non-blocking). If the
        executor is torn down before the conversion runs, the only consequence is
        that already-finalized feather files are converted on the NEXT restart's
        first conversion cycle (the still-active file was never convertible here
        anyway) -- no data loss (D-02/D-03).

        MEM-02: BEFORE any flush/convert, invoke the injected venue shutdown hook
        (if wired) to halt the venue WebSocket producer immediately. `on_stop` is
        the FIRST step of `kernel.stop_async()`; the kernel does not disconnect the
        data client until after this hook AND a post-stop residual sleep, and the
        dYdX adapter then waits a further second before closing its WS. Throughout
        that multi-second window the dYdX Rust WS handler keeps scheduling every
        full-depth L2 delta onto the loop via `call_soon_threadsafe` (no
        backpressure), saturating the bounded data queue and spawning unbounded
        `create_task(put)` coroutines -> the Ctrl+C RAM escalation. Closing the WS
        here stops the producer at the source. The hook is wrapped so a teardown
        error never prevents the funding flush / conversion offload below.
        """
        if self._shutdown_hook is not None:
            try:
                self._shutdown_hook()
            except Exception as exc:
                # MEM-05: route via self.log (pyo3) so a teardown error is visible.
                self.log.exception("Shutdown hook failed", exc)

        self._flush_funding_writer()
        self.run_in_executor(self._run_conversion)

    def _convert_finalized_feather_files(
        self,
        catalog: ParquetDataCatalog,
        data_cls: type,
    ) -> None:
        """
        Convert every ROTATED-OUT feather file for `data_cls` into the catalog.

        Feather files are written per-instrument/bar-type under
        `{table_name}/{identifier}/{identifier}_{timestamp}.feather`, with
        filenames sorted chronologically by `_list_feather_data_files`. Within
        each identifier's directory, the most-recently-created file is still open
        for writes -- it is skipped (`files[:-1]`) -- and all earlier files are
        finalized (rotation already moved on to a newer file, so they will never
        be appended to again).

        MEM-06: once a finalized feather file has been read AND converted, its
        source is DELETED so it is never listed or re-read again on a future
        pass. Without this, `_list_feather_data_files` returns the entire growing
        backlog of already-converted feather files on EVERY conversion pass
        (periodic every `conversion_interval_minutes` AND on every Ctrl+C/on_stop),
        and `_read_feather_file` unconditionally materializes each one into an
        in-memory pyarrow Table -- BEFORE the cheap "already exists, skipping
        write" parquet-level skip can fire (that skip lives inside
        `_convert_feather_table_to_parquet`, AFTER the read). The redundant read
        cost therefore scaled with total recorder uptime, re-reading hours of
        already-durable history into RAM at every shutdown -- the surviving memory
        growth behind cycles 1-4. Deletion is safe: nautilus's interval tracking
        (`_get_directory_intervals`) reads the PARQUET output directory only and
        never depends on feather sources surviving, and `files[:-1]` guarantees
        the still-active file is never a deletion candidate.

        A file is deleted only after a SUCCESSFUL read+convert: a `None` read
        (missing/corrupt) and a raising convert are both skipped (we do not know
        the data reached parquet, so we keep the source). The already-exists case
        IS a success -- `_convert_feather_table_to_parquet` returns without
        raising once the parquet destination is confirmed present, so the data is
        durably captured either way. A deletion failure (e.g. permissions) is
        logged via `self.log` (pyo3, per MEM-05) but never crashes the conversion
        pass nor loses track of the data already being durably converted.
        """
        files_by_directory: dict[str, list] = {}
        for feather_file in catalog._list_feather_data_files(
            kind="live",
            instance_id=self.config.instance_id_str,
            data_cls=data_cls,
        ):
            directory = feather_file.path.rsplit("/", 1)[0]
            files_by_directory.setdefault(directory, []).append(feather_file)

        for files in files_by_directory.values():
            for feather_file in files[:-1]:
                table = catalog._read_feather_file(feather_file.path)
                if table is None:
                    # Missing/corrupt read: data is not confirmed durable in
                    # parquet, so do NOT delete the source -- retry next pass.
                    continue

                # If this raises, the per-type try/except in `_run_conversion`
                # swallows it BEFORE the rm below runs, so a failed convert never
                # deletes its source. The already-exists case returns normally
                # (data confirmed in parquet) and DOES fall through to deletion.
                catalog._convert_feather_table_to_parquet(
                    feather_table=table,
                    feather_path=feather_file.path,
                    data_cls=data_cls,
                    used_catalog=catalog,
                )

                # MEM-06: source is now durably in parquet -- delete it so it is
                # never re-listed/re-read on a future pass. Guard the rm so a
                # deletion failure cannot crash the pass (the data is already
                # safely converted regardless).
                try:
                    catalog.fs.rm(feather_file.path)
                except Exception as exc:
                    self.log.exception(
                        f"Failed to delete converted feather file {feather_file.path}",
                        exc,
                    )
