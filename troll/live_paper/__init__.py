"""
Live/paper-trading module (Story 3.1) -- see node.py for the AD-8 rationale.

As of Story 3.1, this module has zero imports from dydx_collector/ml_signals (confirmed via
grep, not assumption): it depends only on nautilus_trader and the stdlib. AC1 permits
depending on dydx_collector's/ml_signals' shared data types and pure utilities, never their
stateful internals -- Story 3.2 will be the first to actually exercise that allowance, via
ml_signals.indicators. Don't manufacture a dependency here that isn't needed yet.
"""
