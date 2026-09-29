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
The stored book proven against an independently rebuilt book (Story 31.5), pure: the book frame
parsers, the reference book, the close rule, the REST agreement and the second classes. No I/O,
no clock.

The reference side never imports the code it checks: the book is rebuilt here in `Decimal` from
the venue's own frames (the recorder's verbatim `orderbook.50` / `l2Book` lines, `docs/
DATA_DICTIONARY.md` section 1.15), not through `capture`'s `LiveBook` or Nautilus's `OrderBook`,
and the stored rows arrive as plain integers decoded by this context's own reader.

Invariants:
- exact: every price and size is the wire's decimal string as a `Decimal` (anything else is a
  `MalformedLine`), turned into units only by `units` (`Decimal.scaleb`, `Inexact` trapped); no
  book value is ever compared within a tolerance;
- a book is judged only while it is available: after a baseline (a Bybit `snapshot`, any
  Hyperliquid `l2Book`) and until a `u` break or a connection line of its endpoint;
- a failing count is never folded into a passing class: `passed` is false while any is non-zero,
  the reference is invalid or unvalidated, or nothing was verified.
"""

import heapq
import json
from collections import Counter
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from operator import itemgetter
from types import MappingProxyType
from typing import Any
from urllib.parse import parse_qs
from urllib.parse import urlsplit

from verification.domain.conservation import EXAMPLES
from verification.domain.conservation import NS_PER_MS
from verification.domain.conservation import NS_PER_S
from verification.domain.conservation import MalformedLine
from verification.domain.conservation import TradeChannel
from verification.domain.plan_file import RecordingPlan
from verification.domain.subscriptions import BYBIT
from verification.domain.subscriptions import rest_polls
from verification.domain.subscriptions import ws_endpoints
from verification.domain.trade_check import OffGrid
from verification.domain.trade_check import units


# The stored snapshot keeps the top 20 levels per side (`docs/DATA_DICTIONARY.md` section 1.7).
DEPTH = 20

# How close before a row was sampled (`ts_init`) the recorder may have received the message the
# row lacks, for the row to be `boundary_late` rather than `boundary_unexplained`. The margin bounds
# only the difference between the two connections' receipts of the same message -- the recorder's
# `recv_ns` against capture's receipt, which `ts_init` stands in for -- and nothing else: a message
# capture received more than `hold_back_seconds` before the second closed (0.5 s on Bybit) it must
# have applied, so a row lacking it is a defect. Measured maximum of that difference: |capture
# `ts_init` - recorder `recv_ns`| <= 394 ms over ~800k trades (Story 31.4's latency, soak
# 2026-09-29 13:00-15:00Z, `docs/VERIFICATION_REPORT.md`); 500 ms covers it and stays within
# Bybit's hold-back. Known limit: a receipt difference above 500 ms (a lagging capture connection)
# reads as `boundary_unexplained`, a loud false fail, never a false pass. Upgrade path: capture
# records each book message's receipt, so the comparison needs no margin at all.
LATE_ARRIVAL_MARGIN_NS = NS_PER_S // 2

BIDS = "bids"
ASKS = "asks"
SIDES = (BIDS, ASKS)

Level = tuple[Decimal, Decimal]  # (price, size)
UnitLevel = tuple[int, int]  # (price units, size units)
# What a message changed: the (side, price) pairs of a delta, or None for "everything" (a baseline).
Touched = frozenset[tuple[str, Decimal]] | None

_BYBIT_BOOK = "orderbook"
_HYPERLIQUID_BOOK = "l2Book"


def book_channels(plan: RecordingPlan) -> tuple[TradeChannel, ...]:
    """
    Return the plan's book channels, from the recorder's own subscription tables: Bybit
    `<category>.orderbook.50` frames and `<category>.rest.orderbook` polls, Hyperliquid `l2Book`
    frames and `rest.l2Book` polls (`TradeChannel`'s shape: a name, its category, whether REST).
    """
    category: Callable[[str], str] = (
        (lambda name: name.partition(".")[0]) if plan.venue == BYBIT else (lambda _name: "")
    )
    ws = [
        name
        for endpoint in ws_endpoints(plan, lambda _name: "")
        for name in endpoint.data_channels
        if name == _HYPERLIQUID_BOOK or name.partition(".")[2].startswith(f"{_BYBIT_BOOK}.")
    ]
    rest = [p.channel for p in rest_polls(plan) if p.name in (_BYBIT_BOOK, _HYPERLIQUID_BOOK)]
    return tuple(
        [TradeChannel(name, category(name), False) for name in dict.fromkeys(ws)]
        + [TradeChannel(name, category(name), True) for name in dict.fromkeys(rest)]
    )


# --- the wire ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BookMessage:
    """
    One book frame of one instrument: its venue time (ns), the recorder's receipt, whether it is a
    baseline (Bybit `type` "snapshot"; every Hyperliquid `l2Book`), Bybit's `u`, the alignment key
    REST polls are placed by (Bybit `seq`, Hyperliquid venue time) and the levels as the wire gave
    them (a size of 0 deletes).
    """

    venue_ns: int
    recv_ns: int
    baseline: bool
    u: int | None
    key: int
    bids: tuple[Level, ...]
    asks: tuple[Level, ...]


@dataclass(frozen=True)
class RestBook:
    """One REST book poll: its alignment key and levels (best first); `ok` false when failed."""

    ok: bool
    key: int
    recv_ns: int
    bids: tuple[Level, ...] = ()
    asks: tuple[Level, ...] = ()


def _digits(text: str) -> bool:
    return text.isascii() and text.isdigit()


def wire_decimal(value: object, where: str) -> Decimal:
    """
    Parse a wire price or size: a string of ASCII digits with an optional fraction, nothing else
    `Decimal` would also take (exponents, signs, `NaN`, whitespace); `MalformedLine` otherwise.
    """
    if not isinstance(value, str):
        raise MalformedLine(f"{where}: book value {value!r} is not a string")
    whole, dot, fraction = value.partition(".")
    if not (_digits(whole) and (not dot or _digits(fraction))):
        raise MalformedLine(f"{where}: book value {value!r} is not a decimal string")
    return Decimal(value)


def _int(payload: Mapping[str, Any], key: str, where: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise MalformedLine(f"{where}: book field `{key}` is {value!r}, not an integer")
    return value


def _object(value: object, what: str, where: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise MalformedLine(f"{where}: {what} is not a JSON object: {value!r:.200}")
    return value


def _list(value: object, what: str, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise MalformedLine(f"{where}: {what} is not a list: {value!r:.200}")
    return value


def _pairs(items: object, what: str, where: str) -> tuple[Level, ...]:
    """Bybit levels: `[[price, size], ...]`."""
    levels = []
    for item in _list(items, what, where):
        if not isinstance(item, list) or len(item) != 2:
            raise MalformedLine(f"{where}: a {what} level {item!r:.100} is not [price, size]")
        levels.append((wire_decimal(item[0], where), wire_decimal(item[1], where)))
    return tuple(levels)


def _px_sz(items: object, what: str, where: str) -> tuple[Level, ...]:
    """Hyperliquid levels: `[{"px", "sz", "n"}, ...]`."""
    levels = []
    for item in _list(items, what, where):
        level = _object(item, f"a {what} level", where)
        levels.append((wire_decimal(level.get("px"), where), wire_decimal(level.get("sz"), where)))
    return tuple(levels)


def payload_of(record: Mapping[str, object], where: str) -> Mapping[str, Any]:
    """Return the line's `raw` venue text, parsed as a JSON object."""
    raw = record.get("raw")
    if not isinstance(raw, str):
        raise MalformedLine(f"{where}: a book line without a text `raw`")
    try:
        return _object(json.loads(raw), "`raw`", where)
    except ValueError as exc:
        raise MalformedLine(f"{where}: `raw` is not JSON") from exc


def recv_ns_of(record: Mapping[str, object], where: str) -> int:
    value = record.get("recv_ns")
    if not isinstance(value, int) or isinstance(value, bool):
        raise MalformedLine(f"{where}: a book line without an integer `recv_ns`")
    return value


def bybit_frame(record: Mapping[str, object], where: str) -> tuple[str, BookMessage]:
    """
    Parse one Bybit `orderbook.<depth>.<symbol>` frame: `(symbol, message)`. A frame without `ts`,
    `data.u`, `data.seq` or a `type` of "snapshot"/"delta" is a `MalformedLine` (the story's Block
    If: the measured shape is what the replay relies on).
    """
    payload = payload_of(record, where)
    data = _object(payload.get("data"), "`data`", where)
    kind = payload.get("type")
    if kind not in ("snapshot", "delta"):
        raise MalformedLine(f"{where}: book frame type {kind!r}")
    symbol = data.get("s")
    topic = payload.get("topic")
    if not isinstance(topic, str) or topic.rpartition(".")[2] != symbol:
        raise MalformedLine(f"{where}: book frame symbol {symbol!r} is not its topic's {topic!r}")
    message = BookMessage(
        venue_ns=_int(payload, "ts", where) * NS_PER_MS,
        recv_ns=recv_ns_of(record, where),
        baseline=kind == "snapshot",
        u=_int(data, "u", where),
        key=_int(data, "seq", where),
        bids=_pairs(data.get("b"), "bid", where),
        asks=_pairs(data.get("a"), "ask", where),
    )
    return str(symbol), message


def hyperliquid_frame(record: Mapping[str, object], where: str) -> tuple[str, BookMessage]:
    """Parse one Hyperliquid `l2Book` frame: `(coin, message)`; every one is the whole book."""
    data = _object(payload_of(record, where).get("data"), "`data`", where)
    coin, venue_ns, bids, asks = _hyperliquid_book(data, where)
    message = BookMessage(venue_ns, recv_ns_of(record, where), True, None, venue_ns, bids, asks)
    return coin, message


def _hyperliquid_book(
    body: Mapping[str, Any], where: str
) -> tuple[str, int, tuple[Level, ...], tuple[Level, ...]]:
    coin = body.get("coin")
    if not isinstance(coin, str):
        raise MalformedLine(f"{where}: l2Book coin {coin!r}")
    levels = _list(body.get("levels"), "`levels`", where)
    if len(levels) != 2:
        raise MalformedLine(f"{where}: l2Book `levels` has {len(levels)} sides, not 2")
    time_ns = _int(body, "time", where) * NS_PER_MS
    return coin, time_ns, _px_sz(levels[0], "bid", where), _px_sz(levels[1], "ask", where)


def _failed(record: Mapping[str, object]) -> bool:
    return record.get("status") != 200 or "refusal" in record


def bybit_rest(record: Mapping[str, object], where: str) -> tuple[str, RestBook]:
    """
    Parse one Bybit `/v5/market/orderbook` poll: `(the requested symbol, poll)`, keyed by
    `result.seq` -- the counter every WS frame carries as `data.seq`. REST `u` is a different
    counter from WS `u` (the soak: ~29M against ~192M), so it is never used.
    """
    request = record.get("request")
    symbols = parse_qs(urlsplit(request).query).get("symbol") if isinstance(request, str) else None
    if not symbols:
        raise MalformedLine(f"{where}: an orderbook poll without a `symbol` in {request!r}")
    recv_ns = recv_ns_of(record, where)
    payload = {} if _failed(record) else payload_of(record, where)
    if payload.get("retCode") != 0:
        return symbols[0], RestBook(False, 0, recv_ns)
    result = _object(payload.get("result"), "`result`", where)
    if result.get("s") != symbols[0]:
        raise MalformedLine(f"{where}: poll for {symbols[0]} served {result.get('s')!r}")
    bids, asks = _pairs(result.get("b"), "bid", where), _pairs(result.get("a"), "ask", where)
    return symbols[0], RestBook(True, _int(result, "seq", where), recv_ns, bids, asks)


def hyperliquid_rest(record: Mapping[str, object], where: str) -> tuple[str, RestBook]:
    """Parse one Hyperliquid `l2Book` poll: `(the requested coin, poll)`, keyed by its `time`."""
    request = record.get("request")
    try:
        coin = json.loads(request).get("coin") if isinstance(request, str) else None
    except (ValueError, AttributeError) as exc:
        raise MalformedLine(f"{where}: an l2Book poll request {request!r}") from exc
    if not isinstance(coin, str):
        raise MalformedLine(f"{where}: an l2Book poll without a `coin` in {request!r}")
    recv_ns = recv_ns_of(record, where)
    if _failed(record):
        return coin, RestBook(False, 0, recv_ns)
    served, time_ns, bids, asks = _hyperliquid_book(payload_of(record, where), where)
    if served != coin:
        raise MalformedLine(f"{where}: poll for {coin} served {served!r}")
    return coin, RestBook(True, time_ns, recv_ns, bids, asks)


# --- the reference book --------------------------------------------------------------------------


@dataclass(frozen=True)
class BookTop:
    """The best `DEPTH` levels per side, best first, in `Decimal`."""

    bids: tuple[Level, ...]
    asks: tuple[Level, ...]


@dataclass(frozen=True)
class _Undo:
    """
    How to revert the last applied message: whether the book was available before it, the replaced
    sides of a baseline (the old dicts themselves, never a copy), or a delta's previous size of
    each price it touched (None: the price was absent), and the message's receipt.
    """

    available: bool
    sides: Mapping[str, Mapping[Decimal, Decimal]] | None
    levels: Mapping[str, Mapping[Decimal, Decimal | None]]
    recv_ns: int


def _best(
    levels: Mapping[Decimal, Decimal],
    overrides: Mapping[Decimal, Decimal | None],
    side: str,
    depth: int,
) -> tuple[Level, ...]:
    """Return the best `depth` levels of `levels`, `overrides` laid over it (None: removed)."""
    kept = ((p, s) for p, s in levels.items() if p not in overrides)
    over = ((p, s) for p, s in overrides.items() if s is not None)
    pick = heapq.nlargest if side == BIDS else heapq.nsmallest
    return tuple(pick(depth, (*kept, *over), key=itemgetter(0)))


def _sides_of(message: BookMessage) -> dict[str, dict[Decimal, Decimal]]:
    """Return a baseline's two sides (a level of size 0 is absent)."""
    return {
        BIDS: {p: s for p, s in message.bids if s},
        ASKS: {p: s for p, s in message.asks if s},
    }


class ReferenceBook:
    """
    One instrument's reference book, rebuilt from its raw frames in arrival order.

    Invariants: a Bybit `snapshot` (every Hyperliquid message) re-baselines the book; a Bybit delta
    applies only when its `u` is the last `u` + 1 (a zero-level delta advances `u` too), otherwise
    the book is unavailable until the next snapshot (`u_breaks`); a connection line makes it
    unavailable until the next baseline (Hyperliquid: until the message after the
    subscribe reply, which reaches the recorder's connection only). `top` answers only while available, and `top_before_last`
    reverts exactly the last applied message through its undo. Commands: `apply`, `disconnect`.
    """

    def __init__(self, replaces: bool, counted: range | None = None) -> None:
        self._replaces = replaces  # Hyperliquid: every message is the whole book
        # The venue-time window (ns) whose messages the counters report -- the checked day, not
        # its look-back or the hour after it; None counts every message.
        self._counted = counted
        # Hyperliquid answers a subscribe with an immediate `l2Book` to that connection only, off
        # the broadcast cadence (the soak's recorder reconnect at 15:58:34Z: pushes at 511.408,
        # then the reply at 513.642, then 516.930, 521.997 s -- the rows of capture, which did not
        # reconnect, went 511.408 -> 516.930). So the first message after a connection line is a
        # book capture never received: it re-baselines the reference, which stays unavailable
        # until the next (broadcast) message.
        self._reply_pending = False
        self._sides: dict[str, dict[Decimal, Decimal]] = {BIDS: {}, ASKS: {}}
        self.available = False
        self._last_u: int | None = None
        self._last_venue_ns: int | None = None
        self._undo: _Undo | None = None
        self.counts: Counter[str] = Counter()

    def _count(self, name: str, message: BookMessage) -> None:
        if self._counted is None or message.venue_ns in self._counted:
            self.counts[name] += 1

    def disconnect(self) -> None:
        self.available = False
        self._last_u = None
        self._undo = None
        self._reply_pending = self._replaces

    def apply(self, message: BookMessage) -> Touched:
        """Apply one message; return what it touched (None: everything)."""
        self._count("messages", message)
        if self._last_venue_ns is not None and message.venue_ns < self._last_venue_ns:
            self._count("time_regress", message)
        self._last_venue_ns = message.venue_ns
        if message.baseline or self._replaces:
            return self._rebaseline(message)
        if not self.available or self._last_u is None:
            self._count("awaiting_snapshot", message)
            return frozenset()
        if message.u != self._last_u + 1:
            self._count("u_breaks", message)
            self.disconnect()
            return None
        self._last_u = message.u
        if not message.bids and not message.asks:
            self._count("zero_level_messages", message)
        return self._apply_delta(message)

    def _rebaseline(self, message: BookMessage) -> Touched:
        self._count("baselines", message)
        self._undo = _Undo(self.available, self._sides, {}, message.recv_ns)
        self._sides = _sides_of(message)
        self.available = not self._reply_pending
        if self._reply_pending:
            self._count("subscribe_replies", message)
            self._reply_pending = False
        self._last_u = message.u
        return None

    def _apply_delta(self, message: BookMessage) -> Touched:
        previous: dict[str, dict[Decimal, Decimal | None]] = {BIDS: {}, ASKS: {}}
        for side, levels in ((BIDS, message.bids), (ASKS, message.asks)):
            book = self._sides[side]
            for price, size in levels:
                previous[side].setdefault(price, book.get(price))
                if size:
                    book[price] = size
                else:
                    book.pop(price, None)
        self._undo = _Undo(True, None, previous, message.recv_ns)
        return frozenset((side, price) for side in SIDES for price in previous[side])

    @property
    def last_recv_ns(self) -> int | None:
        """The recorder's receipt of the last applied message (None without one)."""
        return None if self._undo is None else self._undo.recv_ns

    def top(self, depth: int = DEPTH) -> BookTop | None:
        if not self.available:
            return None
        return BookTop(*(_best(self._sides[side], {}, side, depth) for side in SIDES))

    def top_before_last(self, depth: int = DEPTH) -> BookTop | None:
        """Return the top before the last applied message (None when it was unavailable)."""
        undo = self._undo
        if undo is None or not undo.available or not self.available:
            return None
        if undo.sides is not None:
            return BookTop(*(_best(undo.sides[side], {}, side, depth) for side in SIDES))
        return BookTop(*(_best(self._sides[s], undo.levels[s], s, depth) for s in SIDES))

    def size(self, side: str, price: Decimal) -> Decimal | None:
        return self._sides[side].get(price)

    def size_before_last(self, side: str, price: Decimal) -> Decimal | None:
        undo = self._undo
        if undo is None:
            return None
        if undo.sides is not None:
            return undo.sides[side].get(price)
        if price in undo.levels[side]:
            return undo.levels[side][price]
        return self._sides[side].get(price)


# --- REST agreement ------------------------------------------------------------------------------

AGREE_KEY = "agree_key"
DISAGREE_KEY = "disagree_key"
AGREE_BRACKET = "agree_bracket"
BETWEEN_PUSHES = "between_pushes"
UNALIGNED = "unaligned"
FAILED = "failed"
PERSISTENT_DISAGREEMENT = "persistent_disagreement"
REST_CLASSES = (
    AGREE_KEY,
    DISAGREE_KEY,
    AGREE_BRACKET,
    BETWEEN_PUSHES,
    UNALIGNED,
    FAILED,
    PERSISTENT_DISAGREEMENT,
)
FAILING_REST = (DISAGREE_KEY, PERSISTENT_DISAGREEMENT)
_JUDGED_POLLS = (AGREE_KEY, DISAGREE_KEY, AGREE_BRACKET, BETWEEN_PUSHES)

# What the replay counts (evidence, never failing: a break makes seconds `reference_unavailable`).
REPLAY_COUNTS = (
    "messages",
    "baselines",
    "u_breaks",
    "zero_level_messages",
    "time_regress",
    "awaiting_snapshot",
    "subscribe_replies",
)

# The reference's verdict on itself, from the REST agreement.
VALIDATED = "validated"
INVALID = "invalid"
UNVALIDATED = "unvalidated"


def _rest_top(poll: RestBook) -> BookTop:
    return BookTop(poll.bids[:DEPTH], poll.asks[:DEPTH])


def _in_span(price: Decimal, levels: Sequence[Level], side: str) -> bool:
    """Whether `price` lies between the best and the worst of `levels` (inclusive)."""
    if not levels:
        return False
    best, worst = levels[0][0], levels[-1][0]
    return worst <= price <= best if side == BIDS else best <= price <= worst


def rest_mismatches(
    poll: RestBook, book: ReferenceBook
) -> dict[tuple[str, Decimal], Decimal | None]:
    """
    Return the levels of a `between_pushes` poll the reference contradicts: a REST top-20 level
    whose size is neither the reference's before nor after the bracketing message, and a reference
    top-20 price inside REST's top-20 price span that REST lacks while the reference held it both
    before and after (value None). Keyed `(side, price)`, valued by the REST size.
    """
    top = book.top()
    found: dict[tuple[str, Decimal], Decimal | None] = {}
    for side in SIDES:
        rest = getattr(_rest_top(poll), side)
        for price, size in rest:
            if size not in (book.size(side, price), book.size_before_last(side, price)):
                found[(side, price)] = size
        rest_prices = {price for price, _ in rest}
        for price, _ in getattr(top, side) if top is not None else ():
            held = book.size_before_last(side, price) is not None
            if held and price not in rest_prices and _in_span(price, rest, side):
                found[(side, price)] = None
    return found


class RestJudge:
    """
    One instrument's REST agreement (`docs/DATA_DICTIONARY.md` section 1.18). Invariant: each poll
    is classified exactly once; a level is `persistent_disagreement` only when two consecutive
    `between_pushes` polls contradict it with the same REST value and no reference message touched
    its price in between (`touch`, called for every applied message, forgets a touched price; any
    other class in between resets the watch).

    Known limit (Hyperliquid): every `l2Book` replaces the whole book, so every price counts as
    touched between two polls and `persistent_disagreement` practically cannot fire there;
    Hyperliquid's validation rests on exact `time` key matches (`agree_key`: 14 of 181 polls in the
    Story 31.5 smoke, 2026-09-29 12:59-16:00Z, all equal). Upgrade path: a second REST poll timed
    to land on a push's `time`.
    """

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.examples: list[str] = []
        self._watch: dict[tuple[str, Decimal], Decimal | None] = {}

    def touch(self, touched: Touched) -> None:
        if touched is None:
            self._watch = {}
        else:
            for key in touched & self._watch.keys():
                del self._watch[key]

    def classify(self, verdict: str, poll: RestBook) -> None:
        """
        Count a verdict that is not `between_pushes`; `disagree_key` keeps an example. A judged
        poll ends a run of consecutive `between_pushes` polls; a failed or unaligned one, which
        says nothing about the book, does not.
        """
        self.counts[verdict] += 1
        if verdict in _JUDGED_POLLS:
            self._watch = {}
        if verdict == DISAGREE_KEY and len(self.examples) < EXAMPLES:
            self.examples.append(f"{DISAGREE_KEY}@key={poll.key}")

    def at_key(self, poll: RestBook, book: ReferenceBook) -> None:
        """Judge a poll keyed as the message just applied: its book must be the reference's."""
        top = book.top()
        if top is None:
            self.classify(UNALIGNED, poll)
        else:
            self.classify(AGREE_KEY if top == _rest_top(poll) else DISAGREE_KEY, poll)

    def between(self, poll: RestBook, book: ReferenceBook) -> None:
        """Judge a poll placed between the last applied message and the one before it."""
        before, after = book.top_before_last(), book.top()
        if before is None or after is None:
            self.classify(UNALIGNED, poll)
        elif _rest_top(poll) in (before, after):
            self.classify(AGREE_BRACKET, poll)
        else:
            self._between_pushes(poll, rest_mismatches(poll, book))

    def _between_pushes(
        self, poll: RestBook, found: dict[tuple[str, Decimal], Decimal | None]
    ) -> None:
        self.counts[BETWEEN_PUSHES] += 1
        kept = sorted(k for k, v in found.items() if k in self._watch and self._watch[k] == v)
        self.counts[PERSISTENT_DISAGREEMENT] += len(kept)
        for side, price in kept[: max(0, EXAMPLES - len(self.examples))]:
            self.examples.append(f"{PERSISTENT_DISAGREEMENT}@key={poll.key}:{side} {price}")
        self._watch = found


def reference_state(rest: Mapping[str, int], rows: int, messages: int) -> str:
    """
    Whether the REST agreement validates the reference: `invalid` with any `disagree_key` or
    `persistent_disagreement`; `unvalidated` when the day holds no reference book message at all
    ("no reference data": a day with no reference never passes), or when no judged poll agreed
    (by key or bracket) while polls were judged or rows exist; `validated` otherwise.
    """
    if any(rest.get(name, 0) for name in FAILING_REST):
        return INVALID
    judged = sum(rest.get(name, 0) for name in _JUDGED_POLLS)
    agreed = rest.get(AGREE_KEY, 0) + rest.get(AGREE_BRACKET, 0)
    if not messages or ((judged or rows) and not agreed):
        return UNVALIDATED
    return VALIDATED


# --- the close rule ------------------------------------------------------------------------------


@dataclass(frozen=True)
class ClosedSecond:
    """
    One exchange second closed under DATA-01's rule: `ref` is the book after every message with
    venue time < (S+1) s (None: unavailable), `before` the same without its last message
    (`omitted_recv_ns` that message's receipt) and `after` the same plus the first message
    excluded (None when unknown).
    """

    second: int
    ref: BookTop | None
    before: BookTop | None
    omitted_recv_ns: int | None
    after: BookTop | None


class BookReplay:
    """
    Closes exchange seconds while one instrument's book replays, and places its REST polls.

    Invariant: every second of `[first_second, end_second)` is closed exactly once, in order: by
    the first message whose venue second lies beyond it, or by `finish` as unavailable (the stream
    ended: no message proves the second closed). Each poll (sorted by key) is placed once, at the
    first message whose key is not below its own. Commands: `message`, `connection`, `finish`.
    """

    def __init__(
        self,
        book: ReferenceBook,
        seconds: range,
        polls: Sequence[RestBook],
        judge: RestJudge,
    ) -> None:
        self._book = book
        self._next = seconds.start
        self._end = seconds.stop
        self._polls = sorted(polls, key=lambda poll: poll.key)
        self._placed = 0
        self._judge = judge

    def connection(self) -> None:
        self._book.disconnect()

    def message(self, message: BookMessage) -> list[ClosedSecond]:
        closing = range(self._next, min(message.venue_ns // NS_PER_S, self._end))
        book = self._book
        ref = before = omitted = None
        if closing:
            ref, before, omitted = book.top(), book.top_before_last(), book.last_recv_ns
        touched = book.apply(message)
        # The polls this message places were taken before it (one at its key is judged and resets
        # the watch anyway), so its touch lands after them: it lies between them and the next poll.
        self._place(message.key)
        self._judge.touch(touched)
        if not closing:
            return []
        self._next = closing.stop
        after = book.top()
        return [ClosedSecond(second, ref, before, omitted, after) for second in closing]

    def _place(self, key: int) -> None:
        while self._placed < len(self._polls) and self._polls[self._placed].key <= key:
            poll = self._polls[self._placed]
            self._placed += 1
            if poll.key == key:
                self._judge.at_key(poll, self._book)
            else:
                self._judge.between(poll, self._book)

    def finish(self) -> list[ClosedSecond]:
        for poll in self._polls[self._placed :]:
            self._judge.classify(UNALIGNED, poll)
        self._placed = len(self._polls)
        closing = range(self._next, self._end)
        self._next = self._end
        return [ClosedSecond(second, None, None, None, None) for second in closing]


# --- the stored row against the reference --------------------------------------------------------


@dataclass(frozen=True)
class BookUnits:
    """One book in stored integer units: `(price units, size units)` per level, best first."""

    bids: tuple[UnitLevel, ...]
    asks: tuple[UnitLevel, ...]


@dataclass(frozen=True)
class StoredBook:
    """
    One stored snapshot row's book: when it was sampled (`ts_init`), the precisions its integers
    count at, and its levels as absolute integer units, best first.
    """

    ts_init: int
    price_precision: int
    size_precision: int
    book: BookUnits


def to_units(top: BookTop, price_precision: int, size_precision: int) -> BookUnits:
    """`top` in units at the row's precisions; `OffGrid` when a value is not a whole unit."""

    def side(levels: tuple[Level, ...]) -> tuple[UnitLevel, ...]:
        return tuple((units(p, price_precision), units(s, size_precision)) for p, s in levels)

    return BookUnits(side(top.bids), side(top.asks))


EXACT = "exact"
BOUNDARY_LATE = "boundary_late"
BOUNDARY_UNEXPLAINED = "boundary_unexplained"
BOUNDARY_EARLY = "boundary_early"
CONTENT_DIFFERS = "content_differs"
OFF_GRID = "off_grid"
DUPLICATE_ROW = "duplicate_row"
MISSING_ROW = "missing_row"
MISSING_ROW_EXPLAINED = "missing_row_explained"
REFERENCE_UNAVAILABLE = "reference_unavailable"
SECOND_CLASSES = (
    EXACT,
    BOUNDARY_LATE,
    BOUNDARY_UNEXPLAINED,
    BOUNDARY_EARLY,
    CONTENT_DIFFERS,
    OFF_GRID,
    DUPLICATE_ROW,
    MISSING_ROW,
    MISSING_ROW_EXPLAINED,
    REFERENCE_UNAVAILABLE,
)
FAILING_SECONDS = frozenset(
    {BOUNDARY_UNEXPLAINED, BOUNDARY_EARLY, CONTENT_DIFFERS, OFF_GRID, DUPLICATE_ROW, MISSING_ROW}
)

MISSING = "missing"
EXTRA = "extra"
SIZE = "size"
PRICE = "price"


@dataclass(frozen=True)
class SecondVerdict:
    """A second's class and, for `content_differs`, per side `"<side> <kind>@<index>"`."""

    verdict: str
    levels: tuple[str, ...] = ()


def _kind(
    row: UnitLevel | None,
    ref: UnitLevel | None,
    rows: Sequence[UnitLevel],
    refs: Sequence[UnitLevel],
) -> str:
    if row is None:
        return MISSING
    if ref is None:
        return EXTRA
    if row[0] == ref[0]:
        return SIZE
    row_known = row[0] in {price for price, _ in refs}
    ref_known = ref[0] in {price for price, _ in rows}
    if ref_known == row_known:
        return PRICE  # a level at another price (or the same levels in another order)
    return EXTRA if ref_known else MISSING


def _side_diff(rows: Sequence[UnitLevel], refs: Sequence[UnitLevel]) -> tuple[int, str] | None:
    """Return the first index at which a stored side differs from the reference's, and how."""
    for index in range(max(len(rows), len(refs))):
        row = rows[index] if index < len(rows) else None
        ref = refs[index] if index < len(refs) else None
        if row != ref:
            return index, _kind(row, ref, rows, refs)
    return None


def level_diff(stored: BookUnits, ref: BookUnits) -> tuple[str, ...]:
    """
    Per side, the first differing level of the row against the reference: `missing` (the row lacks
    the reference's level), `extra` (the row holds a level the reference lacks), `size` (same
    price) or `price`. Example: row bids [100, 98], ref [100, 99, 98] -> ("bids missing@1",).
    """
    found = []
    for side in SIDES:
        diff = _side_diff(getattr(stored, side), getattr(ref, side))
        if diff is not None:
            found.append(f"{side} {diff[1]}@{diff[0]}")
    return tuple(found)


def _equals(top: BookTop | None, row: StoredBook) -> bool:
    if top is None:
        return False
    try:
        return to_units(top, row.price_precision, row.size_precision) == row.book
    except OffGrid:
        return False


def _boundary_late(closed: ClosedSecond, row: StoredBook) -> SecondVerdict:
    """Judge a row lacking the second's last message: late only when it arrived near sampling."""
    recv = closed.omitted_recv_ns
    late = recv is not None and recv >= row.ts_init - LATE_ARRIVAL_MARGIN_NS
    return SecondVerdict(BOUNDARY_LATE if late else BOUNDARY_UNEXPLAINED)


def classify_second(
    closed: ClosedSecond, rows: Sequence[StoredBook], run_covers: bool
) -> SecondVerdict | None:
    """
    Classify one second of the day (None: not judged -- no row and no reference). The row is
    compared with `ref`, then `ref-` (`boundary_late`/`boundary_unexplained`), then `ref+`
    (`boundary_early`); any other difference is `content_differs` with its level details.
    """
    if len(rows) > 1:
        return SecondVerdict(DUPLICATE_ROW)
    if closed.ref is None:
        return SecondVerdict(REFERENCE_UNAVAILABLE) if rows else None
    if not rows:
        return SecondVerdict(MISSING_ROW_EXPLAINED if run_covers else MISSING_ROW)
    (row,) = rows
    try:
        ref = to_units(closed.ref, row.price_precision, row.size_precision)
    except OffGrid:
        return SecondVerdict(OFF_GRID)
    if row.book == ref:
        return SecondVerdict(EXACT)
    if _equals(closed.before, row):
        return _boundary_late(closed, row)
    if _equals(closed.after, row):
        return SecondVerdict(BOUNDARY_EARLY)
    return SecondVerdict(CONTENT_DIFFERS, level_diff(row.book, ref))


# --- the report ----------------------------------------------------------------------------------


class SecondCounter:
    """
    The mutable tally of one instrument's second verdicts (never escapes the run building it):
    classes, `content_differs` level kinds (`"<side> <kind>"` -> count), failing examples.
    """

    def __init__(self) -> None:
        self.classes: Counter[str] = Counter()
        self.levels: Counter[str] = Counter()
        self.examples: list[tuple[int, str, tuple[str, ...]]] = []

    def add(self, second: int, verdict: SecondVerdict) -> None:
        self.classes[verdict.verdict] += 1
        for detail in verdict.levels:
            self.levels[detail.partition("@")[0]] += 1
        if verdict.verdict in FAILING_SECONDS and len(self.examples) < EXAMPLES:
            self.examples.append((second, verdict.verdict, verdict.levels))


def _frozen(counts: Iterable[tuple[str, int]]) -> Mapping[str, int]:
    return MappingProxyType(dict(sorted(counts)))


@dataclass(frozen=True)
class InstrumentBook:
    """
    One instrument's result. It passes only with every failing REST and second count at 0, a
    `validated` reference and, when rows exist, at least one second verified.
    """

    instrument_id: str
    rest: Mapping[str, int]
    rest_examples: tuple[str, ...]
    replay: Mapping[str, int]
    seconds: Mapping[str, int]
    levels: Mapping[str, int]
    examples: tuple[tuple[int, str, tuple[str, ...]], ...]
    rows: int

    @classmethod
    def of(
        cls,
        instrument_id: str,
        judge: RestJudge,
        replay: Counter[str],
        seconds: SecondCounter,
        rows: int,
    ) -> "InstrumentBook":
        return cls(
            instrument_id=instrument_id,
            rest=_frozen((name, judge.counts.get(name, 0)) for name in REST_CLASSES),
            rest_examples=tuple(judge.examples),
            replay=_frozen((name, replay.get(name, 0)) for name in REPLAY_COUNTS),
            seconds=_frozen((name, seconds.classes.get(name, 0)) for name in SECOND_CLASSES),
            levels=_frozen(seconds.levels.items()),
            examples=tuple(seconds.examples),
            rows=rows,
        )

    @property
    def reference(self) -> str:
        return reference_state(self.rest, self.rows, self.replay.get("messages", 0))

    @property
    def verified(self) -> int:
        """Seconds whose row was compared with an available reference."""
        compared = (EXACT, BOUNDARY_LATE, BOUNDARY_UNEXPLAINED, BOUNDARY_EARLY, CONTENT_DIFFERS)
        return sum(self.seconds[name] for name in (*compared, OFF_GRID))

    @property
    def failing(self) -> int:
        rest = sum(self.rest[name] for name in FAILING_REST)
        return rest + sum(self.seconds[name] for name in FAILING_SECONDS)

    @property
    def passed(self) -> bool:
        nothing_verified = self.rows > 0 and self.verified == 0
        return self.failing == 0 and self.reference == VALIDATED and not nothing_verified


@dataclass(frozen=True)
class BookDayReport:
    """
    A venue's checked day: it passes only when every instrument does, the coverage record exists
    and no raw book hour of the day is missing.
    """

    venue: str
    day: str
    coverage_file: str
    coverage_present: bool
    missing_raw_files: tuple[str, ...]
    truncated_neighbour_files: tuple[str, ...]
    instruments: tuple[InstrumentBook, ...]

    @property
    def passed(self) -> bool:
        inputs_whole = self.coverage_present and not self.missing_raw_files
        return inputs_whole and all(report.passed for report in self.instruments)


def closed_seconds(
    replay: BookReplay, events: Iterator[BookMessage | None]
) -> Iterator[ClosedSecond]:
    """Drive `replay` over `events` (a message, or None for a connection line); then finish it."""
    for event in events:
        if event is None:
            replay.connection()
        else:
            yield from replay.message(event)
    yield from replay.finish()
