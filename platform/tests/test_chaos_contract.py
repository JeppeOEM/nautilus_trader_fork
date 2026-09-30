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
Story 31.10's expected-outcome table restates capture's coverage reasons, ledger sites and timing
(`verification.domain.chaos`), because the reference never imports the code it checks. Only
`platform/tests` may import both, so the restatement is pinned here: a renamed reason or site in
capture would otherwise turn a *forbidden* check into one that can never fire -- a silent pass.
"""

from pathlib import Path

from capture.application import capture_service
from capture.application import sites
from capture.domain import coverage
from capture.infrastructure.config import load_venue_config
from verification.domain import chaos


_PLATFORM = Path(__file__).resolve().parent.parent


def test_every_reason_the_table_names_is_one_capture_writes() -> None:
    restated = {chaos.RESTART, chaos.WRITE_FAILED, chaos.CATCH_UP_CAP, chaos.STALE}
    assert restated <= coverage.SECOND_REASONS
    assert (chaos.RESTART, chaos.WRITE_FAILED) == (coverage.RESTART, coverage.WRITE_FAILED)
    assert (chaos.CATCH_UP_CAP, chaos.STALE) == (coverage.CATCH_UP_CAP, coverage.STALE)


def test_every_site_the_table_names_is_capture_s() -> None:
    assert chaos.RESTART_GAP_SITE == sites.RESTART_GAP
    assert chaos.TRADE_BACKFILL_SITE == sites.TRADE_BACKFILL
    assert chaos.STALE_TRADE_SITE == sites.STALE_TRADE
    assert chaos.SKIPPED_SECONDS_SITE == sites.SKIPPED_SECONDS
    assert chaos.FLUSH_WRITE_SITE == sites.FLUSH_WRITE
    assert chaos.SNAPSHOT_PUBLISH_SITE == sites.SNAPSHOT_PUBLISH


def test_the_timing_the_scenarios_rely_on_is_capture_s() -> None:
    """The kill and the read-only span hit the :02 flush; the pauses straddle the catch-up cap."""
    assert chaos.KILL_SECOND == capture_service._FLUSH_PHASE_S
    readonly_end_s = chaos.READONLY_SECOND + chaos.READONLY_HOLD_NS // chaos.NS_PER_S
    assert chaos.READONLY_SECOND > chaos.KILL_SECOND  # starts after this minute's flush
    assert readonly_end_s - 60 > capture_service._FLUSH_PHASE_S  # ends after the next one
    cap_ns = capture_service._MAX_CATCH_UP_SECONDS * chaos.NS_PER_S
    assert chaos.PAUSE_NS[chaos.PAUSE_15S] < cap_ns < chaos.PAUSE_NS[chaos.PAUSE_45S]
    for venue in ("bybit", "hyperliquid"):
        path = _PLATFORM / "capture" / "venues" / venue / "config.toml"
        config, _ = load_venue_config(path, venue.upper())
        assert config.flush_interval_seconds == 60
        assert chaos.PAUSE_NS[chaos.PAUSE_15S] > config.stale_trade_seconds * chaos.NS_PER_S
