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
The single-coin detail read model (SSOT-05): which metrics the coin-detail view shows, where each
comes from, and the inputs both UIs read for one coin.

- `COIN_DETAIL_GROUPS` -- the full-parity metric list, grouped by update cadence, as
  `(title, [(label, rankings:live rank-entry key, display decimals)])`. Every value is read off
  ranking_engine's published rank entry for the coin (`rank_row_for`): the one computer of those
  metrics (SSOT-02), so no UI computes any of them itself.
- `snapshot_for` -- the open coin's newest `snapshots:raw` second, decoded through
  `DydxSecondSnapshot.from_dict` (spine AD-D3: the payload is parsed only by its own type, never
  hand-indexed); its raw book levels feed the TUI's order-book ladder.
- `metrics_history`/`metrics_nearest` -- the ranking context's `metrics.db` history reads
  (`ranking_engine.metrics_store`), and `catalog_snapshot_rows` -- the archived seconds of one
  coin as plain dicts.

Moved out of `bot_tui/app.py`, `bot_tui/coin_detail.py`, `bot_tui/coin_detail_state.py`,
`data_api/routes/metrics.py` and `data_api/app.py` (Story 24.2).
"""

from kernel.second_snapshot import DydxSecondSnapshot
from observability import error_ledger
from ranking_engine import metrics_store

from views.catalog_reads import query_second_snapshots


# Every value column gets at least this many decimals -- user wants enough visible
# precision that a small tick registers as a changing digit instead of a static
# number. buy_count/sell_count are the one deliberate exception below (they're
# integer counts, not decimal quantities -- "7.00000000" would be noise, not
# precision).
MIN_INDICATOR_DECIMALS = 8

# Coin-detail's full-parity indicator list (label, rankings:live rank-entry key,
# display decimals) -- every metric ranking_engine publishes for the open instrument,
# same SSOT-02 source the Coins pane's own columns come from. Not reusing
# `views.ranking_columns.RANKING_COLS`' own format_fn/color_fn here: those assume a
# non-None value (they'd raise on the "warming up" case this vertical list needs to
# handle for every row) and are tuned for compact table cells, not a labeled list --
# bot_tui's coin_detail.format_indicator already owns the None-safe formatting this view needs.
#
# Grouped into one box per update cadence (matches the web /coin/{id} grouping) rather
# than one flat list -- a value's box tells you how often it can actually change without
# reading engine.py. "volatility & market" is a cadence group by convention, not strictly:
# pct_1h/pct_24h/volume24h aren't volatility, but they update on the same 60s-or-slower
# cadence as the volatility fields and would be noise scattered elsewhere.
COIN_DETAIL_GROUPS: tuple[tuple[str, tuple[tuple[str, str, int], ...]], ...] = (
    (
        "live  (1s book state)",
        (
            ("microprice", "microprice", MIN_INDICATOR_DECIMALS),
            ("microprice lean", "microprice_lean", MIN_INDICATOR_DECIMALS),
            ("spread", "spread", MIN_INDICATOR_DECIMALS),
            ("obi(3)", "obi_3", MIN_INDICATOR_DECIMALS),
            ("obi(5)", "obi_5", MIN_INDICATOR_DECIMALS),
            ("obi(10)", "obi_10", MIN_INDICATOR_DECIMALS),
            ("price", "price", MIN_INDICATOR_DECIMALS),
        ),
    ),
    (
        "order flow  (~5m rolling)",
        (
            ("ofi(3)", "ofi_3", MIN_INDICATOR_DECIMALS),
            ("ofi(5)", "ofi_5", MIN_INDICATOR_DECIMALS),
            ("ofi(10)", "ofi_10", MIN_INDICATOR_DECIMALS),
            ("ofi(10) z", "ofi_10_z", MIN_INDICATOR_DECIMALS),
            ("cvd", "cvd", MIN_INDICATOR_DECIMALS),
            ("volume delta (60s)", "volume_delta", MIN_INDICATOR_DECIMALS),
            ("buy count", "buy_count", 0),
            ("sell count", "sell_count", 0),
            ("avg trade size", "avg_trade_size", MIN_INDICATOR_DECIMALS),
        ),
    ),
    (
        "volatility & market  (60s-1h)",
        (
            ("volatility (fast)", "volatility_fast", MIN_INDICATOR_DECIMALS),
            ("volatility (catalog)", "volatility", MIN_INDICATOR_DECIMALS),
            ("volatility score", "volatility_score", MIN_INDICATOR_DECIMALS),
            ("pct 1h", "pct_1h", MIN_INDICATOR_DECIMALS),
            ("pct 24h", "pct_24h", MIN_INDICATOR_DECIMALS),
            ("pct 1w", "pct_1w", MIN_INDICATOR_DECIMALS),
            ("pct 1m", "pct_1m", MIN_INDICATOR_DECIMALS),
            ("volume 24h", "volume24h", MIN_INDICATOR_DECIMALS),
        ),
    ),
)


def rank_row_for(ranking: dict | None, instrument_id: str) -> dict | None:
    """
    Return the rankings:live rank entry matching instrument_id, or None if no rankings:live
    message has arrived yet or this instrument isn't (yet) in it -- e.g. a coin just
    opened before its first live tick has propagated through ranking_engine.
    """
    if ranking is None:
        return None
    for row in ranking.get("ranks", []):
        if isinstance(row, dict) and row.get("instrument_id") == instrument_id:
            return row
    return None


def snapshot_for(batch: object, instrument_id: str) -> DydxSecondSnapshot | None:
    """
    Return the `instrument_id` second of one `snapshots:raw` batch (a JSON list of
    `DydxSecondSnapshot.to_dict()` results), decoded through `DydxSecondSnapshot.from_dict`; None
    when the batch does not carry that coin, or is not a list.

    Every entry is decoded, not only the matching one: the coin is identified by the decoded
    snapshot's own `instrument_id`, never by indexing the raw payload. An entry that fails to
    decode is a malformed publish upstream (the collector writes only `to_dict()` output), so it is
    ledgered at `views.snapshot_decode` -- once per entry -- and skipped; the rest of the batch is
    still read (DATA-07: visible, never silent).
    """
    if not isinstance(batch, list):
        error_ledger.record(
            "views.snapshot_decode", f"snapshots:raw batch is not a list: {type(batch).__name__}"
        )
        return None
    found: DydxSecondSnapshot | None = None
    for entry in batch:
        try:
            snapshot = DydxSecondSnapshot.from_dict(entry)
        except Exception as exc:
            error_ledger.record(
                "views.snapshot_decode", "snapshots:raw entry failed to decode", exc
            )
            continue
        if found is None and snapshot.instrument_id.value == instrument_id:
            found = snapshot
    return found


def metrics_history(symbol: str, db_path: str, days: int = 31) -> list[dict]:
    """One coin's `metrics.db` rows over the last `days` days, oldest first (ranking's own read)."""
    return metrics_store.history(symbol, db_path, days)


def metrics_nearest(symbol: str, ts_ns: int, db_path: str) -> dict | None:
    """Return the `metrics.db` row of one coin closest to `ts_ns`; None if never stored."""
    return metrics_store.nearest(symbol, ts_ns, db_path)


def catalog_snapshot_rows(
    catalog_path: str, instrument_id: str, start_ns: int, end_ns: int
) -> list[dict]:
    """Return the archived seconds of one coin in `[start_ns, end_ns]` as book + OHLC dicts."""
    return [
        {
            "bid_prices": s.bid_prices,
            "bid_sizes": s.bid_sizes,
            "ask_prices": s.ask_prices,
            "ask_sizes": s.ask_sizes,
            "buy_volume": s.buy_volume,
            "sell_volume": s.sell_volume,
            "ts_event": s.ts_event,
            "open_price": s.open_price,
            "high_price": s.high_price,
            "low_price": s.low_price,
            "close_price": s.close_price,
        }
        for s in query_second_snapshots(catalog_path, instrument_id, start_ns, end_ns)
    ]
