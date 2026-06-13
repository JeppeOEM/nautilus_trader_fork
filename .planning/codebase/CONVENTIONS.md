# Coding Conventions

**Analysis Date:** 2026-06-13

## Naming Patterns

**Files:**
- Rust: snake_case (e.g., `correctness.rs`, `stack_str.rs`, `test_market_data_capnp.rs`)
- Python: snake_case (e.g., `config.py`, `transformers.py`, `test_*.py`)
- Test files: Prefix with `test_` for unit/integration tests, or `*_test.rs` for Rust

**Functions:**
- Rust: snake_case (e.g., `check_predicate_true()`, `decode_array()`, `nanos_since_unix_epoch()`)
- Python: snake_case (e.g., `from_str()`, `with_logging()`)
- Rust test functions: descriptive snake_case with prefix (e.g., `test_check_predicate_true()`)
- Python test functions: `test_` prefix followed by descriptive name

**Variables:**
- Rust: snake_case for local variables and struct fields
- Python: snake_case for variables, PascalCase for classes
- Private/internal: Use underscore prefix sparingly (Rust convention is module privacy, not `_`)

**Types:**
- Rust structs/enums: PascalCase (e.g., `QuoteTick`, `CorrectnessError`, `OrderBookDelta`)
- Rust traits: PascalCase (e.g., `FromCapnp`, `ToCapnp`)
- Python classes: PascalCase (e.g., `RiskEngineConfig`)
- Rust type aliases: PascalCase (e.g., `CorrectnessResult<T>`)

## Code Style

**Formatting:**
- Rust: `cargo +nightly fmt` with rustfmt
- Python: `ruff format` (line-length: 100 characters)
- Both: Enforced via pre-commit hooks

**Rustfmt Config:** (`rustfmt.toml`)
```toml
group_imports = "StdExternalCrate"
imports_granularity = "Crate"
```

**Linting:**

**Rust:** clippy with comprehensive pedantic checks:
- `cargo clippy` enforces over 100 rules configured in `Cargo.toml` workspace lints
- Cognitive complexity threshold: 10 (configured in `clippy.toml`)
- Disallowed methods: `getrandom::fill`, `getrandom::u32`, `getrandom::u64` (DST seed control)
- Disallowed types: `tokio::task::LocalSet` (not available under madsim runtime)
- Key denies: `unreachable_pub`, `unexpected_cfgs`, `unsafe_code`, `nonstandard_style`
- Key warns: redundant clones, needless code, performance issues, code simplification patterns

**Python:** ruff with strict ruleset:
- Selected rules: `C4`, `E`, `F`, `W`, `C90`, `D`, `DTZ`, `UP`, `S`, `T10`, `ICN`, `PIE`, `PT`, `PYI`, `Q`, `I`, `RSE`, `TID`, `SIM`, `B`, `PERF`, `FURB`, `ISC`, `FLY`, `LOG`, `ASYNC`, `PD`, `PGH`, `PLE`, `PLW`, `NPY`, `RUF`
- Line length: 100 characters
- Target: Python 3.12+
- Type checking: mypy with `disallow_incomplete_defs = true`

## Import Organization

**Order (Rust):**
1. Standard library (`std::*`)
2. Workspace/external crates
3. Internal crate modules
4. Conditional imports (`#[cfg(...)]`)

Example from `test_market_data_capnp.rs`:
```rust
use nautilus_model::{
    data::{Bar, BarSpecification, BarType, ...},
    enums::{AggregationSource, ...},
    identifiers::InstrumentId,
    types::{Price, Quantity},
};
use nautilus_serialization::capnp::{FromCapnp, ToCapnp, market_capnp};
use rstest::rstest;
use rust_decimal_macros::dec;
use ustr::Ustr;
```

**Order (Python):**
1. `from __future__ import annotations` (at top)
2. Standard library imports
3. Third-party imports
4. Local imports
- Ruff enforces via `isort` plugin (`I`)

Example from `config.py`:
```python
from __future__ import annotations

from nautilus_trader.common.config import NautilusConfig
```

**Path Aliases:**
- Rust: Internal crates referenced by full workspace path (e.g., `nautilus_common::`, `nautilus_core::`)
- Python: Module-relative imports using absolute package names (e.g., `from nautilus_trader.model.identifiers import ...`)

## Error Handling

**Patterns:**

**Rust:**
- Primary: `Result<T, E>` type alias `CorrectnessResult<T>` from `nautilus_core::correctness`
- Custom error enum: `CorrectnessError` with typed variants (PredicateViolation, EmptyString, NonAsciiString, etc.)
- Extension trait: `CorrectnessResultExt<T>` with `expect_display()` method
- Validation: Functions return `Result<()>` with descriptive errors (e.g., `check_predicate_true()`, `check_nonempty_string()`)
- Panic usage: Allowed in tests (configured in `clippy.toml`); use `.expect()` with message

Example from `correctness.rs`:
```rust
pub fn check_predicate_true(predicate: bool, fail_msg: &str) -> Result<()> {
    if !predicate {
        return Err(CorrectnessError::PredicateViolation {
            message: fail_msg.to_string(),
        });
    }
    Ok(())
}
```

**Python:**
- Exceptions: Propagate naturally, use built-in exceptions
- Validation: Return error-aware values or raise exceptions
- Logging: Log errors at appropriate level before raising

## Logging

**Framework:**
- Rust: `log` crate with macros `log::debug!()`, `log::warn!()`, `log::info!()`
- Python: `logging` module with standard levels

**Patterns:**

**Rust** (from `live/src/execution.rs`):
```rust
log::debug!("Flushed {count} deferred execution client instrument update(s)");
log::warn!("Failed to send order event: {e}");
```

**Python:**
- Use module-level logger: `logger = logging.getLogger(__name__)`
- Standard levels: DEBUG, INFO, WARNING, ERROR, CRITICAL

## Comments

**When to Comment:**
- Explain WHY, not WHAT (code already shows WHAT)
- Document non-obvious algorithmic decisions
- Mark safety justifications for `unsafe` code
- Use `// SAFETY:` prefix for unsafe code explanations

**JSDoc/Doc Comments:**

**Rust:** Every public item requires `///` doc comments:
- Module level: `//!` module documentation with examples
- Functions: Describe parameters, return value, panics, errors
- Example from `correctness.rs`:
```rust
/// A message prefix that can be used with calls to `expect` or other assertion-related functions.
///
/// This constant provides a standard message that can be used to indicate a failure condition
/// when a predicate or condition does not hold true. It is typically used in conjunction with
/// functions like `expect` to provide a consistent error message.
pub const FAILED: &str = "Condition failed";
```

**Python:** Docstrings required for all public functions/classes:
- Format: NumPy/Google style (multiline format shown in `RiskEngineConfig`)
- Example from `config.py`:
```python
class RiskEngineConfig(NautilusConfig, frozen=True):
    """
    Configuration for ``RiskEngine`` instances.

    Parameters
    ----------
    bypass : bool, default False
        If True, then will bypass all pre-trade risk checks and rate limits (will still check for duplicate IDs).
    ...
    """
```

## Function Design

**Size:**
- Cognitive complexity threshold (Rust): 10
- Guideline: Keep functions focused on single responsibility
- Break into smaller functions when exceeding cognitive complexity

**Parameters:**
- Rust: Use semantic types (e.g., `Price`, `Quantity` from `nautilus_model::types`) over primitives
- Python: Type hints required for all parameters
- Use builder patterns for complex configuration (e.g., `LiveNodeBuilder`)

**Return Values:**
- Rust: Prefer `Result<T>` for fallible operations
- Python: Return `None` for void operations, use type hints for return types
- Avoid bare exceptions; use typed results

Example from `correctness.rs` parameter validation:
```rust
pub fn check_nonempty_string<T: AsRef<str>>(s: T, param: &str) -> Result<()>
pub fn check_valid_string_ascii<T: AsRef<str>>(s: T, param: &str) -> Result<()>
```

## Module Design

**Exports:**
- Rust: All public exports explicitly declared in module declaration
- Python: Use `__all__` for public API (shown in `__init__.py` files)

**Barrel Files:**
- Rust: Modules re-exported from parent (e.g., `pub use self::core::*;`)
- Python: `__init__.py` files expose public API of subpackage

Example from `serialization/__init__.py`:
```python
__all__ = ["register_serializable_type"]
```

## Type Safety

**Rust:**
- Generic constraints required (e.g., `T: AsRef<str>`)
- Lifetime annotations on borrowed data
- Derive traits: `Debug` (required), `Clone`, `Copy`, `Eq`, `PartialEq` as appropriate

**Python:**
- Type hints: PEP 484 syntax, enforced by mypy with `disallow_incomplete_defs`
- Union types: Use `|` syntax (Python 3.10+)
- Optional: Use `Optional[T]` or `T | None`

## Testing Conventions

**Naming:**
- Rust: `#[test]` functions in `mod tests { ... }` block at module end
- Python: `test_*.py` files in `tests/` directory hierarchy
- Rust integration tests: `tests/test_*.rs` files
- Python fixtures: Defined in `conftest.py` with `@pytest.fixture` decorator

**Test Structure:**
- Arrange-Act-Assert (AAA) pattern
- One logical assertion per test
- Descriptive names: `test_{function}_{scenario}_{expected_result}`

**Mocking:**
- Rust: Use builder patterns and stub types (see `stub_trade_ethusdt_buyer()` in tests)
- Python: Use `pytest-mock` fixtures (from test dependencies)

## Special Conventions

**DST (Deterministic Simulation Testing):**
- Enforce in pre-commit hook: `check-dst-conventions`
- Ban: `getrandom::fill`, direct `tokio::task::LocalSet` usage
- Use: `nautilus_common::live::dst::task::spawn_local` instead

**PyO3 (Python Bindings):**
- Enforce in pre-commit hook: `check-pyo3-conventions`
- Name Rust types matching Python exposure pattern
- Use `#[pyo3(...)]` attributes for FFI

**Tokio Usage:**
- Enforce in pre-commit hook: `check-tokio-usage`
- DST runtime compatibility required
- Avoid OS RNG directly; use seeded alternatives

**Anyhow Error Handling:**
- Enforce in pre-commit hook: `check-anyhow-usage`
- Use for error context and message formatting
- Propagate with `?` operator

**Nautilus Type Usage:**
- Enforce in pre-commit hook: `check-nautilus-conventions`
- Use semantic types (`Price`, `Quantity`, `InstrumentId`, etc.)
- From `nautilus_model` workspace crate

---

*Convention analysis: 2026-06-13*
