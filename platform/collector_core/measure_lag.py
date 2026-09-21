# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  You may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------
"""
Measure a venue's message lag, `ts_init - ts_event` (our receive time minus the venue's stamp),
per data kind -- report-only: it writes nothing (story 22.12).

    python -m collector_core.measure_lag --venue bybit --seconds 10800

It drives the venue's own collector client (same `on_data` shape as the collector), subscribes
the configured instruments (or `--instrument ...`), and after `--seconds` prints n, p50, p99,
p99.9 and max per kind, plus the hold-back that trade p99.9 suggests (`hold_back_seconds`,
rounded up to 0.5 s). A trade older than `--stale-trade-seconds` at arrival is subscribe-time
history (DATA-06) and is counted as replay, not lag; nothing is recorded during the first
`--warmup-seconds` after subscribing, because a subscribe-time replay younger than that bound
would otherwise read as lag (D-44). dYdX book deltas carry no venue stamp
(`ts_event = ts_init`, D-49), so their lag reads 0 by construction.

The lag includes any clock offset between the venue and this host: run it on the host that
runs the collector.
"""

import argparse
import asyncio
import importlib
import math
import tomllib
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nautilus_trader.model.data import TradeTick


VENUES = ("bybit", "hyperliquid", "dydx")
_S_NS = 1_000_000_000
_HOLD_BACK_STEP_S = 0.5


class LagRecorder:
    """The client's `on_data`: records `ts_init - ts_event` (ns) per data class name."""

    def __init__(self, stale_trade_ns: int) -> None:
        self.lags: defaultdict[str, list[int]] = defaultdict(list)
        self.replayed_trades = 0
        self.recording = False  # off during the warm-up after subscribe
        self._stale_trade_ns = stale_trade_ns

    def __call__(self, data: object) -> None:
        if not self.recording:
            return
        ts_event = getattr(data, "ts_event", None)
        ts_init = getattr(data, "ts_init", None)
        if ts_event is None or ts_init is None:
            return
        lag = ts_init - ts_event
        if isinstance(data, TradeTick) and lag > self._stale_trade_ns:
            self.replayed_trades += 1
            return
        self.lags[type(data).__name__].append(lag)


def percentile(sorted_values: list[int], q: float) -> int:
    """Nearest-rank percentile of an ascending, non-empty list (`q` in (0, 1])."""
    rank = max(1, math.ceil(q * len(sorted_values)))
    return sorted_values[rank - 1]


def suggest_hold_back(p999_ns: int) -> float:
    """Trade p99.9 lag rounded up to the next 0.5 s (0.0 when the lag is not positive)."""
    if p999_ns <= 0:
        return 0.0
    return math.ceil(p999_ns / (_HOLD_BACK_STEP_S * _S_NS)) * _HOLD_BACK_STEP_S


def report(recorder: LagRecorder) -> list[str]:
    lines = [f"{'kind':<24}{'n':>9}{'p50 ms':>10}{'p99 ms':>10}{'p99.9 ms':>10}{'max ms':>10}"]
    for kind, lags in sorted(recorder.lags.items()):
        values = sorted(lags)
        cells = [percentile(values, q) / 1e6 for q in (0.5, 0.99, 0.999)] + [values[-1] / 1e6]
        lines.append(f"{kind:<24}{len(values):>9}" + "".join(f"{c:>10.1f}" for c in cells))
    lines.append(
        f"replayed trades excluded (older than the stale bound): {recorder.replayed_trades}"
    )
    trades = sorted(recorder.lags.get("TradeTick", []))
    if trades:
        hold_back = suggest_hold_back(percentile(trades, 0.999))
        lines.append(f"suggested hold_back_seconds (trade p99.9, rounded up to 0.5 s): {hold_back}")
    return lines


def _build_client(venue: str, environment: str, on_data: Callable[[object], None]) -> Any:
    # Lazy imports: the venue packages depend on collector_core, not the other way round.
    if venue == "bybit":
        from nautilus_trader.core.nautilus_pyo3 import BybitEnvironment

        bybit = importlib.import_module("bybit_collector.client")
        env = BybitEnvironment.TESTNET if environment == "testnet" else BybitEnvironment.MAINNET
        return bybit.BybitClient(on_data=on_data, environment=env)
    if venue == "hyperliquid":
        hyperliquid = importlib.import_module("hyperliquid_collector.client")
        return hyperliquid.HyperliquidClient(on_data=on_data, environment=environment)
    from nautilus_trader.core.nautilus_pyo3 import DydxNetwork

    dydx = importlib.import_module("dydx_collector.client")
    return dydx.DydxClient(on_data=on_data, network=DydxNetwork.from_str(environment))  # type: ignore[attr-defined]


def _default_instruments(venue: str) -> list[str]:
    """Return the venue collector's committed config.toml instruments (dYdX's is empty)."""
    path = Path(__file__).resolve().parent.parent / f"{venue}_collector" / "config.toml"
    with path.open("rb") as f:
        return list(tomllib.load(f).get("instruments", []))


async def measure(
    venue: str,
    environment: str,
    instruments: list[str],
    seconds: float,
    warmup_seconds: float,
    recorder: LagRecorder,
) -> None:
    client = _build_client(venue, environment, recorder)
    available = await client.fetch_instruments()
    known = {str(i.id) for i in available}
    try:
        # Inside the try: a connect that fails half-way must still release the WS client.
        await client.connect(asyncio.get_running_loop(), available)
        for iid in instruments:
            if iid not in known:
                raise SystemExit(f"{iid} is not listed on {venue} {environment}")
            await client.subscribe(iid)
        await asyncio.sleep(warmup_seconds)
        recorder.recording = True
        await asyncio.sleep(seconds)
    finally:
        await client.disconnect()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--venue", required=True, choices=VENUES)
    parser.add_argument("--seconds", type=float, required=True)
    parser.add_argument("--instrument", action="append", help="default: the venue's config.toml")
    parser.add_argument("--environment", default="mainnet", choices=("mainnet", "testnet"))
    parser.add_argument("--stale-trade-seconds", type=float, default=10.0)
    parser.add_argument("--warmup-seconds", type=float, default=30.0)
    args = parser.parse_args(argv)
    if args.seconds <= 0 or args.warmup_seconds < 0 or args.stale_trade_seconds <= 0:
        parser.error("--seconds and --stale-trade-seconds must be > 0, --warmup-seconds >= 0")
    instruments = args.instrument or _default_instruments(args.venue)
    if not instruments:
        parser.error(f"no instruments configured for {args.venue}: pass --instrument")
    recorder = LagRecorder(int(args.stale_trade_seconds * _S_NS))
    asyncio.run(
        measure(
            args.venue,
            args.environment,
            instruments,
            args.seconds,
            args.warmup_seconds,
            recorder,
        )
    )
    print(
        f"{args.venue} {args.environment} {args.seconds:.0f}s after a "
        f"{args.warmup_seconds:.0f}s warm-up: {', '.join(instruments)}"
    )
    print("\n".join(report(recorder)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
