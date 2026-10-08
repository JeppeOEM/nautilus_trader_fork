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
Story 33.8: the adapters behind alerting's two input ports, and the executor seam its engine
submits blocking reads through. They live in the interface because they read `views` (the chart's
indicator replay and its drawings store), which `alerting` may not import (AD-D2);
`data_api.alert_wiring` builds them.

- `ChartIndicatorReader` (`IndicatorReader`): an indicator's values at one closed bar, read through
  `views.chart_series.indicator_values_page` -- the chart's own `candle_page`, with the live bus's
  unflushed `recent_rows`/`recent_liquidations` -- so an alert compares exactly the value the
  indicator pane draws (SSOT-02).
- `DrawingFileReader` (`DrawingReader`): a saved trendline from `chart_drawings.toml` through
  `views.preferences.load_chart_drawings`, cached by the file's `(st_ino, mtime_ns, size)`.
- `executor_submit` (`Submit`): runs a job in the loop's default executor and hands its result back
  to the loop.
"""

import asyncio
import logging
import threading
from collections.abc import Callable
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from alerting.application.ports import Anchors
from alerting.application.ports import Failed
from alerting.application.ports import IndicatorReading
from alerting.application.ports import IndicatorRef
from alerting.application.ports import IndicatorResult
from alerting.application.ports import Missing
from observability import error_ledger
from views import chart_series
from views import indicator_picker
from views import preferences
from views.chart_series import RecentLiquidations
from views.chart_series import RecentRows


logger = logging.getLogger(__name__)

# The bars one read replays up to the closed bar: enough history for every indicator's warm-up at
# the picker's usual periods (the chart pane's own first page is this order of size).
# Known limit: an indicator whose warm-up is longer than 300 bars (an EMA(500), a 1000-bar window)
# reads None at the closed bar, so its alert never samples. Upgrade path: a per-indicator warm-up
# length on the catalog entry, read here as the page size.
INDICATOR_READ_BARS = 300

INPUT_SITE = "alerting.engine.input"
_NS_PER_MS = 1_000_000


def _series_values(row: dict[str, Any], prefix: str) -> dict[str, float | None]:
    """
    Return one series' `{output: value}` from a values-page row (`{id}.{output}` keys). An output
    name holds no dot (every catalog output is a plain name), so a key whose rest after `prefix`
    does is another series whose id merely starts the same way: `DepthWithinBps_bps=10.` is a
    prefix of `DepthWithinBps_bps=10.5.bid` too.
    """
    return {
        key[len(prefix) :]: value
        for key, value in row.get("values", {}).items()
        if key.startswith(prefix) and "." not in key[len(prefix) :]
    }


class ChartIndicatorReader:
    """
    `IndicatorReader` over the chart's indicator values page.

    Invariant (one indicator computation, SSOT-02): every value comes from `indicator_values_page`,
    which replays through `indicator_picker.values_by_time`/`replay_entry` over `candle_page` --
    the indicator pane's own path -- never a second replay. The newest row of the page must be the
    closed bar asked for, else there is no reading (`Failed`: that bar is not readable yet); a name
    no longer in `merged_catalog()` is `Missing`, and an output the replay did not produce is left
    for the engine to judge against the reading's `outputs`.
    """

    def __init__(
        self,
        *,
        catalog_path: Callable[[], str],
        candles_dir: Callable[[], str],
        recent_rows: RecentRows,
        recent_liquidations: RecentLiquidations,
    ) -> None:
        # Paths are read per call so a test's or an operator's override of the settings is honoured.
        self._catalog_path = catalog_path
        self._candles_dir = candles_dir
        self._recent_rows = recent_rows
        self._recent_liquidations = recent_liquidations

    def read(
        self,
        instrument_id: str,
        bar_seconds: int,
        closed_t_ms: int,
        refs: Sequence[IndicatorRef],
    ) -> dict[IndicatorRef, IndicatorResult]:
        catalog = indicator_picker.merged_catalog()
        results: dict[IndicatorRef, IndicatorResult] = {
            ref: Missing(f"indicator {ref.name} is no longer in the catalog")
            for ref in refs
            if ref.name not in catalog
        }
        listed = [ref for ref in refs if ref not in results]
        if listed:
            results.update(self._page(instrument_id, bar_seconds, closed_t_ms, listed))
        return results

    def _page(
        self, instrument_id: str, bar_seconds: int, closed_t_ms: int, refs: list[IndicatorRef]
    ) -> dict[IndicatorRef, IndicatorResult]:
        before_ns = (closed_t_ms + bar_seconds * 1000) * _NS_PER_MS
        rows, _has_more, errors = chart_series.indicator_values_page(
            instrument_id,
            before_ns,
            INDICATOR_READ_BARS,
            bar_seconds,
            refs,
            catalog_path=self._catalog_path(),
            candles_dir=self._candles_dir(),
            recent_rows=self._recent_rows,
            recent_liquidations=self._recent_liquidations,
        )
        bars = [row for row in rows if "values" in row]  # gap rows carry only `t`
        if not bars or bars[-1]["t"] != closed_t_ms:
            newest = bars[-1]["t"] if bars else None
            failed = Failed(f"the page's newest bar is t={newest}, not the closed bar")
            return dict.fromkeys(refs, failed)
        prev_row = bars[-2] if len(bars) > 1 else {}
        return {ref: self._reading(ref, bars[-1], prev_row, errors) for ref in refs}

    @staticmethod
    def _reading(
        ref: IndicatorRef, cur_row: dict, prev_row: dict, errors: dict[str, str]
    ) -> IndicatorResult:
        series_id = indicator_picker.indicator_id(ref.name, ref.params, ref.source)
        if series_id in errors:
            return Failed(errors[series_id])
        prefix = f"{series_id}."
        cur = _series_values(cur_row, prefix)
        return IndicatorReading(
            prev=_series_values(prev_row, prefix), cur=cur, outputs=frozenset(cur)
        )


class DrawingFileReader:
    """
    `DrawingReader` over `chart_drawings.toml`.

    Invariant: the anchors returned are the stored item's own, read through
    `preferences.load_chart_drawings` (the drawings route's one loader and validator), and the
    parsed file is reused only while its `(st_ino, mtime_ns, size)` is unchanged -- a save on the
    chart is seen by the next sample. The inode is part of the key because the drawings route saves
    by atomic replace (a new file each time): two saves within one mtime tick that happen to leave
    the same size would otherwise serve the older anchors. A corrupt file raises (the engine ledgers
    it as a read failure, never an invalidation); an absent file, an absent id or another kind is
    `Missing`.
    """

    def __init__(self, path: Callable[[], Path]) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._cached: tuple[Path, int, int, int, dict[str, list[dict[str, Any]]]] | None = None

    def _load(self) -> dict[str, list[dict[str, Any]]]:
        path = self._path()
        try:
            stat = path.stat()
        except FileNotFoundError:
            return {}
        with self._lock:
            cached = self._cached
            key = (path, stat.st_ino, stat.st_mtime_ns, stat.st_size)
            if cached is not None and cached[:4] == key:
                return cached[4]
            drawings = preferences.load_chart_drawings(path)
            self._cached = (*key, drawings)
            return drawings

    def trendline(self, instrument_id: str, drawing_id: str) -> Anchors | Missing:
        item = next((d for d in self._load().get(instrument_id, []) if d["id"] == drawing_id), None)
        if item is None:
            return Missing(f"drawing {drawing_id} no longer exists")
        if item["kind"] != "trendline":
            return Missing(f"drawing {drawing_id} is a {item['kind']}, not a trendline")
        anchors: Anchors = item["anchors"]
        return anchors


def executor_submit(job: Callable[[], Any], done: Callable[[Any], None]) -> None:
    """
    Run `job` in the running loop's default executor and `done(result)` back on the loop
    (`call_soon_threadsafe`). Called on the event loop (the bus observers run there). A `job` that
    raises hands its exception to `done` as the result, so `done` runs for every submitted job (the
    engine's in-flight flag is always cleared); a `done` that raises is ledgered at
    `alerting.engine.input`, never lost in the loop's exception handler.
    """
    loop = asyncio.get_running_loop()

    def finish(result: Any) -> None:
        try:
            done(result)
        except Exception as exc:
            error_ledger.record(INPUT_SITE, "an alert input's result could not be applied", exc)

    def run() -> None:
        try:
            result = job()
        except Exception as exc:  # a job reports its own failures; this is the last resort
            result = exc
        try:
            loop.call_soon_threadsafe(finish, result)
        except RuntimeError:
            # The loop closed while the job ran: the app is shutting down and nothing is left to
            # evaluate the result on; the next start reads the next closed bar afresh.
            logger.info("alert input result dropped: the event loop closed (shutdown)")

    loop.run_in_executor(None, run)
