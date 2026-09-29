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
The reference recorder's composition root (Story 31.1):

    python3 -m verification.recorder --venue BYBIT|HYPERLIQUID

Records every raw WebSocket frame and REST poll response of the venue's collected instruments,
verbatim with its local receive time, to `<VERIFY_DATA_DIR>/raw/<venue>/<channel>/<UTC
hour>.jsonl.zst` (`docs/DATA_DICTIONARY.md` section 1.15). The reference side never imports the
code it checks: this client shares no code with the collectors (aiohttp, `json`, the standard
library, plus `kernel.venue_http` URLs, `kernel.venues` id parsing and `observability`).

Environment:
- `VERIFY_DATA_DIR` (required): the data root; nothing is written anywhere else.
- `VERIFY_RETAIN_DAYS` (default 7): UTC days of raw files kept, today included.
- `BYBIT_COLLECTOR_CONFIG` / `HYPERLIQUID_COLLECTOR_CONFIG`: the venue's `config.toml`, the same
  variables and defaults as the collectors (`capture/venues/<venue>/config.toml` beside this
  package), re-read every 30 s.
- `ERROR_LEDGER_DIR` / `ERROR_LEDGER_SERVICE`: the durable error ledger (DATA-07).

The process runs with `umask 002`: the recorders run as uid 1001 in the collectors' group 1000
(`docker-compose.verify.yml`), so the host user and `make verify-wipe` can delete their files.
SIGTERM and SIGINT close every connection (a `shutdown` connection line) and every zstd stream.
"""

import argparse
import asyncio
import logging
import os
import signal
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

import aiohttp
from observability import error_ledger

from verification.application import sites
from verification.application.ports import Ledger
from verification.application.recorder import Recorder
from verification.application.recorder import Timing
from verification.application.venue_urls import kernel_endpoints
from verification.application.venue_urls import kernel_wiring
from verification.domain.plan_file import RecordingPlan
from verification.domain.plan_file import parse_plan
from verification.domain.subscriptions import VENUES
from verification.infrastructure.aiohttp_io import AiohttpConnector
from verification.infrastructure.aiohttp_io import AiohttpHttp
from verification.infrastructure.raw_store import RawStore


logger = logging.getLogger(__name__)

CONFIG_ENV = MappingProxyType(
    {"BYBIT": "BYBIT_COLLECTOR_CONFIG", "HYPERLIQUID": "HYPERLIQUID_COLLECTOR_CONFIG"}
)
DEFAULT_RETAIN_DAYS = 7
MAX_RETAIN_DAYS = 36500  # a century: anything larger is a typo, not a policy
UMASK = 0o002


def config_path(venue: str, environ: Mapping[str, str]) -> Path:
    """Return the config the collector reads: its env var, else the file beside its package."""
    default = Path(__file__).resolve().parent.parent / "capture" / "venues" / venue.lower()
    return Path(environ.get(CONFIG_ENV[venue], str(default / "config.toml")))


def read_plan_file(path: Path, venue: str) -> RecordingPlan:
    """Read and parse the venue's `config.toml` into the recorded plan."""
    return parse_plan(path.read_text(), venue)


def retain_days(environ: Mapping[str, str]) -> int:
    """
    `VERIFY_RETAIN_DAYS`: ASCII digits only (`str.isdigit` alone admits `"²"` or Arabic-Indic
    digits), 1 to `MAX_RETAIN_DAYS` days.
    """
    raw = environ.get("VERIFY_RETAIN_DAYS", str(DEFAULT_RETAIN_DAYS))
    # Only a few significant digits reach `int()`: over 4300 raise its own ValueError.
    significant = raw.lstrip("0") or "0"
    digits = raw.isascii() and raw.isdigit() and len(significant) <= len(str(MAX_RETAIN_DAYS))
    if not digits or not 1 <= int(significant) <= MAX_RETAIN_DAYS:
        raise SystemExit(
            f"VERIFY_RETAIN_DAYS must be a whole number of days from 1 to {MAX_RETAIN_DAYS} "
            f"(ASCII digits), got {raw!r}"
        )
    return int(significant)


def data_dir(environ: Mapping[str, str]) -> Path:
    """`VERIFY_DATA_DIR`, required: a recorder never writes into an unmounted default."""
    raw = environ.get("VERIFY_DATA_DIR", "")
    if not raw:
        raise SystemExit("VERIFY_DATA_DIR is required (the raw recording root)")
    return Path(raw)


def startup_plan(plan_path: Path, venue: str, ledger: Ledger) -> RecordingPlan:
    """
    Read the plan the recorder starts with. An unreadable file, an unknown environment or an
    unrecorded category refuses start -- ledgered first, so a crash-looping container leaves its
    cause in the durable ledger, not only in a log that restarts with it (DATA-07).
    """
    try:
        plan = read_plan_file(plan_path, venue)
        kernel_endpoints(plan)
    except (OSError, ValueError) as exc:
        ledger(sites.PLAN, f"{venue}: {plan_path} refused at start", exc)
        raise
    return plan


async def serve(venue: str, root: Path, retain: int, plan_path: Path) -> None:
    """Record `venue` until SIGTERM/SIGINT. An unreadable plan at start refuses start."""
    plan = startup_plan(plan_path, venue, error_ledger.record)
    logger.info(
        "%s: recording %s (%s) into %s", venue, list(plan.instruments), plan.environment, root
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    store = RawStore(root, venue, retain, error_ledger.record)
    async with aiohttp.ClientSession() as session:
        recorder = Recorder(
            plan,
            lambda: read_plan_file(plan_path, venue),
            kernel_wiring(),
            AiohttpConnector(session),
            AiohttpHttp(session),
            store,
            error_ledger.record,
            Timing(),
        )
        await recorder.run(stop)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python3 -m verification.recorder")
    parser.add_argument("--venue", required=True, choices=VENUES)
    args = parser.parse_args(argv)
    os.umask(UMASK)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    error_ledger.start()
    try:
        root, retain = data_dir(os.environ), retain_days(os.environ)
    except SystemExit as exc:  # ledgered first: a crash-looping container leaves its cause
        error_ledger.record(sites.PLAN, f"{args.venue}: refused at start: {exc}")
        raise
    asyncio.run(serve(args.venue, root, retain, config_path(args.venue, os.environ)))


if __name__ == "__main__":
    main()
