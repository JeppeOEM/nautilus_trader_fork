import pytest

from ml_signals.venue import MalformedInstrumentId
from ml_signals.venue import venue_of


def test_venue_of() -> None:
    assert venue_of("BTC-USD-PERP.DYDX") == "DYDX"
    assert venue_of("ETH-USD.PERP.BYBIT") == "BYBIT"


@pytest.mark.parametrize("bad", ["", "BTC", ".DYDX", "BTC."])
def test_venue_of_malformed_fails_loudly(bad: str) -> None:
    with pytest.raises(MalformedInstrumentId):
        venue_of(bad)
