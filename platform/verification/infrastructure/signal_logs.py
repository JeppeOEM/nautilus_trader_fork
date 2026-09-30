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
A directory of bot signal logs (Story 31.9): `<dir>/<bot_id>.jsonl`, one JSON record per line, as
`bots.strategies.signal_log` writes them (live) and `bots.signal_replay` writes them (replay). Read
as text only; the records are parsed by `verification.domain.bot_parity`.
"""

from collections.abc import Iterator
from pathlib import Path


SUFFIX = ".jsonl"


class SignalLogDir:
    """
    One directory of signal logs, read-only. Invariant: every complete line is yielded, in file
    order, with its `file:line`; only a final line without its newline is held back -- the writer
    flushes each record as one whole line, so an unterminated tail is a write still in flight (a
    live fleet writes while the tool reads) and is read by the next run, never parsed half-written.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> str:
        return str(self._root)

    def names(self) -> list[str]:
        """Return the bot ids with a log here, sorted."""
        return sorted(path.name.removesuffix(SUFFIX) for path in self._root.glob(f"*{SUFFIX}"))

    def lines(self, name: str) -> Iterator[tuple[str, str]]:
        path = self._root / f"{name}{SUFFIX}"
        with path.open(encoding="utf-8") as handle:
            for number, text in enumerate(handle, start=1):
                if not text.endswith("\n"):
                    return
                yield f"{path}:{number}", text[:-1]
