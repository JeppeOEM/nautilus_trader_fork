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
`HttpRankingHistory`: the `RankingHistory` port over data_api's metrics history API (Story 27.1).

Reads `GET /api/metrics/history/{instrument_id}?days=N` -- ranking's `metrics.db` rows served
through the views read model and ranking's query service -- exactly as `research.rank_history`
reads `/api/metrics/nearest`. HTTP only: research imports neither `ranking` nor `data_api`, and
computes no pct-change or volatility of its own (AD-D10).
"""

import json
import urllib.parse
import urllib.request

import pandas as pd


# The metric fields of data_api's `MetricHistoryItem` (`data_api/routes/metrics.py`), which mirrors
# `ranking.infrastructure.metrics_store.COLS`; an empty history still has every column.
METRIC_COLUMNS = (
    "price",
    "pct_1h",
    "pct_24h",
    "pct_1w",
    "pct_1m",
    "volatility",
    "ofi",
    "microprice",
    "spread",
    "rank",
    "volume24h",
    "funding_rate",
    "funding_annualised",
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


class HttpRankingHistory:
    """
    `RankingHistory` over a running data_api.

    Invariant: every column and value is the API's own (a None field stays a gap, AD-F6); the frame
    only re-indexes rows on their `ts`, never fills, resamples or recomputes one.
    """

    def __init__(self, data_api_url: str = "http://127.0.0.1:9100") -> None:
        self._base = data_api_url.rstrip("/")

    def history(self, instrument_id: str, days: int) -> pd.DataFrame:
        """
        Return the instrument's metric snapshots over the last `days` days, oldest first: a UTC
        `DatetimeIndex` named `ts` (from the row's `ts`, ns), a `ts_event` column holding that `ts`,
        and exactly one column per `METRIC_COLUMNS` entry in that order (NaN where the API sent no
        value). Rows out of order, a repeated `ts`, or a field outside `METRIC_COLUMNS` raise
        `ValueError` -- the frame never reorders or drops what the API said.
        """
        if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
            raise ValueError(f"days must be a positive int, got {days!r}")
        iid = urllib.parse.quote(instrument_id, safe="")
        url = f"{self._base}/api/metrics/history/{iid}?days={days}"
        request = urllib.request.Request(url)  # noqa: S310 (local data_api, not a remote host)
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            payload = json.load(response)
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise ValueError(f"{url}: the response holds no items list")
        items = payload["items"]
        if any(not isinstance(item, dict) or item.get("ts") is None for item in items):
            raise ValueError(f"{url}: a row without a ts")
        frame = pd.DataFrame(items)
        if frame.empty:
            frame = pd.DataFrame(
                {"ts": pd.Series(dtype="int64")}
                | {name: pd.Series(dtype="float64") for name in METRIC_COLUMNS}
            )
        unknown = sorted(set(frame.columns) - {"ts", *METRIC_COLUMNS})
        if unknown:
            raise ValueError(f"{url}: fields {unknown} are not in METRIC_COLUMNS (update both)")
        # A field the API omitted is a gap (None -> NaN), never a missing column.
        frame = frame.reindex(columns=["ts", *METRIC_COLUMNS]).rename(columns={"ts": "ts_event"})
        frame["ts_event"] = frame["ts_event"].astype("int64")
        # One dtype per column whatever the payload: an all-None field is NaN, not an object column.
        frame = frame.astype(dict.fromkeys(METRIC_COLUMNS, "float64"))
        if not frame["ts_event"].is_monotonic_increasing or frame["ts_event"].duplicated().any():
            raise ValueError(f"{url}: rows are not in strictly increasing ts order")
        frame.index = pd.DatetimeIndex(
            pd.to_datetime(frame["ts_event"], unit="ns", utc=True), name="ts"
        )
        return frame
