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
"""The recording plan: the collector's own `config.toml`, read as data."""

import os
from pathlib import Path

import pytest
from observability import error_ledger

from verification import recorder
from verification.application import sites
from verification.domain.plan_file import RecordingPlan
from verification.domain.plan_file import parse_plan
from verification.recorder import config_path
from verification.recorder import data_dir
from verification.recorder import read_plan_file
from verification.recorder import retain_days
from verification.recorder import startup_plan


_PLATFORM = Path(__file__).resolve().parents[2]


def test_instruments_are_deduped_in_order_minus_exclude() -> None:
    text = (
        'environment = "testnet"\ncatalog_path = "/app/catalog"\n'
        'instruments = ["B-LINEAR.BYBIT", "A-SPOT.BYBIT", "B-LINEAR.BYBIT", "C-LINEAR.BYBIT"]\n'
        'exclude = ["C-LINEAR.BYBIT", "Z-LINEAR.BYBIT"]\n'
    )
    assert parse_plan(text, "BYBIT") == RecordingPlan(
        venue="BYBIT", environment="testnet", instruments=("B-LINEAR.BYBIT", "A-SPOT.BYBIT")
    )


def test_environment_defaults_to_mainnet_and_an_empty_plan_is_valid() -> None:
    assert parse_plan("instruments = []", "HYPERLIQUID") == RecordingPlan(
        "HYPERLIQUID", "mainnet", ()
    )


def test_an_empty_plan_file_still_reads_as_a_plan_that_records_nothing(tmp_path: Path) -> None:
    """The recorder's contract: only the verification day tools refuse an empty plan."""
    path = tmp_path / "config.toml"
    path.write_text("instruments = []\n")
    assert read_plan_file(path, "BYBIT").instruments == ()


@pytest.mark.parametrize("text", ["", 'environment = "mainnet"\ncatalog_path = "/app/c"\n'])
def test_a_file_read_mid_save_is_refused_not_an_empty_plan(text: str) -> None:
    """The plan store truncates then writes: a read in between must keep the last good plan."""
    with pytest.raises(ValueError, match="no `instruments` key"):
        parse_plan(text, "BYBIT")


@pytest.mark.parametrize(
    "text",
    [
        'instruments = "BTCUSDT-LINEAR.BYBIT"',
        "instruments = [1, 2]",
        '[instruments]\nid = "BTCUSDT-LINEAR.BYBIT"',
        'instruments = []\nexclude = "BTCUSDT-LINEAR.BYBIT"',
        "environment = 1",
        'instruments = ["SOL-USD-PERP.HYPERLIQUID"]',
        "instruments = [",
    ],
)
def test_a_malformed_plan_is_refused(text: str) -> None:
    with pytest.raises(ValueError):
        parse_plan(text, "BYBIT")


@pytest.mark.parametrize("venue", ["BYBIT", "HYPERLIQUID"])
def test_the_committed_venue_configs_parse(venue: str) -> None:
    path = _PLATFORM / "capture" / "venues" / venue.lower() / "config.toml"
    plan = read_plan_file(path, venue)
    assert plan.instruments, f"{path} collects nothing"
    assert plan.environment == "mainnet"


def test_config_path_uses_the_collectors_variable_and_default() -> None:
    assert config_path("BYBIT", {"BYBIT_COLLECTOR_CONFIG": "/app/b.toml"}) == Path("/app/b.toml")
    assert config_path("HYPERLIQUID", {}) == (
        _PLATFORM / "capture" / "venues" / "hyperliquid" / "config.toml"
    )


def test_settings_refuse_a_missing_root_and_a_bad_retention() -> None:
    assert data_dir({"VERIFY_DATA_DIR": "/app/verify_data"}) == Path("/app/verify_data")
    assert retain_days({}) == 7
    assert retain_days({"VERIFY_RETAIN_DAYS": "3"}) == 3
    with pytest.raises(SystemExit):
        data_dir({})
    assert retain_days({"VERIFY_RETAIN_DAYS": "36500"}) == 36500
    for bad in (
        "0",
        "-1",
        "1.5",
        "seven",
        "",
        " 7",
        "\u00b2",
        "\u0663",
        "36501",
        "0" * 400 + "9" * 400,
        "9" * 5000,  # past `int()`'s digit limit: still the SystemExit, not a bare ValueError
    ):
        with pytest.raises(SystemExit):
            retain_days({"VERIFY_RETAIN_DAYS": bad})


@pytest.mark.parametrize(
    "text", ["", 'environment = "devnet"\ninstruments = ["BTCUSDT-LINEAR.BYBIT"]\n']
)
def test_a_plan_refused_at_start_is_ledgered_before_the_process_exits(
    tmp_path: Path, text: str
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(text)
    ledgered: list[str] = []

    def ledger(site: str, detail: str = "", exc: BaseException | None = None) -> None:
        ledgered.append(site)

    with pytest.raises(ValueError):
        startup_plan(path, "BYBIT", ledger)
    assert ledgered == [sites.PLAN]


def test_a_missing_plan_file_at_start_is_ledgered(tmp_path: Path) -> None:
    ledgered: list[str] = []

    def ledger(site: str, detail: str = "", exc: BaseException | None = None) -> None:
        ledgered.append(site)

    with pytest.raises(OSError):
        startup_plan(tmp_path / "absent.toml", "BYBIT", ledger)
    assert ledgered == [sites.PLAN]


def test_the_environment_is_read_case_insensitively_as_the_collector_reads_it() -> None:
    plan = parse_plan('environment = "Mainnet"\ninstruments = []\n', "BYBIT")
    assert plan.environment == "mainnet"


def test_an_invalid_environment_setting_is_ledgered_before_the_process_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VERIFY_DATA_DIR", raising=False)
    before = error_ledger.counts().get(sites.PLAN, 0)
    umask = os.umask(0o022)
    try:
        with pytest.raises(SystemExit):
            recorder.main(["--venue", "BYBIT"])
    finally:
        os.umask(umask)
    assert error_ledger.counts().get(sites.PLAN, 0) == before + 1
