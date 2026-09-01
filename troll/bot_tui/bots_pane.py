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
"""

COLD_OPEN_TEXT = "waiting for bots:status…"


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
