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
Every skew-related margin is tied to the one bound, `kernel.clocks.MAX_TS_INIT_SKEW_NS` (spine
AD-D3, AD-D7, AD-D18; Story 23.2).

`MAX_TS_INIT_SKEW_NS` couples capture's carry/backfill rules with archive's rebuild/prune
windows (adversary review C1/H1). The kernel cannot import its consumers (AD-D2), so the consumer
half of the rule lives here, in the cross-cutting guards; `kernel/tests/test_clocks.py` holds the
kernel half. Skipped, with the reason shown, in an image without the collector (`live_paper`).
"""

import ast
import importlib.util
import inspect
import textwrap
import tomllib
from collections.abc import Callable

import pytest
from _source_tree import PLATFORM_DIR


if importlib.util.find_spec("collector_core") is None:
    pytest.skip(
        "collector_core is not shipped in this image; run by `make test`", allow_module_level=True
    )

from collector_core import collector
from collector_core import rebuild_seconds
from kernel.clocks import MAX_TS_INIT_SKEW_NS
from kernel.clocks import NS_PER_S
from kernel.clocks import READ_SPAN_MARGIN_NS


_VENUE_CONFIGS = ("dydx_collector", "bybit_collector", "hyperliquid_collector")


def _hold_back_seconds(node: object) -> list[float]:
    # A list is walked too: `tomllib` parses an array-of-tables (`[[instruments]]`) into a list of
    # dicts, so a dict-only walk would skip such a value and pass this guard vacuously.
    if isinstance(node, dict):
        found = [float(v) for k, v in node.items() if k == "hold_back_seconds"]
        return found + [x for v in node.values() for x in _hold_back_seconds(v)]
    if isinstance(node, list):
        return [x for v in node for x in _hold_back_seconds(v)]
    return []


def _max_hold_back_ns() -> int:
    values = [0.0]
    for package in _VENUE_CONFIGS:
        with (PLATFORM_DIR / package / "config.toml").open("rb") as f:
            values += _hold_back_seconds(tomllib.load(f))
    return int(max(values) * NS_PER_S)


def test_the_rebuild_window_is_the_bound() -> None:
    assert rebuild_seconds._TS_INIT_MARGIN_NS == MAX_TS_INIT_SKEW_NS


def test_the_read_margin_is_within_the_bound() -> None:
    assert READ_SPAN_MARGIN_NS <= MAX_TS_INIT_SKEW_NS


def test_catch_up_and_hold_back_stay_within_the_bound() -> None:
    catch_up_ns = collector._MAX_CATCH_UP_SECONDS * NS_PER_S
    assert catch_up_ns <= MAX_TS_INIT_SKEW_NS
    assert _max_hold_back_ns() + collector._VENUE_AHEAD_NS <= MAX_TS_INIT_SKEW_NS


def test_a_caught_up_row_stays_within_the_read_margin() -> None:
    """
    The tighter chain `collector._MAX_CATCH_UP_SECONDS` documents: a caught-up venue-timed row's
    `ts_init` trails its `ts_event` by up to catch-up + 1 s + hold-back, and a venue clock may run
    hold-back + `_VENUE_AHEAD_NS` ahead; the readers widen file spans symmetrically by
    `READ_SPAN_MARGIN_NS`, so each direction alone is the binding limit and their sum is a
    conservative ceiling on both (`Collector._check_skew_budget` enforces the same sum).
    """
    worst = (
        (collector._MAX_CATCH_UP_SECONDS + 1) * NS_PER_S
        + _max_hold_back_ns()
        + collector._VENUE_AHEAD_NS
    )
    assert worst <= READ_SPAN_MARGIN_NS


def _names_in(function: Callable[..., object]) -> set[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


def test_the_backfill_refusal_and_fetch_floor_use_the_bound() -> None:
    """Both halves of the backfill rule read the one constant, never a second literal."""
    assert "MAX_TS_INIT_SKEW_NS" in _names_in(collector.Collector._apply_backfill)
    assert "MAX_TS_INIT_SKEW_NS" in _names_in(collector.Collector._backfill_instrument)


def test_the_collector_refuses_a_hold_back_beyond_the_read_margin() -> None:
    """Deployed configs are bind-mounted: the bound is enforced at construction, not only here."""
    collector._check_skew_budget(_max_hold_back_ns())
    headroom = READ_SPAN_MARGIN_NS - (collector._MAX_CATCH_UP_SECONDS + 1) * NS_PER_S
    collector._check_skew_budget(headroom - collector._VENUE_AHEAD_NS)
    with pytest.raises(ValueError, match="hold_back_seconds"):
        collector._check_skew_budget(headroom - collector._VENUE_AHEAD_NS + 1)
    assert "_check_skew_budget" in _names_in(collector.Collector.__init__)


def test_the_config_walk_reads_an_array_of_tables() -> None:
    """`[[section]]` parses to a list: a dict-only walk would pass this whole guard vacuously."""
    assert _hold_back_seconds({"collector": [{"hold_back_seconds": 7.0}]}) == [7.0]
