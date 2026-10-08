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
`archive.tools.measure_lag`'s `PLAN_ENV` pinned against `docker-compose.yml` and the collectors'
own `CONFIG_PATH` (DW-240): the tool must read the plan the collector collects, so a renamed env
var or a moved mount fails here instead of sending the tool back to a baked-in plan silently.

The file is read with `test_compose_verify`'s YAML-subset parser (the collector image has no YAML
library), imported rather than copied.
"""

import pytest
from _source_tree import PLATFORM_DIR
from archive.tools.measure_lag import _DYDX_DEFAULT_PLAN
from archive.tools.measure_lag import PLAN_ENV
from test_compose_verify import _BASE
from test_compose_verify import _services
from test_compose_verify import _yaml


@pytest.mark.parametrize("venue", ["bybit", "hyperliquid"])
def test_plan_env_is_the_collector_services_plan_var(venue: str) -> None:
    service = _services(_yaml(_BASE))[f"{venue}_collector"]
    plan = service["environment"][PLAN_ENV[venue]]
    mounts = [str(volume).split(":") for volume in service["volumes"]]
    assert [f"./capture/venues/{venue}/config.toml", plan, "rw"] in mounts


@pytest.mark.parametrize("venue", ["bybit", "hyperliquid", "dydx"])
def test_plan_env_is_the_var_the_collector_config_reads(venue: str) -> None:
    source = (PLATFORM_DIR / "capture" / "venues" / venue / "config.py").read_text()
    assert f'os.environ.get("{PLAN_ENV[venue]}"' in source


def test_the_dydx_default_plan_is_the_archive_services_plan() -> None:
    archive = _services(_yaml(_BASE))["archive"]
    assert archive["environment"][PLAN_ENV["dydx"]] == _DYDX_DEFAULT_PLAN
    source = (PLATFORM_DIR / "capture" / "venues" / "dydx" / "config.py").read_text()
    assert f'"{_DYDX_DEFAULT_PLAN}"' in source
