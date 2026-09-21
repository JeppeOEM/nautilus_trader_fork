from collector_core.integrity import ohlc_outside_book
from collector_core.second_snapshot import DydxSecondSnapshot
from nautilus_trader.model.identifiers import InstrumentId


def _snap(high: float | None, low: float | None) -> DydxSecondSnapshot:
    return DydxSecondSnapshot(
        instrument_id=InstrumentId.from_str("BTC-USD-PERP.DYDX"),
        bid_prices=[100.0, 99.0, 98.0], bid_sizes=[1.0, 1.0, 1.0],
        ask_prices=[101.0, 102.0, 103.0], ask_sizes=[1.0, 1.0, 1.0],
        buy_volume=0.0, sell_volume=0.0, buy_count=0, sell_count=0,
        open_price=high, high_price=high, low_price=low, close_price=low,
        ts_event=1, ts_init=1,
    )


def test_trades_inside_book_depth_are_plausible() -> None:
    assert not ohlc_outside_book(_snap(102.5, 99.5))


def test_no_trade_is_never_flagged() -> None:
    assert not ohlc_outside_book(_snap(None, None))


def test_replayed_history_range_is_flagged() -> None:
    assert ohlc_outside_book(_snap(110.0, 95.0))


def test_one_sided_excursion_is_flagged() -> None:
    assert ohlc_outside_book(_snap(102.0, 90.0))
