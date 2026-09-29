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
# the gate and the book
EMPTY_TOP = "collector.empty_top"
UNENCODABLE = "collector.unencodable"
CROSSED_BOOK = "collector.crossed_book"
RESYNC = "collector.resync"
BOOK_SEQUENCE = "collector.book_sequence"
PENDING_DELTAS = "collector.pending_deltas"
BOOK_CROSSCHECK = "collector.book_crosscheck"
BOOK_CROSSCHECK_UNALIGNED = "collector.book_crosscheck_unaligned"
CADENCE = "collector.cadence"
# venue time (story 22.12)
LATE_TRADE = "collector.late_trade"
VENUE_CLOCK_AHEAD = "collector.venue_clock_ahead"
# flush and the downstream sink
FLUSH_WRITE = "collector.flush_write"
CANDLE_STORE = "collector.candle_store"
CANDLE_STORE_CATCH_UP = "collector.candle_store_catch_up"
NO_SECOND_SINK = "collector.no_second_sink"
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
# venue REST polls
OPEN_INTEREST_POLL = "collector.open_interest_poll"
