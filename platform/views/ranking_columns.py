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
Rankings-table column metadata (platform/CLAUDE.md SSOT-04): the one place that says which
columns the web rankings page renders from each `ranking_engine`-published rankings:live rank
entry, in what order, with what label and text formatting. The page's TS mirror
(`frontend/src/pages/RankingsPage.tsx`) is held to the same `(key, label)` sequence by
`data_api/tests/test_ranking_columns_mirror.py`.

The page's pinned identity columns -- Rank, Symbol, Exchange and Instrument (Story 29.1) -- are
not metric columns and are deliberately outside `RANKING_COLS` and the mirror: they read the rank
entry's `instrument_id`, `venue`, `symbol` and `market` fields, rendered on both tabs, with
Symbol and Exchange sortable and filterable on the page.

Story 25.1a removed the `color_fn` member and `POSITIVE_COLOR`/`NEGATIVE_COLOR`: their only
reader was bot_tui's Coins pane (deleted, rankings are web-only), and the web table never
coloured cells from them.

The Technicals tab's per-coin values are the same kind of read model (Story 24.2 moved them out of
`data_api/routes/rankings.py`): `technicals_values` gives each requested column's value at the latest
closed bar (Story 27.7: a still-forming newest bucket is left out) for one
instrument through the chart's own indicator dispatch (`views.indicator_picker`), over candles
from the candle store's query service, else the archive's one seconds -> bars fold -- so a column
always equals what that coin's chart shows at that bar. It computes no indicator and no ranking metric itself.
"""

from collections.abc import Callable
from collections.abc import Sequence
from typing import Any
from typing import Protocol

from candles.application import queries
from candles.application.forming import bars_from_rows
from candles.domain.fold import archive_liquidations
from kernel import catalog_files
from kernel.liquidation import has_liquidation_feed
from kernel.venues import venue_of
from observability import error_ledger

from views import indicator_picker
from views.catalog_reads import liquidation_feed_start


# Each entry: (store_key, header_label, format_fn).
# Reorder, add, or remove rows here to control what's shown and how -- then port the change to
# the TS mirror (the mirror test fails until you do).
#
# Known limit: since Story 25.1a no Python code calls `format_fn` -- the web page formats with
# its own TS functions, and the mirror test holds only `(key, label)` equal, so a decimals change
# on one side alone goes unnoticed. `format_fn` stays as the recorded text format for each column
# (Story 25.1a's spec keeps the 3-tuple). Upgrade path: replace it with a declarative spec
# (kind + decimals + sign) that both sides read and the mirror test compares, or serve the column
# metadata to the page over `/api/rankings` so there is no mirror at all.
# Unit contract for direct consumers of /api/rankings and /data/live/{id} (Story 31.3: stated as
# they are -- nothing normalises them anywhere, server- or client-side): "cvd" and "volume_delta"
# are raw base-token units (buy minus sell size), "spread" and "microprice_lean" raw price units
# (quote currency per base token). A reader wanting USD or bps must scale by the row's own "price"
# (the mid) itself.
#
# The three volatilities are three different numbers and their labels say which (Story 31.3):
# "volatility" is the population stdev of trade-close pct returns over the last 24 h
# (`ranking.domain.metrics.price_stats_from_series`), "volatility_score" the sample stdev of
# mid pct returns over the last hour (`ranking.domain.volatility.VolatilityTracker`, the
# volatility-mode sort key), and "volatility_fast" (coin page only, not a column here) the sample
# stdev of mid pct returns over the last 300 snapshots.
#
# Story 33.7's columns (ranking's Story 33.4 fields plus its 33.7 OI percent changes, each shown as
# published -- the page only scales a value for display, it computes none):
# - "open_interest": the venue's own open-interest units (contracts or base tokens, per venue).
#   Known limit: those units differ per coin, so sorting or filtering OI across rows orders
#   incomparable numbers (1,000,000 DOGE above 50,000 BTC); the filter label says "venue units".
#   Upgrade path: `ranking` publishes an OI notional (OI x mark, SSOT-02) as its own column;
# - "oi_change_1h_pct"/"oi_change_24h_pct": percent (ranking computes them in `Decimal`);
# - "funding_rate": a fraction per funding interval, shown x100 as a percent (4 decimals);
# - "basis_mi_bps": mark minus index in bps, signed;
# - "liq_notional_1h": quote currency (size x bankruptcy price), shown in thousands (`K`);
# - "liq_ratio_1h" (long share of the liquidated size) and "forced_share_1h" (liquidated size
#   over traded size): fractions shown x100 as a percent;
# - "relative_volume": a ratio (last hour over the mean hourly volume), shown with a `x`;
# - "range_position_24h": a fraction (0 = the 24 h low, 1 = the high) shown x100 as a percent with
#   a small inline bar.
# A filter value is typed in the row's raw units (a fraction for funding, never the shown percent).
# Spot dash: the web page applies it, not this module -- on a row whose `market` is "spot" the page
# shows the dash in every `DERIVATIVE_COLUMN_KEYS` cell whatever its value (spot has no
# derivatives), and its sort and filters read it as missing. Relative volume and the 24 h range are
# not derivatives: spot shows them. Each Story 33.7 `format_fn` is only the recorded text format of
# a present value: no code calls it, and None (a missing value, shown as the dash) is never passed.
RANKING_COLS: list[tuple[str, str, Callable[[Any], str]]] = [
    ("ofi_10_z", "OFI10z", lambda v: f"{v:+.2f}"),
    ("obi_10", "OBI10", lambda v: f"{v:.3f}"),
    ("obi_5", "OBI5", lambda v: f"{v:.3f}"),
    ("obi_3", "OBI3", lambda v: f"{v:.3f}"),
    ("cvd", "CVD", lambda v: f"{v:+.2f}"),
    ("spread", "Spread", lambda v: f"{v:.6f}"),
    ("volume_delta", "Vol d 60s", lambda v: f"{v:+.2f}"),
    ("price", "Price", lambda v: f"{v:.4f}"),
    ("pct_1h", "1h %", lambda v: f"{v:+.2f}%"),
    ("pct_24h", "24h %", lambda v: f"{v:+.2f}%"),
    ("pct_1w", "1w %", lambda v: f"{v:+.2f}%"),
    ("pct_1m", "1m %", lambda v: f"{v:+.2f}%"),
    ("volatility", "Vol 24h \u03c3 (trade closes)", lambda v: f"{v:.6f}"),
    ("volatility_score", "Vol 1h \u03c3 (mids)", lambda v: f"{v:.6f}" if v is not None else "—"),
    ("volume24h", "Vol24h", lambda v: f"{v / 1e6:.3f}M"),
    ("open_interest", "OI", lambda v: f"{v:.2f}"),
    ("oi_change_1h_pct", "OI \u03941h %", lambda v: f"{v:+.2f}%"),
    ("oi_change_24h_pct", "OI \u039424h %", lambda v: f"{v:+.2f}%"),
    ("funding_rate", "Funding", lambda v: f"{v * 100:.4f}%"),
    ("basis_mi_bps", "Basis (bps)", lambda v: f"{v:+.2f}"),
    ("liq_notional_1h", "Liq 1h", lambda v: f"{v / 1e3:.1f}K"),
    ("liq_ratio_1h", "Liq L/S", lambda v: f"{v * 100:.1f}%"),
    ("forced_share_1h", "Forced %", lambda v: f"{v * 100:.1f}%"),
    ("relative_volume", "Rel vol", lambda v: f"{v:.2f}\u00d7"),
    ("range_position_24h", "24h range", lambda v: f"{v * 100:.0f}%"),
]

# The derivatives columns (Story 33.7): the page shows a spot row's dash in each, whatever its value,
# and sorts and filters it as missing. The page's `DERIVATIVE_COLUMNS` mirrors this set
# (`data_api/tests/test_ranking_columns_mirror.py`).
DERIVATIVE_COLUMN_KEYS: frozenset[str] = frozenset(
    {
        "open_interest",
        "oi_change_1h_pct",
        "oi_change_24h_pct",
        "funding_rate",
        "basis_mi_bps",
        "liq_notional_1h",
        "liq_ratio_1h",
        "forced_share_1h",
    }
)

# History-only columns (store_key, header_label): plotted on /history/{id}'s per-coin
# 31-day charts from metrics_store rows, but deliberately NOT in RANKING_COLS -- that
# list renders the cross-instrument *ranking* table, so anything here is
# single-coin-page-only. "rank" is here because live rankings:live rank entries never carry
# a "rank" key (row order itself is the live rank). "microprice_lean" ("u lean") is here
# because it belongs on the single-coin page only, not the cross-instrument ranking table.
_HISTORY_ONLY_COLS: list[tuple[str, str]] = [
    ("rank", "Rank"),
    ("microprice_lean", "u lean"),
]


# ---------------------------------------------------------------------------------------------
# Story 17.5: the Technicals tab's per-coin values
# ---------------------------------------------------------------------------------------------

# Recent-window replay per coin: enough bars for the slowest common indicator warm-up. The candle
# store serves any bar size cheaply (an indexed read), so 250 covers SMA(200); the Parquet fallback
# is far costlier per bar, so it keeps a smaller window (60 for 4H+, and never more than a week of raw seconds).
_TECHNICALS_STORE_BARS = 250
_TECHNICALS_BARS = 120
_FALLBACK_MAX_SPAN_S = 7 * 86_400
_TECHNICALS_WIDE_BARS = 60
# Catalog writes lag by a flush interval, so the newest candle is normally a minute or two old;
# older than this the coin's data has genuinely stopped and its value must not read as live.
_TECHNICALS_MAX_CANDLE_AGE_BARS = 5


class CatalogReadError(Exception):
    """
    A catalog read failed. Deliberately not a ValueError: pyarrow's ArrowInvalid is one, and
    would otherwise be mistaken for bad client indicator params (a whole-request 400).
    """


class TechnicalsEntry(Protocol):
    """One requested column: an indicator, its params, and the bar size it is computed on."""

    @property
    def name(self) -> str: ...

    @property
    def params(self) -> dict[str, Any]: ...

    @property
    def source(self) -> str: ...

    @property
    def bar_seconds(self) -> int: ...


def _recent_candles(
    instrument_id: str, bar_seconds: int, now_ns: int, catalog_path: str, candles_dir: str
) -> list[dict]:
    """
    Newest candles for one bar size: the SQLite candle store when it holds the coin (no Parquet
    I/O), else the slow archive read.
    """
    try:
        with queries.open_store(candles_dir, venue_of(instrument_id)) as db:
            if db is not None:
                stored = queries.window(
                    db, instrument_id, bar_seconds, 1 << 62, _TECHNICALS_STORE_BARS
                )
                if stored:
                    return stored
    except Exception as exc:
        raise CatalogReadError(str(exc)) from exc
    return _read_candles(instrument_id, bar_seconds, now_ns, catalog_path, candles_dir)


def _read_candles(
    instrument_id: str, bar_seconds: int, now_ns: int, catalog_path: str, candles_dir: str
) -> list[dict]:
    """
    Read candles the slow way, for a coin the candle store does not hold.

    Raw 1s columns aggregated to `bar_seconds`, over at most a week (MEM-01), so 4H+ columns may get
    fewer bars than the store gives. An instrument with a liquidation feed folds the window's
    archived liquidations too, bounded by the feed's start -- the candle store's persisted one,
    else the archive's first (`views.catalog_reads.liquidation_feed_start`), lowered to the rows read
    (`archive_liquidations`) -- so its bars carry the same `liq_*` the store and the `raw_1s` page
    would (null before or straddling the start, or throughout with none known; audit D-160).
    """
    bars = _TECHNICALS_BARS if bar_seconds <= 3600 else _TECHNICALS_WIDE_BARS
    span_ns = min((bars + 5) * bar_seconds, _FALLBACK_MAX_SPAN_S) * 1_000_000_000
    start_ns = now_ns - span_ns
    try:
        rows = catalog_files.query_second_ohlc(
            catalog_path, instrument_id, start_ns, now_ns, on_foreign=error_ledger.record
        )
        liquidations, since_ns = None, None
        if has_liquidation_feed(instrument_id):
            liquidations, since_ns = archive_liquidations(
                catalog_files.query_liquidations(catalog_path, instrument_id, start_ns, now_ns),
                liquidation_feed_start(catalog_path, candles_dir, instrument_id),
            )
        return bars_from_rows(rows, bar_seconds, liquidations, liquidations_since_ns=since_ns)[
            -bars:
        ]
    except Exception as exc:
        raise CatalogReadError(str(exc)) from exc


def technicals_values(
    instrument_id: str,
    entries: Sequence[TechnicalsEntry],
    now_ns: int,
    *,
    catalog_path: str,
    candles_dir: str,
) -> dict[str, float | None]:
    """
    Each requested indicator's value at the latest *closed* bar for one instrument, keyed
    `"{entry index}.{output}"`, via the chart's own `replay_entry` dispatch, each entry over its own
    column's timeframe (no indicator math here). Candles are built once per distinct bar size.

    Closed bars only (Story 27.7): the candle store's newest bucket is normally the forming one
    (the capture sink upserts it every flush), so a newest candle whose bucket `[t, t + bar)` has
    not closed at `now_ns` is dropped before the replay (`_closed_candles`). One rule for every
    column: a candlestick pattern fired on a forming bar could vanish a flush later, and an RSI on
    it would move under the reader.

    Known limit: "closed" is wall-clock only. Capture upserts seconds every flush (60 s by
    default), so for up to one flush after a bucket closes its last seconds may not be stored yet,
    and a value read then can still change once they land (a transient repaint, then cached for
    the technicals TTL). Upgrade path: close a bucket only once a capture watermark (the newest
    flushed second) has passed its end, rather than `now_ns`.

    A timeframe with no data, or whose newest closed candle is older than
    `_TECHNICALS_MAX_CANDLE_AGE_BARS` bars, contributes nothing: an honest gap, never a stale value
    shown as current (DATA-01). Raises `CatalogReadError` for a failed read, and `ValueError` when
    an entry's replay fails (bad params are client input).
    """
    keyed: dict[str, float | None] = {}
    for bar_seconds in sorted({e.bar_seconds for e in entries}):
        candles = _closed_candles(
            _recent_candles(instrument_id, bar_seconds, now_ns, catalog_path, candles_dir),
            bar_seconds,
            now_ns,
        )
        max_age_ms = _TECHNICALS_MAX_CANDLE_AGE_BARS * bar_seconds * 1000
        if not candles or now_ns // 1_000_000 - candles[-1]["t"] > max_age_ms:
            continue  # no data / stopped: an honest gap, never a stale value shown as current (DATA-01)
        group = [(i, e) for i, e in enumerate(entries) if e.bar_seconds == bar_seconds]
        keyed.update(_latest_of_group(instrument_id, bar_seconds, candles, group, candles_dir))
    return keyed


def _closed_candles(candles: list[dict], bar_seconds: int, now_ns: int) -> list[dict]:
    """
    Return `candles` (oldest first) without every candle whose bucket has not closed at `now_ns`.

    Normally only the newest bucket is still forming, but the store is read unbounded above
    (`_recent_candles`), so a capture clock ahead of this host's by more than a bar leaves several
    unclosed candles; each is dropped, never only the last.
    """
    bar_ns = bar_seconds * 1_000_000_000
    return [c for c in candles if c["t"] * 1_000_000 + bar_ns <= now_ns]


def _latest_of_group(
    instrument_id: str,
    bar_seconds: int,
    candles: list[dict],
    group: list[tuple[int, TechnicalsEntry]],
    candles_dir: str,
) -> dict[str, float | None]:
    """Return the newest candle's value of every entry of one timeframe, keyed by entry index."""
    window = indicator_picker.ReplayWindow(
        instrument_id=instrument_id,
        bar_seconds=bar_seconds,
        start_ms=candles[0]["t"],
        end_ms=candles[-1]["t"] + bar_seconds * 1000,
        candles_dir=candles_dir,
    )
    by_time, errors = indicator_picker.values_by_time(candles, [e for _, e in group], window)
    if (
        errors
    ):  # unlike the chart, one bad column fails the request: a half-filled column reads as data
        raise ValueError(next(iter(errors.values())))
    latest = by_time[candles[-1]["t"]]
    keyed: dict[str, float | None] = {}
    for index, entry in group:
        prefix = indicator_picker.indicator_id(entry.name, entry.params, entry.source) + "."
        keyed.update(
            {
                f"{index}.{k.removeprefix(prefix)}": v
                for k, v in latest.items()
                if k.startswith(prefix)
            }
        )
    return keyed
