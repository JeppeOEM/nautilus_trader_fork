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
r"""
Backfill historical venue klines (`Bar`) for Bybit and Hyperliquid into the Parquet catalog.

Usage:
    python -m collector_core.backfill_bars --catalog /app/catalog \\
        --instrument BTCUSDT-LINEAR.BYBIT [--instrument ETHUSDT-LINEAR.BYBIT ...] \\
        --start 2026-09-01 --end 2026-09-18 \\
        [--bar-spec 1-MINUTE-LAST] [--environment mainnet] [--apply]

**Report-only unless `--apply`**: without it the tool plans and logs the windows it would fetch
and the ranges already covered, builds no venue client, issues no REST call and writes nothing.

The bars written are `EXTERNAL` — the *venue's own* aggregation, not our 1 s fold. They live only
in the Parquet catalog: they never enter `candles_<venue>.db` and never reach the chart, both of
which are built from `DydxSecondSnapshot`. `--start`/`--end` are inclusive **days of bar
coverage**, and a close-stamped bar belongs to the day it *opened* in.

Both venues are normalized to one timestamp convention: `ts_event == ts_init ==` the bar's **close**
time. Bybit is asked for that directly (`timestamp_on_close=True`); Hyperliquid's adapter stamps the
candle's open time (`crates/adapters/hyperliquid/src/data.rs:1267`) so `_close_stamp` shifts it by
one interval, carrying the decoded `Price`/`Quantity` values across unchanged.

Coverage: Bybit serves multi-year kline history; Hyperliquid's `candleSnapshot` returns roughly the
last 5000 candles (~3.5 days at 1 minute), so anything older is permanently unavailable.

Re-runs are idempotent at the *request* level: the windows come from the catalog's own
`get_missing_intervals_for_request`, so a fully archived range plans zero windows and issues zero
REST calls.

Known limit: both venues' kline OHLCV passes through an `f64` round-trip inside the pinned adapters
before it becomes a `Price`/`Quantity` -- Bybit `value.parse::<f64>()` + `Price::new_checked`
(`crates/adapters/bybit/src/common/parse.rs:1187-1198`), Hyperliquid `Price::new(f64, precision)`
(`crates/adapters/hyperliquid/src/data.rs:1278`). They differ only in range validation, **not** in
precision: neither venue's backfilled bars carry a string-exact guarantee, which matters for D-51's
kline reconciliation. Not worked around here -- re-deriving prices in Python would be exactly the
precision laundering NAUT-01 forbids, and `crates/` is off limits (FORK-01). Upgrade path: fix the
two parsers upstream to build `Price` from the decimal string (`Decimal.scaleb()` + `from_raw()`
semantics), then re-run this tool over the affected range in a fresh catalog. Audit row D-52.

Known limit: Hyperliquid history below the `candleSnapshot` retention floor cannot be fetched at
all, by anyone -- there is no repair, only detection (the short-coverage warning below). Upgrade
path: a paid third-party historical feed, loaded through this same `write_data()` path. Audit D-53.

Known limit: `--environment` selects which venue endpoint is queried. It does **not** and cannot
verify what the target catalog already holds -- nothing in the catalog records the environment a
row came from -- so pointing a testnet run at a mainnet catalog (or the reverse) silently mixes
them under one instrument id. Upgrade path: an environment marker written alongside the instrument
definitions, checked here before the first fetch.

Known limit: the accepted `--bar-spec` set is deliberately narrower than either adapter's own
table. Weekly, monthly and 3-day bars are excluded because every bound in this tool is an absolute
epoch multiple and the epoch is a **Thursday**, while both venues open weekly klines on Monday --
every fetched bar would be discarded as off-grid. `6-HOUR` (Bybit-only) and `8-HOUR`
(Hyperliquid-only) are excluded so that one `--bar-spec` behaves identically across a mixed-venue
run rather than succeeding on one venue and aborting on the next. Upgrade path: wire-verify each
venue's grid anchor for those intervals, then widen the per-venue table below.
"""

import argparse
import asyncio
import logging
import time
from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from typing import Any

import pyarrow.parquet as pq

from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.core.datetime import unix_nanos_to_iso8601
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarSpecification
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import PriceType
from nautilus_trader.model.enums import bar_aggregation_to_str
from nautilus_trader.model.enums import price_type_to_str
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.persistence.catalog import ParquetDataCatalog


logger = logging.getLogger(__name__)

_DAY_NS = 86_400 * 1_000_000_000

# Bars per fetch window. Under Bybit's 1000-per-page pagination (the Rust client pages backwards
# internally, `crates/adapters/bybit/src/http/client.rs:3749`) and well under Hyperliquid's
# ~5000-candle `candleSnapshot` response cap, so one window is always one venue round trip.
_WINDOW_BARS = 1000

# Pacing between requests, per venue. Both numbers come from the venues' **published documentation**
# -- Bybit's per-IP request budget and Hyperliquid's aggregate request-weight-per-minute budget for
# `/info`, of which `candleSnapshot` is one of the expensive calls. Neither is wire-verified in this
# environment (DATA-02 would want that), so treat them as conservative defaults, not measurements.
_BYBIT_SLEEP_S = 0.2
_HYPERLIQUID_SLEEP_S = 1.0

# `HyperliquidEnvironment.from_str` is absent from the pinned `nautilus_pyo3.pyi` and the stub
# cannot be edited (FORK-01), so map the CLI's own spelling here.
_HYPERLIQUID_ENVIRONMENTS: dict[str, Any] = {
    "mainnet": nautilus_pyo3.HyperliquidEnvironment.MAINNET,
    "testnet": nautilus_pyo3.HyperliquidEnvironment.TESTNET,
}

# Accepted `(aggregation, step)` per venue. Derived from the adapters' own interval tables --
# `bar_spec_to_bybit_interval` (`crates/adapters/bybit/src/common/parse.rs:225-261`) and
# `bar_type_to_interval` (`crates/adapters/hyperliquid/src/common/parse.rs:437-474`) -- then
# narrowed to intervals that divide a UTC day exactly, so an absolute epoch multiple is always a
# real venue bar boundary, and to the set both venues share. See the `Known limit:` above.
_SHARED_STEPS: dict[str, frozenset[int]] = {
    "MINUTE": frozenset({1, 3, 5, 15, 30}),
    "HOUR": frozenset({1, 2, 4, 12}),
    "DAY": frozenset({1}),
}
_SUPPORTED_SPECS: dict[str, dict[str, frozenset[int]]] = {
    "BYBIT": _SHARED_STEPS,
    "HYPERLIQUID": _SHARED_STEPS,
}

# Same workaround as `collector.py`: `ParquetDataCatalog.write_data()` (pinned nautilus_trader
# 1.229.0) has no compression passthrough and `parquet.py` cannot be modified (FORK-01), so patch
# pyarrow's default. The name check makes this a no-op when `collector.py` was imported **first**;
# `collector.py` has no guard of its own, so importing it *after* this module still double-wraps
# (harmless -- `setdefault` is idempotent -- but do not read the guard as covering both orders).
_orig_write_table = pq.write_table

if _orig_write_table.__name__ != "_write_table_zstd":

    def _write_table_zstd(*args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("compression", "zstd")
        _orig_write_table(*args, **kwargs)

    pq.write_table = _write_table_zstd


_Fetcher = Callable[[BarType, int, int], Awaitable[list[Bar]]]


@dataclass(frozen=True)
class _Job:
    """One validated instrument: its bar type and the catalog's own instrument definition."""

    bar_type: BarType
    instrument: Instrument


@dataclass(frozen=True)
class _Session:
    """
    A venue's fetch callable plus its pacing and retention behaviour.

    `fetch` is a plain callable precisely so the fetch-and-write path is testable offline.
    """

    fetch: _Fetcher
    sleep_s: float
    stop_on_empty: bool
    missing: frozenset[str] = frozenset()


@dataclass
class _Summary:
    """Per-instrument outcome, emitted from a `finally` so an aborted run still reports it."""

    label: str
    planned: int = 0
    written_bars: int = 0
    written_files: int = 0
    discarded: int = 0
    missing_bars: int = 0
    skipped_windows: int = 0
    error: str | None = None


# -- TIME / WINDOW ARITHMETIC ---------------------------------------------------------------------


def _iso(ts_ns: int) -> str:
    return unix_nanos_to_iso8601(ts_ns)


def _dt(ts_ns: int) -> datetime:
    """Nanoseconds -> aware datetime without any `/1e9` float round-trip."""
    secs, nanos = divmod(ts_ns, 1_000_000_000)
    return datetime.fromtimestamp(secs, tz=UTC) + timedelta(microseconds=nanos // 1000)


def _parse_date_ns(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC).timestamp()) * 1_000_000_000


def _bar_floor(ts_ns: int, bar_ns: int) -> int:
    """Largest bar close time <= `ts_ns` on the absolute epoch grid."""
    return ts_ns - ts_ns % bar_ns


def _bar_ceil(ts_ns: int, bar_ns: int) -> int:
    """Smallest bar close time >= `ts_ns` on the absolute epoch grid."""
    return -((-ts_ns) // bar_ns) * bar_ns


def _bar_count(w_start: int, w_end: int, bar_ns: int) -> int:
    """Exact number of bar close times in the inclusive, bar-aligned window `[w_start, w_end]`."""
    return (w_end - w_start) // bar_ns + 1


def _check_range(start_ns: int, end_ns: int, bar_ns: int) -> None:
    if bar_ns <= 0:
        raise ValueError(f"bar_ns must be positive, got {bar_ns}")
    if end_ns < start_ns:
        raise ValueError(f"end {_iso(end_ns)} is before start {_iso(start_ns)}")
    if start_ns % bar_ns or end_ns % bar_ns:
        raise ValueError(
            f"bounds must be multiples of bar_ns={bar_ns}: got {start_ns}, {end_ns}",
        )


def _windows(start_ns: int, end_ns: int, bar_ns: int, limit: int) -> list[tuple[int, int]]:
    """
    Split the inclusive close-time range into contiguous windows of at most `limit` bars.

    Both bounds, and every bound returned, are bar close times on the absolute epoch grid. That is
    the invariant the whole tool rests on: a Parquet file is named for its *data* bounds, so only a
    bar-aligned window can ever be reported as fully covered on the next run.
    """
    if limit <= 0:
        raise ValueError(f"limit must be positive, got {limit}")  # cursor would never advance
    _check_range(start_ns, end_ns, bar_ns)
    span = bar_ns * limit
    out: list[tuple[int, int]] = []
    cursor = start_ns
    while cursor <= end_ns:
        out.append((cursor, min(cursor + span - bar_ns, end_ns)))
        cursor += span
    return out


def _day_range_ns(start_day: str, end_day: str, bar_ns: int, now_ns: int) -> tuple[int, int]:
    """
    `--start D1 --end D2` (inclusive days of coverage) -> inclusive bar **close**-time bounds.

    A close-stamped bar belongs to the day it *opened* in, so D1's first bar closes at
    `midnight(D1) + bar_ns` and D2's last closes at `midnight(D2) + _DAY_NS`. The obvious
    `midnight(D1) .. midnight(D2) + _DAY_NS - 1` silently drops D2's last bar and pulls in D1-1's.

    The end is then clamped to the last closed bar: a window past it can never be filled and would
    be re-planned on every future run. The clamp may empty the range; the caller reports that.
    """
    day1, day2 = _parse_date_ns(start_day), _parse_date_ns(end_day)
    if day1 < 0 or day2 < 0:
        raise ValueError("--start/--end must be on or after 1970-01-01")
    start_ns, end_ns = day1 + bar_ns, day2 + _DAY_NS
    _check_range(start_ns, end_ns, bar_ns)
    return start_ns, min(end_ns, _bar_floor(now_ns, bar_ns))


def _real_gaps(gaps: list[tuple[int, int]], bar_ns: int) -> list[tuple[int, int]]:
    """
    Snap each catalog gap inward to bar close times, dropping gaps that hold no bar close.

    A completed window's file ends `bar_ns - 1` ns before the window does, so the catalog always
    reports a sub-bar sliver afterwards. Without this snap every completed window is re-planned and
    re-fetched forever, and each empty response is then mis-reported as missing venue history.
    """
    out: list[tuple[int, int]] = []
    for low, high in gaps:
        low_snapped, high_snapped = _bar_ceil(low, bar_ns), _bar_floor(high, bar_ns)
        if low_snapped <= high_snapped:
            out.append((low_snapped, high_snapped))
    return out


def _covered_spans(
    start_ns: int,
    end_ns: int,
    gaps: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Complement of the (sorted, disjoint) gaps inside the request -- one span per covered run."""
    spans: list[tuple[int, int]] = []
    cursor = start_ns
    for low, high in gaps:
        if low > cursor:
            spans.append((cursor, low - 1))
        cursor = max(cursor, high + 1)
    if cursor <= end_ns:
        spans.append((cursor, end_ns))
    return spans


# -- PLANNING -------------------------------------------------------------------------------------


def _gaps(
    catalog: ParquetDataCatalog,
    bar_type: BarType,
    start_ns: int,
    end_ns: int,
    bar_ns: int,
) -> list[tuple[int, int]]:
    """Return the catalog's missing intervals for this bar type, snapped to bar close times."""
    _check_range(start_ns, end_ns, bar_ns)
    raw = catalog.get_missing_intervals_for_request(start_ns, end_ns, Bar, str(bar_type))
    return _real_gaps(raw, bar_ns)


def _plan_gaps(
    gaps: list[tuple[int, int]],
    bar_ns: int,
    window_bars: int = _WINDOW_BARS,
) -> list[tuple[int, int]]:
    plan: list[tuple[int, int]] = []
    for low, high in gaps:
        plan.extend(_windows(low, high, bar_ns, window_bars))
    return plan


def _plan(
    catalog: ParquetDataCatalog,
    bar_type: BarType,
    start_ns: int,
    end_ns: int,
    bar_ns: int,
    window_bars: int = _WINDOW_BARS,
) -> list[tuple[int, int]]:
    """Windows still to fetch. Empty when the catalog already covers every bar close in range."""
    return _plan_gaps(_gaps(catalog, bar_type, start_ns, end_ns, bar_ns), bar_ns, window_bars)


def _log_plan(
    label: str,
    plan: list[tuple[int, int]],
    start_ns: int,
    end_ns: int,
    bar_ns: int,
    gaps: list[tuple[int, int]],
    apply: bool,
) -> None:
    for low, high in _covered_spans(start_ns, end_ns, gaps):
        logger.info("%s: already covered %s..%s", label, _iso(low), _iso(high))
    if not plan:
        logger.info(
            "%s: nothing to fetch -- %s..%s fully covered", label, _iso(start_ns), _iso(end_ns)
        )
        return
    verb = "fetching" if apply else "would fetch"
    for w_start, w_end in plan:
        logger.info(
            "%s: %s %s..%s (%d bars)",
            label,
            verb,
            _iso(w_start),
            _iso(w_end),
            _bar_count(w_start, w_end, bar_ns),
        )


# -- BAR HANDLING ---------------------------------------------------------------------------------


def _close_stamp(bars: list[Bar], bar_ns: int) -> list[Bar]:
    """
    Re-stamp Hyperliquid's open-time bars onto close time.

    Bybit klines are close-stamped and Hyperliquid's are open-stamped
    (`crates/adapters/hyperliquid/src/data.rs:1267`), and one bar-type string cannot carry two
    conventions. The rebuild passes the decoded `Price`/`Quantity` straight through -- Cython
    hands back a fresh wrapper per attribute access, but around the *same* raw integer and the same
    precision label, so there is no `Decimal`, no `float` and no re-stamping: NAUT-01's
    `Price(decimal, precision)` trap is never touched.
    """
    return [
        Bar(
            bar.bar_type,
            bar.open,
            bar.high,
            bar.low,
            bar.close,
            bar.volume,
            bar.ts_init + bar_ns,
            bar.ts_init + bar_ns,
        )
        for bar in bars
    ]


def _clean(
    bars: list[Bar],
    w_start: int,
    w_end: int,
    bar_ns: int,
    label: str,
) -> tuple[list[Bar], int]:
    """
    Bound to the window, drop off-grid stamps, dedupe by `ts_init` (first wins), sort ascending.

    Every discard is counted and logged: DATA-07 forbids an unannounced filter.
    """
    out_of_window = off_grid = duplicate = 0
    kept: dict[int, Bar] = {}
    for bar in bars:
        ts_init = bar.ts_init
        if ts_init < w_start or ts_init > w_end:
            out_of_window += 1
        elif ts_init % bar_ns:
            off_grid += 1
        elif ts_init in kept:
            duplicate += 1
        else:
            kept[ts_init] = bar
    discarded = out_of_window + off_grid + duplicate
    if discarded:
        logger.warning(
            "%s: discarded %d of %d served bars (%d out of window, %d off the %d ns grid, %d duplicate)",
            label,
            discarded,
            len(bars),
            out_of_window,
            off_grid,
            bar_ns,
            duplicate,
        )
    return [kept[ts] for ts in sorted(kept)], discarded


def _report_coverage(label: str, bars: list[Bar], w_start: int, w_end: int, bar_ns: int) -> int:
    """
    Whole-window coverage canary. Returns the number of missing bars.

    A missing front, a missing tail and interior holes are reported **separately**, and an entirely
    empty window is its own case -- calling that one "missing at the front" is what previously fed a
    confident, wrong retention diagnosis.
    """
    expected = _bar_count(w_start, w_end, bar_ns)
    span = f"{_iso(w_start)}..{_iso(w_end)}"
    if not bars:
        logger.warning("%s: window %s returned no bars at all (%d expected)", label, span, expected)
        return expected
    front = (bars[0].ts_init - w_start) // bar_ns
    tail = (w_end - bars[-1].ts_init) // bar_ns
    interior = expected - len(bars) - front - tail
    if front or tail or interior:
        logger.warning(
            "%s: window %s served %d of %d bars (earliest %s, latest %s; missing %d front, %d tail,"
            " %d interior)",
            label,
            span,
            len(bars),
            expected,
            _iso(bars[0].ts_init),
            _iso(bars[-1].ts_init),
            front,
            tail,
            interior,
        )
    return expected - len(bars)


def _contiguous_runs(bars: list[Bar], bar_ns: int) -> list[list[Bar]]:
    """Split sorted bars at every interior hole, so each run can be written as its own file."""
    runs: list[list[Bar]] = []
    for bar in bars:
        if runs and bar.ts_init == runs[-1][-1].ts_init + bar_ns:
            runs[-1].append(bar)
        else:
            runs.append([bar])
    return runs


def _precision_mismatch(bars: list[Bar], instrument: Instrument) -> str | None:
    """
    Refuse bars whose precision disagrees with the catalog's own instrument definition.

    A venue tick/lot change between runs would otherwise leave two files under one bar type with
    conflicting precision labels, which `ParquetDataCatalog` then refuses to read or merge -- the
    documented dYdX mark-price incident class (NAUT-01).
    """
    prices = {bar.open.precision for bar in bars}
    sizes = {bar.volume.precision for bar in bars}
    if prices != {instrument.price_precision}:
        return (
            f"venue price precision {sorted(prices)} != {instrument.price_precision} in the "
            f"catalog's instrument definition for {instrument.id}; refusing to write"
        )
    if sizes != {instrument.size_precision}:
        return (
            f"venue size precision {sorted(sizes)} != {instrument.size_precision} in the "
            f"catalog's instrument definition for {instrument.id}; refusing to write"
        )
    return None


def _write_runs(catalog: ParquetDataCatalog, bars: list[Bar], bar_ns: int) -> int:
    """
    Write each contiguous run of bar closes as its own `write_data()` call. Returns files written.

    Never the whole window as one batch: `_write_chunk` names a file `[first.ts_init,
    last.ts_init]`, so a single write spanning an interior hole seals that hole as *covered*
    forever -- the exact DATA-05 loss the coverage canary exists to report.
    """
    runs = _contiguous_runs(bars, bar_ns)
    for run in runs:
        catalog.write_data(run)
    return len(runs)


# -- VALIDATION -----------------------------------------------------------------------------------


def _spec_help(steps: dict[str, frozenset[int]] = _SHARED_STEPS) -> str:
    return ", ".join(
        f"{'/'.join(str(step) for step in sorted(values))}-{aggregation}"
        for aggregation, values in steps.items()
    )


def _check_spec(venue: str, spec: BarSpecification) -> str | None:
    if spec.price_type is not PriceType.LAST:
        return f"price type {price_type_to_str(spec.price_type)} is not supported; use LAST"
    aggregation = bar_aggregation_to_str(spec.aggregation)
    supported = _SUPPORTED_SPECS[venue]
    if spec.step not in supported.get(aggregation, frozenset()):
        return (
            f"--bar-spec {spec.step}-{aggregation}-LAST is not supported on {venue}; "
            f"supported: {_spec_help(supported)} (all -LAST)"
        )
    return None


def _resolve(
    catalog: ParquetDataCatalog,
    bar_type: BarType,
    environment: str,
) -> tuple[Instrument | None, str | None]:
    venue = bar_type.instrument_id.venue.value
    if venue not in _SUPPORTED_SPECS:
        return None, (
            f"venue {venue} is not backfillable by this tool; dYdX bars are derived from its own "
            f"1 s archive (python -m collector_core.build_candles), not from a kline REST backfill"
        )
    if venue == "HYPERLIQUID" and environment not in _HYPERLIQUID_ENVIRONMENTS:
        return None, f"Hyperliquid has no '{environment}' environment (mainnet or testnet only)"
    spec_problem = _check_spec(venue, bar_type.spec)
    if spec_problem:
        return None, spec_problem
    found = catalog.instruments(instrument_ids=[bar_type.instrument_id.value])
    if not found:
        return None, (
            "no instrument definition in the catalog -- run that venue's collector once so it "
            "writes the definition, then re-run this tool"
        )
    return found[0], None


def _validate(
    catalog: ParquetDataCatalog,
    instrument_ids: list[str],
    spec: str,
    environment: str,
) -> list[_Job]:
    """Validate **every** instrument before any client is built, any fetch runs or anything is written."""
    jobs: list[_Job] = []
    problems: list[str] = []
    for iid in instrument_ids:
        try:
            bar_type = BarType.from_str(f"{iid}-{spec}-EXTERNAL")
        except Exception as exc:
            # Nautilus itself rejects some steps (e.g. 7-MINUTE) before this tool's own table
            # ever sees them, so name the supported set here too rather than emit a bare parse error.
            problems.append(
                f"{iid}: cannot parse bar type '{iid}-{spec}-EXTERNAL' ({exc}); "
                f"supported --bar-spec values: {_spec_help()} (all -LAST)"
            )
            continue
        instrument, problem = _resolve(catalog, bar_type, environment)
        if instrument is None:
            problems.append(f"{iid}: {problem}")
        else:
            jobs.append(_Job(bar_type, instrument))
    if problems:
        raise SystemExit("\n".join(["refusing to start -- fix these first:", *problems]))
    return jobs


# -- VENUE SESSIONS -------------------------------------------------------------------------------


def _bybit_product_type(instrument_id: str) -> Any:
    return nautilus_pyo3.bybit_product_type_from_symbol(instrument_id.split(".")[0])


def _cache_listed(client: Any, jobs: list[_Job], listed: dict[str, Any]) -> frozenset[str]:
    """
    Cache each listed instrument on the client; return the ids the venue no longer lists.

    Both `request_bars` paths resolve the instrument from the **client's** own cache, and neither
    `request_instruments` nor `load_instrument_definitions` populates it.
    """
    missing: set[str] = set()
    for job in jobs:
        iid = job.bar_type.instrument_id.value
        found = listed.get(iid)
        if found is None:
            missing.add(iid)
        else:
            client.cache_instrument(found)
    return frozenset(missing)


async def _bybit_session(jobs: list[_Job], environment: str, bar_ns: int) -> _Session:
    client = nautilus_pyo3.BybitHttpClient(
        demo=environment == "demo",
        testnet=environment == "testnet",
    )
    listed: dict[str, Any] = {}
    for product_type in {_bybit_product_type(j.bar_type.instrument_id.value) for j in jobs}:
        for instrument in await client.request_instruments(product_type):
            listed[instrument.id.value] = instrument
    missing = _cache_listed(client, jobs, listed)

    async def fetch(bar_type: BarType, w_start: int, w_end: int) -> list[Bar]:
        # Bybit's REST range params match the kline's OPEN time; our windows are close times.
        pyo3_bars = await client.request_bars(
            product_type=_bybit_product_type(bar_type.instrument_id.value),
            bar_type=nautilus_pyo3.BarType.from_str(str(bar_type)),
            start=_dt(w_start - bar_ns),
            end=_dt(w_end - bar_ns),
            limit=_bar_count(w_start, w_end, bar_ns),
            timestamp_on_close=True,
        )
        return Bar.from_pyo3_list(pyo3_bars)

    return _Session(fetch, _BYBIT_SLEEP_S, stop_on_empty=False, missing=missing)


async def _hyperliquid_session(jobs: list[_Job], environment: str, bar_ns: int) -> _Session:
    client = nautilus_pyo3.HyperliquidHttpClient(
        environment=_HYPERLIQUID_ENVIRONMENTS[environment],
    )
    # Both, or a `.HYPERLIQUID` spot id passes the catalog guard and then dies mid-run on
    # "Instrument not found in cache".
    definitions = await client.load_instrument_definitions(include_spot=True, include_perps=True)
    missing = _cache_listed(client, jobs, {i.id.value: i for i in definitions})

    async def fetch(bar_type: BarType, w_start: int, w_end: int) -> list[Bar]:
        # Hyperliquid's REST range params match the kline's OPEN time; our windows are close times.
        # `limit` must stay None: that adapter truncates to the *oldest* N
        # (`crates/adapters/hyperliquid/src/http/client.rs:2751`), silently dropping the newest.
        pyo3_bars = await client.request_bars(
            bar_type=nautilus_pyo3.BarType.from_str(str(bar_type)),
            start=_dt(w_start - bar_ns),
            end=_dt(w_end - bar_ns),
            limit=None,
        )
        return _close_stamp(Bar.from_pyo3_list(pyo3_bars), bar_ns)

    return _Session(fetch, _HYPERLIQUID_SLEEP_S, stop_on_empty=True, missing=missing)


async def _build_sessions(jobs: list[_Job], environment: str, bar_ns: int) -> dict[str, _Session]:
    builders = {"BYBIT": _bybit_session, "HYPERLIQUID": _hyperliquid_session}
    sessions: dict[str, _Session] = {}
    for venue, builder in builders.items():
        venue_jobs = [j for j in jobs if j.bar_type.instrument_id.venue.value == venue]
        if venue_jobs:
            sessions[venue] = await builder(venue_jobs, environment, bar_ns)
    return sessions


# -- RUN ------------------------------------------------------------------------------------------


def _log_short_circuit(summary: _Summary, w_end: int, remaining: int) -> None:
    """Record that a fully empty window stopped the older ones -- an observation, not a proof."""
    summary.skipped_windows = remaining
    logger.warning(
        "%s: no data at or before %s -- skipping %d older window(s). Observed, not a proven "
        "retention floor.",
        summary.label,
        _iso(w_end),
        remaining,
    )


async def _fetch_windows(
    catalog: ParquetDataCatalog,
    job: _Job,
    plan: list[tuple[int, int]],
    bar_ns: int,
    session: _Session,
    summary: _Summary,
) -> None:
    """
    Fetch and write each planned window, newest first.

    Newest-first so Hyperliquid's retention floor is found on the first empty window rather than
    after every older one has been probed, and so an interrupted run leaves the most recent history
    archived. A merely short window never short-circuits: one no-trade minute at a window's front
    boundary is ordinary for an illiquid perp.
    """
    label = summary.label
    for index, (w_start, w_end) in enumerate(reversed(plan)):
        if index:
            await asyncio.sleep(session.sleep_s)  # paced between requests, never after the last
        served = await session.fetch(job.bar_type, w_start, w_end)
        bars, discarded = _clean(served, w_start, w_end, bar_ns, label)
        summary.discarded += discarded
        summary.missing_bars += _report_coverage(label, bars, w_start, w_end, bar_ns)
        if not bars:
            if session.stop_on_empty:
                _log_short_circuit(summary, w_end, len(plan) - index - 1)
                return
            continue
        mismatch = _precision_mismatch(bars, job.instrument)
        if mismatch:
            raise ValueError(mismatch)
        summary.written_files += _write_runs(catalog, bars, bar_ns)
        summary.written_bars += len(bars)


def _log_summary(summary: _Summary) -> None:
    logger.info(
        "%s: %s -- %d window(s) planned, %d bar(s) written in %d file(s), %d discarded, %d missing,"
        " %d window(s) skipped",
        summary.label,
        f"FAILED ({summary.error})" if summary.error else "ok",
        summary.planned,
        summary.written_bars,
        summary.written_files,
        summary.discarded,
        summary.missing_bars,
        summary.skipped_windows,
    )


async def _run_instrument(
    catalog: ParquetDataCatalog,
    job: _Job,
    start_ns: int,
    end_ns: int,
    bar_ns: int,
    session: _Session | None,
    apply: bool,
) -> _Summary:
    """One instrument, fully isolated: its failure is reported and the batch continues (AC #12)."""
    summary = _Summary(str(job.bar_type))
    try:
        gaps = _gaps(catalog, job.bar_type, start_ns, end_ns, bar_ns)
        plan = _plan_gaps(gaps, bar_ns)
        summary.planned = len(plan)
        _log_plan(summary.label, plan, start_ns, end_ns, bar_ns, gaps, apply)
        if not apply or session is None:
            return summary
        if job.bar_type.instrument_id.value in session.missing:
            raise LookupError("not listed by the venue (delisted?) -- nothing fetched")
        await _fetch_windows(catalog, job, plan, bar_ns, session, summary)
    except Exception as exc:
        summary.error = f"{type(exc).__name__}: {exc}"
        logger.error("%s: aborted -- %s", summary.label, summary.error, exc_info=True)
    finally:
        _log_summary(summary)
    return summary


async def _run(args: argparse.Namespace) -> int:
    catalog = ParquetDataCatalog(args.catalog)
    jobs = _validate(catalog, args.instrument, args.bar_spec, args.environment)
    bar_ns = int(jobs[0].bar_type.spec.timedelta.value)
    try:
        start_ns, end_ns = _day_range_ns(args.start, args.end, bar_ns, time.time_ns())
    except ValueError as exc:
        raise SystemExit(f"invalid --start/--end: {exc}") from exc
    if end_ns < start_ns:
        logger.warning(
            "nothing to do: %s..%s is entirely after the last closed bar", args.start, args.end
        )
        return 0
    if not args.apply:
        logger.info("report-only (pass --apply to fetch and write)")
    sessions = await _build_sessions(jobs, args.environment, bar_ns) if args.apply else {}
    failures = 0
    for job in jobs:
        session = sessions.get(job.bar_type.instrument_id.venue.value)
        summary = await _run_instrument(catalog, job, start_ns, end_ns, bar_ns, session, args.apply)
        failures += bool(summary.error)
    return 1 if failures else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--catalog", required=True, help="ParquetDataCatalog root")
    parser.add_argument(
        "--instrument", action="append", required=True, help="repeatable; full Nautilus id"
    )
    parser.add_argument("--start", required=True, help="YYYY-MM-DD (UTC, inclusive day)")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD (UTC, inclusive day)")
    parser.add_argument("--bar-spec", default="1-MINUTE-LAST", help="default: 1-MINUTE-LAST")
    parser.add_argument(
        "--environment",
        default="mainnet",
        choices=("mainnet", "testnet", "demo"),
        help="which venue endpoint to query; it cannot verify the catalog's own environment",
    )
    parser.add_argument("--apply", action="store_true", help="actually fetch and write")
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(asyncio.run(_run(_parser().parse_args())))


if __name__ == "__main__":
    main()
