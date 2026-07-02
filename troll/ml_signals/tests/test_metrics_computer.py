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
Unit tests for _book_metrics()'s stale/crossed-book gap handling.

top_of_book_series is monkeypatched to a fixed row sequence so the gap-reset
decision in _book_metrics can be tested in isolation from catalog I/O.
"""

from ml_signals import metrics_computer


class _FakeCatalog:
    """
    order_book_deltas() only needs to return something truthy -- the real
    rows come from the monkeypatched top_of_book_series below.
    """

    def order_book_deltas(self, instrument_ids: list[str], start: int) -> list[str]:
        return ["not-empty"]


def test_book_metrics_no_gap_accumulates_ofi(monkeypatch) -> None:
    rows = [
        (0, 100.0, 1.0, 101.0, 1.0),
        (1_000_000_000, 100.5, 1.0, 101.5, 1.0),
        (2_000_000_000, 101.0, 1.0, 102.0, 1.0),
    ]
    monkeypatch.setattr(metrics_computer, "top_of_book_series", lambda deltas, iid: iter(rows))

    result = metrics_computer._book_metrics(
        _FakeCatalog(), "BTC-USD-PERP.DYDX", now_ns=10_000_000_000
    )

    assert result["ofi"] is not None, "3 consecutive 1s-spaced updates must initialize OFI"


def test_book_metrics_resets_ofi_across_stale_gap(monkeypatch) -> None:
    """
    A >3s gap between top-of-book updates means the collector's crossed/stale
    guard (see collector.py's resync watchdog) skipped snapshots for a while --
    _book_metrics must not compute an OFI delta spanning that gap.
    """
    rows = [
        (0, 100.0, 1.0, 101.0, 1.0),
        (1_000_000_000, 100.5, 1.0, 101.5, 1.0),  # 2nd update -- OFI initializes here
        (5_000_000_000, 200.0, 1.0, 201.0, 1.0),  # >3s gap -- must reset first
    ]
    monkeypatch.setattr(metrics_computer, "top_of_book_series", lambda deltas, iid: iter(rows))

    result = metrics_computer._book_metrics(
        _FakeCatalog(), "BTC-USD-PERP.DYDX", now_ns=10_000_000_000
    )

    # After the reset, the post-gap row is only the first observation again --
    # OFI needs a second update to initialize, which never comes here.
    assert result["ofi"] is None, "OFI must reset (un-initialize) across a stale/crossed-book gap"
    assert result["microprice"] is not None  # microprice has no windowed history to corrupt


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
