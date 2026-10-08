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
Story 33.12: `chart_watchlist.toml` -- the chart page's pinned instruments, validated by
`views.preferences.validate_watchlist` (every refusal names its entry, nothing is dropped or
deduplicated) and written whole by `save_watchlist`.
"""

import tomllib
from pathlib import Path
from typing import Any

import pytest

from views.preferences import MAX_INSTRUMENT_ID_LENGTH
from views.preferences import MAX_WATCHLIST
from views.preferences import WatchlistError
from views.preferences import load_watchlist
from views.preferences import save_watchlist
from views.preferences import validate_watchlist


_BYBIT_BTC = "BTCUSDT-LINEAR.BYBIT"
_HL_SOL = "SOL-USD-PERP.HYPERLIQUID"
_BYBIT_SPOT = "ETHUSDT-SPOT.BYBIT"


def _file(tmp_path: Path) -> Path:
    return tmp_path / "chart_watchlist.toml"


def _refusal(body: Any) -> str:
    with pytest.raises(WatchlistError) as caught:
        validate_watchlist(body)
    return caught.value.field


def test_a_valid_body_becomes_the_ids_in_the_operators_order() -> None:
    body = {"instruments": [_HL_SOL, _BYBIT_BTC, _BYBIT_SPOT]}

    assert validate_watchlist(body) == [_HL_SOL, _BYBIT_BTC, _BYBIT_SPOT]


def test_the_watchlist_round_trips_through_the_file(tmp_path: Path) -> None:
    ids = [_HL_SOL, _BYBIT_BTC, _BYBIT_SPOT]

    save_watchlist(ids, _file(tmp_path))

    assert load_watchlist(_file(tmp_path)) == ids


def test_the_written_text_is_pinned(tmp_path: Path) -> None:
    save_watchlist([_BYBIT_BTC, _HL_SOL], _file(tmp_path))

    assert _file(tmp_path).read_text() == (
        'v = 1\ninstruments = [\n    "BTCUSDT-LINEAR.BYBIT",\n    "SOL-USD-PERP.HYPERLIQUID",\n]\n'
    )


def test_an_empty_list_is_stored_and_loads_empty(tmp_path: Path) -> None:
    save_watchlist([], _file(tmp_path))

    assert _file(tmp_path).read_text() == "v = 1\ninstruments = []\n"
    assert load_watchlist(_file(tmp_path)) == []


def test_a_missing_file_loads_empty(tmp_path: Path) -> None:
    assert load_watchlist(tmp_path / "absent.toml") == []


def test_a_list_of_the_maximum_length_is_kept(tmp_path: Path) -> None:
    ids = [f"C{i}-USD-PERP.HYPERLIQUID" for i in range(MAX_WATCHLIST)]

    save_watchlist(ids, _file(tmp_path))

    assert load_watchlist(_file(tmp_path)) == ids


def test_an_id_of_the_maximum_length_is_kept() -> None:
    iid = "A" * (MAX_INSTRUMENT_ID_LENGTH - 2) + ".X"

    assert validate_watchlist({"instruments": [iid]}) == [iid]


@pytest.mark.parametrize(
    "text",
    [
        "v = 2\ninstruments = []\n",  # another version
        "instruments = []\n",  # no version
        "v = true\ninstruments = []\n",  # a bool version (`True == 1` in Python)
        "v = 1.0\ninstruments = []\n",  # a float version
        "v = 1\ninstruments = []\nstray = 1\n",  # a stray key
        'v = 1\ninstruments = ["BTCUSDT"]\n',  # a malformed entry
        'v = 1\ninstruments = "BTCUSDT-LINEAR.BYBIT"\n',  # not a list
    ],
)
def test_a_file_that_is_not_a_v1_watchlist_file_is_refused(tmp_path: Path, text: str) -> None:
    _file(tmp_path).write_text(text)

    with pytest.raises(WatchlistError):
        load_watchlist(_file(tmp_path))


def test_a_file_without_instruments_loads_empty(tmp_path: Path) -> None:
    _file(tmp_path).write_text("v = 1\n")

    assert load_watchlist(_file(tmp_path)) == []


def test_an_unparseable_file_raises(tmp_path: Path) -> None:
    _file(tmp_path).write_text("v = = 1")

    with pytest.raises(tomllib.TOMLDecodeError):
        load_watchlist(_file(tmp_path))


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ([_BYBIT_BTC], "instruments"),  # not the {"instruments": [...]} object
        ({}, "instruments"),
        ({"instruments": [], "extra": 1}, "instruments"),
        ({"instruments": _BYBIT_BTC}, "instruments"),  # a string, not a list
        ({"instruments": None}, "instruments"),
        (
            {"instruments": [f"C{i}-USD-PERP.HYPERLIQUID" for i in range(MAX_WATCHLIST + 1)]},
            "instruments",
        ),
        ({"instruments": [_BYBIT_BTC, 7]}, "instruments[1]"),
        ({"instruments": [None]}, "instruments[0]"),
        ({"instruments": [""]}, "instruments[0]"),
        ({"instruments": [_HL_SOL, _BYBIT_BTC, "BTCUSDT"]}, "instruments[2]"),  # no .VENUE
        ({"instruments": ["BTCUSDT."]}, "instruments[0]"),  # an empty venue
        ({"instruments": [".BYBIT"]}, "instruments[0]"),  # an empty symbol
        ({"instruments": ["A" * MAX_INSTRUMENT_ID_LENGTH + ".X"]}, "instruments[0]"),
        ({"instruments": [_BYBIT_BTC, _HL_SOL, _BYBIT_BTC]}, "instruments[2]"),  # a duplicate
    ],
)
def test_every_refusal_names_the_entry(body: Any, field: str) -> None:
    assert _refusal(body) == field


def test_a_refusal_message_carries_the_entry_path() -> None:
    with pytest.raises(WatchlistError) as caught:
        validate_watchlist({"instruments": [_BYBIT_BTC, "BTCUSDT"]})

    assert str(caught.value).startswith("instruments[1]: ")


def test_save_refuses_an_invalid_list_and_leaves_the_old_file(tmp_path: Path) -> None:
    save_watchlist([_BYBIT_BTC], _file(tmp_path))
    before = _file(tmp_path).read_bytes()

    with pytest.raises(WatchlistError):
        save_watchlist([_HL_SOL, _HL_SOL], _file(tmp_path))

    assert _file(tmp_path).read_bytes() == before
