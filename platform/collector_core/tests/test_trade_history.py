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
The venue-neutral exact conversions of the reconnect backfill (`collector_core.domain.trade_history`,
story 22.14): a value is converted exactly or refused, never rounded. No network.
"""

import pytest

from collector_core.domain.trade_history import BackfillError
from collector_core.domain.trade_history import exact_text
from collector_core.domain.trade_history import iso_to_ns
from collector_core.tests.trade_history_kit import MS


# -- exactness -------------------------------------------------------------------------------------


def test_exact_text_pads_to_the_precision_without_rounding() -> None:
    assert exact_text("84765.0", 1) == "84765.0"
    assert exact_text("84776", 2) == "84776.00"
    assert exact_text("84700.00", 1) == "84700.0"  # trailing zeros only: still exact


@pytest.mark.parametrize(("text", "precision"), [("84765.05", 1), ("0.0000001", 6), ("abc", 2)])
def test_exact_text_refuses_what_it_would_have_to_round(text: str, precision: int) -> None:
    with pytest.raises(BackfillError):
        exact_text(text, precision)


def test_iso_created_at_is_exact_integer_nanoseconds() -> None:
    assert iso_to_ns("2026-09-21T11:36:07.507Z") == 1_789_990_567_507_000_000
    assert iso_to_ns("1970-01-01T00:00:00.001Z") == MS


def test_iso_without_timezone_is_refused() -> None:
    with pytest.raises(BackfillError):
        iso_to_ns("2026-09-21T11:36:07.507")
