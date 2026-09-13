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
Unit test for _prune_stale_ws_raw_logs: guards against the orphaned-backup leak where
nautilus_trader's FileWriter never seeds its in-memory backup queue from files already
on disk, so a restarted process leaves the previous run's rotated logs permanently
unpruned (confirmed by reading crates/common/src/logging/writer.rs; see the function's
docstring).
"""

from pathlib import Path

import dydx_collector.collector as collector


def test_prune_stale_ws_raw_logs_deletes_orphaned_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(collector, "_WS_RAW_LOG_DIR", tmp_path)
    stale = tmp_path / "ws_raw_debug_2026-09-12_134542:440.log"
    stale.write_text("orphaned from a previous process run")
    unrelated = tmp_path / "other_file.txt"
    unrelated.write_text("must not be touched")

    collector._prune_stale_ws_raw_logs()

    assert not stale.exists()
    assert unrelated.exists()


def test_prune_stale_ws_raw_logs_handles_missing_dir(tmp_path: Path, monkeypatch) -> None:
    missing = tmp_path / "does_not_exist"
    monkeypatch.setattr(collector, "_WS_RAW_LOG_DIR", missing)

    collector._prune_stale_ws_raw_logs()  # must not raise
