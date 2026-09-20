from common.venues import market_kind
from common.venues import venue_kind


def test_known_venues() -> None:
    assert venue_kind("DYDX") == "dex"
    assert venue_kind("HYPERLIQUID") == "dex"
    assert venue_kind("BYBIT") == "cex"


def test_unknown_venue_does_not_raise() -> None:
    assert venue_kind("NEWVENUE") == "unknown"


def test_market_kind_real_id_shapes() -> None:
    assert market_kind("BTC-USD-PERP.DYDX") == "perp"
    assert market_kind("BTCUSDT-LINEAR.BYBIT") == "perp"
    assert market_kind("BTCUSDT-SPOT.BYBIT") == "spot"
    assert market_kind("BTCUSD-INVERSE.BYBIT") == "perp"
    assert market_kind("BTC-USD-PERP.HYPERLIQUID") == "perp"
    assert market_kind("HYPE-USDC-SPOT.HYPERLIQUID") == "spot"
    assert market_kind("km:US500-USD-PERP.HYPERLIQUID") == "perp"


def test_market_kind_unknown_never_raises() -> None:
    assert market_kind("BTC-30SEP26-100000-C.BYBIT") == "unknown"
    assert market_kind("BTCUSDT-SPOT") == "unknown"
    assert market_kind("") == "unknown"
