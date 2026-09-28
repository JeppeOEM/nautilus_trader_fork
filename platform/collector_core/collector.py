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
Deprecated re-export shim (Story 26.2): `collector_core.collector` moved to
`capture.application.capture_service`, `capture.infrastructure.parquet_writer`.

`Collector` is not served: its successor `CaptureService` changed shape (the client is a factory,
`venue=` is required, the `VENUE` ClassVar is gone, and it is never subclassed -- a venue is wired
by its composition root, `capture/venues/<v>/__main__.py`'s `build_capture`). A stale caller
fails loudly naming it (`_REPLACED_NAMES`), never with a TypeError deep inside `__init__`. The
refusal is an `ImportError`, not an `AttributeError`: `from collector_core.collector import
Collector` turns an `AttributeError` into a generic "cannot import name" and drops the message.

Pure re-export otherwise: every served name *is* its successor object.
"""

import warnings

from capture.application.capture_service import run_forever
from capture.infrastructure.parquet_writer import quarantine_corrupt_parquet


__all__ = [
    "quarantine_corrupt_parquet",
    "run_forever",
]

REMOVE_AFTER = "26-3-closeout-shims-gone-spines-reconciled"

_REPLACED_NAMES: dict[str, str] = {
    "Collector": (
        "capture.application.capture_service.CaptureService (a client factory plus venue=, "
        "wired by capture/venues/<v>/__main__.py's build_capture, never subclassed)"
    ),
}


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "collector_core.collector moved to "
    "capture (Story 26.2); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)


def __getattr__(name: str) -> object:
    if name in _REPLACED_NAMES:
        raise ImportError(
            f"collector_core.collector.{name} was replaced by {_REPLACED_NAMES[name]}",
            name=__name__,
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
