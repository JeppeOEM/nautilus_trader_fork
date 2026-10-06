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
The catalog replay of a live paper fleet's `DummyStrategy` and `liquidation_cascade` bots (Stories
31.9, 33.14): the backtest side of the live/backtest signal parity `verification.bot_parity`
measures.

    python3 -m bots.signal_replay --config F --catalog P --live-log DIR --out DIR
        [--start ISO] [--end ISO] [--bot BOT_ID ...] [--strategy dummy|liquidation_cascade]

For each replayable bot of the paper file F (`bots.infrastructure.config.load_paper_config`) --
every dummy and cascade bot, or those of `--strategy` --, one `BacktestNode` run (NAUT-03) of the
same strategy, loaded by string path with the bot's own thresholds, sizing and exits (a dummy bot)
or exactly the config the live host builds (a cascade bot: `nautilus_host.strategy_params`),
writing its signal log to `<out>/<bot_id>.jsonl`:

- **Window.** The live log `<live-log>/<bot_id>.jsonl`'s latest run segment (from its last `start`
  record): `start` is that record's `ts_ns` exactly and `end` the segment's last record's. The run's
  start is the engine clock at `on_start`, and the strategy starts its 1 s timer at that very
  nanosecond (`DummyStrategy._open_signal_log`), so every replay cycle lands on the same `start + k
  s` as a live one and `verification.bot_parity` pairs cycles by equal `ts_ns`, never by nearest
  match. `--start` is snapped up onto that grid (the next live cycle at or after it), so an override
  keeps the pairing; `--end` is taken as given. `--start` is refused up front when a cascade bot is
  selected: its parity needs the replay to start at the live start. The live `start` record's
  instrument, thresholds and sizing must equal the file's (else refused): a replay of other
  parameters would compare nothing. A cascade bot's is checked per strategy: its instrument, sizing
  and every `[bots.params]` key must equal the `start` record's field (numbers compared as
  decimals); the parity tool then compares the two `start` records whole. A cascade replay ticks on
  whole UTC seconds from the same start, as the live bot does, so its grid needs no alignment.
- **Market data.** The source catalog's instrument definition and `TradeTick`s (so the
  `LAST-INTERNAL` trend bars aggregate from the same trades the live bot saw), plus, derived per
  stored `DydxSecondSnapshot` row, one `OrderBookDeltas` (the whole stored book) and one
  `QuoteTick` (its top of book) by `kernel.snapshot_book`, stamped at the row's `ts_init`, the
  clock a backtest replays on (`docs/DATA_DICTIONARY.md` §1.7). The derived data goes into a
  throwaway catalog (a temporary directory, removed after the run); the source catalog is only read,
  one hour of rows at a time, bounded on `ts_init` (MEM-01). A cascade bot is replayed on the
  derived quotes alone (it reads no book or trade) plus the source catalog's `custom_liquidation`
  rows of its instrument (`kernel.liquidation.Liquidation`, data client id `LIQUIDATIONS`, the one
  its strategy subscribes through), streamed by `BacktestDataConfig` on `ts_init`, the order the
  live bridge delivered them in.
- **Venue.** Configured like the fleet's Sandbox execution client -- the venue's `[venues.<VENUE>]`
  account type and starting balances (`PaperFleet.venue_config`), NETTING, an `L1_MBP` book and
  leverage 1, `SandboxExecutionClientConfig`'s defaults, which the host leaves unset -- but it does
  not simulate fills as the live Sandbox does (the Known limit below).

No warm-up: the replay starts cold at the live start, as the live bot did, but a live bot receives
the venue's book snapshot on subscribing while the replay holds no book until the first stored row
after `start` (at most about 1 + `hold_back` s later): its first cycles may be `book_skipped`
(`no_book`/`one_sided`), which the comparator explains, never hides.

Known limit (fills): the backtest's `L1_MBP` matching engine skips a trade older than its book's
last update, and the derived quotes and deltas are stamped at the row's `ts_init` (at or after
`S + 1 + hold_back` s for second S) while the trades keep the venue's `ts_event`, so most replay
trades arrive "older" than the book and are skipped for fill simulation; a market order fills at
the derived quote. Fills, positions and so `action` (whether an order was submitted this cycle
depends on the position) differ from the live Sandbox's by construction, which is why
`verification.bot_parity` counts `action` disagreements as informational. Upgrade path: replay
the trades on the same clock as the book (their `ts_init`, the clock the book is stamped on), or
derive the replay's book from the raw trade-and-book archive at venue time.

Known limit: `BacktestRunConfig.chunk_size` is left None, so one run loads its whole window of
derived deltas (about 41 per second for a 20-level book) into memory: about 150 k deltas per hour.
Upgrade path: set `chunk_size` (every data type here is built in, so the streaming path applies)
once a window beyond a few hours is replayed.

Known limit: the engine's own log is bypassed (`LoggingConfig(bypass_logging=True)`: the Rust
logger initialises once per process and a run replays several bots), so a strategy error inside the
run shows only as a missing or short replay log -- which is checked: a run whose log has no `start`
record at the window's start is refused. Upgrade path: one process-wide `LoggingConfig` with a
`log_directory`, as `research/tests/conftest.py` keeps its guard alive.

Exit 0 when every selected bot replayed, 1 on any refusal (a missing or malformed live log or
segment, a mismatched config, no instrument definition in force at the start or no rows in the
catalog, an engine error, a run that wrote no fresh aligned log reaching the window end), 2 on a
usage error. Each bot is replayed on its own: a refusal is ledgered at `bots.signal_replay.refused`
(`observability.error_ledger`, a `job_service` ledger of its own) and the next bot still runs.
"""

import argparse
import json
import sys
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
from kernel.clocks import NS_PER_S
from kernel.liquidation import LIQUIDATION_CLIENT_ID
from kernel.liquidation import Liquidation
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.snapshot_book import snapshot_deltas
from kernel.snapshot_book import snapshot_quote
from kernel.venues import venue_of
from observability import error_ledger

from bots.domain.config import BotConfig
from bots.domain.config import PaperFleet
from bots.domain.config import VenuePaperConfig
from bots.infrastructure.config import load_paper_config
from bots.infrastructure.nautilus_host import LIQUIDATION_STRATEGIES
from bots.infrastructure.nautilus_host import STRATEGIES
from bots.infrastructure.nautilus_host import strategy_params
from nautilus_trader.backtest.engine import BacktestEngineConfig
from nautilus_trader.backtest.node import BacktestDataConfig
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.backtest.node import BacktestRunConfig
from nautilus_trader.backtest.node import BacktestVenueConfig
from nautilus_trader.config import ImportableStrategyConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_STRATEGY_PATH = "bots.strategies.dummy:DummyStrategy"
_CONFIG_PATH = "bots.strategies.dummy:DummyStrategyConfig"
_DUMMY = "dummy"
# The strategies this tool replays (`--strategy`): the dummy and every liquidation strategy, whose
# signal logs `verification.bot_parity` pairs.
REPLAYED = (_DUMMY, *sorted(LIQUIDATION_STRATEGIES))
# A custom data type goes in by its import path: `BacktestDataConfig.data_cls` is typed `str`.
_LIQUIDATION_CLS = f"{Liquidation.__module__}:{Liquidation.__qualname__}"
# The source catalog is read one hour of rows at a time (MEM-01).
_CHUNK_NS = 3_600 * NS_PER_S
# The live `start` record's fields that must equal the paper file's bot (see the docstring).
_CHECKED_FIELDS = (
    "instrument_id",
    "trade_size",
    "trend_buy_threshold",
    "trend_sell_threshold",
    "ofi_confirm_threshold",
)


class ReplayRefused(Exception):
    """A replay that cannot be run or did not produce an aligned log (exit 1)."""


# Every refusal is ledgered here (DATA-07): a bot's replay that did not happen is never silent.
REFUSED_SITE = "bots.signal_replay.refused"
# A bot's replay is refused, not crashed, on these: the tool's own refusals, a malformed paper file
# or log (`ValueError`), an unreadable file (`OSError`); an engine error is wrapped by `_run_node`.
_REFUSALS = (ReplayRefused, ValueError, OSError)


@dataclass(frozen=True)
class LiveSegment:
    """The latest run of one signal log: its `start` record, last `ts_ns` and cycle count."""

    start: dict[str, Any]
    end_ns: int
    cycles: int = 0

    @property
    def start_ns(self) -> int:
        return int(self.start["ts_ns"])


@dataclass(frozen=True)
class DerivedCounts:
    rows: int
    deltas: int
    quotes: int


@dataclass(frozen=True)
class ReplayOutcome:
    bot_id: str
    start_ns: int
    end_ns: int
    derived: DerivedCounts
    iterations: int
    records: int
    wall_seconds: float


def _complete_lines(path: Path, offset: int) -> Iterator[tuple[str, str]]:
    """
    Yield `(file:line, text)` of every complete line from byte `offset` on; a final line without
    its newline is held back -- a write still in flight (a live fleet writes while this reads),
    read by the next run, never parsed half-written (`verification`'s `SignalLogDir` rule).
    """
    with path.open("rb") as handle:
        handle.seek(offset)
        for number, raw in enumerate(handle, start=1):
            if not raw.endswith(b"\n"):
                return
            yield f"{path}@{offset}:{number}", raw[:-1].decode("utf-8")


def _record(where: str, text: str) -> dict[str, Any]:
    """Parse one complete line: a JSON object with a `kind` and an integer `ts_ns`, else refused."""
    try:
        record = json.loads(text)
    except ValueError as exc:
        raise ReplayRefused(f"{where}: not a JSON record: {text[:200]!r}") from exc
    if (
        not isinstance(record, dict)
        or not isinstance(record.get("kind"), str)
        or not isinstance(record.get("ts_ns"), int)
    ):
        raise ReplayRefused(f"{where}: not a signal-log record: {text[:200]!r}")
    return record


def latest_segment(path: Path, offset: int = 0) -> LiveSegment:
    """
    Read a signal log from byte `offset` line by line (bounded memory) and return its latest
    segment: the last `start` record and the `ts_ns` of the last record after it (the start's own
    when none). A malformed complete line (blank, not JSON, no `kind`/`ts_ns`) is refused.
    """
    if not path.is_file():
        raise ReplayRefused(f"no signal log at {path}")
    start: dict[str, Any] | None = None
    end_ns = count = 0
    for where, text in _complete_lines(path, offset):
        record = _record(where, text)
        if record["kind"] == "start":
            start, count = record, 0
        count += 1
        end_ns = record["ts_ns"]
    if start is None:
        raise ReplayRefused(f"{path} holds no `start` record after byte {offset}")
    return LiveSegment(start, end_ns, count - 1)


def aligned_start(live_start_ns: int, requested_ns: int) -> int:
    """
    Return the first live grid time (`live_start_ns + k s`, k >= 0) at or after `requested_ns`: a
    start before the live run is the live start (the comparator refuses a replay starting earlier).
    """
    k = max(0, -((live_start_ns - requested_ns) // NS_PER_S))  # ceil((requested - start) / 1 s)
    return live_start_ns + k * NS_PER_S


def replay_window(
    segment: LiveSegment, start_ns: int | None, end_ns: int | None
) -> tuple[int, int]:
    """Return the replay's `[start, end]`: the live segment's, or the overrides (start snapped)."""
    start = segment.start_ns if start_ns is None else aligned_start(segment.start_ns, start_ns)
    end = segment.end_ns if end_ns is None else end_ns
    if end <= start:
        raise ReplayRefused(f"empty replay window [{start}, {end}]")
    return start, end


def _same_value(live: object, configured: object) -> bool:
    """
    Judge a `start` record field against a paper-file value: numbers as decimals (`3` is `3.0`,
    `"0.004"` is `0.004`), lists in order, any other text exactly.
    """
    if isinstance(live, list) and isinstance(configured, list | tuple):
        return len(live) == len(configured) and all(
            _same_value(a, b) for a, b in zip(live, configured, strict=True)
        )
    if isinstance(live, bool) or isinstance(configured, bool):  # `True == 1` in Python: not here
        return type(live) is type(configured) and live == configured
    if live == configured:
        return True
    numeric = (int, float, str)
    if not (isinstance(live, numeric) and isinstance(configured, numeric)):
        return False
    try:
        return Decimal(str(live)) == Decimal(str(configured))
    except ArithmeticError:  # decimal.InvalidOperation: text that is no number
        return False


def _check_params_config(bot: BotConfig, start_record: dict[str, Any]) -> None:
    """Refuse a string-path bot's live run whose `start` record differs from the paper file."""
    expected = {
        key: value
        for key, value in strategy_params(bot, None).items()
        if key != "order_id_tag"  # the record's `bot_id`, checked by the parity tool
    }
    differing = {
        name: (start_record.get(name), value)
        for name, value in expected.items()
        if not _same_value(start_record.get(name), value)
    }
    if start_record.get("strategy") != bot.strategy:
        differing["strategy"] = (start_record.get("strategy"), bot.strategy)
    if differing:
        raise ReplayRefused(f"{bot.bot_id}: live run vs config (live, file): {differing}")


def check_same_config(bot: BotConfig, start_record: dict[str, Any]) -> None:
    """Refuse a live run whose `start` record names other parameters than the paper file."""
    if bot.strategy != _DUMMY:
        _check_params_config(bot, start_record)
        return
    expected = {
        "instrument_id": bot.instrument_id,
        "trade_size": str(bot.trade_size),
        "trend_buy_threshold": bot.trend_buy_threshold,
        "trend_sell_threshold": bot.trend_sell_threshold,
        "ofi_confirm_threshold": bot.ofi_confirm_threshold,
    }
    differing = {
        name: (start_record.get(name), expected[name])
        for name in _CHECKED_FIELDS
        if start_record.get(name) != expected[name]
    }
    if differing:
        raise ReplayRefused(f"{bot.bot_id}: live run vs config (live, file): {differing}")


def catalog_instrument(
    catalog: ParquetDataCatalog, instrument_id: str, start_ns: int
) -> Instrument:
    """
    Return the definition in force at the window start (the latest with `ts_init <= start`, as
    the live bot's cache held it); refused when none was stored by then.
    """
    found = [
        instrument
        for instrument in catalog.instruments(instrument_ids=[instrument_id])
        if instrument.ts_init <= start_ns
    ]
    if not found:
        raise ReplayRefused(f"no {instrument_id} definition stored by the window start {start_ns}")
    return max(found, key=lambda instrument: instrument.ts_init)


def _snapshot_chunks(
    catalog: ParquetDataCatalog, instrument_id: str, start_ns: int, end_ns: int
) -> Iterator[list[DydxSecondSnapshot]]:
    """Yield the stored rows with `ts_init` in `[start, end]`, one hour at a time, `ts_init` order."""
    for chunk_start in range(start_ns, end_ns + 1, _CHUNK_NS):
        chunk_end = min(chunk_start + _CHUNK_NS - 1, end_ns)
        results = catalog.query(
            data_cls=DydxSecondSnapshot,
            identifiers=[instrument_id],
            start=chunk_start,
            end=chunk_end,
        )
        # A custom data type comes back wrapped in `CustomData`.
        rows = [r.data if hasattr(r, "data") else r for r in results]
        yield sorted(rows, key=lambda row: row.ts_init)


def write_derived(
    source: ParquetDataCatalog,
    derived: ParquetDataCatalog,
    instrument: Instrument,
    start_ns: int,
    end_ns: int,
    with_book: bool = True,
) -> DerivedCounts:
    """
    Write the instrument and every row's quote in `[start, end]` to `derived`, and its deltas
    unless `with_book` is False (a cascade bot reads no book).
    """
    derived.write_data([instrument])
    rows = deltas = quotes = 0
    for chunk in _snapshot_chunks(source, str(instrument.id), start_ns, end_ns):
        book: list[OrderBookDelta] = []
        tops: list[QuoteTick] = []
        for row in chunk:
            if with_book:
                book.extend(snapshot_deltas(instrument, row).deltas)
            quote = snapshot_quote(instrument, row)
            if quote is not None:
                tops.append(quote)
        if book:
            derived.write_data(book)
        if tops:
            derived.write_data(tops)
        rows, deltas, quotes = rows + len(chunk), deltas + len(book), quotes + len(tops)
    if rows == 0:
        raise ReplayRefused(f"no {instrument.id} snapshot rows between {start_ns} and {end_ns}")
    return DerivedCounts(rows, deltas, quotes)


def _data_configs(
    source_path: str,
    derived_path: str,
    instrument_id: InstrumentId,
    window: tuple[int, int],
    strategy: str = _DUMMY,
) -> list[BacktestDataConfig]:
    """
    Stream a dummy bot's derived deltas and quotes and the source's trades, or a cascade bot's
    derived quotes and the source's liquidations; bounds inclusive, on `ts_init`.
    """
    start_ns, end_ns = window
    bounds = {"instrument_id": instrument_id, "start_time": start_ns, "end_time": end_ns}
    if strategy in LIQUIDATION_STRATEGIES:
        return [
            BacktestDataConfig(catalog_path=derived_path, data_cls=QuoteTick, **bounds),
            BacktestDataConfig(
                catalog_path=source_path,
                data_cls=_LIQUIDATION_CLS,
                client_id=LIQUIDATION_CLIENT_ID,
                **bounds,
            ),
        ]
    return [
        BacktestDataConfig(catalog_path=derived_path, data_cls=OrderBookDelta, **bounds),
        BacktestDataConfig(catalog_path=derived_path, data_cls=QuoteTick, **bounds),
        BacktestDataConfig(catalog_path=source_path, data_cls=TradeTick, **bounds),
    ]


def strategy_config(bot: BotConfig, signal_log_path: Path) -> ImportableStrategyConfig:
    """Build the bot's strategy with exactly the fields the live host sets (`_strategy_for`)."""
    paths = STRATEGIES[bot.strategy]
    if paths is not None:
        strategy_path, config_path = paths
        return ImportableStrategyConfig(
            strategy_path=strategy_path,
            config_path=config_path,
            config=strategy_params(bot, str(signal_log_path)),
        )
    return ImportableStrategyConfig(
        strategy_path=_STRATEGY_PATH,
        config_path=_CONFIG_PATH,
        config={
            "instrument_id": bot.instrument_id,
            "trade_size": str(bot.trade_size),
            "trend_buy_threshold": bot.trend_buy_threshold,
            "trend_sell_threshold": bot.trend_sell_threshold,
            "ofi_confirm_threshold": bot.ofi_confirm_threshold,
            "take_profit_bps": bot.take_profit_bps,
            "stop_loss_bps": bot.stop_loss_bps,
            "signal_log_path": str(signal_log_path),
            "order_id_tag": bot.bot_id,
        },
    )


def venue_config(venue: str, pool: VenuePaperConfig) -> BacktestVenueConfig:
    """Build the venue as the fleet's Sandbox client simulates it (see the module docstring)."""
    return BacktestVenueConfig(
        name=venue,
        oms_type="NETTING",
        account_type=pool.account_type,
        starting_balances=list(pool.starting_balances),
        book_type="L1_MBP",
        default_leverage=1.0,
    )


def run_config(
    bot: BotConfig,
    fleet: PaperFleet,
    paths: tuple[str, str],
    window: tuple[int, int],
    signal_log_path: Path,
) -> BacktestRunConfig:
    """Build one run: the bot's strategy over `window` (source and derived catalog `paths`)."""
    source_path, derived_path = paths
    start_ns, end_ns = window
    venue = venue_of(bot.instrument_id)
    instrument_id = InstrumentId.from_str(bot.instrument_id)
    return BacktestRunConfig(
        engine=BacktestEngineConfig(
            logging=LoggingConfig(bypass_logging=True),
            strategies=[strategy_config(bot, signal_log_path)],
        ),
        venues=[venue_config(venue, fleet.venue_config(venue))],
        data=_data_configs(source_path, derived_path, instrument_id, window, bot.strategy),
        start=start_ns,
        end=end_ns,
        raise_exception=True,
    )


def _run_node(config: BacktestRunConfig) -> int:
    """
    Run one config; return the engine's iteration count. Refused when nothing streamed or the
    engine raised (the bot's run is lost, never its error: `main` ledgers it).
    """
    node = BacktestNode(configs=[config])
    try:
        results = node.run()
    except Exception as exc:  # the engine's and the strategy's errors alike
        raise ReplayRefused(f"run {config.id} raised {exc!r}") from exc
    finally:
        node.dispose()
    if len(results) != 1 or results[0].iterations == 0:
        raise ReplayRefused(f"run {config.id} streamed no data")
    return results[0].iterations


def _check_replay_log(path: Path, window: tuple[int, int], offset: int) -> int:
    """
    Check the run wrote a fresh segment (a `start` record after `offset`, the file's size before
    the run: an older run's segment never passes for this one) that starts at the window start
    and reaches its end within one timer interval; return its cycle count.
    """
    start_ns, end_ns = window
    segment = latest_segment(path, offset)
    if segment.start_ns != start_ns:
        raise ReplayRefused(f"{path}: the replay started at {segment.start_ns}, not {start_ns}")
    if segment.end_ns < end_ns - NS_PER_S:
        raise ReplayRefused(
            f"{path}: the replay's last record {segment.end_ns} stops short of the window end "
            f"{end_ns} (by over one 1 s interval): a run cut short"
        )
    return segment.cycles


def replay_bot(
    bot: BotConfig, fleet: PaperFleet, catalog_path: str, window: tuple[int, int], out_dir: Path
) -> ReplayOutcome:
    """Replay one bot over `window`; its log is appended to `<out_dir>/<bot_id>.jsonl`."""
    began = time.monotonic()
    source = ParquetDataCatalog(catalog_path)
    instrument = catalog_instrument(source, bot.instrument_id, window[0])
    log_path = out_dir / f"{bot.bot_id}.jsonl"
    offset = log_path.stat().st_size if log_path.exists() else 0
    with tempfile.TemporaryDirectory(prefix="signal_replay_") as derived_path:
        with_book = bot.strategy == _DUMMY
        derived = ParquetDataCatalog(derived_path)
        counts = write_derived(source, derived, instrument, *window, with_book=with_book)
        config = run_config(bot, fleet, (catalog_path, derived_path), window, log_path)
        iterations = _run_node(config)
    records = _check_replay_log(log_path, window, offset)
    elapsed = time.monotonic() - began
    return ReplayOutcome(bot.bot_id, *window, counts, iterations, records, elapsed)


def _iso_ns(text: str) -> int:
    stamp = pd.Timestamp(text)
    if stamp.tzinfo is None:
        raise argparse.ArgumentTypeError(f"{text!r} needs an explicit UTC offset (e.g. ...Z)")
    return int(stamp.value)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m bots.signal_replay", description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="the fleet's paper file")
    parser.add_argument("--catalog", required=True, help="the source catalog (read only)")
    parser.add_argument("--live-log", required=True, type=Path, help="the live signal log dir")
    parser.add_argument("--out", required=True, type=Path, help="the replay signal log dir")
    parser.add_argument("--start", type=_iso_ns, help="override (snapped onto the live grid)")
    parser.add_argument("--end", type=_iso_ns, help="override the window's end")
    parser.add_argument("--bot", action="append", help="replay only this bot_id (repeatable)")
    parser.add_argument("--strategy", choices=REPLAYED, help="replay only this strategy's bots")
    return parser


def _selected_bots(
    fleet: PaperFleet, wanted: list[str] | None, strategy: str | None = None
) -> list[BotConfig]:
    """Return the replayable bots (`REPLAYED`, or `strategy`'s), or the `wanted` ids of them."""
    kinds = REPLAYED if strategy is None else (strategy,)
    replayable = [bot for bot in fleet.bots if bot.strategy in kinds]
    if wanted is None:
        return replayable
    unknown = sorted(set(wanted) - {bot.bot_id for bot in replayable})
    if unknown:
        raise ReplayRefused(f"no replayable bot {unknown} of {list(kinds)} in the config")
    return [bot for bot in replayable if bot.bot_id in wanted]


def _report(outcome: ReplayOutcome) -> str:
    derived = outcome.derived
    return (
        f"{outcome.bot_id}: [{outcome.start_ns}, {outcome.end_ns}] rows={derived.rows} "
        f"deltas={derived.deltas} quotes={derived.quotes} iterations={outcome.iterations} "
        f"records={outcome.records} wall={outcome.wall_seconds:.1f}s"
    )


def _replay_one(args: argparse.Namespace, fleet: PaperFleet, bot: BotConfig) -> ReplayOutcome:
    segment = latest_segment(args.live_log / f"{bot.bot_id}.jsonl")
    check_same_config(bot, segment.start)
    window = replay_window(segment, args.start, args.end)
    return replay_bot(bot, fleet, args.catalog, window, args.out)


def _refuse(detail: str, exc: BaseException) -> None:
    """Ledger one refusal (DATA-07) and say it on stderr."""
    error_ledger.record(REFUSED_SITE, detail, exc)
    print(f"refused: {detail}", file=sys.stderr, flush=True)


def _refuse_cascade_start(bots: list[BotConfig], start_ns: int | None) -> None:
    """
    Refuse `--start` when a cascade bot is selected: its parity needs the replay to start at the
    live start (the detector's baseline remembers `baseline_s` of history a later start never had,
    `verification.domain.cascade_parity`), so a later start could only be refused there, after a
    whole replay.
    """
    cascade = [bot.bot_id for bot in bots if bot.strategy in LIQUIDATION_STRATEGIES]
    if start_ns is not None and cascade:
        raise ReplayRefused(
            f"--start cannot be set for {cascade}: a cascade replay starts at the live start "
            "(replay the other bots with --strategy dummy)"
        )


def run(args: argparse.Namespace) -> tuple[list[ReplayOutcome], list[str]]:
    """
    Replay every selected bot of the paper file (see the module docstring); return the
    outcomes and the refused bots' ids. One bot's refusal is ledgered and the next bot still runs;
    `--start` with a cascade bot selected refuses the whole run up front.
    """
    fleet = load_paper_config(args.config)
    bots = _selected_bots(fleet, args.bot, args.strategy)
    _refuse_cascade_start(bots, args.start)
    outcomes, refused = [], []
    for bot in bots:
        try:
            outcome = _replay_one(args, fleet, bot)
        except _REFUSALS as exc:
            _refuse(f"{bot.bot_id}: {exc}", exc)
            refused.append(bot.bot_id)
            continue
        print(_report(outcome), flush=True)
        outcomes.append(outcome)
    return outcomes, refused


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    error_ledger.start(service=error_ledger.job_service("signal_replay", "bots"))
    try:
        _, refused = run(args)
    except _REFUSALS as exc:  # the paper file or the bot selection
        _refuse(str(exc), exc)
        return 1
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
