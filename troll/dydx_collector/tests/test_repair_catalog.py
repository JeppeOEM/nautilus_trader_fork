from pathlib import Path

from dydx_collector.minute_rollup import MinuteRollupBuilder
from dydx_collector.repair_catalog import find_impossible_snapshots
from dydx_collector.repair_catalog import repair_instrument
from dydx_collector.second_snapshot import DydxSecondSnapshot
from ml_signals.catalog_stats import query_minute_rollups
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


def test_spike_snapshot_is_cleared_and_its_rollup_regenerated(tmp_path: Path) -> None:
    catalog = ParquetDataCatalog(str(tmp_path))
    # 3 minutes of 1s data; the spike sits in the middle minute, so its rollup closes.
    snaps = [_snap(s) for s in range(180)]
    snaps[70] = _snap(70, high=150.0, low=50.0)
    catalog.write_data(snaps)
    builder = MinuteRollupBuilder()
    rollups = [r for s in snaps if (r := builder.update(_IID, s)) is not None]
    catalog.write_data(rollups)

    flagged = find_impossible_snapshots(str(tmp_path), _IID, _T0, _T0 + 200 * _SEC)
    assert [f.ts_event for f in flagged] == [_T0 + 70 * _SEC]

    repair_instrument(catalog, str(tmp_path), _IID, flagged)

    assert find_impossible_snapshots(str(tmp_path), _IID, _T0, _T0 + 200 * _SEC) == []
    rows = query_second_snapshots(str(tmp_path), _IID, _T0, _T0 + 200 * _SEC)
    assert len(rows) == 180
    assert all(r.high_price is None for r in rows)
    rebuilt = {r.ts_event: r for r in query_minute_rollups(str(tmp_path), _IID, _T0, _T0 + 200 * _SEC)}
    assert rebuilt[_T0 + 60 * _SEC].high is None
