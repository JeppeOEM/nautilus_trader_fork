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
Pure Bots-pane row/formatting functions (Story 4.4, AC1/AC2; Story 4.5, AC1) -- no
urwid import, no I/O.

bots:status is published one message per bot (one live_paper process = one bot),
unlike rankings:live's single aggregated list -- bots_state.py accumulates the latest
message per bot_id into a dict; this module turns that dict into a deterministic,
orderable row list and formats individual fields for display. app.py assembles the
final urwid markup itself (coloring only the PnL segment, mirroring Story 4.3's
bid/ask ladder-column precedent of "fixed position + color, never color alone").

Story 4.5 adds bot_detail_lines()/format_win_rate_detail() for Bot-detail's live-
snapshot-header region (a different text layout from the Bots-pane row, sharing the
same underlying bots:status row shape and the same format_pnl/format_exposure/
format_uptime helpers -- format_win_rate/format_bot_line, the Bots-pane row's own
already-shipped Story 4.4 formatting, are deliberately left untouched).

Story 4.7 adds the trades-blotter/PnL-sparkline formatters for Bot-detail's other two
regions (bots:history:{bot_id}:{range}, read via bot_history_state.py -- a distinct
wire contract from bots:status, so these take a `dict | None` history entry directly
rather than the `row: dict` shape every function above takes) plus next_range() and
dashboard_bot_url(), the pure logic behind the `t`/`o` keys app.py wires in.
"""

from datetime import UTC
from datetime import datetime


COLD_OPEN_TEXT = "waiting for bots:status…"

# Distinct from COLD_OPEN_TEXT (that's "no bots:status message has ever arrived for
# any bot"): these two describe bots:history:{bot_id}:{range}'s own three-state shape
# for whichever single bot/range Bot-detail currently has open (Story 4.7, AC1/AC4).
# "unavailable" (never fetched, or stale past bot_history_state's own timeout) must
# stay visually distinct from "fetched, genuinely has zero trades" -- collapsing them
# would make a bot that's simply never traded indistinguishable from a broken read
# path, the same "None is not 0.0" discipline format_win_rate_detail already applies
# to win_rate.
HISTORY_UNAVAILABLE_TEXT = "history unavailable"
NO_TRADES_YET_TEXT = "no trades yet"

_RANGE_CYCLE = ("day", "week", "month", "all")
_SPARK_CHARS = "▁▂▃▄▅▆▇█"


def bot_rows(statuses: dict[str, dict]) -> list[dict]:
    """
    Return the latest known status dict per bot, sorted by bot_id for a stable render
    order. bots:status has no ranking concept of its own (unlike rankings:live's "row
    order is the wire contract" rule) -- alphabetical bot_id is simply the simplest
    deterministic choice available, not a meaningful order preserved from the wire.
    """
    return [statuses[bot_id] for bot_id in sorted(statuses)]


def format_pnl(value: float) -> str:
    """
    Sign-prefixed PnL text, e.g. "+123.45" / "-6.00" -- the sign always lives in the
    text itself, never carried by color alone (color is an additional cue app.py
    applies on top, never a substitute for it).
    """
    sign = "+" if value >= 0 else "-"
    return f"{sign}{abs(value):>9.2f}"


def format_exposure(value: float) -> str:
    return f"{value:>10.2f}"


def format_uptime(started_at: float, now: float) -> str:
    """
    "{h}h{m:02}m" once an hour has elapsed, "{m}m{s:02}s" under an hour -- terse,
    data-only, matching this codebase's established voice (troll/CLAUDE.md READ-01),
    never a fully-spelled-out duration string.
    """
    elapsed = max(0.0, now - started_at)
    total_seconds = int(elapsed)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h{minutes:02}m"
    return f"{minutes}m{seconds:02}s"


def format_win_rate(win_rate: float | None) -> str:
    """
    "n/a" before any position has closed -- never a fabricated 0% (mirrors
    coin_detail.format_indicator's "warming up..." sentinel for the same idea).
    """
    if win_rate is None:
        return "n/a"
    return f"{win_rate:.0%}"


def format_bot_line(row: dict, stale: bool, now: float) -> str:
    """
    One full plain-text row -- bot_id, PnL (realized + unrealized), symbol, mode,
    running/stopped, position side, net exposure, uptime, win-rate (Story 4.4, AC1).
    Used directly by tests and as the source of truth app.py's colored urwid.Text
    markup must match field-for-field.
    """
    stale_marker = "~ " if stale else "  "
    pnl_text = format_pnl(row["realized_pnl"] + row["unrealized_pnl"])
    uptime_text = format_uptime(row["started_at"], now)
    win_rate_text = format_win_rate(row["win_rate"])
    running_text = "run" if row["running"] else "off"
    return (
        f"{stale_marker}{row['bot_id']:<12} {pnl_text}  {row['symbol']:<18} "
        f"{row['mode']:<5} {running_text} {row['position_side']:<5} "
        f"{format_exposure(row['net_exposure'])}  up {uptime_text}  wr {win_rate_text}"
    )


def format_win_rate_detail(win_rate: float | None, closed_trades: int) -> str:
    """
    "n/a" before any position has closed (never a fabricated "0% (0 trades)") --
    otherwise "{pct}% ({n} trades)", e.g. "41% (63 trades)" (Story 4.5, AC1). A real
    0% win rate (at least one closed, losing trade) is a distinguishable value from
    "no trades yet" -- both are handled: None means the latter, 0.0 means the former.
    """
    if win_rate is None:
        return "n/a"
    return f"{win_rate:.0%} ({closed_trades} trades)"


def bot_detail_lines(row: dict, now: float) -> list[str]:
    """
    Bot-detail's live-snapshot-header region, as three plain-text lines matching the
    UX mockup's two-column field pairing (mockups/key-bot-detail.html):
    line 1 = strategy/symbol + mode, line 2 = PnL + position, line 3 = uptime +
    win-rate. app.py lays these out in urwid.Columns and colors the PnL segment --
    this function only produces the text (Story 4.5, AC1).
    """
    pnl_text = format_pnl(row["realized_pnl"] + row["unrealized_pnl"])
    uptime_text = format_uptime(row["started_at"], now)
    win_rate_text = format_win_rate_detail(row["win_rate"], row["closed_trades"])
    return [
        f"strategy   {row['strategy']} / {row['symbol']}        mode      {row['mode']}",
        f"pnl        {pnl_text}                              "
        f"position  {row['position_side']} {format_exposure(row['net_exposure']).strip()}",
        f"uptime     {uptime_text}                                win rate  {win_rate_text}",
    ]


def next_range(current: str) -> str:
    """Day -> week -> month -> all -> day..., never free-form (Story 4.7, AC2)."""
    idx = _RANGE_CYCLE.index(current)
    return _RANGE_CYCLE[(idx + 1) % len(_RANGE_CYCLE)]


def format_trade_line(trade: dict) -> str:
    """
    One blotter row: timestamp (from trade["ts"], UNIX nanoseconds per Story 4.6's wire
    contract), side, price, qty, realized PnL -- blank (not "0.00") for a non-closing
    fill, matching trade["realized_pnl"] being None rather than a fabricated zero.
    """
    ts_text = datetime.fromtimestamp(trade["ts"] / 1_000_000_000, tz=UTC).strftime("%m-%d %H:%M:%S")
    pnl = trade["realized_pnl"]
    # Width matches format_pnl's own output exactly (not a hardcoded literal) so a
    # future change to that function's formatting can't silently misalign this column.
    pnl_text = format_pnl(pnl) if pnl is not None else " " * len(format_pnl(0.0))
    return (
        f"{ts_text}  {trade['side']:<4} {trade['price']:>12.2f} {trade['qty']:>10.4f}  {pnl_text}"
    )


def trades_blotter_lines(entry: dict | None) -> list[str]:
    """
    Bot-detail's trades-blotter region (Story 4.7, AC1/AC4): `entry` is
    bot_history_state.get_history()'s result for the currently-open bot/range --
    already None whenever that read surface is unavailable (never fetched or stale),
    so this function only has to distinguish "unavailable" from "fetched but empty"
    from "has real fills"; ordering (oldest-first) is inherited as-is from Story 4.6's
    wire contract, never re-sorted here.
    """
    if entry is None:
        return [HISTORY_UNAVAILABLE_TEXT]
    trades = entry.get("trades", [])
    if not trades:
        return [NO_TRADES_YET_TEXT]
    return [format_trade_line(trade) for trade in trades]


def pnl_sparkline_text(entry: dict | None) -> str:
    """
    Bot-detail's PnL-over-time region (Story 4.7, AC1/AC4) as a compact one-line
    unicode bar-per-bucket sparkline over `entry["pnl_series"]`, scaled to this
    series' own min/max (never a fixed absolute scale -- a single outlier day would
    otherwise flatten every other bucket's bar to the same lowest glyph). A flat
    series (every bucket equal, including the single-point case) renders the middle
    glyph throughout rather than dividing by a zero range.
    """
    if entry is None:
        return HISTORY_UNAVAILABLE_TEXT
    series = entry.get("pnl_series", [])
    if not series:
        return NO_TRADES_YET_TEXT
    values = [point["pnl"] for point in series]
    lo, hi = min(values), max(values)
    if hi == lo:
        return _SPARK_CHARS[len(_SPARK_CHARS) // 2] * len(values)
    span = hi - lo
    return "".join(
        _SPARK_CHARS[
            min(len(_SPARK_CHARS) - 1, round((value - lo) / span * (len(_SPARK_CHARS) - 1)))
        ]
        for value in values
    )


def dashboard_bot_url(base_url: str, bot_id: str) -> str:
    """
    `/bot/{bot_id}` (Story 4.7, AC3) -- mirrors coin_detail.dashboard_chart_url()'s
    exact URL-building shape. The web dashboard has no route there yet (checked: its
    route list has nothing bot-shaped) -- that gap pre-dates this story and building
    it is explicitly out of this story's scope (spec's Never section); this function's
    only job is producing the URL bot_tui itself opens.
    """
    return f"{base_url.rstrip('/')}/bot/{bot_id}"
