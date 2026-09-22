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
The dYdX entrypoint's incident-report wiring (Story 23.1): the venue-free handler in
`observability.incidents` gets dYdX's id shape, report title, raw-log location and warning texts
from `dydx_collector.collector.INCIDENTS`, and produces the same reports as before the move.
"""

import dataclasses
import json
import os
from pathlib import Path

import pytest
from observability import incidents

from dydx_collector.collector import INCIDENTS


_IID = "BTC-USD-PERP.DYDX"
_T = 1_800_000_000_000_000_000


def _compose_file() -> Path:
    """
    Return the checkout's compose file: `make test` mounts it at `PLATFORM_SOURCE_DIR`, a host
    run finds it two levels up (the image's `/app` has none, so there this fails loudly).
    """
    configured = os.environ.get("PLATFORM_SOURCE_DIR")
    platform_dir = Path(configured) if configured else Path(__file__).resolve().parents[2]
    return platform_dir / "docker-compose.yml"


def test_report_dir_is_the_compose_bind_mount() -> None:
    """Read from the compose file, so renaming the mount fails here, not at the first incident."""
    volume = f"- ./data/incident_reports:{INCIDENTS.report_dir}"
    lines = [line.strip() for line in _compose_file().read_text().splitlines()]
    assert volume in lines, f"docker-compose.yml no longer mounts {INCIDENTS.report_dir}"


def test_raw_log_is_the_rust_writers_ephemeral_buffer() -> None:
    assert INCIDENTS.raw_log_dir == Path("/tmp/nautilus_logs")  # noqa: S108
    assert INCIDENTS.raw_log_name == "ws_raw_debug"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "Crossed book for NEAR-USD-PERP.DYDX (bid=2.266000 >= ask=2.266000) — skipping "
            "snapshot [last bid delta 0.1s ago, last ask delta 0.1s ago]",
            ("crossed_book", "NEAR-USD-PERP.DYDX"),
        ),
        (f"Stale book for {_IID}", ("stale_book", _IID)),
        (f"Resyncing {_IID}", ("resync", _IID)),
        ("_second_loop tick arrived 2.1s late", ("second_loop_lag", None)),
        (
            json.dumps({"instrument_id": _IID, "reason": "steady_state_crossed_book"}),
            ("steady_state_crossed_book", _IID),
        ),
        ("Something unexpected happened", ("unclassified", None)),
    ],
)
def test_dydx_warnings_classify_as_before_the_move(
    message: str, expected: tuple[str, str | None]
) -> None:
    assert incidents.classify_incident(INCIDENTS, message) == expected


def test_dydx_report_carries_ws_raw_evidence_for_the_ticker(tmp_path: Path) -> None:
    config = dataclasses.replace(INCIDENTS, raw_log_dir=tmp_path, report_dir=tmp_path / "reports")
    # A [WS_RAW] line's "id" is the ticker: BTC-USD for BTC-USD-PERP.DYDX.
    line = incidents.ns_to_iso(_T) + ' [DEBUG] x: [WS_RAW] {"id":"BTC-USD","type":"channel_data"}\n'
    (tmp_path / "ws_raw_debug_1.log").write_text(line)
    path = incidents.IncidentReportWriter(config).write(
        "crossed_book", _IID, "WARNING", "dydx_collector.collector", "Crossed book for X", _T
    )
    content = Path(path).read_text()
    assert content.startswith("=== dYdX Collector Incident Report ===\n")
    assert "--- Raw WS evidence (BTC-USD, 1 messages) ---\n" + line in content
