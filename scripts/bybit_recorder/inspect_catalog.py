"""Ad-hoc inspection script for the Phase 2 Plan 02 live smoke test.

Run AFTER stopping the recorder (Ctrl-C), from the repo root (same cwd used
to run the recorder, so the relative "catalog" path resolves the same way):

    python scripts/bybit_recorder/inspect_catalog.py

Not part of the plan's tracked files — safe to delete afterwards.
"""

from nautilus_trader.model.data import IndexPriceUpdate, MarkPriceUpdate
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog

catalog = ParquetDataCatalog("catalog/streaming")

linear_id = InstrumentId.from_str("BTCUSDT-LINEAR.BYBIT")
spot_id = InstrumentId.from_str("ETHUSDT-SPOT.BYBIT")
bar_type = "BTCUSDT-LINEAR.BYBIT-1-MINUTE-LAST-EXTERNAL"

print("=== Phase 2 Plan 02 catalog smoke check ===\n")

trades = catalog.trade_ticks(instrument_ids=[linear_id, spot_id])
print(f"trade_ticks:        {len(trades)}")

quotes = catalog.quote_ticks(instrument_ids=[linear_id, spot_id])
print(f"quote_ticks:        {len(quotes)}")

deltas = catalog.order_book_deltas(instrument_ids=[linear_id, spot_id])
print(f"order_book_deltas:  {len(deltas)}")

bars = catalog.bars(bar_types=[bar_type])
print(f"bars (1-MINUTE):    {len(bars)}")

mark = catalog.query(data_cls=MarkPriceUpdate, identifiers=[linear_id])
print(f"mark_price_updates: {len(mark)}")

index = catalog.query(data_cls=IndexPriceUpdate, identifiers=[linear_id])
print(f"index_price_updates:{len(index)}")

funding = catalog.funding_rates(instrument_ids=[linear_id])
print(f"funding_rates:      {len(funding)}  (expect a HANDFUL, not thousands)")
for f in funding:
    print(f"  rate={f.rate}  ts_event={f.ts_event}")

print("\n=== Pass/fail hints ===")
checks = {
    "trade_ticks": len(trades) > 0,
    "quote_ticks": len(quotes) > 0,
    "order_book_deltas": len(deltas) > 0,
    "bars": len(bars) > 0,
    "mark_price_updates": len(mark) > 0,
    "index_price_updates": len(index) > 0,
    "funding_rates (nonzero)": len(funding) > 0,
    "funding_rates (deduped, <50)": len(funding) < 50,
}
for name, ok in checks.items():
    print(f"  [{'OK' if ok else 'FAIL'}] {name}")
