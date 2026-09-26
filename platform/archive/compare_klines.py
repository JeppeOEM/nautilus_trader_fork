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
r"""
Reconcile a closed day's 1 m bars, bar for bar, against the venue's own klines (story 22.13, D-51).

Usage:
    python -m archive.compare_klines --venue BYBIT --day 2026-09-20 \\
        --catalog /app/catalog --db /app/candles_dir/candles_bybit.db \\
        --rebuilt-by RUN_ID --rebuilt IID [--rebuilt IID ...] [--not-rebuilt IID ...] \\
        [--instrument BTCUSDT-LINEAR.BYBIT ...] [--environment mainnet|testnet] \\
        [--kline-source venue|catalog]

The comparison (exact integer units, no tolerance; Bybit's seeded definition) is
`archive.application.reconcile_day`'s docstring. Theirs: the venue's 1 m klines, fetched over
`kernel.venue_http` and parsed from the venue's decimal strings with `Decimal(text).scaleb(p)` --
never through float (the pyo3 kline paths parse through `f64`, audit D-52).

`--rebuilt-by RUN_ID` is the rebuild proof: the id of the `rebuild_seconds --apply` run that
rebuilt this (venue, day), with one `--rebuilt IID` per instrument that run rebuilt and one
`--not-rebuilt IID` per instrument it refused. The nightly saga passes all three itself. Without
`--rebuilt-by` nothing is compared or written (`reconcile.not_rebuilt`, exit 1). With it, only
instruments named by `--rebuilt` and not by `--not-rebuilt` are compared (an allowlist); every
other instrument on the day is ledgered `reconcile.not_rebuilt` and gets no `verified_days` row
(exit 2). Known limit: standalone, `--rebuilt-by` is an operator attestation this tool cannot check
-- `rebuilt` is never persisted (AD-D9). Upgrade path: run the reconcile in-process in the saga
once MEM-01 allows it.

`--kline-source catalog` compares against story 22.9's backfilled `<iid>-1-MINUTE-LAST-EXTERNAL`
bars instead (close-stamped, so a bar opens at `ts_event - 60 s`). Those values went through the
adapters' `f64` parse (D-52), so it only reports and ledgers: it never writes `verified_days`
and so can never release trades to the prune.

Instruments: `--instrument`, else every id of `--venue` with snapshot or trade files on the day.
The venue symbol is the catalog instrument definition's `raw_symbol` (dYdX `BTC-USD`, Bybit
`BTCUSDT`, Hyperliquid `BTC`); Bybit's category comes from the id's `-LINEAR`/`-SPOT` suffix.

Per instrument-day: an upsert into `verified_days` (pass/fail + mismatch count), which gates trade
retention in `prune_catalog`. Prints a line per instrument and the venue's pass rate (instruments
and minutes). Exit 0 all passed, 2 findings (any mismatch, per-instrument error or not-rebuilt
instrument, all ledgered), 1 a run-level failure (bad arguments, an open day, a missing catalog or
candle store, no rebuild proof).
"""

import argparse
import logging
import time
from pathlib import Path

from candles.application.rebuild import parse_date_ns
from candles.infrastructure.sqlite_store import connect_ro
from candles.infrastructure.verified_days import VerifiedDaysStore
from kernel.venue_http import HttpJson
from kernel.venue_http import http_json
from kernel.venues import has_venue
from observability import error_ledger

from archive.application.catalog_check import catalog_missing
from archive.application.ports import VenueKlines
from archive.application.reconcile_day import day_text
from archive.application.reconcile_day import instruments_on_day
from archive.application.reconcile_day import run
from archive.domain.archive_day import RebuildProof
from archive.domain.reconciliation import DAY_MS
from archive.domain.reconciliation import VENUES
from archive.infrastructure.catalog_klines import CatalogKlines
from archive.infrastructure.klines_bybit import BybitKlines
from archive.infrastructure.klines_dydx import DydxKlines
from archive.infrastructure.klines_hyperliquid import HyperliquidKlines
from nautilus_trader.persistence.catalog import ParquetDataCatalog


logger = logging.getLogger(__name__)

_MS_NS = 1_000_000


def venue_klines(venue: str, environment: str, http: HttpJson = http_json) -> VenueKlines:
    """Return one venue's `VenueKlines` adapter (the composition root's one dispatch on it)."""
    if venue == "DYDX":
        return DydxKlines(environment, http)
    if venue == "BYBIT":
        return BybitKlines(environment, http)
    if venue == "HYPERLIQUID":
        return HyperliquidKlines(environment, http)
    raise ValueError(f"no kline adapter for venue {venue!r}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--venue", required=True, choices=VENUES)
    parser.add_argument("--day", required=True, help="YYYY-MM-DD (UTC), a closed day")
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--db", required=True, help="candles_<venue>.db")
    parser.add_argument("--instrument", action="append", help="repeatable; default: all on D")
    parser.add_argument("--environment", default="mainnet", choices=("mainnet", "testnet"))
    parser.add_argument("--kline-source", default="venue", choices=("venue", "catalog"))
    parser.add_argument(
        "--rebuilt-by", help="run id of the rebuild_seconds --apply run that rebuilt this day"
    )
    parser.add_argument(
        "--rebuilt",
        action="append",
        default=[],
        help="repeatable: an instrument that run rebuilt (only these are compared)",
    )
    parser.add_argument(
        "--not-rebuilt",
        action="append",
        default=[],
        help="repeatable: an instrument that run refused (never compared)",
    )
    return parser


def proof_for(
    rebuilt_by: str | None,
    venue: str,
    day_ms: int,
    rebuilt: list[str],
    not_rebuilt: list[str],
) -> RebuildProof | None:
    """
    Build the run's rebuild proof, keyed on the *parsed* day's canonical `YYYY-MM-DD` -- never
    the raw `--day` text (`2026-9-20` must prove, and record, `2026-09-20`).
    """
    if rebuilt_by is None:
        return None
    return RebuildProof(
        rebuilt_by, venue, day_text(day_ms), frozenset(rebuilt), frozenset(not_rebuilt)
    )


def _store_missing(args: argparse.Namespace) -> bool:
    if Path(args.db).exists():
        return False
    error_ledger.record("reconcile.error", f"candle store {args.db} missing")
    return True


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the exit code (0 pass, 2 findings, 1 run-level failure)."""
    parser = _parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    day_ms = parse_date_ns(args.day) // _MS_NS
    if day_ms + DAY_MS > time.time() * 1000:
        error_ledger.record("reconcile.error", f"{args.day} is not a closed UTC day")
        return 1
    if catalog_missing("compare_klines", args.catalog):
        return 1
    iids = args.instrument or instruments_on_day(args.catalog, args.venue, day_ms)
    wrong = [i for i in iids if not has_venue(i, args.venue)]
    if wrong:
        parser.error(f"--instrument ids not of venue {args.venue}: {wrong}")
    proof = proof_for(args.rebuilt_by, args.venue, day_ms, args.rebuilt, args.not_rebuilt)
    if proof is not None and _store_missing(args):
        return 1
    if args.kline_source == "catalog":
        logger.info("kline source catalog (D-52 f64 bars): report only, verified_days untouched")
        klines: VenueKlines = CatalogKlines(ParquetDataCatalog(args.catalog))
    else:
        klines = venue_klines(args.venue, args.environment)
    verified = VerifiedDaysStore(args.db) if args.kline_source == "venue" else None
    return run(
        lambda: connect_ro(args.db), args.catalog, args.venue, day_ms, iids, klines, verified, proof
    )


if __name__ == "__main__":
    raise SystemExit(main())
