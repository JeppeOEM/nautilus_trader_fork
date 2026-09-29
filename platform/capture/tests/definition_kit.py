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
Instrument definitions for capture's tests (Story 30.2): the service encodes each snapshot at its
instrument definition's precisions (`CaptureService._instruments`, filled by `run()` from the
venue), so a test that samples without `run()` hands it definitions here, exactly as `run()` would.
"""

from decimal import Decimal

from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


# The tests' book and trade builders use `Price(x, 2)` and `Quantity(x, 3)`.
PRICE_PRECISION = 2
SIZE_PRECISION = 3


def definition(
    iid: str, price_precision: int = PRICE_PRECISION, size_precision: int = SIZE_PRECISION
) -> CryptoPerpetual:
    """Build a definition of `iid` at the given precisions (increment one unit of each)."""
    return CryptoPerpetual(
        instrument_id=InstrumentId.from_str(iid),
        raw_symbol=Symbol(iid.split(".")[0]),
        base_currency=BTC,
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=price_precision,
        price_increment=Price(Decimal(1).scaleb(-price_precision), price_precision),
        size_precision=size_precision,
        size_increment=Quantity(Decimal(1).scaleb(-size_precision), size_precision),
        ts_event=0,
        ts_init=0,
    )


def definitions(*iids: str) -> dict[str, Instrument]:
    """`CaptureService._instruments` for `iids`, at the tests' precisions."""
    return {iid: definition(iid) for iid in iids}
