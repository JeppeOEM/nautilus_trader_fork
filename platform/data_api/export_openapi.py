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
Dumps data_api's OpenAPI schema to stdout, for `frontend/`'s OpenAPI->TypeScript codegen
(AD-F5) -- no need to spin up a live server just to regenerate types.

Usage (from platform/): PYTHONPATH=. python3 -m data_api.export_openapi > frontend/openapi.json
Then (from platform/frontend/): npm run codegen
"""

import json
import sys

from data_api.app import app


def main() -> None:
    json.dump(app.openapi(), sys.stdout, indent=2)


if __name__ == "__main__":
    main()
