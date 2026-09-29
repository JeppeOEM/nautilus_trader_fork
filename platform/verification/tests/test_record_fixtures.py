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
"""The fixture tool never replaces good fixtures with a short recording, and trims any frame."""

import json
import shutil
from pathlib import Path

import pytest

from verification.domain.plan_file import RecordingPlan
from verification.infrastructure.raw_store import FILE_SUFFIX
from verification.infrastructure.raw_store import RawStore
from verification.infrastructure.raw_store import iter_records
from verification.infrastructure.raw_store import venue_dir
from verification.tools.record_fixtures import Trim
from verification.tools.record_fixtures import _swap_in
from verification.tools.record_fixtures import main
from verification.tools.record_fixtures import recording_problems
from verification.tools.record_fixtures import trim


_FIXTURES = Path(__file__).parent / "fixtures"
_HL = RecordingPlan("HYPERLIQUID", "mainnet", ("SOL-USD-PERP.HYPERLIQUID",))
_BYBIT = RecordingPlan(
    "BYBIT",
    "mainnet",
    ("BTCUSDT-LINEAR.BYBIT", "ETHUSDT-LINEAR.BYBIT", "BTCUSDT-SPOT.BYBIT", "ETHUSDT-SPOT.BYBIT"),
)


def _copy(tmp_path: Path, venue: str) -> Path:
    work = tmp_path / "work"
    shutil.copytree(venue_dir(_FIXTURES, venue), venue_dir(work, venue))
    return work


def _nothing(site: str, detail: str = "", exc: BaseException | None = None) -> None:
    raise AssertionError(f"unexpected ledger entry {site}: {detail}")


def test_the_committed_fixtures_are_a_complete_recording() -> None:
    assert recording_problems(_HL, _FIXTURES) == []
    assert recording_problems(_BYBIT, _FIXTURES) == []


def test_a_recording_missing_a_channel_or_the_reconnect_is_refused(tmp_path: Path) -> None:
    work = _copy(tmp_path, "HYPERLIQUID")
    shutil.rmtree(venue_dir(work, "HYPERLIQUID") / "trades")
    shutil.rmtree(venue_dir(work, "HYPERLIQUID") / "rest.l2Book")
    assert recording_problems(_HL, work) == [
        "rest.l2Book: no good response",
        "trades: no data frame",
    ]
    shutil.rmtree(venue_dir(work, "HYPERLIQUID") / "connection")
    assert "ws: no forced reconnect recorded" in recording_problems(_HL, work)


def test_trim_keeps_a_non_json_frame_and_leaves_no_staging(tmp_path: Path) -> None:
    work = _copy(tmp_path, "HYPERLIQUID")
    store = RawStore(work, "HYPERLIQUID", 36500, _nothing)
    frame = {"kind": "frame", "recv_ns": 1790676700000000000, "endpoint": "ws", "raw": "not json {"}
    store.write("unparsed", 1790676700000000000, frame)
    store.close()
    out = tmp_path / "out"
    out.mkdir()
    kept = trim("HYPERLIQUID", work, out, Trim(book_frames=5, frames=5, rest_lines=1))
    assert kept["unparsed"] == 1
    assert kept["l2Book"] == 2 * 5 + 4  # five frames per connection, plus its four connection lines
    unparsed = sorted((venue_dir(out, "HYPERLIQUID") / "unparsed").glob(f"*{FILE_SUFFIX}"))
    assert [line["raw"] for path in unparsed for line in iter_records(path)] == ["not json {"]
    assert sorted(p.name for p in out.iterdir()) == ["raw"]


def test_a_swap_that_died_between_its_renames_restores_the_old_fixtures_first(
    tmp_path: Path,
) -> None:
    target, new = tmp_path / "bybit", tmp_path / "staged"
    (tmp_path / "bybit.old").mkdir()
    (tmp_path / "bybit.old" / "kept").write_text("committed")
    new.mkdir()
    (new / "fresh").write_text("new")
    _swap_in(new, target)
    assert sorted(p.name for p in target.iterdir()) == ["fresh"]
    assert not (tmp_path / "bybit.old").exists()


def test_a_failed_swap_puts_the_committed_fixtures_back(tmp_path: Path) -> None:
    target = tmp_path / "bybit"
    target.mkdir()
    (target / "kept").write_text("committed")
    with pytest.raises(OSError):
        _swap_in(tmp_path / "never-staged", target)
    assert (target / "kept").read_text() == "committed"
    assert not (tmp_path / "bybit.old").exists()


@pytest.mark.parametrize("reconnect_after", ["0", "180", "200"])
def test_a_reconnect_outside_the_recording_is_refused_before_recording(
    tmp_path: Path, reconnect_after: str
) -> None:
    argv = ["--venue", "BYBIT", "--seconds", "180", "--reconnect-after", reconnect_after]
    with pytest.raises(SystemExit):
        main([*argv, "--out", str(tmp_path / "out")])
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("flag", ["--book-frames", "--frames", "--rest-lines"])
def test_a_trim_that_keeps_no_data_is_refused_before_recording(tmp_path: Path, flag: str) -> None:
    argv = ["--venue", "BYBIT", "--seconds", "180", "--reconnect-after", "90", flag, "0"]
    with pytest.raises(SystemExit):
        main([*argv, "--out", str(tmp_path / "out")])
    assert not (tmp_path / "out").exists()


def test_the_hyperliquid_trim_keeps_each_coins_share(tmp_path: Path) -> None:
    work = tmp_path / "work"
    store = RawStore(work, "HYPERLIQUID", 36500, _nothing)
    ts = 1790676700000000000
    store.write("connection", ts, {"kind": "connection", "event": "open", "ts_ns": ts})
    for i, coin in enumerate(["SOL"] * 5 + ["BTC"] * 5):
        raw = json.dumps({"channel": "trades", "data": [{"coin": coin, "tid": i}]})
        line = {"kind": "frame", "recv_ns": ts + i + 1, "endpoint": "ws", "raw": raw}
        store.write("trades", ts + i + 1, line)
    store.close()
    out = tmp_path / "out"
    out.mkdir()
    trim("HYPERLIQUID", work, out, Trim(book_frames=2, frames=2, rest_lines=1))
    paths = sorted((venue_dir(out, "HYPERLIQUID") / "trades").glob(f"*{FILE_SUFFIX}"))
    coins = [
        json.loads(str(line["raw"]))["data"][0]["coin"]
        for path in paths
        for line in iter_records(path)
        if line["kind"] == "frame"
    ]
    assert coins == ["SOL", "SOL", "BTC", "BTC"]
