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
Deprecated re-export shim (Story 24.1): `ml_signals.candle_store` moved to the `candles` context.

Pure re-export, defines nothing: every name here *is* the `candles` object (a copied fold or schema
would be a second source of truth for the same bars). Import from `candles` instead, e.g.
`from candles.application.queries import window`,
`from candles.infrastructure.sqlite_store import connect_ro`,
`from candles.domain.fold import BAR_SECONDS`.

`apply_batch` is gone rather than re-exported: the store is fed one instrument at a time through
`candles.application.sink.CandleSink`, so its single-transaction-per-flush shape no longer exists.
"""

import warnings

from candles.application.queries import latest
from candles.application.queries import oldest_t
from candles.application.queries import window
from candles.domain.fold import BAR_SECONDS
from candles.domain.fold import RETAIN_DAYS
from candles.domain.fold import fold_arrays
from candles.infrastructure.sqlite_store import apply_seconds
from candles.infrastructure.sqlite_store import connect_ro
from candles.infrastructure.sqlite_store import connect_rw
from candles.infrastructure.sqlite_store import mark_verified
from candles.infrastructure.sqlite_store import prune
from candles.infrastructure.sqlite_store import rebuild
from candles.infrastructure.sqlite_store import rebuild_from_arrays
from candles.infrastructure.sqlite_store import verified_status
from candles.infrastructure.sqlite_store import watermarks


__all__ = [
    "BAR_SECONDS",
    "RETAIN_DAYS",
    "apply_seconds",
    "connect_ro",
    "connect_rw",
    "fold_arrays",
    "latest",
    "mark_verified",
    "oldest_t",
    "prune",
    "rebuild",
    "rebuild_from_arrays",
    "verified_status",
    "watermarks",
    "window",
]

REMOVE_AFTER = "24-3-alerting-context-as-forming-bar-observer"

# A name whose successor changed shape is never served: a stale caller fails loudly instead of
# getting a silently different transaction boundary.
_REPLACED_NAMES: dict[str, str] = {
    "apply_batch": "candles.application.sink.CandleSink.apply(instrument_id, rows), per instrument",
}


def __getattr__(name: str) -> object:
    if name in _REPLACED_NAMES:
        raise AttributeError(
            f"ml_signals.candle_store.{name} was replaced by {_REPLACED_NAMES[name]} (Story 24.1)"
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "ml_signals.candle_store moved to the candles context (Story 24.1); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
