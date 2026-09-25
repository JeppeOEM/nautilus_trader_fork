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
"""The ports capture declares: what a downstream context must offer for capture to feed it."""

from collections.abc import Mapping
from collections.abc import Sequence
from typing import Protocol

from kernel.second_snapshot import SecondRow


class SecondSink(Protocol):
    """
    Where capture hands the 1 s rows it has just archived.

    Invariant (a derived reader is never ahead of the archive): `apply` is called from `_flush_once`
    only with the rows whose `ParquetDataCatalog.write_data` returned, never with the buffer -- so
    nothing downstream can hold a second the Parquet archive does not. `watermarks` closes the other
    half: at startup capture reads each instrument's last applied `ts_event` and replays the archive
    from there, so a crash between a flush and its sink write is filled rather than lost forever.
    The commands that would violate it are applying `self._buffer` instead of `flushed_seconds`, and
    skipping the catch-up on the argument that the live feed will fill in anyway.

    Declared here, in capture, and satisfied structurally: the implementation (today
    `candles.application.sink.CandleSink`) never imports this module, so the dependency arrow runs
    capture -> port <- candles and no capture -> candles import exists (spine AD-D2/AD-D8).

    `apply` returns the number of seconds it actually took (rows at or before its own watermark are
    a replay and count 0). It may raise: capture ledgers the failure per instrument and carries on.
    """

    def apply(self, instrument_id: str, rows: Sequence[SecondRow]) -> int: ...

    def watermarks(self) -> Mapping[str, int]: ...
