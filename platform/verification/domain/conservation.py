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
Conservation accounting (Story 31.2), pure: every reference trade of a UTC day is archived or
explained by a durable record, and every second of the day has a snapshot row or a coverage run.

The reference side never imports the code it checks: the coverage record
(`<catalog>/../coverage/<venue>.jsonl`) and the archive-gap markers (`<catalog>/_archive_gaps/`)
are parsed here from their published line formats (`docs/DATA_DICTIONARY.md`), not through
capture's encoders or `kernel.archive_markers`, and the reference trades are read from the raw
venue frames the recorder stored verbatim.

Invariants:
- a line of either durable file is parsed exactly or refused (`MalformedLine`, naming file:line);
  nothing is skipped or defaulted, because a skipped coverage line would turn an explained gap
  into an unexplained one -- or, worse, a misread span into an explained loss;
- a trade is `unexplained` only when the reference saw it, the archive does not hold it, and no
  `trades_dropped` range, `trades_unrecoverable` window or widened archive-gap marker covers its
  own venue time; a second is `unexplained` only when it has neither a row nor a `seconds` run.

Story 33.1 appends a fifth kind, `liquidations_unrecoverable` (`LiquidationWindow`): parsed as
strictly as the others, and explaining no trade and no second -- only the liquidations tool reads
it (`verification.liquidations`).
"""

import json
from bisect import bisect_right
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import replace
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from kernel.venues import bybit_category

from verification.domain.plan_file import RecordingPlan
from verification.domain.subscriptions import BYBIT
from verification.domain.subscriptions import HYPERLIQUID
from verification.domain.subscriptions import bybit_symbol
from verification.domain.subscriptions import hyperliquid_coin
from verification.domain.subscriptions import rest_polls
from verification.domain.subscriptions import ws_endpoints


NS_PER_MS = 1_000_000
NS_PER_S = 1_000_000_000
SECONDS_PER_HOUR = 3600
NS_PER_HOUR = SECONDS_PER_HOUR * NS_PER_S
HOURS_PER_DAY = 24
SECONDS_PER_DAY = HOURS_PER_DAY * SECONDS_PER_HOUR
EXAMPLES = 5  # unexplained ids/seconds listed per instrument, for diagnosis

# Archive-gap marker spans are `ts_init` (capture stamps them with arrival time), but a trade is
# placed by its own `ts_event`, which precedes its `ts_init` by at most the catalog readers' span
# margin (`kernel.clocks.READ_SPAN_MARGIN_NS`, 60 s: restated, not imported, because
# `kernel.clocks` is code the reference side checks). So a marker explains trades with `ts_event`
# in `[from_ns - 60 s, to_ns]`. Known limit: a REST backfill restamps `ts_init` at admission, so
# a `write_failed` marker of a backfilled batch misses trades whose venue time is further back:
# they count as unexplained (a loud false fail, never a false pass). Upgrade path: markers that
# also carry the batch's `ts_event` span.
GAP_MARKER_MARGIN_NS = 60 * NS_PER_S

SECONDS = "seconds"
TRADES_DROPPED = "trades_dropped"
TRADES_BACKFILLED = "trades_backfilled"
TRADES_UNRECOVERABLE = "trades_unrecoverable"
LIQUIDATIONS_UNRECOVERABLE = "liquidations_unrecoverable"
ARCHIVE_GAP = "archive_gap"
# Its reasons: the liquidation socket was not active, no collector process was running, or the
# received rows' catalog write failed.
LIQUIDATION_REASONS = frozenset({"feed_down", "not_running", "write_failed"})

_COVERAGE_KEYS = MappingProxyType(
    {
        SECONDS: frozenset({"kind", "instrument_id", "reason", "first_s", "last_s", "count"}),
        TRADES_DROPPED: frozenset(
            {"kind", "instrument_id", "reason", "first_ns", "last_ns", "count"}
        ),
        TRADES_BACKFILLED: frozenset({"kind", "instrument_id", "count", "trade_ids"}),
        TRADES_UNRECOVERABLE: frozenset({"kind", "instrument_id", "reason", "from_ns", "to_ns"}),
        LIQUIDATIONS_UNRECOVERABLE: frozenset(
            {"kind", "instrument_id", "reason", "from_ns", "to_ns"}
        ),
    }
)
_GAP_MARKER_KEYS = frozenset({"instrument_id", "from_ns", "to_ns", "reason", "count"})

# The recorder's trade channels (`verification.domain.subscriptions`): Bybit `<category>.publicTrade`
# frames and `<category>.rest.recent-trade` polls, Hyperliquid `trades` frames.
_WS_TRADE_KINDS = ("publicTrade", "trades")
_REST_TRADE_POLL = "recent-trade"


class MalformedLine(ValueError):
    """A durable or raw line that does not have the documented shape: refused, never skipped."""


@dataclass(frozen=True)
class SecondsRun:
    """Seconds `[first_s, last_s]` of one instrument with no row, for one stated reason."""

    instrument_id: str
    reason: str
    first_s: int
    last_s: int


@dataclass(frozen=True)
class TradeWindow:
    """
    Venue-time span `[from_ns, to_ns]` in which missing trades of the instrument are ledgered:
    at most `cap` of them (the count the writer recorded), or any number when `cap` is None.
    """

    instrument_id: str
    source: str
    from_ns: int
    to_ns: int
    cap: int | None


@dataclass(frozen=True)
class Backfilled:
    """Trade ids of one instrument that a REST backfill archived."""

    instrument_id: str
    trade_ids: tuple[str, ...]


@dataclass(frozen=True)
class LiquidationWindow:
    """An inclusive venue-time span `[from_ns, to_ns]` whose liquidations were never received."""

    instrument_id: str
    reason: str
    from_ns: int
    to_ns: int


CoverageEntry = SecondsRun | TradeWindow | Backfilled | LiquidationWindow


def _int(entry: Mapping[str, Any], key: str, where: str) -> int:
    value = entry[key]
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise MalformedLine(f"{where}: `{key}` must be a non-negative integer, got {value!r}")
    return value


def _str(entry: Mapping[str, Any], key: str, where: str) -> str:
    value = entry[key]
    if not isinstance(value, str) or not value:
        raise MalformedLine(f"{where}: `{key}` must be a non-empty string, got {value!r}")
    return value


def _span(entry: Mapping[str, Any], keys: tuple[str, str], where: str) -> tuple[int, int]:
    low, high = _int(entry, keys[0], where), _int(entry, keys[1], where)
    if low > high:
        raise MalformedLine(f"{where}: inverted span {keys[0]} {low} > {keys[1]} {high}")
    return low, high


def _json_object(text: str, where: str) -> dict[str, Any]:
    try:
        entry = json.loads(text)
    except ValueError as exc:
        raise MalformedLine(f"{where}: not JSON: {text[:200]!r}") from exc
    if not isinstance(entry, dict):
        raise MalformedLine(f"{where}: not a JSON object: {text[:200]!r}")
    return entry


def _seconds_run(entry: Mapping[str, Any], where: str) -> SecondsRun:
    first, last = _span(entry, ("first_s", "last_s"), where)
    if _int(entry, "count", where) != last - first + 1:
        raise MalformedLine(f"{where}: `count` {entry['count']} != last_s - first_s + 1")
    iid, reason = _str(entry, "instrument_id", where), _str(entry, "reason", where)
    return SecondsRun(iid, reason, first, last)


def _dropped(entry: Mapping[str, Any], where: str) -> TradeWindow:
    first, last = _span(entry, ("first_ns", "last_ns"), where)
    count = _int(entry, "count", where)
    if count < 1:
        raise MalformedLine(f"{where}: a drop of no trades")
    source = f"{TRADES_DROPPED}:{_str(entry, 'reason', where)}"
    return TradeWindow(_str(entry, "instrument_id", where), source, first, last, count)


def _unrecoverable(entry: Mapping[str, Any], where: str) -> TradeWindow:
    first, last = _span(entry, ("from_ns", "to_ns"), where)
    source = f"{TRADES_UNRECOVERABLE}:{_str(entry, 'reason', where)}"
    # Uncapped: the window is what the venue's REST could not reach, the number lost in it unknown.
    return TradeWindow(_str(entry, "instrument_id", where), source, first, last, None)


def _backfilled(entry: Mapping[str, Any], where: str) -> Backfilled:
    ids = entry["trade_ids"]
    if not isinstance(ids, list) or not all(isinstance(i, str) and i for i in ids):
        raise MalformedLine(f"{where}: `trade_ids` must be a list of non-empty strings")
    if _int(entry, "count", where) != len(ids):
        raise MalformedLine(f"{where}: `count` {entry['count']} != {len(ids)} trade ids")
    return Backfilled(_str(entry, "instrument_id", where), tuple(ids))


def _liquidation_window(entry: Mapping[str, Any], where: str) -> LiquidationWindow:
    first, last = _span(entry, ("from_ns", "to_ns"), where)
    reason = _str(entry, "reason", where)
    if reason not in LIQUIDATION_REASONS:
        raise MalformedLine(f"{where}: unknown liquidation coverage reason {reason!r}")
    return LiquidationWindow(_str(entry, "instrument_id", where), reason, first, last)


_BUILDERS: Mapping[str, Callable[[Mapping[str, Any], str], CoverageEntry]] = MappingProxyType(
    {
        SECONDS: _seconds_run,
        TRADES_DROPPED: _dropped,
        TRADES_BACKFILLED: _backfilled,
        TRADES_UNRECOVERABLE: _unrecoverable,
        LIQUIDATIONS_UNRECOVERABLE: _liquidation_window,
    }
)


def parse_coverage_line(text: str, where: str) -> CoverageEntry:
    """Parse one coverage line (`where` is its `file:line`); `MalformedLine` for anything else."""
    entry = _json_object(text, where)
    kind = entry.get("kind")
    keys = _COVERAGE_KEYS.get(kind) if isinstance(kind, str) else None
    if keys is None:
        raise MalformedLine(f"{where}: unknown coverage kind {kind!r}")
    if set(entry) != keys:
        raise MalformedLine(f"{where}: {kind} keys {sorted(entry)} != {sorted(keys)}")
    return _BUILDERS[str(kind)](entry, where)


def parse_gap_marker_span(text: str, where: str) -> TradeWindow:
    """
    Parse one archive-gap marker line into its own `ts_init` span, unwidened, capped at its
    `count` (0, a `quarantined` file whose lost rows are unknown, is uncapped): the span the
    nightly rebuild keeps live rows in (`docs/DATA_DICTIONARY.md` section 6).
    """
    entry = _json_object(text, where)
    if set(entry) != _GAP_MARKER_KEYS:
        raise MalformedLine(f"{where}: marker keys {sorted(entry)} != {sorted(_GAP_MARKER_KEYS)}")
    first, last = _span(entry, ("from_ns", "to_ns"), where)
    cap = _int(entry, "count", where) or None
    source = f"{ARCHIVE_GAP}:{_str(entry, 'reason', where)}"
    return TradeWindow(_str(entry, "instrument_id", where), source, first, last, cap)


def parse_gap_marker_line(text: str, where: str) -> TradeWindow:
    """
    Parse one archive-gap marker line into the venue-time window it explains: its `ts_init` span
    widened below by `GAP_MARKER_MARGIN_NS`, capped at its `count`.
    """
    span = parse_gap_marker_span(text, where)
    return replace(span, from_ns=max(0, span.from_ns - GAP_MARKER_MARGIN_NS))


@dataclass(frozen=True)
class Intervals:
    """
    Closed nanosecond spans, merged. Invariant: `starts` ascend and every span ends before the
    next one starts, so `contains` is one bisect; only `of` builds one (it sorts and merges).
    """

    starts: tuple[int, ...]
    ends: tuple[int, ...]

    @classmethod
    def of(cls, spans: Iterable[tuple[int, int]]) -> "Intervals":
        merged: list[list[int]] = []
        for low, high in sorted(spans):
            if merged and low <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], high)
            else:
                merged.append([low, high])
        return cls(tuple(s for s, _ in merged), tuple(e for _, e in merged))

    def contains(self, value: int) -> bool:
        index = bisect_right(self.starts, value) - 1
        return index >= 0 and value <= self.ends[index]


@dataclass(frozen=True)
class Explanations:
    """
    What can explain a missing trade, and how much of it is spent. Invariant: an uncapped span
    explains any number of trades; capped window `i` explains at most its `cap` trades over the
    whole instrument-day -- `used[i]` counts those already explained, and only `explain` advances
    it (returning a new value, so a count is never spent twice). A trade inside an uncapped span
    never spends a capped window's budget.

    Known limit: `trades_unrecoverable` windows and count-0 (`quarantined`) archive-gap markers
    carry no count, so they explain every missing trade inside them -- a loss there beyond what
    the venue's depth or the quarantined file really held is not caught. Upgrade path: capture
    records the count it could not recover (the venue's depth tells how many were requested), and
    a quarantine counts the rows of the file it moves aside.
    """

    uncapped: Intervals
    capped: tuple[TradeWindow, ...]
    used: tuple[int, ...]

    @classmethod
    def of(cls, windows: Iterable[TradeWindow]) -> "Explanations":
        windows = tuple(windows)
        capped = tuple(sorted((w for w in windows if w.cap is not None), key=_window_order))
        spans = ((w.from_ns, w.to_ns) for w in windows if w.cap is None)
        return cls(Intervals.of(spans), capped, (0,) * len(capped))

    def _budget_for(self, ts_ns: int, used: list[int]) -> int | None:
        """
        Return the covering window with room that ends first. Trades come in time order, so a
        window ending sooner can serve fewer of the trades still to come: spending it first never
        leaves a trade unexplained that some assignment would explain (earliest-deadline-first).
        """
        best: int | None = None
        for index, window in enumerate(self.capped):
            has_room = window.cap is not None and used[index] < window.cap
            if not has_room or not window.from_ns <= ts_ns <= window.to_ns:
                continue
            if best is None or window.to_ns < self.capped[best].to_ns:
                best = index
        return best

    def explain(self, missing: Sequence[tuple[str, int]]) -> tuple[list[str], "Explanations"]:
        """Explain `(id, venue time)` trades in time order; return the unexplained ids."""
        used = list(self.used)
        unexplained = []
        for trade_id, ts_ns in sorted(missing, key=lambda trade: (trade[1], trade[0])):
            if self.uncapped.contains(ts_ns):
                continue
            index = self._budget_for(ts_ns, used)
            if index is None:
                unexplained.append(trade_id)
            else:
                used[index] += 1
        return sorted(unexplained), replace(self, used=tuple(used))


def _window_order(window: TradeWindow) -> tuple[int, int, str]:
    return (window.from_ns, window.to_ns, window.source)


@dataclass(frozen=True)
class TradeChannel:
    """A raw channel holding reference trades: its name, category (Bybit) and whether REST."""

    name: str
    category: str
    rest: bool


# Aggressor sides as Nautilus's `AggressorSide` enum values, restated (the reference side never
# imports `nautilus_trader`): the archive's `aggressor_side` column stores exactly these.
NO_AGGRESSOR = 0
BUYER = 1
SELLER = 2

# Each venue's documented side tokens (Bybit `S`/`side`: "Buy"/"Sell"; Hyperliquid `side`: "B"
# bid/buyer, "A" ask/seller). Any other token -- Bybit's adapter maps "" to NoAggressor -- is
# NO_AGGRESSOR, kept with its token so the report can show what the wire carried.
_SIDE_TOKENS: Mapping[str, Mapping[str, int]] = MappingProxyType(
    {
        BYBIT: MappingProxyType({"Buy": BUYER, "Sell": SELLER}),
        HYPERLIQUID: MappingProxyType({"B": BUYER, "A": SELLER}),
    }
)


@dataclass(frozen=True)
class ReferenceTrade:
    """
    One trade the venue published: its instrument, id, own venue time and source, and its values
    exactly as the wire's decimal strings (`Decimal`, never `float`). `side` is BUYER / SELLER /
    NO_AGGRESSOR, `side_token` the wire's own token. `order` is the fold's tie-break within one
    venue time: `(ts_ns, recv_ns of the line, chronological position in the line)`.
    """

    instrument_id: str
    trade_id: str
    ts_ns: int
    via_rest: bool
    price: Decimal
    size: Decimal
    side: int
    side_token: str
    order: tuple[int, ...]


def instrument_category(venue: str, instrument_id: str) -> str:
    """Return an instrument's raw-channel category: Bybit's `linear`/`spot`, else empty."""
    return bybit_category(instrument_id) if venue == BYBIT else ""


def _channel_category(venue: str, channel: str) -> str:
    """Bybit channels are `<category>.<kind>`; Hyperliquid's carry no category."""
    return channel.partition(".")[0] if venue == BYBIT else ""


def trade_channels(plan: RecordingPlan) -> tuple[TradeChannel, ...]:
    """Return the plan's trade channels, from the recorder's own subscription tables."""
    ws = [
        channel
        for endpoint in ws_endpoints(plan, lambda _name: "")
        for channel in endpoint.data_channels
        if channel.rpartition(".")[2] in _WS_TRADE_KINDS
    ]
    rest = [poll.channel for poll in rest_polls(plan) if poll.name == _REST_TRADE_POLL]
    return tuple(
        [
            TradeChannel(name, _channel_category(plan.venue, name), False)
            for name in dict.fromkeys(ws)
        ]
        + [
            TradeChannel(name, _channel_category(plan.venue, name), True)
            for name in dict.fromkeys(rest)
        ]
    )


def wire_index(plan: RecordingPlan) -> Mapping[tuple[str, str], str]:
    """`(category, wire symbol or coin)` -> instrument id, for every plan id."""
    if plan.venue == BYBIT:
        return {(bybit_category(i), bybit_symbol(i)): i for i in plan.instruments}
    return {("", hyperliquid_coin(i)): i for i in plan.instruments}


def _field(item: Mapping[str, Any], key: str, kind: type, where: str) -> Any:
    value = item.get(key)
    if not isinstance(value, kind) or isinstance(value, bool):
        raise MalformedLine(f"{where}: trade field `{key}` is {value!r}, not {kind.__name__}")
    return value


def _ms_digits(value: str, where: str) -> int:
    if not (value.isascii() and value.isdigit()):
        raise MalformedLine(f"{where}: trade time {value!r} is not whole milliseconds")
    return int(value)


@dataclass(frozen=True)
class _WireTrade:
    """One trade item as the wire spelled it."""

    wire: str
    trade_id: str
    ms: int
    price: Decimal
    size: Decimal
    side_token: str


def _digits(text: str) -> bool:
    return text.isascii() and text.isdigit()


def _decimal(item: Mapping[str, Any], key: str, where: str) -> Decimal:
    """
    Parse a wire price or size: ASCII digits with an optional fraction, nothing else `Decimal` would
    also take (exponents, signs, `NaN`, `Infinity`, underscores, whitespace).
    """
    value = _field(item, key, str, where)
    whole, dot, fraction = value.partition(".")
    if not (_digits(whole) and (not dot or _digits(fraction))):
        raise MalformedLine(f"{where}: trade field `{key}` {value!r} is not a decimal string")
    return Decimal(value)


def _bybit_ws(item: Mapping[str, Any], where: str) -> _WireTrade:
    symbol, trade_id = _field(item, "s", str, where), _field(item, "i", str, where)
    price, size = _decimal(item, "p", where), _decimal(item, "v", where)
    side = _field(item, "S", str, where)
    return _WireTrade(symbol, trade_id, _field(item, "T", int, where), price, size, side)


def _bybit_rest(item: Mapping[str, Any], where: str) -> _WireTrade:
    symbol, trade_id = _field(item, "symbol", str, where), _field(item, "execId", str, where)
    ms = _ms_digits(_field(item, "time", str, where), where)
    price, size = _decimal(item, "price", where), _decimal(item, "size", where)
    return _WireTrade(symbol, trade_id, ms, price, size, _field(item, "side", str, where))


def _hyperliquid_ws(item: Mapping[str, Any], where: str) -> _WireTrade:
    tid = _field(item, "tid", int, where)  # capture archives `str(tid)`
    price, size = _decimal(item, "px", where), _decimal(item, "sz", where)
    coin, ms = _field(item, "coin", str, where), _field(item, "time", int, where)
    return _WireTrade(coin, str(tid), ms, price, size, _field(item, "side", str, where))


def _items(payload: Any, path: tuple[str, ...], where: str) -> list[Any]:
    for key in path:
        payload = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise MalformedLine(f"{where}: `{'.'.join(path)}` is not a list of trade objects")
    return payload


def _payload(record: Mapping[str, object], where: str) -> Any:
    raw = record.get("raw")
    if not isinstance(raw, str):
        raise MalformedLine(f"{where}: a trade line without a text `raw`")
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise MalformedLine(f"{where}: `raw` is not JSON") from exc


def _wanted_kind(record: Mapping[str, object], channel: TradeChannel, where: str) -> bool:
    """Whether the line carries trades: a frame (WS) or a good poll (REST); connection lines no."""
    kind = record.get("kind")
    if kind == "connection":
        return False
    if kind != ("rest" if channel.rest else "frame"):
        raise MalformedLine(f"{where}: unexpected line kind {kind!r} in {channel.name}")
    # A failed or refused poll carries no trades; the recorder ledgered it (`verification.recorder
    # .rest`), and its line keeps the `status`/`refusal` that say so.
    return not channel.rest or (record.get("status") == 200 and "refusal" not in record)


def _recv_ns(record: Mapping[str, object], where: str) -> int:
    value = record.get("recv_ns")
    if not isinstance(value, int) or isinstance(value, bool):
        raise MalformedLine(f"{where}: a trade line without an integer `recv_ns`")
    return value


def _parsed(venue: str, channel: TradeChannel, payload: Any, where: str) -> list[_WireTrade]:
    """Return the line's trades in time order (a Bybit `recent-trade` list is newest first)."""
    if venue != BYBIT:
        return [_hyperliquid_ws(item, where) for item in _items(payload, ("data",), where)]
    if channel.rest:
        items = _items(payload, ("result", "list"), where)
        return [_bybit_rest(item, where) for item in reversed(items)]
    return [_bybit_ws(item, where) for item in _items(payload, ("data",), where)]


def reference_trades(
    venue: str,
    channel: TradeChannel,
    record: Mapping[str, object],
    index: Mapping[tuple[str, str], str],
) -> list[ReferenceTrade]:
    """
    Return the plan's trades in one raw line (`MalformedLine` for a trade line of another shape).
    Trades of instruments outside the plan are not the report's subject and are left out.
    """
    if not isinstance(record, Mapping):  # `json.loads` of a line can be any JSON value
        raise MalformedLine(
            f"{channel.name}: a raw line that is not a JSON object: {record!r:.200}"
        )
    where = f"{channel.name} line recv_ns={record.get('recv_ns')}"
    if not _wanted_kind(record, channel, where):
        return []
    recv_ns = _recv_ns(record, where)
    sides = _SIDE_TOKENS[venue]
    return [
        ReferenceTrade(
            instrument_id=index[(channel.category, trade.wire)],
            trade_id=trade.trade_id,
            ts_ns=trade.ms * NS_PER_MS,
            via_rest=channel.rest,
            price=trade.price,
            size=trade.size,
            side=sides.get(trade.side_token, NO_AGGRESSOR),
            side_token=trade.side_token,
            order=(trade.ms * NS_PER_MS, recv_ns, position),
        )
        for position, trade in enumerate(_parsed(venue, channel, _payload(record, where), where))
        if (channel.category, trade.wire) in index
    ]


@dataclass(frozen=True)
class TradeCounts:
    """
    One instrument's trade conservation. `seen` = WS + REST reference ids with venue time in the
    day (`rest_only` of them seen only by REST); `archived` = seen ∩ archive; `backfilled` =
    archived ∩ coverage backfill ids; `ledgered_unrecoverable` + `unexplained` = seen - archived.
    """

    seen: int = 0
    rest_only: int = 0
    archived: int = 0
    backfilled: int = 0
    ledgered_unrecoverable: int = 0
    unexplained: int = 0
    archived_not_seen: int = 0
    archived_twice: int = 0
    examples_unexplained: tuple[str, ...] = ()

    def plus(self, other: "TradeCounts") -> "TradeCounts":
        """Sum two partitions (hours) of the same instrument-day."""
        examples = (self.examples_unexplained + other.examples_unexplained)[:EXAMPLES]
        sums = {name: getattr(self, name) + getattr(other, name) for name in _TRADE_SUMS}
        return replace(self, **sums, examples_unexplained=examples)


_TRADE_SUMS = (
    "seen",
    "rest_only",
    "archived",
    "backfilled",
    "ledgered_unrecoverable",
    "unexplained",
    "archived_not_seen",
    "archived_twice",
)


@dataclass(frozen=True)
class ReferenceHour:
    """One hour's reference ids (venue time in the hour) with their times, and the WS-seen ones."""

    times: Mapping[str, int]
    ws_ids: frozenset[str]


def tally_trades(
    reference: ReferenceHour,
    archived: Mapping[str, int],
    backfilled: frozenset[str],
    explanations: Explanations,
) -> tuple[TradeCounts, Explanations]:
    """
    Account one hour: the reference and archived ids whose own venue time lies in it. Returns the
    explanations with this hour's spending, for the next hour.
    """
    seen = reference.times.keys()
    kept = seen & archived.keys()
    missing = [(trade_id, reference.times[trade_id]) for trade_id in seen - kept]
    unexplained, explanations = explanations.explain(missing)
    counts = TradeCounts(
        seen=len(seen),
        rest_only=len(seen - reference.ws_ids),
        archived=len(kept),
        backfilled=len(kept & backfilled),
        ledgered_unrecoverable=len(missing) - len(unexplained),
        unexplained=len(unexplained),
        archived_not_seen=len(archived.keys() - seen),
        examples_unexplained=tuple(unexplained[:EXAMPLES]),
    )
    return counts, explanations


@dataclass(frozen=True)
class SecondCounts:
    """
    One instrument's second conservation. `rows` counts seconds holding at least one row, so
    `rows + sum(explained_by_reason) + unexplained == expected`. `multiple_reasons` counts seconds
    two runs cover (legitimate across a restart; the earliest-starting run explains the second).
    """

    expected: int
    rows: int
    explained_by_reason: Mapping[str, int]
    unexplained: int
    duplicate_rows: int
    row_and_reason: int
    multiple_reasons: int
    examples_unexplained: tuple[int, ...]


def reasons(
    day_start_s: int, runs: Sequence[SecondsRun], end_s: int | None = None
) -> tuple[dict[int, str], int]:
    """
    Map every second of `[day_start_s, end_s)` (the day from `day_start_s` when `end_s` is None) a
    `seconds` run covers to its reason (the earliest-starting run's, ties by reason), and count
    the seconds two runs cover. A run is clipped to the span: its seconds outside it are not
    counted.
    """
    last_s = (day_start_s + SECONDS_PER_DAY if end_s is None else end_s) - 1
    found: dict[int, str] = {}
    doubled: set[int] = set()
    for run in sorted(runs, key=lambda r: (r.first_s, r.reason)):
        low = max(run.first_s, day_start_s)
        high = min(run.last_s, last_s)
        for second in range(low, high + 1):
            if second in found:
                doubled.add(second)
            else:
                found[second] = run.reason
    return found, len(doubled)


def tally_seconds(
    day_start_s: int,
    rows: Mapping[int, int],
    runs: Sequence[SecondsRun],
    end_s: int | None = None,
) -> SecondCounts:
    """
    Account every second of `[day_start_s, end_s)` (the day from `day_start_s` when `end_s` is
    None, Story 31.10's window otherwise): `rows` maps a second to its row count.
    """
    stop_s = day_start_s + SECONDS_PER_DAY if end_s is None else end_s
    by_second, multiple = reasons(day_start_s, runs, stop_s)
    explained: dict[str, int] = {}
    with_row = duplicate = both = 0
    unexplained: list[int] = []
    for second in range(day_start_s, stop_s):
        count, reason = rows.get(second, 0), by_second.get(second)
        if count:
            with_row, duplicate = with_row + 1, duplicate + (count > 1)
            both += reason is not None
        elif reason is not None:
            explained[reason] = explained.get(reason, 0) + 1
        else:
            unexplained.append(second)
    return SecondCounts(
        expected=stop_s - day_start_s,
        rows=with_row,
        explained_by_reason=dict(sorted(explained.items())),
        unexplained=len(unexplained),
        duplicate_rows=duplicate,
        row_and_reason=both,
        multiple_reasons=multiple,
        examples_unexplained=tuple(unexplained[:EXAMPLES]),
    )


@dataclass(frozen=True)
class InstrumentReport:
    """
    One instrument's day: it passes only with nothing unexplained, twice or doubled -- `failing`,
    the sum of those five counts, is 0.
    """

    instrument_id: str
    trades: TradeCounts
    seconds: SecondCounts

    @property
    def failing(self) -> int:
        return (
            self.trades.unexplained
            + self.trades.archived_twice
            + self.seconds.unexplained
            + self.seconds.duplicate_rows
            + self.seconds.row_and_reason
        )

    @property
    def passed(self) -> bool:
        return self.failing == 0


@dataclass(frozen=True)
class DayReport:
    """
    A venue's day -- or a window of whole seconds `[start, end)`, labelled `day` too (Story
    31.10) -- every plan instrument's report, and what the inputs lacked. It passes only when
    every instrument does, the coverage record exists (without it nothing is explained) and no
    raw reference hour of the day itself is missing (a missing hour makes the trade side vacuous).
    The neighbour hours outside the day may be missing, or end truncated
    (`truncated_neighbour_files`): they only widen what is seen.
    """

    venue: str
    day: str
    coverage_file: str
    coverage_present: bool
    missing_raw_files: tuple[str, ...]
    truncated_neighbour_files: tuple[str, ...]
    instruments: tuple[InstrumentReport, ...]
    start: str | None = None  # a window's own bounds (Story 31.10); None for a whole day
    end: str | None = None

    @property
    def passed(self) -> bool:
        inputs_whole = self.coverage_present and not self.missing_raw_files
        return inputs_whole and all(report.passed for report in self.instruments)
