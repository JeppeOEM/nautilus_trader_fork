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
Every error-ledger site capture writes (DDD spine AD-D6, DATA-07): one per event type.

Invariant: `CaptureService._ledger` is the only caller of `observability.error_ledger.record` in
capture, and every site it (or an adapter, client or venue loop handed it) names is a constant
here -- `platform/tests/test_boundaries.py` greps for both. The strings are published language
(`GET /api/errors`, the durable ledger files, `archive.crosscheck_errors`): frozen (AD-D12).
"""

# ingest
ENQUEUE = "collector.enqueue"
PROCESS = "collector.process"
UNPLANNED_MESSAGE = "collector.unplanned_message"
UNKNOWN_MESSAGE = "collector.unknown_message"
STALE_TRADE = "collector.stale_trade"
# the gate and the book
EMPTY_TOP = "collector.empty_top"
UNENCODABLE = "collector.unencodable"
CROSSED_BOOK = "collector.crossed_book"
RESYNC = "collector.resync"
BOOK_SEQUENCE = "collector.book_sequence"
PENDING_DELTAS = "collector.pending_deltas"
BOOK_CROSSCHECK = "collector.book_crosscheck"
BOOK_CROSSCHECK_UNALIGNED = "collector.book_crosscheck_unaligned"
BOOK_CROSSCHECK_UNCONFIRMED = "collector.book_crosscheck_unconfirmed"
CADENCE = "collector.cadence"
OHLC_OUTSIDE_BOOK = "collector.ohlc_outside_book"
# seconds without a row (Story 31.2): the coverage record's reasons, summarised per flush
SECOND_REJECTED = "collector.second_rejected"
SKIPPED_SECONDS = "collector.skipped_seconds"
RESTART_GAP = "collector.restart_gap"
COVERAGE_WRITE = "collector.coverage_write"
DEDUP_SEED = "collector.dedup_seed"
# venue time (story 22.12)
LATE_TRADE = "collector.late_trade"
VENUE_CLOCK_AHEAD = "collector.venue_clock_ahead"
# flush and the downstream sink
FLUSH_WRITE = "collector.flush_write"
CANDLE_STORE = "collector.candle_store"
CANDLE_STORE_CATCH_UP = "collector.candle_store_catch_up"
NO_SECOND_SINK = "collector.no_second_sink"
CANDLE_STORE_BEHIND = "collector.candle_store_behind"
SNAPSHOT_PUBLISH = "collector.snapshot_publish"
# the per-flush hot-path record on `capture:hotpath` (Story 28.1)
HOTPATH_PUBLISH = "collector.hotpath_publish"
# the archive adapter
CORRUPT_PARQUET = "collector.corrupt_parquet"
CAPTURE_LOCK_WAIT = "collector.capture_lock_wait"
ARCHIVE_GAPS_WRITE = "archive_gaps.write"
ARCHIVE_GAPS_INVERTED_SPAN = "archive_gaps.inverted_span"
# feeds, reconnects and the trade backfill (story 22.14)
FEED_STATE = "collector.feed_state"
TRADE_FEED = "collector.trade_feed"
TRADE_BACKFILL = "collector.trade_backfill"
# the applied set (Story 25.4)
SUBSCRIBE_FAILED = "collector.subscribe_failed"
UNSUBSCRIBE_FAILED = "collector.unsubscribe_failed"
DISCONNECT = "collector.disconnect"
CRASH = "collector.crash"
# venue REST polls
OPEN_INTEREST_POLL = "collector.open_interest_poll"
# the liquidation socket (Story 33.1): a refused subscribe ack, a failed send, a transition to
# `down`; and a failed `liquidations:raw` publish
LIQUIDATION_FEED = "collector.liquidation_feed"
LIQUIDATION_PUBLISH = "collector.liquidation_publish"
