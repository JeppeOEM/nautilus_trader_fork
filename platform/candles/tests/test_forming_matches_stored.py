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
The chart's forming bar and the stored closed bar are the same bar (Story 24.1 AC #2).

Story 21.2's "source equivalence" restated over the single fold: a day of recorded seconds is
rebuilt into a real store, and every closed bucket is *also* handed to `forming_bar` exactly as the
live bus hands it its in-progress buffer. Every `o/h/l/c/v` and every `partial` verdict must agree,
at 1 m, 5 m and 1 h -- if they ever could not, the chart's last candle would jump at bar close.
"""

import pytest

from candles.application import queries
from candles.application.forming import forming_bar
from candles.domain.candle import is_partial
from candles.infrastructure.sqlite_store import CandleStore
from candles.tests.test_candle_store import _DAY0_MS
from candles.tests.test_candle_store import _IID
from candles.tests.test_candle_store import _fixture


_BARS = (60, 300, 3600)


def _bucket_rows(rows: list, bar_seconds: int) -> dict[int, list]:
    """Group the fixture's seconds by bucket start (ms), as the live buffer accumulates them."""
    buckets: dict[int, list] = {}
    bar_ms = bar_seconds * 1000
    for row in sorted(rows, key=lambda r: r.ts_event):
        buckets.setdefault(row.ts_event // 1_000_000 // bar_ms * bar_ms, []).append(row)
    return buckets


def _rebuilt(store: CandleStore, rows: list) -> None:
    """Write the day through the rebuild CLI's path: one `fold_arrays` call for all of it."""
    store.rebuild(_IID, rows, _DAY0_MS, _DAY0_MS + 86_400_000)


def _flushed(store: CandleStore, rows: list) -> None:
    """
    Write the day through the collector's path: 30-second `SecondSink.apply` batches.

    Distinct from `_rebuilt` on purpose. Folding the day at once cannot exercise `_UPSERT`'s
    accumulation (`v = v + excluded.v`, `h = max(...)`, `seconds_observed = seconds_observed + ...`),
    which is what actually has to agree with a single fold for a live chart's bar not to jump -- a
    bucket wider than a flush is written by many statements and read as one bar.
    """
    ordered = sorted(rows, key=lambda r: r.ts_event)
    for start in range(0, len(ordered), 30):
        store.apply(_IID, ordered[start : start + 30])


@pytest.fixture(scope="module", params=[_rebuilt, _flushed], ids=["rebuilt", "flushed"])
def stored(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> tuple[CandleStore, list]:
    rows = _fixture()
    store = CandleStore(str(tmp_path_factory.mktemp("candles") / "c.db"))
    request.param(store, rows)
    return store, rows


@pytest.mark.parametrize("bar_seconds", _BARS)
def test_forming_bar_equals_the_stored_closed_bar(
    stored: tuple[CandleStore, list], bar_seconds: int
) -> None:
    store, rows = stored
    buckets = _bucket_rows(rows, bar_seconds)
    closed = queries.window(store.connection, _IID, bar_seconds, 1 << 62, 10_000)
    assert closed, "the fixture must produce traded buckets at this width"
    for bar in closed:
        live = forming_bar(buckets[bar["t"]], bar_seconds)
        assert live is not None, bar
        for key in ("t", "o", "h", "l", "c"):
            assert live[key] == bar[key], (bar_seconds, key, live, bar)
        assert live["v"] == pytest.approx(bar["v"], rel=1e-12), (bar_seconds, live, bar)


@pytest.mark.parametrize("bar_seconds", _BARS)
def test_the_partial_verdict_agrees_with_the_seconds_the_buffer_saw(
    stored: tuple[CandleStore, list], bar_seconds: int
) -> None:
    """`partial` is a coverage count, so the buffer's own row count must reach the same verdict."""
    store, rows = stored
    buckets = _bucket_rows(rows, bar_seconds)
    closed = queries.window(store.connection, _IID, bar_seconds, 1 << 62, 10_000)
    for bar in closed:
        observed = len(buckets[bar["t"]])
        assert bar["seconds_observed"] == observed, (bar_seconds, bar)
        assert bar["partial"] == is_partial(observed, bar_seconds), (bar_seconds, bar)


@pytest.mark.parametrize("bar_seconds", _BARS)
def test_a_bucket_the_store_skipped_is_one_no_row_traded_in(
    stored: tuple[CandleStore, list], bar_seconds: int
) -> None:
    """The other direction: every bucket without a stored bar folds to None, never to a bar."""
    store, rows = stored
    buckets = _bucket_rows(rows, bar_seconds)
    have = {
        bar["t"] for bar in queries.window(store.connection, _IID, bar_seconds, 1 << 62, 10_000)
    }
    missing = [t for t in buckets if t not in have]
    assert all(forming_bar(buckets[t], bar_seconds) is None for t in missing)
