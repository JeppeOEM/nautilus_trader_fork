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
(`verification.recorder.*`), the conservation tool's (`verification.conservation.*`), the
trades tool's (`verification.trades.*`), the book tool's (`verification.book.*`), the derivs
tool's (`verification.derivs.*`), the catalog tool's (`verification.catalog.*`), the candles
tool's (`verification.candles.*`), the bot parity tool's (`verification.bot_parity.*`) and the
fault injection tool's (`verification.chaos.*`): every
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
BOOK_REFUSED = "verification.book.refused"
# The derivs tool refused to run: the book tool's refusals, plus a mark/index file without its
# `price_precision` label, a stored value that is not decimal text, a null value or clock, an
# unreadable `open_interest_poll_seconds`; or it crashed (the detail says `crashed`; re-raised).
DERIVS_REFUSED = "verification.derivs.refused"
# The catalog tool refused to run: a day not closed, a missing catalog, candles directory or store
# file, an unreadable plan, a plan instrument without a stored definition, a day file that vanished
# or appeared mid-run ("catalog changed during the check (maintenance ran?)"), an uncreatable
# scratch directory; or it crashed (the detail says `crashed`; the exception is then re-raised).
CATALOG_REFUSED = "verification.catalog.refused"
# The candles tool refused to run: a day not closed, a missing catalog, candles directory, store
# file, raw directory or coverage record, an unreadable plan, a malformed line, the data_api
# unreachable or answering non-200 or a malformed page, a file that vanished mid-run; or it crashed
# (the detail says `crashed`; the exception is then re-raised).
CANDLES_REFUSED = "verification.candles.refused"
# The bot parity tool refused to run: a missing log directory, catalog or coverage record, no log
# of a venue bot, a bot without its replay log, a malformed record, a replay whose `start` differs
# from the live one, a catalog not yet flushed past a bot's window, a stored row the book decoder
# refuses (or a float-layout file), a file that vanished mid-run; or it crashed (the detail says
# `crashed`; the exception is then re-raised).
BOT_PARITY_REFUSED = "verification.bot_parity.refused"
# The fault injection tool refused to act: no `data/.verify-stack` beside the catalog or the raw
# root, a target container not named `verify-*` or not in compose project `verify`, the scenario
# log's last run still open or ended less than the spacing ago, `sudo -n` not permitted for a
# network cut, no address or catalog leaf to fault, a malformed scenario or coverage line, a
# window's input unreadable; or it crashed (the detail says `crashed`; the exception is re-raised).
CHAOS_REFUSED = "verification.chaos.refused"
# A fault command did not return 0 (the scenario may not have been injected as logged), the
# run was interrupted (its fault ended early; the detail says whether the undo held), or its
# `end` line could not be written after the undo (the run stays open in the log).
CHAOS_FAULT_FAILED = "verification.chaos.fault_failed"
# An undo command did not return 0: the fault may still be in place -- check the target by hand.
CHAOS_UNDO_FAILED = "verification.chaos.undo_failed"
