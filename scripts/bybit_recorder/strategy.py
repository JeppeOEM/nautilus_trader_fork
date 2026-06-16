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
Re-export shim for the (now exchange-agnostic) recorder strategy (DYDX-01).

``RecorderStrategy`` / ``RecorderStrategyConfig`` moved to
``scripts.common_recorder.strategy``; they are re-exported here so existing imports
like ``from scripts.bybit_recorder.strategy import RecorderStrategy`` keep working.

This module also re-exports the Bybit ``load_recorder_config`` and registers it as
the common strategy's module-level default config loader, so a ``RecorderStrategy``
built via this package (e.g. in unit tests) resolves the Bybit loader during
hot-reload even without an explicit per-instance ``set_config_loader`` call. The
PRIMARY wiring remains the per-instance injection in ``recorder.py``.
"""

from scripts.bybit_recorder.config import load_recorder_config
from scripts.common_recorder.strategy import RecorderStrategy
from scripts.common_recorder.strategy import RecorderStrategyConfig
from scripts.common_recorder.strategy import set_default_config_loader


__all__ = ["RecorderStrategy", "RecorderStrategyConfig"]


def _default_loader(path):
    # Late-bound lookup of this module's `load_recorder_config` so that a test
    # patching `scripts.bybit_recorder.strategy.load_recorder_config` is honored
    # at reload time (the strategy never imports a venue config module directly).
    import sys

    return sys.modules[__name__].load_recorder_config(path)


# Register the Bybit loader as the common strategy's module-level default so the
# back-compat re-export path (and unit tests) resolve it without explicit wiring.
set_default_config_loader(_default_loader)
