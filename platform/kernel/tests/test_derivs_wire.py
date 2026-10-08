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
"""The `derivs:raw` row (Story 33.4): one exact round trip per kind, the funding unit, bad rows."""

import json
from decimal import Decimal
from typing import Any

import pytest

from kernel.derivs_wire import DerivsTick
from kernel.derivs_wire import from_wire
from kernel.derivs_wire import to_wire
from kernel.open_interest import OpenInterest
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.model.data import FundingRateUpdate
from nautilus_trader.model.data import IndexPriceUpdate
from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


_IID = "BTCUSDT-LINEAR.BYBIT"
_T = 1_759_700_000_000_000_000


def _funding(interval: int | None = 480, next_ns: int | None = _T + 1) -> FundingRateUpdate:
    # Built the way the Bybit client builds it: the Rust object, then `from_pyo3`.
    raw = nautilus_pyo3.FundingRateUpdate(
        nautilus_pyo3.InstrumentId.from_str(_IID), Decimal("0.0001"), _T, _T + 5, interval, next_ns
    )
    return FundingRateUpdate.from_pyo3(raw)


def _over_json(row: dict[str, Any] | None) -> DerivsTick:
    assert row is not None
    return from_wire(json.loads(json.dumps(row)))


def test_mark_round_trips_with_its_exact_text() -> None:
    mark = MarkPriceUpdate(InstrumentId.from_str(_IID), Price.from_str("100.50"), _T, _T + 5)
    assert _over_json(to_wire(mark)) == DerivsTick(_IID, "mark", _T, _T + 5, Decimal("100.50"))


def test_index_round_trips_with_its_exact_text() -> None:
    index = IndexPriceUpdate(InstrumentId.from_str(_IID), Price.from_str("100.00"), _T, _T + 5)
    assert to_wire(index) == {
        "instrument_id": _IID,
        "kind": "index",
        "t": _T,
        "ts_init": _T + 5,
        "value": "100.00",
    }


def test_open_interest_round_trips() -> None:
    oi = OpenInterest(InstrumentId.from_str(_IID), Decimal("51234.567"), _T, _T + 5)
    assert _over_json(to_wire(oi)) == DerivsTick(_IID, "oi", _T, _T + 5, Decimal("51234.567"))


def test_funding_carries_its_interval_in_seconds_and_the_next_funding_time() -> None:
    assert to_wire(_funding()) == {
        "instrument_id": _IID,
        "kind": "funding",
        "t": _T,
        "ts_init": _T + 5,
        "value": "0.0001",
        "interval": 28_800,
        "next_funding_ns": _T + 1,
    }


def test_funding_interval_is_minutes_on_the_nautilus_object() -> None:
    """
    The pinned unit: Bybit's adapter stores `fundingIntervalHour * 60` (8 h -> 480), Hyperliquid a
    fixed 60, and Nautilus documents minutes. If this ever reads otherwise, `to_wire`'s x 60 is wrong.
    """
    assert _funding().interval == 480
    assert "interval : int\n    Time interval (minutes)" in (FundingRateUpdate.__doc__ or "")


def test_funding_without_interval_or_next_time_round_trips_as_none() -> None:
    tick = _over_json(to_wire(_funding(interval=None, next_ns=None)))
    assert (tick.interval, tick.next_funding_ns) == (None, None)


def test_a_zero_funding_interval_is_encoded_as_none_and_decodes() -> None:
    """A 0 interval (the venue sent none) is no interval: `from_wire` refuses a zero one."""
    tick = _over_json(to_wire(_funding(interval=0)))
    assert tick.interval is None


def test_any_other_type_is_not_a_derivs_row() -> None:
    trade = TradeTick(
        InstrumentId.from_str(_IID),
        Price.from_str("1.0"),
        Quantity.from_str("1"),
        AggressorSide.BUYER,
        TradeId("a"),
        _T,
        _T,
    )
    assert to_wire(trade) is None


def _good() -> dict[str, Any]:
    return {
        "instrument_id": _IID,
        "kind": "funding",
        "t": _T,
        "ts_init": _T,
        "value": "0.0001",
        "interval": 28_800,
        "next_funding_ns": _T,
    }


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "basis"},
        {"kind": None},
        {"instrument_id": ""},
        {"instrument_id": 7},
        {"t": "1"},
        {"t": -1},
        {"t": True},
        {"ts_init": None},
        {"value": 0.0001},
        {"value": None},
        {"value": "abc"},
        {"value": "NaN"},
        {"value": "Infinity"},
        {"interval": 0},
        {"interval": "28800"},
        {"next_funding_ns": 1.5},
    ],
)
def test_a_malformed_row_is_refused(change: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="derivs row"):
        from_wire({**_good(), **change})


def test_a_row_missing_its_value_is_refused() -> None:
    row = _good()
    del row["value"]
    with pytest.raises(ValueError, match="not exact text"):
        from_wire(row)


@pytest.mark.parametrize("row", [None, [], "row"])
def test_a_row_that_is_not_an_object_is_refused(row: object) -> None:
    with pytest.raises(ValueError, match="not an object"):
        from_wire(row)


def test_an_unknown_added_key_is_ignored() -> None:
    assert from_wire({**_good(), "later": 1}) == from_wire(_good())


def test_funding_keys_on_another_kind_are_not_read() -> None:
    tick = from_wire({**_good(), "kind": "mark", "interval": "junk"})
    assert (tick.interval, tick.next_funding_ns) == (None, None)


@pytest.mark.parametrize(
    ("value", "text"),
    [("0.00000012", "0.00000012"), ("1.2E-7", "0.00000012"), ("0E-8", "0.00000000")],
)
def test_the_value_is_positional_text_and_decodes_back_exactly(value: str, text: str) -> None:
    """`str(Decimal("0.00000012"))` is `1.2E-7`; the wire carries the positional form."""
    tick = DerivsTick(_IID, "oi", 1, 2, Decimal(value))
    row = tick.to_wire()
    assert row["value"] == text
    assert from_wire(row).value == Decimal(value)
