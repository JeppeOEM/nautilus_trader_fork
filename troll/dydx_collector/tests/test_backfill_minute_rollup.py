from pathlib import Path

from dydx_collector.backfill_minute_rollup import _DAY_NS
from dydx_collector.backfill_minute_rollup import all_instruments
from dydx_collector.backfill_minute_rollup import backfill_instrument
from dydx_collector.backfill_minute_rollup import data_range_ns
from dydx_collector.backfill_minute_rollup import day_chunks
from dydx_collector.minute_rollup import DydxMinuteRollup
from dydx_collector.second_snapshot import DydxSecondSnapshot
from ml_signals.candles import aggregate_ohlc
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog

IID = "BTC-USD-PERP.DYDX"
_SEC = 1_000_000_000


def _snap(ts: int, price: float) -> DydxSecondSnapshot:
    return DydxSecondSnapshot(
        instrument_id=InstrumentId.from_str(IID),
        bid_prices=[price - 1], bid_sizes=[1.0], ask_prices=[price + 1], ask_sizes=[1.0],
        buy_volume=1.0, sell_volume=0.5, buy_count=1, sell_count=1, ts_event=ts, ts_init=ts,
        open_price=price, high_price=price + 0.5, low_price=price - 0.5, close_price=price,
    )


def _seed(path: Path) -> list[DydxSecondSnapshot]:
    """Three minutes straddling a UTC midnight, so a minute-aligned chunk boundary is crossed."""
    start = 3 * _DAY_NS - 90 * _SEC
    snaps = [_snap(start + i * _SEC, 100.0 + i) for i in range(200)]
    ParquetDataCatalog(str(path)).write_data(snaps)
    return snaps


def test_day_chunks_cover_range_inclusively() -> None:
    assert list(day_chunks(_DAY_NS - 1, _DAY_NS)) == [(0, _DAY_NS - 1), (_DAY_NS, 2 * _DAY_NS - 1)]


def test_discovery_and_range_from_filenames(tmp_path: Path) -> None:
    snaps = _seed(tmp_path)
    assert all_instruments(str(tmp_path)) == [IID]
    assert data_range_ns(str(tmp_path), IID) == (snaps[0].ts_event, snaps[-1].ts_event)


def test_backfill_ohlcv_matches_aggregate_ohlc_across_chunk_boundary(tmp_path: Path) -> None:
    snaps = _seed(tmp_path)
    lo, hi = data_range_ns(str(tmp_path), IID)
    catalog = ParquetDataCatalog(str(tmp_path))
    assert backfill_instrument(catalog, str(tmp_path), IID, lo, hi) > 0

    rollups = [r.data for r in catalog.query(DydxMinuteRollup, identifiers=[IID])]
    expected = aggregate_ohlc(
        [(s.ts_event, s.open_price, s.high_price, s.low_price, s.close_price, s.buy_volume + s.sell_volume)
         for s in snaps],
        60,
    )
    closed = {r.ts_event: r for r in rollups}
    for c in expected[:-1]:  # last minute is still open, never emitted
        r = closed[c.ts_open]
        assert (r.open, r.high, r.low, r.close) == (c.open, c.high, c.low, c.close)
        assert r.buy_volume + r.sell_volume == c.volume


def test_final_minute_of_requested_range_is_emitted(tmp_path: Path) -> None:
    snaps = _seed(tmp_path)  # 200s of data; more data exists past the requested end
    catalog = ParquetDataCatalog(str(tmp_path))
    end = snaps[0].ts_event + 100 * _SEC  # mid-minute (3d + 10s)
    backfill_instrument(catalog, str(tmp_path), IID, snaps[0].ts_event, end)
    got = sorted(r.data.ts_event for r in catalog.query(DydxMinuteRollup, identifiers=[IID]))
    assert got[-1] == end // (60 * _SEC) * (60 * _SEC)
    assert all(t <= end for t in got)
