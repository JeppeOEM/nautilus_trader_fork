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
"""`ArchiveDay`: one invariant test per command, and every illegal move refused (AD-D9)."""

import pytest

from archive.domain.archive_day import ArchiveDay
from archive.domain.archive_day import DayStatus
from archive.domain.archive_day import IllegalTransition
from archive.domain.archive_day import RebuildProof
from archive.domain.reconciliation import ReconciliationResult


_IID = "BTCUSDT-LINEAR.BYBIT"
_DAY = "2026-09-20"
_PROOF = RebuildProof("run1", "BYBIT", _DAY, frozenset({_IID}))


def _day(status: DayStatus = DayStatus.PROVISIONAL) -> ArchiveDay:
    return ArchiveDay("BYBIT", _IID, _DAY, status)


def test_the_persisted_status_maps_onto_the_states() -> None:
    assert (
        ArchiveDay.from_verified_status("BYBIT", _IID, _DAY, None).status is DayStatus.PROVISIONAL
    )
    assert ArchiveDay.from_verified_status("BYBIT", _IID, _DAY, "pass").status is DayStatus.VERIFIED
    assert ArchiveDay.from_verified_status("BYBIT", _IID, _DAY, "fail").status is (
        DayStatus.MISMATCHED
    )
    with pytest.raises(ValueError, match="unknown verified_days status"):
        ArchiveDay.from_verified_status("BYBIT", _IID, _DAY, "maybe")


@pytest.mark.parametrize("start", [DayStatus.PROVISIONAL, DayStatus.MISMATCHED, DayStatus.VERIFIED])
def test_rebuilt_needs_a_proof_covering_the_day(start: DayStatus) -> None:
    assert _day(start).rebuilt(_PROOF).status is DayStatus.REBUILT


@pytest.mark.parametrize(
    "proof",
    [
        RebuildProof("run1", "BYBIT", _DAY, frozenset({_IID}), frozenset({_IID})),  # refused it
        RebuildProof("run1", "BYBIT", _DAY),  # never named it rebuilt (an allowlist)
        RebuildProof("run1", "DYDX", _DAY, frozenset({_IID})),  # another venue's run
        RebuildProof("run1", "BYBIT", "2026-09-19", frozenset({_IID})),  # another day's run
    ],
)
def test_rebuilt_is_refused_without_a_covering_proof(proof: RebuildProof) -> None:
    with pytest.raises(IllegalTransition, match="did not rebuild it"):
        _day().rebuilt(proof)


@pytest.mark.parametrize("start", [DayStatus.REBUILT, DayStatus.RELEASED])
def test_rebuilt_is_refused_from_rebuilt_or_released(start: DayStatus) -> None:
    with pytest.raises(IllegalTransition, match="cannot rebuild"):
        _day(start).rebuilt(_PROOF)


def test_reconciled_goes_to_verified_only_on_an_exact_match() -> None:
    rebuilt = _day().rebuilt(_PROOF)
    assert rebuilt.reconciled(ReconciliationResult(_IID, "pass", 5)).status is DayStatus.VERIFIED
    failed = ReconciliationResult(_IID, "fail", 5, ["a minute differs"])
    assert rebuilt.reconciled(failed).status is DayStatus.MISMATCHED


@pytest.mark.parametrize(
    "start",
    [DayStatus.PROVISIONAL, DayStatus.VERIFIED, DayStatus.MISMATCHED, DayStatus.RELEASED],
)
def test_reconciled_is_refused_unless_rebuilt(start: DayStatus) -> None:
    with pytest.raises(IllegalTransition, match="not rebuilt"):
        _day(start).reconciled(ReconciliationResult(_IID, "pass"))


@pytest.mark.parametrize("status", ["error", "not_rebuilt"])
def test_a_finding_is_never_a_verdict(status: str) -> None:
    with pytest.raises(IllegalTransition):
        _day().rebuilt(_PROOF).reconciled(ReconciliationResult(_IID, status))


def test_another_instruments_result_is_refused() -> None:
    with pytest.raises(IllegalTransition):
        _day().rebuilt(_PROOF).reconciled(ReconciliationResult("ETHUSDT-LINEAR.BYBIT", "pass"))


def test_released_only_from_verified() -> None:
    assert _day(DayStatus.VERIFIED).released().status is DayStatus.RELEASED
    for start in (DayStatus.PROVISIONAL, DayStatus.REBUILT, DayStatus.MISMATCHED):
        with pytest.raises(IllegalTransition, match="cannot release"):
            _day(start).released()


def test_only_verified_and_mismatched_are_ever_persisted() -> None:
    assert _day(DayStatus.VERIFIED).verified_status() == "pass"
    assert _day(DayStatus.MISMATCHED).verified_status() == "fail"
    for start in (DayStatus.PROVISIONAL, DayStatus.REBUILT, DayStatus.RELEASED):
        with pytest.raises(IllegalTransition, match="never persisted"):
            _day(start).verified_status()


def test_a_proof_covers_only_the_rebuilt_and_never_the_refused() -> None:
    proof = RebuildProof(
        "run1", "BYBIT", _DAY, frozenset({_IID, "Y.BYBIT"}), frozenset({"Y.BYBIT"})
    )
    assert proof.covers(_IID)
    assert not proof.covers("Y.BYBIT")  # named in both lists: refused wins
    assert not proof.covers("X.BYBIT")  # in neither list: never processed, never covered
