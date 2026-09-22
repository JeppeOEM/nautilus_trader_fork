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
Persistent markers for spans where the raw trade archive is known to miss trades the live fold
used (story 22.13).

`rebuild_seconds` replaces a covered row's trade columns with the fold of the archived trades.
Where the archive lost trades that the live second still holds, that would zero correct values.
The ways it can happen, each recorded here:

- `write_failed`: a trade batch's `write_data` failed while the same instrument's snapshot rows
  landed (the collector's flush);
- `quarantined`: `quarantine_corrupt_parquet` moved an unreadable trade file aside;
- `pruned`: `prune_catalog` deleted a verified trade file, but an older unverified day's files
  remain, so `covered_from` still reaches back past it.

A row whose `ts_event` falls in any marker span is `not covered` and keeps its live values. One
JSON line per gap in `<catalog>/_archive_gaps/<iid>.jsonl`: `instrument_id`, `from_ns`, `to_ns`
(inclusive), `reason`, `count`. Only one collector writes a given instrument's file (the same
single-writer rule as its catalog leaves), and the maintenance tools write it under the
maintenance lock. A marker that cannot be written is ledgered (`archive_gaps.write`).
"""

import json
import os
from pathlib import Path

from observability import error_ledger


GAPS_DIRNAME = "_archive_gaps"
# How long after a trade's `ts_event` it can still reach the archive (`ts_init`): the live age
# filter (`stale_trade_seconds`, 10 s) bounds it for live trades, and the reconnect trade backfill
# (story 22.14, `Collector._apply_backfill`) refuses -- and counts -- any unseen REST trade older
# than this, so the bound holds for backfilled trades too. Shared by the rebuild's `ts_init` query
# window, the quarantine marker and the prune gate's previous-day check.
ARRIVAL_MARGIN_NS = 300 * 1_000_000_000


def _gaps_path(catalog_path: str, iid: str) -> Path:
    return Path(catalog_path) / GAPS_DIRNAME / f"{iid}.jsonl"


def record_gap(
    catalog_path: str, iid: str, from_ns: int, to_ns: int, reason: str, count: int
) -> None:
    """Append one gap marker; a failure is ledgered, never raised (callers must carry on)."""
    line = json.dumps(
        {
            "instrument_id": iid,
            "from_ns": from_ns,
            "to_ns": to_ns,
            "reason": reason,
            "count": count,
        }
    )
    path = _gaps_path(catalog_path, iid)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError as e:
        error_ledger.record("archive_gaps.write", f"could not record archive gap {line}", e)


def load_gaps(catalog_path: str, iid: str) -> list[tuple[int, int]]:
    """
    Return the instrument's gap spans `(from_ns, to_ns)`, inclusive. A malformed line raises
    `ValueError`: the rebuild then refuses the instrument-day rather than guess.
    """
    path = _gaps_path(catalog_path, iid)
    if not path.exists():
        return []
    spans = []
    for number, text in enumerate(path.read_text().splitlines(), start=1):
        if not text.strip():
            continue
        try:
            entry = json.loads(text)
            spans.append((int(entry["from_ns"]), int(entry["to_ns"])))
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            raise ValueError(f"{path}:{number}: malformed archive-gap marker {text!r}") from e
    return spans


def in_gap(ts_ns: int, gaps: list[tuple[int, int]]) -> bool:
    return any(lo <= ts_ns <= hi for lo, hi in gaps)
