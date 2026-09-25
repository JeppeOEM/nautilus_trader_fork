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
Shared rankings-table column metadata (platform/CLAUDE.md SSOT-03): the web dashboard and
bot_tui's Coins pane both render one row per instrument from the exact same
ranking_engine-published rankings:live rank entry -- this module is the single place
that says which columns exist, in what order, with what label and text formatting, so
neither UI can drift from the other by adding/reordering/reformatting a column alone.

`color_fn` returns a CSS hex color for the dashboard's HTML rendering (`None` = no
sign-coloring for that column). Only two colors are ever used across this whole table
-- POSITIVE_COLOR (green) and NEGATIVE_COLOR (red) -- so bot_tui's urwid renderer maps
a `color_fn` result back onto its own `pnl-pos`/`pnl-neg` palette entries by comparing
against these same two constants, rather than reimplementing each column's own
sign/threshold rule (e.g. OBI's ">0.5" boundary) a second time.

The Technicals tab's per-coin values are the same kind of read model (Story 24.2 moved them out of
`data_api/routes/rankings.py`): `technicals_values` gives each requested column's latest value for
one instrument through the chart's own indicator dispatch (`views.indicator_picker`), over candles
from the candle store's query service, else the archive's one seconds -> bars fold -- so a column
always equals what that coin's chart shows. It computes no indicator and no ranking metric itself.
"""

from collections.abc import Sequence
from typing import Any
from typing import Protocol

from candles.application import queries
from candles.application.forming import bars_from_rows
from kernel import catalog_files
from kernel.venues import venue_of

from views import indicator_picker


POSITIVE_COLOR = "#2a9d2a"
NEGATIVE_COLOR = "#c0392b"

# Each entry: (store_key, header_label, format_fn, color_fn|None).
# Reorder, add, or remove rows here to control what's shown and how -- in both UIs.
# color_fn receives the raw float value and returns a CSS color string or None.
# Unit contract for direct consumers of /api/rankings and /data/live/{id}: "cvd" and
# "volume_delta" are raw base-asset-token deltas, "spread"/"microprice_lean" are raw
# price-unit deltas -- neither is scaled by price server-side. The rankings/coin-detail
# HTML pages normalize these client-side (see the inline JS's usdFromTokens/
# bpsFromPriceUnits) using each row's own "price" field; a script hitting the JSON
# endpoints directly must do the same multiplication/division itself to get comparable
# USD/bps units.
#
# "Vol(catalog)" is deliberately disambiguated from the coin-detail page's other two
# volatility figures -- "volatility_fast" (live-tick, ~300s) and "volatility_score"
# (VolatilityTracker's cross-sectional rank, 3600s) -- rather than a bare "Vol", which
# used to collide with those on the same row/page. "catalog" matches the label already
# used for this same field on both the web coin-detail page (dashboard.py's IND_GROUPS)
# and bot_tui's coin-detail groups (app.py's _DETAIL_GROUPS) -- same field, same name,
# everywhere it appears.
RANKING_COLS: list[tuple[str, str, object, object]] = [
    (
        "ofi_10_z",
        "OFI10z",
        lambda v: f"{v:+.2f}",
        lambda v: POSITIVE_COLOR if v > 0 else NEGATIVE_COLOR,
    ),
    (
        "obi_10",
        "OBI10",
        lambda v: f"{v:.3f}",
        lambda v: POSITIVE_COLOR if v > 0.5 else NEGATIVE_COLOR,
    ),
    (
        "obi_5",
        "OBI5",
        lambda v: f"{v:.3f}",
        lambda v: POSITIVE_COLOR if v > 0.5 else NEGATIVE_COLOR,
    ),
    (
        "obi_3",
        "OBI3",
        lambda v: f"{v:.3f}",
        lambda v: POSITIVE_COLOR if v > 0.5 else NEGATIVE_COLOR,
    ),
    ("cvd", "CVD", lambda v: f"{v:+.2f}", lambda v: POSITIVE_COLOR if v > 0 else NEGATIVE_COLOR),
    ("spread", "Spread", lambda v: f"{v:.6f}", None),
    (
        "volume_delta",
        "Vol d 60s",
        lambda v: f"{v:+.2f}",
        lambda v: POSITIVE_COLOR if v > 0 else NEGATIVE_COLOR,
    ),
    ("price", "Price", lambda v: f"{v:.4f}", None),
    (
        "pct_1h",
        "1h %",
        lambda v: f"{v:+.2f}%",
        lambda v: POSITIVE_COLOR if v > 0 else NEGATIVE_COLOR,
    ),
    (
        "pct_24h",
        "24h %",
        lambda v: f"{v:+.2f}%",
        lambda v: POSITIVE_COLOR if v > 0 else NEGATIVE_COLOR,
    ),
    (
        "pct_1w",
        "1w %",
        lambda v: f"{v:+.2f}%",
        lambda v: POSITIVE_COLOR if v > 0 else NEGATIVE_COLOR,
    ),
    (
        "pct_1m",
        "1m %",
        lambda v: f"{v:+.2f}%",
        lambda v: POSITIVE_COLOR if v > 0 else NEGATIVE_COLOR,
    ),
    ("volatility", "Vol(catalog)", lambda v: f"{v:.6f}", None),
    ("volatility_score", "Vol Score", lambda v: f"{v:.6f}" if v is not None else "—", None),
    ("volume24h", "Vol24h", lambda v: f"{v / 1e6:.3f}M", None),
]

# History-only columns (store_key, header_label): plotted on /history/{id}'s per-coin
# 31-day charts from metrics_store rows, but deliberately NOT in RANKING_COLS -- that
# list is also used to render the cross-instrument *ranking* table (both the web
# dashboard's table and bot_tui's Coins pane), so anything here is single-coin-page-only.
# "rank" is here because live rankings:live rank entries never carry a "rank" key (row
# order itself is the live rank); bot_tui's Coins pane already shows rank as its own
# leading column, not sourced from this list. "microprice_lean" ("u lean") is here
# because it belongs on the single-coin page only, not the cross-instrument ranking
# table -- it's already shown on both the web and bot_tui coin-detail views.
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
    return _read_candles(instrument_id, bar_seconds, now_ns, catalog_path)


def _read_candles(
    instrument_id: str, bar_seconds: int, now_ns: int, catalog_path: str
) -> list[dict]:
    """
    Read candles the slow way, for a coin the candle store does not hold.

    Raw 1s columns aggregated to `bar_seconds`, over at most a week (MEM-01), so 4H+ columns may get
    fewer bars than the store gives.
    """
    bars = _TECHNICALS_BARS if bar_seconds <= 3600 else _TECHNICALS_WIDE_BARS
    span_ns = min((bars + 5) * bar_seconds, _FALLBACK_MAX_SPAN_S) * 1_000_000_000
    try:
        rows = catalog_files.query_second_ohlc(
            catalog_path, instrument_id, now_ns - span_ns, now_ns
        )
        return bars_from_rows(rows, bar_seconds)[-bars:]
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
    Each requested indicator's latest value for one instrument, keyed `"{entry index}.{output}"`,
    via the chart's own `replay_entry` dispatch, each entry over its own column's timeframe (no
    indicator math here). Candles are built once per distinct bar size.

    A timeframe with no data, or whose newest candle is older than
    `_TECHNICALS_MAX_CANDLE_AGE_BARS` bars, contributes nothing: an honest gap, never a stale value
    shown as current (DATA-01). Raises `CatalogReadError` for a failed read, and `ValueError` when
    an entry's replay fails (bad params are client input).
    """
    keyed: dict[str, float | None] = {}
    for bar_seconds in sorted({e.bar_seconds for e in entries}):
        candles = _recent_candles(instrument_id, bar_seconds, now_ns, catalog_path, candles_dir)
        max_age_ms = _TECHNICALS_MAX_CANDLE_AGE_BARS * bar_seconds * 1000
        if not candles or now_ns // 1_000_000 - candles[-1]["t"] > max_age_ms:
            continue  # no data / stopped: an honest gap, never a stale value shown as current (DATA-01)
        group = [(i, e) for i, e in enumerate(entries) if e.bar_seconds == bar_seconds]
        keyed.update(_latest_of_group(instrument_id, bar_seconds, candles, group))
    return keyed


def _latest_of_group(
    instrument_id: str,
    bar_seconds: int,
    candles: list[dict],
    group: list[tuple[int, TechnicalsEntry]],
) -> dict[str, float | None]:
    """Return the newest candle's value of every entry of one timeframe, keyed by entry index."""
    window = indicator_picker.ReplayWindow(
        instrument_id=instrument_id,
        bar_seconds=bar_seconds,
        start_ms=candles[0]["t"],
        end_ms=candles[-1]["t"] + bar_seconds * 1000,
    )
    by_time, errors = indicator_picker.values_by_time(candles, [e for _, e in group], window)
    if (
        errors
    ):  # unlike the chart, one bad column fails the request: a half-filled column reads as data
        raise ValueError(next(iter(errors.values())))
    latest = by_time[candles[-1]["t"]]
    keyed: dict[str, float | None] = {}
    for index, entry in group:
        prefix = indicator_picker.indicator_id(entry.name, entry.params) + "."
        keyed.update(
            {
                f"{index}.{k.removeprefix(prefix)}": v
                for k, v in latest.items()
                if k.startswith(prefix)
            }
        )
    return keyed
