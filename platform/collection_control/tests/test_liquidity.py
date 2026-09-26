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
"""`classify_liquidity`: the USD-volume split that alone admits a pin (OBS-03)."""

from collection_control.domain.liquidity import LiquidityTier
from collection_control.domain.liquidity import classify_liquidity


def _markets(**markets: dict) -> dict:
    return {"markets": markets}


def test_splits_by_volume24h_threshold() -> None:
    result = classify_liquidity(
        _markets(
            BTC={"ticker": "BTC-USD", "volume24H": "500000"},
            SHIB={"ticker": "SHIB-USD", "volume24H": "500"},
            ETH={"ticker": "ETH-USD", "volume24H": "100000"},  # exactly at threshold
        ),
        min_volume_usd=100_000.0,
    )
    assert result.liquid == {"BTC-USD-PERP.DYDX", "ETH-USD-PERP.DYDX"}
    assert result.illiquid == {"SHIB-USD-PERP.DYDX"}
    assert result.tier("BTC-USD-PERP.DYDX") is LiquidityTier.LIQUID
    assert result.tier("SHIB-USD-PERP.DYDX") is LiquidityTier.ILLIQUID
    assert result.min_volume_usd == 100_000.0


def test_low_token_count_high_volume_is_liquid() -> None:
    # Regression for the production incident AD-7 exists to prevent: BTC at 458
    # tokens of raw openInterest looks illiquid, but its USD volume24H is huge.
    result = classify_liquidity(
        _markets(BTC={"ticker": "BTC-USD", "openInterest": "458", "volume24H": "50000000"}),
        min_volume_usd=100_000.0,
    )
    assert "BTC-USD-PERP.DYDX" in result.liquid


def test_missing_volume_is_illiquid() -> None:
    result = classify_liquidity(_markets(X={"ticker": "X-USD"}), min_volume_usd=1.0)
    assert result.illiquid == {"X-USD-PERP.DYDX"}


def test_unparseable_volume_is_unclassified_and_recorded_never_illiquid() -> None:
    """No volume is not zero volume (DATA-01): the caller ledgers `unparseable`."""
    result = classify_liquidity(_markets(X={"ticker": "X-USD", "volume24H": "n/a"}), 1.0)
    assert result.unparseable == {"X-USD-PERP.DYDX": "'n/a'"}
    assert result.tier("X-USD-PERP.DYDX") is None
    assert result.illiquid == frozenset()


def test_non_finite_or_negative_volumes_are_unparseable_never_classified() -> None:
    """`float` parses these, but none is a volume: NaN would read illiquid, inf would win pins."""
    result = classify_liquidity(
        _markets(
            N={"ticker": "N-USD", "volume24H": "NaN"},
            I={"ticker": "I-USD", "volume24H": "Infinity"},
            M={"ticker": "M-USD", "volume24H": "-5"},
        ),
        1.0,
    )
    assert set(result.unparseable) == {"N-USD-PERP.DYDX", "I-USD-PERP.DYDX", "M-USD-PERP.DYDX"}
    assert (result.liquid, result.illiquid) == (frozenset(), frozenset())


def test_excluded_coin_is_always_illiquid() -> None:
    result = classify_liquidity(
        _markets(BTC={"ticker": "BTC-USD", "volume24H": "50000000"}),
        min_volume_usd=1.0,
        exclude=frozenset({"BTC-USD-PERP.DYDX"}),
    )
    assert (result.liquid, result.illiquid) == (frozenset(), {"BTC-USD-PERP.DYDX"})


def test_max_liquid_keeps_highest_volume() -> None:
    # dYdX's WS server hard-caps subscriptions per channel at 32 per connection -- exceeding it
    # gets every subscription rejected in a loop (the plan's cap, `collector_core.config`), so
    # overflow must be demoted by volume, not left in.
    result = classify_liquidity(
        _markets(
            BTC={"ticker": "BTC-USD", "volume24H": "500000"},
            ETH={"ticker": "ETH-USD", "volume24H": "300000"},
            SOL={"ticker": "SOL-USD", "volume24H": "200000"},
        ),
        min_volume_usd=100_000.0,
        max_liquid=2,
    )
    assert result.liquid == {"BTC-USD-PERP.DYDX", "ETH-USD-PERP.DYDX"}
    assert result.illiquid == {"SOL-USD-PERP.DYDX"}


def test_max_liquid_ties_demote_in_the_venues_order() -> None:
    result = classify_liquidity(
        _markets(
            A={"ticker": "A-USD", "volume24H": "200000"},
            B={"ticker": "B-USD", "volume24H": "200000"},
        ),
        min_volume_usd=100_000.0,
        max_liquid=1,
    )
    assert result.liquid == {"B-USD-PERP.DYDX"}


def test_max_liquid_zero_demotes_everything() -> None:
    result = classify_liquidity(
        _markets(BTC={"ticker": "BTC-USD", "volume24H": "500000"}), 100_000.0, max_liquid=0
    )
    assert (result.liquid, result.illiquid) == (frozenset(), {"BTC-USD-PERP.DYDX"})


def test_max_liquid_above_count_is_noop() -> None:
    result = classify_liquidity(
        _markets(BTC={"ticker": "BTC-USD", "volume24H": "500000"}), 100_000.0, max_liquid=32
    )
    assert (result.liquid, result.illiquid) == ({"BTC-USD-PERP.DYDX"}, frozenset())
