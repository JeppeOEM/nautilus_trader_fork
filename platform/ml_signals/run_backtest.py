"""
OFIStrategy backtest on 1s snapshots. Run from ml_signals/:

python run_backtest.py --start 2026-09-05 --end 2026-09-06 [--symbol ETH-USD-PERP.DYDX] [--threshold 2.0]
"""

import argparse
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # "ml_signals.…" string paths

from ml_signals.strategies.backtest_ofi import run


p = argparse.ArgumentParser()
p.add_argument("--symbol", default="BTC-USD-PERP.DYDX")
p.add_argument("--start", required=True, help="bounded window, e.g. 2026-09-05 (MEM-01)")
p.add_argument("--end", required=True)
p.add_argument("--threshold", type=float, default=2.0, help="OFI z-score entry threshold")
p.add_argument("--warmup", type=int, default=600, help="seconds of data before trading")
a = p.parse_args()

result = run(a.symbol, a.start, a.end, ofi_threshold=a.threshold, warmup_seconds=a.warmup)
print(f"Events: {result.iterations}")
print(f"PnL: {result.stats_pnls}")
print(f"Returns: {result.stats_returns}")
