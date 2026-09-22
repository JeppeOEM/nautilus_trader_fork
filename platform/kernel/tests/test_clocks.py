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
"""`kernel.clocks`: the catalog file-stem parse, the two clocks and the kernel-internal margins."""

from datetime import UTC
from datetime import datetime

import pytest

from kernel.clocks import MAX_TS_INIT_SKEW_NS
from kernel.clocks import NS_PER_DAY
from kernel.clocks import NS_PER_S
from kernel.clocks import READ_SPAN_MARGIN_NS
from kernel.clocks import CatalogFileSpan
from kernel.clocks import TwoClocks


def _ns(text: str, nanos: int) -> int:
    moment = datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    return int(moment.timestamp()) * NS_PER_S + nanos


def test_stem_parses_both_bounds() -> None:
    span = CatalogFileSpan.from_stem(
        "2026-06-30T17-17-34-103475440Z_2026-06-30T17-18-00-000000000Z"
    )
    assert span == CatalogFileSpan(
        _ns("2026-06-30 17:17:34", 103_475_440), _ns("2026-06-30 17:18:00", 0)
    )


def test_from_path_uses_the_stem() -> None:
    path = (
        "/c/data/trade_tick/X.BYBIT/"
        "2026-01-01T00-00-00-000000001Z_2026-01-01T00-00-01-000000000Z.parquet"
    )
    span = CatalogFileSpan.from_path(path)
    assert (span.start_ns % NS_PER_DAY, span.end_ns % NS_PER_DAY) == (1, NS_PER_S)


@pytest.mark.parametrize(
    "stem",
    [
        "",
        "not-a-catalog-file",
        "2026-06-30T17-17-34Z_2026-06-30T17-18-00-0Z",
        "a_b",
        "2026-06-30T17-18-00-000000000Z_2026-06-30T17-17-34-103475440Z",  # inverted
    ],
)
def test_a_stem_the_catalog_did_not_write_raises(stem: str) -> None:
    with pytest.raises(ValueError):
        CatalogFileSpan.from_stem(stem)


def test_an_equal_bound_stem_is_a_span_of_one_instant() -> None:
    stem = "2026-06-30T17-17-34-103475440Z_2026-06-30T17-17-34-103475440Z"
    span = CatalogFileSpan.from_stem(stem)
    assert span.start_ns == span.end_ns


def test_covers_and_overlaps() -> None:
    span = CatalogFileSpan(100 * NS_PER_S, 200 * NS_PER_S)
    assert span.covers(100 * NS_PER_S - MAX_TS_INIT_SKEW_NS)
    assert not span.covers(100 * NS_PER_S - MAX_TS_INIT_SKEW_NS - 1)
    assert span.covers(250 * NS_PER_S, margin_ns=50 * NS_PER_S)
    assert not span.covers(250 * NS_PER_S + 1, margin_ns=50 * NS_PER_S)
    assert span.overlaps(200 * NS_PER_S, 300 * NS_PER_S)
    assert not span.overlaps(200 * NS_PER_S + 1, 300 * NS_PER_S)
    assert span.overlaps(201 * NS_PER_S, 300 * NS_PER_S, margin_ns=NS_PER_S)
    assert not span.overlaps(0, 100 * NS_PER_S - 2, margin_ns=1)


def test_two_clocks_skew() -> None:
    assert TwoClocks(ts_event=10, ts_init=15).skew_ns == 5
    assert TwoClocks(ts_event=0, ts_init=MAX_TS_INIT_SKEW_NS).within_skew()
    assert not TwoClocks(ts_event=0, ts_init=MAX_TS_INIT_SKEW_NS + 1).within_skew()
    assert TwoClocks(ts_event=5, ts_init=0).within_skew()  # venue clock ahead of ours


def test_the_skew_bound_and_the_read_margin() -> None:
    """
    The kernel half of AD-D3's coupling rule; capture's and archive's constants are asserted in
    `platform/tests/test_skew_constants.py` (the kernel may not import them).
    """
    assert MAX_TS_INIT_SKEW_NS == 300 * NS_PER_S
    assert 0 < READ_SPAN_MARGIN_NS <= MAX_TS_INIT_SKEW_NS


def test_a_stamp_whose_fractional_field_is_not_nanoseconds_is_refused() -> None:
    """A shorter fractional field would parse to a silently wrong instant, not an error."""
    for stamp in ("2026-06-30T17-17-34-103475Z", "2026-06-30T17-17-34-1034754400Z"):
        with pytest.raises(ValueError, match="not a catalog file stamp"):
            CatalogFileSpan.from_stem(f"{stamp}_{stamp}")
