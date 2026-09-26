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
r"""
Prune old Parquet files from the catalog: the one retention run (story 22.13, Story 25.1).

Parquet filenames in the catalog encode their time range: `<start_ts>_<end_ts>.parquet`.

Usage (report only unless --apply; --dry-run is the explicit spelling of the default):
    python -m archive.prune_catalog --catalog /app/catalog \\
        [--types order_book_deltas --days 14] \\
        [--candles-dir /app/candles_dir --trade-retention-days 7] \\
        [--dydx-plan /app/dydx_collector/config.toml] [--venue BYBIT] [--apply]

The rules are `archive.domain.retention`'s docstring. `--types T --days N` deletes files of those
data types whose end timestamp is older than N days. `trade_tick` is refused there: raw trades
exist to correct and prove the aggregates, so they are released only by the trade policy.

Trade policy (with `--candles-dir`): a `data/trade_tick/<iid>/` file is deleted only when every
UTC day its name spans is older than `--trade-retention-days` (default 7) **and** that
instrument-day's `verified_days` row in `candles_<venue>.db` is `pass` (`compare_klines`).
Otherwise it is kept, and each old kept (instrument, day) is reported with its reason:
`unverified` (no row) or `failed` (a kline mismatch). A day younger than the window is kept
silently. A file starting within the arrival margin (5 min) after midnight also needs the
previous day proven: its trades' `ts_event` can belong to it. Every deleted trade file is first
recorded as a `pruned` archive gap so a later rebuild keeps those rows' live values.

Plan retention (with `--dydx-plan`, the dYdX collection plan's `config.toml`, read through
collection control's one loader `dydx_collector.config.load_config`): dYdX leaves whose instrument
the plan no longer collects lose every type except `trade_tick` once older than
`non_config_retain_hours`; a collected instrument with `store_order_book_deltas` and a finite
`retain_hours` loses its older `order_book_deltas`. Applied to DYDX leaves only, for `--venue DYDX`
or no `--venue`. This replaced the dYdX collector's in-process `_prune_loop` (Story 25.1).

A file whose name does not parse is skipped and reported, never deleted; a file whose span reaches
the current UTC day is never deleted (the next nightly takes it). `--venue V` limits every policy
to instruments whose id ends in `.V`. `--apply` takes the catalog maintenance flock; a report-only
run deletes nothing and takes no lock, so it can run beside a nightly. An unusable plan file
(unreadable, empty, malformed, a window that is not finite hours >= 0) is `prune.bad_plan`, exit 1.
A dYdX instrument with a file reaching the current UTC day is never treated as dropped.

Exit code: 0 done; 2 findings -- a decided deletion did not happen: a per-file error
(`prune.error`), a file that reached the open day by the time it was deleted (`prune.open_day`) or
a trade file kept because its `pruned` marker could not be written (`archive_gaps.write`), all
ledgered; 1 a run-level failure (bad arguments, a missing catalog, an unusable plan, the lock
held).
"""

import argparse
import logging
import time
from collections.abc import Callable
from pathlib import Path

from candles.infrastructure.verified_days import VerifiedDaysDir
from dydx_collector.config import load_config
from kernel.venues import VENUE_KINDS
from observability import error_ledger

from archive.application.catalog_check import catalog_missing
from archive.application.ports import RetentionPlanSource
from archive.application.prune import PruneReport
from archive.application.prune import decide
from archive.application.prune import execute
from archive.application.prune import log_summary
from archive.domain.retention import TRADE_TICK
from archive.domain.retention import PlanRetention
from archive.domain.retention import RetentionDecision
from archive.domain.retention import RetentionPolicy
from archive.infrastructure.gap_markers import GapMarkerFiles
from archive.infrastructure.maintenance_lock import MAINTENANCE_LOCK_NAME
from archive.infrastructure.maintenance_lock import maintenance


logger = logging.getLogger(__name__)

_FINDINGS = 2  # finished, but a decided deletion did not happen (ledgered)


# How long a dYdX plan read waits between its two fingerprints (see `DydxPlanFile`).
PLAN_SETTLE_SECONDS = 1.0


class DydxPlanFile:
    """
    `RetentionPlanSource` over the dYdX collection plan file (`dydx_collector/config.toml`).

    Invariant: see `archive.application.ports.RetentionPlanSource` -- read fresh through the one
    plan loader on every run. An empty file is refused (`ValueError`): it is the committed
    placeholder a missing bind mount leaves in place, and read as "collect nothing" it would age
    out every dYdX instrument's data. A plan listing no instruments (e.g. read mid-save) is
    refused for the same reason.

    A plan that changes while it is read is refused too: `dydx_collector.config.save_config`
    truncates and rewrites the bind-mounted file in place, and a read landing mid-save can parse
    as a valid plan listing only the first instruments, which would age out the rest. The file's
    bytes and stat are taken, then `settle` waits, the plan is loaded, and both are taken again;
    any difference means a save was in flight.
    Known limit: a save stalled for the whole settle window with the same partial bytes on both
    reads passes. The upgrade path is an atomic save (temp file + `os.replace`), which needs the
    compose file to mount the plan's directory rather than the file itself (a rename over a
    single-file bind mount fails with EBUSY).
    """

    def __init__(self, path: str | Path, settle: Callable[[], object] | None = None) -> None:
        self._path = Path(path)
        self._settle = settle or (lambda: time.sleep(PLAN_SETTLE_SECONDS))

    def _fingerprint(self) -> tuple[int, int, bytes]:
        stat = self._path.stat()
        return stat.st_mtime_ns, stat.st_size, self._path.read_bytes()

    def retention(self) -> PlanRetention:
        before = self._fingerprint()
        if before[1] == 0:
            raise ValueError(f"{self._path} is empty (the placeholder, not a collection plan)")
        self._settle()
        config = load_config(self._path)
        if self._fingerprint() != before:
            raise ValueError(f"{self._path} changed while being read (a save in flight?)")
        if not config.instruments:  # e.g. a half-saved plan: never "every leaf was dropped"
            raise ValueError(f"{self._path} lists no instruments; refusing to age out every leaf")
        return PlanRetention(
            collected=frozenset(e.id for e in config.instruments),
            non_config_retain_hours=config.non_config_retain_hours,
            delta_retain_hours={
                e.id: e.retain_hours for e in config.instruments if e.store_order_book_deltas
            },
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--types", nargs="+", help="data types for plain age retention")
    parser.add_argument("--days", type=int, default=14, help="age retention for --types")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="delete (default: report only)")
    mode.add_argument("--dry-run", action="store_true", help="report only (the default)")
    parser.add_argument("--candles-dir", help="directory of candles_<venue>.db; enables trades")
    parser.add_argument("--trade-retention-days", type=int, default=7)
    parser.add_argument(
        "--venue", choices=sorted(VENUE_KINDS), help="only instruments of this venue, e.g. DYDX"
    )
    parser.add_argument(
        "--dydx-plan", help="the dYdX collection plan (config.toml); enables plan retention"
    )
    return parser


def _plan(args: argparse.Namespace, source: RetentionPlanSource | None) -> PlanRetention | None:
    if source is None:
        return None
    if args.venue not in (None, "DYDX"):
        logger.info("  --dydx-plan ignored: plan retention applies to DYDX leaves only")
        return None
    return source.retention()


def _policy(args: argparse.Namespace, plan: PlanRetention | None) -> RetentionPolicy:
    return RetentionPolicy(
        now_ns=time.time_ns(),
        trade_retention_days=args.trade_retention_days if args.candles_dir else None,
        age_types=frozenset(args.types or ()),
        age_days=args.days,
        plan=plan,
    )


def _validated(parser: argparse.ArgumentParser, argv: list[str] | None) -> argparse.Namespace:
    args = parser.parse_args(argv)
    if args.types and TRADE_TICK in args.types:
        parser.error(
            "trade_tick has its own verification-gated policy (--candles-dir), not --types"
        )
    if not args.types and not args.candles_dir and not args.dydx_plan:
        parser.error("nothing to do: give --types, --candles-dir and/or --dydx-plan")
    if args.days < 1 or args.trade_retention_days < 1:
        parser.error("--days and --trade-retention-days must be at least 1")
    return args


def _decide(args: argparse.Namespace, policy: RetentionPolicy) -> RetentionDecision:
    """Decide through the `VerifiedDays` port (one read-only connection per venue, then closed)."""
    if not args.candles_dir:
        return decide(args.catalog, policy, None, args.venue)
    with VerifiedDaysDir(args.candles_dir) as verified:
        return decide(args.catalog, policy, verified, args.venue)


def _execute(args: argparse.Namespace, policy: RetentionPolicy) -> PruneReport | None:
    """Apply under the maintenance flock (None when it is held); a report takes no lock."""
    markers = GapMarkerFiles(args.catalog)
    if not args.apply:
        return execute(_decide(args, policy), None, markers)
    with maintenance(args.catalog) as writer:
        if writer is None:
            logger.error("prune: another run holds %s; not starting", MAINTENANCE_LOCK_NAME)
            return None
        return execute(_decide(args, policy), writer, markers)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the exit code (0 done, 2 findings, 1 run-level failure)."""
    args = _validated(_parser(), argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if catalog_missing("prune", args.catalog):
        return 1
    try:
        plan = _plan(args, DydxPlanFile(args.dydx_plan) if args.dydx_plan else None)
    except (OSError, ValueError, KeyError, TypeError) as e:  # unreadable, empty, malformed plan
        error_ledger.record(
            "prune.bad_plan", f"dYdX plan {args.dydx_plan} unusable: {e}; nothing pruned", exc=e
        )
        return 1
    policy = _policy(args, plan)
    report = _execute(args, policy)
    if report is None:
        return 1
    log_summary(report, policy, args.apply)
    return _FINDINGS if report.has_findings() else 0


if __name__ == "__main__":
    raise SystemExit(main())
