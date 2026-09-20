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
"""`top_levels_mismatch` (pure) + the core's feed-liveness gate and REST cross-check (story 22.5)."""

import asyncio
import logging
import time
from pathlib import Path

import pytest
from ml_signals import error_ledger

from collector_core.book_check import top_levels_mismatch
from collector_core.config import core_config_from_dict
from collector_core.tests.test_collector import _BYBIT
from collector_core.tests.test_collector import _S
from collector_core.tests.test_collector import _collector
from collector_core.tests.test_collector import _deltas
from collector_core.tests.test_collector import _tick


_BIDS = [(100.0, 1.0), (99.5, 2.0), (99.0, 3.0)]


def test_identical_books_agree() -> None:
    assert top_levels_mismatch(_BIDS, list(_BIDS)) == []


def test_one_level_shifted_within_tolerance() -> None:
    rest = [(100.0, 1.0), (99.5, 2.0), (98.5, 3.0)]  # tail level moved: 1 absent price allowed
    assert top_levels_mismatch(_BIDS, rest) == []


def test_best_price_differs() -> None:
    (msg, *_) = top_levels_mismatch(_BIDS, [(100.5, 1.0), *_BIDS[1:]])
    assert msg.startswith("best price")


def test_size_drift_over_tolerance() -> None:
    (msg,) = top_levels_mismatch(_BIDS, [(100.0, 1.0), (99.5, 2.5), (99.0, 3.0)])
    assert "size at 99.5" in msg


def test_rest_shorter_than_depth_is_not_a_mismatch() -> None:
    assert top_levels_mismatch(_BIDS, _BIDS[:2]) == []


def test_one_side_empty_is_a_mismatch() -> None:
    assert top_levels_mismatch(_BIDS, []) == ["one side empty: live=3 rest=0"]
    assert top_levels_mismatch([], []) == []


def test_config_keys_validated() -> None:
    cfg = core_config_from_dict({"feed_stale_seconds": 12, "book_crosscheck_seconds": 0}, ("mainnet",))
    assert (cfg.feed_stale_seconds, cfg.book_crosscheck_seconds) == (12.0, 0.0)
    with pytest.raises(ValueError, match="feed_stale_seconds"):
        core_config_from_dict({"feed_stale_seconds": 0}, ("mainnet",))
    with pytest.raises(ValueError, match="book_crosscheck_seconds"):
        core_config_from_dict({"book_crosscheck_seconds": -1}, ("mainnet",))


def test_gate_names_dead_feed_vs_silent_instrument(tmp_path: Path, caplog) -> None:
    c = _collector(tmp_path)
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)]))
    now = time.time_ns()
    # Feed alive (message just now) but this instrument's book is old -> instrument silent.
    c._last_book_update_ns[_BYBIT] = now - 60 * _S
    with caplog.at_level(logging.WARNING, logger="collector_core.collector"):
        assert _tick(c, now) == []
    assert "instrument silent" in caplog.text
    # Nothing at all for a minute -> feed dead, said so.
    caplog.clear()
    c._last_book_update_ns[_BYBIT] = now
    c._last_feed_message_ns = now - 60 * _S
    with caplog.at_level(logging.WARNING, logger="collector_core.collector"):
        assert _tick(c, now) == []
    assert "feed dead" in caplog.text


class _RestClient:
    def __init__(self, bids: list, asks: list) -> None:
        self.levels = (bids, asks)

    async def fetch_book_levels(self, iid: str) -> tuple[list, list]:
        return self.levels


def test_crosscheck_ledgers_persistent_mismatch_and_passes_clean(tmp_path: Path) -> None:
    error_ledger.reset()
    rest = _RestClient([(100.0, 1.0)], [(100.5, 1.0)])
    c = _collector(tmp_path, client=rest)
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)]))
    asyncio.run(c._crosscheck_one(_BYBIT))
    assert error_ledger.counts() == {}
    rest.levels = ([(99.0, 1.0)], [(100.5, 1.0)])  # REST disagrees on the best bid, both captures
    asyncio.run(c._crosscheck_one(_BYBIT))
    assert error_ledger.counts() == {"collector.book_crosscheck": 1}


def test_crosscheck_ignores_skew_seen_in_only_one_capture(tmp_path: Path) -> None:
    error_ledger.reset()
    c = _collector(tmp_path)
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)]))

    class _Moving:
        async def fetch_book_levels(self, iid: str) -> tuple[list, list]:
            # The live book moves to match REST while the request is in flight.
            c._process_data(_deltas([(99.0, 1.0)], [(100.5, 1.0)]))
            return [(99.0, 1.0)], [(100.5, 1.0)]

    c._client = _Moving()
    asyncio.run(c._crosscheck_one(_BYBIT))
    assert error_ledger.counts() == {}


def test_persistent_keeps_only_levels_wrong_in_both_rounds() -> None:
    from collector_core.book_check import persistent

    first = ["bids size at 99.5: live=1 rest=2", "asks best price: live=1 rest=2"]
    second = ["bids size at 99.5: live=1 rest=3", "bids size at 99.0: live=1 rest=2"]
    assert persistent(first, second) == ["bids size at 99.5: live=1 rest=3"]
