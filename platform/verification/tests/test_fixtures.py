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
The committed wire fixtures (recorded from the live venues by `verification.tools.record_fixtures`
on 2026-09-29, 180 s per venue with one client-side reconnect at 90 s), read through the real
reader: every frame is filed where the classifier says, the Bybit books are a snapshot plus a
contiguous delta run per topic and connection, Hyperliquid's books are full and two-sided, and a
reconnect is present.
"""

import json
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from verification.domain.subscriptions import classify_frame
from verification.domain.subscriptions import rest_refusal
from verification.infrastructure.raw_store import FILE_SUFFIX
from verification.infrastructure.raw_store import iter_records
from verification.infrastructure.raw_store import venue_dir


_FIXTURES = Path(__file__).parent / "fixtures"
_BOOK_CHANNELS = ("linear.orderbook.50", "spot.orderbook.50")
_MIN_DELTAS = 200

Line = dict[str, object]


def _channels(venue: str) -> list[str]:
    return sorted(path.name for path in venue_dir(_FIXTURES, venue).iterdir() if path.is_dir())


def _lines(venue: str, channel: str) -> list[Line]:
    paths = sorted((venue_dir(_FIXTURES, venue) / channel).glob(f"*{FILE_SUFFIX}"))
    assert paths, f"no fixture for {venue} {channel}"
    return [line for path in paths for line in iter_records(path)]


def _segments(lines: list[Line]) -> Iterator[list[dict[str, Any]]]:
    """Yield the decoded frames of each connection (split at the channel's own `open` lines)."""
    segment: list[dict[str, Any]] | None = None
    for line in lines:
        if line["kind"] == "connection" and line["event"] == "open":
            if segment:
                yield segment
            segment = []
        elif line["kind"] == "frame":
            assert segment is not None, "a frame before any open line"
            segment.append(json.loads(str(line["raw"])))
    if segment:
        yield segment


@pytest.mark.parametrize("venue", ["BYBIT", "HYPERLIQUID"])
def test_every_fixture_line_is_filed_where_the_classifier_files_it(venue: str) -> None:
    for channel in _channels(venue):
        for line in _lines(venue, channel):
            if line["kind"] == "frame":
                classified = classify_frame(venue, str(line["endpoint"]), str(line["raw"]))
                assert (classified.channel, classified.anomaly) == (channel, None)
            elif line["kind"] == "rest":
                assert rest_refusal(venue, int(str(line["status"])), str(line["raw"])) is None
            else:
                assert line["kind"] == "connection"


@pytest.mark.parametrize(
    ("venue", "expected"),
    [
        (
            "BYBIT",
            {
                "linear.orderbook.50",
                "linear.publicTrade",
                "linear.tickers",
                "spot.orderbook.50",
                "spot.publicTrade",
                "linear.rest.instruments-info",
                "linear.rest.open-interest",
                "linear.rest.tickers",
                "linear.rest.recent-trade",
                "linear.rest.orderbook",
                "spot.rest.instruments-info",
                "spot.rest.recent-trade",
                "spot.rest.orderbook",
            },
        ),
        (
            "HYPERLIQUID",
            {"l2Book", "trades", "activeAssetCtx", "rest.metaAndAssetCtxs", "rest.l2Book"},
        ),
    ],
)
def test_every_recorded_channel_has_a_fixture(venue: str, expected: set[str]) -> None:
    assert expected <= set(_channels(venue))


@pytest.mark.parametrize("venue", ["BYBIT", "HYPERLIQUID"])
def test_a_client_side_reconnect_is_recorded_in_every_data_channel(venue: str) -> None:
    connection = _lines(venue, "connection")
    endpoints = {line["endpoint"] for line in connection}
    for endpoint in endpoints:
        events = [(l["event"], l["reason"]) for l in connection if l["endpoint"] == endpoint]
        assert events == [
            ("open", "startup"),
            ("close", "forced_reconnect"),
            ("open", "forced_reconnect"),
            ("close", "shutdown"),
        ]
    data = [c for c in _channels(venue) if ".rest." not in c and not c.startswith("rest.")]
    for channel in set(data) - {"connection", "control", "linear.control", "spot.control"}:
        assert len(list(_segments(_lines(venue, channel)))) == 2, channel


@pytest.mark.parametrize("channel", _BOOK_CHANNELS)
def test_bybit_books_are_a_snapshot_then_a_contiguous_delta_run(channel: str) -> None:
    topics_checked = 0
    for segment in _segments(_lines("BYBIT", channel)):
        topics = sorted({str(frame["topic"]) for frame in segment})
        for topic in topics:
            frames = [frame for frame in segment if frame["topic"] == topic]
            assert frames[0]["type"] == "snapshot", f"{topic}: the first frame after subscribing"
            deltas = frames[1:]
            assert len(deltas) >= _MIN_DELTAS
            assert {frame["type"] for frame in deltas} == {"delta"}
            ids = [int(frame["data"]["u"]) for frame in frames]
            assert ids == list(range(ids[0], ids[0] + len(ids))), f"{topic}: a gap in u"
            topics_checked += 1
    assert topics_checked == 4  # BTCUSDT and ETHUSDT, over two connections


def test_bybit_trades_and_tickers_carry_their_documented_fields() -> None:
    trade = next(f for s in _segments(_lines("BYBIT", "linear.publicTrade")) for f in s)
    assert {"T", "s", "S", "v", "p", "i"} <= set(trade["data"][0])
    ticker = next(f for s in _segments(_lines("BYBIT", "linear.tickers")) for f in s)
    assert ticker["type"] == "snapshot"
    assert {"markPrice", "indexPrice", "fundingRate", "openInterest"} <= set(ticker["data"])


def test_hyperliquid_books_are_full_and_two_sided() -> None:
    books = [frame for segment in _segments(_lines("HYPERLIQUID", "l2Book")) for frame in segment]
    assert len(books) >= 10
    for frame in books:
        bids, asks = frame["data"]["levels"]
        assert bids
        assert asks
        bid_prices = [Decimal(level["px"]) for level in bids]
        ask_prices = [Decimal(level["px"]) for level in asks]
        assert bid_prices == sorted(bid_prices, reverse=True)
        assert ask_prices == sorted(ask_prices)
        assert bid_prices[0] < ask_prices[0]


def test_hyperliquid_trades_and_asset_contexts_name_the_coin() -> None:
    trades = [f for s in _segments(_lines("HYPERLIQUID", "trades")) for f in s]
    assert trades
    assert all(trade["coin"] == "SOL" for frame in trades for trade in frame["data"])
    contexts = [f for s in _segments(_lines("HYPERLIQUID", "activeAssetCtx")) for f in s]
    assert contexts
    assert all(frame["data"]["coin"] == "SOL" for frame in contexts)
