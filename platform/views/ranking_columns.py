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

Story 25.1a removed the `color_fn` member and `POSITIVE_COLOR`/`NEGATIVE_COLOR`: their only
reader was bot_tui's Coins pane (deleted, rankings are web-only), and the web table never
coloured cells from them.

The Technicals tab's per-coin values are the same kind of read model (Story 24.2 moved them out of
`data_api/routes/rankings.py`): `technicals_values` gives each requested column's latest value for
one instrument through the chart's own indicator dispatch (`views.indicator_picker`), over candles
from the candle store's query service, else the archive's one seconds -> bars fold -- so a column
always equals what that coin's chart shows. It computes no indicator and no ranking metric itself.
"""

from collections.abc import Callable
from collections.abc import Sequence
from typing import Any
from typing import Protocol

from candles.application import queries
from candles.application.forming import bars_from_rows
from kernel import catalog_files
from kernel.venues import venue_of

from views import indicator_picker


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
# used for this same field on the web coin-detail page -- same field, same name, everywhere
# it appears.
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
    ("volatility", "Vol(catalog)", lambda v: f"{v:.6f}"),
    ("volatility_score", "Vol Score", lambda v: f"{v:.6f}" if v is not None else "—"),
    ("volume24h", "Vol24h", lambda v: f"{v / 1e6:.3f}M"),
]

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
