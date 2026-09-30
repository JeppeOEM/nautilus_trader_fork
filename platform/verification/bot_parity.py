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
The bot parity tool's composition root (Story 31.9):

    python3 -m verification.bot_parity --venue BYBIT|HYPERLIQUID --live-dir DIR --replay-dir DIR
        [--catalog DIR] [--json]

For every dummy bot of the venue with a live signal log (`bots.strategies.signal_log`, one
`<bot_id>.jsonl` per bot), its latest run segment is paired cycle by cycle (equal `ts_ns`) with its
catalog replay's (`bots.signal_replay`, the same file name under `--replay-dir`). Per bot and signal
it reports the exact-equal share, the largest absolute difference, the decision and action
disagreements and every divergence's class, judged against the catalog's stored snapshot rows (read
raw) and the coverage record (`verification.domain.bot_parity`; `docs/DATA_DICTIONARY.md` §1.22).

Prints a report (or `--json`) and exits 0 when nothing is `unexplained`, 1 when something is (or
the inputs are refused), 2 on a usage error. A refusal -- a missing log directory, catalog or
coverage record, no log of a venue bot, a bot without its replay log or logging more levels a
side than the stored rows hold, a log whose venue cannot be told, a catalog not yet flushed
past a bot's window (no stored row in the minute after it), a malformed record, a replay whose
`start` record differs from the live one (but for `ts_ns`) or starts off the live grid, a stored
row the oracle's book decoder refuses (or a float-layout snapshot file), a file vanishing
mid-run -- is ledgered at
`verification.bot_parity.refused` and exits with its message (status 1); any other exception is a
crash, ledgered at the same site and re-raised.

The reference side never imports the code it checks (`tests/test_boundaries.py`): no `bots`,
`kernel.indicators` or `nautilus_trader`; the logs are read as JSON text, the catalog raw.
Environment: `CATALOG_PATH` (unless `--catalog`); `ERROR_LEDGER_DIR` / `ERROR_LEDGER_SERVICE`.
"""

import argparse
import os
import sys
from collections.abc import Mapping

from observability import error_ledger

from verification.application import sites
from verification.application.bot_parity import ParityInputs
from verification.application.bot_parity import ParityReport
from verification.application.bot_parity import check
from verification.application.bot_parity import dumps
from verification.application.bot_parity import render_text
from verification.conservation import Refused
from verification.conservation import directory
from verification.domain.subscriptions import VENUES
from verification.infrastructure.catalog_reader import CoverageFiles
from verification.infrastructure.signal_logs import SignalLogDir
from verification.infrastructure.snapshot_book import SnapshotBooks


def inputs_of(args: argparse.Namespace, environ: Mapping[str, str]) -> ParityInputs:
    """Build the read-only sources from the arguments and environment (`Refused` if absent)."""
    live = directory(args.live_dir, "--live-dir")
    replay = directory(args.replay_dir, "--replay-dir")
    catalog = directory(args.catalog or environ.get("CATALOG_PATH"), "CATALOG_PATH")
    coverage = CoverageFiles(catalog, args.venue)
    if not coverage.present():
        raise Refused(f"coverage record {coverage.path} does not exist")
    return ParityInputs(
        live=SignalLogDir(live),
        replay=SignalLogDir(replay),
        catalog=SnapshotBooks(catalog),
        coverage=coverage,
    )


def run(args: argparse.Namespace, environ: Mapping[str, str]) -> ParityReport:
    """Build the inputs and compare every bot; any input that cannot be trusted is `Refused`."""
    inputs = inputs_of(args, environ)
    try:
        return check(args.venue, inputs)
    # ParityRefused, MalformedRecord, SegmentMismatch, MalformedLine (coverage), FloatLayout and a
    # stored row the book decoder refuses are ValueErrors; a vanished file an OSError; an
    # ArithmeticError a stored value the exact decode cannot hold.
    except (ValueError, ArithmeticError, OSError) as exc:
        raise Refused(str(exc)) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m verification.bot_parity")
    parser.add_argument("--venue", required=True, choices=VENUES)
    parser.add_argument("--live-dir", required=True, help="the live fleet's signal logs")
    parser.add_argument("--replay-dir", required=True, help="bots.signal_replay's --out")
    parser.add_argument("--catalog", help="the catalog root (default: CATALOG_PATH)")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    return parser


def main(argv: list[str] | None = None) -> int:
    """
    Run the tool; return the exit status (0: nothing unexplained, 1: something unexplained). A
    refusal raises `SystemExit`; any other exception is ledgered at the same site as a crash, then
    re-raised (DATA-07). No clock: the tool judges whatever the logs hold, and each bot's window
    ends at the earlier of its two sides' last records.
    """
    args = _parser().parse_args(argv)
    error_ledger.start(
        service=error_ledger.job_service("verify_bot_parity", "verification", args.venue)
    )
    try:
        report = run(args, os.environ)
    except Refused as exc:
        error_ledger.record(sites.BOT_PARITY_REFUSED, f"{args.venue}: {exc}", exc)
        raise SystemExit(f"bot_parity refused: {exc}") from exc
    except Exception as exc:
        error_ledger.record(sites.BOT_PARITY_REFUSED, f"{args.venue}: crashed: {exc!r}", exc)
        raise
    print(dumps(report) if args.json else render_text(report))
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
