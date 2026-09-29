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
Pure market-browser functions (Story 29.5) -- no urwid import, no I/O, no module state, mirroring
collector_pane.py's separation of concerns.

The browser (the Collector pane's `/`) searches the venues' market names from `markets:live`
(markets_state.py), groups the matches per venue under that venue's Collector header, and marks
each row with what `collector:status` (collector_state.py) says about it. `a` adds the focused row
through `collector:control`'s `start`, after `add_refusal` finds nothing against it. Nothing here
is ever optimistic: only a `collector:status` row makes a row `collected`.

It shows names and states only -- no volume, price or capacity number (operator decision
2026-09-26), and no cap for Bybit or Hyperliquid, whose plans have none.
"""

from collections.abc import Collection
from collections.abc import Mapping
from collections.abc import Sequence
from typing import NamedTuple

from bot_tui.bots_pane import fit
from bot_tui.collector_pane import ID_WIDTH
from bot_tui.collector_pane import VenueSection
from bot_tui.collector_pane import format_section_header
from bot_tui.collector_pane import utc_ns_text


COLD_OPEN_TEXT = "waiting for markets:live…"

# How long an add sent from this TUI waits for its answer -- a status row or a `last_refusal`
# naming it -- before its row reads "no answer from <VENUE> collector" and `a` is allowed again.
# A collector applies and publishes a command at once, so two minutes is only reached when the
# message was lost (the collector down for less than collector_state's staleness window).
ADD_ANSWER_TIMEOUT_SECONDS: float = 120.0


class BrowserRow(NamedTuple):
    """One market of a venue's `markets:live` list."""

    instrument_id: str
    symbol: str


class BrowserGroup(NamedTuple):
    """One venue's matches, sorted by id."""

    venue: str
    rows: list[BrowserRow]


class AddContext(NamedTuple):
    """
    What `collector:status` and this TUI know about one id, the input of both `result_marker` and
    `add_refusal`: its status row (None when it has none), its venue's aggregate (None before one),
    when this TUI sent an add of it (None when none is outstanding) and, when the collector refused
    that add, the reason (`collector_state.add_refused_reason`, recorded as the refusal arrived).
    """

    instrument_id: str
    venue: str
    status_row: dict | None
    plan: dict | None
    sent_at: float | None
    refused_reason: str | None


def search(markets: Mapping[str, Sequence[tuple[str, str]]], query: str) -> list[BrowserGroup]:
    """
    Return every venue's markets whose symbol or id contains `query` -- trimmed and casefolded,
    matched case-insensitively as a substring; an empty query matches everything. One group per
    venue in `markets`, venues sorted, ids sorted within each; a venue without a match keeps its
    (empty) group, so its header still says it was searched.
    """
    needle = query.strip().casefold()
    groups = []
    for venue in sorted(markets):
        rows = sorted(
            BrowserRow(iid, symbol)
            for iid, symbol in markets[venue]
            if needle in symbol.casefold() or needle in iid.casefold()
        )
        groups.append(BrowserGroup(venue, rows))
    return groups


def _unpinned(plan: dict | None) -> Collection[str]:
    ids = (plan or {}).get("unpinned_ids")
    return ids if isinstance(ids, list) else ()


def _subscribe_failed_at(plan: dict | None, instrument_id: str) -> str | None:
    """Return the UTC time of the last apply when its `failed` list holds the id, else None."""
    last_apply = (plan or {}).get("last_apply")
    if not isinstance(last_apply, dict):
        return None
    failed = last_apply.get("failed")
    if isinstance(failed, list) and instrument_id in failed:
        return utc_ns_text(last_apply.get("ts"))
    return None


def refusal_reason(ctx: AddContext) -> str | None:
    """Return why the collector refused this TUI's outstanding add of the id, else None."""
    return ctx.refused_reason if ctx.sent_at is not None else None


def add_awaiting(ctx: AddContext, now: float) -> bool:
    """Return whether an add sent from here is still within its answer timeout, unanswered."""
    if ctx.sent_at is None or refusal_reason(ctx) is not None:
        return False
    return now - ctx.sent_at <= ADD_ANSWER_TIMEOUT_SECONDS


def _status_marker(ctx: AddContext, row: dict) -> str:
    if row.get("pending") is not True:
        return "collected"
    failed_at = _subscribe_failed_at(ctx.plan, ctx.instrument_id)
    if failed_at is None:
        return "pending"
    return f"pending · failed: subscribe failed in last apply {failed_at}, retrying"


def result_marker(ctx: AddContext, now: float) -> str:
    """
    Return the row's one marker, the first rule that applies: a status row (`collected`, or
    `pending` with the last apply's subscribe failure when it names the id); a refusal of this
    TUI's add (`failed: <reason>`); `excluded` (in the plan's `unpinned_ids`); an add still
    awaiting its answer (`pending`), or past `ADD_ANSWER_TIMEOUT_SECONDS` (`no answer from <VENUE>
    collector`); else blank.
    """
    if ctx.status_row is not None:
        return _status_marker(ctx, ctx.status_row)
    reason = refusal_reason(ctx)
    if reason is not None:
        return f"failed: {reason}"
    if ctx.instrument_id in _unpinned(ctx.plan):
        return "excluded"
    if ctx.sent_at is None:
        return ""
    if add_awaiting(ctx, now):
        return "pending"
    return f"no answer from {ctx.venue} collector"


def add_refusal(
    ctx: AddContext, now: float, venue_refusal: str | None, cap: int | None, count: int
) -> str | None:
    """
    Return why `a` must not send an add of this id, or None to send it -- the first that applies:
    the venue's plan takes no command (`venue_refusal`: missing, stale or static, from
    `collector_state.command_refusal`); already collected or in the plan pending; excluded (only
    `:start <ID>` re-adds an unpinned id); an add from here still awaiting its answer; the published
    `cap` reached by `count` -- the venue's rows plus this TUI's other adds still awaiting their
    answer, so two quick adds at cap - 1 do not both pass. A plan with no cap (Bybit, Hyperliquid)
    is never refused for size. Known limit: advisory only -- another TUI's in-flight adds are not
    counted, so the collector's own cap check stays the authority (its refusal shows as `failed`).
    """
    if venue_refusal is not None:
        return venue_refusal
    if ctx.status_row is not None:
        pending = ctx.status_row.get("pending") is True
        return "already in the plan (pending)" if pending else "already collected"
    if ctx.instrument_id in _unpinned(ctx.plan):
        return "excluded (unpinned): re-add with :start <ID>"
    if add_awaiting(ctx, now):
        return "add already sent, waiting for collector:status"
    if cap is not None and count >= cap:
        return f"cap reached ({cap})"
    return None


def format_group_header(
    group: BrowserGroup, section: VenueSection, plan_stale: bool, markets_stale: bool
) -> str:
    """
    Return the Collector pane's header for the group's venue (`format_section_header`, from its
    current `collector:status` state) plus `· M matches` (`· 1 match`). `markets_stale` marks the
    header the way its rows are (`~ `) when the Collector header does not already carry the marker.
    """
    header = format_section_header(section, plan_stale)
    marker = "~ " if markets_stale and not plan_stale else ""
    count = len(group.rows)
    return f"{marker}{header} · {count} {'match' if count == 1 else 'matches'}"


def format_result_line(row: BrowserRow, marker: str, stale: bool) -> str:
    """One result row: `~ ` when its venue's list is stale, the `fit()`-bounded id, the marker."""
    prefix = "~ " if stale else "  "
    return f"{prefix}{fit(row.instrument_id, ID_WIDTH)} {marker}".rstrip()
