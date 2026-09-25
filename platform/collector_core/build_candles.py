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
Deprecated re-export shim (Story 24.1): `collector_core.build_candles` moved to the `candles`
context, and its CLI is now `python -m candles.rebuild` (same flags, same behaviour).

Pure re-export, defines nothing: every name here *is* the `candles` object. Import from `candles`
instead, e.g. `from candles.application.rebuild import rebuild_instrument`. `_parse_date_ns` is
public there as `parse_date_ns` -- a `_private` name must not cross a context.
"""

import warnings

from candles.application.rebuild import all_instruments
from candles.application.rebuild import data_range_ns
from candles.application.rebuild import day_chunks
from candles.application.rebuild import parse_date_ns
from candles.application.rebuild import rebuild_instrument
from candles.application.rebuild import venue_instruments
from candles.rebuild import main


__all__ = [
    "all_instruments",
    "data_range_ns",
    "day_chunks",
    "main",
    "parse_date_ns",
    "rebuild_instrument",
    "venue_instruments",
]

REMOVE_AFTER = "24-3-alerting-context-as-forming-bar-observer"

_REPLACED_NAMES: dict[str, str] = {
    "_parse_date_ns": "candles.application.rebuild.parse_date_ns",
}


def __getattr__(name: str) -> object:
    if name in _REPLACED_NAMES:
        raise AttributeError(
            f"collector_core.build_candles.{name} was replaced by {_REPLACED_NAMES[name]} "
            "(Story 24.1)"
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_MESSAGE = (
    "collector_core.build_candles moved to candles (Story 24.1); run `python -m candles.rebuild`; "
    f"this shim is removed after {REMOVE_AFTER}"
)

# Attributed to the importing module, not to importlib's frames.
warnings.warn(_MESSAGE, DeprecationWarning, skip_file_prefixes=("<frozen importlib",))


if __name__ == "__main__":  # `python -m collector_core.build_candles` still runs the CLI
    # The warning above does not reach this caller: under `-m` it is attributed to `<frozen runpy>`,
    # so the default `__main__`-only DeprecationWarning filter drops it -- and an operator or cron
    # line still typing the old command is the one reader this shim exists for. Re-emit it with the
    # filter forced, in a process whose only job is to run the CLI once.
    warnings.simplefilter("always", DeprecationWarning)
    warnings.warn(_MESSAGE, DeprecationWarning, stacklevel=1)
    main()
