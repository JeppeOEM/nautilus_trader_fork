# Testing Patterns

**Analysis Date:** 2026-06-13

## Test Framework

**Rust Runner:**
- `cargo test` (default test harness)
- `cargo test --release` for optimized testing
- `cargo bench` for benchmarks (separate from tests)

**Rust Assertion Library:**
- Standard library `assert!` macro
- `rstest` for parameterized tests with `#[rstest]` attribute
- Custom error assertions via `Result<T, E>` pattern

**Python Runner:**
- `pytest` version 7.4.4 (intentionally held at 7.x)
- Config: `pyproject.toml` `[tool.pytest.ini_options]`

**Python Test Commands:**
```bash
pytest                           # Run all tests
pytest tests/unit/              # Run unit tests only
pytest -xvs tests/              # Stop on first failure, verbose, no capture
pytest --cov=nautilus_trader    # Run with coverage
pytest -k "test_name"           # Run tests matching pattern
pytest -m "not slow"            # Run tests excluding marker
```

**Run Commands (Workspace):**
```bash
cargo test --workspace           # All Rust tests
cargo test -p nautilus-core      # Specific crate tests
uv run pytest                     # All Python tests
uv run pytest --cov=nautilus_trader --cov-report=html
```

## Test File Organization

**Location:**

**Rust:**
- Unit tests: Inline in module file in `mod tests { ... }` block at end
- Integration tests: `crates/{crate_name}/tests/test_*.rs` files
- Benchmarks: `crates/{crate_name}/benches/*.rs` files
- Example: `crates/serialization/tests/test_market_data_capnp.rs`

**Python:**
- Test files: `tests/unit/`, `tests/integration/`, `tests/acceptance_tests/`
- Fixtures: `tests/conftest.py` (session-level) or module-local
- Memory leak tests: `tests/mem_leak_tests/`
- Example: `tests/unit/common/test_message_bus.py`

**Naming:**

**Rust:**
- Test function: `test_{function}_{scenario}` or `test_{scenario}`
- Example: `test_quote_tick_roundtrip()`, `test_check_predicate_true()`

**Python:**
- Test file: `test_{module}.py`
- Test function: `test_{function}_{scenario}`
- Example: `test_instantiate_defaults()`, `test_send_delivers_to_endpoint()`

## Test Structure

**Rust Suite Organization:**
```rust
#[cfg(test)]
mod tests {
    use super::*;
    use rstest::rstest;

    #[rstest]
    fn test_basic_case() {
        // Arrange
        let value = Price::from("100.50");
        
        // Act
        let result = process(value);
        
        // Assert
        assert_eq!(result, expected);
    }

    #[rstest]
    #[case(100.50, 100.50)]
    #[case(0.00001, 0.00001)]
    fn test_with_parameters(#[case] input: f64, #[case] expected: f64) {
        // Arrange & Act
        let value = Price::from(input.to_string().as_str());
        
        // Assert
        assert_eq!(value.as_f64(), expected);
    }

    #[rstest]
    #[should_panic(expected = "Condition failed")]
    fn test_panic_case() {
        // Test code that should panic
    }
}
```

**Python Suite Organization:**
```python
import pytest

@pytest.fixture
def trader_id():
    return TraderId.from_str("TRADER-001")

@pytest.fixture
def bus(trader_id):
    return MessageBus(trader_id=trader_id)

def test_instantiate_defaults(bus, trader_id):
    # Arrange (via fixtures) & Act & Assert
    assert bus.trader_id == trader_id
    assert bus.name == "MessageBus"
    assert bus.has_backing is False

@pytest.mark.parametrize("input,expected", [
    (100.50, 100.50),
    (0.00001, 0.00001),
])
def test_with_parameters(input, expected):
    # Test with multiple parameter sets
    value = Price.from(str(input))
    assert value.as_f64() == expected

class TestMessageBus:
    """Test class for related tests."""
    
    def test_send_delivers_to_endpoint(self, bus):
        received = []
        bus.register("mailbox", received.append)
        bus.send("mailbox", "msg")
        assert received == ["msg"]
        assert bus.sent_count == 1
```

**Patterns:**

**Setup (Arrange):**
- Rust: Direct object construction or builder patterns
- Python: pytest fixtures with scope (function, class, module, session)

**Teardown:**
- Rust: Automatic via `Drop` trait implementation
- Python: Fixture yield or autouse fixtures; session cleanup in `conftest.py`

Example from `conftest.py`:
```python
@pytest.fixture(scope="session")
def session_event_loop(event_loop_policy):
    """Session-scoped event loop with cleanup."""
    policy = event_loop_policy
    loop = policy.new_event_loop()
    asyncio.set_event_loop(loop)
    
    yield loop
    
    # Cleanup
    try:
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        if not loop.is_running():
            loop.close()
    except RuntimeError:
        pass
    finally:
        asyncio.set_event_loop(None)
```

**Assertion Pattern:**
- Rust: `assert_eq!(actual, expected)`, `assert!(condition)`
- Python: `assert actual == expected`, `assert condition`

## Mocking

**Framework:**

**Rust:**
- No external mocking framework; use builder patterns and test stubs
- Trait-based mocking via implementations
- Example stub from tests: `stub_trade_ethusdt_buyer()`, `stub_bar()`, `stub_delta()`

**Python:**
- `pytest-mock` (from test dependencies)
- `pytest.fixture` decorator for setup
- Manual mocks via function replacement

**Patterns:**

**Rust Mocking:**
```rust
// Use test utility functions
#[rstest]
fn test_with_stub_data() {
    let trade = stub_trade_ethusdt_buyer();
    let bytes = trade.to_sbe().unwrap();
    let decoded = TradeTick::from_sbe(&bytes).unwrap();
    assert_eq!(trade, decoded);
}

// Use builder for custom objects
#[rstest]
fn test_with_builder() {
    let instrument = InstrumentId::from("BTCUSDT.BINANCE");
    let bar = Bar::builder()
        .instrument_id(instrument)
        .open(Price::from("100.50"))
        .build();
    assert_eq!(bar.open, Price::from("100.50"));
}
```

**Python Mocking:**
```python
def test_with_mock(mocker):
    mock_func = mocker.patch('module.function')
    mock_func.return_value = 42
    
    result = function_under_test()
    
    assert result == expected
    mock_func.assert_called_once()

@pytest.fixture
def mock_venue(mocker):
    return mocker.Mock(spec=Venue)
```

**What to Mock:**
- External services (HTTP clients, databases)
- Time-dependent operations (use mocked clocks)
- Random operations (use seeded RNG or mocks)

**What NOT to Mock:**
- Domain model types (`Price`, `Quantity`, `Order`, etc.)
- Core business logic
- Standard library types
- Internal helper functions

## Fixtures and Factories

**Test Data:**

**Rust:**
```rust
// Stub functions in testkit crate (nautilus-testkit)
use nautilus_testkit::stubs::*;

#[rstest]
fn test_with_fixture_data() {
    let quote = QuoteTick {
        instrument_id: InstrumentId::from("BTCUSDT.BINANCE"),
        bid_price: Price::from("100.50"),
        ask_price: Price::from("100.55"),
        bid_size: Quantity::from("10.5"),
        ask_size: Quantity::from("8.3"),
        ts_event: 1234567890.into(),
        ts_init: 1234567891.into(),
    };
    
    let bytes = quote.to_capnp(...);
    // ...
}
```

**Python:**
```python
# In conftest.py or test file
@pytest.fixture
def trader_id():
    return TraderId.from_str("TRADER-001")

@pytest.fixture
def instrument():
    return CurrencyPair.from_str("BTCUSDT")

@pytest.fixture
def quote_tick(instrument):
    from nautilus_trader.model.data import QuoteTick
    from nautilus_trader.model.types import Price, Quantity
    
    return QuoteTick(
        instrument_id=instrument,
        bid_price=Price.from_str("100.50"),
        ask_price=Price.from_str("100.55"),
        bid_size=Quantity.from_str("10.5"),
        ask_size=Quantity.from_str("8.3"),
        ts_event=1234567890,
        ts_init=1234567891,
    )
```

**Location:**
- Rust: Inline in test module or `testkit` crate utilities
- Python: `tests/conftest.py` for shared fixtures; module-local for specific tests
- Reusable stubs: `nautilus_trader.test_kit.providers` (TestDataProvider, TestInstrumentProvider)

## Coverage

**Requirements:** No explicit fail-under threshold (fail_under = 0 in pyproject.toml)

**View Coverage (Python):**
```bash
uv run pytest --cov=nautilus_trader --cov-report=html
# Open htmlcov/index.html in browser
```

**Coverage Config:**
```toml
[tool.coverage.run]
plugins = ["Cython.Coverage"]
source = ["nautilus_trader"]
omit = [
  "nautilus_trader/adapters/*",
  "nautilus_trader/examples/*",
  "nautilus_trader/test_kit/*",
]

[tool.coverage.report]
fail_under = 0
show_missing = true
```

## Test Types

**Unit Tests:**
- Scope: Single function/method with minimal dependencies
- Location: `tests/unit/` (Python) or inline `mod tests` (Rust)
- Dependencies: Mocked or minimal fixtures
- Run time: <1ms per test

**Integration Tests:**
- Scope: Multiple components working together
- Location: `tests/integration/` (Python) or `tests/test_*.rs` (Rust)
- Dependencies: Real or controlled external systems
- Run time: 1-100ms per test

**Acceptance/E2E Tests:**
- Scope: Complete workflows from user perspective
- Location: `tests/acceptance_tests/`
- Example: `test_blackbox.py` (end-to-end system tests)
- Run time: Seconds to minutes

**Example Test Types:**
```rust
// Unit test - inline in module
#[cfg(test)]
mod tests {
    #[rstest]
    fn test_price_from_string() {
        let price = Price::from("100.50");
        assert_eq!(price.as_f64(), 100.5);
    }
}

// Integration test - crates/core/tests/integration_test.rs
#[rstest]
fn test_serialization_roundtrip() {
    let value = QuoteTick::default();
    let bytes = value.to_sbe().unwrap();
    let decoded = QuoteTick::from_sbe(&bytes).unwrap();
    assert_eq!(value, decoded);
}
```

## Async Testing

**Framework:**
- Rust: `tokio` runtime via `#[tokio::test]` (when feature enabled)
- Python: `pytest-asyncio` with `asyncio_mode = "strict"`

**Patterns:**

**Rust Async:**
```rust
#[tokio::test]
async fn test_async_operation() {
    let result = async_function().await;
    assert_eq!(result, expected);
}

// With rstest
#[rstest]
#[tokio::test]
async fn test_with_parameters(#[case] value: u32) {
    assert!(value > 0);
}
```

**Python Async:**
```python
@pytest.mark.asyncio
async def test_async_operation():
    result = await async_function()
    assert result == expected

@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest.mark.asyncio
async def test_with_event_loop(event_loop):
    # Test with custom event loop
    pass
```

**Event Loop Management:**
- Scope: Session-level for efficiency (set in conftest.py)
- Policy: uvloop on Linux/macOS, default on Windows (from event_loop_policy fixture)

## Error Testing

**Patterns:**

**Rust Error Testing:**
```rust
#[rstest]
fn test_returns_error() {
    let result = check_predicate_true(false, "failed");
    assert!(result.is_err());
    
    let error = result.unwrap_err();
    assert_eq!(error, CorrectnessError::PredicateViolation {
        message: "failed".to_string(),
    });
}

#[rstest]
#[should_panic(expected = "Condition failed")]
fn test_panic_with_expect() {
    let result: CorrectnessResult<()> = Err(CorrectnessError::EmptyString {
        param: "value".to_string(),
    });
    result.expect_display(FAILED);
}
```

**Python Error Testing:**
```python
def test_raises_exception():
    with pytest.raises(ValueError, match="expected message"):
        function_that_raises()

def test_error_details():
    with pytest.raises(ValueError) as exc_info:
        function_that_raises()
    assert "expected" in str(exc_info.value)
```

## Common Testing Issues

**Testing Conventions Hook:**
- Enforced by pre-commit hook: `check-testing-conventions`
- Ensures test naming, structure, and patterns follow standards
- Prevents test-specific shortcuts (panic usage only in tests, allowed unwrap, expect)

**Panics in Production vs Tests:**
- Clippy configuration: `allow-expect-in-tests = true`, `allow-unwrap-in-tests = true`
- In `clippy.toml` — allows these only in test code
- Production code must return `Result<T, E>`

**PyO3 Test Collection Prevention:**
- Classes named `Test*` (e.g., `TestClock`) are utility classes from Rust, not test classes
- Custom `pytest_pycollect_makeitem()` hook in `conftest.py` prevents pytest collection
- Prevents pytest-asyncio from trying to set attributes on immutable PyO3 types

## Benchmarking

**Rust Benchmarks:**
- Framework: `criterion` (from dev-dependencies)
- Alternative: `iai` for instruction count benchmarks
- Location: `crates/{crate}/benches/*.rs`
- Run: `cargo bench` or `cargo bench --bench {name}`

Example from workspace:
```bash
cargo bench --bench correctness    # Correctness benchmarks
cargo bench --bench time           # Time measurement benchmarks
cargo bench --bench stack_str_iai  # Instruction count analysis
```

**Python Performance Tests:**
- Framework: `pytest-benchmark` (from test dependencies)
- Coverage testing: `pytest-codspeed` for tracking

## Test Helpers

**Rust Test Utilities:**
- Location: `crates/testkit/` (nautilus-testkit crate)
- Provides: Stub generators, test fixtures, utility functions
- Import: `use nautilus_testkit::stubs::*;`

**Python Test Utilities:**
- Location: `nautilus_trader/test_kit/`
- Provides: `TestDataProvider`, `TestInstrumentProvider`, test fixtures
- Modules: `providers.py`, `stubs.py`

---

*Testing analysis: 2026-06-13*
