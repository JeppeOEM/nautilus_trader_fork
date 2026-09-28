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
Research reads market data only through bounded paths (Story 24.4; amended Story 27.1).

Every market-data row a research module or notebook reads goes through `kernel.catalog_files`
(column-projected, time-bounded), `BacktestDataConfig` (streamed by `BacktestNode`), the candle
store's query service, or a catalog-object query that names **both** `start=` and `end=` keywords
(`ParquetDataCatalog.query(..., start=, end=)`, as `research.application.frames` does, and any
`MarketFrames` call, whose `start`/`end` are required keyword-only). An unbounded catalog query
decodes or materialises a whole slice (MEM-01) and fails here; a direct Parquet file read
(`read_parquet`, `read_table`, `ParquetFile`, ...) fails unconditionally -- it bypasses the one read
path the kernel keeps consistent with what capture writes. Instrument definitions
(`ParquetDataCatalog.instruments`) are metadata, not market data, and writing a throwaway catalog
(`strategies.snapshot_backtest`) reads nothing, so neither is flagged.

The scan is static (AST): every non-test `.py` under `research/` and the code cells of every
notebook, with IPython magics and shell escapes stripped. The only exemption names the story that
replaces the file and fails once that story is `done`, or once the file no longer needs it.
"""

import ast
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_source_tree() -> ModuleType:
    """`platform/tests/_source_tree`: the sprint board parser the other expiring tables use."""
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


_SOURCE_TREE = _load_source_tree()
_RESEARCH_DIR = _SOURCE_TREE.PLATFORM_DIR / "research"

# Catalog-object queries, matched as a called name or attribute: allowed only with both `start=`
# and `end=` keywords (Story 27.1).
BOUNDABLE_READS = frozenset(
    {
        "query",
        "trade_ticks",
        "quote_ticks",
        "order_book_deltas",
        "order_book_depth10",
        "bars",
        "custom_data",
        "generic_data",
    }
)
# Direct Parquet reads, forbidden however they are called.
FORBIDDEN_READS = frozenset(
    {
        "read_parquet",
        "read_pandas",
        "read_table",
        "scan_parquet",
        "iter_batches",
        "ParquetFile",
        "ParquetDataset",
        "dataset",  # `pyarrow.dataset.dataset`
    }
)

# File name -> the story whose `done` retires the exemption.
LEGACY_READS_UNTIL: dict[str, str] = {
    # Reads the retired `custom_dydx_minute_bar` directory with `pd.read_parquet` (its leading
    # `Known limit:` cell); Story 27.7 replaces the notebook.
    "candlestick_pattern_scanner.ipynb": (
        "27-7-candlestick-pattern-detector-kernel-chart-screener-scanner"
    ),
}


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None


def _bounded(call: ast.Call) -> bool:
    """Both `start=` and `end=` passed, neither as the literal `None` (which is no bound)."""
    keywords = {
        keyword.arg
        for keyword in call.keywords
        if not (isinstance(keyword.value, ast.Constant) and keyword.value.value is None)
    }
    return {"start", "end"} <= keywords


def _forbidden(call: ast.Call) -> str | None:
    name = _called_name(call)
    if name in FORBIDDEN_READS or (name in BOUNDABLE_READS and not _bounded(call)):
        return name
    return None


def forbidden_reads(source: str) -> list[tuple[int, str]]:
    """Return (line, name) of every forbidden read call in `source`."""
    return sorted(
        (node.lineno, name)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and (name := _forbidden(node)) is not None
    )


def _notebook_code(path: Path) -> str:
    """
    Join the notebook's code cells into one module, blanking magics and shell escapes. A `%%`
    cell magic makes the whole cell non-Python, so that cell is blanked entirely.
    """
    cells = json.loads(path.read_text())["cells"]
    lines = [
        "" if line.lstrip().startswith(("%", "!")) else line.rstrip("\n")
        for cell in cells
        if cell["cell_type"] == "code" and not "".join(cell["source"]).lstrip().startswith("%%")
        for line in [*"".join(cell["source"]).splitlines(), ""]
    ]
    return "\n".join(lines)


def _sources() -> dict[str, str]:
    """Every scanned research file (relative path) -> its Python source."""
    modules = {
        str(path.relative_to(_RESEARCH_DIR)): path.read_text()
        for path in sorted(_RESEARCH_DIR.rglob("*.py"))
        if "tests" not in path.relative_to(_RESEARCH_DIR).parts
    }
    notebooks = {
        str(path.relative_to(_RESEARCH_DIR)): _notebook_code(path)
        for path in sorted(_RESEARCH_DIR.rglob("*.ipynb"))
        if ".ipynb_checkpoints" not in path.parts
    }
    return modules | notebooks


_SOURCES = _sources()


def _violations() -> dict[str, list[tuple[int, str]]]:
    return {name: found for name, source in _SOURCES.items() if (found := forbidden_reads(source))}


def test_the_scan_sees_modules_and_notebooks() -> None:
    names = set(_SOURCES)
    assert "strategies/snapshot_backtest.py" in names
    assert "notebooks/dydx_catalog_pandas.ipynb" in names
    assert "application/frames.py" in names
    assert not any(name.startswith("tests/") for name in names)


def test_research_reads_market_data_only_through_the_kernel_or_backtest_configs() -> None:
    offending = {
        name: found
        for name, found in _violations().items()
        if Path(name).name not in LEGACY_READS_UNTIL
    }
    assert offending == {}, (
        "read market data via kernel.catalog_files, BacktestDataConfig or a start=/end=-bounded "
        "catalog query (MEM-01, Stories 24.4 and 27.1)"
    )


def test_every_read_exemption_is_still_needed() -> None:
    reading = {Path(name).name for name in _violations()}
    assert sorted(set(LEGACY_READS_UNTIL) - reading) == [], "no read needs these: delete them"


@pytest.mark.parametrize(("name", "story"), sorted(LEGACY_READS_UNTIL.items()))
def test_read_exemption_expires_with_its_story(name: str, story: str) -> None:
    reason = _SOURCE_TREE.unknown_or_done(story, _SOURCE_TREE.story_statuses())
    assert reason is None, f"{name}: {reason} -- the notebook should be gone"


def test_scanner_flags_each_kind_of_read_and_not_metadata() -> None:
    source = (
        "catalog.query(X)\ncatalog.trade_ticks()\npd.read_parquet(p)\npq.read_table(p)\n"
        "ParquetFile(p)\ncatalog.instruments(instrument_ids=[i])\nquery_top_of_book(c, i, 0, 1)\n"
    )
    assert forbidden_reads(source) == [
        (1, "query"),
        (2, "trade_ticks"),
        (3, "read_parquet"),
        (4, "read_table"),
        (5, "ParquetFile"),
    ]


def test_notebook_scan_strips_magics(tmp_path: Path) -> None:
    notebook = tmp_path / "n.ipynb"
    cells = [
        {"cell_type": "markdown", "source": ["catalog.query(X)\n"]},
        {"cell_type": "code", "source": ["%pip install x\n", "!ls\n", "catalog.bars()"]},
        {"cell_type": "code", "source": ["%%bash\n", "catalog.query(X) | not python\n"]},
    ]
    notebook.write_text(json.dumps({"cells": cells}))
    assert forbidden_reads(_notebook_code(notebook)) == [(3, "bars")]


def test_a_catalog_query_needs_both_bounds() -> None:
    source = (
        "catalog.query(X, identifiers=[i], start=a, end=b)\n"
        "catalog.query(X, start=a)\n"
        "catalog.trade_ticks(end=b)\n"
        "frames.bars(i, 60, start=a, end=b)\n"
        "frames.bars(i, 60)\n"
        "pd.read_parquet(p, start=a, end=b)\n"
        "catalog.query(X, a, b)\n"
        "catalog.query(X, start=None, end=b)\n"
        "catalog.query(X, start=a, end=None)\n"
        "frames.seconds(i, start=a, end=b)\n"
    )
    assert forbidden_reads(source) == [
        (2, "query"),
        (3, "trade_ticks"),
        (5, "bars"),
        (6, "read_parquet"),
        (7, "query"),
        (8, "query"),
        (9, "query"),
    ]
