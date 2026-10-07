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

bots:status is published one message per bot (one bots process = one bot) --
bots_state.py accumulates the latest message per bot_id into a dict; this module turns
that dict into a deterministic, orderable row list and formats individual fields for
display. app.py assembles the final urwid markup itself (coloring only the PnL segment:
"fixed position + color, never color alone").

Story 4.5 adds bot_detail_lines()/format_win_rate_detail() for Bot-detail's live-
snapshot-header region (a different text layout from the Bots-pane row, sharing the
same underlying bots:status row shape and the same format_pnl/format_exposure/
format_uptime helpers -- format_win_rate/format_bot_line, the Bots-pane row's own
already-shipped Story 4.4 formatting, are deliberately left untouched).

Story 4.7 adds the trades-blotter/PnL-sparkline formatters for Bot-detail's other two
regions (bots:history:{bot_id}:{range}, read via bot_history_state.py -- a distinct
wire contract from bots:status, so these take a `dict | None` history entry directly
rather than the `row: dict` shape every function above takes) plus next_range(), the pure
logic behind the range keys app.py wires in.

Story 29.6 adds the `entry`/`sl`/`tp` columns and a column header: `BOTS_COLUMNS` is the one
column-width source, `bot_line_segments` builds a row as (attr, text) segments over it,
`format_bot_line` joins their texts and `bots_header_line` puts each label at its column's
start, so the header, the plain row and app.py's colored row can never drift apart. The price
fields arrive as `str(Price)` strings and are only reformatted as `Decimal`, never `float`.
"""

from datetime import UTC
from datetime import datetime
from decimal import Decimal
from decimal import InvalidOperation


COLD_OPEN_TEXT = "waiting for bots:status…"

# Widths for the Bots-pane row's own genuinely-unbounded-length fields (bot_id is
# operator-chosen in config.toml; symbol grows with the instrument ticker, e.g.
# "RENDER-USD-PERP.DYDX" is 20 chars vs "BTC-USD-PERP.DYDX"'s 17) -- see fit()'s own
# docstring for why plain f-string `:<N` padding alone isn't sufficient here.
BOT_ID_WIDTH = 12
SYMBOL_WIDTH = 20
# format_pnl/format_exposure's own widths.
PNL_WIDTH = 10
EXPOSURE_WIDTH = 10
# A price string has no fixed length ("0.00001234", "105,234.5"); an exit adds its signed
# distance from mark ("-1.8%", "+12.3%").
PRICE_WIDTH = 11
DISTANCE_WIDTH = 6
EXIT_WIDTH = PRICE_WIDTH + 1 + DISTANCE_WIDTH
# "999h59m" and "100%": the widest uptime under 1_000 hours and the widest win rate.
UPTIME_WIDTH = 7
WIN_RATE_WIDTH = 4

# The Bots-pane row, column by column: (header label, width including the gap after it). The one
# source of every column's start, for the rows and for the header.
BOTS_COLUMNS: tuple[tuple[str, int], ...] = (
    ("", 2),  # the stale marker
    ("bot", BOT_ID_WIDTH + 1),
    ("pnl", PNL_WIDTH + 2),
    ("symbol", SYMBOL_WIDTH + 1),
    ("mode", 6),
    ("run", 4),
    ("side", 6),
    ("exposure", EXPOSURE_WIDTH + 2),
    ("entry", PRICE_WIDTH + 2),
    ("sl", EXIT_WIDTH + 2),
    ("tp", EXIT_WIDTH + 2),
    ("up", len("up ") + UPTIME_WIDTH + 2),
    ("wr", len("wr ") + WIN_RATE_WIDTH),
)
# The terminal width one Bots-pane row needs (docs/BOT_OPERATIONS.md states it): narrower, and
# urwid wraps every row onto a second line.
BOTS_PANE_MIN_WIDTH = sum(width for _label, width in BOTS_COLUMNS)

# The attr app.py's palette colors an unprotected position's `sl` cell with.
WARNING_ATTR = "warning"
NOT_AVAILABLE_TEXT = "n/a"


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
    order. bots:status has no ranking concept of its own -- alphabetical bot_id is
    simply the simplest deterministic choice available, not a meaningful order
    preserved from the wire.
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
    data-only, matching this codebase's established voice (platform/CLAUDE.md READ-01),
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
    "n/a" before any position has closed -- never a fabricated 0%.
    """
    if win_rate is None:
        return "n/a"
    return f"{win_rate:.0%}"


type Segment = tuple[str | None, str]


def _decimal(text: str) -> Decimal | None:
    """Return a wire price string as a finite `Decimal`, None when it is not one (never raises)."""
    try:
        value = Decimal(text)
    except (InvalidOperation, TypeError, ValueError):
        return None
    return value if value.is_finite() else None


def format_price(text: str) -> str:
    """
    Return a `str(Price)` with thousands separators at its own precision ("58,900.0"), in
    fixed-point notation however small ("0.0000001234", never "1.234E-7"); `n/a` for a value
    that is not a number, so one malformed message cannot take the pane down.
    """
    value = _decimal(text)
    return format(value, ",f") if value is not None else NOT_AVAILABLE_TEXT


def format_distance(price: str, mark: str | None) -> str:
    """Signed percent distance of `price` from `mark`, e.g. "-1.8%"; blank without a mark."""
    price_value = _decimal(price)
    mark_value = _decimal(mark) if mark is not None else None
    if price_value is None or mark_value is None or mark_value == 0:
        return ""
    distance = (price_value - mark_value) / mark_value * 100
    # One decimal fits DISTANCE_WIDTH up to "+99.9%"; at 100% or more whole percents do, so the
    # cell's digits are never cut.
    return f"{distance:+.1f}%" if abs(distance) < Decimal("99.95") else f"{distance:+.0f}%"


def has_exit_fields(row: dict) -> bool:
    """Return False for a `bots:status` message from a producer that predates Story 29.6."""
    return "stop_loss" in row


def _pad(segments: list[Segment], width: int) -> list[Segment]:
    used = sum(len(text) for _attr, text in segments)
    return [*segments, (None, " " * (width - used))] if used < width else segments


def _entry_cell(row: dict) -> list[Segment]:
    if not has_exit_fields(row):
        return [(None, NOT_AVAILABLE_TEXT)]
    if row["position_side"] == "flat":
        return []
    entry = row["entry_price"]
    text = format_price(entry) if entry is not None else NOT_AVAILABLE_TEXT
    return [(None, fit(text, PRICE_WIDTH))]


def _exit_cell(row: dict, kind: str) -> list[Segment]:
    """
    Return the `sl`/`tp` cell: `n/a` (older producer), blank (flat), `none` (no such order -- in the
    warning attr for a missing stop), `armed` (an order whose price is not set yet, e.g. a
    trailing stop), else the price and its distance from mark.
    """
    if not has_exit_fields(row):
        return [(None, NOT_AVAILABLE_TEXT)]
    if row["position_side"] == "flat":
        return []
    if not row[f"{kind}_orders"]:
        return [(WARNING_ATTR if kind == "stop_loss" else None, "none")]
    price = row[kind]
    if price is None:
        return [(None, "armed")]
    distance = format_distance(price, row["mark_price"])
    return [(None, f"{fit(format_price(price), PRICE_WIDTH)} {fit(distance, DISTANCE_WIDTH)}")]


def bot_line_segments(row: dict, stale: bool, now: float) -> list[Segment]:
    """
    One Bots-pane row as (attr, text) segments, each column exactly its `BOTS_COLUMNS` width.
    Only the PnL (by sign) and an unprotected position's `none` stop carry an attr -- "fixed
    position + color, never color alone": the text always carries the meaning.
    """
    pnl = row["realized_pnl"] + row["unrealized_pnl"]
    cells: list[list[Segment]] = [
        [(None, "~ " if stale else "")],
        [(None, fit(row["bot_id"], BOT_ID_WIDTH))],
        [("pnl-pos" if pnl >= 0 else "pnl-neg", fit(format_pnl(pnl), PNL_WIDTH))],
        [(None, fit(row["symbol"], SYMBOL_WIDTH))],
        [(None, f"{row['mode']:<5}")],
        [(None, "run" if row["running"] else "off")],
        [(None, f"{row['position_side']:<5}")],
        [(None, fit(format_exposure(row["net_exposure"]), EXPOSURE_WIDTH))],
        _entry_cell(row),
        _exit_cell(row, "stop_loss"),
        _exit_cell(row, "take_profit"),
        [(None, f"up {fit(format_uptime(row['started_at'], now), UPTIME_WIDTH)}")],
        [(None, f"wr {fit(format_win_rate(row['win_rate']), WIN_RATE_WIDTH)}")],
    ]
    segments: list[Segment] = []
    for cell, (_label, width) in zip(cells, BOTS_COLUMNS, strict=True):
        segments += _pad(cell, width)
    return segments


def format_bot_line(row: dict, stale: bool, now: float) -> str:
    """
    One full plain-text row -- bot_id, PnL (realized + unrealized), symbol, mode,
    running/stopped, position side, net exposure, entry, stop-loss, take-profit, uptime,
    win-rate (Story 4.4, AC1; Story 29.6). The text of `bot_line_segments`, which app.py
    colors: the two can never disagree.
    """
    return "".join(text for _attr, text in bot_line_segments(row, stale, now))


def bots_header_line() -> str:
    """Return the Bots pane's column header, each label where its column starts in every row."""
    return "".join(fit(label, width) for label, width in BOTS_COLUMNS)


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


def bot_detail_segments(row: dict, now: float) -> list[list[Segment]]:
    """
    Bot-detail's live-snapshot-header region, one list of (attr, text) segments per line,
    matching the UX mockup's two-column field pairing (mockups/key-bot-detail.html):
    line 1 = strategy/symbol + mode, line 2 = PnL + position, line 3 = uptime +
    win-rate (Story 4.5, AC1); line 4 = quantity, entry, mark and open orders, line 5 =
    stop-loss and take-profit with their order counts and the time since the last fill
    (Story 29.6). Only line 2's PnL segment carries an attr, by sign (DW-85: built tagged,
    never found again by searching the rendered text) -- "fixed position + color, never
    color alone", like `bot_line_segments`. `now` is wall clock: `started_at` and
    `last_fill_at` are the wire's timestamps.
    """
    pnl = row["realized_pnl"] + row["unrealized_pnl"]
    uptime_text = format_uptime(row["started_at"], now)
    win_rate_text = format_win_rate_detail(row["win_rate"], row["closed_trades"])
    exposure_text = format_exposure(row["net_exposure"]).strip()
    strategy_line = f"strategy   {row['strategy']} / {row['symbol']}        mode      {row['mode']}"
    position_text = f"position  {row['position_side']} {exposure_text}"
    uptime_line = (
        f"uptime     {uptime_text}                                win rate  {win_rate_text}"
    )
    position_lines: list[list[Segment]] = [
        [(None, line)] for line in _position_detail_lines(row, now)
    ]
    return [
        [(None, strategy_line)],
        [
            (None, "pnl        "),
            ("pnl-pos" if pnl >= 0 else "pnl-neg", format_pnl(pnl)),
            (None, f"                              {position_text}"),
        ],
        [(None, uptime_line)],
        *position_lines,
    ]


def bot_detail_lines(row: dict, now: float) -> list[str]:
    """
    Bot-detail's live-snapshot-header region as plain-text lines: the text of
    `bot_detail_segments`, which app.py colors, so the two can never disagree.
    """
    return ["".join(text for _attr, text in line) for line in bot_detail_segments(row, now)]


def _detail_price(text: str | None) -> str:
    return format_price(text) if text is not None else "-"


def _detail_exit(row: dict, kind: str) -> str:
    if row["position_side"] == "flat":
        return "-"
    count = row[f"{kind}_orders"]
    if not count:
        return "none"
    price = row[kind]
    text = format_price(price) if price is not None else "armed"
    return f"{text} ({count} order{'s' if count != 1 else ''})"


def _position_detail_lines(row: dict, now: float) -> list[str]:
    if not has_exit_fields(row):
        return [
            f"quantity   {NOT_AVAILABLE_TEXT}   entry {NOT_AVAILABLE_TEXT}   "
            f"mark {NOT_AVAILABLE_TEXT}   open orders {NOT_AVAILABLE_TEXT}",
            f"stop loss  {NOT_AVAILABLE_TEXT}   take profit {NOT_AVAILABLE_TEXT}   "
            f"last fill {NOT_AVAILABLE_TEXT}",
        ]
    last_fill_at = row["last_fill_at"]
    last_fill = (
        f"{format_uptime(last_fill_at / 1e9, now)} ago"
        if last_fill_at is not None
        else "no fills yet"
    )
    return [
        f"quantity   {row['position_qty'] or '-'}   entry {_detail_price(row['entry_price'])}   "
        f"mark {_detail_price(row['mark_price'])}   open orders {row['open_orders']}",
        f"stop loss  {_detail_exit(row, 'stop_loss')}   "
        f"take profit {_detail_exit(row, 'take_profit')}   last fill {last_fill}",
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
    field, which kernel.performance_metrics.all_metrics() computes as the single
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


def format_incident_line(incident: dict, now: float) -> str:
    """
    One incidents-log row: timestamp (from incident["started_at"], UNIX seconds --
    bots/domain/bot.py's own time.time()-based wire contract, distinct from the
    trades blotter's ts_event-derived nanosecond timestamps), a type label, and a
    duration -- "ongoing (Nm..)" while ended_at is still None (an open incident, per
    bots.domain.bot.incident_transition), a fixed duration once it closes. "process_start"
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
