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
Per-instrument derivatives, liquidation and traded-flow state behind the ranking row's Story 33.4
fields (`DERIVS_FIELDS`, appended after every pre-33.4 key; `docs/DATA_DICTIONARY.md` §3.2/§3.3).

Inputs: `derivs:raw` (`kernel.derivs_wire.DerivsTick`: mark, index, funding, open interest),
`liquidations:raw` (`kernel.liquidation.Liquidation`), the traded volume and last close of every
`snapshots:raw` second, and the one-time catalog backfill (25 h of open interest, 1 h of
liquidations, the price backfill's seconds). Every value is held as an exact `Decimal` and every
formula runs in `Decimal`; a field becomes a `float` only in the row `DerivsState.fields` returns
(DATA-04). `basis_bps`/`funding_annualised` are the kernel's one formulas (SSOT-02).

Null vs 0: a missing input is None, never 0 -- an id with no liquidation feed
(`kernel.liquidation.has_liquidation_feed`) has every `liq_*`/`forced_share_1h` None, a ratio with a
zero denominator is None, and a change whose base the series does not reach is None (so is a
percent change, `oi_change_*_pct`, whose base is 0).

Pure (ranking/domain): no clock, no I/O, no ledger -- a command returns what to ledger.

Known limits (each names its upgrade path):
- funding, mark and index carry no staleness bound: their venue cadence was never measured, so a
  feed that stopped keeps its last value until the instrument ages out. Upgrade path: capture's
  per-feed status (`collector:status`) consumed here, or a bound measured per venue.
- the liquidation window straddling the capture's start (or a liquidation-socket outage) reads as 0
  for the part it did not see, not None: ranking keeps no feed-start record. Upgrade path: the
  candle store's `liquidation_feed_since` rule (D-160) read once at backfill.
- the hourly volume windows are whole minutes: the oldest, partial minute of a window is left out
  (a window up to one minute short), and a capture outage inside one reads as no trading.
- the hourly volume backfill misses the seconds the collector published before ranking subscribed
  to `snapshots:raw` but had not yet flushed to the catalog: they are in neither source (never
  received live, not yet archived when the backfill read ran), so `relative_volume` and
  `forced_share_1h` are understated by up to one capture flush interval of volume during the first
  hour after a ranking start (audit D-169). Upgrade path: run the backfill read one flush interval
  after subscribing (`TradedVolume.backfill` already keeps only seconds before the first live one).
- `basis_ml_bps` compares the mark to the last traded close only while that close is at most
  `LAST_CLOSE_MAX_AGE_NS` old (an untraded instrument's close is not today's price); the bound is
  borrowed from `OI_MAX_AGE_NS`, not measured per instrument. Upgrade path: a bound from the
  instrument's own trade cadence.
"""

from dataclasses import dataclass
from decimal import Decimal

from kernel.derivs_wire import FUNDING
from kernel.derivs_wire import INDEX
from kernel.derivs_wire import MARK
from kernel.derivs_wire import OI
from kernel.derivs_wire import DerivsTick
from kernel.indicators import basis_bps
from kernel.indicators import funding_annualised
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from kernel.venues import market_kind

from ranking.domain.price_series import PriceRange


MINUTE_NS: int = 60 * 1_000_000_000
HOUR_NS: int = 60 * MINUTE_NS

# The open-interest series covers 25 h: the 24 h change plus the backfill margin, as the price
# series does (`price_series.PRICE_LOOKBACK_HOURS`).
OI_LOOKBACK_NS: int = 25 * HOUR_NS

# The most an open-interest point may lie before the time it stands for -- the latest before now,
# a change's base before its target -- and still be that time's value: three of Bybit's 300 s REST
# polls (`capture/venues/bybit/config.toml`'s `open_interest_poll_seconds`, dYdX's default too;
# Hyperliquid's comes over the WebSocket, far faster). Past it the value is None: a stalled poll
# or a gap at the base never reads as current (DATA-01).
OI_MAX_AGE_NS: int = 900 * 1_000_000_000

# The most the last traded close may lie before now and still be the mark-last basis's reference:
# the open-interest bound's 15 minutes (`OI_MAX_AGE_NS`). An older close (an instrument that has not
# traded since) is no current last price, so the basis is None, never a stale comparison.
LAST_CLOSE_MAX_AGE_NS: int = OI_MAX_AGE_NS

# The liquidation fields' window.
LIQUIDATION_WINDOW_NS: int = HOUR_NS

# `relative_volume`'s denominator is the mean hourly volume over at most the trailing 24 h, and the
# ratio is None until the instrument's traded seconds reach back 2 h: under that, the "mean" would
# be little more than the last hour itself.
RELATIVE_VOLUME_MEAN_NS: int = 24 * HOUR_NS
RELATIVE_VOLUME_MIN_SPAN_NS: int = 2 * HOUR_NS

# The traded-volume buckets kept: the 24 h mean plus the backfill margin.
VOLUME_LOOKBACK_NS: int = 25 * HOUR_NS

# The rank row's and slow row's Story 33.4 keys, in their published order (AD-D12: appended).
DERIVS_FIELDS: tuple[str, ...] = (
    "funding_rate",
    "funding_annualised",
    "next_funding_ns",
    "open_interest",
    "oi_change_1h",
    "oi_change_24h",
    "basis_mi_bps",
    "basis_ml_bps",
    "liq_long_1h",
    "liq_short_1h",
    "liq_notional_1h",
    "liq_ratio_1h",
    "forced_share_1h",
    "relative_volume",
    "high_24h",
    "low_24h",
    "range_position_24h",
    "oi_change_1h_pct",
    "oi_change_24h_pct",
)


def units_decimal(units: int, precision: int) -> Decimal:
    """Return `units * 10^-precision` exactly (a stored integer count at its precision)."""
    return Decimal(units).scaleb(-precision)


def has_open_interest(instrument_id: str) -> bool:
    """Whether the instrument has open interest at all: perpetuals only (spot has none)."""
    return market_kind(instrument_id) == "perp"


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    return numerator / denominator if denominator else None


def _percent_change(latest: Decimal | None, base: Decimal | None) -> Decimal | None:
    """
    Return `(latest - base) / base * 100` exactly in `Decimal`; None when either is missing or the
    base is 0 (a percent of nothing is undefined, never 0 or infinity).
    """
    if latest is None or base is None:
        return None
    ratio = _ratio(latest - base, base)
    return None if ratio is None else ratio * 100


class OpenInterestSeries:
    """
    One instrument's open interest over `OI_LOOKBACK_NS`.

    Invariants: at most one point per minute, the newest `ts_event` winning (`add`), so the series
    is bounded at 25 x 60 points whatever the feed's rate; nothing older than `OI_LOOKBACK_NS` before
    the newest point is kept; one `ts_event` delivered twice with different values is refused and
    reported, never overwritten -- the backfill and the live feed meeting on the same point agree
    or are ledgered (DATA-07).
    """

    def __init__(self) -> None:
        self._by_minute: dict[int, tuple[int, Decimal]] = {}

    def add(self, ts_event: int, value: Decimal) -> str | None:
        """Keep the point unless its minute holds a newer one; return a disagreement, else None."""
        minute = ts_event // MINUTE_NS
        held = self._by_minute.get(minute)
        if held is not None and held[0] == ts_event and held[1] != value:
            return f"open interest at ts_event {ts_event} delivered as {held[1]} and {value}"
        if held is not None and held[0] >= ts_event:
            return None
        self._by_minute[minute] = (ts_event, value)
        if held is None:
            self._prune()
        return None

    def _prune(self) -> None:
        newest = max(ts for ts, _ in self._by_minute.values())
        oldest_minute = (newest - OI_LOOKBACK_NS) // MINUTE_NS
        for minute in [m for m in self._by_minute if m < oldest_minute]:
            del self._by_minute[minute]

    def _as_of(self, points: list[tuple[int, Decimal]], at_ns: int) -> Decimal | None:
        """Return the newest value at or before `at_ns`, if within `OI_MAX_AGE_NS` of it."""
        before = [(ts, value) for ts, value in points if ts <= at_ns]
        if not before or at_ns - before[-1][0] > OI_MAX_AGE_NS:
            return None
        return before[-1][1]

    def fields(self, now_ns: int) -> dict[str, float | None]:
        """
        Return `open_interest`, its absolute 1 h / 24 h changes and the same changes as a percent of
        the base (`oi_change_*_pct`, Story 33.7), each None per the rules.
        """
        points = sorted(self._by_minute.values())
        latest = self._as_of(points, now_ns)
        changes: dict[str, float | None] = {}
        for name, hours in (("oi_change_1h", 1), ("oi_change_24h", 24)):
            base = self._as_of(points, now_ns - hours * HOUR_NS)
            changes[name] = None if latest is None or base is None else float(latest - base)
            changes[f"{name}_pct"] = _float(_percent_change(latest, base))
        return {"open_interest": _float(latest), **changes}


@dataclass(frozen=True)
class _HeldLiquidation:
    ts_event: int
    side: LiquidatedSide
    size: Decimal
    notional: Decimal


class LiquidationWindow:
    """
    One instrument's liquidations over the last `LIQUIDATION_WINDOW_NS`.

    Invariant: each venue event is counted once, keyed on its `venue_event_id` -- the 1 h backfill
    and the live feed overlap, and Redis may redeliver -- and `expire` drops every event older than
    the window, so the held set is bounded by one hour's events.
    """

    def __init__(self) -> None:
        self._events: dict[str, _HeldLiquidation] = {}

    def add(self, liquidation: Liquidation) -> None:
        precision = liquidation.price_precision + liquidation.size_precision
        self._events.setdefault(
            liquidation.venue_event_id,
            _HeldLiquidation(
                ts_event=liquidation.ts_event,
                side=liquidation.side,
                size=units_decimal(liquidation.size_units, liquidation.size_precision),
                notional=units_decimal(liquidation.notional_units(), precision),
            ),
        )

    def expire(self, now_ns: int) -> None:
        cutoff = now_ns - LIQUIDATION_WINDOW_NS
        self._events = {k: e for k, e in self._events.items() if e.ts_event > cutoff}

    def totals(self, now_ns: int) -> tuple[Decimal, Decimal, Decimal]:
        """(long size, short size, notional at the bankruptcy price) inside the window."""
        cutoff = now_ns - LIQUIDATION_WINDOW_NS
        held = [e for e in self._events.values() if cutoff < e.ts_event <= now_ns]
        long_size = sum((e.size for e in held if e.side is LiquidatedSide.LONG), Decimal(0))
        short_size = sum((e.size for e in held if e.side is LiquidatedSide.SHORT), Decimal(0))
        return long_size, short_size, sum((e.notional for e in held), Decimal(0))


class TradedVolume:
    """
    One instrument's traded volume (buy + sell size) in minute buckets over `VOLUME_LOOKBACK_NS`.

    Invariant: each traded second is counted once. Live seconds must strictly ascend (`add_live`
    refuses a repeat or an older second -- a Redis redelivery -- and reports it), and the backfill
    adds only seconds before the first live one, so the archive and the feed never both count a
    second. Bounded: at most 25 x 60 buckets, pruned whenever a new one opens.
    """

    def __init__(self) -> None:
        self._by_minute: dict[int, Decimal] = {}
        self._first_ns: int | None = None
        self._first_live_ns: int | None = None
        self._last_live_ns: int | None = None

    def add_live(self, ts_event: int, volume: Decimal) -> str | None:
        if self._last_live_ns is not None and ts_event <= self._last_live_ns:
            return (
                f"out-of-order traded second DROPPED from the hourly volume "
                f"(ts_event={ts_event} <= last={self._last_live_ns})"
            )
        if self._first_live_ns is None:
            self._first_live_ns = ts_event
        self._last_live_ns = ts_event
        self._add(ts_event, volume)
        return None

    def backfill(self, seconds: list[tuple[int, Decimal]]) -> None:
        for ts_event, volume in seconds:
            if self._first_live_ns is None or ts_event < self._first_live_ns:
                self._add(ts_event, volume)

    def _add(self, ts_event: int, volume: Decimal) -> None:
        self._first_ns = ts_event if self._first_ns is None else min(self._first_ns, ts_event)
        minute = ts_event // MINUTE_NS
        opened = minute not in self._by_minute
        self._by_minute[minute] = self._by_minute.get(minute, Decimal(0)) + volume
        if opened:
            oldest_minute = (max(self._by_minute) * MINUTE_NS - VOLUME_LOOKBACK_NS) // MINUTE_NS
            for stale in [m for m in self._by_minute if m < oldest_minute]:
                del self._by_minute[stale]

    def within(self, now_ns: int, window_ns: int) -> Decimal:
        """Return the volume of the whole minutes starting in `[now_ns - window_ns, now_ns]`."""
        start = now_ns - window_ns
        return sum(
            (v for m, v in self._by_minute.items() if start <= m * MINUTE_NS <= now_ns),
            Decimal(0),
        )

    def relative(self, now_ns: int) -> Decimal | None:
        """
        Return the last hour's volume over the mean hourly volume of the trailing 24 h (or of the span
        held, when shorter); None under `RELATIVE_VOLUME_MIN_SPAN_NS` of history or with no volume.
        """
        if self._first_ns is None or now_ns - self._first_ns < RELATIVE_VOLUME_MIN_SPAN_NS:
            return None
        span_ns = min(now_ns - self._first_ns, RELATIVE_VOLUME_MEAN_NS)
        mean = self.within(now_ns, span_ns) * HOUR_NS / span_ns
        return _ratio(self.within(now_ns, HOUR_NS), mean)


class DerivsState:
    """
    One instrument's derivatives, liquidation and flow inputs.

    Invariant: every held value is the exact `Decimal` its source carried; mark, index and funding
    hold the newest tick by `ts_event` (`ingest`), an older one is refused and reported.
    `last_seen_ns` is the arrival time of its newest input, for `RankingBoard.age_out`.
    """

    def __init__(self, last_seen_ns: int) -> None:
        self.last_seen_ns = last_seen_ns
        self.mark: DerivsTick | None = None
        self.index: DerivsTick | None = None
        self.funding: DerivsTick | None = None
        self.last_close: Decimal | None = None
        self.last_close_ns: int | None = None  # the close's `ts_event`
        self.open_interest = OpenInterestSeries()
        self.liquidations = LiquidationWindow()
        self.volume = TradedVolume()

    def note_close(self, ts_event: int, close: Decimal) -> None:
        """Keep the newest traded close (the mark-last basis's reference) and its time."""
        self.last_close, self.last_close_ns = close, ts_event

    def _current_close(self, now_ns: int) -> Decimal | None:
        """Return the last close while within `LAST_CLOSE_MAX_AGE_NS` of `now_ns`, else None."""
        if self.last_close_ns is None or now_ns - self.last_close_ns > LAST_CLOSE_MAX_AGE_NS:
            return None
        return self.last_close

    def ingest(self, tick: DerivsTick) -> str | None:
        """Apply one `derivs:raw` tick; return what to ledger (a refused tick), else None."""
        if tick.kind == OI:
            return self.open_interest.add(tick.t, tick.value)
        attribute = {MARK: "mark", INDEX: "index", FUNDING: "funding"}[tick.kind]
        held: DerivsTick | None = getattr(self, attribute)
        if held is not None and tick.t < held.t:
            return f"out-of-order {tick.kind} tick DROPPED (t={tick.t} < held t={held.t})"
        setattr(self, attribute, tick)
        return None

    def fields(
        self, now_ns: int, liquidation_feed: bool, price_range: PriceRange | None
    ) -> dict[str, float | int | None]:
        """Return the `DERIVS_FIELDS` row, in order, floats only here."""
        row = {
            **self._funding_fields(),
            **self.open_interest.fields(now_ns),
            **self._basis_fields(now_ns),
            **self._liquidation_fields(now_ns, liquidation_feed),
            "relative_volume": _float(self.volume.relative(now_ns)),
            **_range_fields(price_range),
        }
        return {name: row[name] for name in DERIVS_FIELDS}

    def _funding_fields(self) -> dict[str, float | int | None]:
        if self.funding is None:
            return {"funding_rate": None, "funding_annualised": None, "next_funding_ns": None}
        annualised = funding_annualised(self.funding.value, self.funding.interval)
        return {
            "funding_rate": float(self.funding.value),
            "funding_annualised": _float(annualised),
            "next_funding_ns": self.funding.next_funding_ns,
        }

    def _basis_fields(self, now_ns: int) -> dict[str, float | None]:
        mark = None if self.mark is None else self.mark.value
        index = None if self.index is None else self.index.value
        close = self._current_close(now_ns)
        return {
            "basis_mi_bps": None
            if mark is None or index is None
            else _float(basis_bps(mark, index)),
            "basis_ml_bps": None
            if mark is None or close is None
            else _float(basis_bps(mark, close)),
        }

    def _liquidation_fields(self, now_ns: int, liquidation_feed: bool) -> dict[str, float | None]:
        if not liquidation_feed:
            return dict.fromkeys(
                (
                    "liq_long_1h",
                    "liq_short_1h",
                    "liq_notional_1h",
                    "liq_ratio_1h",
                    "forced_share_1h",
                )
            )
        long_size, short_size, notional = self.liquidations.totals(now_ns)
        forced = long_size + short_size
        return {
            "liq_long_1h": float(long_size),
            "liq_short_1h": float(short_size),
            "liq_notional_1h": float(notional),
            "liq_ratio_1h": _float(_ratio(long_size, forced)),
            "forced_share_1h": _float(_ratio(forced, self.volume.within(now_ns, HOUR_NS))),
        }


def _range_fields(price_range: PriceRange | None) -> dict[str, float | None]:
    """
    `high_24h`/`low_24h` as stored and `range_position_24h` = (last - low) / (high - low), computed
    in `Decimal` from each float's shortest repr (the decimal the stored units encode); None on a
    flat range, where the position is undefined.
    """
    if price_range is None:
        return {"high_24h": None, "low_24h": None, "range_position_24h": None}
    high, low, last = (Decimal(repr(v)) for v in price_range)
    return {
        "high_24h": price_range.high,
        "low_24h": price_range.low,
        "range_position_24h": _float(_ratio(last - low, high - low)),
    }
