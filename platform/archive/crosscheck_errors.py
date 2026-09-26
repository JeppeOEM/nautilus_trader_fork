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
Day-long data/error cross-check (story 23.3): the durable per-service error ledgers against the
archived second-snapshot rows over one UTC window.

Usage:
    python3 -m archive.crosscheck_errors --catalog /app/catalog \
        --errors-dir /app/errors_dir \
        [--since 2026-09-21T00:00:00Z] [--until 2026-09-22T00:00:00Z] \
        [--venue dydx|bybit|hyperliquid] [--fail-on SITE ...]

What is checked, how a gap is explained and every Known limit are
`archive.application.crosscheck`'s docstring. Default window: the last 24 hours ending now (UTC).
Default `--fail-on`: `collector.book_crosscheck`, `collector.book_sequence`,
`collector.pending_deltas`. Exit code: non-zero when any gap is `UNEXPLAINED`, any `--fail-on` site
has a non-zero count, or nothing at all was read (no ledger file, no instrument); zero otherwise.
A catalog directory that does not exist is `archive.catalog_missing`, exit 1.
"""

import argparse
import sys
from datetime import UTC
from datetime import datetime
from datetime import timedelta

from kernel.clocks import NS_PER_S

from archive.application.catalog_check import catalog_missing
from archive.application.crosscheck import DEFAULT_FAIL_ON
from archive.application.crosscheck import build_report
from archive.application.crosscheck import exit_code
from archive.application.crosscheck import nothing_was_checked
from archive.application.crosscheck import parse_ts
from archive.application.crosscheck import print_report
from archive.application.crosscheck import to_ns


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").strip().split("\n\n")[0])
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--errors-dir", required=True)
    parser.add_argument("--since", help="ISO-8601 UTC instant; default: 24h before --until")
    parser.add_argument("--until", help="ISO-8601 UTC instant; default: now")
    parser.add_argument("--venue", choices=("dydx", "bybit", "hyperliquid"))
    parser.add_argument("--fail-on", nargs="*", default=list(DEFAULT_FAIL_ON))
    return parser


def _window(since: str | None, until: str | None) -> tuple[int, int]:
    """Resolve `--since`/`--until` to a half-open ns window; raises ValueError when invalid."""
    until_ns = parse_ts(until) if until else to_ns(datetime.now(UTC))
    since_ns = (
        parse_ts(since) if since else until_ns - int(timedelta(hours=24).total_seconds()) * NS_PER_S
    )
    if since_ns >= until_ns:
        raise ValueError("--since must be before --until")
    return since_ns, until_ns


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the exit code (see the module docstring)."""
    args = _build_parser().parse_args(argv)
    try:
        since_ns, until_ns = _window(args.since, args.until)
    except ValueError as exc:
        print(
            f"error: --since/--until must be ISO-8601 UTC instants with --since first: {exc}",
            file=sys.stderr,
        )
        return 1
    if catalog_missing("crosscheck_errors", args.catalog):
        return 1
    if not args.fail_on:
        # `--fail-on` with no values (nargs="*") is a valid but easy-to-hit foot-gun: it
        # silently drops the documented defaults instead of disabling the check on purpose.
        print(
            "warning: --fail-on given with no sites; no site will fail the exit code",
            file=sys.stderr,
        )

    report = build_report(args.catalog, args.errors_dir, since_ns, until_ns, args.venue)
    print_report(report, tuple(args.fail_on))
    if nothing_was_checked(report, args.errors_dir, args.catalog):
        return 1
    return exit_code(report, tuple(args.fail_on))


if __name__ == "__main__":
    sys.exit(main())
