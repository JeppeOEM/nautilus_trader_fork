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
Deprecated re-export shim (Story 24.4): `ml_signals.strategies.backtest_ofi` moved to the research
context (`research.strategies.backtest_ofi`).

Pure re-export, defines nothing: every name here *is* the `research.strategies.backtest_ofi` object.
Import from `research.strategies.backtest_ofi` instead, e.g. `from research.strategies.backtest_ofi
import run`.

`python -m ml_signals.strategies.backtest_ofi` still runs it; use
`python -m research.strategies.backtest_ofi`.
"""

import warnings

from research.strategies.backtest_ofi import run


__all__ = [
    "run",
]

REMOVE_AFTER = "25-2-ranking-context-rankingboard-replaces-module-globals"


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "ml_signals.strategies.backtest_ofi moved to research.strategies.backtest_ofi (Story 24.4); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)


if __name__ == "__main__":
    import runpy

    runpy.run_module("research.strategies.backtest_ofi", run_name="__main__")
