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
"""Bucket trade prices into OHLC candles at an arbitrary timeframe."""

from dataclasses import dataclass


TIMEFRAMES = {
    "1m": 60,
    "5m": 300,
    "10m": 600,
    "15m": 900,
    "30m": 1800,
    "45m": 2700,
    "1h": 3600,
}


@dataclass
class Candle:
    ts_open: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


def build_candles(rows: list[tuple[int, float, float]], period_seconds: int) -> list[Candle]:
    """
    Bucket (ts_event, price, size) trade rows into `period_seconds`-wide OHLC candles.

    The last candle covers "now" and is necessarily partial — callers re-fetch
    trades from the catalog and recompute on every request, so it fills in
    live as new trades land. No special-cased "live candle" code needed.
    """
    period_ns = period_seconds * 1_000_000_000
    buckets: dict[int, list[tuple[float, float]]] = {}
    for ts_event, price, size in sorted(rows, key=lambda row: row[0]):
        buckets.setdefault(ts_event // period_ns, []).append((price, size))

    return [
        Candle(
            ts_open=bucket_key * period_ns,
            open=prices_sizes[0][0],
            high=max(p for p, _ in prices_sizes),
            low=min(p for p, _ in prices_sizes),
            close=prices_sizes[-1][0],
            volume=sum(s for _, s in prices_sizes),
        )
        for bucket_key, prices_sizes in sorted(buckets.items())
    ]
