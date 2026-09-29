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
Mark, index, funding and open interest proven against the venue (Story 31.6), pure: the ticker
frame and poll parsers, the reference streams, the three row matchers, the update and REST
classes, the poll coverage, the label check and the report values. No I/O, no clock.

The reference side never imports the code it checks: the reference is the recorder's verbatim
Bybit `tickers` / Hyperliquid `activeAssetCtx` frames and REST polls (`docs/DATA_DICTIONARY.md`
section 1.15), parsed here from the venues' wire formats; the stored rows arrive as plain values
decoded by this context's own reader. The storage filters being checked are restated from the
Rust adapters, never imported: Bybit's `funding_cache`
(`crates/adapters/bybit/src/python/websocket.rs:1549-1570`) and Hyperliquid's per-field string
caches (`crates/adapters/hyperliquid/src/websocket/handler.rs:868-970`).

Invariants:
- exact: every value is the wire's or the store's decimal text as a `Decimal` (or an integer);
  nothing is compared within a value tolerance -- the two bounds below are time alignments only,
  each beside its measurement;
- a failing count is never folded into a passing class: `passed` is false while any is non-zero
  or the reference is unvalidated.
"""

import json
from bisect import bisect_left
from bisect import bisect_right
from collections import Counter
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from types import MappingProxyType
from typing import Any
from urllib.parse import parse_qs
from urllib.parse import urlsplit

from verification.domain.conservation import EXAMPLES
from verification.domain.conservation import NS_PER_MS
from verification.domain.conservation import NS_PER_S
from verification.domain.conservation import Intervals
from verification.domain.conservation import MalformedLine
from verification.domain.instrument_check import DefinitionReport
from verification.domain.reference_book import payload_of
from verification.domain.reference_book import recv_ns_of


MARK = "mark"
INDEX = "index"
FUNDING = "funding"
OPEN_INTEREST = "open_interest"
TYPES = (MARK, INDEX, FUNDING, OPEN_INTEREST)
PRICE_TYPES = (MARK, INDEX)

# How far a stored Hyperliquid row's `ts_init` (the adapter's receive clock) may lie from the
# recorder's `recv_ns` of the frame it came from. Hyperliquid's `activeAssetCtx` carries no venue
# time, so the two receipts are the only alignment. Measured on the soak (2026-09-29
# 13:00-17:00Z, SOL): stored `ts_init - recv_ns` from p1 -57 ms to max 437 ms; the push cadence
# is median 1.02 s, min 196 ms. 1 s covers the measured spread twice over; an equal value from a
# neighbouring push inside it is still told apart because a match requires value equality and
# consumes the earliest unconsumed update. Known limit (audit D-112, OPEN): a receipt difference
# above 1 s reads as a `not_stored` update, a loud false fail, and its row as `agree_state` when a
# frame within the bound held the value (else `unmatched`). The follow-up smoke hit it once: capture's
# connection got SOL `oraclePx` 117.525 one push (+1,048 ms) after the recorder's. Upgrade path:
# re-measure over a full day (Story 31.11) and widen the bound past a push, or capture records the
# frame's receipt next to the row.
HL_MATCH_BOUND_NS = NS_PER_S

# How far before a Bybit open-interest row's `ts_event` (the collector's clock after the poll's
# response, `capture/venues/bybit/open_interest.py:44`) the venue state it stored may lie: the poll
# reads the state when Bybit answers, then the response travels back. Measured on the soak: every
# one of 96 rows equals the WS `openInterest` state at `ts_event` or at most 623 ms before it (the
# poll's latency); the median WS open-interest update interval is 8.7 s, so a 2 s window admits
# at most the one or two states around the response. Known limit: a poll slower than 2 s reads as
# `unmatched`, a loud false fail; and the window compares a local clock (`ts_event`) with Bybit's
# venue keys, so it assumes no clock skew between this host and Bybit beyond what it absorbs. The
# evidence for that assumption: the recorder's `recv_ns - ts` over 261,598 `linear.tickers` frames
# (2026-09-29 13:00-17:00Z) was min 80.3 ms, p1 83.4, p50 99.7, p99 184.7, max 411 ms -- never
# negative, so the host clock does not run behind Bybit's by more than the ~80 ms network floor;
# a skew of a second or more would shift every row out of its window (a loud false fail, never a
# false pass). Upgrade path: capture stamps the row with the response's `time`.
OI_POLL_WINDOW_NS = 2 * NS_PER_S

# Coverage `seconds` reasons that mean the collector's own feed was absent, so an update it did not
# store is explained: `restart` (no collector process), `stale` (no book message for the stale
# bound: the socket or its subscriptions were dead), `no_book` (no book yet: connecting or
# resubscribing) and `not_collected` (planned, not subscribed on the wire). Every other reason
# (`crossed`, `empty_top`, `unencodable`, `catch_up_cap`, `missed_tick`, `write_failed`) is a
# verdict on a second of a live feed, which still delivered its ticker frames.
FEED_LOSS_REASONS = frozenset({"restart", "stale", "no_book", "not_collected"})

# Hyperliquid funds hourly (venue docs, "Funding"); the adapter stores `interval` 60 and no
# `next_funding_ns` (`crates/adapters/hyperliquid/src/websocket/parse.rs`).
HL_FUNDING_INTERVAL_MINUTES = 60
MINUTES_PER_HOUR = 60

# A poll gap is a spacing above 3/2 of the configured period (integers, no float).
POLL_GAP_NUMERATOR = 3
POLL_GAP_DENOMINATOR = 2

# Row classes (`docs/DATA_DICTIONARY.md` section 1.19).
EXACT = "exact"
AGREE_STATE = "agree_state"
VALUE_MISMATCH = "value_mismatch"
UNMATCHED = "unmatched"
REFERENCE_UNAVAILABLE = "reference_unavailable"
OFF_GRID = "off_grid"
TS_RULE = "ts_rule"
DUPLICATE = "duplicate"
ROW_CLASSES = (
    EXACT,
    AGREE_STATE,
    VALUE_MISMATCH,
    UNMATCHED,
    REFERENCE_UNAVAILABLE,
    OFF_GRID,
    TS_RULE,
    DUPLICATE,
)
FAILING_ROWS = frozenset({VALUE_MISMATCH, UNMATCHED, OFF_GRID, TS_RULE, DUPLICATE})

# Reference-update classes. `reference_unavailable` is an update whose own time lies in a recorder
# gap: after a recorder reconnect its first frame is that connection's snapshot, which capture's
# connection never received.
EXPECTED = "expected"  # an update the collector must store (a hint, never reported)
STORED = "stored"
UNCHANGED = "unchanged"
NEXT_TIME_ONLY = "next_time_only"
NOT_STORED_EXPLAINED = "not_stored_explained"
NOT_STORED = "not_stored"
UPDATE_CLASSES = (
    STORED,
    UNCHANGED,
    NEXT_TIME_ONLY,
    NOT_STORED_EXPLAINED,
    NOT_STORED,
    REFERENCE_UNAVAILABLE,
)
FAILING_UPDATES = frozenset({NOT_STORED})

# REST classes: Bybit's placed by the response's `time`, Hyperliquid's by the receive window.
AGREE_KEY = "agree_key"
AGREE_BRACKET = "agree_bracket"
AGREE = "agree"
BETWEEN_PUSHES = "between_pushes"
UNALIGNED = "unaligned"
FAILED = "failed"
REST_CLASSES = (AGREE_KEY, AGREE_BRACKET, AGREE, BETWEEN_PUSHES, UNALIGNED, FAILED)
_AGREEING = (AGREE_KEY, AGREE_BRACKET, AGREE)
_JUDGED = (*_AGREEING, BETWEEN_PUSHES)
VALIDATED = "validated"
UNVALIDATED = "unvalidated"

# The wire fields each type is read from.
BYBIT_FIELDS = MappingProxyType(
    {MARK: "markPrice", INDEX: "indexPrice", FUNDING: "fundingRate", OPEN_INTEREST: "openInterest"}
)
HYPERLIQUID_FIELDS = MappingProxyType(
    {MARK: "markPx", INDEX: "oraclePx", FUNDING: "funding", OPEN_INTEREST: "openInterest"}
)
_BYBIT_INTERVAL = "fundingIntervalHour"
_BYBIT_NEXT = "nextFundingTime"
_BYBIT_WIRE = frozenset({*BYBIT_FIELDS.values(), _BYBIT_INTERVAL, _BYBIT_NEXT})

# What a stored value is: `(value,)` for a price or open interest, `(rate, interval minutes,
# next_funding_ns)` for funding (None: the field was not stored).
Value = tuple[Decimal | int | None, ...]


def _is_digits(text: str) -> bool:
    return text.isascii() and text.isdigit()


def _plain_decimal(text: str) -> bool:
    """ASCII digits with an optional fraction and an optional leading `-` (funding is signed)."""
    whole, dot, fraction = text.removeprefix("-").partition(".")
    return _is_digits(whole) and (not dot or _is_digits(fraction))


def decimal_text(value: object, where: str) -> Decimal:
    """
    Parse a wire decimal: `_plain_decimal` text, nothing else `Decimal` would take (exponents,
    `+`, `NaN`, whitespace); `MalformedLine` otherwise.
    """
    if not isinstance(value, str) or not _plain_decimal(value):
        raise MalformedLine(f"{where}: value {value!r:.80} is not a decimal string")
    return Decimal(value)


def stored_decimal(value: object, where: str) -> Decimal:
    """
    Parse a stored decimal text: plain, or in exact scientific notation. Nautilus serialises a
    funding `rate` through `rust_decimal`, which writes small magnitudes that way (the soak:
    `"-9.368E-7"` for Hyperliquid's wire `-0.0000009368`), and Python's `str(Decimal)` does too.
    The exponent form is exact, so it is accepted here -- never on the wire -- while `NaN`,
    infinities, a leading `+` and whitespace are still refused (`MalformedLine`).
    """
    text = value if isinstance(value, str) else ""
    mantissa, marker, exponent = text.partition("E")
    power = exponent[1:] if exponent[:1] in ("+", "-") else exponent
    if not (_plain_decimal(mantissa) and (not marker or _is_digits(power))):
        raise MalformedLine(f"{where}: stored value {value!r:.80} is not decimal text")
    return Decimal(text)


def _digits(value: object, where: str) -> int:
    if not isinstance(value, str) or not (value.isascii() and value.isdigit()):
        raise MalformedLine(f"{where}: value {value!r:.80} is not an unsigned integer string")
    return int(value)


def _object(value: object, what: str, where: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise MalformedLine(f"{where}: {what} is not a JSON object: {value!r:.200}")
    return value


def _int(payload: Mapping[str, Any], key: str, where: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise MalformedLine(f"{where}: `{key}` is {value!r:.80}, not an integer")
    return value


# --- the wire ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Frame:
    """One ticker/context frame of one instrument: its key (ns), receipt and wire field strings."""

    key: int
    recv_ns: int
    fields: Mapping[str, str]


def bybit_ticker(record: Mapping[str, object], where: str) -> tuple[str, Frame]:
    """
    Parse one Bybit `tickers.<symbol>` frame: `(symbol, frame)`, keyed by the frame's `ts` ms x
    10^6 (what the adapter stamps as `ts_event`). A frame without `ts` or `data.symbol`, or whose
    topic names another symbol, is a `MalformedLine` (the story's Block If).
    """
    payload = payload_of(record, where)
    data = _object(payload.get("data"), "`data`", where)
    symbol, topic = data.get("symbol"), payload.get("topic")
    if not isinstance(symbol, str) or topic != f"tickers.{symbol}":
        raise MalformedLine(f"{where}: ticker symbol {symbol!r} is not its topic's {topic!r}")
    fields = {name: value for name, value in data.items() if name in _BYBIT_WIRE}
    for name, value in fields.items():
        _check_field(name, value, where)
    frame = Frame(_int(payload, "ts", where) * NS_PER_MS, recv_ns_of(record, where), fields)
    return symbol, frame


def _check_field(name: str, value: object, where: str) -> None:
    # Bybit sends an empty string for a field an instrument has none of (`fundingRate: ""` on a
    # dated future). The adapter's parse of it fails and writes nothing (`parse_ticker_linear_
    # funding`'s "empty funding_rate" bail, called from `crates/adapters/bybit/src/python/
    # websocket.rs:1549-1570` after the `funding_cache` was already updated), so it is kept as a
    # wire string -- the cache emulation needs it -- but never becomes a value.
    if value == "":
        return
    if name in (_BYBIT_INTERVAL, _BYBIT_NEXT):
        _digits(value, f"{where} `{name}`")
    else:
        decimal_text(value, f"{where} `{name}`")


def hyperliquid_coin_of(record: Mapping[str, object]) -> str | None:
    """
    Return the coin an `activeAssetCtx` line names, without judging the rest of it (None when it
    names none): a frame of another coin is skipped before the strict parse, so a venue oddity in
    a coin the plan does not hold never refuses the run. A frame naming no coin is parsed, and
    refused, by `hyperliquid_ctx`.
    """
    try:
        body = json.loads(str(record.get("raw")))
    except ValueError:
        return None
    data = body.get("data") if isinstance(body, dict) else None
    coin = data.get("coin") if isinstance(data, dict) else None
    return coin if isinstance(coin, str) else None


def hyperliquid_ctx(record: Mapping[str, object], where: str) -> tuple[str, Frame]:
    """
    Parse one Hyperliquid `activeAssetCtx` frame: `(coin, frame)`, keyed by the recorder's
    `recv_ns` (the wire carries no time). A frame without `data.coin` or `data.ctx` is a
    `MalformedLine` (the story's Block If).
    """
    data = _object(payload_of(record, where).get("data"), "`data`", where)
    coin = data.get("coin")
    if not isinstance(coin, str):
        raise MalformedLine(f"{where}: activeAssetCtx coin {coin!r}")
    ctx = _object(data.get("ctx"), "`data.ctx`", where)
    fields = {name: ctx[name] for name in HYPERLIQUID_FIELDS.values() if name in ctx}
    for name, value in fields.items():
        decimal_text(value, f"{where} `{name}`")
    recv_ns = recv_ns_of(record, where)
    return coin, Frame(recv_ns, recv_ns, fields)


@dataclass(frozen=True)
class Poll:
    """
    One REST poll of one instrument: `ok` false when failed; `key` the response's venue `time`
    (Bybit, ns) or its receipt (Hyperliquid); the request's send and receipt; the field values.
    """

    ok: bool
    key: int
    sent_ns: int
    recv_ns: int
    values: Mapping[str, Decimal]


def failed_poll(record: Mapping[str, object]) -> bool:
    """
    Return whether the recorder saw the poll fail: a status other than 200, or a recorder
    `refusal` (a body that is not JSON, Bybit's in-band refusal, an empty list) or `error`.
    """
    return record.get("status") != 200 or "refusal" in record or "error" in record


def _sent_ns(record: Mapping[str, object], where: str) -> int:
    value = record.get("sent_ns")
    if not isinstance(value, int) or isinstance(value, bool):
        raise MalformedLine(f"{where}: a REST line without an integer `sent_ns`")
    return value


def request_symbol(record: Mapping[str, object], where: str) -> str:
    """Return the `symbol` a Bybit REST line requested."""
    request = record.get("request")
    symbols = parse_qs(urlsplit(request).query).get("symbol") if isinstance(request, str) else None
    if not symbols:
        raise MalformedLine(f"{where}: a Bybit poll without a `symbol` in {request!r}")
    return symbols[0]


def bybit_list_item(
    record: Mapping[str, object], symbol: str, where: str
) -> Mapping[str, Any] | None:
    """
    Return `result.list[0]` of a Bybit poll, or None when the venue's answer holds no usable item:
    a failed poll, a `retCode` other than 0, no `result.list` or an empty one, an item that is not
    an object or names another symbol. Those are venue oddities -- the poll is counted `failed`,
    never a refusal of the run; only the recorder's own line shape is refused.
    """
    if failed_poll(record):
        return None
    payload = payload_of(record, where)
    result = payload.get("result")
    items = result.get("list") if isinstance(result, dict) else None
    if payload.get("retCode") != 0 or not isinstance(items, list) or not items:
        return None
    item = items[0]
    return item if isinstance(item, dict) and item.get("symbol") == symbol else None


def _decimals(item: Mapping[str, Any], fields: Iterable[str]) -> Mapping[str, Decimal]:
    """Return the item's fields that are plain decimal text; any other is left out, unjudged."""
    found = {}
    for field in fields:
        value = item.get(field)
        if isinstance(value, str) and _plain_decimal(value):
            found[field] = Decimal(value)
    return MappingProxyType(found)


def bybit_ticker_poll(record: Mapping[str, object], where: str) -> tuple[str, Poll]:
    """Parse one Bybit `/v5/market/tickers` poll: `(requested symbol, poll)`."""
    symbol = request_symbol(record, where)
    sent, recv = _sent_ns(record, where), recv_ns_of(record, where)
    item = bybit_list_item(record, symbol, where)
    time_ms = payload_of(record, where).get("time") if item is not None else None
    if item is None or not isinstance(time_ms, int) or isinstance(time_ms, bool):
        return symbol, Poll(False, 0, sent, recv, MappingProxyType({}))
    values = _decimals(item, BYBIT_FIELDS.values())
    return symbol, Poll(True, time_ms * NS_PER_MS, sent, recv, values)


def hyperliquid_universe(
    record: Mapping[str, object], coin: str
) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    """
    Return `(universe entry, asset context)` of a good `metaAndAssetCtxs` poll, by `name`; None
    for a venue answer without one -- not `[meta, ctxs]`, the coin absent or listed twice, the
    contexts not matching the universe one for one (a `failed` poll, never a refusal).
    """
    try:
        body = json.loads(str(record.get("raw")))
    except ValueError:
        return None
    if not isinstance(body, list) or len(body) != 2 or not isinstance(body[0], dict):
        return None
    universe, contexts = body[0].get("universe"), body[1]
    if not isinstance(universe, list) or not isinstance(contexts, list):
        return None
    names = [entry.get("name") if isinstance(entry, dict) else None for entry in universe]
    if names.count(coin) != 1 or len(contexts) != len(universe):
        return None
    entry, context = universe[names.index(coin)], contexts[names.index(coin)]
    return (entry, context) if isinstance(context, dict) else None


def hyperliquid_ctx_poll(record: Mapping[str, object], coin: str, where: str) -> Poll:
    """Parse one Hyperliquid `metaAndAssetCtxs` poll for `coin`."""
    sent, recv = _sent_ns(record, where), recv_ns_of(record, where)
    found = None if failed_poll(record) else hyperliquid_universe(record, coin)
    if found is None:
        return Poll(False, recv, sent, recv, MappingProxyType({}))
    return Poll(True, recv, sent, recv, _decimals(found[1], HYPERLIQUID_FIELDS.values()))


# --- the reference -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Recording:
    """
    What the recorder's channel says about its own availability: `gaps` (its connection gaps,
    widened, and every hour it has no file for) and `resets` (each connection line and each
    missing hour's start, after which a field's state is unknown until it is sent again).
    """

    gaps: Intervals
    resets: tuple[int, ...]

    def in_gap(self, ts_ns: int) -> bool:
        return self.gaps.contains(ts_ns)

    def last_reset(self, ts_ns: int) -> int | None:
        index = bisect_right(self.resets, ts_ns) - 1
        return self.resets[index] if index >= 0 else None


@dataclass(frozen=True)
class Timeline:
    """
    Values keyed by time, sorted by key (stable: equal keys keep arrival order), each with the
    recorder's receipt of its frame.
    """

    keys: tuple[int, ...]
    receipts: tuple[int, ...]
    values: tuple[Value, ...]

    @classmethod
    def of(cls, entries: Iterable[tuple[int, int, Value]]) -> "Timeline":
        """Build from `(key, recv_ns, value)` entries in arrival order."""
        ordered = sorted(entries, key=lambda entry: entry[0])
        return cls(
            tuple(e[0] for e in ordered), tuple(e[1] for e in ordered), tuple(e[2] for e in ordered)
        )

    def last_at(self, ts_ns: int) -> int:
        """Return the index of the last value keyed at or before `ts_ns` (-1: none)."""
        return bisect_right(self.keys, ts_ns) - 1

    def window(self, low: int, high: int) -> range:
        """Return the indexes keyed in `[low, high]`."""
        return range(bisect_left(self.keys, low), bisect_right(self.keys, high))

    def state_at(self, ts_ns: int, recording: Recording) -> Value | None:
        """
        Return the last value keyed at or before `ts_ns`, or None (unknown) when there is none or
        its frame was received before the recorder's last reset at or before `ts_ns`. The receipt,
        not the key, is compared with the reset: both are local clocks, whereas a Bybit key is the
        venue's -- the soak's reconnect snapshot carried `ts` 12:59:20.283 and arrived at
        12:59:21.26, after its connection's `open` line at 12:59:21.07.
        """
        index = self.last_at(ts_ns)
        reset = recording.last_reset(ts_ns)
        if index < 0 or (reset is not None and self.receipts[index] < reset):
            return None
        return self.values[index]


@dataclass(frozen=True)
class Update:
    """
    One reference update of one type: its key, the value a stored row must hold, and the
    storage hint (`expected`, or Bybit funding's `unchanged` / `next_time_only`).
    """

    key: int
    value: Value
    hint: str = EXPECTED


@dataclass(frozen=True)
class TypeReference:
    """
    One instrument-type's reference over the read window: its updates (key order), `frames` (the
    REST-validated field keyed by venue time on Bybit; every frame's value keyed by receipt on
    Hyperliquid), the Bybit state `components` (one timeline per value component) and the
    recording's availability.
    """

    kind: str
    updates: tuple[Update, ...]
    frames: Timeline
    components: tuple[Timeline, ...]
    recording: Recording

    def state(self, ts_ns: int) -> Value | None:
        """Return the composed state at `ts_ns` (Bybit); None when its first part is unknown."""
        parts = [c.state_at(ts_ns, self.recording) for c in self.components]
        if not parts or parts[0] is None:
            return None
        return tuple(part[0] if part is not None else None for part in parts)


def _component(frames: Sequence[Frame], field: str, scale: int | None) -> Timeline:
    """One Bybit field's timeline: decimals as `Decimal`, integer fields times `scale`."""
    pairs = []
    for frame in frames:
        text = frame.fields.get(field)
        if text:  # an empty string is no value (`_check_field`)
            value = Decimal(text) if scale is None else int(text) * scale
            pairs.append((frame.key, frame.recv_ns, (value,)))
    return Timeline.of(pairs)


def _funding_value(frame: Frame) -> Value:
    rate = frame.fields.get(BYBIT_FIELDS[FUNDING])
    interval = frame.fields.get(_BYBIT_INTERVAL)
    next_time = frame.fields.get(_BYBIT_NEXT)
    return (
        Decimal(rate) if rate else None,
        int(interval) * MINUTES_PER_HOUR if interval else None,
        int(next_time) * NS_PER_MS if next_time else None,
    )


def bybit_funding_updates(frames: Sequence[Frame]) -> tuple[Update, ...]:
    """
    Emulate the adapter's `funding_cache` over the frames in arrival order: a frame carrying
    `fundingRate` or `nextFundingTime` is an update; it is `expected` when either string differs
    from the last seen, `next_time_only` when only `nextFundingTime` changed and the frame has no
    rate (the adapter's parse needs the rate, so nothing is written: a Known limit), `unchanged`
    otherwise. The cache compares strings, exactly as the Rust `Option<String>` does. A frame whose
    `fundingRate` is the empty string (a dated future) updates the cache but yields no update: the
    adapter's parse fails on it and writes nothing.
    """
    last_rate: str | None = None
    last_next: str | None = None
    updates = []
    for frame in frames:
        rate, next_time = frame.fields.get("fundingRate"), frame.fields.get(_BYBIT_NEXT)
        if rate is None and next_time is None:
            continue
        changed = (rate is not None and rate != last_rate) or (
            next_time is not None and next_time != last_next
        )
        last_rate = rate if rate is not None else last_rate
        last_next = next_time if next_time is not None else last_next
        if rate == "":
            continue
        hint = UNCHANGED if not changed else (NEXT_TIME_ONLY if rate is None else EXPECTED)
        updates.append(Update(frame.key, _funding_value(frame), hint))
    return tuple(sorted(updates, key=lambda update: update.key))


def bybit_references(frames: Sequence[Frame], recording: Recording) -> Mapping[str, TypeReference]:
    """Every type's reference from one Bybit symbol's ticker frames (arrival order)."""
    references = {}
    for kind in (MARK, INDEX, OPEN_INTEREST):
        timeline = _component(frames, BYBIT_FIELDS[kind], None)
        updates = tuple(Update(k, v) for k, v in zip(timeline.keys, timeline.values, strict=True))
        references[kind] = TypeReference(kind, updates, timeline, (timeline,), recording)
    rate = _component(frames, BYBIT_FIELDS[FUNDING], None)
    components = (
        rate,
        _component(frames, _BYBIT_INTERVAL, MINUTES_PER_HOUR),
        _component(frames, _BYBIT_NEXT, NS_PER_MS),
    )
    updates = bybit_funding_updates(frames)
    references[FUNDING] = TypeReference(FUNDING, updates, rate, components, recording)
    return references


def _hyperliquid_value(kind: str, text: str) -> Value:
    if kind == FUNDING:
        return (Decimal(text), HL_FUNDING_INTERVAL_MINUTES, None)
    return (Decimal(text),)


def hyperliquid_references(
    frames: Sequence[Frame], recording: Recording
) -> Mapping[str, TypeReference]:
    """
    Every type's reference from one coin's context frames (arrival order): an update is a frame
    whose wire string of the field differs from the previous frame's, as the adapter's caches
    compare them.
    """
    references = {}
    for kind, field in HYPERLIQUID_FIELDS.items():
        pairs, updates, last = [], [], None
        for frame in frames:
            text = frame.fields.get(field)
            if text is None:
                continue
            value = _hyperliquid_value(kind, text)
            pairs.append((frame.key, frame.recv_ns, value))
            if text != last:
                updates.append(Update(frame.key, value))
            last = text
        timeline = Timeline.of(pairs)
        # Sorted by receipt before any bisect: the host's wall clock can step back (NTP), so
        # arrival order is not always receipt order.
        ordered = tuple(sorted(updates, key=lambda update: update.key))
        references[kind] = TypeReference(kind, ordered, timeline, (), recording)
    return references


# --- stored rows ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class StoredValue:
    """One stored row, decoded: its clocks, its value and whether it is whole at its file's label."""

    ts_event: int
    ts_init: int
    value: Value
    on_grid: bool = True


def ts_whole_ms(row: StoredValue) -> bool:
    """Bybit mark, index and funding: `ts_event` is the venue frame's `ts`, whole milliseconds."""
    return row.ts_event % NS_PER_MS == 0


def ts_is_init(row: StoredValue) -> bool:
    """Bybit open interest and every Hyperliquid type: `ts_event == ts_init`, a local clock."""
    return row.ts_event == row.ts_init


def agrees(stored: Value, state: Value) -> bool:
    """
    Whether a stored value equals a reference state: component by component, a stored None (a
    field the frame did not carry: Bybit funding's `interval`/`next_funding_ns`) agrees with any
    state; every other component must be equal.
    """
    return len(stored) == len(state) and all(
        mine is None or mine == theirs for mine, theirs in zip(stored, state, strict=True)
    )


def compare_state(stored: Value, state: Value) -> str:
    """
    Classify a stored value against a composed reference state (Bybit funding's `(rate,
    interval, next time)`, one component elsewhere): `unmatched` when any component both sides
    know differs; `reference_unavailable` when the rest agree but a component the row stores is
    unknown to the reference (a collector restart's snapshot row carries `interval`/`next_funding_ns`,
    which no recorder frame since the last reset carried); `agree_state` otherwise. A stored None
    (a field its frame did not carry) agrees with any state.
    """
    if len(stored) != len(state):
        return UNMATCHED
    pairs = [(mine, theirs) for mine, theirs in zip(stored, state, strict=True) if mine is not None]
    if any(theirs is not None and mine != theirs for mine, theirs in pairs):
        return UNMATCHED
    if any(theirs is None for _, theirs in pairs):
        return REFERENCE_UNAVAILABLE
    return AGREE_STATE


def _show(value: Value | None) -> str:
    if value is None:
        return "unknown"
    return "/".join("null" if part is None else str(part) for part in value)


class RowJudge:
    """
    The mutable tally of one instrument-type's rows (never escapes the run building it): row
    classes, consumed update indexes, failing examples and the `(ts_event, value)` pairs seen.
    """

    def __init__(self, reference: TypeReference, counted: range | None = None) -> None:
        self.reference = reference
        # Rows outside `counted` (ns of `ts_event`) are matched -- they consume the updates they
        # came from -- but not counted: Hyperliquid's rows are read up to `HL_MATCH_BOUND_NS`
        # beyond the day so an update received just after midnight finds its row stamped just
        # before it (two clocks), and vice versa.
        self.counted = counted
        self.classes: Counter[str] = Counter()
        self.consumed: set[int] = set()
        self.examples: list[str] = []
        self._seen: set[tuple[int, Value]] = set()

    def prelude(self, row: StoredValue, rule: Callable[[StoredValue], bool]) -> str | None:
        """Return the row-shape class, before any comparison: duplicate, `ts` rule, off grid."""
        pair = (row.ts_event, row.value)
        if pair in self._seen:
            return DUPLICATE
        self._seen.add(pair)
        if not rule(row):
            return TS_RULE
        return None if row.on_grid else OFF_GRID

    def add(self, verdict: str, row: StoredValue, detail: str = "") -> None:
        if self.counted is not None and row.ts_event not in self.counted:
            return
        self.classes[verdict] += 1
        if verdict in FAILING_ROWS and len(self.examples) < EXAMPLES:
            suffix = f" {detail}" if detail else ""
            self.examples.append(f"{verdict}@{row.ts_event}: stored {_show(row.value)}{suffix}")


def match_keyed(rows: Iterable[StoredValue], judge: RowJudge) -> None:
    """
    Bybit mark, index, funding: each row against the reference updates of its own key
    (`ts_event`, a multiset per key): an unconsumed equal one is `exact`; a same-key update of
    another value is `value_mismatch`; without one the row is judged against the state at its key.
    """
    reference = judge.reference
    by_key: dict[int, list[int]] = {}
    for index, update in enumerate(reference.updates):
        by_key.setdefault(update.key, []).append(index)
    for row in sorted(rows, key=lambda r: (r.ts_event, r.ts_init)):
        verdict = judge.prelude(row, ts_whole_ms)
        if verdict is not None:
            judge.add(verdict, row)
            continue
        same = by_key.get(row.ts_event, [])
        verdict, detail = _keyed_verdict(row, same, judge)
        judge.add(verdict, row, detail)


def _keyed_verdict(row: StoredValue, same: Sequence[int], judge: RowJudge) -> tuple[str, str]:
    """
    Classify a keyed row: a recorded frame of its own key is compared first, even inside a
    recorder gap (its widened margins hold frames the recorder did receive); only without one does
    a gap make the row `reference_unavailable`.
    """
    reference = judge.reference
    free = [i for i in same if i not in judge.consumed and reference.updates[i].value == row.value]
    if free:
        judge.consumed.add(free[0])
        return EXACT, ""
    if same:
        return VALUE_MISMATCH, f"reference {_show(reference.updates[same[0]].value)}"
    state = reference.state(row.ts_event)
    if state is None or reference.recording.in_gap(row.ts_event):
        return REFERENCE_UNAVAILABLE, ""
    verdict = compare_state(row.value, state)
    return verdict, f"state {_show(state)}" if verdict == UNMATCHED else ""


def match_by_receive(rows: Iterable[StoredValue], judge: RowJudge) -> None:
    """
    Hyperliquid, every type: a row is `exact` with the earliest unconsumed update of equal value
    received within `HL_MATCH_BOUND_NS` of its `ts_init`; else `agree_state` when any frame
    received within the bound held its value; else `unmatched`.
    """
    reference = judge.reference
    keys = tuple(update.key for update in reference.updates)
    for row in sorted(rows, key=lambda r: (r.ts_init, r.ts_event)):
        verdict = judge.prelude(row, ts_is_init)
        if verdict is None:
            verdict = _receive_verdict(row, keys, judge)
        judge.add(verdict, row)


def _receive_verdict(row: StoredValue, keys: Sequence[int], judge: RowJudge) -> str:
    """Classify a received row: an equal update or frame first; a recorder gap decides a miss."""
    reference = judge.reference
    low, high = row.ts_init - HL_MATCH_BOUND_NS, row.ts_init + HL_MATCH_BOUND_NS
    for index in range(bisect_left(keys, low), bisect_right(keys, high)):
        if index not in judge.consumed and reference.updates[index].value == row.value:
            judge.consumed.add(index)
            return EXACT
    frames = reference.frames
    if any(frames.values[i] == row.value for i in frames.window(low, high)):
        return AGREE_STATE
    return REFERENCE_UNAVAILABLE if reference.recording.in_gap(row.ts_init) else UNMATCHED


def match_poll_window(rows: Iterable[StoredValue], judge: RowJudge) -> None:
    """
    Bybit open interest: a row is `agree_state` when its value equals the WS state at some venue
    time in `[ts_event - OI_POLL_WINDOW_NS, ts_event]` (the poll read the venue's state before its
    response came back); `unmatched` otherwise; `reference_unavailable` when the state is unknown
    over the whole window.
    """
    reference = judge.reference
    for row in sorted(rows, key=lambda r: (r.ts_event, r.ts_init)):
        verdict = judge.prelude(row, ts_is_init)
        if verdict is None:
            verdict = _window_verdict(row, reference)
        judge.add(verdict, row)


def _window_verdict(row: StoredValue, reference: TypeReference) -> str:
    """Classify a polled row: a recorded state in the window first; a gap decides a miss."""
    low = row.ts_event - OI_POLL_WINDOW_NS
    first = reference.state(low)
    later = [reference.frames.values[i] for i in reference.frames.window(low + 1, row.ts_event)]
    states = ([first] if first is not None else []) + later
    if any(agrees(row.value, state) for state in states):
        return AGREE_STATE
    if not states or reference.recording.in_gap(row.ts_event):
        return REFERENCE_UNAVAILABLE
    return UNMATCHED


def classify_updates(
    reference: TypeReference, consumed: set[int], day: range, feed_loss: Intervals
) -> tuple[Counter[str], list[str]]:
    """
    Classify every reference update keyed in `day` (ns): `stored` when a row consumed it, its hint
    (`unchanged`, `next_time_only`) when the collector is not meant to store it, else
    `reference_unavailable` in a recorder gap, `not_stored_explained` in a coverage run of a
    `FEED_LOSS_REASONS` reason (`feed_loss`: seconds), `not_stored` otherwise.
    """
    classes: Counter[str] = Counter()
    examples: list[str] = []
    for index, update in enumerate(reference.updates):
        if update.key not in day:
            continue
        verdict = _update_verdict(index, update, reference, consumed, feed_loss)
        classes[verdict] += 1
        if verdict in FAILING_UPDATES and len(examples) < EXAMPLES:
            examples.append(f"{verdict}@{update.key}: reference {_show(update.value)}")
    return classes, examples


def _update_verdict(
    index: int, update: Update, reference: TypeReference, consumed: set[int], feed_loss: Intervals
) -> str:
    if index in consumed:
        return STORED
    if update.hint != EXPECTED:
        return update.hint
    if reference.recording.in_gap(update.key):
        return REFERENCE_UNAVAILABLE
    if feed_loss.contains(update.key // NS_PER_S):
        return NOT_STORED_EXPLAINED
    return NOT_STORED


# --- REST agreement ------------------------------------------------------------------------------


def rest_keyed(poll: Poll, field: str, reference: TypeReference) -> str:
    """
    Judge a good Bybit poll's field against the WS state at the response's `time`: `agree_key` equal to
    it, `agree_bracket` equal to the update just before or just after it, `between_pushes`
    neither, `unaligned` when the state is unknown or the time lies in a recorder gap.
    """
    value = (poll.values[field],)
    frames = reference.frames
    if reference.recording.in_gap(poll.key):
        return UNALIGNED
    state = frames.state_at(poll.key, reference.recording)
    if state is None:
        return UNALIGNED
    if state == value:
        return AGREE_KEY
    index = frames.last_at(poll.key)
    neighbours = [
        frames.values[i] for i in (index - 1, index + 1) if _usable(frames, i, poll.key, reference)
    ]
    return AGREE_BRACKET if value in neighbours else BETWEEN_PUSHES


def _usable(frames: Timeline, index: int, at: int, reference: TypeReference) -> bool:
    """
    Whether a bracket neighbour belongs to the same recorded stretch as the state at `at`: it
    exists, lies in no recorder gap, and no reset separates it from `at` -- the receipt-vs-reset
    rule of `Timeline.state_at`, applied to the update before (received after the reset in force
    at `at`) and the update after (no reset between `at` and its receipt).
    """
    if not 0 <= index < len(frames.keys) or reference.recording.in_gap(frames.keys[index]):
        return False
    recording = reference.recording
    reset = recording.last_reset(at)
    if frames.keys[index] <= at:
        return reset is None or frames.receipts[index] >= reset
    return recording.last_reset(frames.receipts[index]) == reset


def rest_by_receive(poll: Poll, field: str, reference: TypeReference) -> str:
    """
    Judge a good Hyperliquid poll's field: `agree` when a frame received in `[sent_ns - bound, recv_ns +
    bound]` held it; `unaligned` in a recorder gap; `between_pushes` otherwise.
    """
    value = poll.values[field]
    frames = reference.frames
    low, high = poll.sent_ns - HL_MATCH_BOUND_NS, poll.recv_ns + HL_MATCH_BOUND_NS
    if any(frames.values[i][0] == value for i in frames.window(low, high)):
        return AGREE
    return UNALIGNED if reference.recording.in_gap(poll.recv_ns) else BETWEEN_PUSHES


def reference_state(rest: Mapping[str, int], rows: int) -> str:
    """
    `unvalidated` when polls were judged and none agreed, or when rows exist and no poll was
    judged; `validated` otherwise (no rows and no poll: nothing to validate).
    """
    judged = sum(rest.get(name, 0) for name in _JUDGED)
    agreeing = sum(rest.get(name, 0) for name in _AGREEING)
    if (judged and not agreeing) or (rows and not judged):
        return UNVALIDATED
    return VALIDATED


# --- open interest poll coverage -----------------------------------------------------------------


@dataclass(frozen=True)
class PollCoverage:
    """
    Bybit open interest's coverage: the configured period, the polls a whole day expects, the
    rows, the spacings over 3/2 of the period (from the day's start and to its end included), and
    how many of them `restart` coverage runs explain (not failing): a gap is explained only when,
    with every restart span subtracted, no uncovered stretch of it still exceeds 3/2 of the period.
    """

    period_s: int
    expected: int
    rows: int
    gaps: int
    gaps_explained: int
    examples: tuple[str, ...]

    @property
    def failing(self) -> int:
        return self.gaps - self.gaps_explained


def longest_uncovered(spans: Intervals, low: int, high: int) -> int:
    """Return the longest stretch of `[low, high]` no span covers (ns)."""
    longest, cursor = 0, low
    for index in range(max(0, bisect_right(spans.starts, low) - 1), len(spans.starts)):
        start, end = spans.starts[index], spans.ends[index]
        if start > high:
            break
        if end < cursor:
            continue
        longest = max(longest, start - cursor)
        cursor = max(cursor, end)
    return max(longest, high - cursor)


def poll_coverage(
    ts_events: Sequence[int], day: range, period_s: int, restarts: Intervals
) -> PollCoverage:
    """Judge the spacing of the day's rows (`ts_events`, ns) against the poll period."""
    period_ns = period_s * NS_PER_S
    edges = [day.start, *sorted(ts_events), day.stop]
    gaps = explained = 0
    examples: list[str] = []
    for low, high in pairwise(edges):
        if (high - low) * POLL_GAP_DENOMINATOR <= POLL_GAP_NUMERATOR * period_ns:
            continue
        gaps += 1
        uncovered = longest_uncovered(restarts, low, high)
        if uncovered * POLL_GAP_DENOMINATOR <= POLL_GAP_NUMERATOR * period_ns:
            explained += 1
        elif len(examples) < EXAMPLES:
            examples.append(f"poll_gap {low}..{high} ({(high - low) // NS_PER_S} s)")
    expected = (day.stop - day.start) // period_ns
    return PollCoverage(period_s, expected, len(ts_events), gaps, explained, tuple(examples))


# --- labels --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FileLabel:
    """One mark/index file with rows in the day: name, `price_precision` label, first/last row."""

    name: str
    label: int
    first_ts_init: int
    last_ts_init: int


def label_check(
    files: Sequence[FileLabel], precisions_over: Callable[[int, int], set[int]]
) -> tuple[tuple[int, ...], int, tuple[str, ...]]:
    """
    Return the day's distinct labels, how many files carry a label other than the `price_precision`
    of any instrument definition in force over their `[first, last]` row (`precisions_over`: empty
    without a definition, which the definition check fails on its own) and those files' examples.
    """
    labels = tuple(sorted({file.label for file in files}))
    wrong = []
    for file in files:
        other = sorted(precisions_over(file.first_ts_init, file.last_ts_init) - {file.label})
        if other:
            wrong.append(f"{file.name}: label {file.label}, definition {other}")
    return labels, len(wrong), tuple(wrong[:EXAMPLES])


# --- the report ----------------------------------------------------------------------------------


def frozen_counts(counts: Mapping[str, int], names: Iterable[str]) -> Mapping[str, int]:
    return MappingProxyType({name: counts.get(name, 0) for name in names})


@dataclass(frozen=True)
class TypeReport:
    """
    One instrument-type's result. It fails on any failing row or update count, more than one
    label or a label other than the definition's, an unexplained open-interest poll gap, or an
    unvalidated reference.
    """

    kind: str
    ratio: str
    ts_rule: str
    rest: Mapping[str, int]
    rows: int
    row_classes: Mapping[str, int]
    updates: Mapping[str, int]
    coverage: PollCoverage | None
    labels: tuple[int, ...] | None
    label_vs_definition: int
    examples: tuple[str, ...]

    @property
    def reference(self) -> str:
        return reference_state(self.rest, self.rows)

    @property
    def failing(self) -> int:
        rows = sum(self.row_classes.get(name, 0) for name in FAILING_ROWS)
        updates = sum(self.updates.get(name, 0) for name in FAILING_UPDATES)
        labels = int(self.labels is not None and len(self.labels) > 1) + self.label_vs_definition
        coverage = self.coverage.failing if self.coverage is not None else 0
        return rows + updates + labels + coverage

    @property
    def passed(self) -> bool:
        return self.failing == 0 and self.reference == VALIDATED


@dataclass(frozen=True)
class InstrumentDerivs:
    """One plan instrument: its types (none for spot) and its definition verdicts."""

    instrument_id: str
    types: tuple[TypeReport, ...]
    definitions: DefinitionReport

    @property
    def passed(self) -> bool:
        return self.definitions.passed and all(report.passed for report in self.types)


@dataclass(frozen=True)
class SpotReport:
    """
    Rows with `ts_event` in the day under a derivative type for any `-SPOT.BYBIT` id: `rows` maps
    `<type directory>/<id>` to its count, every plan spot id's four entries included (0 rows).
    Every such row is `fabricated`: spot has no mark, index, funding or open interest.
    """

    rows: Mapping[str, int]

    @property
    def fabricated(self) -> int:
        return sum(self.rows.values())


@dataclass(frozen=True)
class DerivsDayReport:
    """
    A venue's checked day: it passes only when every instrument does, no spot row is fabricated,
    the coverage record exists and no raw reference hour of the day is missing.
    `oi_poll_seconds`: Bybit's configured open-interest poll period (None on other venues).
    """

    venue: str
    day: str
    oi_poll_seconds: int | None
    coverage_file: str
    coverage_present: bool
    missing_raw_files: tuple[str, ...]
    truncated_neighbour_files: tuple[str, ...]
    instruments: tuple[InstrumentDerivs, ...]
    spot: SpotReport | None

    @property
    def passed(self) -> bool:
        inputs_whole = self.coverage_present and not self.missing_raw_files
        spot_clean = self.spot is None or self.spot.fabricated == 0
        return inputs_whole and spot_clean and all(r.passed for r in self.instruments)
