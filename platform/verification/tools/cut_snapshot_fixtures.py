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
Cuts the derived-signal tests' real fixtures from a soak catalog (Story 31.3):

    python3 -m verification.tools.cut_snapshot_fixtures --catalog data/catalog \
        --out verification/tests/fixtures/snapshots

For each instrument it reads the stored `DydxSecondSnapshot` rows with pyarrow (raw Parquet,
never `ParquetDataCatalog` or `kernel.second_snapshot`: the reference side never imports the code
it checks), keeps `--rows` consecutive stored rows starting at the first row at or after
`--skip-seconds` past the instrument's first stored second (the collector's start-up is skipped),
and writes them to `<out>/<iid>.jsonl.gz` -- one JSON object per row holding the stored integers
exactly as written (gap-encoded book prices, units, both precisions). `<out>/README.md` gets one
line per instrument: the source window and the *cutter checkout revision* (the checkout this tool
ran from, flagged `-dirty` when its tree had uncommitted changes). That is not the revision the
soak's collectors ran: `docs/VERIFICATION_REPORT.md` records the soak's collector revision.

Read-only on the catalog. Never fabricates a fixture: an instrument with fewer than `--rows`
stored rows in its window exits non-zero and nothing under `--out` is written.
"""

import argparse
import gzip
import json
import subprocess
from pathlib import Path

from verification.infrastructure.catalog_reader import SNAPSHOT_DIR
from verification.infrastructure.catalog_reader import first_snapshot_ts
from verification.infrastructure.catalog_reader import read_snapshot_rows


# The five soak instruments of Epic 31 (Bybit linear + spot, Hyperliquid).
SOAK_INSTRUMENTS = (
    "BTCUSDT-LINEAR.BYBIT",
    "BTCUSDT-SPOT.BYBIT",
    "ETHUSDT-LINEAR.BYBIT",
    "ETHUSDT-SPOT.BYBIT",
    "SOL-USD-PERP.HYPERLIQUID",
)
NS_PER_S = 1_000_000_000
# How far past the first kept row the read reaches: rows come ~1/s, so 4x covers gaps.
_READ_SPAN_FACTOR = 4


def cut(catalog: Path, instrument_id: str, rows: int, skip_seconds: int) -> list[dict[str, object]]:
    """Return `rows` consecutive stored rows from `skip_seconds` past the first stored second."""
    first = first_snapshot_ts(catalog, instrument_id)
    if first is None:
        raise SystemExit(f"{instrument_id}: no {SNAPSHOT_DIR} rows under {catalog}")
    start = first + skip_seconds * NS_PER_S
    end = start + rows * _READ_SPAN_FACTOR * NS_PER_S
    kept = read_snapshot_rows(catalog, instrument_id, (start, end))[:rows]
    if len(kept) < rows:
        raise SystemExit(f"{instrument_id}: only {len(kept)} rows in [{start}, {end}), need {rows}")
    return kept


def write_fixture(path: Path, rows: list[dict[str, object]]) -> None:
    """One JSON object per line, gzip with a zero mtime so a re-cut of the same rows is identical."""
    text = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    with path.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as out:
        out.write(text.encode("utf-8"))


def _git(*args: str) -> str:
    found = subprocess.run(  # noqa: S603 -- fixed git arguments, no user input
        ["git", *args],  # noqa: S607
        # The checkout this tool lives in, whatever the caller's cwd.
        cwd=Path(__file__).resolve().parent,
        capture_output=True,
        text=True,
        check=False,
    )
    return found.stdout.strip()


def _revision() -> str:
    """Return the cutter checkout's revision, `-dirty` when its tree has uncommitted changes."""
    revision = _git("rev-parse", "--short=10", "HEAD") or "unknown"
    return f"{revision}-dirty" if _git("status", "--porcelain") else revision


def readme_line(instrument_id: str, rows: list[dict[str, object]], revision: str) -> str:
    first, last = rows[0]["ts_event"], rows[-1]["ts_event"]
    return (
        f"- `{instrument_id}.jsonl.gz`: {len(rows)} stored rows, ts_event {first}..{last}, "
        f"cutter checkout revision {revision} (`python3 -m verification.tools."
        f"cut_snapshot_fixtures`; the soak's collector revision: `docs/VERIFICATION_REPORT.md`)\n"
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python3 -m verification.tools.cut_snapshot_fixtures")
    parser.add_argument("--catalog", type=Path, default=Path("data/catalog"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--instrument", action="append", dest="instruments")
    parser.add_argument("--rows", type=int, default=300)
    parser.add_argument("--skip-seconds", type=int, default=120)
    args = parser.parse_args(argv)
    if args.rows < 1:
        parser.error(f"--rows must be at least 1, got {args.rows}")
    instruments = tuple(args.instruments or SOAK_INSTRUMENTS)
    cuts = {iid: cut(args.catalog, iid, args.rows, args.skip_seconds) for iid in instruments}
    args.out.mkdir(parents=True, exist_ok=True)
    revision = _revision()
    lines = ["# Real snapshot fixtures (Story 31.3)\n", "\n"]
    for iid, rows in cuts.items():
        write_fixture(args.out / f"{iid}.jsonl.gz", rows)
        lines.append(readme_line(iid, rows, revision))
    (args.out / "README.md").write_text("".join(lines), encoding="utf-8")
    print(json.dumps({iid: len(rows) for iid, rows in cuts.items()}))


if __name__ == "__main__":
    main()
