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
The verification context's error-ledger sites (DATA-07), the reference recorder's
(`verification.recorder.*`), the conservation tool's (`verification.conservation.*`) and the
trades tool's (`verification.trades.*`): every
failure either survives or refuses on is recorded at one of these through
`observability.error_ledger.record`, never a bare log line.

Invariant: each site is named once, here, and every other verification module reports through
these constants (`verification/tests/test_sites.py`), so a reader of the durable ledger
(`data/errors/<ERROR_LEDGER_SERVICE>.jsonl`) can count one failure class by one name.
"""

# A WebSocket connect attempt failed (DNS, TLS, refused, timeout): an `error` line, then backoff.
CONNECT = "verification.recorder.connect"
# An open connection ended without being asked to (server close, transport error, failed send).
CONNECTION = "verification.recorder.connection"
# No data frame on any of an endpoint's data channels for its stale bound: forced reconnect.
STALE_FEED = "verification.recorder.stale_feed"
# A received frame that is not JSON, filed verbatim in channel `unparsed`.
UNPARSED = "verification.recorder.unparsed"
# JSON whose shape the subscription tables do not name, filed verbatim in channel `unknown`.
UNKNOWN_FRAME = "verification.recorder.unknown_frame"
# The venue refused something (Bybit `success: false`, Hyperliquid channel `error`).
VENUE_ERROR = "verification.recorder.venue_error"
# A REST poll failed: a non-2xx status or a transport error (the line is written either way).
REST = "verification.recorder.rest"
# The venue config.toml could not be read or parsed: the last good plan is kept (or, at start,
# the recorder refuses to start, as it does for an invalid VERIFY_DATA_DIR/VERIFY_RETAIN_DAYS).
PLAN = "verification.recorder.plan"
# A raw file's tail was a truncated zstd frame (a crash): its complete lines were rewritten.
TRUNCATED_TAIL = "verification.recorder.truncated_tail"
# An existing raw file could not be read at all (not a zstd stream, a frame the decoder refuses):
# renamed aside as `<file>.corrupt-<ns>` and a fresh file started.
CORRUPT_FILE = "verification.recorder.corrupt_file"
# The bytes-per-day accounting at a rotation could not read the files.
ACCOUNTING = "verification.recorder.accounting"
# A raw file could not be opened, written, closed or repaired (lines lost are counted in detail).
WRITE = "verification.recorder.write"
# The retention prune failed to delete a file.
PRUNE = "verification.recorder.prune"
# A recorder loop died of an unexpected exception: the process exits and restarts.
CRASH = "verification.recorder.crash"
# The conservation tool refused to run: a missing raw root or catalog, an unreadable plan, a
# malformed coverage or archive-gap line, a truncated raw file of a closed hour.
CONSERVATION_REFUSED = "verification.conservation.refused"
# The trades tool refused to run: a day not closed, a missing raw root or catalog, an unreadable
# plan, a malformed line, a truncated raw file of a checked hour, an unknown `--stage`; or it
# crashed on any other exception (the detail says `crashed`; the exception is then re-raised).
TRADES_REFUSED = "verification.trades.refused"
