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
The coverage record is a contract between two contexts that share no code: capture writes it
(`capture.domain.coverage`) and `verification.conservation` reads it, refusing any key set it
does not know. Only `platform/tests` may import both, so the seam is pinned here: every line kind
capture can write parses back to the same values.
"""

from capture.domain.coverage import DEPTH
from capture.domain.coverage import SECOND_REASONS
from capture.domain.coverage import SecondsRun
from capture.domain.coverage import TradesBackfilled
from capture.domain.coverage import TradesDropped
from capture.domain.coverage import TradesUnrecoverable
from verification.domain.conservation import Backfilled
from verification.domain.conservation import SecondsRun as ParsedRun
from verification.domain.conservation import TradeWindow
from verification.domain.conservation import parse_coverage_line


_IID = "BTCUSDT-LINEAR.BYBIT"


def test_every_seconds_reason_capture_writes_parses() -> None:
    for reason in sorted(SECOND_REASONS):
        line = SecondsRun(_IID, reason, 100, 104).to_json_line()
        parsed = parse_coverage_line(line, "contract")
        assert isinstance(parsed, ParsedRun)
        assert (parsed.instrument_id, parsed.reason, parsed.first_s, parsed.last_s) == (
            _IID,
            reason,
            100,
            104,
        )


def test_trade_lines_capture_writes_parse() -> None:
    dropped = parse_coverage_line(TradesDropped(_IID, "stale", 5, 9, 3).to_json_line(), "c")
    lost = parse_coverage_line(TradesUnrecoverable(_IID, DEPTH, 7, 11).to_json_line(), "c")
    filled = parse_coverage_line(TradesBackfilled(_IID, ("a", "b")).to_json_line(), "c")
    assert isinstance(dropped, TradeWindow)
    assert isinstance(lost, TradeWindow)
    assert isinstance(filled, Backfilled)
    assert (dropped.from_ns, dropped.to_ns, lost.from_ns, lost.to_ns) == (5, 9, 7, 11)
    assert filled.trade_ids == ("a", "b")
