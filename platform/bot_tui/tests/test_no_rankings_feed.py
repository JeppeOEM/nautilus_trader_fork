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
Story 25.1a: rankings are web-only. bot_tui is a control surface for bots and the
collector, so no module under `bot_tui/` (tests included) may subscribe to, publish to
or read the ranking feeds, or import the ranking read models. A plain text scan rather
than an import check: a channel name is a string literal, and re-adding one is exactly
how a TUI rankings view would come back. Imports are the other half and are enforced
structurally, not here: `tests/test_boundaries.py`'s graph has no `(BOT_TUI, VIEWS)` edge, so
any `views` import from `bot_tui` fails there however it is spelled.

This file is the one exemption, since it has to spell the names out.
"""

from pathlib import Path


_BOT_TUI_DIR = Path(__file__).resolve().parent.parent

_FORBIDDEN = (
    "rankings:live",
    "snapshots:raw",
    "ranking:control",
    "views.coin_detail",
    "views.ranking_columns",
)


def test_no_bot_tui_module_references_a_ranking_feed_or_read_model() -> None:
    this_file = Path(__file__).resolve()
    sources = [p for p in sorted(_BOT_TUI_DIR.rglob("*.py")) if p.resolve() != this_file]
    assert sources, f"no bot_tui sources found under {_BOT_TUI_DIR}"
    offenders = [
        f"{path.relative_to(_BOT_TUI_DIR)}: {needle}"
        for path in sources
        for needle in _FORBIDDEN
        if needle in path.read_text()
    ]
    assert offenders == []
