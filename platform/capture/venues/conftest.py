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
Close every candle store a venue test opens.

Each venue's `build_capture` opens a real `CandleStore` (via `store_from_env`) and hands it to the
`CaptureService` as its `SecondSink`; the service closes it only when its `run()` ends. Most venue
tests build a capture and never run it, so without this each one leaks a SQLite connection,
reported as `ResourceWarning: unclosed database` at garbage collection (TEST-04).

Wrapping `CandleStore.__init__` is the one place that sees every store, whatever import path or
helper built it, so a new `build_capture` caller is covered without editing it. Closing an already
closed store (one whose capture did run) is a no-op.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from candles.infrastructure.sqlite_store import CandleStore


@pytest.fixture(autouse=True)
def _close_candle_stores(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    opened: list[CandleStore] = []
    real_init = CandleStore.__init__

    def _recording_init(self: CandleStore, *args: Any, **kwargs: Any) -> None:
        real_init(self, *args, **kwargs)
        opened.append(self)

    monkeypatch.setattr(CandleStore, "__init__", _recording_init)
    yield
    for store in opened:
        store.close()
