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
`research.application.gallery`'s cascade rows (Story 33.14): the four specs' labels and default
stop, and `cascade_sample` on a tmp catalog -- a definition stored twice, a missing definition and
a missing feed are each stated, never a crash; and the four `OFIStrategy` forced-flow rows of Story
33.13 with their sample.

The sample catalog: `BTCUSDT-LINEAR.BYBIT` (price precision 2, size 3) and three LONG
liquidations received at 10 s, 20 s and 30 s of one UTC day; the third, 0.00101 BTC (size precision
5) at 10 000.01, is 10.1000101 USDT: seven decimals, the definition holds five, so it is skipped
and counted.
"""

from decimal import Decimal
from pathlib import Path

from kernel.clocks import NS_PER_DAY
from kernel.clocks import NS_PER_S
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from observability import error_ledger

from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from research.application.gallery import CascadeSample
from research.application.gallery import cascade_sample
from research.application.gallery import cascade_specs
from research.application.gallery import ofi_sample
from research.application.gallery import ofi_specs
from research.strategies.liquidation_cascade_strategy import DEFAULT_STOP_PCT


_IID = InstrumentId.from_str("BTCUSDT-LINEAR.BYBIT")
_DAY = 20_000 * NS_PER_DAY
_DETECTOR = {"window_s": 10, "baseline_s": 60, "intensity_threshold": 3.0, "decay_ratio": 0.5}


def _instrument(ts_init: int) -> CryptoPerpetual:
    return CryptoPerpetual(
        instrument_id=_IID,
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=BTC,
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=2,
        size_precision=3,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.001"),
        max_quantity=None,
        min_quantity=None,
        max_notional=None,
        min_notional=None,
        max_price=None,
        min_price=None,
        margin_init=Decimal("0.1"),
        margin_maint=Decimal("0.05"),
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0005"),
        ts_event=ts_init,
        ts_init=ts_init,
    )


def _liquidations() -> list[Liquidation]:
    def row(second: int, size_units: int, size_precision: int, price_units: int) -> Liquidation:
        ts = _DAY + second * NS_PER_S
        side = LiquidatedSide.LONG
        return Liquidation(
            _IID, side, size_units, price_units, 2, size_precision, str(second), ts, ts
        )

    return [row(10, 1, 3, 1_000_000), row(20, 1, 3, 1_000_000), row(30, 101, 5, 1_000_001)]


def _catalog(path: Path, definitions: int) -> str:
    catalog = ParquetDataCatalog(str(path))
    for k in range(definitions):
        catalog.write_data([_instrument(k * NS_PER_S)])
    catalog.write_data(_liquidations())
    return str(path)


def _sample(catalog_path: str) -> CascadeSample:
    return cascade_sample(catalog_path, str(_IID), _DAY, _DAY + 60 * NS_PER_S, _DETECTOR)


def test_a_definition_stored_twice_is_one_definition(tmp_path: Path) -> None:
    path = _catalog(tmp_path, definitions=2)
    assert len(ParquetDataCatalog(path).instruments(instrument_ids=[str(_IID)])) == 2
    error_ledger.reset()
    try:
        sample = _sample(path)
    finally:
        error_ledger.reset()
    assert (sample.has_feed, sample.has_definition, sample.days, sample.liquidations) == (
        True,
        True,
        1,
        3,
    )
    assert sample.unscalable_rows == 1  # 101 000 101 units of 10^-7: 1 010 001.01 at 10^-5
    assert str(sample).endswith("1 unscalable row(s) skipped")


def test_a_missing_definition_is_stated_not_crashed_on(tmp_path: Path) -> None:
    sample = _sample(_catalog(tmp_path, definitions=0))
    assert (sample.has_feed, sample.has_definition, sample.liquidations) == (True, False, 0)
    assert str(sample) == f"{_IID} has no instrument definition in the catalog: no sample"


def test_an_id_without_a_feed_is_stated(tmp_path: Path) -> None:
    sample = cascade_sample(str(tmp_path), "BTCUSDT-SPOT.BYBIT", _DAY, _DAY + NS_PER_S, {})
    assert not sample.has_feed
    assert "has no liquidation feed" in str(sample)


def test_the_cascade_rows_name_instrument_and_data_and_share_the_default_stop() -> None:
    specs = cascade_specs("/catalog", str(_IID), _DAY, _DAY + NS_PER_S, {})
    assert [s.label for s in specs] == [
        f"Cascade follow short only ({_IID}, liquidations)",
        f"Cascade follow both sides ({_IID}, liquidations)",
        f"Cascade fade short only ({_IID}, liquidations)",
        f"Cascade fade both sides ({_IID}, liquidations)",
    ]
    assert {s.spec.params["stop_pct"] for s in specs} == {DEFAULT_STOP_PCT}
    assert DEFAULT_STOP_PCT == 0.01
    atr = cascade_specs("/catalog", str(_IID), _DAY, _DAY + NS_PER_S, {"stop_atr_multiple": 2.0})
    assert all("stop_pct" not in s.spec.params for s in atr)


def test_the_ofi_rows_differ_from_the_baseline_by_one_forced_flow_field() -> None:
    # A params override of a forced-flow field never reaches a row: each row sets both.
    params = {"ofi_threshold": 2.0, "forced_flow_filter": True}
    specs = ofi_specs("/catalog", str(_IID), _DAY, _DAY + NS_PER_S, params)
    assert [s.label for s in specs] == [
        f"OFI {name} ({_IID}, seconds_liquidations)"
        for name in ("baseline", "forced-flow filter", "cascade fade", "cascade follow")
    ]
    fields = [
        (s.spec.params["forced_flow_filter"], s.spec.params["liquidation_cascade_mode"])
        for s in specs
    ]
    assert fields == [(False, "off"), (True, "off"), (False, "fade"), (False, "follow")]
    assert {s.spec.data for s in specs} == {"seconds_liquidations"}
    assert {s.spec.params["ofi_threshold"] for s in specs} == {2.0}
    # The cumulative-delta gate is on in every row, baseline included, so the filter can matter.
    assert {s.spec.params["cum_delta_threshold"] for s in specs} == {0.0}
    gated = ofi_specs("/c", str(_IID), _DAY, _DAY + NS_PER_S, {"cum_delta_threshold": 2.5})
    assert {s.spec.params["cum_delta_threshold"] for s in gated} == {2.5}
    assert {s.spec.fill_model for s in specs} == {None}  # the gallery's venue defaults


def test_no_ofi_row_without_a_feed() -> None:
    assert ofi_specs("/catalog", "BTC-USD-PERP.HYPERLIQUID", _DAY, _DAY + NS_PER_S, {}) == []


def test_the_ofi_sample_reads_the_ofi_detector_fields(tmp_path: Path) -> None:
    path = _catalog(tmp_path, definitions=1)
    ofi = {f"cascade_{key}": value for key, value in _DETECTOR.items()}
    error_ledger.reset()
    try:
        assert ofi_sample(path, str(_IID), _DAY, _DAY + 60 * NS_PER_S, ofi) == _sample(path)
    finally:
        error_ledger.reset()
