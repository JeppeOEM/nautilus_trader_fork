# Coding Conventions

**Analysis Date:** 2026-06-26

## Naming Patterns

**Files:**
- `snake_case` for all Python files: `client.py`, `book_features.py`, `open_interest.py`
- Test files: `test_*.py` prefix format (e.g., `test_client.py`, `test_book_features.py`)
- Config files: `*_config.py` suffix pattern (e.g., `config.py` for configuration modules)

**Functions:**
- `snake_case` for all functions: `fetch_instruments()`, `_buffer_key()`, `_at_fixed_precision()`
- Private/internal functions: underscore prefix when implementation-detail only: `_on_data()`, `_flush_once()`, `_handle_message()`
- Test functions: descriptive `snake_case` with `test_` prefix: `test_value_is_preserved_exactly_across_precisions()`, `test_depth_profile_extracts_correct_levels()`
- Async functions: same convention with `async def` keyword: `async def connect()`, `async def run()`

**Variables:**
- `snake_case` for local variables and parameters: `instrument_id`, `buffer_key`, `on_data`
- Single-use helpers with leading underscore: `_path()`, `_row()`, `_delta()` in test helpers

**Classes:**
- `PascalCase` for all classes: `Collector`, `DydxClient`, `DepthProfile`, `BookImbalance`, `LiquidityDistance`, `CancellationTracker`
- `PascalCase` for dataclass names (also with `@dataclass` decorator)

**Constants:**
- `UPPERCASE_WITH_UNDERSCORES` for module-level constants: `CONFIG_PATH`, `FIXED_PRECISION`, `VALUES_AT_VARYING_PRECISION`

## Code Style

**Formatting:**
- Tool: `ruff format` (official formatter, configured in `pyproject.toml`)
- Line length: 100 characters (enforced via ruff config)
- Enforced via pre-commit hook: `check-formatting-py`
- All Python code must pass `ruff format --check`

**Linting:**
- Tool: `ruff` with extensive rule set (Python 3.12+)
- Selected rules: C4, E, F, W, C90, D, DTZ, UP, S, T10, ICN, PIE, PT, PYI, Q, I, RSE, TID, SIM, B, PERF, FURB, ISC, FLY, LOG, ASYNC, PD, PGH, PLE, PLW, NPY, RUF
- Cognitive complexity limit: 10 (mccabe)
- Type checking: `mypy` with `disallow_incomplete_defs = true` enforced
- Pre-commit hooks: `ruff` and `ruff-format` check all commits

**Example: Line 100 or less**
```python
async def disconnect(self) -> None:
    if not self._ws.is_closed():
        await self._ws.disconnect()
```

## Import Organization

**Order (enforced by ruff isort plugin):**
1. Python standard library: `import asyncio`, `import logging`, `from pathlib import Path`, `from collections import defaultdict`
2. Third-party libraries: `import pyarrow as pa`, `from decimal import Decimal`, `import numpy as np`
3. Nautilus trader: `from nautilus_trader.model.data import Bar`, `from nautilus_trader.core.nautilus_pyo3 import ...`
4. Local application: `from dydx_collector.client import DydxClient`, `from ml_signals.book_features import DepthProfile`

**Specific rules:**
- Force single line imports: one import per line
- Lines after imports: 2 blank lines
- Known first-party: `nautilus_trader`
- Example from `dydx_collector/client.py`:
```python
import asyncio
import logging
from collections.abc import Callable

from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.core.nautilus_pyo3 import FIXED_PRECISION
from nautilus_trader.core.nautilus_pyo3 import DydxNetwork
from nautilus_trader.model.data import FundingRateUpdate
```

## Error Handling

**Patterns:**
- Try/except blocks with logging on errors (logged before raising or in error handler)
- Exception logging via `logger.exception()` which includes traceback: `logger.exception(f"Failed to write {key}, dropping {len(items)} items")`
- Type ignore comments for pyO3 bindings: `# type: ignore[attr-defined]` on lines 85, 86, 87 in `client.py`
- Return type hints required for all functions: `-> None`, `-> list[Instrument]`, `-> tuple[list[...], list[...]]`
- Async error handling: wrap blocking I/O in try/except with logging, never let exceptions bubble unlogged

**Example from `collector.py` (lines 78-81):**
```python
try:
    self._catalog.write_data(items)
except Exception:
    logger.exception(f"Failed to write {key}, dropping {len(items)} items")
```

## Logging

**Framework:** `logging` module with standard levels (DEBUG, INFO, WARNING, ERROR, CRITICAL)

**Module-level logger pattern:**
```python
logger = logging.getLogger(__name__)
```

**Usage:**
- `logger.info()` for informational messages (subscriptions, auto-subscribe events): `logger.info(f"Subscribed {entry.id}")`
- `logger.debug()` for detailed messages: `logger.debug(f"Ignoring message of type {type(message).__name__}")`
- `logger.exception()` in except blocks to log error with traceback: `logger.exception("Failed to poll open interest")`
- `logger.warning()` for concerning events: `logger.warning(f"Configured instruments not found on dYdX: {missing}")`

**Configuration:**
- Logging setup in main entrypoint: `logging.basicConfig(level=logging.INFO)` in `collector.py` line 187

## Comments

**When to Comment:**
- Explain **WHY** and **context**, not WHAT (code already shows what it does)
- Document non-obvious algorithmic decisions or workarounds
- Explain safety decisions for unusual patterns

**Examples:**
Module-level docstring (lines 15-24 in `client.py`):
```python
"""
Thin wrapper around dYdX's Rust-backed HTTP/WebSocket clients.

Deliberately bypasses TradingNode/Strategy/DataEngine: that live-runtime path
has a documented unbounded-queue-growth + shutdown-wedge bug under high message
load (see memory project_dydx_collector_python_pivot). This client drives the
same Rust connection/reconnect/throttle/decode logic directly with our own
asyncio loop and callback, so the collector never touches the buggy layer.

"""
```

Function-level docstring (lines 46-63 in `client.py`):
```python
"""
Re-stamp a Price at nautilus's max fixed-point precision, exactly.

dYdX's oracle (mark/index) price feed derives each tick's `Price.precision`
from however many decimal digits that specific value has left after
stripping trailing zeros (crates/adapters/dydx/src/common/parse.rs's
parse_price), so consecutive ticks for the same instrument can carry
different precision labels. ParquetDataCatalog stamps that label into
each file's Arrow metadata and correctly refuses to read/merge files
whose labels disagree -- which is the actual error this works around.
...
"""
```

Inline comments (lines 134-137 in `client.py`):
```python
# Trades/orderbook/bars arrive wrapped in a PyCapsule; markets-channel
# updates (mark/index price, funding, instrument status) arrive as plain
# pyo3-native objects instead -- both paths mirror
# nautilus_trader/adapters/dydx/data.py's `_handle_msg`.
```

## Function Design

**Type Hints Required:**
- All parameters: `def _buffer_key(data: Any) -> tuple[type, str]:`
- All return values: `-> None`, `-> list[Instrument]`, `-> tuple[list[...], list[...]]`, `-> DepthProfile | None`
- Use `| None` syntax (Python 3.10+) for optional types: `-> DepthProfile | None`
- Function signature example (lines 79-89 in `client.py`):
```python
def __init__(
    self,
    on_data: Callable[[object], None],
    network: DydxNetwork = DydxNetwork.MAINNET,
) -> None:
```

**Async Functions:**
- Use `async def` for I/O-bound operations
- Return awaitable results (coroutines)
- Example: `async def connect(self, loop: asyncio.AbstractEventLoop, instruments: list[Instrument]) -> None:`

**Default Parameters:**
- Provide sensible defaults for configuration: `levels: int = 4`, `pct_threshold: float = 0.8`, `network: DydxNetwork = DydxNetwork.MAINNET`
- Use None-safe patterns when appropriate: `ask_levels: list[tuple[float, float]] | None = None`

**Parameter Order:**
- Required parameters first
- Optional parameters with defaults later
- Self/cls first in methods

**Example of proper function design (lines 92-108 in `book_features.py`):**
```python
def depth_profile(book: OrderBook, levels: int = 4) -> DepthProfile | None:
    """
    Extract size and price at the top `levels` levels on each side.

    Returns None if either side has no quotes (book not yet initialised).
    """
    bids = book.bids()
    asks = book.asks()
    if not bids or not asks:
        return None

    bid_prices = [lv.price.as_double() for lv in bids[:levels]]
    bid_sizes  = [lv.size()            for lv in bids[:levels]]
    ask_prices = [lv.price.as_double() for lv in asks[:levels]]
    ask_sizes  = [lv.size()            for lv in asks[:levels]]

    return DepthProfile(bid_prices, bid_sizes, ask_prices, ask_sizes)
```

## Module Design

**Dataclasses for Configuration:**
- Use `@dataclass(frozen=True)` for immutable config objects
- Example from `config.py` (lines 24-37):
```python
@dataclass(frozen=True)
class InstrumentEntry:
    id: str
    bar_intervals: tuple[str, ...]

@dataclass(frozen=True)
class CollectorConfig:
    network: DydxNetwork
    catalog_path: str
    flush_interval_seconds: int
    config_reload_seconds: int
    open_interest_poll_seconds: int
    instruments: tuple[InstrumentEntry, ...]
```

**Module Docstrings:**
- Explain purpose and design decisions at the top of each file
- Include rationale for architectural choices
- Reference related files or issues when relevant

## Copyright and License

**Required Header:**
All source files must include LGPL-3.0 copyright header (lines 1-14 in every file):
```python
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
```
Year in header must be current (enforced by pre-commit hook `check-copyright-year`).

## Pre-Commit Hooks Applied

- `check-formatting-py` - Python formatting via `ruff format`
- `ruff` - Python linting with extensive rule set
- `ruff-format` - Python code formatting
- `mypy` - Python type checking with `disallow_incomplete_defs`
- All checks enforced automatically on commit

---

*Convention analysis: 2026-06-26*
