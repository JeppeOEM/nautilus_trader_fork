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
The `archive` service: nightly maintenance scheduled in our own code (Story 25.1b; compose service
`archive`, `python3 -m archive.scheduler`). It replaces the host crontab line.

What it runs and when is `archive.application.scheduler`'s docstring; the schedule is
`archive/config.toml` (`ARCHIVE_CONFIG`). This composition root wires the chains, each step a
child process:
- per venue-day, `archive.nightly.steps` (the nightly saga);
- `archive.consolidate_catalog --apply`, then `archive.backup_catalog` when `backup_enabled`
  (Story 26.1b), after every full run;
- `archive.consolidate_catalog --apply --closed-hours`, the intraday merge.

It also wires the lock probe, the `state.json` store and the Redis status bus and control
channel, then runs the loop.

Environment:
- `REDIS_URL`
- `CATALOG_PATH` (default `/app/catalog`)
- `CANDLES_DIR` (default `/app/candles_dir`)
- `DYDX_PLAN_PATH`: the dYdX collection plan, for its retention (default
  `/app/dydx_collector/config.toml`)
- `ARCHIVE_STATE_DIR` (default `/app/archive_state`)
- `ARCHIVE_CONFIG` (default: the package's `config.toml`)
- `RCLONE_REMOTE`/`RCLONE_BUCKET`/`RCLONE_CONFIG`, read by the backup step. With
  `backup_enabled = true` and either of the first two unset or blank, the service refuses to start
  (`archive.config_invalid`, exit 1), rather than fail every night; with it false they are unused.

Status is published on `archive:status`; `{"command": "run_now", "day": "YYYY-MM-DD" | null}` on
`archive:control` queues one day's full run.
"""

import argparse
import asyncio
import datetime as dt
import logging
import os
import signal
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

from observability import error_ledger

from archive.application.nightly import Step
from archive.application.nightly import StepRunner
from archive.application.scheduler import LOCK_TIMEOUT_EXIT
from archive.application.scheduler import ArchiveScheduler
from archive.application.scheduler import Chains
from archive.backup_catalog import configured_target
from archive.infrastructure.maintenance_lock import MaintenanceLockProbe
from archive.infrastructure.redis_bus import RedisControlChannel
from archive.infrastructure.redis_bus import RedisStatusBus
from archive.infrastructure.scheduler_config import load_scheduler_config
from archive.infrastructure.state_store import JsonStateStore
from archive.nightly import peak_child_rss_mb
from archive.nightly import steps


logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = Path(__file__).resolve().parent / "config.toml"


class SystemClock:
    """`Clock` over the host's wall clock. Invariant: always a tz-aware UTC instant."""

    def now(self) -> dt.datetime:
        return dt.datetime.now(dt.UTC)


def timed_runner(timeout_seconds: float) -> StepRunner:
    """
    Build a `StepRunner` whose child is killed after `timeout_seconds` (the step then exits 124,
    the same code as a lock timeout: no real exit code exists). Without it a hung child -- an
    rclone stalled on a dead TCP connection, a wedged rebuild -- would hold the job loop forever.
    """

    def run(argv: list[str]) -> int:
        try:
            return subprocess.run(argv, check=False, timeout=timeout_seconds).returncode  # noqa: S603 (our own module argv)
        except subprocess.TimeoutExpired:  # subprocess.run has already killed and reaped it
            error_ledger.record(
                "archive.step_timeout",
                f"{' '.join(argv[1:4])}: still running after {timeout_seconds:.0f}s; killed",
            )
            return LOCK_TIMEOUT_EXIT

    return run


def build_chains(
    catalog: str, candles_dir: str, dydx_plan: str | None, backup_enabled: bool
) -> Chains:
    """
    Each job's chain of child-process steps (a step's name is its `nightly.<name>` ledger); no
    backup chain when `backup_enabled` is false.
    """

    def module(dotted: str, *args: str) -> list[str]:
        """One step's argv (`tests/test_images.py` reads the dotted path from this call)."""
        return [sys.executable, "-m", dotted, *args]

    def nightly(venue: str, day: str, result_file: str) -> list[Step]:
        return steps(catalog, candles_dir, venue, day, result_file, dydx_plan)

    def consolidate() -> list[Step]:
        return [
            Step(
                "consolidate_catalog",
                module("archive.consolidate_catalog", "--catalog", catalog, "--apply"),
            )
        ]

    def closed_hours() -> list[Step]:
        return [
            Step(
                "consolidate_closed_hours",
                module(
                    "archive.consolidate_catalog", "--catalog", catalog, "--apply", "--closed-hours"
                ),
            )
        ]

    def backup() -> list[Step]:
        return [Step("backup_catalog", module("archive.backup_catalog", "--catalog", catalog))]

    return Chains(nightly, consolidate, closed_hours, backup if backup_enabled else None)


def build_scheduler(env: Mapping[str, str]) -> tuple[ArchiveScheduler, str]:
    """
    Wire the scheduler; return it and the Redis URL its control channel subscribes to.
    `ValueError` for an invalid config, or an enabled backup with no target (`configured_target`,
    the backup step's own rule); `OSError` for a missing or unreadable config file.
    """
    catalog = env.get("CATALOG_PATH", "/app/catalog")
    config = load_scheduler_config(env.get("ARCHIVE_CONFIG") or _DEFAULT_CONFIG)
    if config.backup_enabled and configured_target(env) is None:
        raise ValueError(
            "backup_enabled = true but RCLONE_REMOTE or RCLONE_BUCKET is not set (platform/.env; "
            "README 'Nightly maintenance'); set both, or backup_enabled = false"
        )
    chains = build_chains(
        catalog,
        env.get("CANDLES_DIR", "/app/candles_dir"),
        env.get("DYDX_PLAN_PATH", "/app/dydx_collector/config.toml"),
        config.backup_enabled,
    )
    redis_url = env.get("REDIS_URL", "redis://127.0.0.1:6379")
    scheduler = ArchiveScheduler(
        config,
        chains,
        timed_runner(config.step_timeout_minutes * 60.0),
        MaintenanceLockProbe(catalog),
        JsonStateStore(env.get("ARCHIVE_STATE_DIR", "/app/archive_state")),
        RedisStatusBus(redis_url),
        SystemClock(),
        peak_rss_mb=peak_child_rss_mb,
    )
    return scheduler, redis_url


async def _serve(scheduler: ArchiveScheduler, redis_url: str) -> None:
    """Serve until SIGTERM/SIGINT (compose stop) cancels the loop."""
    task = asyncio.current_task()
    if task is None:  # asyncio.run always runs this as a task
        raise RuntimeError("archive scheduler: not running as an asyncio task")
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, task.cancel)
    try:
        await scheduler.serve(RedisControlChannel(redis_url))
    except asyncio.CancelledError:
        logger.info("archive scheduler: stopping")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: run the service (it takes no arguments; the environment configures it)."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    error_ledger.start()
    try:
        scheduler, redis_url = build_scheduler(os.environ)
    except (ValueError, OSError) as e:  # a bad key or value, or a missing/unreadable config file
        error_ledger.record("archive.config_invalid", f"archive service not started: {e}", e)
        return 1
    asyncio.run(_serve(scheduler, redis_url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
