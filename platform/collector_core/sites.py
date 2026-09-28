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
Deprecated re-export shim (Story 26.2): `collector_core.sites` moved to `capture.application.sites`.

Pure re-export, defines nothing: every name here *is* its successor object.
"""

import warnings

from capture.application.sites import ARCHIVE_GAPS_INVERTED_SPAN
from capture.application.sites import ARCHIVE_GAPS_WRITE
from capture.application.sites import BOOK_CROSSCHECK
from capture.application.sites import BOOK_CROSSCHECK_UNALIGNED
from capture.application.sites import BOOK_SEQUENCE
from capture.application.sites import CADENCE
from capture.application.sites import CANDLE_STORE
from capture.application.sites import CANDLE_STORE_CATCH_UP
from capture.application.sites import CAPTURE_LOCK_WAIT
from capture.application.sites import CORRUPT_PARQUET
from capture.application.sites import CROSSED_BOOK
from capture.application.sites import DISCONNECT
from capture.application.sites import EMPTY_TOP
from capture.application.sites import ENQUEUE
from capture.application.sites import FEED_STATE
from capture.application.sites import FLUSH_WRITE
from capture.application.sites import LATE_TRADE
from capture.application.sites import NO_SECOND_SINK
from capture.application.sites import OPEN_INTEREST_POLL
from capture.application.sites import PENDING_DELTAS
from capture.application.sites import PROCESS
from capture.application.sites import RESYNC
from capture.application.sites import SUBSCRIBE_FAILED
from capture.application.sites import TRADE_BACKFILL
from capture.application.sites import TRADE_FEED
from capture.application.sites import UNPLANNED_MESSAGE
from capture.application.sites import UNSUBSCRIBE_FAILED
from capture.application.sites import VENUE_CLOCK_AHEAD


__all__ = [
    "ARCHIVE_GAPS_INVERTED_SPAN",
    "ARCHIVE_GAPS_WRITE",
    "BOOK_CROSSCHECK",
    "BOOK_CROSSCHECK_UNALIGNED",
    "BOOK_SEQUENCE",
    "CADENCE",
    "CANDLE_STORE",
    "CANDLE_STORE_CATCH_UP",
    "CAPTURE_LOCK_WAIT",
    "CORRUPT_PARQUET",
    "CROSSED_BOOK",
    "DISCONNECT",
    "EMPTY_TOP",
    "ENQUEUE",
    "FEED_STATE",
    "FLUSH_WRITE",
    "LATE_TRADE",
    "NO_SECOND_SINK",
    "OPEN_INTEREST_POLL",
    "PENDING_DELTAS",
    "PROCESS",
    "RESYNC",
    "SUBSCRIBE_FAILED",
    "TRADE_BACKFILL",
    "TRADE_FEED",
    "UNPLANNED_MESSAGE",
    "UNSUBSCRIBE_FAILED",
    "VENUE_CLOCK_AHEAD",
]

REMOVE_AFTER = "26-3-closeout-shims-gone-spines-reconciled"


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "collector_core.sites moved to "
    "capture.application.sites (Story 26.2); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
