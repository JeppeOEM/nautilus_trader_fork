"""Time `/api/candles` initial-page loads against a real catalog (Story 21.5).

Usage (from platform/): PYTHONPATH=. CATALOG_PATH=data/catalog python scripts/bench_candles.py [IID...]
"""

import sys
import time

from fastapi.testclient import TestClient

from data_api.app import app

BAR_SECONDS = (60, 900, 3600, 14400)
RUNS = 3


def main() -> None:
    iids = sys.argv[1:] or ["BTC-USD-PERP.DYDX"]
    client = TestClient(app)
    now_ns = time.time_ns()
    for iid in iids:
        for bar in BAR_SECONDS:
            times = []
            for _ in range(RUNS):
                t0 = time.perf_counter()
                r = client.get(f"/api/candles/{iid}", params={"before_ns": now_ns, "limit": 120, "bar_seconds": bar})
                times.append(time.perf_counter() - t0)
            n = len(r.json()["items"]) if r.status_code == 200 else r.status_code
            print(f"{iid:22} bar={bar:>6}s items={n!s:>4} cold={times[0]:.2f}s warm={min(times[1:]):.2f}s")


if __name__ == "__main__":
    main()
