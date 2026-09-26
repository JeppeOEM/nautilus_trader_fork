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
Pure collection-overview-pane row/formatting functions (Story 6.1) -- no urwid import,
no I/O, mirroring bots_pane.py's separation of concerns.

collector:status is published one message per instrument (like bots:status is one
message per bot) -- collector_state.py accumulates the latest message per instrument id
into a dict; this module turns that dict into a deterministic, orderable row list and
formats individual fields for display.

Story 25.1b adds the nightly-maintenance line (`format_archive_line`) shown under the rows, from
archive_state.py's latest `archive:status` message.
"""

import datetime as dt


COLD_OPEN_TEXT = "waiting for collector:status…"


def collector_rows(statuses: dict[str, dict]) -> list[dict]:
    """Latest known status dict per instrument, sorted by id."""
    return sorted(statuses.values(), key=lambda row: row["id"])


def format_collector_line(row: dict, stale: bool) -> str:
    """
    One full plain-text row: id, liquid/illiquid label.
    Used directly by tests and as the source of truth app.py's urwid.Text must match.
    """
    stale_marker = "~ " if stale else "  "
    liquid_text = "liquid" if row.get("liquid", False) else "illiquid"
    return f"{stale_marker}{row['id']:<22} {liquid_text:<8}"


def format_unpinned_line(unpinned_ids: list[str]) -> str:
    """
    One trailing informational line listing every id in collector.py's config.exclude --
    whether it landed there via the TUI's "unpin" control action or a hand-edit of
    config.toml. These aren't currently collected, so they have no row of their own
    above this line. Empty string (render nothing) if none.
    """
    if not unpinned_ids:
        return ""
    return "unpinned (add back with :start <ID>): " + ", ".join(sorted(unpinned_ids))


ARCHIVE_NO_STATUS_TEXT = "archive: no status yet"


def _run_outcome(steps: list) -> str:
    """Return ok if every step exited 0, findings if the only non-zero exits are 2, else FAILED."""
    exits = [step.get("exit") if isinstance(step, dict) else None for step in steps]
    if all(e == 0 for e in exits):
        return "ok"
    if all(e in (0, 2) for e in exits):
        return "findings"
    return "FAILED"


def _step_label(step: dict) -> str:
    venue = step.get("venue")
    prefix = f"{venue} " if isinstance(venue, str) and venue else ""
    return f"{prefix}{step.get('name')}={step.get('exit')}"


def _non_zero_steps(steps: list) -> str:
    return ", ".join(
        _step_label(step) for step in steps if isinstance(step, dict) and step.get("exit") != 0
    )


def _parse_utc(value: object) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(dt.UTC)


def _hhmm(value: object) -> str:
    parsed = _parse_utc(value)
    return parsed.strftime("%H:%M") if parsed else "?"


def _run_day(run: dict) -> str:
    day = run.get("day")
    if isinstance(day, str):
        return day
    days = run.get("days")
    if isinstance(days, list) and days:
        return ",".join(str(d) for d in days)
    return "?"


def _run_steps(run: dict) -> list:
    steps = run.get("steps")
    return steps if isinstance(steps, list) else []


def _finished_run_text(label: str, run: dict) -> str:
    steps = _run_steps(run)
    outcome = _run_outcome(steps)
    detail = "" if outcome == "ok" else f" ({_non_zero_steps(steps)})"
    span = f"{_hhmm(run.get('started'))}-{_hhmm(run.get('finished'))}Z"
    return f"{label} {_run_day(run)} {outcome}{detail} {span}"


def _next_run_text(next_run: object, now: dt.datetime) -> str:
    parsed = _parse_utc(next_run)
    if parsed is None:
        return f"next {next_run!r}"
    minutes = max(0, int((parsed - now).total_seconds() // 60))
    return f"next {parsed:%Y-%m-%d %H:%M}Z (in {minutes // 60}h{minutes % 60:02d}m)"


def format_archive_line(status: dict | None, now: dt.datetime, stale: bool = False) -> str:
    """
    One line for the nightly maintenance (`archive:status`, Story 25.1b), e.g.
    `archive: last 2026-09-25 ok 03:07-03:41Z · next 2026-09-27 03:07Z (in 12h40m)` or
    `archive: running nightly 2026-09-25 (step 3)`. A failed intraday merge is appended so it
    never hides behind a clean nightly, and `backup off` when the status says the off-site backup
    is disabled (Story 26.1b: the catalog then has no copy off the host). `stale` (no message for
    several heartbeats) prefixes `~ `, the Collector rows' stale marker. Nested fields are read
    defensively: a message only guarantees `next_run`/`last_run`.
    """
    if status is None:
        return ARCHIVE_NO_STATUS_TEXT
    marker = "~ " if stale else ""
    running = status.get("running")
    last_run = status.get("last_run")
    if isinstance(running, dict):
        step = len(_run_steps(running)) + 1
        head = f"running {running.get('kind', '?')} {_run_day(running)} (step {step})"
    elif isinstance(last_run, dict):
        head = _finished_run_text("last", last_run)
    else:
        head = "no run yet"
    parts = [head]
    intraday = status.get("last_intraday")
    if isinstance(intraday, dict) and _run_outcome(_run_steps(intraday)) != "ok":
        parts.append(_finished_run_text("intraday", intraday))
    parts.append(_next_run_text(status.get("next_run"), now))
    if status.get("backup") == "disabled":
        parts.append("backup off")
    return f"{marker}archive: " + " · ".join(parts)
