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
The display chain traced value by value (Story 31.9, AC1): one stored second followed along every
hop -- catalog row -> `snapshots:raw` -> `RankingBoard` -> `rankings:live` -> `metrics.db` -> the
`data_api` responses -- and every field compared, per hop, with the hop before it or with the
independent reference (`verification.domain.reference_signals`, 31.3's tolerances).

Fixture variant (always runs): the committed real rows (Bybit BTCUSDT linear, Hyperliquid SOL) are
published as `snapshots:raw` JSON through a real `RankingEngine` (in-memory publishers, a real tmp
`metrics.db`), the engine's own message is served by the real `/api/rankings`, its row by the real
`/api/metrics/history`, and the rows by the real `/api/snapshots` over a tmp `write_data` catalog.
The traced second is each fixture's last row, so every windowed field covers all 300 rows so far.

Every difference a hop makes on purpose is one named row of `ACCOUNTED`; every key a hop emits is
either compared or names such a row (`RANK_KEYS`, `DB_KEYS`, `SNAPSHOT_KEYS`), so a new field
fails here until it is traced.

Live variant (`VERIFY_STACK=1`, skipped otherwise): the same hops against the running verify stack
(redis 127.0.0.1:26379, data_api 127.0.0.1:29100, the catalog at `CATALOG_PATH`).

A declared composition root (`tests/test_boundaries.py`): it drives the code it traces.
"""

import asyncio
import json
import os
import sqlite3
import time
import urllib.request
from collections.abc import Callable
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any
from typing import NamedTuple

import data_api.app as app_module
import data_api.routes.metrics as metrics_routes
import data_api.routes.snapshots as snapshots_routes
import pytest
import redis
from data_api import buses
from fastapi.testclient import TestClient
from kernel.indicators import OFI_GAP_NS
from kernel.indicators import mid_price
from kernel.liquidation import Liquidation
from kernel.open_interest import OpenInterest
from kernel.second_snapshot import DydxSecondSnapshot
from observability import error_ledger
from ranking.application.engine import RankingConfig
from ranking.application.engine import RankingEngine
from ranking.application.ports import SNAPSHOTS_CHANNEL
from ranking.domain.board import ROLLING_WINDOW
from ranking.domain.board import VOLUME_DELTA_WINDOW
from ranking.domain.board import RankingBoard
from ranking.domain.board import RankingsPublisher
from ranking.domain.metrics import PCT_MAX_SHORTFALL_NS
from ranking.domain.metrics import VOLATILITY_WINDOW_NS
from ranking.domain.price_series import PricePoint
from ranking.infrastructure import metrics_store
from ranking.infrastructure.metrics_store import COLS
from ranking.infrastructure.metrics_store import SqliteMetricsStore
from ranking.tests.support import FakeClock
from views.rankings_bus import RankingsBus

from nautilus_trader.model.data import CustomData
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from verification.domain import reference_signals as ref
from verification.domain.reference_signals import RefBook
from verification.domain.signal_compare import FAILING
from verification.domain.signal_compare import Agreement
from verification.domain.signal_compare import Tally
from verification.domain.signal_compare import at_places
from verification.domain.signal_compare import equal
from verification.domain.signal_compare import relative
from verification.domain.signal_compare import within_ulps
from verification.tests.signal_cases import NS_PER_S
from verification.tests.signal_cases import carried
from verification.tests.signal_cases import load_fixture
from verification.tests.signal_cases import pinned_zero


# --- the accounted differences --------------------------------------------------------------


class Accounted(NamedTuple):
    """One difference a hop makes on purpose: where, which field, and why it is not a defect."""

    name: str
    hop: str
    field: str
    difference: str


ACCOUNTED: tuple[Accounted, ...] = (
    Accounted(
        "metrics_price_is_trade_close",
        "board -> metrics.db",
        "price",
        "the last trade close of the price series (DATA_DICTIONARY §3.3), never the live mid that "
        "rankings:live calls `price`; compared with the reference close instead",
    ),
    Accounted(
        "metrics_ts_is_wall_clock",
        "board -> metrics.db",
        "ts",
        "the engine's wall clock when the slow loop ran, not any row's ts_event",
    ),
    Accounted(
        "rankings_live_has_no_ts_event",
        "board -> rankings:live",
        "updated_at",
        "the message carries the engine's wall clock at build; no rank entry names the ts_event "
        "of the second it reflects",
    ),
    Accounted(
        "spread_rounded_to_price_precision",
        "board -> rankings:live, metrics.db",
        "spread",
        "`kernel.indicators.spread` rounds the float difference to the row's price precision "
        "(Story 31.3); judged `at_places(p)`",
    ),
    Accounted(
        "float_decode",
        "snapshots:raw -> board",
        "every derived value",
        "the board decodes `units / 10^p` to the nearest double and computes in floats; judged "
        "`at_places` (exact values) or `relative`/`within_ulps` (divisions) against the exact "
        "reference, FLOAT_NOISE reported as its own class",
    ),
    Accounted(
        "snapshots_mid_is_kernel_mid_price",
        "catalog -> /api/snapshots",
        "mid",
        "`views.chart_series.price_series_rows` serves `kernel.indicators.mid_price` (it computed "
        "`(bid + ask) / 2` inline until Story 31.9, an SSOT-01 smell, audit D-131; the verify "
        "stack's data_api image predates the fix): compared, every row, with both the kernel "
        "function (bit-equal) and the reference",
    ),
    Accounted(
        "api_rankings_renames_ranks",
        "rankings:live -> /api/rankings",
        "ranks",
        "the one sanctioned reshape: `ranks` is served as `items`; every entry byte-for-byte",
    ),
    Accounted(
        "metrics_ofi_is_ofi_5",
        "board -> metrics.db",
        "ofi",
        "metrics.db's `ofi` is the rank entry's raw `ofi_5` under its historical name",
    ),
    Accounted(
        "metrics_rank_is_real",
        "board -> metrics.db",
        "rank",
        "stored in a REAL column: the rank entry's int 1 reads back as 1.0 (equal as numbers)",
    ),
    Accounted(
        "metrics_api_row_has_no_instrument_id",
        "metrics.db -> /api/metrics/history",
        "instrument_id",
        "the history item omits the id: the request path names it",
    ),
    Accounted(
        "undefined_z_published_as_zero",
        "board -> rankings:live",
        "ofi_10_z",
        "an undefined z-score is published 0.0 (DATA_DICTIONARY §2.2, D-89): `pinned_zero`",
    ),
    Accounted(
        "obi_carried_on_zero_total",
        "board -> rankings:live",
        "obi_3/obi_5/obi_10",
        "a zero-total OBI keeps the last defined value (§2.3, D-89): the reference is `carried`",
    ),
    Accounted(
        "snapshots_t_is_milliseconds",
        "catalog -> /api/snapshots",
        "t",
        "the chart's time axis is `ts_event // 10^6` (ms), truncated",
    ),
    Accounted(
        "volume24h_from_the_volume_poll",
        "volume source -> rankings:live, metrics.db",
        "volume24h",
        "the venue's USD 24 h volume from the poll, not derived from any snapshot",
    ),
    Accounted(
        "slow_fields_from_the_last_slow_loop",
        "metrics.db -> rankings:live",
        "pct_1h/pct_24h/pct_1w/pct_1m/volatility and Story 33.4's derivatives/flow/range fields",
        "copied from the last slow-loop row (None before the first, or when older than 3 cycles). "
        "The 33.4 fields have no reference here: the trace feeds no derivs:raw/liquidations:raw "
        "and spans under 2 h; each is hand-computed in ranking/tests/test_derivs.py",
    ),
    Accounted(
        "in_flight_publish",
        "snapshots:raw -> rankings:live (live stack)",
        "every stateless field",
        "a message published while the engine still handles the previous batch reflects that "
        "batch, although the newer one already reached a subscriber: matched to the newest or the "
        "one before it, counted apart",
    ),
    Accounted(
        "board_changed_before_its_publish",
        "board -> metrics.db (live stack)",
        "ofi/microprice/spread/rank/volume24h",
        "the slow loop read the board after a change no message carried yet: a batch whose publish "
        "waits on the publish lock, or a volume refresh, which publishes nothing itself (the next "
        "batch or heartbeat does). Matched to the first message after `ts`; after a volume refresh "
        "alone, `volume24h`/`rank` from that message and the book columns from the last at/before "
        "it. Counted apart (D-130: `ts` itself is stamped when the board is read)",
    ),
)
_ACCOUNTED_NAMES = frozenset(a.name for a in ACCOUNTED)

# How each hop's keys are judged: "reference" (the independent reference), "identity" (a literal
# spelled out here), "hop" (equal to the previous hop), or "accounted:<name>" (a row above).
RANK_KEYS: dict[str, str] = {
    "instrument_id": "identity",
    "venue": "identity",
    "symbol": "identity",
    "venue_kind": "identity",
    "market": "identity",
    "rank": "identity",
    "volume24h": "accounted:volume24h_from_the_volume_poll",
    "volatility_score": "reference",
    "ofi_10_z": "accounted:undefined_z_published_as_zero",
    "ofi_3": "reference",
    "ofi_5": "reference",
    "ofi_10": "reference",
    "obi_3": "accounted:obi_carried_on_zero_total",
    "obi_5": "accounted:obi_carried_on_zero_total",
    "obi_10": "accounted:obi_carried_on_zero_total",
    "microprice": "reference",
    "microprice_lean": "reference",
    "spread": "accounted:spread_rounded_to_price_precision",
    "cvd": "reference",
    "volume_delta": "reference",
    "buy_count": "reference",
    "sell_count": "reference",
    "avg_trade_size": "reference",
    "volatility_fast": "reference",
    "price": "reference",
    "pct_1h": "accounted:slow_fields_from_the_last_slow_loop",
    "pct_24h": "accounted:slow_fields_from_the_last_slow_loop",
    "pct_1w": "accounted:slow_fields_from_the_last_slow_loop",
    "pct_1m": "accounted:slow_fields_from_the_last_slow_loop",
    "volatility": "accounted:slow_fields_from_the_last_slow_loop",
    "funding_rate": "accounted:slow_fields_from_the_last_slow_loop",
    "funding_annualised": "accounted:slow_fields_from_the_last_slow_loop",
    "next_funding_ns": "accounted:slow_fields_from_the_last_slow_loop",
    "open_interest": "accounted:slow_fields_from_the_last_slow_loop",
    "oi_change_1h": "accounted:slow_fields_from_the_last_slow_loop",
    "oi_change_24h": "accounted:slow_fields_from_the_last_slow_loop",
    "basis_mi_bps": "accounted:slow_fields_from_the_last_slow_loop",
    "basis_ml_bps": "accounted:slow_fields_from_the_last_slow_loop",
    "liq_long_1h": "accounted:slow_fields_from_the_last_slow_loop",
    "liq_short_1h": "accounted:slow_fields_from_the_last_slow_loop",
    "liq_notional_1h": "accounted:slow_fields_from_the_last_slow_loop",
    "liq_ratio_1h": "accounted:slow_fields_from_the_last_slow_loop",
    "forced_share_1h": "accounted:slow_fields_from_the_last_slow_loop",
    "relative_volume": "accounted:slow_fields_from_the_last_slow_loop",
    "high_24h": "accounted:slow_fields_from_the_last_slow_loop",
    "low_24h": "accounted:slow_fields_from_the_last_slow_loop",
    "range_position_24h": "accounted:slow_fields_from_the_last_slow_loop",
}
MESSAGE_KEYS: dict[str, str] = {
    "mode": "identity",
    "updated_at": "accounted:rankings_live_has_no_ts_event",
    "ranks": "accounted:api_rankings_renames_ranks",
    "stale_instrument_ids": "identity",
}
DB_KEYS: dict[str, str] = {
    "ts": "accounted:metrics_ts_is_wall_clock",
    "instrument_id": "accounted:metrics_api_row_has_no_instrument_id",
    "price": "accounted:metrics_price_is_trade_close",
    "pct_1h": "reference",
    "pct_24h": "reference",
    "pct_1w": "reference",
    "pct_1m": "reference",
    "volatility": "reference",
    "ofi": "accounted:metrics_ofi_is_ofi_5",
    "microprice": "hop",
    "spread": "hop",
    "rank": "accounted:metrics_rank_is_real",
    "volume24h": "hop",
    "funding_rate": "hop",
    "funding_annualised": "hop",
    "open_interest": "hop",
    "oi_change_1h": "hop",
    "oi_change_24h": "hop",
    "basis_mi_bps": "hop",
    "basis_ml_bps": "hop",
    "liq_long_1h": "hop",
    "liq_short_1h": "hop",
    "liq_notional_1h": "hop",
    "liq_ratio_1h": "hop",
    "forced_share_1h": "hop",
    "relative_volume": "hop",
    "high_24h": "hop",
    "low_24h": "hop",
    "range_position_24h": "hop",
}
SNAPSHOT_KEYS: dict[str, str] = {
    "t": "accounted:snapshots_t_is_milliseconds",
    "bid_units": "hop",
    "ask_units": "hop",
    "price_precision": "hop",
    "mid": "accounted:snapshots_mid_is_kernel_mid_price",
    "micro": "reference",
    "price": "reference",
}
# metrics.db column -> the rank entry field holding the board's value at write time.
_DB_FROM_RANK = {
    "ofi": "ofi_5",
    "microprice": "microprice",
    "spread": "spread",
    "rank": "rank",
    "volume24h": "volume24h",
    "pct_1h": "pct_1h",
    "pct_24h": "pct_24h",
    "pct_1w": "pct_1w",
    "pct_1m": "pct_1m",
    "volatility": "volatility",
}
# Story 33.4's metrics.db columns -> the rank entry fields holding them (the same slow row); checked
# at write time only -- the live-stack judge's column groups predate them.
_DB_33_4_FROM_RANK = {
    "funding_rate": "funding_rate",
    "funding_annualised": "funding_annualised",
    "open_interest": "open_interest",
    "oi_change_1h": "oi_change_1h",
    "oi_change_24h": "oi_change_24h",
    "basis_mi_bps": "basis_mi_bps",
    "basis_ml_bps": "basis_ml_bps",
    "liq_long_1h": "liq_long_1h",
    "liq_short_1h": "liq_short_1h",
    "liq_notional_1h": "liq_notional_1h",
    "liq_ratio_1h": "liq_ratio_1h",
    "forced_share_1h": "forced_share_1h",
    "relative_volume": "relative_volume",
    "high_24h": "high_24h",
    "low_24h": "low_24h",
    "range_position_24h": "range_position_24h",
}

# --- the fixture trace ------------------------------------------------------------------------

TRACED_IDS = ("BTCUSDT-LINEAR.BYBIT", "SOL-USD-PERP.HYPERLIQUID")
_IDENTITY = {
    "BTCUSDT-LINEAR.BYBIT": {"venue": "BYBIT", "symbol": "BTC", "venue_kind": "cex"},
    "SOL-USD-PERP.HYPERLIQUID": {"venue": "HYPERLIQUID", "symbol": "SOL", "venue_kind": "dex"},
}
_VOLUME_USD = 123_456_789.25
_HEARTBEAT_S = 5
_VOLATILITY_LOOKBACK_S = 3_600
_HOUR_NS = 3_600 * NS_PER_S


class _Recorder:
    """A `LivePublisher` keeping every message it is handed, verbatim."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def publish(self, message: str) -> None:
        self.messages.append(message)


class _Volumes:
    """A `VolumeSource` answering one fixed USD volume for the traced instrument."""

    def __init__(self, instrument_id: str) -> None:
        self.name = "fixture"
        self._volumes = {instrument_id: _VOLUME_USD}

    async def fetch(self) -> dict[str, float]:
        return dict(self._volumes)


class _NoBackfill:
    """A `PriceHistory` with an empty archive: the price series is only what the trace fed."""

    def series(self, instrument_id: str, start_ns: int) -> list[PricePoint]:
        return []


class _NoDerivs:
    """A `DerivsHistory` with an empty archive: no open interest or liquidation to backfill."""

    def open_interest(self, instrument_id: str, start_ns: int, end_ns: int) -> list[OpenInterest]:
        return []

    def liquidations(self, instrument_id: str, start_ns: int, end_ns: int) -> list[Liquidation]:
        return []


@dataclass(frozen=True)
class _Trace:
    """Every hop's output for one instrument's traced (last) second."""

    iid: str
    rows: list[dict[str, Any]]
    payloads: list[str]
    traced_published: bool
    traced_message: dict[str, Any]
    final_raw: str
    db_row: dict[str, Any]
    slow_ns: int
    db_path: str
    catalog: str

    @property
    def final(self) -> dict[str, Any]:
        return json.loads(self.final_raw)

    def rank(self, message: dict[str, Any] | None = None) -> dict[str, Any]:
        (entry,) = [r for r in (message or self.final)["ranks"] if r["instrument_id"] == self.iid]
        return entry


def _engine(
    iid: str, clock: FakeClock, store: SqliteMetricsStore, live: _Recorder
) -> RankingEngine:
    publisher = RankingsPublisher(_HEARTBEAT_S, now_fn=clock.time)
    board = RankingBoard(publisher, _VOLATILITY_LOOKBACK_S, RankingConfig().volume_max_age_ns)
    return RankingEngine(
        board,
        volume_sources=[_Volumes(iid)],
        prices=_NoBackfill(),
        derivs=_NoDerivs(),
        history=store,
        live=live,
        markets=_Recorder(),
        config=RankingConfig(),
        clock=clock.time_ns,
    )


def _wire(row: dict[str, Any]) -> str:
    """Return the capture encoder's `snapshots:raw` message for one stored row."""
    return json.dumps([DydxSecondSnapshot.to_dict(DydxSecondSnapshot.from_dict(row))])


async def _feed(
    engine: RankingEngine, live: _Recorder, clock: FakeClock, rows: list[dict]
) -> tuple[list[str], int]:
    """Publish each row at its own `ts_init` (arrival); return the payloads and the last's count."""
    payloads, published = [], 0
    for row in rows:
        clock.ns = row["ts_init"]
        payloads.append(_wire(row))
        before = len(live.messages)
        await engine.handle(SNAPSHOTS_CHANNEL, payloads[-1])
        published = len(live.messages) - before
    return payloads, published


async def _run(iid: str, rows: list[dict], db_path: str, monkeypatch: pytest.MonkeyPatch) -> dict:
    clock = FakeClock(rows[0]["ts_init"])
    store = SqliteMetricsStore(db_path)
    live = _Recorder()
    engine = _engine(iid, clock, store, live)
    try:
        await engine.volume_cycle()
        payloads, published = await _feed(engine, live, clock, rows)
        traced = json.loads(live.messages[-1])
        clock.ns += NS_PER_S
        slow_ns = clock.ns
        # metrics.db prunes and reads by the wall clock: the recorded day's, not today's.
        monkeypatch.setattr(metrics_store.time, "time_ns", clock.time_ns)
        await engine.slow_loop_once()
        clock.ns += _HEARTBEAT_S * NS_PER_S  # nothing ingested since: the heartbeat republishes
        await engine.maybe_publish()
    finally:
        store.close()
    return {
        "payloads": payloads,
        "traced_published": published == 1,
        "traced_message": traced,
        "final_raw": live.messages[-1],
        "slow_ns": slow_ns,
    }


def _db_row(db_path: str, iid: str) -> dict[str, Any]:
    """Return the one stored `metrics.db` row, every column, read with plain SQL."""
    with closing(sqlite3.connect(db_path)) as db:
        db.row_factory = sqlite3.Row
        (row,) = db.execute("SELECT * FROM snapshots WHERE instrument_id = ?", (iid,)).fetchall()
        return dict(row)


def _write_catalog(root: Path, rows: list[dict]) -> str:
    ParquetDataCatalog(str(root)).write_data([DydxSecondSnapshot.from_dict(r) for r in rows])
    return str(root)


@pytest.fixture(scope="module", params=TRACED_IDS)
def trace(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> _Trace:
    iid: str = request.param
    rows = load_fixture(iid)
    root = tmp_path_factory.mktemp(iid.replace(".", "_"))
    db_path = str(root / "metrics.db")
    error_ledger.reset()
    with pytest.MonkeyPatch.context() as patched:
        ran = asyncio.run(_run(iid, rows, db_path, patched))
    db_row = _db_row(db_path, iid)
    assert error_ledger.counts() == {}, "the trace ledgered a drop"
    return _Trace(
        iid=iid,
        rows=rows,
        db_row=db_row,
        db_path=db_path,
        catalog=_write_catalog(root / "catalog", rows),
        **ran,
    )


# --- hop 1: catalog row -> snapshots:raw ------------------------------------------------------


def _catalog_rows(catalog: str, iid: str, start_ns: int, end_ns: int) -> list[dict[str, Any]]:
    """Return the stored rows with `ts_init` in `[start, end]`, re-encoded by `to_dict`."""
    found = ParquetDataCatalog(catalog).query(
        DydxSecondSnapshot, identifiers=[iid], start=start_ns, end=end_ns
    )
    objects = [o.data if isinstance(o, CustomData) else o for o in found]
    return [DydxSecondSnapshot.to_dict(o) for o in objects]


def test_the_stored_integers_are_the_payload_integers(trace: _Trace) -> None:
    """Every published entry is the stored row, integer for integer; the catalog holds the same."""
    for row, payload in zip(trace.rows, trace.payloads, strict=True):
        assert json.loads(payload) == [row]
    traced = trace.rows[-1]
    stored = _catalog_rows(trace.catalog, trace.iid, traced["ts_init"], traced["ts_init"])
    assert stored == [traced]
    assert json.loads(trace.payloads[-1]) == stored


# --- hop 2: snapshots:raw -> board -> rankings:live -------------------------------------------


def _fed(rows: list[dict[str, Any]]) -> list[RefBook]:
    """§3.2: the board feeds its trackers only from a two-sided book with a positive mid."""
    books = [RefBook.from_stored(r) for r in rows]
    return [b for b in books if (m := ref.mid(b)) is not None and m > 0]


def _mid(book: RefBook) -> Decimal:
    centre = ref.mid(book)
    assert centre is not None, "a fed book is two-sided"
    return centre


def _closes(rows: list[dict[str, Any]]) -> list[ref.PricePoint]:
    books = [RefBook.from_stored(r) for r in rows]
    return [(b.ts_event, b.close_price) for b in books if b.close_price is not None]


def _expected_flow(fed: list[RefBook]) -> dict[str, Any]:
    window = fed[-ROLLING_WINDOW:]
    return {
        "cvd": ref.cvd(window),
        "volume_delta": ref.cvd(fed[-VOLUME_DELTA_WINDOW:]),
        "avg_trade_size": ref.avg_trade_size(window),
        "volatility_fast": ref.vol_fast([_mid(b) for b in window], ROLLING_WINDOW),
        "volatility_score": ref.vol_score_1h(
            [(b.ts_event, _mid(b)) for b in fed], _VOLATILITY_LOOKBACK_S * NS_PER_S
        ),
        "buy_count": sum(b.buy_count for b in window),
        "sell_count": sum(b.sell_count for b in window),
    }


def _expected_book(fed: list[RefBook]) -> dict[str, Any]:
    """Return the book fields, the trackers built as `InstrumentMetrics` builds them."""
    latest = fed[-1]
    centre, micro = ref.mid(latest), ref.microprice(latest)
    out: dict[str, Any] = {
        "price": centre,
        "microprice": micro,
        "microprice_lean": None if micro is None or centre is None else micro - centre,
        "spread": ref.spread(latest),
        "ofi_10_z": ref.rolling_ofi_z(fed, 10, 50, False, OFI_GAP_NS, 3_600)[-1],
    }
    for n in (3, 5, 10):
        out[f"ofi_{n}"] = ref.rolling_ofi(fed, n, 300, False, OFI_GAP_NS)[-1]
        out[f"obi_{n}"] = carried([ref.obi(b, n) for b in fed])[-1]
    return out


def _expected_slow(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """§3.3 over the trade closes; 1w/1m have no stored base (the trace's metrics.db was empty)."""
    series = _closes(rows)
    return {
        "pct_1h": ref.pct_change(series, _HOUR_NS, PCT_MAX_SHORTFALL_NS),
        "pct_24h": ref.pct_change(series, 24 * _HOUR_NS, PCT_MAX_SHORTFALL_NS),
        "pct_1w": ref.pct_change_from(series[-1][1] if series else None, None),
        "pct_1m": ref.pct_change_from(series[-1][1] if series else None, None),
        "volatility": ref.vol_catalog_24h(series, VOLATILITY_WINDOW_NS),
    }


_AT_SIZE_PLACES = ("cvd", "volume_delta", "ofi_3", "ofi_5", "ofi_10")
_RELATIVE = (
    "avg_trade_size",
    "volatility_fast",
    "volatility_score",
    "microprice",
    "obi_3",
    "obi_5",
    "obi_10",
    "pct_1h",
    "pct_24h",
    "pct_1w",
    "pct_1m",
    "volatility",
)


def _compare_to_reference(tally: Tally, entry: dict, want: dict, book: RefBook) -> None:
    p, s = book.price_precision, book.size_precision
    for name in _AT_SIZE_PLACES:
        tally.record(name, at_places(entry[name], want[name], s))
    for name in _RELATIVE:
        tally.record(name, relative(entry[name], want[name]))
    for name in ("buy_count", "sell_count"):
        tally.record(name, equal(entry[name], want[name]))
    tally.record("price", at_places(entry["price"], want["price"], p + 1))
    tally.record("spread", at_places(entry["spread"], want["spread"], p))
    tally.record("ofi_10_z", pinned_zero(entry["ofi_10_z"], want["ofi_10_z"]))
    magnitude = float(want["price"])
    lean = within_ulps(entry["microprice_lean"], want["microprice_lean"], magnitude)
    tally.record("microprice_lean", lean)


def _assert_agrees(tally: Tally) -> None:
    print("\n" + "\n".join(tally.report()))
    assert tally.signals(), "nothing was compared"
    assert tally.failures() == {}


def test_the_traced_second_published_rankings_live(trace: _Trace) -> None:
    """
    The traced row's own handling published; the heartbeat after the slow loop changed no field
    the board computes from the book (nothing was ingested in between).
    """
    assert trace.traced_published
    fast = [k for k, how in RANK_KEYS.items() if "slow_fields" not in how]
    final, traced = trace.rank(), trace.rank(trace.traced_message)
    assert {k: final[k] for k in fast} == {k: traced[k] for k in fast}


def test_every_rank_field_equals_the_reference_over_the_rows_so_far(trace: _Trace) -> None:
    fed = _fed(trace.rows)
    want = {**_expected_flow(fed), **_expected_book(fed), **_expected_slow(trace.rows)}
    tally = Tally()
    _compare_to_reference(tally, trace.rank(), want, fed[-1])
    _assert_agrees(tally)
    assert tally.count("pct_1h", Agreement.BOTH_UNDEFINED) == 1  # 300 s never reaches back 1 h


def test_the_rank_entry_identity_fields_and_message_envelope(trace: _Trace) -> None:
    entry, message = trace.rank(), trace.final
    expected = {"instrument_id": trace.iid, "market": "perp", "rank": 1, **_IDENTITY[trace.iid]}
    assert {k: entry[k] for k in expected} == expected
    assert entry["volume24h"] == _VOLUME_USD
    assert message["mode"] == "volume"
    assert message["stale_instrument_ids"] == []
    assert message["updated_at"] == trace.slow_ns + _HEARTBEAT_S * NS_PER_S  # the wall clock


def test_every_emitted_key_is_compared_or_accounted(trace: _Trace) -> None:
    assert set(trace.rank()) == set(RANK_KEYS)
    assert set(trace.final) == set(MESSAGE_KEYS)
    assert set(trace.db_row) == set(DB_KEYS)
    rules = [
        *RANK_KEYS.values(),
        *MESSAGE_KEYS.values(),
        *DB_KEYS.values(),
        *SNAPSHOT_KEYS.values(),
    ]
    named = {rule.split(":", 1)[1] for rule in rules if rule.startswith("accounted:")}
    assert named <= _ACCOUNTED_NAMES, "a key names no accounted row"
    assert len(_ACCOUNTED_NAMES) == len(ACCOUNTED), "accounted rows are named uniquely"
    live_only = {"in_flight_publish", "board_changed_before_its_publish", "float_decode"}
    assert _ACCOUNTED_NAMES - named == live_only, "an accounted row no key uses"


# --- hop 3: board -> metrics.db -----------------------------------------------------------------


def test_the_metrics_row_is_the_board_state_at_write_time(trace: _Trace) -> None:
    """Every column equals the rank entry published from the same board state (no ingest since)."""
    entry, row = trace.rank(), trace.db_row
    from_rank = _DB_FROM_RANK | _DB_33_4_FROM_RANK
    assert {col: row[col] for col in from_rank} == {
        col: entry[field] for col, field in from_rank.items()
    }
    assert (row["instrument_id"], row["ts"]) == (trace.iid, trace.slow_ns)
    assert set(row) == {"ts", "instrument_id", *COLS}


def test_the_metrics_price_is_the_last_trade_close(trace: _Trace) -> None:
    (latest,) = _closes(trace.rows)[-1:]
    precision = trace.rows[-1]["price_precision"]
    assert at_places(trace.db_row["price"], latest[1], precision) is Agreement.EXACT


# --- hop 4: rankings:live -> /api/rankings; metrics.db -> /api/metrics/history ----------------


def test_api_rankings_serves_the_bus_message_verbatim(
    trace: _Trace, monkeypatch: pytest.MonkeyPatch
) -> None:
    bus = RankingsBus()
    monkeypatch.setattr(buses, "bus", bus)
    bus.handle_message(json.loads(trace.final_raw))
    served = TestClient(app_module.app).get("/api/rankings")
    assert served.status_code == 200
    message = trace.final
    assert served.json() == {
        "items": message["ranks"],
        "updated_at": message["updated_at"],
        "mode": message["mode"],
        "stale_instrument_ids": message["stale_instrument_ids"],
    }


def test_api_metrics_history_serves_the_stored_row_verbatim(
    trace: _Trace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(metrics_routes, "METRICS_DB_PATH", trace.db_path)
    monkeypatch.setattr(metrics_store.time, "time_ns", lambda: trace.slow_ns)
    served = TestClient(app_module.app).get(f"/api/metrics/history/{trace.iid}")
    assert served.status_code == 200
    (item,) = served.json()["items"]
    assert item == {c: trace.db_row[c] for c in ("ts", *COLS)}


# --- hop 5: catalog -> /api/snapshots ---------------------------------------------------------


def _served_snapshots(trace: _Trace, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    monkeypatch.setattr(snapshots_routes, "CATALOG_PATH", trace.catalog)
    before_ns = trace.rows[-1]["ts_event"] + 1_000_000
    served = TestClient(app_module.app).get(
        f"/api/snapshots/{trace.iid}", params={"before_ns": before_ns, "limit": len(trace.rows)}
    )
    assert served.status_code == 200
    items: list[dict[str, Any]] = served.json()["items"]
    return items


def _compare_snapshot(tally: Tally, item: dict[str, Any], row: dict[str, Any]) -> None:
    book, snapshot = RefBook.from_stored(row), DydxSecondSnapshot.from_dict(row)
    p = row["price_precision"]
    assert item["t"] == row["ts_event"] // 1_000_000
    assert (item["bid_units"], item["ask_units"]) == (row["bid_prices"][0], row["ask_prices"][0])
    assert item["price_precision"] == p
    tally.record("mid", at_places(item["mid"], ref.mid(book), p + 1))
    tally.record("mid_is_kernel_mid", equal(item["mid"], mid_price(snapshot.as_floats())))
    tally.record("micro", relative(item["micro"], ref.microprice(book)))
    tally.record("price", relative(item["price"], ref.cvd_weighted_price(book)))


def test_api_snapshots_serves_the_stored_units_and_reference_mid_micro(
    trace: _Trace, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = _served_snapshots(trace, monkeypatch)
    assert len(items) == len(trace.rows), "a 1 s fixture has no gap row"
    assert set(items[-1]) == set(SNAPSHOT_KEYS)
    tally = Tally()
    for item, row in zip(items, trace.rows, strict=True):
        _compare_snapshot(tally, item, row)
    _assert_agrees(tally)
    entry = trace.rank()
    assert (items[-1]["mid"], items[-1]["micro"]) == (entry["price"], entry["microprice"])


# --- the live variant (the running verify stack) ------------------------------------------------

LIVE = pytest.mark.skipif(
    os.environ.get("VERIFY_STACK") != "1",
    reason="traces the running verify stack: set VERIFY_STACK=1 (and CATALOG_PATH)",
)
_REDIS = ("127.0.0.1", 26379)
_API = "http://127.0.0.1:29100"
_CATALOG_WAIT_S = 150
_LISTEN_S = 30
_METRICS_WAIT_S = 90


class _Bus:
    """One subscriber on both channels, in the order the server delivered them."""

    def __init__(self) -> None:
        client = redis.Redis(host=_REDIS[0], port=_REDIS[1], decode_responses=True)
        self._pubsub = client.pubsub()
        self._pubsub.subscribe(SNAPSHOTS_CHANNEL, "rankings:live")

    def read(self, seconds: float) -> list[tuple[str, Any]]:
        """Every message received within `seconds`, `(channel, decoded payload)`."""
        out: list[tuple[str, Any]] = []
        deadline = time.monotonic() + seconds
        while (left := deadline - time.monotonic()) > 0:
            message = self._pubsub.get_message(timeout=min(left, 1.0))
            if message is not None and message["type"] == "message":
                out.append((message["channel"], json.loads(message["data"])))
        return out

    def close(self) -> None:
        self._pubsub.close()


@pytest.fixture
def bus() -> Iterator[_Bus]:
    subscriber = _Bus()
    yield subscriber
    subscriber.close()


def _get(path: str) -> Any:
    with urllib.request.urlopen(f"{_API}{path}", timeout=10) as response:  # noqa: S310 -- fixed URL
        return json.loads(response.read())


_STATELESS = ("price", "microprice", "microprice_lean", "spread", "obi_3", "obi_5", "obi_10")


def _stateless_verdicts(entry: dict[str, Any], book: RefBook) -> list[Agreement]:
    """Judge the stateless fields of one live rank entry against one decoded batch entry."""
    p = book.price_precision
    micro, centre = ref.microprice(book), ref.mid(book)
    verdicts = [
        at_places(entry["price"], centre, p + 1),
        relative(entry["microprice"], micro),
        at_places(entry["spread"], ref.spread(book), p),
    ]
    if micro is not None and centre is not None:
        verdicts.append(within_ulps(entry["microprice_lean"], micro - centre, float(centre)))
    for n in (3, 5, 10):
        if (obi := ref.obi(book, n)) is not None:  # a zero total carries the last (accounted)
            verdicts.append(relative(entry[f"obi_{n}"], obi))
    return verdicts


def _agrees(entry: dict[str, Any], book: RefBook) -> bool:
    return not any(v in FAILING for v in _stateless_verdicts(entry, book))


def _judge_message(message: dict, fed: dict[str, list[RefBook]], counts: dict[str, int]) -> None:
    for entry in message["ranks"]:
        books = fed.get(entry["instrument_id"], [])
        if len(books) < 2:
            counts["unseen"] += 1  # the engine's state may predate this subscription
        elif _agrees(entry, books[-1]):
            counts["newest"] += 1
        elif _agrees(entry, books[-2]):
            counts["in_flight"] += 1  # accounted: in_flight_publish
        else:
            counts["different"] += 1
            print(f"DIFFERENT {entry['instrument_id']}: {entry} vs {books[-1]}")


def _feed_books(batch: list[dict[str, Any]], fed: dict[str, list[RefBook]]) -> None:
    for row in batch:
        (book,) = _fed([row]) or (None,)
        if book is not None:
            kept = fed.setdefault(row["instrument_id"], [])
            kept[:] = [*kept[-1:], book]


@LIVE
def test_live_rankings_stateless_fields_match_the_last_batch(bus: _Bus) -> None:
    fed: dict[str, list[RefBook]] = {}
    counts = {"newest": 0, "in_flight": 0, "unseen": 0, "different": 0}
    for channel, payload in bus.read(_LISTEN_S):
        if channel == SNAPSHOTS_CHANNEL:
            _feed_books(payload, fed)
        else:
            _judge_message(payload, fed, counts)
    print(f"\nrankings:live entries: {counts}")
    assert counts["newest"] > 0
    assert counts["different"] == 0


def _stored_row(catalog: str, entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the catalog's row of one published entry, [] while unflushed (or mid-write)."""
    try:
        rows = _catalog_rows(catalog, entry["instrument_id"], entry["ts_init"], entry["ts_init"])
    except Exception as exc:  # a file the collector is still writing: read again next poll
        print(f"catalog read retried: {exc!r}")
        return []
    return [r for r in rows if r["ts_event"] == entry["ts_event"]]


def _wait_stored(catalog: str, entry: dict[str, Any], deadline: float) -> list[dict[str, Any]]:
    while not (stored := _stored_row(catalog, entry)) and time.monotonic() < deadline:
        time.sleep(5)
    return stored


@LIVE
def test_live_traced_rows_reach_the_catalog_unchanged(bus: _Bus) -> None:
    """Every entry of the first batches seen (both venues) is flushed integer for integer."""
    catalog = os.environ.get("CATALOG_PATH", "data/catalog")
    assert Path(catalog).is_dir(), f"CATALOG_PATH {catalog!r} is not the verify catalog"
    entries = [e for c, p in bus.read(3) if c == SNAPSHOTS_CHANNEL for e in p]
    assert entries, "no snapshots:raw entry in 3 s"
    started = time.monotonic()
    deadline = started + _CATALOG_WAIT_S
    for entry in entries:
        stored = _wait_stored(catalog, entry, deadline)
        assert stored == [entry], f"{entry['instrument_id']} @ {entry['ts_event']} not as sent"
    print(f"\n{len(entries)} traced rows stored as sent after {time.monotonic() - started:.0f} s")


def _rankings_matches(served: dict[str, Any], message: dict[str, Any]) -> bool:
    return served == {
        "items": message["ranks"],
        "updated_at": message["updated_at"],
        "mode": message["mode"],
        "stale_instrument_ids": message.get("stale_instrument_ids", []),
    }


def _rankings_messages(bus: _Bus, seconds: float) -> list[dict[str, Any]]:
    return [p for c, p in bus.read(seconds) if c == "rankings:live"]


@LIVE
def test_live_api_rankings_serves_the_latest_bus_message(bus: _Bus) -> None:
    """The message current at the request, or the one published while it was in flight."""
    for _attempt in range(5):
        before = _rankings_messages(bus, 1.5)[-1:]
        served = _get("/api/rankings")
        after = _rankings_messages(bus, 1.5)[:2]
        if any(_rankings_matches(served, m) for m in [*before, *after]):
            return
    pytest.fail(f"/api/rankings never equalled a bus message: {served}")


def _new_metrics_row(iid: str, since_ns: int) -> dict[str, Any] | None:
    items = _get(f"/api/metrics/history/{iid}?days=1")["items"]
    return items[-1] if items and items[-1]["ts"] > since_ns else None


def _rank_at(messages: list[dict[str, Any]], iid: str, pick: Callable[[int], bool]) -> list[dict]:
    return [
        {**r, "updated_at": m["updated_at"]}
        for m in messages
        if pick(m["updated_at"])
        for r in m["ranks"]
        if r["instrument_id"] == iid
    ]


def _db_view(entry: dict[str, Any], columns: tuple[str, ...]) -> dict[str, Any]:
    return {col: entry[_DB_FROM_RANK[col]] for col in columns}


_FAST_DB = ("ofi", "microprice", "spread", "rank", "volume24h")
_SLOW_DB = ("pct_1h", "pct_24h", "pct_1w", "pct_1m", "volatility")


_VOLUME_DB = ("rank", "volume24h")
_BOOK_DB = tuple(c for c in _FAST_DB if c not in _VOLUME_DB)


def _matches(entry: dict[str, Any] | None, row: dict[str, Any], columns: tuple[str, ...]) -> bool:
    return entry is not None and _db_view(entry, columns) == {c: row[c] for c in columns}


def _judge_metrics_row(row: dict[str, Any], messages: list[dict[str, Any]], iid: str) -> str:
    """
    Return which message the row's board-state columns equal: the last at or before its `ts`
    (D-130: `ts` is stamped when the board is read), else the first after it (accounted:
    `board_changed_before_its_publish`). Its slow columns must reach a later message.
    """
    ts = row["ts"]
    at_or_before = _rank_at(messages, iid, lambda t: t <= ts)
    before = at_or_before[-1] if at_or_before else None
    after = _rank_at(messages, iid, lambda t: t > ts)
    assert any(_matches(e, row, _SLOW_DB) for e in after), f"{iid}: {row}"
    if _matches(before, row, _FAST_DB):
        return "at_or_before"
    first = after[0] if after else None
    if _matches(first, row, _FAST_DB):
        return "first_after"
    volume_only = _matches(before, row, _BOOK_DB) and _matches(first, row, _VOLUME_DB)
    assert volume_only, f"{iid}: {row}; last at/before: {before}; first after: {first}"
    return "first_after (volume refresh)"


def _message(updated_at: int, ofi: float, volume: float) -> dict[str, Any]:
    rank = {"instrument_id": "X", "ofi_5": ofi, "microprice": 1.0, "spread": 0.1, "rank": 1}
    rank |= {"volume24h": volume, "pct_1h": 0.0, "pct_24h": 0.0, "pct_1w": 0.0, "pct_1m": 0.0}
    return {"updated_at": updated_at, "ranks": [{**rank, "volatility": 0.0}]}


def _row(ts: int, message: dict[str, Any]) -> dict[str, Any]:
    return {"ts": ts, **{col: message["ranks"][0][_DB_FROM_RANK[col]] for col in _DB_FROM_RANK}}


def test_a_metrics_row_matching_only_a_later_publish_is_never_accounted() -> None:
    """Planted defect: the accounted row admits the first message after `ts`, never a later one."""
    messages = [_message(10, 1.0, 5.0), _message(20, 2.0, 5.0), _message(30, 3.0, 5.0)]
    assert _judge_metrics_row(_row(15, messages[0]), messages, "X") == "at_or_before"
    assert _judge_metrics_row(_row(15, messages[1]), messages, "X") == "first_after"
    with pytest.raises(AssertionError):
        _judge_metrics_row(_row(15, messages[2]), messages, "X")  # stamped two publishes late


@LIVE
def test_live_newest_metrics_rows_match_the_rankings_messages_around_them(bus: _Bus) -> None:
    since, messages = time.time_ns(), _rankings_messages(bus, 2)
    ids = sorted(r["instrument_id"] for r in messages[-1]["ranks"])
    deadline = time.monotonic() + _METRICS_WAIT_S
    while _new_metrics_row(ids[0], since) is None and time.monotonic() < deadline:
        messages += _rankings_messages(bus, 5)
    messages += _rankings_messages(bus, 10)
    rows = {iid: _new_metrics_row(iid, since) for iid in ids}
    assert all(rows.values()), f"no new metrics.db row within {_METRICS_WAIT_S} s: {rows}"
    matched = {iid: _judge_metrics_row(row, messages, iid) for iid, row in rows.items() if row}
    print(f"\nmetrics.db rows vs rankings:live: {matched}")
