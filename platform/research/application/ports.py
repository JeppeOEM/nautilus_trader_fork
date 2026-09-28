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
The research ports (Story 27.1): how a notebook reads market frames, reads ranking history and runs
a backtest, as `typing.Protocol`s, plus the `RunSpec`/`RunResult` values a backtest run exchanges.

Implementations: `frames.CatalogFrames`, `ranking_history.HttpRankingHistory`,
`backtest_runner.NodeRunner`. A notebook constructs the one it needs and calls only these methods.
"""

import re
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from types import MappingProxyType
from typing import Protocol

import pandas as pd
from kernel.venues import venue_of

from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.core.datetime import time_object_to_dt
from research.domain.equity import EquityCurve
from research.domain.report import MetricReport
from research.domain.trades import TradeLedger


# `bars:<step>-<aggregation>`, a Nautilus bar spec without the price type (`bars:1-MINUTE`).
_BARS_DATA = re.compile(r"bars:[1-9][0-9]*-(MILLISECOND|SECOND|MINUTE|HOUR|DAY|WEEK|MONTH)")
# Keys the runner sets on every strategy config itself.
RESERVED_PARAMS = frozenset({"instrument_id", "order_id_tag", "bar_type"})


def window_ns(start: str | int, end: str | int) -> tuple[int, int]:
    """Parse a window exactly as `ParquetDataCatalog.query` does (naive = UTC, int = ns)."""
    start_ns = dt_to_unix_nanos(time_object_to_dt(start))
    end_ns = dt_to_unix_nanos(time_object_to_dt(end))
    if end_ns <= start_ns:
        raise ValueError(f"end {end!r} must be after start {start!r}")
    return start_ns, end_ns


@dataclass(frozen=True)
class RunSpec:
    """
    One backtest: which strategy, on which instruments, over which window, fed which data.

    Invariant: at least one instrument, no id twice, all on one venue (Known limit: one simulated venue per
    run -- upgrade path: one `BacktestVenueConfig` per venue with its own starting balance); a
    `data` kind of `"seconds"` (`DydxSecondSnapshot` + quotes derived from their top of book),
    `"trades"` (`TradeTick`) or `"bars:<step>-<aggregation>"` (`TradeTick` aggregated by Nautilus
    into `<iid>-<step>-<aggregation>-LAST-INTERNAL` bars, injected as the strategy's `bar_type`);
    a positive int starting balance; a window whose end is after its start; `params` never sets a key the runner owns (`RESERVED_PARAMS`).
    `start`/`end` bound every read (MEM-01) and follow Nautilus's parsing (ISO string, naive = UTC,
    or int ns). The runner bounds the replay on `ts_init` (the clock a backtest replays on,
    `BacktestDataConfig`), while `MarketFrames` windows on `ts_event`, so a frame read over the
    same `start`/`end` can differ from the replayed rows at each edge by up to
    `kernel.clocks.MAX_TS_INIT_SKEW_NS`. Violated at construction -- `__post_init__` raises `ValueError`. `params` is
    stored as a read-only mapping.
    """

    catalog_path: str
    instrument_ids: tuple[str, ...]
    start: str | int
    end: str | int
    strategy_path: str
    config_path: str
    params: Mapping[str, object] = field(default_factory=dict)
    starting_balance: int = 10_000
    data: str = "seconds"

    def __post_init__(self) -> None:
        if isinstance(self.instrument_ids, str):
            raise ValueError(
                f"instrument_ids must be a tuple of ids, not the str {self.instrument_ids!r}"
            )
        if not self.instrument_ids:
            raise ValueError("a run needs at least one instrument")
        if len(set(self.instrument_ids)) != len(self.instrument_ids):
            raise ValueError(f"an instrument is listed twice in {self.instrument_ids}")
        venues = {venue_of(iid) for iid in self.instrument_ids}
        if len(venues) != 1:
            raise ValueError(
                f"one venue per run (Known limit), got {sorted(venues)} for {self.instrument_ids}"
            )
        if self.data not in ("seconds", "trades") and not _BARS_DATA.fullmatch(self.data):
            raise ValueError(
                f"data must be 'seconds', 'trades' or 'bars:<step>-<aggregation>', got {self.data!r}"
            )
        balance = self.starting_balance
        if isinstance(balance, bool) or not isinstance(balance, int) or balance <= 0:
            raise ValueError(f"starting_balance must be a positive int, got {balance!r}")
        window_ns(self.start, self.end)
        check_params(self.params)
        object.__setattr__(self, "instrument_ids", tuple(self.instrument_ids))
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))

    @property
    def venue(self) -> str:
        return venue_of(self.instrument_ids[0])

    @property
    def bar_spec(self) -> str | None:
        """`1-MINUTE` for `data="bars:1-MINUTE"`, None for the tick kinds."""
        return self.data.removeprefix("bars:") if self.data.startswith("bars:") else None


def check_params(params: Mapping[str, object]) -> None:
    reserved = RESERVED_PARAMS & set(params)
    if reserved:
        raise ValueError(f"params may not set {sorted(reserved)}: the runner sets them")


@dataclass(frozen=True, eq=False)
class RunResult:
    """
    The outcome of one `BacktestRunConfig`, attributed by its id.

    Invariant: `config_id` is the `BacktestRunConfig.id` that produced every other field (results
    are matched by id, never by list position); `params` are the merged strategy parameters of that
    config; `trades`, `equity` and `metrics` come from that config's own engine.
    `metrics` are `kernel.performance_metrics.all_metrics` over the *closed* trades
    (`pnl_by_day == trades.pnl_by_day()`, realized PnL per UTC day of exit -- the one path bots'
    history shares, SSOT-02), while `equity` is the account balance `total` at each account event
    -- realized PnL and every commission paid, including an open position's entry fee, so the
    two can differ (e.g. `equity` ends below `starting_balance + sum(realized pnl)` by that fee).
    Known limit: `equity` is not marked to market -- an open position's unrealized PnL never
    moves it, so `equity.drawdowns()` understates an adverse move held open, and
    `metrics.max_drawdown` (daily realized returns) is a third, coarser view; upgrade path: sample
    `portfolio.net_exposures`/`unrealized_pnls` on a timer in the engine and add them per event;
    `nautilus_stats` is Nautilus's own `{"pnls": stats_pnls, "returns": stats_returns}` for
    cross-checking; `wall_seconds` is the engine's run time (data loading excluded).
    """

    config_id: str
    params: Mapping[str, object]
    equity: EquityCurve
    trades: TradeLedger
    metrics: MetricReport
    pnl_by_day: list[dict]
    nautilus_stats: dict
    iterations: int
    wall_seconds: float

    def pnl_by_hour_of_day(self) -> dict[int, float]:
        """
        Realized PnL per UTC hour of exit, exactly `self.trades.by_hour_of_day()` (Story 27.5): an
        hour with no exit is absent, never 0; nothing is summed here.
        """
        return self.trades.by_hour_of_day()

    def pnl_by_weekday(self) -> dict[int, float]:
        """
        Realized PnL per UTC weekday of exit (0 = Monday), exactly `self.trades.by_weekday()`
        (Story 27.5): a weekday with no exit is absent, never 0; nothing is summed here.
        """
        return self.trades.by_weekday()


class MarketFrames(Protocol):
    """
    Time-bounded market-data frames for one instrument.

    Invariant: every read is bounded -- `start` and `end` are required keyword arguments with no
    default (MEM-01), the window is half-open `[start, end)` on `ts_event`, and a frame never holds a
    value that was not read: gaps stay gaps (missing rows, None/NaN), never forward-filled or
    interpolated. Every frame has a UTC `DatetimeIndex` named `ts` plus an int `ts_event` column.
    Derived microstructure columns come from `kernel.indicators`, bars from the candle store
    (never a third seconds-to-bars fold).
    """

    def seconds(self, instrument_id: str, *, start: str | int, end: str | int) -> pd.DataFrame: ...

    def trades(self, instrument_id: str, *, start: str | int, end: str | int) -> pd.DataFrame: ...

    def bars(
        self, instrument_id: str, bar_seconds: int, *, start: str | int, end: str | int
    ) -> pd.DataFrame: ...

    def bar_coverage(
        self, instrument_id: str, bar_seconds: int, *, start: str | int, end: str | int
    ) -> list[tuple[int, int]]:
        """
        Return the `[start_ns, end_ns)` spans of the maximal runs of stored `bar_seconds` buckets whose
        whole bar lies in `[start, end)`, oldest first; `FileNotFoundError` without a store (as
        `bars`), so a missing store is never read as an outage: each span is a window `bars` reads without raising, and the holes between spans are buckets never
        observed (a collector outage) or outside the store (Story 27.4).
        """
        ...

    def same_symbol(self, instrument_id: str) -> list[str]:
        """
        Return the catalog's defined ids trading the same asset (`kernel.venues.asset_key`), itself
        included, sorted by venue then id; `[]` when the id has no asset key (Story 27.4).
        """
        ...

    def funding(self, instrument_id: str, *, start: str | int, end: str | int) -> pd.DataFrame: ...

    def open_interest(
        self, instrument_id: str, *, start: str | int, end: str | int
    ) -> pd.DataFrame: ...

    def mark_index(
        self, instrument_id: str, *, start: str | int, end: str | int
    ) -> pd.DataFrame: ...

    def objects(
        self, data_cls: type, instrument_id: str, *, start: str | int, end: str | int
    ) -> list:
        """
        Return the typed rows of the window, `ts_event` ascending, for checks that need the exact
        `Price`/`Quantity` a frame's float columns drop (the trade fold). Rows are the catalog's own
        class, except `IndexPriceUpdate`, which the pinned catalog cannot decode: its rows are
        `kernel.catalog_files.IndexPrice` (`ts_event`, `ts_init`, an exact `price`, not `value`).
        """
        ...


class RankingHistory(Protocol):
    """
    Ranking's published metric history for one instrument.

    Invariant: every value is ranking's own (pct-change, volatility, rank, ... as `metrics.db`
    holds them, AD-D10); research computes none of them and never imports `ranking` or `data_api`.
    """

    def history(self, instrument_id: str, days: int) -> pd.DataFrame: ...


class BacktestRunner(Protocol):
    """
    Runs backtests through `BacktestNode` (NAUT-03) and returns typed results.

    Invariant: one `RunResult` per distinct run config, attributed by `BacktestRunConfig.id`; a
    config Nautilus returned no result for raises (DATA-07), never a silent gap.
    """

    def run(self, spec: RunSpec) -> RunResult: ...

    def sweep(self, spec: RunSpec, grid: Sequence[Mapping[str, object]]) -> list[RunResult]:
        """One run per grid point, each point's params merged over `spec.params`."""
        ...
