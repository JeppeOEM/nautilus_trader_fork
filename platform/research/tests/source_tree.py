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
`platform/tests/_source_tree` for research's tests: the checkout they scan and the sprint board
their expiring exemption tables are judged against (Stories 24.4, 27.2). Loaded by path, since
`platform/tests` is not a package these tests can import.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load() -> ModuleType:
    if "_source_tree" in sys.modules:
        return sys.modules["_source_tree"]
    path = Path(__file__).resolve().parents[2] / "tests" / "_source_tree.py"
    spec = importlib.util.spec_from_file_location("_source_tree", path)
    assert spec is not None, path
    assert spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    sys.modules["_source_tree"] = module
    spec.loader.exec_module(module)
    return module


SOURCE_TREE = _load()
