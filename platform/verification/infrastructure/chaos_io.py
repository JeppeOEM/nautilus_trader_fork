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
Fault injection's adapters (Story 31.10), wired only by `verification.chaos`: the subprocess
runner, the scenario log file (append + fsync), the name resolver, the catalog leaf permission
walker and the collector ledger reader.
"""

import json
import os
import socket
import stat
import subprocess
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from observability import error_ledger

from verification.domain.chaos import CommandRecord


# A command that has not returned by then is recorded as failed (-1): the longest is the deploy's
# image build; every docker/iptables call of a fault returns in seconds.
COMMAND_TIMEOUT_S = 1800


def subprocess_runner(argv: Sequence[str]) -> CommandRecord:
    """Run one command (no shell), capturing its output; a timeout is returncode -1."""
    command = tuple(argv)
    try:
        done = subprocess.run(  # noqa: S603 (the tool's own fixed argv, never a shell)
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return CommandRecord(command, -1, f"timed out after {COMMAND_TIMEOUT_S} s")
    return CommandRecord(command, done.returncode, (done.stdout or "") + (done.stderr or ""))


class ScenarioLogFile:
    """The scenario log `<VERIFY_DATA_DIR>/chaos/scenarios.jsonl` (see `ScenarioLog`)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def lines(self) -> Iterator[tuple[str, str]]:
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as fh:
            for number, text in enumerate(fh, start=1):
                yield f"{self.path}:{number}", text.rstrip("\n")

    def append(self, line: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, separators=(",", ":")) + "\n")
            fh.flush()
            os.fsync(fh.fileno())


def resolve_host(host: str) -> tuple[str, ...]:
    """Return every IPv4/IPv6 address `getaddrinfo` gives the host for TCP (`OSError` if none)."""
    found = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    return tuple(sorted({str(sockaddr[0]) for *_, sockaddr in found}))


class CatalogLeaves:
    """The catalog's per-instrument leaf directories and their modes (see `LeafPermissions`)."""

    def __init__(self, catalog: Path) -> None:
        self._data = catalog / "data"

    def leaves(self, venue: str) -> tuple[str, ...]:
        suffix = f".{venue}"
        return tuple(
            sorted(str(p) for p in self._data.glob("*/*") if p.is_dir() and p.name.endswith(suffix))
        )

    def mode(self, path: str) -> int:
        return stat.S_IMODE(os.stat(path).st_mode)

    def set_mode(self, path: str, mode: int) -> None:
        os.chmod(path, mode)


def collector_service(venue: str) -> str:
    """Return a collector's ledger service (`docker-compose.yml`'s `ERROR_LEDGER_SERVICE`)."""
    return f"{venue.lower()}_collector"


class LedgerFiles:
    """
    The collectors' durable error ledgers (`<ERROR_LEDGER_DIR>/<venue>_collector.jsonl`, its
    rotated files included), read through `observability.error_ledger`'s own reader. That reader
    skips a line it cannot parse (a torn last line): a skipped line can only hide a site, which
    fails a required-site check -- never a false pass.
    """

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def has(self, venue: str) -> bool:
        return bool(error_ledger.ledger_files(self._directory, collector_service(venue)))

    def sites(self, venue: str, start_ns: int, end_ns: int) -> frozenset[str]:
        records = error_ledger.iter_records(
            self._directory, collector_service(venue), since_ns=start_ns, until_ns=end_ns - 1
        )
        return frozenset(str(record["site"]) for record in records)
