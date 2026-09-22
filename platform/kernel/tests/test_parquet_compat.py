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
"""`kernel.parquet_compat`: the one zstd `write_table` default, installed at most once."""

from typing import Any

import pyarrow.parquet as pq
import pytest

from kernel.parquet_compat import apply_zstd_default


def test_installed_once_whatever_the_number_of_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def plain(*_args: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(pq, "write_table", plain)
    apply_zstd_default()
    wrapped = pq.write_table
    apply_zstd_default()
    apply_zstd_default()
    assert pq.write_table is wrapped
    assert wrapped is not plain
    pq.write_table("table", "where")
    pq.write_table("table", "where", compression="snappy")
    assert calls == [{"compression": "zstd"}, {"compression": "snappy"}]


def test_importing_the_kernel_patches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    import kernel.parquet_compat

    def plain(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(pq, "write_table", plain)
    importlib.reload(kernel.parquet_compat)
    assert pq.write_table is plain
