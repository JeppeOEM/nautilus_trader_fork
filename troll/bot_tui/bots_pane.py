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

# Widths for the Bots-pane row's own genuinely-unbounded-length fields (bot_id is
# operator-chosen in config.toml; symbol grows with the instrument ticker, e.g.
# "RENDER-USD-PERP.DYDX" is 20 chars vs "BTC-USD-PERP.DYDX"'s 17) -- see fit()'s own
# docstring for why plain f-string `:<N` padding alone isn't sufficient here.
BOT_ID_WIDTH = 12
SYMBOL_WIDTH = 20


def fit(text: str, width: int) -> str:
    """
    Left-justify to exactly `width` characters, truncating (with a trailing "…")
    rather than overflowing when longer.

    Plain f-string `:<N` formatting only pads a short string -- it never clips a
    long one. bot_id and symbol are the two fields here with no fixed vocabulary
    (mode/running/position_side are all short, closed sets), so an operator-chosen
    bot_id or a longer-than-usual ticker (e.g. "RENDER-USD-PERP.DYDX" overflowing an
    18-char `:<18` field by 2) pushes every column after it out of alignment for
    that row only -- rows for shorter instruments still line up, so the whole table
    looks broken/inconsistently spaced rather than obviously wrong. This guarantees
    every row is exactly `width` characters for this field, always.
    """
    if len(text) <= width:
        return f"{text:<{width}}"
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "…"

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

# Same "unavailable vs genuinely empty" distinction as HISTORY_UNAVAILABLE_TEXT/
# NO_TRADES_YET_TEXT, applied to bot_incidents_state's own read surface.
INCIDENTS_UNAVAILABLE_TEXT = "incidents unavailable"
NO_INCIDENTS_TEXT = "no incidents recorded"

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
        f"{stale_marker}{fit(row['bot_id'], BOT_ID_WIDTH)} {pnl_text}  "
        f"{fit(row['symbol'], SYMBOL_WIDTH)} "
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


def previous_range(current: str) -> str:
    """All -> month -> week -> day -> all... (reverse of next_range)."""
    idx = _RANGE_CYCLE.index(current)
    return _RANGE_CYCLE[(idx - 1) % len(_RANGE_CYCLE)]


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


def format_stat(value: float | None, fmt: str = "{:.2f}") -> str:
    """
    "n/a" for an unavailable stat -- not fetched, or genuinely undefined (e.g. a
    zero-variance return series makes Sharpe undefined) -- never a fabricated 0.00
    (mirrors format_win_rate's own "None is not 0.0" convention).
    """
    if value is None:
        return "n/a"
    return fmt.format(value)


def metrics_lines(entry: dict | None) -> list[str]:
    """
    Bot-detail's performance-metrics region: Sharpe, Sortino, Calmar, max drawdown,
    profit factor, expectancy, avg win/loss -- sourced from bots:history's "metrics"
    field, which ml_signals.performance_metrics.all_metrics() computes as the single
    shared implementation (SSOT-02) this function only formats, never recomputes.

    A missing "metrics" key (e.g. a stale cached payload from before this field
    existed) is treated the same as every individual stat being unavailable -- each
    renders "n/a" via format_stat rather than this function special-casing it.
    """
    if entry is None:
        return [HISTORY_UNAVAILABLE_TEXT]
    metrics = entry.get("metrics") or {}
    return [
        (
            f"sharpe  {format_stat(metrics.get('sharpe_ratio')):>7}  "
            f"sortino {format_stat(metrics.get('sortino_ratio')):>7}  "
            f"calmar  {format_stat(metrics.get('calmar_ratio')):>7}  "
            f"max dd  {format_stat(metrics.get('max_drawdown'), '{:.2%}'):>8}"
        ),
        (
            f"profit factor {format_stat(metrics.get('profit_factor')):>7}  "
            f"expectancy {format_stat(metrics.get('expectancy')):>9}  "
            f"avg win {format_stat(metrics.get('avg_win')):>9}  "
            f"avg loss {format_stat(metrics.get('avg_loss')):>9}"
        ),
    ]


def dashboard_bot_url(base_url: str, bot_id: str) -> str:
    """
    `/bot/{bot_id}` (Story 4.7, AC3) -- mirrors coin_detail.dashboard_chart_url()'s
    exact URL-building shape. The web dashboard has no route there yet (checked: its
    route list has nothing bot-shaped) -- that gap pre-dates this story and building
    it is explicitly out of this story's scope (spec's Never section); this function's
    only job is producing the URL bot_tui itself opens.
    """
    return f"{base_url.rstrip('/')}/bot/{bot_id}"


def format_incident_line(incident: dict, now: float) -> str:
    """
    One incidents-log row: timestamp (from incident["started_at"], UNIX seconds --
    live_paper/bot_status.py's own time.time()-based wire contract, distinct from the
    trades blotter's ts_event-derived nanosecond timestamps), a type label, and a
    duration -- "ongoing (Nm..)" while ended_at is still None (an open incident, per
    bot_status._incident_transition), a fixed duration once it closes. "process_start"
    incidents are zero-duration markers (no duration text).
    """
    ts_text = datetime.fromtimestamp(incident["started_at"], tz=UTC).strftime("%m-%d %H:%M:%S")
    if incident["type"] == "process_start":
        return f"{ts_text}  {'restarted':<11}"
    ended_at = incident.get("ended_at")
    if ended_at is None:
        duration_text = f"ongoing ({format_uptime(incident['started_at'], now)})"
    else:
        duration_text = format_uptime(incident["started_at"], ended_at)
    return f"{ts_text}  {'stale feed':<11}{duration_text}"


def incidents_lines(incidents: list[dict] | None, now: float) -> list[str]:
    """
    Bot-detail's incidents-log region: `incidents` is
    bot_incidents_state.get_incidents()'s result for the currently-open bot -- already
    None whenever that read surface is unavailable (never fetched yet), distinct from
    "fetched, genuinely has no incidents". Most-recent-first -- bot_status.py's own
    wire contract appends new incidents oldest-last.
    """
    if incidents is None:
        return [INCIDENTS_UNAVAILABLE_TEXT]
    if not incidents:
        return [NO_INCIDENTS_TEXT]
    return [format_incident_line(inc, now) for inc in reversed(incidents)]
