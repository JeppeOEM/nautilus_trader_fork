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
Trendline geometry (Story 33.8): the price a saved chart trendline stands at, at a bar's time.

Invariant (one line, two languages): this is the port of `frontend/src/lib/drawings.ts`'s
`trendlinePriceAt`, and both evaluate the same expression in the same order over IEEE doubles, so
on the drawn segment the line the chart draws and the line an alert crosses are the same line. The
two are held together by one fixture, `alerting/tests/fixtures/trendline_cases.json`, read by both
test suites.

Known limit (extrapolation): the line is extended beyond both anchors without bound, so an alert
fires on a cross wherever the line has been projected to, while the chart's `TrendlinePrimitive`
draws only the segment between the anchors -- past the second anchor the alert watches a line the
chart does not show (on the segment the two agree exactly). Upgrade path: a per-drawing `extend`
setting (segment, ray, extended), honoured by the primitive and this function alike.
"""

from collections.abc import Mapping
from collections.abc import Sequence


def trendline_price_at(anchors: Sequence[Mapping[str, float]], t: float) -> float | None:
    """
    Return `a.price + (b.price - a.price) * (t - a.time) / (b.time - a.time)`, the trendline through
    the two `{time, price}` anchors (times in UTC seconds, the stored drawing's shape) at `t`
    seconds, extrapolated beyond both; None for a vertical line (equal anchor times), which has no
    single price at any time.
    """
    a, b = anchors
    span = b["time"] - a["time"]
    if span == 0:
        return None
    return a["price"] + (b["price"] - a["price"]) * (t - a["time"]) / span
