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
"""Tests for bot_tui.archive_state -- Story 25.1b. Pure logic, no Redis."""

import pytest

from bot_tui import archive_state


def _status() -> dict:
    return {"next_run": "2026-09-27T03:07:00Z", "last_run": None, "extra": 1}


def test_channel_is_archive_status() -> None:
    assert archive_state.ARCHIVE_STATUS_CHANNEL == "archive:status"


def test_valid_message_is_kept_verbatim_and_fresh() -> None:
    message = _status()

    archive_state._handle_status_message(message)

    assert message == archive_state._LATEST_ARCHIVE_STATUS
    assert not archive_state.is_stale()


@pytest.mark.parametrize(
    "message",
    ["text", ["x"], {"last_run": None}, {"next_run": "2026-09-27T03:07:00Z"}],
)
def test_malformed_message_keeps_the_previous_status(message: object) -> None:
    good = _status()
    archive_state._handle_status_message(good)

    archive_state._handle_status_message(message)

    assert good == archive_state._LATEST_ARCHIVE_STATUS


def test_stale_before_any_message_and_after_missed_heartbeats() -> None:
    assert archive_state.is_stale()
    archive_state._handle_status_message(_status())
    received = archive_state._LATEST_RECEIVED_AT

    assert not archive_state.is_stale(now=received + archive_state._STATUS_STALE_SECONDS)
    assert archive_state.is_stale(now=received + archive_state._STATUS_STALE_SECONDS + 1)
