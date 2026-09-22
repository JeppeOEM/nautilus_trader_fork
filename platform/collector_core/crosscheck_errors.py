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
snapshot row in the window is read through `ml_signals.catalog_stats.query_second_ohlc` -- never
a `ParquetDataCatalog` construction, one UTC day and one instrument at a time (MEM-01) -- and a
gap between two consecutive rows wider than `_GAP_THRESHOLD_NS` is reported. Each gap is matched,
within `_MAX_TS_INIT_SKEW_NS` of either edge, against the owning collector's ledger entries: a
`process_start` in that window explains it as a restart, any other entry explains it by site, and
no entry at all makes it `UNEXPLAINED` -- a DATA-07 finding, never tolerated.

Known limit: the gap-to-ledger match is time proximity only, not causally verified -- the
ledger's frozen AC1 line schema carries no instrument id, so an unrelated site's entry (e.g. a
`notify` failure) falling in the same ~10-minute window as a real, different-instrument data gap
will "explain" it, and two distinct gaps on one instrument inside that window can cross-explain
each other the same way. Upgrade path: carry the instrument id on every collector ledger site's
`detail`, structured, and match on it as well as on time.

Known limit: only gaps *between* two observed rows are detected; a dead instrument with no rows
at all in the window, or a gap touching the window's own `--since`/`--until` edge, is not (there
is no data on either side to diff against) -- `docs/DEPLOY_CHECKLIST.md` §6 therefore reads
"(none)" alongside a nonzero row-count check for exactly this reason. Upgrade path: persist each
run's last-seen timestamp per instrument and compare the next run's first row against it.

Exit code: non-zero when any gap is `UNEXPLAINED`, or any `--fail-on` site has a non-zero count
summed across the services printed; zero otherwise.
"""

import argparse
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from itertools import pairwise
from pathlib import Path

from ml_signals.catalog_stats import query_second_ohlc  # 23.2 moves this to kernel.catalog_files.
from ml_signals.venue import MalformedInstrumentId  # 23.2 moves this to kernel.venues.
from ml_signals.venue import venue_of  # 23.2 moves this to kernel.venues.
from observability import error_ledger

from collector_core.archive_gaps import ARRIVAL_MARGIN_NS


NS_PER_S = 1_000_000_000  # 23.2 moves this to kernel.clocks.NS_PER_S.
NS_PER_DAY = 86_400 * NS_PER_S  # 23.2 moves this to kernel.clocks.NS_PER_DAY.

# The catalog's second-snapshot partition directory, as `ml_signals.catalog_stats` itself spells
# it. 23.2 moves this to kernel.catalog_files.SNAPSHOT_DIRNAME.
SNAPSHOT_DIRNAME = "custom_dydx_second_snapshot"

# The one 300 s skew bound the archive already uses for "how far apart two clocks can be"
# (`collector_core.archive_gaps.ARRIVAL_MARGIN_NS`); 23.2 renames it kernel.clocks.
# MAX_TS_INIT_SKEW_NS. Aliased here so this module reads as the matcher's tolerance, not as a
# trade-arrival margin.
_MAX_TS_INIT_SKEW_NS = ARRIVAL_MARGIN_NS

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
class Report:
    services: list[ServiceReport] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)


def _parse_ts(text: str) -> int:
    """`--since`/`--until`: an ISO-8601 UTC instant (`Z` or `+00:00`) -> epoch ns."""
    moment = datetime.fromisoformat(text)  # Python 3.11+ parses the `Z` suffix itself
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.astimezone(UTC).timestamp()) * NS_PER_S


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


def _explain_gap(errors_dir: str, service: str | None, start_ns: int, end_ns: int) -> str:
    if service is None:
        return "UNEXPLAINED"
    records = list(
        error_ledger.iter_records(
            errors_dir,
            service,
            since_ns=start_ns - _MAX_TS_INIT_SKEW_NS,
            until_ns=end_ns + _MAX_TS_INIT_SKEW_NS,
        )
    )
    if any(rec["site"] == error_ledger.PROCESS_START_SITE for rec in records):
        return "restart"
    sites = sorted({rec["site"] for rec in records})
    if sites:
        return f"explained: {', '.join(sites)}"
    return "UNEXPLAINED"


def _service_report(errors_dir: str, service: str, since_ns: int, until_ns: int) -> ServiceReport:
    records = list(
        error_ledger.iter_records(errors_dir, service, since_ns=since_ns, until_ns=until_ns)
    )
    restarts = sum(1 for rec in records if rec["site"] == error_ledger.PROCESS_START_SITE)
    return ServiceReport(
        service=service, restarts=restarts, site_counts=error_ledger.site_counts(records)
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
    """
    report = Report()

    for service in error_ledger.services(errors_dir):
        report.services.append(_service_report(errors_dir, service, since_ns, until_ns))

    for instrument_id in _instruments_for_venue(catalog_path, venue):
        seconds = _snapshot_seconds(catalog_path, instrument_id, since_ns, until_ns)
        owner = _owning_service(instrument_id)
        for start_ns, end_ns in _find_gaps(seconds):
            explanation = _explain_gap(errors_dir, owner, start_ns, end_ns)
            report.gaps.append(Gap(instrument_id, start_ns, end_ns, explanation))

    return report


def _fail_on_totals(report: Report, fail_on: tuple[str, ...]) -> dict[str, int]:
    """Each `--fail-on` site's count summed across every service in the report."""
    totals: dict[str, int] = dict.fromkeys(fail_on, 0)
    for svc in report.services:
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


def _print_gaps(report: Report) -> None:
    print("== Instrument gaps ==")
    if not report.gaps:
        print("  (none)")
    for gap in report.gaps:
        start = datetime.fromtimestamp(gap.start_ns / NS_PER_S, tz=UTC).isoformat()
        end = datetime.fromtimestamp(gap.end_ns / NS_PER_S, tz=UTC).isoformat()
        print(f"  {gap.instrument_id} [{start} .. {end}]: {gap.explanation}")


def _print_report(report: Report, fail_on: tuple[str, ...]) -> None:
    _print_services(report)
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
    until_ns = _parse_ts(until) if until else int(datetime.now(UTC).timestamp()) * NS_PER_S
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
    return _exit_code(report, tuple(args.fail_on))


if __name__ == "__main__":
    sys.exit(main())
