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
Deprecated re-export shim (Story 25.3): `live_paper.node`'s entrypoint moved to the bots
context's composition root -- run `python3 -m bots`. `build_node` moved to
`bots.infrastructure.nautilus_host` with a changed shape (it returns the hosted bots and
schedules no task), so it is not served here.

Pure re-export, defines nothing: `main` *is* `bots.__main__.main`.
"""

import warnings

from bots.__main__ import main


__all__ = [
    "main",
]

REMOVE_AFTER = "26-1-livebook-tradeintake-feedgroup-pure-secondsampler-in-place"


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "live_paper.node moved to bots (Story 25.3; run `python3 -m bots`); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)

if __name__ == "__main__":
    # Under `python -m` the import-time warning above is attributed to runpy's frame and hidden
    # by the default filters; this one is issued from `__main__`, where it is shown.
    warnings.warn(
        f"python -m {__spec__.name} is deprecated: use python -m bots (Story 25.3)",
        DeprecationWarning,
        stacklevel=1,
    )
    main()
