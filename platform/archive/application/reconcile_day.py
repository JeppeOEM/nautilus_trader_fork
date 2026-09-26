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
Reconcile a closed day's 1 m bars, bar for bar, against the venue's own klines (story 22.13, D-51).

Ours: `candles.application.queries.window(db, iid, 60, ...)` -- the bars `python -m candles.rebuild
--day D` folded from the rebuilt seconds, read over a read-only connection the composition root
opens. Theirs: the `VenueKlines` port (`archive.infrastructure.klines_<venue>`). The comparison is
`archive.domain.reconciliation.compare`: exact integer units, no tolerance.

Bybit defines a kline differently: it is seeded with the previous kline's close, so its `open` is
that close (not the minute's first trade) and its `high`/`low` include it. Wire evidence, BTCUSDT
2026-09-20 (fetched 2026-09-21): open == previous close in 999/999 minutes, linear and spot alike;
the open never lies outside [low, high] and equals high or low in 17-24 % of minutes. Hyperliquid
(782/1439) and dYdX (32/506 traded minutes) show only natural ties: their open is the first trade.
So for Bybit our bars are put in Bybit's definition before comparing (`seed_with_previous_close`:
open = our previous traded minute's close, high/low widened to include it) -- an exact
transformation of our own integers, not a tolerance. A day whose previous close we lack (the first
collected day) keeps our first-trade open for its first minute, and mismatches honestly. The day's
first minute is seeded from the store's last traded close before midnight, which is provisional
if the previous day has not been rebuilt yet. A seed also chains: one missing or wrong minute
shifts the *next* traded minute's seed, so mismatches often come in pairs and the second is a
consequence of the first -- its ledger message says so (`seed from a mismatched minute`).

The rebuild proof (AD-D9): a reconcile judges only what the same saga run rebuilt. Without a
`RebuildProof` nothing is compared or written (`reconcile.not_rebuilt`, exit 1); an instrument the
proof does not cover (the rebuild refused it, or it is not among the instruments the rebuild
names as rebuilt -- the proof is an allowlist) is ledgered `reconcile.not_rebuilt` and written no
verdict (a finding, exit 2). Otherwise the `ArchiveDay` transitions (`rebuilt`, then `reconciled`)
decide what `VerifiedDays.mark_verified` records: `pass` or `fail`.

Per mismatch: `error_ledger.record("reconcile.kline_mismatch", "{iid} {minute} vol {ours}/{theirs}
ohlc {ours}/{theirs}")`, `-` for a missing side. A per-instrument error -- a fetch error, a missing
instrument definition, a value not representable at the instrument's precision, a venue with no
history for the day (no traded kline while we have traded bars, or a Hyperliquid `candleSnapshot`
starting after our first traded minute: outside its retention, D-53) -- is `reconcile.error` and
writes no `verified_days` row (the day stays unverified).
"""

import http.client
import logging
import sqlite3
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC
from datetime import datetime
from pathlib import Path

from candles.application import queries
from candles.application.verified_days import VerifiedDays
from kernel.catalog_files import SNAPSHOT_DIRNAME
from kernel.clocks import CatalogFileSpan
from kernel.venues import has_venue
from observability import error_ledger

from archive.application.ports import VenueKlines
from archive.domain.archive_day import ArchiveDay
from archive.domain.archive_day import IllegalTransition
from archive.domain.archive_day import RebuildProof
from archive.domain.reconciliation import DAY_MS
from archive.domain.reconciliation import Kline
from archive.domain.reconciliation import KlineError
from archive.domain.reconciliation import ReconciliationResult
from archive.domain.reconciliation import compare
from archive.domain.reconciliation import float_units
from archive.domain.reconciliation import seed_with_previous_close
from archive.domain.reconciliation import traded_in_day
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.persistence.catalog import ParquetDataCatalog


logger = logging.getLogger(__name__)

_MS_NS = 1_000_000

# Opens the candle store read-only for one instrument; yields None when the store does not exist.
OpenStore = Callable[[], AbstractContextManager[sqlite3.Connection | None]]


def day_text(day_ms: int) -> str:
    return datetime.fromtimestamp(day_ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def our_klines(
    db: sqlite3.Connection, iid: str, day_ms: int, price_p: int, size_p: int
) -> list[Kline]:
    """Return the candle store's traded 1 m bars of the day, as exact integer units."""
    return traded_in_day(
        [
            Kline(
                b["t"],
                float_units(b["o"], price_p, "open"),
                float_units(b["h"], price_p, "high"),
                float_units(b["l"], price_p, "low"),
                float_units(b["c"], price_p, "close"),
                float_units(b["v"], size_p, "volume"),
            )
            for b in queries.window(db, iid, 60, day_ms + DAY_MS, 1440)
        ],
        day_ms,
    )


def _close_before(db: sqlite3.Connection, iid: str, day_ms: int, price_p: int) -> int | None:
    """Our last traded 1 m close before the day (None when the store has none)."""
    before = queries.window(db, iid, 60, day_ms, 1)
    return float_units(before[0]["c"], price_p, "previous close") if before else None


def _ours_in_venue_definition(
    db: sqlite3.Connection, iid: str, day_ms: int, inst: Instrument
) -> list[Kline]:
    ours = our_klines(db, iid, day_ms, inst.price_precision, inst.size_precision)
    if not has_venue(iid, "BYBIT"):
        return ours
    return seed_with_previous_close(ours, _close_before(db, iid, day_ms, inst.price_precision))


def _load_instrument(catalog: ParquetDataCatalog, iid: str) -> Instrument:
    found = catalog.instruments(instrument_ids=[iid])
    if not found:
        raise KlineError(f"{iid}: no instrument definition in the catalog")
    return found[0]


def _check_venue_history(iid: str, ours: list[Kline], theirs: list[Kline]) -> None:
    """
    Refuse (as a per-instrument error, not a verdict) a day the venue cannot serve: no traded
    kline at all while we traded, or a Hyperliquid `candleSnapshot` that starts after our first
    traded minute -- the day lies (partly) outside its ~5000-candle retention (D-53).
    """
    if ours and not theirs:
        raise KlineError(
            f"venue has no history for this day/range: no traded kline, ours has {len(ours)}"
        )
    if has_venue(iid, "HYPERLIQUID") and ours and theirs and theirs[0].t_ms > ours[0].t_ms:
        raise KlineError(
            "venue has no history for this day/range: candleSnapshot starts at "
            f"{theirs[0].t_ms}, after our first traded minute {ours[0].t_ms} (retention, D-53)"
        )


def _compared(
    open_store: OpenStore, catalog: ParquetDataCatalog, iid: str, day_ms: int, klines: VenueKlines
) -> ReconciliationResult:
    inst = _load_instrument(catalog, iid)
    theirs = traded_in_day(klines.fetch(inst, day_ms), day_ms)
    with open_store() as ro:
        if ro is None:
            raise KlineError("candle store does not exist")
        ours = _ours_in_venue_definition(ro, iid, day_ms, inst)
    _check_venue_history(iid, ours, theirs)
    return compare(iid, ours, theirs, inst, seeded=has_venue(iid, "BYBIT"))


def _record_verdict(
    verified_days: VerifiedDays, proof: RebuildProof, iid: str, result: ReconciliationResult
) -> None:
    """Walk the day's state machine and persist where it lands (`pass`/`fail`)."""
    status = verified_days.verified_status(iid, proof.day)
    day = ArchiveDay.from_verified_status(proof.venue, iid, proof.day, status)
    judged = day.rebuilt(proof).reconciled(result)
    verified_days.mark_verified(
        iid, proof.day, judged.verified_status(), len(result.mismatches), int(time.time() * 1000)
    )


def reconcile_instrument(
    open_store: OpenStore,
    catalog: ParquetDataCatalog,
    iid: str,
    day_ms: int,
    klines: VenueKlines,
    verified_days: VerifiedDays | None,
    proof: RebuildProof,
) -> ReconciliationResult:
    """
    Compare one instrument-day, ledger every mismatch and (unless `verified_days` is None, as for
    the f64 catalog klines) record the verdict. Any per-instrument failure is an "error" result;
    an instrument outside `proof` is a "not_rebuilt" one, compared and recorded not at all.

    The verdict goes through the `VerifiedDays` port, never a connection of this tool's own: the
    candle store owns day status (AD-D9), and this tool only reads its bars.
    """
    day = day_text(day_ms)
    if not proof.covers(iid):
        why = "refused it" if iid in proof.not_rebuilt else "did not rebuild it"
        error_ledger.record(
            "reconcile.not_rebuilt",
            f"{iid} {day}: run {proof.run_id}'s rebuild {why}; not compared, stays unverified",
        )
        return ReconciliationResult(iid, "not_rebuilt")
    try:
        result = _compared(open_store, catalog, iid, day_ms, klines)
        for message in result.mismatches:
            error_ledger.record("reconcile.kline_mismatch", message)
        if verified_days is not None:
            _record_verdict(verified_days, proof, iid, result)
    except (  # urllib's errors are OSErrors; sqlite3 from `mark_verified` on a locked store
        KlineError,
        IllegalTransition,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        http.client.HTTPException,
        sqlite3.OperationalError,
    ) as e:
        error_ledger.record("reconcile.error", f"{iid} {day}: {e!r}; stays unverified", exc=e)
        return ReconciliationResult(iid, "error")
    return result


def _overlaps_day(path: Path, lo: int, hi: int) -> bool:
    """
    Whether the file's name span meets the day; a name the catalog did not write is ledgered and
    skipped (the catalog never reads it as data), never allowed to abort the whole compare.
    """
    try:
        return CatalogFileSpan.from_path(path).overlaps(lo, hi)
    except ValueError as e:
        error_ledger.record("reconcile.error", f"{path}: not a catalog file name ({e}); skipped")
        return False


def instruments_on_day(catalog_path: str, venue: str, day_ms: int) -> list[str]:
    """Ids of `venue` with a snapshot or trade file overlapping the day (file names only)."""
    lo, hi = day_ms * _MS_NS, (day_ms + DAY_MS) * _MS_NS - 1
    found: set[str] = set()
    for data_type in (SNAPSHOT_DIRNAME, "trade_tick"):
        root = Path(catalog_path) / "data" / data_type
        leaves = (
            [d for d in root.iterdir() if d.is_dir() and has_venue(d.name, venue)]
            if root.is_dir()
            else []
        )
        for leaf in leaves:
            if any(_overlaps_day(p, lo, hi) for p in sorted(leaf.glob("*.parquet"))):
                found.add(leaf.name)
    return sorted(found)


def summary_line(venue: str, day: str, results: list[ReconciliationResult]) -> str:
    """Format the venue's pass rate over instruments and over minutes (errors counted apart)."""
    judged = [r for r in results if r.status in ("pass", "fail")]
    passed = sum(r.status == "pass" for r in judged)
    minutes = sum(r.minutes for r in judged)
    matched = minutes - sum(len(r.mismatches) for r in judged)
    not_rebuilt = sum(r.status == "not_rebuilt" for r in results)

    def pct(a: int, b: int) -> str:
        return f"{100 * a / b:.1f}%" if b else "n/a"

    return (
        f"compare_klines {venue} {day}: instruments pass {passed}/{len(judged)} "
        f"({pct(passed, len(judged))}), minutes matched {matched}/{minutes} "
        f"({pct(matched, minutes)}), errors {len(results) - len(judged) - not_rebuilt}"
        + (f", not rebuilt {not_rebuilt}" if not_rebuilt else "")
    )


def _proof_refusal(proof: RebuildProof | None, venue: str, day: str) -> str | None:
    """Why `proof` cannot license judging `venue`'s `day`, or None when it can."""
    if proof is None:
        return "no rebuild proof (--rebuilt-by)"
    if proof.day != day or proof.venue != venue:
        return f"the rebuild proof is for {proof.venue} {proof.day}, not this venue-day"
    return None


def run(
    open_store: OpenStore,
    catalog_path: str,
    venue: str,
    day_ms: int,
    iids: list[str],
    klines: VenueKlines,
    verified_days: VerifiedDays | None,
    proof: RebuildProof | None,
) -> int:
    """
    Reconcile every instrument; returns the exit code: 0 all passed, 2 findings (mismatches,
    per-instrument errors or instruments the rebuild refused), 1 when there is no rebuild proof
    (nothing compared or written).

    `verified_days` is the day-status port the verdicts go to; None reports without recording
    (the f64 catalog-kline source).
    """
    day = day_text(day_ms)
    refusal = _proof_refusal(proof, venue, day)
    if refusal is not None or proof is None:
        error_ledger.record(
            "reconcile.not_rebuilt", f"{venue} {day}: {refusal}; nothing compared or written"
        )
        return 1
    catalog = ParquetDataCatalog(catalog_path)
    results = []
    for iid in iids:
        result = reconcile_instrument(
            open_store, catalog, iid, day_ms, klines, verified_days, proof
        )
        results.append(result)
        logger.info(
            "%s %s: %s -- minutes %d, mismatched %d",
            iid,
            day,
            result.status,
            result.minutes,
            len(result.mismatches),
        )
    logger.info("%s", summary_line(venue, day, results))
    return 2 if any(r.status != "pass" for r in results) else 0
