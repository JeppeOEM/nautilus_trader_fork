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
"""Hyperliquid REST l2Book parse (payload shape from crates/adapters/hyperliquid/test_data/http_l2_book_btc.json)."""

from hyperliquid_collector.book_snapshot import parse_l2_book


def test_parse_l2_book_best_first_bids_then_asks() -> None:
    payload = {
        "coin": "BTC",
        "levels": [
            [{"px": "110427.0", "sz": "4.11882"}, {"px": "110426.0", "sz": "0.31694"}],
            [{"px": "110428.0", "sz": "3.72573"}, {"px": "110430.0", "sz": "0.03586"}],
        ],
        "time": 1761786491067,
    }
    bids, asks = parse_l2_book(payload)
    assert bids == [(110427.0, 4.11882), (110426.0, 0.31694)]
    assert asks == [(110428.0, 3.72573), (110430.0, 0.03586)]
