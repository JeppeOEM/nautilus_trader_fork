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
Capture's CPU budget in `docker-compose.yml` (Story 28.1): the three collectors weigh
`cpu_shares: 1024` and carry a `mem_limit` with its evidence comment (and an equal
`memswap_limit`: no swap), every batch or interactive
service weighs 256, `redis` keeps the default weight (it is on capture's publish path), and no
service sets a hard `cpus:` cap or a `deploy:` block -- a cap would throttle a collector in the
very burst it must not fall behind in (`docs/DEPLOY_CHECKLIST.md` §7).

The file is read with `test_compose_verify`'s YAML-subset parser (the collector image has no YAML
library), imported rather than copied.
"""

import re

import pytest
from test_compose_verify import _BASE
from test_compose_verify import _services
from test_compose_verify import _yaml


_COLLECTORS = ("collector", "bybit_collector", "hyperliquid_collector")
_BATCH = ("archive", "ranking_engine", "data_api", "bot_tui", "live-paper", "dozzle")
_MEM_LIMIT = re.compile(r"^\d+m$")


def _compose() -> dict[str, dict]:
    return _services(_yaml(_BASE))


def test_every_service_is_budgeted() -> None:
    assert set(_compose()) == {"redis", *_COLLECTORS, *_BATCH}, "a new service needs a weight"


@pytest.mark.parametrize("name", _COLLECTORS)
def test_a_collector_has_the_full_weight_and_a_memory_limit(name: str) -> None:
    service = _compose()[name]
    assert service["cpu_shares"] == "1024"
    assert _MEM_LIMIT.fullmatch(service["mem_limit"]), service.get("mem_limit")
    # No swap on top: a swapping collector stalls instead of failing visibly.
    assert service["memswap_limit"] == service["mem_limit"]


@pytest.mark.parametrize("name", _BATCH)
def test_a_batch_service_has_a_quarter_weight(name: str) -> None:
    assert _compose()[name]["cpu_shares"] == "256"


def test_redis_keeps_the_default_weight() -> None:
    assert "cpu_shares" not in _compose()["redis"]


def test_no_service_sets_a_hard_cpu_cap_or_a_deploy_block() -> None:
    capped = {
        name: sorted({"cpus", "deploy"} & set(service))
        for name, service in _compose().items()
        if {"cpus", "deploy"} & set(service)
    }
    assert capped == {}


def test_every_mem_limit_states_its_evidence() -> None:
    """The comment block right above each `mem_limit:` names the measurement and the 1.5x rule."""
    lines = _BASE.read_text().splitlines()
    limits = [n for n, line in enumerate(lines) if line.strip().startswith("mem_limit:")]
    assert len(limits) == len(_COLLECTORS)
    for n in limits:
        above = []
        for line in reversed(lines[:n]):
            if not line.strip().startswith("#"):
                break
            above.append(line)
        evidence = " ".join(above)
        assert "x 1.5" in evidence, f"line {n + 1}: no evidence comment above the mem_limit"
        assert "MiB" in evidence, f"line {n + 1}: the evidence names no measured size"
