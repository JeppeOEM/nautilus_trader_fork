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
Tests for bot_tui.app's pure command-dispatch/pop-back logic -- Story 4.1, AC3/AC4.

These are the only two functions in app.py that don't require a real urwid screen;
everything else (MainLoop wiring, widget construction, async redraw) is exercised via
the manual smoke check documented in the story's Task 6, not automated here -- see
Story 4.1's Dev Notes "Testing strategy: pure logic vs. urwid wiring."
"""

from bot_tui.app import _QUIT_SENTINEL
from bot_tui.app import _dispatch_command
from bot_tui.app import _parse_collector_command
from bot_tui.app import _pop_view


def test_dispatch_recognized_command_switches_view_and_pushes_history() -> None:
    new_view, stack, echo = _dispatch_command("coins", [], "bots")
    assert (new_view, stack, echo) == ("bots", ["coins"], None)


def test_dispatch_same_view_command_is_a_no_op_push() -> None:
    new_view, stack, echo = _dispatch_command("coins", [], "coins")
    assert (new_view, stack, echo) == ("coins", [], None)


def test_dispatch_unrecognized_command_echoes_and_does_not_navigate() -> None:
    new_view, stack, echo = _dispatch_command("coins", [], "frobnicate")
    assert new_view == "coins"
    assert stack == []
    assert echo == "unknown command: frobnicate"


def test_dispatch_empty_command_is_unrecognized() -> None:
    new_view, stack, echo = _dispatch_command("coins", [], "")
    assert echo == "unknown command: "


def test_dispatch_h_alias_opens_help_same_as_full_name() -> None:
    new_view, stack, echo = _dispatch_command("coins", [], "h")
    assert (new_view, stack, echo) == ("help", ["coins"], None)


def test_dispatch_quit_returns_quit_sentinel() -> None:
    new_view, stack, echo = _dispatch_command("coins", [], "q")
    assert new_view == _QUIT_SENTINEL
    assert echo is None


def test_dispatch_data_command_opens_collector_view() -> None:
    new_view, stack, echo = _dispatch_command("coins", [], "data")
    assert (new_view, stack, echo) == ("collector", ["coins"], None)


def test_dispatch_old_collector_command_no_longer_recognized() -> None:
    new_view, stack, echo = _dispatch_command("coins", [], "collector")
    assert new_view == "coins"
    assert echo == "unknown command: collector"


def test_parse_collector_command_pintop() -> None:
    assert _parse_collector_command("pintop") == ("pin_top_liquid", None)


def test_parse_collector_command_start_with_id() -> None:
    assert _parse_collector_command("start BTC-USD-PERP.DYDX") == ("start", "BTC-USD-PERP.DYDX")


def test_parse_collector_command_start_without_id_falls_through() -> None:
    assert _parse_collector_command("start ") is None


def test_parse_collector_command_old_refresh_no_longer_recognized() -> None:
    assert _parse_collector_command("refresh") is None


def test_pop_view_from_non_root_returns_to_previous() -> None:
    new_view, stack = _pop_view("bots", ["coins"])
    assert (new_view, stack) == ("coins", [])


def test_pop_view_at_root_is_a_no_op() -> None:
    new_view, stack = _pop_view("coins", [])
    assert (new_view, stack) == ("coins", [])


def test_pop_view_never_raises_on_empty_stack() -> None:
    # AC4: esc must never exit/raise, even repeatedly at the root.
    view: str = "coins"
    stack: list[str] = []
    for _ in range(3):
        view, stack = _pop_view(view, stack)
    assert (view, stack) == ("coins", [])
