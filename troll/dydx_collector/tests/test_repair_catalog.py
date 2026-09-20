from pathlib import Path

from dydx_collector.build_candles import rebuild_instrument
from dydx_collector.repair_catalog import find_impossible_snapshots
from dydx_collector.repair_catalog import repair_instrument
from dydx_collector.second_snapshot import DydxSecondSnapshot
from ml_signals import candle_store
from ml_signals.catalog_stats import query_second_snapshots
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_IID = "BTC-USD-PERP.DYDX"
_T0 = 1_789_800_000 * 1_000_000_000 // 60_000_000_000 * 60_000_000_000
_SEC = 1_000_000_000


def _snap(second: int, high: float | None = None, low: float | None = None) -> DydxSecondSnapshot:
    traded = high is not None
    return DydxSecondSnapshot(
        instrument_id=InstrumentId.from_str(_IID),
        bid_prices=[100.0, 99.0], bid_sizes=[1.0, 1.0], ask_prices=[101.0, 102.0], ask_sizes=[1.0, 1.0],
        buy_volume=5.0 if traded else 0.0, sell_volume=0.0, buy_count=1 if traded else 0, sell_count=0,
        open_price=high, high_price=high, low_price=low, close_price=low,
        ts_event=_T0 + second * _SEC, ts_init=_T0 + second * _SEC,
    )


def test_spike_snapshot_is_cleared_and_its_candle_rebuilt(tmp_path: Path) -> None:
    catalog = ParquetDataCatalog(str(tmp_path / "cat"))
    catalog_path = str(tmp_path / "cat")
    # 3 minutes of 1s data; the spike sits in the middle minute, which is otherwise untraded.
    snaps = [_snap(s) for s in range(180)]
    snaps[70] = _snap(70, high=150.0, low=50.0)
    catalog.write_data(snaps)
    db_path = str(tmp_path / "candles.db")
    rebuild_instrument(db_path, catalog_path, _IID, _T0, _T0 + 200 * _SEC)
    db = candle_store.connect_rw(db_path)
    assert [c["h"] for c in candle_store.window(db, _IID, 60, 1 << 62, 10)] == [150.0]  # the spike is in the store

    flagged = find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 200 * _SEC)
    assert [f.ts_event for f in flagged] == [_T0 + 70 * _SEC]

    repair_instrument(catalog, catalog_path, _IID, flagged, db_path)

    assert find_impossible_snapshots(catalog_path, _IID, _T0, _T0 + 200 * _SEC) == []
    rows = query_second_snapshots(catalog_path, _IID, _T0, _T0 + 200 * _SEC)
    assert len(rows) == 180
    assert all(r.high_price is None for r in rows)
    assert candle_store.window(db, _IID, 60, 1 << 62, 10) == []  # no trade left in that minute
