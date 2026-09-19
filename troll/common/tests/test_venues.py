from common.venues import venue_kind


def test_known_venues() -> None:
    assert venue_kind("DYDX") == "dex"
    assert venue_kind("HYPERLIQUID") == "dex"
    assert venue_kind("BYBIT") == "cex"


def test_unknown_venue_does_not_raise() -> None:
    assert venue_kind("NEWVENUE") == "unknown"
