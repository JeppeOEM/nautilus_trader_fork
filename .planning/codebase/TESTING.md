# Testing Patterns

**Analysis Date:** 2026-06-26

## Test Framework

**Runner:**
- `pytest` 7.4.4 (held at 7.x intentionally, not 8.x)
- Config: `pyproject.toml` (lines 497-502)
- Entry points: Test files located at root of each module (`troll/dydx_collector/test_*.py`, `troll/ml_signals/test_*.py`)

**Assertion Library:**
- Python built-in `assert` statements (allowed in test suite via ruff configuration)

**Run Commands:**
```bash
pytest troll/dydx_collector/
pytest troll/ml_signals/
pytest troll/dydx_collector/test_client.py::test_value_is_preserved_exactly_across_precisions
pytest troll/dydx_collector/test_client.py -v
```

**Configuration in pyproject.toml:**
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --new-first --failed-first --doctest-modules --doctest-glob=\"*.pyx\""
asyncio_mode = "strict"
asyncio_default_fixture_loop_scope = "session"
filterwarnings = ["ignore::UserWarning", "ignore::DeprecationWarning"]
```

**Key pytest plugins:**
- `pytest-asyncio==0.23.8` - Async test support (pinned for stability)
- `pytest-cov==6.3.0` - Coverage measurement
- `pytest-mock>=3.15.1` - Mocking support (not yet heavily used in troll/)
- `pytest-xdist[psutil]>=3.8.0` - Parallel test execution capability

## Test File Organization

**Location:**
- Co-located with implementation files at root of module
- Pattern: `test_*.py` files sit alongside `*.py` implementation files in the same directory
- Examples:
  - `troll/dydx_collector/test_client.py` (tests `client.py`)
  - `troll/ml_signals/test_book_features.py` (tests `book_features.py`)
  - `troll/ml_signals/test_indicators.py` (tests `indicators.py`)

**Naming:**
- File: `test_<module>.py` prefix format
- Functions: `test_<functionality>()` descriptive names
- Examples:
  - `test_value_is_preserved_exactly_across_precisions()`
  - `test_depth_profile_extracts_correct_levels()`
  - `test_cancel_tracker_mixed_pressure()`

**Structure:**
```
troll/
├── dydx_collector/
│   ├── client.py
│   ├── test_client.py          ← tests for client.py
│   ├── collector.py
│   ├── config.py
│   ├── open_interest.py
│   └── ...
├── ml_signals/
│   ├── book_features.py
│   ├── test_book_features.py   ← tests for book_features.py
│   ├── indicators.py
│   ├── test_indicators.py      ← tests for indicators.py
│   └── ...
```

## Test Structure

**Organization Pattern:**
- No class-based test organization; all tests are functions at module level
- Module-level constants for test data: `IID = InstrumentId.from_str("TEST-PERP.SIM")` (line 35 in `test_book_features.py`)
- Helper functions with `_` prefix for setup: `_delta()`, `_book()`, `_path()`, `_row()` (lines 38-50 in `test_book_features.py`)

**Typical test file structure (from `test_book_features.py`):**
```python
# ----- Copyright header -----

"""Self-check: re-stamping a Price at fixed precision never changes its value."""

# ----- Imports -----
from decimal import Decimal

from dydx_collector.client import _at_fixed_precision
from nautilus_trader.core.nautilus_pyo3 import FIXED_PRECISION
from nautilus_trader.model.objects import Price

# ----- Test data constants -----
VALUES_AT_VARYING_PRECISION = [
    ("1644.710689", 6),
    ("1647.81348", 5),
    ("61090.59855", 5),
    ("100", 0),
    ("0.000001", 6),
]

# ----- Helper functions -----
def _delta(action: BookAction, side: OrderSide, price: float, size: float, ts: int = 0) -> OrderBookDelta:
    order = BookOrder(side=side, price=Price(price, 1), size=Quantity(size, 1), order_id=0)
    return OrderBookDelta(instrument_id=IID, action=action, order=order, flags=0, sequence=0, ts_event=ts, ts_init=ts)

# ----- Test functions organized by feature -----

# ---- depth_profile ----

def test_depth_profile_extracts_correct_levels() -> None:
    # test implementation

# ---- book_imbalance ----

def test_imbalance_balanced() -> None:
    # test implementation

# ----- Runner: allow direct execution -----
if __name__ == "__main__":
    test_function_one()
    test_function_two()
    print("ok")
```

**Patterns:**
- Inline setup: Create test data directly in test function using helper functions
- No fixtures: Use helper functions instead for deterministic, readable test setup
- Teardown: Use context managers (not shown in current tests; tests are stateless)
- Return type hints on all test functions: `-> None`

**Example test function from `test_book_features.py` (lines 55-63):**
```python
def test_depth_profile_extracts_correct_levels() -> None:
    book = _book((100.0, 5.0), (99.0, 3.0), ask_levels=[(101.0, 4.0), (102.0, 6.0)])
    profile = depth_profile(book, levels=2)
    assert profile is not None
    assert profile.bid_prices == [100.0, 99.0]
    assert profile.bid_sizes  == [5.0, 3.0]
    assert profile.ask_prices == [101.0, 102.0]
    assert profile.ask_sizes  == [4.0, 6.0]
    assert profile.levels == 2
```

## Mocking

**Framework:** `pytest-mock` (part of test dependencies)

**Current Usage:**
- Not heavily used in troll/ codebase yet
- When needed, use `mocker` fixture from pytest-mock (provided by plugin)
- Keep mocking minimal; prefer real objects when possible

**Philosophy:**
- Prefer testing real behavior with actual Nautilus types
- Mock only external dependencies: filesystem, network, subprocess
- Example: `tempfile.mktemp()` is used instead of mocking file I/O (line 28 in `test_metrics_store.py`)

## Fixtures and Factories

**Test Data:**
- Helper functions with `_` prefix to create reusable test objects
- Examples from `test_book_features.py`:
  - `_delta()` (lines 38-40) - Creates OrderBookDelta test objects
  - `_book()` (lines 43-50) - Builds OrderBook with specified bid/ask levels

**Location:**
- Defined at module level in the same test file as their consumers
- No conftest.py (uses inline helpers instead)

**Immutable Configuration Pattern (from `test_metrics_store.py`, lines 27-37):**
```python
_NOW = time.time_ns()
_DAY_NS = 86_400 * 1_000_000_000

def _path() -> str:
    return tempfile.mktemp(suffix=".db")

def _row(ts: int, instrument_id: str = "BTC-USD-PERP.DYDX", **kwargs) -> dict:
    base = {
        "ts": ts, "instrument_id": instrument_id,
        "price": None, "pct_1h": None, "pct_24h": None, "volatility": None,
        "ofi": None, "microprice": None, "spread": None,
    }
    return {**base, **kwargs}
```

## Coverage

**Requirements:** Not enforced (fail_under = 0 in `pyproject.toml`)

**View Coverage:**
```bash
pytest --cov=troll.dydx_collector --cov-report=html troll/dydx_collector/
pytest --cov=troll.ml_signals --cov-report=html troll/ml_signals/
```

**Configuration in pyproject.toml:**
```toml
[tool.coverage.run]
plugins = ["Cython.Coverage"]
source = ["nautilus_trader"]
omit = [
  "nautilus_trader/adapters/*",
  "nautilus_trader/examples/*",
  "nautilus_trader/test_kit/*",
]
```

## Test Types

**Unit Tests:**
- Scope: Single function or small data structure behavior
- Approach: Direct function calls with assertions
- No async unless the function being tested is async
- Examples:
  - `test_depth_profile_extracts_correct_levels()` - Tests `depth_profile()` function with various book states
  - `test_imbalance_balanced()` - Tests `book_imbalance()` calculation

**Integration Tests:**
- Scope: Not yet used in troll/ codebase
- When needed: Test interaction between collector, catalog, and Nautilus types
- Pattern: Would follow same structure as unit tests but orchestrate multiple components

**E2E Tests:**
- Not present in troll/ codebase
- Collector runs live against dYdX during development
- Backtester runs via CLI (`backtest_dydx.py`)

## Common Patterns

**Basic Assertion Pattern (from `test_client.py`, lines 36-42):**
```python
def test_value_is_preserved_exactly_across_precisions() -> None:
    for value_str, precision in VALUES_AT_VARYING_PRECISION:
        original = Price(Decimal(value_str), precision)
        fixed = _at_fixed_precision(original)

        assert fixed.as_decimal() == Decimal(value_str)
        assert fixed.precision == FIXED_PRECISION
```

**Stateful Setup Pattern (from `test_book_features.py`, lines 134-146):**
```python
def test_cancel_tracker_all_adds_yields_negative_pressure() -> None:
    tracker = CancellationTracker(window=10)
    d = _delta(BookAction.ADD, OrderSide.BUY, 100.0, 5.0)
    tracker.update(d, best_bid_price=100.0, best_ask_price=101.0)
    tracker.update(d, best_bid_price=100.0, best_ask_price=101.0)
    # deleted=0, added=10 → (0-10)/10 = -1.0
    assert tracker.rate().bid_pressure == -1.0
```

**Floating Point Comparison Pattern (from `test_book_features.py`, line 99):**
```python
assert abs(imb.aggregate - 0.2) < 1e-9
```
Use `abs(actual - expected) < epsilon` for floating point values.

**None Checking Pattern (from `test_book_features.py`, lines 55-57):**
```python
profile = depth_profile(book, levels=2)
assert profile is not None
assert profile.bid_prices == [100.0, 99.0]
```
Always assert that nullable returns are not None before accessing attributes.

**Return Type Annotations:**
All test functions must declare `-> None` return type (enforced by mypy with disallow_incomplete_defs).

**Direct Execution Pattern (from `test_client.py`, lines 55-58):**
```python
if __name__ == "__main__":
    test_value_is_preserved_exactly_across_precisions()
    test_all_results_share_the_same_precision_label()
    print("ok")
```
Allow direct execution as a Python script: `python test_client.py`

## Async Testing

**Framework:** `pytest-asyncio==0.23.8`

**Not yet used in troll/ codebase:**
- dydx_collector client uses asyncio but is tested indirectly via collector integration
- When needed: Mark test functions with `@pytest.mark.asyncio` and use `async def test_*():`
- Example (not in codebase, but pattern):
```python
@pytest.mark.asyncio
async def test_client_connect() -> None:
    client = DydxClient(on_data=lambda x: None)
    # test async operations
    await client.connect(...)
```

**Mode:** `asyncio_mode = "strict"` (from pyproject.toml) enforces proper async handling

## Error Testing

**Pattern (from `test_metrics_store.py`):**
- Test error paths by triggering the error condition
- Assert on side effects (logging via logger, exception raised, state change)
- Example: Tests that show behavior when data is persisted/unpersisted correctly

**Not yet seen in tests:**
- Context manager pattern: `with pytest.raises(ValueError):`
- Exception message assertion: `with pytest.raises(ValueError, match="expected pattern"):`
- These patterns are available but not yet used in troll/ tests

## Test Data and Fixtures

**Test Data Constants (file-level):**
- `IID = InstrumentId.from_str("TEST-PERP.SIM")` - Reusable instrument ID
- `VALUES_AT_VARYING_PRECISION` - Test vector for parametric testing
- `_NOW = time.time_ns()` - Timestamp reference point
- `_DAY_NS = 86_400 * 1_000_000_000` - Time constant for calculations

**No conftest.py:**
- Troll modules don't use pytest conftest.py
- Fixtures are defined as helper functions at test file level
- Keeps tests self-contained and easier to read

## Example: Full Test File Structure

From `test_book_features.py`:

```python
# ----- Header -----
# ----- Copyright -----
"""Self-check: depth_profile, imbalance, liquidity_distance, CancellationTracker."""

# ----- Imports (3 groups) -----
from nautilus_trader.model.book import OrderBook
# ... more imports

from ml_signals.book_features import CancellationTracker
# ... local imports

# ----- Constants -----
IID = InstrumentId.from_str("TEST-PERP.SIM")

# ----- Helpers -----
def _delta(...) -> OrderBookDelta:
    ...

def _book(...) -> OrderBook:
    ...

# ----- Test groups with comments -----
# ---- depth_profile ----

def test_depth_profile_extracts_correct_levels() -> None:
    ...

# ---- book_imbalance ----

def test_imbalance_balanced() -> None:
    ...

# ----- Direct execution -----
if __name__ == "__main__":
    test_depth_profile_extracts_correct_levels()
    test_depth_profile_empty_book_returns_none()
    # ... all tests
    print("ok")
```

---

*Testing analysis: 2026-06-26*
