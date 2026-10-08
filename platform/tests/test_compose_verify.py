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
The verification stack's compose override (Story 31.1), pinned against the base file: every service
renamed `verify-*`, every port and port-derived URL restated on its own default (redis 26379,
data_api 29100, dozzle 28080) and bound to 127.0.0.1 only (SEC-01), the base's logging policy
restated unchanged, the two reference recorders wired as the story says, the dYdX collector never
started, and the `make verify-*` targets running exactly that project.

The collector image has no YAML library (`test_compose_profiles.py`), so both files are read by
`_yaml`, a parser of the YAML subset compose files here use: block mappings and sequences, plain
and quoted scalars, flow sequences, comments, `&anchor`/`*alias` and a node tag (`!override`).
Anything else it meets raises, so an unsupported construct fails this test instead of being misread.
"""

import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from _source_tree import PLATFORM_DIR
from test_images import import_closure
from verification.application.recorder import Timing
from verification.infrastructure.aiohttp_io import CLOSE_TIMEOUT_SECONDS
from verification.infrastructure.aiohttp_io import CONNECT_TIMEOUT_SECONDS


_BASE = PLATFORM_DIR / "docker-compose.yml"
_OVERRIDE = PLATFORM_DIR / "docker-compose.verify.yml"
_DOCKERFILE = PLATFORM_DIR / "collector.dockerfile"
_MAKEFILE = PLATFORM_DIR / "Makefile"

_PORT_VARIABLES = {"REDIS_PORT": ("6379", "26379"), "DATA_API_PORT": ("9100", "29100")}
_PORT_VARIABLES["DOZZLE_PORT"] = ("8080", "28080")
_PORT_USE = re.compile(r"\$\{(REDIS_PORT|DATA_API_PORT|DOZZLE_PORT)(:-(\d+))?\}")
_RECORDERS = {
    "reference_recorder_bybit": ("BYBIT", "bybit_collector", "BYBIT_COLLECTOR_CONFIG"),
    "reference_recorder_hyperliquid": (
        "HYPERLIQUID",
        "hyperliquid_collector",
        "HYPERLIQUID_COLLECTOR_CONFIG",
    ),
}
_VERIFY_SERVICES = {
    "redis",
    "bybit_collector",
    "hyperliquid_collector",
    "archive",
    "ranking_engine",
    "data_api",
    "dozzle",
    "live-paper",
    *_RECORDERS,
}


# --- a parser of the YAML subset these compose files use --------------------------------------


@dataclass(frozen=True)
class Tagged:
    """A node carrying an explicit tag (`ports: !override [...]`)."""

    tag: str
    value: Any


class _Lines:
    """The file's meaningful lines as (indent, text), comments and blank lines dropped."""

    def __init__(self, text: str) -> None:
        self.items: list[tuple[int, str]] = []
        for raw in text.splitlines():
            stripped = _strip_comment(raw).rstrip()
            if stripped.strip():
                self.items.append((len(stripped) - len(stripped.lstrip()), stripped.strip()))
        self.anchors: dict[str, Any] = {}


def _strip_comment(line: str) -> str:
    quote = None
    for index, char in enumerate(line):
        if char in "\"'" and quote in (None, char):
            quote = None if quote else char
        elif char == "#" and quote is None and (index == 0 or line[index - 1].isspace()):
            return line[:index]
    return line


def _scalar(text: str, lines: _Lines) -> Any:
    if text.startswith("*"):
        return lines.anchors[text[1:]]
    if text.startswith('"'):
        return json.loads(text)
    if text.startswith("'"):
        return text[1:-1].replace("''", "'")
    if text.startswith("["):
        return [_scalar(item.strip(), lines) for item in text[1:-1].split(",") if item.strip()]
    if text.startswith(("{", "|", ">", "<<", "&", "!")):
        raise ValueError(f"unsupported YAML construct: {text!r}")
    return text


def _split_key(text: str) -> tuple[str, str]:
    match = re.match(r"^([^\s:\"'][^:]*|\"[^\"]*\"):(?:\s+(.*))?$", text)
    if not match:
        raise ValueError(f"not a mapping entry: {text!r}")
    return match.group(1).strip('"'), (match.group(2) or "").strip()


def _node(lines: _Lines, index: int, indent: int, rest: str) -> tuple[Any, int]:
    """Read the value after `key:` (or `-`): inline, or the indented block (or scalar) below."""
    anchor = tag = None
    while rest.startswith(("&", "!")):
        head, _, rest = rest.partition(" ")
        anchor, tag = (head[1:], tag) if head.startswith("&") else (anchor, head)
    if rest:
        value, index = _scalar(rest, lines), index + 1
    elif index + 1 < len(lines.items) and lines.items[index + 1][0] > indent:
        value, index = _child(lines, index + 1)
    else:
        value, index = None, index + 1
    if tag is not None:
        value = Tagged(tag, value)
    if anchor is not None:
        lines.anchors[anchor] = value
    return value, index


def _child(lines: _Lines, index: int) -> tuple[Any, int]:
    indent, text = lines.items[index]
    if text.startswith("- ") or text == "-":
        return _sequence(lines, index, indent)
    if re.match(r"^[^\s:\"'][^:]*:(\s|$)", text):
        return _mapping(lines, index, indent)
    parts = []  # a plain scalar continued over several lines
    while index < len(lines.items) and lines.items[index][0] >= indent:
        parts.append(lines.items[index][1])
        index += 1
    return " ".join(parts), index


def _sequence(lines: _Lines, index: int, indent: int) -> tuple[list[Any], int]:
    items = []
    while index < len(lines.items) and lines.items[index][0] == indent:
        text = lines.items[index][1]
        if not text.startswith("-"):
            break
        value, index = _node(lines, index, indent, text[1:].strip())
        items.append(value)
    return items, index


def _mapping(lines: _Lines, index: int, indent: int) -> tuple[dict[str, Any], int]:
    mapping: dict[str, Any] = {}
    while index < len(lines.items) and lines.items[index][0] == indent:
        key, rest = _split_key(lines.items[index][1])
        if key in mapping:
            raise ValueError(f"duplicate key {key!r}")
        mapping[key], index = _node(lines, index, indent, rest)
    if index < len(lines.items) and lines.items[index][0] > indent:
        raise ValueError(f"unexpected indentation at {lines.items[index][1]!r}")
    return mapping, index


def _yaml(path: Path) -> dict[str, Any]:
    lines = _Lines(path.read_text())
    document, index = _mapping(lines, 0, 0)
    assert index == len(lines.items), f"{path.name}: unparsed tail at {lines.items[index]!r}"
    return document


def test_the_subset_parser_reads_anchors_aliases_tags_and_continuations(tmp_path: Path) -> None:
    source = tmp_path / "c.yml"
    source.write_text(
        'x: &a\n  k: "v # not a comment"  # a comment\n'
        "y: &p\n  ./one:/two:rw\n"
        's:\n  one:\n    l: *a\n    p: *p\n    ports: !override\n      - "127.0.0.1:1:2"\n'
        '    f: ["a", b]\n    e:\n    n: 1\n'
    )
    assert _yaml(source) == {
        "x": {"k": "v # not a comment"},
        "y": "./one:/two:rw",
        "s": {
            "one": {
                "l": {"k": "v # not a comment"},
                "p": "./one:/two:rw",
                "ports": Tagged("!override", ["127.0.0.1:1:2"]),
                "f": ["a", "b"],
                "e": None,
                "n": "1",
            }
        },
    }
    source.write_text("a:\n  b: {inline: map}\n")
    with pytest.raises(ValueError):
        _yaml(source)


# --- the override against the base ----------------------------------------------------------


def _services(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return document["services"]


def _untagged(value: Any) -> Any:
    return value.value if isinstance(value, Tagged) else value


def _port_uses(node: Any, path: tuple[str, ...]) -> list[tuple[tuple[str, ...], Any]]:
    """(path, value) of every string, or list holding one, that names a port variable."""
    if isinstance(node, dict):
        return [use for key, value in node.items() for use in _port_uses(value, (*path, key))]
    if isinstance(node, list) and any(_PORT_USE.search(str(item)) for item in node):
        return [(path, node)]
    if isinstance(node, str) and _PORT_USE.search(node):
        return [(path, node)]
    return []


def _verify_default(value: Any) -> Any:
    """Return the base value with each port variable's live default swapped for the verify one."""
    if isinstance(value, list):
        return [_verify_default(item) for item in value]

    def swap(match: re.Match[str]) -> str:
        live, verify = _PORT_VARIABLES[match.group(1)]
        assert match.group(3) == live, f"the base's {match.group(1)} default moved: {value!r}"
        return "${" + match.group(1) + ":-" + verify + "}"

    return _PORT_USE.sub(swap, value)


def _at(tree: dict[str, Any], path: tuple[str, ...]) -> Any:
    for key in path:
        tree = tree.get(key, {}) if isinstance(tree, dict) else {}
    return tree


def test_every_service_of_the_base_gets_a_verify_container_name() -> None:
    base, override = _services(_yaml(_BASE)), _services(_yaml(_OVERRIDE))
    assert set(base) <= set(override), "a base service keeps its live container name"
    names = [service["container_name"] for service in override.values()]
    assert all(name.startswith("verify-") for name in names)
    assert len(set(names)) == len(names)
    assert not set(names) & {service.get("container_name") for service in base.values()}


def test_every_port_use_of_the_base_is_restated_on_its_verify_default() -> None:
    base, override = _yaml(_BASE), _yaml(_OVERRIDE)
    uses = _port_uses(base, ())
    assert len(uses) >= 10, "the base's port uses were not found"
    for path, value in uses:
        restated = _at(override, path)
        assert _untagged(restated) == _verify_default(value), path
        if isinstance(value, list):
            # A plain list in an override is appended to the base's list: it must replace it.
            assert isinstance(restated, Tagged), path
            assert restated.tag == "!override", path


def test_every_published_port_is_localhost_only_on_the_verify_ports() -> None:
    override = _services(_yaml(_OVERRIDE))
    published = [port for s in override.values() for port in _untagged(s.get("ports", []))]
    assert published == [
        "127.0.0.1:${REDIS_PORT:-26379}:6379",
        "127.0.0.1:${DOZZLE_PORT:-28080}:8080",
    ]


def test_the_logging_anchor_is_restated_unchanged() -> None:
    base, override = _yaml(_BASE), _yaml(_OVERRIDE)
    assert override["x-logging"] == base["x-logging"]
    for name in _RECORDERS:
        assert _services(override)[name]["logging"] == base["x-logging"]


@pytest.mark.parametrize("name", sorted(_RECORDERS))
def test_the_recorders_run_the_collector_image_as_their_own_uid(name: str) -> None:
    venue, collector_name, config_env = _RECORDERS[name]
    recorder = _services(_yaml(_OVERRIDE))[name]
    collector = _services(_yaml(_BASE))[collector_name]
    assert recorder["build"] == collector["build"]
    assert recorder["command"] == f"python3 -m verification.recorder --venue {venue}"
    assert (recorder["network_mode"], recorder["restart"]) == ("host", "always")
    uid, gid = recorder["user"].split(":")
    collector_uid, collector_gid = collector["user"].split(":")
    assert uid != collector_uid, "Story 31.10 cuts the collectors' connections by uid"
    assert gid == collector_gid, "the same group, so the collectors' side can delete its files"


@pytest.mark.parametrize("name", sorted(_RECORDERS))
def test_the_recorders_read_the_collectors_config_and_write_the_ledger(name: str) -> None:
    venue, collector_name, config_env = _RECORDERS[name]
    recorder = _services(_yaml(_OVERRIDE))[name]
    collector = _services(_yaml(_BASE))[collector_name]
    environment = recorder["environment"]
    assert environment["ERROR_LEDGER_DIR"] == "/app/errors_dir"
    assert environment["ERROR_LEDGER_SERVICE"] == name
    assert environment["VERIFY_DATA_DIR"] == "/app/verify_data"
    collector_config = collector["environment"][config_env]
    (source,) = [v.split(":")[0] for v in collector["volumes"] if f":{collector_config}:" in v]
    # The recorder mounts the directory of the collector's file (a host-side replacement of the
    # file stays visible) and reads that same file inside it.
    source_dir, file_name = source.rsplit("/", 1)
    mount = f"/app/{venue.lower()}_venue"
    assert environment[config_env] == f"{mount}/{file_name}"
    assert set(recorder["volumes"]) == {
        "./data/verification:/app/verify_data",
        "./data/errors:/app/errors_dir",
        f"{source_dir}:{mount}:ro",
    }


@pytest.mark.parametrize("name", sorted(_RECORDERS))
def test_the_recorders_get_time_to_close_their_streams_before_sigkill(name: str) -> None:
    grace = _services(_yaml(_OVERRIDE))[name]["stop_grace_period"]
    assert re.fullmatch(r"\d+s", grace), grace
    timing = Timing()
    worst = (
        timing.shutdown_grace_seconds
        + CONNECT_TIMEOUT_SECONDS
        + CLOSE_TIMEOUT_SECONDS
        + timing.supervise_seconds
    )
    assert int(grace[:-1]) > worst


def _copied_packages() -> set[str]:
    return set(
        re.findall(r"^COPY\s+platform/(\w+)\s+\./\1\s*$", _DOCKERFILE.read_text(), re.MULTILINE)
    )


@pytest.mark.parametrize("name", sorted(_RECORDERS))
def test_the_collector_image_ships_the_recorders_import_closure(name: str) -> None:
    command = _services(_yaml(_OVERRIDE))[name]["command"].split()
    module = command[command.index("-m") + 1]
    needed = {dotted.split(".")[0] for dotted in import_closure(module)}
    assert "verification" in needed
    assert needed <= _copied_packages(), sorted(needed - _copied_packages())


# --- the make targets -------------------------------------------------------------------------


def _makefile() -> tuple[dict[str, str], dict[str, list[str]]]:
    """(variables, target -> recipe lines), backslash continuations joined."""
    variables: dict[str, str] = {"PLATFORM_DIR": ""}  # `$(dir ...)` of the Makefile itself
    recipes: dict[str, list[str]] = {}
    target: str | None = None
    for line in _MAKEFILE.read_text().replace("\\\n", " ").splitlines():
        if assignment := re.match(r"^([A-Z_]+)\s*[:?]?=\s*(.*)$", line):
            variables.setdefault(assignment.group(1), assignment.group(2).strip())
        elif rule := re.match(r"^(\.?[A-Za-z][\w-]*):(?!=)(.*)$", line):
            target = rule.group(1)
            recipes[target] = [rule.group(2).strip()] if target == ".PHONY" else []
        elif line.startswith("\t") and target is not None:
            recipes[target].append(line.strip())
    return variables, recipes


def _expand(text: str, variables: dict[str, str]) -> str:
    for _ in range(5):
        text = re.sub(r"\$\((\w+)\)", lambda m: variables.get(m.group(1), m.group(0)), text)
    return text


def _verify_compose_lines(target: str) -> list[list[str]]:
    variables, recipes = _makefile()
    lines = [line.lstrip("@-") for line in recipes[target]]
    return [
        shlex.split(_expand(line, variables))
        for line in lines
        if line.startswith("$(VERIFY_COMPOSE)")
    ]


@pytest.mark.parametrize("target", ["verify-up", "verify-down", "verify-wipe"])
def test_the_verify_targets_exist_and_are_phony(target: str) -> None:
    _, recipes = _makefile()
    assert target in recipes
    assert target in recipes[".PHONY"][0].split()


@pytest.mark.parametrize("target", ["verify-up", "verify-down"])
def test_the_verify_targets_run_the_verify_project_on_the_verify_ports(target: str) -> None:
    (tokens,) = _verify_compose_lines(target)
    variables, _ = _makefile()
    assert tokens[:3] == ["REDIS_PORT=26379", "DATA_API_PORT=29100", "DOZZLE_PORT=28080"]
    compose = tokens[3:]
    assert compose[:4] == ["docker", "compose", "-p", "verify"]
    files = [compose[i + 1] for i, word in enumerate(compose) if word == "-f"]
    assert [Path(f).name for f in files] == ["docker-compose.yml", "docker-compose.verify.yml"]
    assert variables["VERIFY_REDIS_PORT"] == "26379"


def test_the_verify_paper_fleet_logs_its_signals_into_the_verification_dir() -> None:
    """
    Story 31.9: `live-paper` runs the parity fleet file over the base's config mount (compose merges
    volumes by container path, so the same target replaces the base's entry) and writes its signal
    logs into data/verification/bot_signals/live -- only that directory is mounted, never the rest
    of data/verification, which the reference recorders own (DATA-02).
    """
    override = _services(_yaml(_OVERRIDE))["live-paper"]
    base = _services(_yaml(_BASE))["live-paper"]
    assert override["container_name"] == "verify-live-paper"
    assert override["environment"] == {
        "REDIS_URL": "redis://127.0.0.1:${REDIS_PORT:-26379}",
        "BOT_SIGNAL_LOG_DIR": "/app/verify_data/bot_signals/live",
    }
    assert override["volumes"] == [
        "./bots/config.verify.toml:/app/bots/config.toml:ro",
        "./data/verification/bot_signals/live:/app/verify_data/bot_signals/live",
    ]
    targets = {volume.split(":")[1]: volume.split(":")[0] for volume in base["volumes"]}
    assert targets["/app/bots/config.toml"] == "./bots/config.toml"
    assert "/app/verify_data" not in targets
    assert (PLATFORM_DIR / "bots" / "config.verify.toml").is_file()


def test_verify_up_starts_exactly_the_verify_services_and_never_dydx() -> None:
    (tokens,) = _verify_compose_lines("verify-up")
    started = tokens[tokens.index("--build") + 1 :]
    assert set(started) == _VERIFY_SERVICES
    assert "collector" not in started
    assert tokens[tokens.index("up") + 1 : tokens.index("--build")] == ["-d"]


def test_verify_up_creates_every_bind_mounted_data_dir_first() -> None:
    variables, recipes = _makefile()
    recipe = [_expand(line, variables) for line in recipes["verify-up"]]
    override, base = _services(_yaml(_OVERRIDE)), _services(_yaml(_BASE))
    mounted = {
        volume.split(":")[0].removeprefix("./data/").rstrip("/")
        for name in _VERIFY_SERVICES
        for volume in base.get(name, {}).get("volumes", []) + override[name].get("volumes", [])
        if volume.startswith("./data/") and volume.split(":")[0].count(".toml") == 0
    }
    (mkdir,) = [line for line in recipe if line.startswith("cd data && mkdir -p ")]
    assert mkdir.startswith("cd data && mkdir -p ")
    created = set(mkdir.removeprefix("cd data && mkdir -p ").split())
    assert mounted <= created, sorted(mounted - created)
    writable = " ".join(line for line in recipe if line.startswith("chmod g+w"))
    assert "data/verification" in writable
    assert "data/errors" in writable


def test_verify_wipe_refuses_a_running_stack_and_keeps_the_plan_and_preferences() -> None:
    variables, recipes = _makefile()
    recipe = " ".join(_expand(line, variables) for line in recipes["verify-wipe"])
    assert "com.docker.compose.project=verify" in recipe
    assert "exit 1" in recipe
    for kept in ("dydx_config.toml", "preferences", "alerts"):
        assert f"! -name {kept}" in recipe
    assert "-print" in recipe, "it lists what it deletes"


def test_verify_wipe_deletes_only_a_data_dir_verify_up_marked() -> None:
    variables, recipes = _makefile()
    up = [_expand(line, variables) for line in recipes["verify-up"]]
    wipe = [_expand(line, variables) for line in recipes["verify-wipe"]]
    marker = variables["VERIFY_MARKER"]
    assert f"touch data/{marker}" in up
    guard = next(i for i, line in enumerate(wipe) if f"[ ! -e data/{marker} ]" in line)
    assert guard < next(i for i, line in enumerate(wipe) if "rm -rf" in line)
    assert f"! -name {marker}" in _expand(variables["VERIFY_KEEP"], variables), "stays marked"


def test_verify_up_and_down_refuse_another_checkouts_verify_stack() -> None:
    variables, recipes = _makefile()
    others = variables["VERIFY_OTHER_CHECKOUTS"]
    assert "com.docker.compose.project=verify" in others
    assert "com.docker.compose.project.working_dir" in others
    assert "realpath" in others
    for target in ("verify-up", "verify-down"):
        recipe = recipes[target]
        guard = next(i for i, line in enumerate(recipe) if "$(VERIFY_OTHER_CHECKOUTS)" in line)
        assert guard < next(i for i, line in enumerate(recipe) if "$(VERIFY_COMPOSE)" in line)
        assert "exit 1" in recipe[guard]


def test_verify_up_refuses_a_host_group_the_recorders_cannot_write_as() -> None:
    _, recipes = _makefile()
    (guard,) = [line for line in recipes["verify-up"] if "id -g" in line]
    assert "!= 1000" in guard
    assert "exit 1" in guard


def test_make_test_runs_the_verification_tests() -> None:
    _, recipes = _makefile()
    assert "verification/tests" in " ".join(recipes["test"]).split()


def test_verify_up_and_wipe_refuse_while_other_containers_use_this_data_dir() -> None:
    variables, recipes = _makefile()
    users = variables["VERIFY_DATA_USERS"]
    for probe in (
        "docker ps -q",
        "docker inspect",
        ".Mounts",
        "com.docker.compose.project",
        "realpath",
    ):
        assert probe in users, probe
    up = " ".join(recipes["verify-up"])
    assert "$(VERIFY_DATA_USERS)" in up
    assert """$$2 != "verify\"""" in up, "verify-up refuses only other projects' containers"
    assert up.index("$(VERIFY_DATA_USERS)") < up.index("$(VERIFY_COMPOSE)")
    wipe = recipes["verify-wipe"]
    guard = next(i for i, line in enumerate(wipe) if "$(VERIFY_DATA_USERS)" in line)
    assert guard < next(i for i, line in enumerate(wipe) if "rm -rf" in line)


def test_the_verify_archive_can_never_sync_to_the_live_backup_target() -> None:
    base = _services(_yaml(_BASE))["archive"]["environment"]
    archive = _services(_yaml(_OVERRIDE))["archive"]["environment"]
    for variable in ("RCLONE_REMOTE", "RCLONE_BUCKET"):
        assert variable in base, "the base still reads it: the override must blank it"
        assert archive[variable] == ""


def test_the_verify_archive_runs_verify_day_over_the_recorders_data() -> None:
    """
    Story 31.11: the verify stack's nightly `verify_day` step reads the recorders' raw files
    read-only, writes only its scratch, reads the coverage record beside the catalog, the venue
    plans the recorders read, and the verify data_api on its loopback port.
    """
    base = _services(_yaml(_BASE))["archive"]
    archive = _services(_yaml(_OVERRIDE))["archive"]
    environment = archive["environment"]
    assert environment["VERIFY_DATA_DIR"] == "/app/verify_data"
    assert environment["VERIFY_DATA_API_URL"] == "http://127.0.0.1:${DATA_API_PORT:-29100}"
    assert archive["volumes"] == [
        "./data/verification/raw:/app/verify_data/raw:ro",
        "./data/verification/scratch:/app/verify_data/scratch",
        "./data/coverage:/app/coverage:ro",
        "./capture/venues/bybit:/app/bybit_venue:ro",
        "./capture/venues/hyperliquid:/app/hyperliquid_venue:ro",
    ]
    # The tools read `<catalog>/../coverage`: the base's catalog mount decides where that is.
    base_targets = {volume.split(":")[1]: volume.split(":")[0] for volume in base["volumes"]}
    assert base["environment"]["CATALOG_PATH"] == "/app/catalog"
    assert base_targets["/app/catalog"] == "./data/catalog"
    # Compose merges volumes by container path: none of these may replace a base mount.
    assert not {volume.split(":")[1] for volume in archive["volumes"]} & set(base_targets)
    # The same plan files the recorders read, through the same directory mounts.
    for name, (venue, _, config_env) in _RECORDERS.items():
        recorder = _services(_yaml(_OVERRIDE))[name]
        assert environment[config_env] == recorder["environment"][config_env]
        mount = f"/app/{venue.lower()}_venue"
        (source,) = [v.split(":")[0] for v in recorder["volumes"] if f":{mount}:" in v]
        assert f"{source}:{mount}:ro" in archive["volumes"]
    assert "archive" in _copied_packages()
    assert "verification" in _copied_packages()


def test_verify_up_makes_a_raw_dir_it_created_writable_by_the_recorders() -> None:
    variables, recipes = _makefile()
    recipe = [_expand(line, variables) for line in recipes["verify-up"]]
    (raw,) = [line for line in recipe if "data/verification/raw" in line]
    assert raw.startswith("find data/verification/raw -maxdepth 0 -user")
    assert raw.endswith("-exec chmod g+w {} +")


@pytest.mark.parametrize("target", ["verify-up", "verify-down", "verify-wipe"])
def test_every_verify_target_checks_the_docker_daemon_answers_first(target: str) -> None:
    variables, recipes = _makefile()
    assert recipes[target][0] == "@$(VERIFY_DOCKER_UP)"
    assert "exit 1" in variables["VERIFY_DOCKER_UP"]


def test_the_checkout_guards_match_this_checkout_given_and_resolved() -> None:
    variables, _ = _makefile()
    assert '-e "$(VERIFY_HERE)"' in variables["VERIFY_OTHER_CHECKOUTS"]
    users = variables["VERIFY_DATA_USERS"]
    assert 'given="$(VERIFY_HERE)/data"' in users
    assert 'index(d, m "/") == 1' in users, "a mount of a directory above data/ reaches it too"


def test_verify_up_never_marks_a_data_dir_holding_unmarked_data() -> None:
    variables, recipes = _makefile()
    unmarked = variables["VERIFY_UNMARKED_DATA"]
    assert "[ ! -e $(PLATFORM_DIR)data/$(VERIFY_MARKER) ]" in unmarked
    assert "$(VERIFY_KEEP)" in unmarked
    up = recipes["verify-up"]
    guard = next(i for i, line in enumerate(up) if "$(VERIFY_UNMARKED_DATA)" in line)
    assert "exit 1" in up[guard]
    assert guard < next(
        i for i, line in enumerate(up) if "$(VERIFY_MARKER)" in line and "touch" in line
    )
    assert guard < next(i for i, line in enumerate(up) if "$(VERIFY_COMPOSE)" in line)
