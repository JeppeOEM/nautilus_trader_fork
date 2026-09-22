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
import logging

import pytest

from observability import error_ledger


def test_record_logs_error_with_traceback_and_counts(caplog: pytest.LogCaptureFixture) -> None:
    error_ledger.reset()
    try:
        raise ValueError("bad row")
    except ValueError as exc:
        with caplog.at_level(logging.ERROR):
            error_ledger.record("site.a", "row DROPPED", exc)
    error_ledger.record("site.a", "again")
    assert error_ledger.counts() == {"site.a": 2}
    assert error_ledger.last_details() == {"site.a": "again"}
    assert caplog.records[0].levelno == logging.ERROR
    assert caplog.records[0].exc_info is not None
    error_ledger.reset()
