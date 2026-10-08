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
The liquidations tool's day report (Story 33.1): per instrument of the venue, every archived
liquidation of the UTC day matched against the archived trades (`domain.liquidation_check`), and
the seconds of the day the coverage record says no liquidation could be received
(`liquidations_unrecoverable`, `feed_down` or `not_running`).

The report states a share, never a verdict: there is no `passed` and no failing count, so
`archive.verify_day` keeps it beside the day's verdict and never in it. A venue without a
liquidation feed (`LIQUIDATION_VENUES`) is reported `applicable: false` with no instrument.
"""

from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any
from typing import Protocol

from kernel.venues import has_venue

from verification.application.conservation import CoverageSource
from verification.application.conservation import day_end_ns
from verification.application.conservation import day_start_ns
from verification.domain.conservation import CoverageEntry
from verification.domain.conservation import LiquidationWindow
from verification.domain.liquidation_check import MATCH_WINDOW_NS
from verification.domain.liquidation_check import UNMATCHED_SHOWN
from verification.domain.liquidation_check import Fill
from verification.domain.liquidation_check import InstrumentMatch
from verification.domain.liquidation_check import StoredLiquidation
from verification.domain.liquidation_check import covered_seconds
from verification.domain.liquidation_check import instrument_match
from verification.domain.verdict import LIQUIDATION_VENUES


class LiquidationSource(Protocol):
    """The catalog's liquidations and candidate trades, read raw (`LiquidationCatalog`)."""

    def instruments(self, venue: str) -> list[str]: ...

    def liquidations(
        self, instrument_id: str, start_ns: int, end_ns: int
    ) -> list[StoredLiquidation]: ...

    def fills(self, instrument_id: str, window: tuple[int, int], sizes: set[int]) -> list[Fill]: ...


@dataclass(frozen=True)
class LiquidationInputs:
    catalog: LiquidationSource
    coverage: CoverageSource


@dataclass(frozen=True)
class LiquidationDayReport:
    venue: str
    day: date
    applicable: bool
    coverage_present: bool
    instruments: tuple[InstrumentMatch, ...]


def _windows(
    entries: Iterable[CoverageEntry], venue: str, span: tuple[int, int]
) -> dict[str, list[tuple[int, int]]]:
    """
    Return the venue's liquidation windows overlapping the day, kept while the coverage file streams
    (MEM-01: the file's whole history is never held).
    """
    windows: dict[str, list[tuple[int, int]]] = {}
    for entry in entries:
        if not isinstance(entry, LiquidationWindow) or not has_venue(entry.instrument_id, venue):
            continue
        if entry.to_ns >= span[0] and entry.from_ns < span[1]:
            windows.setdefault(entry.instrument_id, []).append((entry.from_ns, entry.to_ns))
    return windows


def _instrument(
    iid: str, catalog: LiquidationSource, windows: list[tuple[int, int]], span: tuple[int, int]
) -> InstrumentMatch | None:
    """Return the instrument's day, or None when it had neither a liquidation nor a window that day."""
    start_ns, end_ns = span
    rows = catalog.liquidations(iid, start_ns, end_ns)
    if not rows and not windows:
        return None
    # Trades up to the window past either edge: a liquidation at 23:59:59 may print at 00:00:01.
    margin = (start_ns - MATCH_WINDOW_NS, end_ns + MATCH_WINDOW_NS)
    fills = catalog.fills(iid, margin, {row.size_raw() for row in rows})
    return instrument_match(iid, rows, fills, covered_seconds(windows, start_ns, end_ns))


def check_day(venue: str, day: date, inputs: LiquidationInputs) -> LiquidationDayReport:
    """
    Match the day's liquidations: the instruments with a stored liquidation that day or a
    liquidation window overlapping it (a directory of an instrument quiet that day is not one).
    """
    present = inputs.coverage.present()
    if venue not in LIQUIDATION_VENUES:
        return LiquidationDayReport(venue, day, False, present, ())
    start_ns = day_start_ns(day)
    span = (start_ns, day_end_ns(start_ns))
    windows = _windows(inputs.coverage.entries(), venue, span)
    iids = sorted(set(inputs.catalog.instruments(venue)) | set(windows))
    found = (_instrument(i, inputs.catalog, windows.get(i, []), span) for i in iids)
    return LiquidationDayReport(venue, day, True, present, tuple(m for m in found if m))


def _share(matched: int, total: int) -> float | None:
    return None if total == 0 else round(matched / total, 6)


def _instrument_json(match: InstrumentMatch) -> dict[str, Any]:
    return {
        "instrument_id": match.instrument_id,
        "total": match.total,
        "matched": match.matched,
        "share": _share(match.matched, match.total),
        "unmatched_count": len(match.unmatched_ids),
        "unmatched_ids": list(match.unmatched_ids[:UNMATCHED_SHOWN]),
        "unrecoverable_seconds": match.unrecoverable_seconds,
    }


def report_json(report: LiquidationDayReport) -> dict[str, Any]:
    """Return the `--json` report: the day's totals, then one entry per instrument (unmatched ids capped)."""
    total = sum(m.total for m in report.instruments)
    matched = sum(m.matched for m in report.instruments)
    return {
        "venue": report.venue,
        "day": report.day.isoformat(),
        "applicable": report.applicable,
        "coverage_present": report.coverage_present,
        "total": total,
        "matched": matched,
        "share": _share(matched, total),
        "unrecoverable_seconds": sum(m.unrecoverable_seconds for m in report.instruments),
        "instruments": [_instrument_json(m) for m in report.instruments],
    }


def _line(entry: Mapping[str, Any]) -> str:
    share = "-" if entry["share"] is None else f"{entry['share']:.1%}"
    head = f"  {entry['instrument_id']}: {entry['matched']}/{entry['total']} matched ({share})"
    tail = f", unrecoverable {entry['unrecoverable_seconds']} s"
    shown = entry["unmatched_ids"]
    return head + tail + (f"; unmatched: {', '.join(shown)}" if shown else "")


def render_text(report: LiquidationDayReport) -> str:
    body = report_json(report)
    if not report.applicable:
        return f"liquidations {report.venue} {report.day}: not applicable (no liquidation feed)"
    lines = [
        f"liquidations {report.venue} {report.day}: {body['matched']}/{body['total']} matched to "
        f"a same-size forced-side trade within 2 s (a self-check, never 100 %)",
        *(_line(entry) for entry in body["instruments"]),
    ]
    if not report.coverage_present:
        lines.append("  no coverage record: unrecoverable windows unknown")
    return "\n".join(lines)
