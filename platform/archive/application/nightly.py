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
The nightly saga (AD-D9): one venue-day's steps in order, each its own child process, carrying
the rebuild's proof to the reconcile.

A step's exit 2 means "findings": it finished, but some instruments were refused (rebuild) or
mismatched / could not be compared (compare) -- all ledgered. The saga continues past findings,
since one instrument's standing problem must not halt the venue's maintenance every night. Any
other non-zero exit is a failure and stops the saga at that step. Findings and failures are
recorded as `nightly.<step>` in the error ledger.

The proof: the rebuild step writes its `--result-file` (`{"venue", "day", "rebuilt", "refused"}`)
into the saga's own temporary directory; the saga reads it into that step's `StepResult` as a
`RebuildProof` for its `run_id` -- both lists, `rebuilt` and `refused` (as `not_rebuilt`) -- and
appends `--rebuilt-by <run_id>`, one `--rebuilt <iid>` per rebuilt and one `--not-rebuilt <iid>`
per refused instrument to the reconcile step's argv, so the reconcile judges only what this run
rebuilt (an allowlist: an instrument in neither list is not judged). A missing or invalid result file fails the rebuild step (the saga stops before
consolidating anything). Why a file: the steps stay subprocesses (MEM-01 -- each step's memory is
returned to the OS before the next starts), so the proof crosses the process boundary once, as
inter-process transport; it is never persisted.
"""

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeGuard

from observability import error_ledger

from archive.domain.archive_day import RebuildProof


logger = logging.getLogger(__name__)

FINDINGS = 2
_RESULT_FAILED = 1  # the exit code a rebuild step gets when its result file is unusable


@dataclass(frozen=True)
class Step:
    """
    One nightly step: its name (the ledger suffix), its command line and its role in the saga.

    `result_file` marks the rebuild step (the saga reads its proof there); `needs_proof` the
    reconcile step (the saga appends that proof to its argv).
    """

    name: str
    argv: list[str]
    result_file: str | None = None
    needs_proof: bool = False


@dataclass
class StepResult:
    """How one step ended; the rebuild step's also carries the run's `RebuildProof`."""

    name: str
    code: int
    seconds: float
    proof: RebuildProof | None = None

    def outcome(self) -> str:
        if self.code == 0:
            return "ok"
        return "findings" if self.code == FINDINGS else "FAILED"


StepRunner = Callable[[list[str]], int]


def _ids(value: object) -> TypeGuard[list[str]]:
    return isinstance(value, list) and all(isinstance(i, str) for i in value)


def read_rebuild_result(path: str, run_id: str, venue: str, day: str) -> RebuildProof:
    """Parse the rebuild step's result file into the run's proof; `ValueError` when unusable."""
    try:
        result = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"rebuild result {path} unreadable: {e!r}") from e
    if not isinstance(result, dict) or not _ids(result.get("rebuilt")):
        raise ValueError(f"rebuild result {path} is not a rebuild result: {result!r}")
    refused = result.get("refused")
    if not _ids(refused):
        raise ValueError(f"rebuild result {path} is not a rebuild result: {result!r}")
    if (result.get("venue"), result.get("day")) != (venue, day):
        raise ValueError(f"rebuild result {path} is for another venue-day: {result!r}")
    return RebuildProof(run_id, venue, day, frozenset(result["rebuilt"]), frozenset(refused))


def _argv(step: Step, proof: RebuildProof | None) -> list[str]:
    if not step.needs_proof or proof is None:
        return step.argv
    rebuilt = [arg for iid in sorted(proof.rebuilt) for arg in ("--rebuilt", iid)]
    refused = [arg for iid in sorted(proof.not_rebuilt) for arg in ("--not-rebuilt", iid)]
    return [*step.argv, "--rebuilt-by", proof.run_id, *rebuilt, *refused]


def _read_proof(step: Step, result: StepResult, run_id: str, venue: str, day: str) -> None:
    """Carry the rebuild step's proof into its result; an unusable file fails the step."""
    if step.result_file is None or result.outcome() == "FAILED":
        return
    try:
        result.proof = read_rebuild_result(step.result_file, run_id, venue, day)
    except ValueError as e:
        logger.error("nightly %s: %s", step.name, e)
        result.code = _RESULT_FAILED


def run_steps(
    chain: list[Step],
    runner: StepRunner,
    run_id: str,
    venue: str,
    day: str,
    on_step: Callable[[StepResult], None] | None = None,
) -> list[StepResult]:
    """
    Run the chain, stopping after the first failed step (a step's findings, exit 2, continue).

    `on_step` (the `archive` service's status, Story 25.1b) is called with each step's result once
    it is final -- after the rebuild's proof is read, which may still fail that step -- and before
    the next step starts. It is called on the thread running the chain.
    """
    results: list[StepResult] = []
    proof: RebuildProof | None = None
    for step in chain:
        started = time.monotonic()
        code = runner(_argv(step, proof))
        result = StepResult(step.name, code, time.monotonic() - started)
        _read_proof(step, result, run_id, venue, day)
        proof = result.proof or proof
        results.append(result)
        if on_step is not None:
            on_step(result)
        if result.outcome() == "FAILED":
            error_ledger.record(f"nightly.{step.name}", f"exit {result.code}; later steps not run")
            break
        if result.outcome() == "findings":
            error_ledger.record(
                f"nightly.{step.name}", "findings: per-instrument refusals/mismatches (see above)"
            )
    return results


def exit_code(results: list[StepResult], findings: bool = False) -> int:
    """Return the failing step's code, else 2 on any step's findings (or a run-level `findings`), else 0."""
    failed = [r for r in results if r.outcome() == "FAILED"]
    if failed:
        return failed[0].code
    return FINDINGS if findings or any(r.outcome() == "findings" for r in results) else 0


def summary_line(
    venue: str,
    day: str,
    run_id: str,
    results: list[StepResult],
    peak_mb: float,
    findings: bool = False,
) -> str:
    parts = ", ".join(f"{r.name} {r.outcome()} {r.seconds:.1f}s" for r in results)
    code = exit_code(results, findings)
    overall = "ok" if code == 0 else ("findings" if code == FINDINGS else "FAILED")
    return (
        f"nightly {venue} {day}: {parts}; peak child RSS {peak_mb:.0f} MB; outcome {overall}; "
        f"run id {run_id}"
    )
