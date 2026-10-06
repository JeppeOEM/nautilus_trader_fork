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
The venue cutover's compose gate (Story 29.3): dYdX's `collector` runs only behind the `dydx`
profile, through `make up-dydx`/`down-dydx`, and no default start or restart target names it,
while the `archive` service keeps DYDX in its nightly list until the dYdX days age out.

Parsed by indentation and plain `tomllib`, never PyYAML: the collector image has no YAML library.
"""

import re
import shlex
import tomllib

import pytest
from _source_tree import PLATFORM_DIR


_COMPOSE = PLATFORM_DIR / "docker-compose.yml"
_MAKEFILE = PLATFORM_DIR / "Makefile"
_ARCHIVE_CONFIG = PLATFORM_DIR / "archive" / "config.toml"

# Every target that starts or restarts the default stack: none may bring dYdX back.
_DEFAULT_TARGETS = ("up", "redeploy", "redeploy-all", "redeploy-no-paper", "frontend-dev")

# Compose verbs that start or restart a named service (`build` only builds; `run` is the
# maintenance targets' one-off container, which is not in the default targets).
_STARTING_VERBS = ("up", "start", "restart", "create")


def _service_profiles() -> dict[str, str | None]:
    """Each compose service -> the raw value of its `profiles:` line, or None without one."""
    profiles: dict[str, str | None] = {}
    current: str | None = None
    in_services = False
    for line in _COMPOSE.read_text().splitlines():
        if line and not line[0].isspace():
            in_services = line.startswith("services:")
            continue
        if not in_services or not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        key, _, value = line.strip().partition(":")
        if indent == 2:
            current = key
            profiles[current] = None
        elif current is not None and indent == 4 and key == "profiles":
            profiles[current] = value.strip()
    return profiles


def _makefile_recipes() -> dict[str, list[str]]:
    """Target -> its recipe lines, backslash continuations joined."""
    recipes: dict[str, list[str]] = {}
    target: str | None = None
    for line in _MAKEFILE.read_text().replace("\\\n", " ").splitlines():
        match = re.match(r"^([A-Za-z][\w-]*):(?!=)", line)
        if match:
            target = match.group(1)
            recipes[target] = []
        elif line.startswith("\t") and target is not None:
            recipes[target].append(line.strip())
    return recipes


def _compose_commands(target: str) -> list[list[str]]:
    """Return the `$(COMPOSE) ...` lines of a target's recipe, as tokens after `$(COMPOSE)`."""
    recipes = _makefile_recipes()
    assert target in recipes, f"Makefile has no target {target!r}"
    # Make's `@` (silent) and `-` (ignore errors) prefixes do not change what the line runs.
    lines = (line.lstrip("@-") for line in recipes[target])
    return [shlex.split(line)[1:] for line in lines if line.startswith("$(COMPOSE)")]


def test_the_collector_service_is_gated_behind_the_dydx_profile() -> None:
    assert _service_profiles()["collector"] == '["dydx"]'


@pytest.mark.parametrize("service", ["bybit_collector", "hyperliquid_collector", "archive"])
def test_the_default_services_carry_no_profile(service: str) -> None:
    profiles = _service_profiles()

    assert service in profiles, f"docker-compose.yml has no service {service!r}"
    assert profiles[service] is None


def test_up_dydx_starts_the_collector_under_its_profile() -> None:
    assert _compose_commands("up-dydx") == [
        ["--profile", "dydx", "up", "-d", "--build", "collector"]
    ]


def test_down_dydx_removes_the_collector_rather_than_stopping_it() -> None:
    # A stopped `restart: always` container is started again with the Docker daemon, so only a
    # removal keeps dYdX down across a reboot.
    assert _compose_commands("down-dydx") == [["--profile", "dydx", "rm", "-sf", "collector"]]


@pytest.mark.parametrize("target", _DEFAULT_TARGETS)
def test_no_default_target_starts_the_collector_service(target: str) -> None:
    commands = _compose_commands(target)

    assert commands, f"{target} runs no compose command"
    for tokens in commands:
        verbs = [verb for verb in _STARTING_VERBS if verb in tokens]
        for verb in verbs:
            assert "collector" not in tokens[tokens.index(verb) + 1 :], f"{target}: {tokens}"


@pytest.mark.parametrize("target", _DEFAULT_TARGETS)
def test_no_default_target_enables_the_dydx_profile(target: str) -> None:
    # Any spelling reaches dYdX: `--profile dydx`, `--profile=dydx`, a `COMPOSE_PROFILES=dydx`
    # prefix, a raw `docker compose` line or a `$(MAKE) up-dydx` sub-make. None may appear.
    recipe = _makefile_recipes()[target]

    assert not [line for line in recipe if "dydx" in line.lower()], f"{target}: {recipe}"


@pytest.mark.parametrize("target", ["up", "redeploy-all"])
def test_the_rebuilding_targets_still_build_the_collector_image(target: str) -> None:
    # Compose tags one image per service and the maintenance targets (`test`, `nightly`, ...)
    # `run` the `collector` one: `up`/`redeploy-all` had been its only builders.
    assert ["build", "collector"] in _compose_commands(target)


def test_the_nightly_keeps_dydx_until_its_days_age_out() -> None:
    venues = tomllib.loads(_ARCHIVE_CONFIG.read_text())["venues"]

    assert "DYDX" in venues


def test_build_candles_rebuilds_one_venues_own_store() -> None:
    """
    DW-194: a hard-coded `--db .../candles_dydx.db` folded every venue's ids into the dYdX store.
    The target names the venue and lets `candles.rebuild` derive its store (AD-D12).
    """
    commands = _compose_commands("build-candles")

    assert len(commands) == 1
    tokens = commands[0]
    assert tokens[tokens.index("--venue") + 1] == "$(VENUE)"
    assert tokens[tokens.index("--candles-dir") + 1] == "/app/candles_dir"
    assert "--db" not in tokens


def test_build_candles_refuses_a_missing_venue_before_compose_runs() -> None:
    """Without VENUE the target would run `--venue` with no value: the guard must come first."""
    recipe = _makefile_recipes()["build-candles"]

    assert recipe[0].startswith('@test -n "$(VENUE)" ||')
    assert recipe[0].endswith("exit 1; }")
