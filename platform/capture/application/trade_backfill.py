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
The trade backfill's application half (story 22.14; split from `collector_core/trade_backfill.py`
in Story 26.1): what one backfill did, and how a venue fetch is admitted into a `TradeIntake`.
The fetch and parse half is each venue's `trade_history.py` (`ports.VenueTradeHistory`); the
scheduling (`FeedGroup` requests, the settle, the loop) is the `CaptureService`'s.
"""

from dataclasses import dataclass
from dataclasses import field

from kernel.clocks import MAX_TS_INIT_SKEW_NS
from kernel.clocks import TwoClocks

from capture.domain.trade_intake import REST_FEED_NAME
from capture.domain.trade_intake import TradeIntake
from nautilus_trader.model.data import TradeTick


# The lookback re-reads a little before the last archived trade: ids already archived are
# counted, not written twice.
BACKFILL_LOOKBACK_NS: int = 5_000_000_000


@dataclass
class BackfillReport:
    """What one backfill did, for its single `collector.trade_backfill` ledger entry."""

    feed: str
    reasons: list[str]
    instruments: int = 0
    backfilled: int = 0
    already: int = 0
    refused: int = 0
    no_baseline: int = 0
    unrecoverable: dict[str, float] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def message(self) -> str:
        unrecoverable = {iid: round(s, 3) for iid, s in self.unrecoverable.items()}
        return (
            f"feed {self.feed} ({'; '.join(self.reasons)}): {self.instruments} instruments, "
            f"backfilled {self.backfilled}, already archived {self.already}, refused "
            f"{self.refused} (older than the {MAX_TS_INIT_SKEW_NS // 1_000_000_000} s arrival "
            f"margin), unrecoverable seconds {unrecoverable}, no baseline {self.no_baseline}, "
            f"errors {self.errors}"
        )


def restamped(trade: TradeTick, ts_init: int) -> TradeTick:
    return TradeTick(
        trade.instrument_id,
        trade.price,
        trade.size,
        trade.aggressor_side,
        trade.trade_id,
        trade.ts_event,
        ts_init,
    )


def admit_backfill(
    intake: TradeIntake, trades: list[TradeTick], now_ns: int, report: BackfillReport
) -> tuple[list[TradeTick], int | None]:
    """
    Return (the unseen trades restamped to `now_ns`, oldest first, to archive; the newest refused
    trade's `ts_event` -- known lost up to there -- or None). Never into the live second: the
    nightly rebuild places them. `ts_init` is `now_ns` because the flush needs every new trade's
    `ts_init` at or after what it already wrote, and live trades may have been flushed while the
    fetch ran; the arrival bound (`MAX_TS_INIT_SKEW_NS`, the rebuild's and prune's window) is
    checked on that stamp -- an older trade would be invisible to them, so it is refused.
    """
    archive: list[TradeTick] = []
    newest_refused: int | None = None
    for trade in trades:
        trade_id = str(trade.trade_id)
        if intake.first_feed(trade_id) is not None:
            report.already += 1
            continue
        if not TwoClocks(trade.ts_event, now_ns).within_skew(MAX_TS_INIT_SKEW_NS):
            report.refused += 1
            newest_refused = trade.ts_event  # oldest first: the last one is the newest
            continue
        intake.register(trade_id, REST_FEED_NAME)
        archive.append(restamped(trade, now_ns))
        intake.advance(trade.ts_event)
        intake.backfilled += 1
        report.backfilled += 1
    return archive, newest_refused
