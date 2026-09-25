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
Day-long data/error cross-check (story 23.3): read the durable per-service error ledgers
(`observability.error_ledger`) and the archived `custom_dydx_second_snapshot` rows over one UTC
window, and answer the question the Epic 22 operator actions all needed -- "did anything go
wrong, and does the archived data prove it" -- without trusting Docker's rotated json-file logs
or a per-process in-memory count that resets on every restart.

Usage:
    python3 -m collector_core.crosscheck_errors --catalog /app/catalog \
        --errors-dir /app/errors_dir \
        [--since 2026-09-21T00:00:00Z] [--until 2026-09-22T00:00:00Z] \
        [--venue dydx|bybit|hyperliquid] [--fail-on SITE ...]

Default window: the last 24 hours ending now (UTC). Default `--fail-on`:
`collector.book_crosscheck`, `collector.book_sequence`, `collector.pending_deltas`.

Per service (every venue's collector, `ranking_engine`, `data_api`, `live-paper`, `bot_tui`;
`--venue` never narrows this -- a `--fail-on` site belonging to a service other than the named
venue's own collector, e.g. `ranking_engine.volume24h` with `--venue bybit`, must still be
checked): restarts (`process_start` lines) and per-site counts in the window, suppressed carries
folded in (DATA-07).

Per collected instrument (narrowed to one venue's instruments with `--venue`): every second-
snapshot row in the window is read through `kernel.catalog_files.query_second_ohlc` -- never
a `ParquetDataCatalog` construction, one UTC day and one instrument at a time (MEM-01) -- and a
gap between two consecutive rows wider than `_GAP_THRESHOLD_NS` is reported, alongside an
"Instruments" section giving every selected instrument's row count and first/last row timestamp
(the dead-instrument check of `docs/DEPLOY_CHECKLIST.md` §6, which a gap list alone cannot
answer). Each gap is matched against the owning collector's ledger entries lying within
`_MAX_TS_INIT_SKEW_NS` of one of the gap's own *edges*: a `process_start` there explains it as a
restart, any other entry explains it by site, and no entry at all makes it `UNEXPLAINED` -- a
DATA-07 finding, never tolerated.

Known limit: the gap-to-ledger match is time proximity only, not causally verified -- the
ledger's frozen AC1 line schema carries no instrument id, so an unrelated site's entry (e.g. a
`notify` failure) falling within the skew bound of a real, different-instrument data gap's edge
will "explain" it, and two distinct gaps on one instrument inside that window can cross-explain
each other the same way. A site that fires *continuously* (e.g. a steadily nonzero
`collector.late_trade`) therefore explains every gap whose edge it brackets, so
`explained: <site>` is not automatically benign -- read the named sites, never the word
"explained". Only the gap's edges are matched, never its interior, so a long gap with nothing at
either edge stays `UNEXPLAINED` however chatty the service was mid-gap. Upgrade path: carry the
instrument id on every collector ledger site's `detail`, structured, and match on it as well as
on time.

Known limit: only gaps *between* two observed rows are detected; a dead instrument with no rows
at all in the window, or a gap touching the window's own `--since`/`--until` edge, is not (there
is no data on either side to diff against) -- the "Instruments" section's row counts are what
covers that case, and `docs/DEPLOY_CHECKLIST.md` §6 says to read both. An instrument *delisted*
mid-window is the mirror image: `_catalog_instruments()` lists every partition directory ever
written, so it shows rows before the delisting, none after, and its trailing absence can never
be explained by a ledger entry -- expect a permanent `UNEXPLAINED`-looking tail for a delisted
id. Upgrade path: persist each run's last-seen timestamp per instrument, compare the next run's
first row against it, and read the venue's own instrument list to tell "delisted" from "dead".

Known limit (expected on the first real run): three sampler skip paths in
`collector_core/collector.py` -- empty top-of-book (~:1208), stale book (~:1218) and no book at
all (~:1342) -- emit neither a snapshot row nor a ledger entry, so every such episode surfaces
here as an `UNEXPLAINED` gap. That is AC5 working as designed: an unledgered skip *is* the
DATA-07 finding. The resolution is to give those three paths their own ledger sites (the spine
assigns that to the `SecondSampler` story), never to relax this check or to widen the matcher.

Exit code: non-zero when any gap is `UNEXPLAINED`, or any `--fail-on` site has a non-zero count
summed across the services printed; zero otherwise. `process_start` is a usable `--fail-on`
token and then makes each service's restart count fail the run -- a crash-loop must not certify
a clean day just because every gap it caused prints `restart` (DATA-07: "a restart-tolerant
pipeline does not make a crash-looping one acceptable"). `main()` also exits 1 when the errors
directory holds no ledger file at all, or when the catalog yields no instrument: certifying a
day as clean having read nothing is the worst possible outcome for this tool.
"""

import argparse
import sys
from bisect import bisect_left
from bisect import bisect_right
from collections.abc import Iterator
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

from kernel.catalog_files import SNAPSHOT_DIRNAME
from kernel.catalog_files import query_second_ohlc
from kernel.clocks import MAX_TS_INIT_SKEW_NS
from kernel.clocks import NS_PER_DAY
from kernel.clocks import NS_PER_S
from kernel.venues import MalformedInstrumentId
from kernel.venues import venue_of
from observability import error_ledger


# Aliased so this module reads as the matcher's tolerance, not as a generic clock-skew bound.
_MAX_TS_INIT_SKEW_NS = MAX_TS_INIT_SKEW_NS

# A sampled second is 1 s apart; 1.5 s absorbs the sampler's own jitter without hiding a missed
# second (two consecutive rows 2 s apart means one second was never archived).
_GAP_THRESHOLD_NS = int(1.5 * NS_PER_S)

_DEFAULT_FAIL_ON = (
    "collector.book_crosscheck",
    "collector.book_sequence",
    "collector.pending_deltas",
)

# Venue token (the uppercase Nautilus id suffix) -> the compose service that owns its collector,
# used to pick which service's ledger explains a given instrument's gap. `ranking_engine`,
# `data_api`, `live-paper` and `bot_tui` have no owning venue and are never gap-matched through
# this map -- they are still always in the printed "Services" section (`build_report` never
# filters that by venue). Kept in parity with `common.venues.VENUE_KINDS` by
# `test_venue_service_map_covers_every_registered_venue`: a new venue there needs an entry here.
_VENUE_SERVICE = {
    "DYDX": "collector",
    "BYBIT": "bybit_collector",
    "HYPERLIQUID": "hyperliquid_collector",
}


@dataclass
class ServiceReport:
    service: str
    restarts: int
    site_counts: dict[str, int]


@dataclass
class Gap:
    instrument_id: str
    start_ns: int
    end_ns: int
    explanation: str  # "restart", "explained: <site>[, <site> ...]" or "UNEXPLAINED"

    @property
    def unexplained(self) -> bool:
        return self.explanation == "UNEXPLAINED"


@dataclass
class InstrumentReport:
    """One selected instrument's observed coverage: the dead-instrument check gaps cannot give."""

    instrument_id: str
    rows: int
    first_ns: int | None
    last_ns: int | None


@dataclass
class Report:
    services: list[ServiceReport] = field(default_factory=list)
    instruments: list[InstrumentReport] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)


@dataclass
class _ServiceLedger:
    """
    One service's window of ledger records plus their timestamps, read exactly once.

    Named invariant (DESIGN-01): `timestamps[i] == records[i]["ts_ns"]` and both are ascending,
    which is what lets `_explain_gap` bisect instead of re-reading the rotated file set per gap.
    """

    records: list[dict[str, Any]] = field(default_factory=list)
    timestamps: list[int] = field(default_factory=list)


def _to_ns(moment: datetime) -> int:
    """Convert an aware `datetime` to epoch ns exactly: no float seconds, no lost sub-second."""
    whole = moment.astimezone(UTC).replace(microsecond=0)
    return int(whole.timestamp()) * NS_PER_S + moment.microsecond * 1_000


def _parse_ts(text: str) -> int:
    """`--since`/`--until`: an ISO-8601 UTC instant (`Z` or `+00:00`) -> epoch ns."""
    moment = datetime.fromisoformat(text)  # Python 3.11+ parses the `Z` suffix itself
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return _to_ns(moment)


def _day_starts(since_ns: int, until_ns: int) -> Iterator[int]:
    """Every UTC day-start (ns) whose day overlaps `[since_ns, until_ns]`."""
    day = (since_ns // NS_PER_DAY) * NS_PER_DAY
    while day <= until_ns:
        yield day
        day += NS_PER_DAY


def _catalog_instruments(catalog_path: str) -> list[str]:
    """Every instrument id with a second-snapshot partition (a directory listing, no file I/O)."""
    root = Path(catalog_path) / "data" / SNAPSHOT_DIRNAME
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def _instruments_for_venue(catalog_path: str, venue: str | None) -> list[str]:
    ids = _catalog_instruments(catalog_path)
    if venue is None:
        return ids
    wanted = venue.upper()
    kept = []
    for iid in ids:
        try:
            if venue_of(iid) == wanted:
                kept.append(iid)
        except MalformedInstrumentId:
            continue
    return kept


def _snapshot_seconds(
    catalog_path: str, instrument_id: str, since_ns: int, until_ns: int
) -> list[int]:
    """`ts_event` of every row in the window, ascending, one UTC day at a time (MEM-01)."""
    seconds: list[int] = []
    for day_start in _day_starts(since_ns, until_ns):
        lo = max(day_start, since_ns)
        hi = min(day_start + NS_PER_DAY - 1, until_ns)
        rows = query_second_ohlc(catalog_path, instrument_id, lo, hi)
        seconds.extend(row.ts_event for row in rows if lo <= row.ts_event <= hi)
    seconds.sort()
    return seconds


def _find_gaps(seconds: list[int]) -> list[tuple[int, int]]:
    """Interior gaps only (Known limit in the module docstring): between two observed rows."""
    return [(prev, cur) for prev, cur in pairwise(seconds) if cur - prev > _GAP_THRESHOLD_NS]


def _owning_service(instrument_id: str) -> str | None:
    try:
        return _VENUE_SERVICE.get(venue_of(instrument_id))
    except MalformedInstrumentId:
        return None


def _records_near(ledger: _ServiceLedger, lo_ns: int, hi_ns: int) -> list[dict[str, Any]]:
    left = bisect_left(ledger.timestamps, lo_ns)
    right = bisect_right(ledger.timestamps, hi_ns)
    return ledger.records[left:right]


def _explain_gap(ledger: _ServiceLedger, start_ns: int, end_ns: int) -> str:
    """
    Match only within the skew bound of one of the gap's own *edges*, never across its interior.

    A service that logs steadily (`collector.late_trade` can be nonzero all day) would otherwise
    explain a multi-hour gap with a single unrelated entry that happens to fall inside it, and
    `UNEXPLAINED` would become unreachable in production. For a gap shorter than twice the skew
    bound the two edge windows overlap, so short-gap behaviour is exactly as before.
    """
    near = _records_near(ledger, start_ns - _MAX_TS_INIT_SKEW_NS, start_ns + _MAX_TS_INIT_SKEW_NS)
    near += _records_near(ledger, end_ns - _MAX_TS_INIT_SKEW_NS, end_ns + _MAX_TS_INIT_SKEW_NS)
    if any(rec["site"] == error_ledger.PROCESS_START_SITE for rec in near):
        return "restart"
    sites = sorted({rec["site"] for rec in near})
    if sites:
        return f"explained: {', '.join(sites)}"
    return "UNEXPLAINED"


def _read_ledger(errors_dir: str, service: str, since_ns: int, until_ns: int) -> _ServiceLedger:
    """
    Read the service's records once, ascending, over the window widened by the skew bound.

    Widened because `_explain_gap` matches a gap edge +/- the skew bound, and a gap edge can sit
    at the window's own boundary; the report itself still only counts records inside
    `[since_ns, until_ns]` (`_service_report`).
    """
    records = sorted(
        error_ledger.iter_records(
            errors_dir,
            service,
            since_ns=since_ns - _MAX_TS_INIT_SKEW_NS,
            until_ns=until_ns + _MAX_TS_INIT_SKEW_NS,
        ),
        key=lambda rec: rec["ts_ns"],
    )
    return _ServiceLedger(records=records, timestamps=[rec["ts_ns"] for rec in records])


def _service_report(
    service: str, ledger: _ServiceLedger, since_ns: int, until_ns: int
) -> ServiceReport:
    in_window = _records_near(ledger, since_ns, until_ns)
    restarts = sum(1 for rec in in_window if rec["site"] == error_ledger.PROCESS_START_SITE)
    return ServiceReport(
        service=service, restarts=restarts, site_counts=error_ledger.site_counts(in_window)
    )


def _instrument_report(instrument_id: str, seconds: list[int]) -> InstrumentReport:
    return InstrumentReport(
        instrument_id=instrument_id,
        rows=len(seconds),
        first_ns=seconds[0] if seconds else None,
        last_ns=seconds[-1] if seconds else None,
    )


def build_report(
    catalog_path: str, errors_dir: str, since_ns: int, until_ns: int, venue: str | None
) -> Report:
    """
    `venue` narrows which instruments' data gaps are checked (and so which collector's ledger
    explains them) -- it never narrows the printed "Services"/`--fail-on` scope, which always
    covers every service found under `errors_dir`. A `--fail-on` site belonging to a service
    other than the requested venue's own collector (e.g. `ranking_engine.volume24h` with
    `--venue bybit`) must still be checked, or `--venue` would silently exclude it from the
    exit code.

    Each service's ledger is read exactly once here and handed to every gap that service owns --
    a 24 h window over ~120 instruments would otherwise re-glob and re-parse the whole rotated
    file set once per gap.
    """
    report = Report()

    ledgers = {
        service: _read_ledger(errors_dir, service, since_ns, until_ns)
        for service in error_ledger.services(errors_dir)
    }
    for service, ledger in ledgers.items():
        report.services.append(_service_report(service, ledger, since_ns, until_ns))

    for instrument_id in _instruments_for_venue(catalog_path, venue):
        seconds = _snapshot_seconds(catalog_path, instrument_id, since_ns, until_ns)
        report.instruments.append(_instrument_report(instrument_id, seconds))
        owner = _owning_service(instrument_id)
        ledger = ledgers.get(owner, _ServiceLedger()) if owner is not None else _ServiceLedger()
        for start_ns, end_ns in _find_gaps(seconds):
            report.gaps.append(
                Gap(instrument_id, start_ns, end_ns, _explain_gap(ledger, start_ns, end_ns))
            )

    return report


def _fail_on_totals(report: Report, fail_on: tuple[str, ...]) -> dict[str, int]:
    """
    Each `--fail-on` site's count summed across every service in the report.

    `process_start` is not an ordinary site -- `error_ledger.site_counts()` excludes it, so it
    is counted here from each service's `restarts` instead. Naming it in `--fail-on` is how the
    operator makes a crash-loop fail the run: without it, 40 OOM-kills explain every gap they
    caused as `restart` and the day still exits 0 (DATA-07).
    """
    totals: dict[str, int] = dict.fromkeys(fail_on, 0)
    restarts_fail = error_ledger.PROCESS_START_SITE in totals
    for svc in report.services:
        if restarts_fail:
            totals[error_ledger.PROCESS_START_SITE] += svc.restarts
        for site, count in svc.site_counts.items():
            if site in totals:
                totals[site] += count
    return totals


def _print_services(report: Report) -> None:
    print("== Services ==")
    if not report.services:
        print("  (no ledger files found)")
    for svc in report.services:
        print(f"  {svc.service}: restarts={svc.restarts}")
        for site, count in sorted(svc.site_counts.items()):
            print(f"    {site}: {count}")


def _iso(ns: int | None) -> str:
    if ns is None:
        return "-"
    return datetime.fromtimestamp(ns / NS_PER_S, tz=UTC).isoformat()


def _print_instruments(report: Report) -> None:
    """Row coverage per instrument -- the check §6 asks for that a gap list cannot answer."""
    print("== Instruments ==")
    if not report.instruments:
        print("  (no second-snapshot partitions found)")
    for inst in report.instruments:
        print(
            f"  {inst.instrument_id}: rows={inst.rows} "
            f"first={_iso(inst.first_ns)} last={_iso(inst.last_ns)}"
        )


def _print_gaps(report: Report) -> None:
    print("== Instrument gaps ==")
    if not report.gaps:
        print("  (none)")
    for gap in report.gaps:
        window = f"[{_iso(gap.start_ns)} .. {_iso(gap.end_ns)}]"
        print(f"  {gap.instrument_id} {window}: {gap.explanation}")


def _print_report(report: Report, fail_on: tuple[str, ...]) -> None:
    _print_services(report)
    _print_instruments(report)
    _print_gaps(report)
    print("== Fail-on sites ==")
    for site, count in _fail_on_totals(report, fail_on).items():
        print(f"  {site}: {count}")


def _exit_code(report: Report, fail_on: tuple[str, ...]) -> int:
    if any(gap.unexplained for gap in report.gaps):
        return 1
    if any(count > 0 for count in _fail_on_totals(report, fail_on).values()):
        return 1
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").strip().split("\n\n")[0])
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--errors-dir", required=True)
    parser.add_argument("--since", help="ISO-8601 UTC instant; default: 24h before --until")
    parser.add_argument("--until", help="ISO-8601 UTC instant; default: now")
    parser.add_argument("--venue", choices=("dydx", "bybit", "hyperliquid"))
    parser.add_argument("--fail-on", nargs="*", default=list(_DEFAULT_FAIL_ON))
    return parser


def _window(since: str | None, until: str | None) -> tuple[int, int]:
    """Resolve `--since`/`--until` to a half-open ns window; raises ValueError when invalid."""
    until_ns = _parse_ts(until) if until else _to_ns(datetime.now(UTC))
    since_ns = (
        _parse_ts(since)
        if since
        else until_ns - int(timedelta(hours=24).total_seconds()) * NS_PER_S
    )
    if since_ns >= until_ns:
        raise ValueError("--since must be before --until")
    return since_ns, until_ns


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        since_ns, until_ns = _window(args.since, args.until)
    except ValueError as exc:
        print(
            f"error: --since/--until must be ISO-8601 UTC instants with --since first: {exc}",
            file=sys.stderr,
        )
        return 1
    if not args.fail_on:
        # `--fail-on` with no values (nargs="*") is a valid but easy-to-hit foot-gun: it
        # silently drops the documented defaults instead of disabling the check on purpose.
        print(
            "warning: --fail-on given with no sites; no site will fail the exit code",
            file=sys.stderr,
        )

    report = build_report(args.catalog, args.errors_dir, since_ns, until_ns, args.venue)
    _print_report(report, tuple(args.fail_on))
    if _nothing_was_checked(report, args.errors_dir, args.catalog):
        return 1
    return _exit_code(report, tuple(args.fail_on))


def _nothing_was_checked(report: Report, errors_dir: str, catalog_path: str) -> bool:
    """
    Report whether the run read no ledger and no instrument -- it must not certify the day.

    `build_report` stays pure -- an empty report is a legitimate value there. But exiting 0 on
    a missing `errors_dir` mount, an unset `ERROR_LEDGER_DIR`, or a `--catalog` pointing
    somewhere with no second-snapshot partitions would report "clean" having checked nothing,
    which is the one answer this tool must never give (DATA-07).
    """
    if not report.services:
        print(
            f"error: no ledger file under {errors_dir}; nothing was cross-checked "
            "(is the errors_dir mount present and ERROR_LEDGER_DIR set on every service?)",
            file=sys.stderr,
        )
        return True
    if not report.instruments:
        print(
            f"error: no second-snapshot instrument under {catalog_path}; nothing was "
            "cross-checked (wrong --catalog, or --venue matches no collected instrument?)",
            file=sys.stderr,
        )
        return True
    return False


if __name__ == "__main__":
    sys.exit(main())
