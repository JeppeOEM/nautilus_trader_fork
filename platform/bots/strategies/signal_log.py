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
The opt-in per-cycle signal log of `DummyStrategy` (Story 31.9): one JSON line per decision cycle
(every 1 s book timer event and every bar), so a live paper run and its catalog replay
(`bots.signal_replay`) can be compared cycle by cycle by `verification.bot_parity`.

Record kinds: `start` (one per `on_start`: the clock at start and the config's thresholds, levels,
windows and bar spec), `book`, `book_skipped` (reason `no_book` or `one_sided`: a skipped second is
visible, never silent, DATA-07) and `bar`. Every float is written with `json`'s shortest repr,
which round-trips to the exact double the indicators were fed (a NaN is written as `NaN`,
which Python's `json` reads back; it is never dropped).

`signal_of` is the one statement of the strategy's entry rule: `DummyStrategy._wanted_side`
decides from it, and the log records it, so the logged `signal` can never disagree with the side
the strategy acted on.

Known limit: the log is opt-in (`DummyStrategyConfig.signal_log_path`, set by the host from
`BOT_SIGNAL_LOG_DIR`) and is one file per bot, growing for the whole run (about 1 KB per cycle,
so about 86 MB per bot per day). Upgrade path: hourly rotation (`<bot_id>.<hour>.jsonl`).
"""

import json
from pathlib import Path
from typing import IO
from typing import Any


SIGNAL_LONG = "long"
SIGNAL_SHORT = "short"
SIGNAL_NONE = "none"
SIGNAL_NOT_READY = "not_ready"

SKIP_NO_BOOK = "no_book"
SKIP_ONE_SIDED = "one_sided"


def signal_of(
    trend: float | None,
    mlofi: float | None,
    buy_threshold: float,
    sell_threshold: float,
    confirm_threshold: float,
) -> str:
    """
    Return the decision one cycle's signals call for: `not_ready` while either input is uninitialized
    (None), `long` when the trend probability exceeds the buy threshold confirmed by a multi-level
    OFI above the confirm threshold, `short` symmetrically, `none` otherwise.
    """
    if trend is None or mlofi is None:
        return SIGNAL_NOT_READY
    if trend > buy_threshold and mlofi > confirm_threshold:
        return SIGNAL_LONG
    if trend < sell_threshold and mlofi < -confirm_threshold:
        return SIGNAL_SHORT
    return SIGNAL_NONE


class SignalLog:
    """
    An append-only JSON-lines file of one bot's decision cycles.

    Invariant: every record is one complete line in the file (written and flushed as one line), and
    nothing is written after `close` -- a record from a stopped strategy would be a cycle that
    never decided anything. Violated by: `write` after `close` (raises).
    """

    def __init__(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._file: IO[str] | None = open(path, "a", encoding="utf-8")  # noqa: SIM115

    def write(self, record: dict[str, Any]) -> None:
        if self._file is None:
            raise RuntimeError("signal log written after close")
        self._file.write(json.dumps(record, separators=(",", ":")) + "\n")
        self._file.flush()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
