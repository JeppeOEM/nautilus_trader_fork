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
Only the allowlisted folds turn finer rows into OHLC bars (Story 24.1's invariant, DW-192).

Every bar the platform serves comes from two production folds: `kernel.fold.fold_trades` folds one
second's trades into the 1 s row, and `candles.domain.fold.fold_arrays` folds 1 s rows into every
wider bar (the live feed, the rebuild and the forming bar alike). A third production fold would be
a second definition of a bar that can silently disagree with the first. The verification context
re-derives bars on purpose, independently of what it checks (Epic 31), so its reference folds are
allowlisted separately and must never share code with the production ones.

The detector is AST-based and runs over every module `_source_tree.python_modules()` walks (tests
included). A function (or method) is a fold when it holds both a max and a min reduction
(`max(...)`, `.max()`, `np.maximum`/`np.fmax`/`np.nanmax` and their `.reduceat` forms; min
symmetric) and either builds a record whose keyword arguments or dict-literal keys cover one whole
OHLC key set, or buckets its rows (`bucket_start*(...)`, any `*.reduceat`). Nested functions are
judged on their own bodies.

Adding a fold: a new *production* fold is almost always wrong -- call `fold_arrays` instead. A new
verification reference fold goes into `REFERENCE_FOLDS` as `module.qualname` with the reason it
exists. A function the detector flags that is not a fold (it only samples per bucket, say) is a
detector bug: tighten the detector and add the shape to the self-tests below, never an allowlist
entry.

Known limit: the detector is a heuristic over the shapes the platform's folds take today. It does
not see a fold that builds its bar positionally (`SecondOHLC(t, o, h, l, c, ...)`) without
`bucket_start*`/`reduceat`, one that fills its OHLC by subscript (`bar["high"] = ...`) and buckets
with `ts // bar * bar`, one written at module level or in a lambda, or one delegated to pandas
(`resample(...).ohlc()`, `.agg({"high": "max"})`). Code review still owns those. Upgrade path:
add each such shape to the detector, and a self-test for it, when one first appears in the tree.
"""

import ast
from collections.abc import Iterator

from _source_tree import python_modules


PRODUCTION_FOLDS: dict[str, str] = {
    "kernel.fold.fold_trades": "Story 24.1 AD: trades -> the 1 s row, the one trade fold",
    "candles.domain.fold.fold_arrays": (
        "Story 24.1 AD: 1 s rows -> every wider bar; every other bar comes from these two"
    ),
}

REFERENCE_FOLDS: dict[str, str] = {
    "verification.domain.reference_signals._fold_bucket": (
        "Epic 31: the reference candle re-derived from books, never sharing production's code"
    ),
    "verification.domain.candle_check.merge_candles": (
        "Epic 31: the reference 1W bar folded from its days, independent of production"
    ),
    "verification.domain.trade_check.fold_second": (
        "Epic 31: the reference 1 s trade fold the rebuild is checked against"
    ),
}

_MAX_NAMES = frozenset({"max", "amax", "nanmax", "maximum", "fmax"})
_MIN_NAMES = frozenset({"min", "amin", "nanmin", "minimum", "fmin"})
_OHLC_KEY_SETS = (
    frozenset({"o", "h", "l", "c"}),
    frozenset({"open", "high", "low", "close"}),
    frozenset({"open_price", "high_price", "low_price", "close_price"}),
)

_Function = ast.FunctionDef | ast.AsyncFunctionDef
_Scope = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _callee(func: ast.expr) -> str | None:
    """Return the called name's last component: `max` for `max(...)`, `x.max()`, `np.max(...)`."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_reduction(call: ast.Call, names: frozenset[str]) -> bool:
    """Tell a call to one of `names`, or to `<one of names>.reduceat` (`np.maximum.reduceat`)."""
    func = call.func
    if _callee(func) in names:
        return True
    return (
        isinstance(func, ast.Attribute) and func.attr == "reduceat" and _callee(func.value) in names
    )


def _own_nodes(function: _Function) -> Iterator[ast.AST]:
    """Every node of `function`'s own body: a nested def or class is judged on its own."""
    stack: list[ast.AST] = list(function.body)
    while stack:
        node = stack.pop()
        yield node
        stack.extend(child for child in ast.iter_child_nodes(node) if not isinstance(child, _Scope))


def _record_keys(node: ast.AST) -> set[str]:
    """Return the field names a call's keywords or a dict literal's string keys give a record."""
    if isinstance(node, ast.Call):
        return {keyword.arg for keyword in node.keywords if keyword.arg}
    if isinstance(node, ast.Dict):
        return {
            k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)
        }
    return set()


def _buckets(call: ast.Call) -> bool:
    callee = _callee(call.func) or ""
    return callee.startswith("bucket_start") or callee == "reduceat"


def _is_fold(function: _Function) -> bool:
    nodes = list(_own_nodes(function))
    calls = [node for node in nodes if isinstance(node, ast.Call)]
    reduces = any(_is_reduction(c, _MAX_NAMES) for c in calls) and any(
        _is_reduction(c, _MIN_NAMES) for c in calls
    )
    if not reduces:
        return False
    builds_ohlc = any(keys <= _record_keys(node) for node in nodes for keys in _OHLC_KEY_SETS)
    return builds_ohlc or any(_buckets(c) for c in calls)


def _functions(node: ast.AST, prefix: str = "") -> Iterator[tuple[str, _Function]]:
    """Every function and method under `node`, with its dotted qualname (`Class.method`)."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
            yield prefix + child.name, child
            yield from _functions(child, f"{prefix}{child.name}.")
        elif isinstance(child, ast.ClassDef):
            yield from _functions(child, f"{prefix}{child.name}.")
        else:
            yield from _functions(child, prefix)


def _folds_in(source: str, module: str) -> set[str]:
    tree = ast.parse(source)
    return {f"{module}.{name}" for name, function in _functions(tree) if _is_fold(function)}


def _tree_folds() -> set[str]:
    return {
        fold
        for module, path in python_modules().items()
        for fold in _folds_in(path.read_text(), module)
    }


def test_the_only_folds_are_the_allowlisted_ones() -> None:
    found = _tree_folds()
    allowed = PRODUCTION_FOLDS.keys() | REFERENCE_FOLDS.keys()
    assert sorted(found - allowed) == [], (
        "a new OHLC fold: call candles.domain.fold.fold_arrays instead (see the module docstring)"
    )
    assert sorted(allowed - found) == [], (
        "an allowlisted fold is gone or no longer folds: drop or update its entry"
    )


def test_the_two_allowlists_do_not_overlap() -> None:
    assert PRODUCTION_FOLDS.keys() & REFERENCE_FOLDS.keys() == set()


_REDUCEAT_FOLD = """
import numpy as np

def fold_bars(ts, o, h, low, c, bar):
    starts = np.flatnonzero(np.diff(ts // bar, prepend=-1))
    return o[starts], np.maximum.reduceat(h, starts), np.minimum.reduceat(low, starts), c
"""

_DICT_LOOP_FOLD = """
class Roller:
    def roll_up(self, rows, bar):
        out = {}
        for r in rows:
            t = r.ts // bar * bar
            b = out.get(t)
            if b is None:
                out[t] = {"o": r.price, "h": r.price, "l": r.price, "c": r.price}
            else:
                b["h"] = max(b["h"], r.price)
                b["l"] = min(b["l"], r.price)
                b["c"] = r.price
        return out
"""

_CLAMP = """
def clamp(x, lo, hi):
    return {"value": max(lo, min(x, hi))}
"""


def test_a_numpy_reduceat_fold_is_caught() -> None:
    assert _folds_in(_REDUCEAT_FOLD, "synthetic") == {"synthetic.fold_bars"}


def test_a_dict_of_ohlc_loop_in_a_method_is_caught() -> None:
    assert _folds_in(_DICT_LOOP_FOLD, "synthetic") == {"synthetic.Roller.roll_up"}


def test_a_max_and_min_without_a_bar_record_is_not_a_fold() -> None:
    assert _folds_in(_CLAMP, "synthetic") == set()


def test_indicator_bucket_sampling_is_not_a_fold() -> None:
    """
    DW-192's admission: `views.chart_series.replay_bucket_samples` keeps one indicator sample per
    bucket (served by `data_api/routes/indicator_series.py`); it buckets but folds no OHLC.
    """
    path = python_modules()["views.chart_series"]
    tree = ast.parse(path.read_text())
    functions = dict(_functions(tree))
    assert "replay_bucket_samples" in functions
    assert not _is_fold(functions["replay_bucket_samples"])
