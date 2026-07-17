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
Integration test: backtest_dydx.run() backtests every coin in the live Watchlist (or an
explicit coin-set) via a single BacktestNode call, with per-coin results distinguishable
(Story 2.4, AC1-AC4).

Only ONE BacktestNode construction in this file, deliberately -- see deferred-work.md ("Story
2.2 implementation" and its Story 2.3 addenda) for the confirmed, reproducible native-crash
pattern this avoids. Named to collect alphabetically after test_ofi_strategy.py
('test_w' > 'test_o') for the same reason. Every other test function below either constructs no
BacktestNode at all (run() returns before reaching it, e.g. when every symbol is skipped) or
replaces backtest_dydx.BacktestNode with a fake class entirely, so none of them count against
that one-construction budget.

test_multi_coin_backtest_skips_missing_instruments_and_dedupes_duplicates_while_keeping_per_coin_results_distinguishable
covers AC1 (Watchlist as instrument universe, via a monkeypatched backtest_dydx.fetch_watchlist --
fetch_watchlist() itself requires a live dashboard process, so it is never invoked for real in a
test; this monkeypatches the function directly rather than urlopen, a different layer than
test_watchlist.py's own urlopen-monkeypatch tests, since here we only need to control run()'s
input, not exercise the HTTP/JSON-parsing path Story 1.3 already covers separately), AC2 (no
manual per-coin config editing -- backtest_dydx.run() builds every BacktestRunConfig from a plain
symbols loop), and AC4 (per-coin results distinguishable -- asserts the two coins' BTC/ETH price
series produce different iteration counts). This asserts each symbol's own iteration count is
correct, which rules out one class of bug (e.g. the wrong catalog data or symbol being fed to the
wrong result) but does not by itself prove there is zero venue/portfolio state cross-talk between
the two BacktestEngine instances -- each is a fully independent BacktestRunConfig with its own
venue name and account, so cross-talk would require Nautilus's own engine isolation to be broken,
which is out of this story's scope to re-verify.
"""

import logging
import tempfile
import time
from decimal import Decimal

import pytest
from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import ETH
from nautilus_trader.model.currencies import USDC
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from ml_signals import backtest_dydx

_BTC_IID = InstrumentId(Symbol("BTC-USD-PERP"), Venue("DYDX"))
_ETH_IID = InstrumentId(Symbol("ETH-USD-PERP"), Venue("DYDX"))


def _instrument(iid: InstrumentId, base) -> CryptoPerpetual:
    return CryptoPerpetual(
        instrument_id=iid, raw_symbol=iid.symbol,
        base_currency=base, quote_currency=USDC, settlement_currency=USDC, is_inverse=False,
        price_precision=1, size_precision=3,
        price_increment=Price(0.1, 1), size_increment=Quantity(0.001, 3),
        max_quantity=None, min_quantity=None, max_notional=None, min_notional=None,
        max_price=None, min_price=None,
        margin_init=Decimal("0.1"), margin_maint=Decimal("0.05"),
        maker_fee=Decimal("0.0002"), taker_fee=Decimal("0.0005"),
        ts_event=0, ts_init=0,
    )


def _trades(iid: InstrumentId, n: int, now_ns: int) -> list[TradeTick]:
    return [
        TradeTick(
            instrument_id=iid, price=Price(100.0 + (i % 10) * 0.5, 1), size=Quantity(1.0, 3),
            aggressor_side=AggressorSide.BUYER if i % 2 == 0 else AggressorSide.SELLER,
            trade_id=TradeId(str(i)), ts_event=now_ns - (n - i) * 1_000_000_000, ts_init=now_ns - (n - i) * 1_000_000_000,
        )
        for i in range(n)
    ]


def test_multi_coin_backtest_skips_missing_instruments_and_dedupes_duplicates_while_keeping_per_coin_results_distinguishable(
    monkeypatch, caplog,
) -> None:
    now_ns = time.time_ns()
    with tempfile.TemporaryDirectory() as tmp:
        catalog = ParquetDataCatalog(tmp)
        catalog.write_data([_instrument(_BTC_IID, BTC), _instrument(_ETH_IID, ETH)])
        catalog.write_data(_trades(_BTC_IID, 200, now_ns))
        catalog.write_data(_trades(_ETH_IID, 80, now_ns))

        # BTC listed twice (dedup, AC2) plus SOL, which has no catalog instrument (Task 2 skip).
        monkeypatch.setattr(
            backtest_dydx,
            "fetch_watchlist",
            lambda: [
                "BTC-USD-PERP.DYDX", "BTC-USD-PERP.DYDX", "ETH-USD-PERP.DYDX", "SOL-USD-PERP.DYDX",
            ],
        )

        with caplog.at_level(logging.WARNING):
            results = backtest_dydx.run(symbols=None, catalog_path=tmp, bar_interval="1-SECOND")

        assert set(results.keys()) == {"BTC-USD-PERP.DYDX", "ETH-USD-PERP.DYDX"}
        assert results["BTC-USD-PERP.DYDX"].iterations == 200
        assert results["ETH-USD-PERP.DYDX"].iterations == 80
        assert "SOL-USD-PERP.DYDX" in caplog.text


def test_run_returns_empty_dict_when_no_symbol_matches_the_catalog_and_re_resolves_the_watchlist_fresh_each_call(
    monkeypatch, caplog,
) -> None:
    """AC3: re-running with a changed Watchlist changes the effective coin-set with no code
    change -- proven here via two sequential calls with different mocked fetch_watchlist results,
    both against a catalog matching neither, so run() returns {} before ever touching
    BacktestNode (no construction in this test)."""
    with tempfile.TemporaryDirectory() as tmp:
        catalog = ParquetDataCatalog(tmp)
        catalog.write_data([_instrument(_BTC_IID, BTC)])  # present, but never requested below

        monkeypatch.setattr(backtest_dydx, "fetch_watchlist", lambda: ["ETH-USD-PERP.DYDX"])
        with caplog.at_level(logging.WARNING):
            assert backtest_dydx.run(symbols=None, catalog_path=tmp) == {}
        assert "ETH-USD-PERP.DYDX" in caplog.text
        caplog.clear()

        monkeypatch.setattr(backtest_dydx, "fetch_watchlist", lambda: ["SOL-USD-PERP.DYDX"])
        with caplog.at_level(logging.WARNING):
            assert backtest_dydx.run(symbols=None, catalog_path=tmp) == {}
        assert "SOL-USD-PERP.DYDX" in caplog.text


def test_run_rejects_a_bare_str_symbols_argument() -> None:
    """A bare str would otherwise be iterated character-by-character as bogus single-char symbols."""
    with pytest.raises(TypeError, match="list\\[str\\]"):
        backtest_dydx.run(symbols="BTC-USD-PERP.DYDX")


def test_run_wraps_a_fetch_watchlist_failure_in_a_clear_runtime_error(monkeypatch) -> None:
    def _raise() -> list[str]:
        raise ValueError("dashboard unreachable")

    monkeypatch.setattr(backtest_dydx, "fetch_watchlist", _raise)

    with pytest.raises(RuntimeError, match="dashboard") as exc_info:
        backtest_dydx.run(symbols=None)
    assert isinstance(exc_info.value.__cause__, ValueError)


def test_run_correctly_attributes_results_when_backtestnode_drops_one_config(monkeypatch) -> None:
    """The entire reason run() matches results back to symbols via config.id/run_config_id rather
    than list position: BacktestNode.run() silently drops a config's result if raise_exception is
    False (the default) and that config's engine build/run fails internally, so the returned list
    can be shorter than the configs list. Simulated here via a fake BacktestNode -- no real
    BacktestEngine/BacktestNode construction, so this doesn't affect the file's crash-mitigation
    budget."""

    class _FakeResult:
        def __init__(self, run_config_id: str) -> None:
            self.run_config_id = run_config_id

    class _FakeNode:
        def __init__(self, configs) -> None:
            self._configs = configs

        def run(self):
            # Drop the first config's result, as a real raise_exception=False failure would.
            return [_FakeResult(config.id) for config in self._configs[1:]]

        def dispose(self) -> None:
            pass

    monkeypatch.setattr(backtest_dydx, "BacktestNode", _FakeNode)
    monkeypatch.setattr(
        backtest_dydx, "fetch_watchlist", lambda: ["BTC-USD-PERP.DYDX", "ETH-USD-PERP.DYDX"],
    )

    with tempfile.TemporaryDirectory() as tmp:
        catalog = ParquetDataCatalog(tmp)
        catalog.write_data([_instrument(_BTC_IID, BTC), _instrument(_ETH_IID, ETH)])

        results = backtest_dydx.run(symbols=None, catalog_path=tmp)

    # BTC's config was built first and is the one _FakeNode drops -- only ETH should survive,
    # correctly attributed by id rather than by position.
    assert set(results.keys()) == {"ETH-USD-PERP.DYDX"}


if __name__ == "__main__":
    test_multi_coin_backtest_skips_missing_instruments_and_dedupes_duplicates_while_keeping_per_coin_results_distinguishable()
    print("ok")
