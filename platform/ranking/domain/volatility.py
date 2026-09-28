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
Cross-sectional volatility tracker (Story 1.8 / FR16) -- a per-instrument rolling
mid-price/return buffer with a configurable age-based lookback, default 3600s (1h).

This is a purpose-built volatility computation, distinct from the other two the ranking
publishes (`ranking.domain.metrics.price_stats_from_series`' 25h np.std -- the `volatility`
field -- and `RankingBoard`'s live 300-entry `volatility_fast`). Do not consolidate them --
see Story 1.8's Dev Notes for why that's explicit scope creep. Moved into the ranking context in
Story 25.2.

Age-based eviction (not a fixed-length maxlen deque) is deliberate: a fixed-length
window silently shrinks its effective time span across a variable snapshot rate or a
resync gap, whereas this lookback must represent a stable wall-clock/event-time window
regardless of cadence.
"""

import statistics
from collections import deque


class VolatilityTracker:
    """
    Cross-sectional volatility: per-instrument stdev of consecutive-price percentage
    returns over the trailing `lookback_seconds` (default 3600s = 1h).
    """

    def __init__(self, lookback_seconds: int = 3600) -> None:
        self._lookback_ns = lookback_seconds * 1_000_000_000
        self._buffers: dict[str, deque[tuple[int, float]]] = {}

    def update(self, instrument_id: str, ts_event_ns: int, mid_price: float) -> None:
        """Append (ts_event_ns, mid_price) and evict entries older than lookback_seconds."""
        buf = self._buffers.setdefault(instrument_id, deque())
        buf.append((ts_event_ns, mid_price))
        cutoff = ts_event_ns - self._lookback_ns
        while buf and buf[0][0] < cutoff:
            buf.popleft()

    def score(self, instrument_id: str) -> float | None:
        """
        Stdev of the buffer's consecutive-price returns, or None if fewer than 2
        returns are available -- an instrument with insufficient history (newly
        subscribed, or mid-resync-gap) is a missing value, not an error.
        """
        buf = self._buffers.get(instrument_id)
        if buf is None:
            return None
        prices = [price for _, price in buf]
        rets = [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, len(prices))]
        if len(rets) < 2:
            return None
        return statistics.stdev(rets)

    def drop(self, instrument_id: str) -> None:
        """Release an aged-out instrument's buffer (MEM-02); a later return starts empty."""
        self._buffers.pop(instrument_id, None)
