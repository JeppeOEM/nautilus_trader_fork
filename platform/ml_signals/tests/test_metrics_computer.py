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
"""Unit tests for compute_snapshot()'s price-stats/book-metrics merge."""

from ml_signals import metrics_computer


class _FakeCatalog:
    """order_book_deltas() is never called -- book_metrics_fn is always given."""


def test_compute_snapshot_merges_price_stats_and_book_metrics_fn(monkeypatch) -> None:
    monkeypatch.setattr(
        metrics_computer, "price_stats", lambda catalog, iid, start_ns: {"price": 1.0}
    )

    result = metrics_computer.compute_snapshot(
        catalog=_FakeCatalog(),
        instrument_id="BTC-USD-PERP.DYDX",
        now_ns=10_000_000_000,
        book_metrics_fn=lambda iid: {"ofi": 0.5, "microprice": 100.5, "spread": 1.0},
    )

    assert result["price"] == 1.0
    assert result["ofi"] == 0.5
    assert result["microprice"] == 100.5
    assert result["spread"] == 1.0


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
