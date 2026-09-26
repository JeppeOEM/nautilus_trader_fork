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
"""Story 25.1b: `ArchiveStatusBus`'s shape check and cache discipline (pure, no Redis)."""

import copy
import json

import pytest
from observability import error_ledger

from views import archive_status_bus
from views.archive_status_bus import ArchiveStatusBus


def _run(**overrides: object) -> dict:
    run = {
        "run_id": "r-1",
        "kind": "nightly",
        "day": "2026-09-25",
        "days": ["2026-09-25"],
        "started": "2026-09-26T03:07:00Z",
        "finished": "2026-09-26T03:41:12Z",
        "steps": [
            {"venue": "BYBIT", "name": "rebuild_seconds", "exit": 0, "duration_s": 12.5},
            {"venue": None, "name": "consolidate", "exit": 2, "duration_s": 3},
        ],
    }
    run.update(overrides)
    return run


def _status(**overrides: object) -> dict:
    status = {
        "next_run": "2026-09-27T03:07:00Z",
        "next_intraday": "2026-09-26T16:07:00Z",
        "running": None,
        "last_run": _run(),
        "last_intraday": None,
    }
    status.update(overrides)
    return status


def _ledger_count() -> int:
    return error_ledger.counts().get("views.archive_status", 0)


def test_channel_is_archive_status() -> None:
    assert archive_status_bus.ARCHIVE_STATUS_CHANNEL == "archive:status"


def test_latest_is_none_before_any_message() -> None:
    assert ArchiveStatusBus().latest is None


def test_valid_message_is_cached_verbatim() -> None:
    bus = ArchiveStatusBus()
    message = _status()

    bus.handle_message(message)

    assert bus.latest == message


def test_minimal_message_with_null_last_run_is_accepted() -> None:
    bus = ArchiveStatusBus()

    bus.handle_message({"next_run": "2026-09-27T03:07:00Z", "last_run": None})

    assert bus.latest is not None


def test_additive_keys_are_tolerated() -> None:
    bus = ArchiveStatusBus()
    message = _status(future_key=1, running=_run(finished=None, extra="x"))

    bus.handle_message(message)

    assert bus.latest == message


@pytest.mark.parametrize(
    "message",
    [
        "not-a-dict",
        [],
        {"next_run": "2026-09-27T03:07:00Z"},  # last_run key missing
        {"last_run": None},  # next_run missing
        _status(next_run=123),
        _status(next_intraday=5),
        _status(last_run="yesterday"),
        _status(last_run=_run(finished=None)),  # a completed run must say when it finished
        _status(last_run=_run(steps="none")),
        _status(last_run=_run(steps=[{"venue": None, "name": "x", "exit": "0", "duration_s": 1}])),
        _status(last_run=_run(steps=[{"venue": None, "name": "x", "exit": True, "duration_s": 1}])),
        _status(last_run=_run(steps=[{"venue": 1, "name": "x", "exit": 0, "duration_s": 1}])),
        _status(running=_run(run_id=None)),
        _status(last_intraday=_run(kind=None)),
        _status(last_run=_run(days=[20260925])),
        _status(last_run=_run(days="2026-09-25")),
        _status(running=_run(finished=5)),
    ],
)
def test_malformed_message_is_ledgered_and_keeps_previous_cache(message: object) -> None:
    bus = ArchiveStatusBus()
    good = _status()
    bus.handle_message(good)
    before = _ledger_count()

    bus.handle_message(copy.deepcopy(message))

    assert bus.latest == good
    assert _ledger_count() == before + 1


def test_unparseable_payload_is_ledgered_and_keeps_cache() -> None:
    bus = ArchiveStatusBus()
    good = _status()
    bus._ingest(json.dumps(good))
    before = _ledger_count()

    bus._ingest("{not json")

    assert bus.latest == good
    assert _ledger_count() == before + 1
