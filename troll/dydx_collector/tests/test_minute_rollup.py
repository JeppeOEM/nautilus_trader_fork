from dydx_collector.minute_rollup import _MINUTE_NS
from dydx_collector.minute_rollup import MinuteRollupBuilder
from dydx_collector.second_snapshot import DydxSecondSnapshot
from ml_signals.indicators import MultiLevelOFI
from nautilus_trader.model.identifiers import InstrumentId

IID = "BTC-USD-PERP.DYDX"


def _snap(sec: int, bid: float = 100.0, bsz: float = 1.0, trade: float | None = None, buy: float = 0.0):
    ts = sec * 1_000_000_000
    return DydxSecondSnapshot(
        instrument_id=InstrumentId.from_str(IID),
        bid_prices=[bid, bid - 1],
        bid_sizes=[bsz, 2.0],
        ask_prices=[bid + 1, bid + 2],
        ask_sizes=[1.0, 1.0],
        buy_volume=buy,
        sell_volume=0.0,
        buy_count=1 if buy else 0,
        sell_count=0,
        ts_event=ts,
        ts_init=ts,
        open_price=trade,
        high_price=trade,
        low_price=trade,
        close_price=trade,
    )


def _feed(b: MinuteRollupBuilder, snaps: list) -> list:
    return [r for s in snaps if (r := b.update(IID, s)) is not None]


def test_ohlcv_counts_and_close_only_on_boundary() -> None:
    b = MinuteRollupBuilder()
    snaps = [_snap(0, trade=10.0, buy=1.0), _snap(1, trade=12.0, buy=2.0), _snap(2, trade=9.0, buy=1.0)]
    assert _feed(b, snaps) == []
    (r,) = _feed(b, [_snap(60)])
    assert (r.open, r.high, r.low, r.close) == (10.0, 12.0, 9.0, 9.0)
    assert (r.buy_volume, r.buy_count, r.seconds_observed, r.ts_event) == (4.0, 3, 3, 0)
    assert (r.close_bid_price, r.close_ask_price) == (100.0, 101.0)


def test_ofi_includes_boundary_crossing_contribution() -> None:
    b = MinuteRollupBuilder()
    snaps = [_snap(58, bsz=1.0), _snap(59, bsz=1.0), _snap(60, bsz=5.0), _snap(120)]
    ref = MultiLevelOFI(levels=5, window=1)
    contribs = []
    for s in snaps[:3]:
        ref.update_raw(s.bid_prices, s.bid_sizes, s.ask_prices, s.ask_sizes)
        contribs.append(ref.value)
    expected = contribs[2]  # the second-60 delta (+4 bid size) lives in minute 1
    assert expected == 4.0
    rollups = _feed(b, snaps)
    assert [r.ts_event for r in rollups] == [0, _MINUTE_NS]
    assert rollups[1].ofi_5 == expected


def test_discard_book_state_suppresses_phantom_ofi() -> None:
    b = MinuteRollupBuilder()
    _feed(b, [_snap(0, bid=100.0), _snap(1, bid=100.0)])
    b.discard_book_state(IID)
    (r,) = _feed(b, [_snap(2, bid=500.0), _snap(60)])
    assert r.ofi_5 == 0.0


def test_skipped_minute_emits_nothing() -> None:
    b = MinuteRollupBuilder()
    rollups = _feed(b, [_snap(0), _snap(1), _snap(300)])
    assert [r.ts_event for r in rollups] == [0]


def test_no_trade_minute_has_none_ohlc_and_real_obi() -> None:
    b = MinuteRollupBuilder()
    (r,) = _feed(b, [_snap(0), _snap(1), _snap(60)])
    assert (r.open, r.high, r.low, r.close) == (None, None, None, None)
    assert r.obi_5 == 0.6
    assert r.seconds_observed == 2


def test_dict_roundtrip() -> None:
    from dydx_collector.minute_rollup import DydxMinuteRollup

    b = MinuteRollupBuilder()
    (r,) = _feed(b, [_snap(0, trade=10.0), _snap(60)])
    assert DydxMinuteRollup.to_dict(DydxMinuteRollup.from_dict(DydxMinuteRollup.to_dict(r))) == DydxMinuteRollup.to_dict(r)


def test_catalog_write_read_roundtrip(tmp_path) -> None:
    from dydx_collector.minute_rollup import DydxMinuteRollup
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    (r,) = _feed(MinuteRollupBuilder(), [_snap(0, trade=10.0), _snap(60)])
    catalog = ParquetDataCatalog(str(tmp_path))
    catalog.write_data([r])
    (out,) = catalog.custom_data(DydxMinuteRollup)
    assert DydxMinuteRollup.to_dict(out.data) == DydxMinuteRollup.to_dict(r)
