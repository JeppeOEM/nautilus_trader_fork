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
The day verdict (Story 31.11): each verifier's `--json` report reduced to one verdict per data
type, and the six reduced to one verdict per venue-day -- what `archive.verify_day` records and the
`archive` scheduler keeps as `verification_days`.

Invariant: a reduction never passes what the tool did not pass, and never re-derives a tool's
predicate. A type is `pass` only when its tool exited 0 *and* its report says `passed: true` with
at least one instrument judged, none failing and its `failing` count 0 (the candles tool, whose
exit 0 also covers a provisional run, additionally needs the served bars checked); any other
report is `fail`; no parseable report is `refused`. Every
count is read from the report, where the tool's own domain computed it (SSOT-01): each
instrument's `passed` and `failing`, the top-level `failing` where the report has one. The only
counts summed here are those the report does not carry itself: catalog instruments (its `parity`
and `candles` entries, one per instrument and type) and candles instruments (whose `passed` is
`failing == 0`, the candles domain's own per-instrument rule). A day is `verified` only when every
tool of `TOOLS` has a `pass`. Standard library only, no I/O: the tools are reached through their
command lines by `archive.verify_day`, never imported.

The liquidations self-check (Story 33.1) is deliberately *not* a tool of `TOOLS`: it states a
matched share, never a pass, and a venue without a liquidation feed would otherwise never be
`verified`. `summarise_liquidations` reduces its report to the summary `verify_day` keeps beside
the verdict (result key `liquidations`), for the venues of `LIQUIDATION_VENUES` only.
"""

import json
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import replace
from typing import Any


TOOLS = ("conservation", "trades", "book", "derivs", "catalog", "candles")

PASSED = "pass"
FAILED = "fail"
REFUSED = "refused"

VERIFIED = "verified"
FINDINGS = "findings"

# The venues with a liquidation feed, whose day `verify_day` also reports (never judges).
# Hyperliquid is absent: it has no feed, Story 33.2 having refuted both public-data hypotheses
# (`docs/DATA_DICTIONARY.md` §1.26).
LIQUIDATION_VENUES = ("BYBIT",)
# The summary's `report` when the tool printed a report it could read.
REPORTED = "reported"

# The candles tool's `served` value when the data_api's bars were compared (`--no-served` omits it).
SERVED_CHECKED = "checked"


class MalformedReport(ValueError):
    """A report that parsed as JSON but lacks a count or verdict the reduction needs."""


@dataclass(frozen=True)
class InstrumentVerdict:
    """One instrument's result within one type, as its tool's report states it."""

    passed: bool
    failing: int


@dataclass(frozen=True)
class TypeVerdict:
    """
    One data type's verdict for one venue-day. `failing`: the report's failing count;
    `inputs_missing`: missing raw reference files plus 1 for an absent coverage record;
    `missing_raw_files` names them (for the ledger); `reason`: why a `refused` has no report.
    """

    tool: str
    verdict: str
    instruments: Mapping[str, InstrumentVerdict]
    failing: int = 0
    inputs_missing: int = 0
    missing_raw_files: tuple[str, ...] = ()
    reason: str = ""

    @property
    def passed(self) -> bool:
        return self.verdict == PASSED

    @property
    def failing_instruments(self) -> tuple[str, ...]:
        return tuple(iid for iid, result in self.instruments.items() if not result.passed)

    def to_json(self) -> dict[str, Any]:
        """Return the verdict as `verification_days` keeps it (raw file names stay in the ledger)."""
        return {
            "verdict": self.verdict,
            "failing": self.failing,
            "inputs_missing": self.inputs_missing,
            "reason": self.reason,
            "instruments": {
                iid: {"passed": result.passed, "failing": result.failing}
                for iid, result in sorted(self.instruments.items())
            },
        }


def parse_report(text: str) -> Mapping[str, Any] | None:
    """Return a tool's stdout as its JSON report object; None when it is not one."""
    try:
        body = json.loads(text)
    except ValueError:  # json.JSONDecodeError is a ValueError
        return None
    return body if isinstance(body, dict) else None


def _count(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MalformedReport(f"{what} is not a count: {value!r}")
    return value


def _flag(value: object, what: str) -> bool:
    if not isinstance(value, bool):
        raise MalformedReport(f"{what} is not a boolean: {value!r}")
    return value


def _entries(body: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    entries = body.get(key)
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise MalformedReport(f"`{key}` is not a list of objects: {entries!r}")
    return entries


def _iid(entry: Mapping[str, Any]) -> str:
    iid = entry.get("instrument_id")
    if not isinstance(iid, str):
        raise MalformedReport(f"an entry without an instrument_id: {entry!r}")
    return iid


def _reported_instruments(body: Mapping[str, Any]) -> dict[str, InstrumentVerdict]:
    """conservation, trades, book, derivs: every entry carries its own `passed` and `failing`."""
    instruments: dict[str, InstrumentVerdict] = {}
    for entry in _entries(body, "instruments"):
        iid = _iid(entry)
        if iid in instruments:  # a second entry must never overwrite a failing first one
            raise MalformedReport(f"instrument {iid} reported twice")
        passed = _flag(entry.get("passed"), f"{iid} passed")
        instruments[iid] = InstrumentVerdict(passed, _count(entry.get("failing"), f"{iid} failing"))
    return instruments


def _summed(entries: Iterable[Mapping[str, Any]]) -> dict[str, InstrumentVerdict]:
    failing: dict[str, int] = {}
    for entry in entries:
        iid = _iid(entry)
        failing[iid] = failing.get(iid, 0) + _count(entry.get("failing"), f"{iid} failing")
    return {iid: InstrumentVerdict(count == 0, count) for iid, count in failing.items()}


def _instruments(tool: str, body: Mapping[str, Any]) -> dict[str, InstrumentVerdict]:
    if tool == "catalog":
        return _summed([*_entries(body, "parity"), *_entries(body, "candles")])
    if tool == "candles":
        return _summed(_entries(body, "instruments"))
    return _reported_instruments(body)


def _inputs_missing(body: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the report's missing raw reference files (none without the key: catalog)."""
    missing = body.get("missing_raw_files", [])
    if not isinstance(missing, list) or not all(isinstance(m, str) for m in missing):
        raise MalformedReport(f"`missing_raw_files` is not a list of names: {missing!r}")
    return tuple(missing)


def _passes(tool: str, exit_code: int, body: Mapping[str, Any], verdict: TypeVerdict) -> bool:
    # A report contradicting itself (`passed` with a failing instrument) is never a pass.
    passed = exit_code == 0 and _flag(body.get("passed"), "passed")
    passed = passed and not verdict.failing_instruments and verdict.failing == 0
    # A report judging no instrument (an empty plan, a wrong mount) checked nothing: never a pass.
    passed = passed and bool(verdict.instruments)
    if tool == "candles":
        return passed and body.get("served") == SERVED_CHECKED
    return passed


def _reduce(tool: str, exit_code: int, body: Mapping[str, Any]) -> TypeVerdict:
    instruments = _instruments(tool, body)
    if "failing" in body:
        failing = _count(body["failing"], "failing")
    else:
        failing = sum(result.failing for result in instruments.values())
    missing = _inputs_missing(body)
    coverage = body.get("coverage_present", True)
    absent_coverage = 0 if _flag(coverage, "coverage_present") else 1
    counted = TypeVerdict(
        tool=tool,
        verdict=FAILED,
        failing=failing,
        inputs_missing=len(missing) + absent_coverage,
        instruments=instruments,
        missing_raw_files=missing,
    )
    return replace(counted, verdict=PASSED) if _passes(tool, exit_code, body, counted) else counted


def summarise(
    tool: str, exit_code: int, body: Mapping[str, Any] | None, reason: str = ""
) -> TypeVerdict:
    """
    Reduce one tool run to its type's verdict: `pass` only on exit 0 with a passing report (see the
    module docstring), `fail` on any other report, `refused` with no report or one missing a count
    the reduction needs (`reason` says why; a default names the exit code).
    """
    if tool not in TOOLS:
        raise ValueError(f"unknown verification tool {tool!r}: one of {', '.join(TOOLS)}")
    if body is None:
        return TypeVerdict(tool, REFUSED, {}, reason=reason or f"no JSON report (exit {exit_code})")
    try:
        return _reduce(tool, exit_code, body)
    except MalformedReport as exc:
        return TypeVerdict(tool, REFUSED, {}, reason=f"malformed report (exit {exit_code}): {exc}")


def day_verdict(types: Iterable[TypeVerdict], checked_at: str) -> dict[str, Any]:
    """
    Return the venue-day's verdict: `verified` only when every tool of `TOOLS` has a `pass` (a type
    never run is not a pass), else `findings`; each type's `to_json` under its tool.
    """
    by_tool = {verdict.tool: verdict for verdict in types}
    verified = all(tool in by_tool and by_tool[tool].passed for tool in TOOLS)
    return {
        "verification": VERIFIED if verified else FINDINGS,
        "checked_at": checked_at,
        "types": {tool: verdict.to_json() for tool, verdict in by_tool.items()},
    }


def _share(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 1:
        raise MalformedReport(f"share is not a fraction: {value!r}")
    return float(value)


def _liquidation_instruments(body: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    instruments: dict[str, dict[str, int]] = {}
    for entry in _entries(body, "instruments"):
        iid = _iid(entry)
        if iid in instruments:
            raise MalformedReport(f"instrument {iid} reported twice")
        instruments[iid] = {
            "total": _count(entry.get("total"), f"{iid} total"),
            "matched": _count(entry.get("matched"), f"{iid} matched"),
            "unmatched": _count(entry.get("unmatched_count"), f"{iid} unmatched_count"),
            "unrecoverable_seconds": _count(
                entry.get("unrecoverable_seconds"), f"{iid} unrecoverable_seconds"
            ),
        }
    return instruments


def summarise_liquidations(
    exit_code: int, body: Mapping[str, Any] | None, reason: str = ""
) -> dict[str, Any]:
    """
    Reduce one liquidations report to the summary `verify_day` keeps under `liquidations`:
    `report` `reported` with `coverage_present` and the counts the report states (the share as it states it, never
    recomputed), or `refused` with why there is none. Never a verdict: nothing here passes or
    fails a day.
    """
    if body is None:
        return {"report": REFUSED, "reason": reason or f"no JSON report (exit {exit_code})"}
    try:
        return {
            "report": REPORTED,
            "applicable": _flag(body.get("applicable"), "applicable"),
            # Without the coverage record `unrecoverable_seconds` 0 means unknown, never none.
            "coverage_present": _flag(body.get("coverage_present"), "coverage_present"),
            "total": _count(body.get("total"), "total"),
            "matched": _count(body.get("matched"), "matched"),
            "share": _share(body.get("share")),
            "unrecoverable_seconds": _count(
                body.get("unrecoverable_seconds"), "unrecoverable_seconds"
            ),
            "instruments": _liquidation_instruments(body),
        }
    except MalformedReport as exc:
        return {"report": REFUSED, "reason": f"malformed report (exit {exit_code}): {exc}"}
