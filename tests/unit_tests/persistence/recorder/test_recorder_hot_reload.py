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
Unit tests for the recorder's hot-reload config-diff machinery (HOT-01).

The seven non-addition behaviors (timer registration, diff, removal, depth-swap
ordering, bar-interval delta, threshold warning, linear gating) are exercised here
with mocked subscribe/unsubscribe spies and a real ``Cache``/``TestClock`` (Plan
06-01 Task 3). The three runtime-instrument-load behaviors
(``addition_subscribes``, ``failed_not_retried``, ``failed_resets_on_change``)
require the Pattern-3 mock data client and the ADD branch, which land in Plan 02,
so they are explicitly skipped here to keep the suite green between waves.
"""

import logging

from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from tests.unit_tests.persistence.recorder.test_recorder_strategy import _build_strategy


INSTRUMENT_ID_LINEAR = InstrumentId.from_str("BTCUSDT-LINEAR.BYBIT")
INSTRUMENT_ID_SPOT = InstrumentId.from_str("ETHUSDT-SPOT.BYBIT")
# A brand-new instrument not present at startup (the hot-add subject).
INSTRUMENT_ID_NEW = InstrumentId.from_str("SOLUSDT-LINEAR.BYBIT")


def _make_mock_client(mocker, find_returns):
    """
    Build a mock Bybit data client whose ``instrument_provider`` drives the
    two-phase load.

    Parameters
    ----------
    mocker : MockerFixture
        The pytest-mock fixture.
    find_returns : list
        The sequence of return values for successive ``provider.find(id)`` calls
        (e.g. ``[None, instrument]`` simulates "load in flight on poll N, resolved
        on poll N+1"). The last value is repeated for any further calls.

    Returns
    -------
    Mock
        A mock client with ``.instrument_provider.find`` / ``.load`` /
        ``._cache_instruments`` and a no-op ``client._cache_instruments``.
    """
    client = mocker.Mock()
    provider = client.instrument_provider

    seq = list(find_returns)

    def _find(_instrument_id):
        if len(seq) > 1:
            return seq.pop(0)
        return seq[0] if seq else None

    provider.find.side_effect = _find
    provider.load.return_value = None
    # client._cache_instruments() repopulates the WS/HTTP precision caches; a
    # no-op mock keeps the unit test off the real adapter machinery (A1).
    client._cache_instruments.return_value = None
    return client


def _make_parsed_cfg(instruments):
    """
    Build a minimal parsed ``RecorderConfig`` carrying just the instrument list.

    ``_diff_config``/``_config_signature`` only read ``parsed_cfg.instruments``
    (each entry's ``id``, ``depth``, ``bar_intervals``, ``product_type``), so the
    other ``RecorderConfig`` fields are filled with throwaway-but-valid values.
    """
    from scripts.bybit_recorder.config import RecorderConfig

    return RecorderConfig(
        trader_id="BYBIT-COLLECTOR-001",
        catalog_path="catalog",
        streaming_path="catalog/streaming",
        instruments=instruments,
    )


def _make_entry(instrument_id, depth, bar_intervals, product_type):
    from scripts.bybit_recorder.config import InstrumentEntry

    return InstrumentEntry(
        id=instrument_id,
        depth=depth,
        bar_intervals=bar_intervals,
        product_type=product_type,
    )


def _patch_all_feed_methods(mocker, strategy):
    """
    Patch every subscribe/unsubscribe method on the strategy and attach them to a
    single parent mock so call-ORDER across methods can be asserted.
    """
    parent = mocker.Mock()
    for name in (
        "subscribe_trade_ticks",
        "subscribe_quote_ticks",
        "subscribe_order_book_deltas",
        "subscribe_bars",
        "subscribe_mark_prices",
        "subscribe_index_prices",
        "subscribe_funding_rates",
        "unsubscribe_trade_ticks",
        "unsubscribe_quote_ticks",
        "unsubscribe_order_book_deltas",
        "unsubscribe_bars",
        "unsubscribe_mark_prices",
        "unsubscribe_index_prices",
        "unsubscribe_funding_rates",
    ):
        parent.attach_mock(mocker.patch.object(strategy, name), name)
    return parent


def test_timer_registered(mocker, mock_cache):
    # Arrange
    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    mock_cache.add_instrument(instrument)
    strategy, clock = _build_strategy(mocker, mock_cache, [instrument.id])
    _patch_all_feed_methods(mocker, strategy)

    # Act
    strategy.on_start()

    # Assert
    assert "config-reload" in clock.timer_names


def test_detects_addition(mocker, mock_cache):
    # Arrange: one instrument currently subscribed, parsed config adds a second.
    strategy, _ = _build_strategy(mocker, mock_cache, [INSTRUMENT_ID_LINEAR])
    strategy._subscribed_params[INSTRUMENT_ID_LINEAR] = (50, frozenset({"1-MINUTE"}))
    strategy._product_types[INSTRUMENT_ID_LINEAR] = "linear"

    new_entry = _make_entry(INSTRUMENT_ID_SPOT, 50, ["1-MINUTE"], "spot")
    parsed_cfg = _make_parsed_cfg(
        [
            _make_entry(INSTRUMENT_ID_LINEAR, 50, ["1-MINUTE"], "linear"),
            new_entry,
        ],
    )

    # Act
    additions, removals, param_changes = strategy._diff_config(parsed_cfg)

    # Assert
    assert [e.id for e in additions] == [INSTRUMENT_ID_SPOT]
    assert removals == []
    assert param_changes == []


def _new_instrument_stub():
    """
    Return a stub `Instrument` whose id is `INSTRUMENT_ID_NEW`.

    Built from a real `TestInstrumentProvider` instrument so `cache.add_instrument`
    accepts it; the cache lookup in the ADD branch matches on the id the provider
    `find` returns.
    """
    from nautilus_trader.model.instruments import CryptoPerpetual
    from nautilus_trader.model.objects import Price
    from nautilus_trader.model.objects import Quantity

    base = TestInstrumentProvider.btcusdt_perp_binance()
    return CryptoPerpetual(
        instrument_id=INSTRUMENT_ID_NEW,
        raw_symbol=base.raw_symbol,
        base_currency=base.base_currency,
        quote_currency=base.quote_currency,
        settlement_currency=base.settlement_currency,
        is_inverse=False,
        price_precision=base.price_precision,
        size_precision=base.size_precision,
        price_increment=base.price_increment,
        size_increment=base.size_increment,
        margin_init=base.margin_init,
        margin_maint=base.margin_maint,
        maker_fee=base.maker_fee,
        taker_fee=base.taker_fee,
        ts_event=0,
        ts_init=0,
        max_quantity=Quantity.from_str("1000"),
        min_quantity=Quantity.from_str("0.001"),
        max_price=Price.from_str("1000000"),
        min_price=Price.from_str("0.01"),
    )


def _seed_running(strategy, instrument_id, depth=50, intervals=("1-MINUTE",), product="linear"):
    strategy._subscribed_params[instrument_id] = (depth, frozenset(intervals))
    strategy._product_types[instrument_id] = product


def test_addition_subscribes(mocker, mock_cache, tmp_path):
    # A new id absent at startup is loaded at runtime (two-phase) and subscribed
    # once the provider confirms it: find() returns None on poll N (schedule the
    # load) then the instrument on poll N+1 (confirm + cache + subscribe).
    new_instrument = _new_instrument_stub()
    client = _make_mock_client(mocker, find_returns=[None, new_instrument])
    strategy, _ = _build_strategy(
        mocker,
        mock_cache,
        [INSTRUMENT_ID_LINEAR],
        linear_instrument_ids=[INSTRUMENT_ID_LINEAR],
        reload_config_path=str(tmp_path / "recorder.toml"),
        data_client=client,
    )
    _seed_running(strategy, INSTRUMENT_ID_LINEAR)
    parent = _patch_all_feed_methods(mocker, strategy)

    # Parsed config adds the new LINEAR instrument alongside the existing one.
    parsed = _make_parsed_cfg(
        [
            _make_entry(INSTRUMENT_ID_LINEAR, 50, ["1-MINUTE"], "linear"),
            _make_entry(INSTRUMENT_ID_NEW, 50, ["1-MINUTE"], "linear"),
        ],
    )
    mocker.patch(
        "scripts.bybit_recorder.strategy.load_recorder_config",
        return_value=(parsed, []),
    )

    # Poll N: load scheduled, NO subscribe yet, id pending.
    strategy._on_config_reload(event=None)
    client.instrument_provider.load.assert_called_once_with(INSTRUMENT_ID_NEW)
    parent.subscribe_trade_ticks.assert_not_called()
    assert INSTRUMENT_ID_NEW not in strategy._subscribed_params

    # Poll N+1: find resolves -> confirm + cache + subscribe full feed set.
    strategy._on_config_reload(event=None)

    parent.subscribe_trade_ticks.assert_called_once_with(INSTRUMENT_ID_NEW)
    parent.subscribe_quote_ticks.assert_called_once_with(INSTRUMENT_ID_NEW)
    parent.subscribe_order_book_deltas.assert_called_once()
    parent.subscribe_bars.assert_called_once()
    # Linear => mark/index/funding also subscribed.
    parent.subscribe_mark_prices.assert_called_once_with(INSTRUMENT_ID_NEW)
    parent.subscribe_index_prices.assert_called_once_with(INSTRUMENT_ID_NEW)
    parent.subscribe_funding_rates.assert_called_once_with(INSTRUMENT_ID_NEW)
    # Bookkeeping updated: subscribed params recorded, count incremented, cache
    # populated, precision caches repopulated, load NOT re-scheduled.
    assert INSTRUMENT_ID_NEW in strategy._subscribed_params
    assert strategy._hot_added_count == 1
    assert mock_cache.instrument(INSTRUMENT_ID_NEW) is not None
    client._cache_instruments.assert_called_once()
    client.instrument_provider.load.assert_called_once()  # still once, not re-loaded


def test_removal_unsubscribes(mocker, mock_cache, tmp_path):
    # Arrange: a linear instrument is currently subscribed; the reloaded toml is
    # empty (instrument removed). _on_config_reload must unsubscribe every feed
    # and prune all _last_seen entries for that id.
    strategy, _ = _build_strategy(
        mocker,
        mock_cache,
        [INSTRUMENT_ID_LINEAR],
        reload_config_path=str(tmp_path / "recorder.toml"),
    )
    strategy._subscribed_params[INSTRUMENT_ID_LINEAR] = (50, frozenset({"1-MINUTE"}))
    strategy._product_types[INSTRUMENT_ID_LINEAR] = "linear"
    # Seed _last_seen with one entry per stream for the to-be-removed instrument.
    for stream in ("trade", "quote", "deltas", "bar", "mark", "index", "funding"):
        strategy._last_seen[(stream, INSTRUMENT_ID_LINEAR)] = 1
    parent = _patch_all_feed_methods(mocker, strategy)

    # Empty parsed config => removal.
    mocker.patch(
        "scripts.bybit_recorder.strategy.load_recorder_config",
        return_value=(_make_parsed_cfg([]), []),
    )

    # Act
    strategy._on_config_reload(event=None)

    # Assert: every unsubscribe_* called for the removed id ...
    parent.unsubscribe_trade_ticks.assert_called_once_with(INSTRUMENT_ID_LINEAR)
    parent.unsubscribe_quote_ticks.assert_called_once_with(INSTRUMENT_ID_LINEAR)
    parent.unsubscribe_order_book_deltas.assert_called_once_with(INSTRUMENT_ID_LINEAR)
    parent.unsubscribe_bars.assert_called_once()
    parent.unsubscribe_mark_prices.assert_called_once_with(INSTRUMENT_ID_LINEAR)
    parent.unsubscribe_index_prices.assert_called_once_with(INSTRUMENT_ID_LINEAR)
    parent.unsubscribe_funding_rates.assert_called_once_with(INSTRUMENT_ID_LINEAR)
    # ... and zero _last_seen entries remain for the removed id.
    assert not [k for k in strategy._last_seen if k[1] == INSTRUMENT_ID_LINEAR]
    assert INSTRUMENT_ID_LINEAR not in strategy._subscribed_params


def test_depth_swap_order(mocker, mock_cache):
    # Arrange: a depth change must unsubscribe-then-subscribe (Pitfall 2 order).
    strategy, _ = _build_strategy(mocker, mock_cache, [INSTRUMENT_ID_LINEAR])
    parent = _patch_all_feed_methods(mocker, strategy)

    # Act
    strategy._apply_param_change(
        INSTRUMENT_ID_LINEAR,
        old_depth=50,
        old_intervals=["1-MINUTE"],
        new_depth=200,
        new_intervals=["1-MINUTE"],
    )

    # Assert: unsubscribe_order_book_deltas is called BEFORE subscribe_order_book_deltas.
    order_calls = [
        c[0]
        for c in parent.mock_calls
        if c[0] in ("unsubscribe_order_book_deltas", "subscribe_order_book_deltas")
    ]
    assert order_calls == ["unsubscribe_order_book_deltas", "subscribe_order_book_deltas"]
    # And the new subscription uses the new depth.
    _, kwargs = parent.subscribe_order_book_deltas.call_args
    assert kwargs["depth"] == 200
    # bar intervals unchanged => no bar sub/unsub.
    parent.subscribe_bars.assert_not_called()
    parent.unsubscribe_bars.assert_not_called()


def test_bar_interval_delta(mocker, mock_cache):
    # Arrange
    strategy, _ = _build_strategy(mocker, mock_cache, [INSTRUMENT_ID_LINEAR])
    parent = _patch_all_feed_methods(mocker, strategy)

    # Act: add a 5-MINUTE interval (depth unchanged) -> only subscribe_bars(5-MINUTE).
    strategy._apply_param_change(
        INSTRUMENT_ID_LINEAR,
        old_depth=50,
        old_intervals=["1-MINUTE"],
        new_depth=50,
        new_intervals=["1-MINUTE", "5-MINUTE"],
    )

    # Assert: exactly one subscribe_bars for the ADDED interval; no unsubscribe; no
    # depth swap (depth unchanged).
    parent.subscribe_bars.assert_called_once()
    (added_bar_type,), _ = parent.subscribe_bars.call_args
    assert "5-MINUTE" in str(added_bar_type)
    parent.unsubscribe_bars.assert_not_called()
    parent.unsubscribe_order_book_deltas.assert_not_called()
    parent.subscribe_order_book_deltas.assert_not_called()

    # Reverse case: remove the 5-MINUTE interval -> only unsubscribe_bars(5-MINUTE).
    parent.reset_mock()
    strategy._apply_param_change(
        INSTRUMENT_ID_LINEAR,
        old_depth=50,
        old_intervals=["1-MINUTE", "5-MINUTE"],
        new_depth=50,
        new_intervals=["1-MINUTE"],
    )
    parent.unsubscribe_bars.assert_called_once()
    (removed_bar_type,), _ = parent.unsubscribe_bars.call_args
    assert "5-MINUTE" in str(removed_bar_type)
    parent.subscribe_bars.assert_not_called()


def test_failed_not_retried(mocker, mock_cache, tmp_path, caplog):
    # A Bybit-unknown id: find() returns None on BOTH polls. Poll N schedules the
    # load (pending); poll N+1 sees it still unresolved -> ERROR once, mark failed,
    # NO subscribe. A third poll with the SAME toml does NOT re-load (single
    # attempt per snapshot, D-07/D-11).
    client = _make_mock_client(mocker, find_returns=[None])  # always None
    strategy, _ = _build_strategy(
        mocker,
        mock_cache,
        [INSTRUMENT_ID_LINEAR],
        linear_instrument_ids=[INSTRUMENT_ID_LINEAR],
        reload_config_path=str(tmp_path / "recorder.toml"),
        data_client=client,
    )
    _seed_running(strategy, INSTRUMENT_ID_LINEAR)
    parent = _patch_all_feed_methods(mocker, strategy)

    parsed = _make_parsed_cfg(
        [
            _make_entry(INSTRUMENT_ID_LINEAR, 50, ["1-MINUTE"], "linear"),
            _make_entry(INSTRUMENT_ID_NEW, 50, ["1-MINUTE"], "linear"),
        ],
    )
    mocker.patch(
        "scripts.bybit_recorder.strategy.load_recorder_config",
        return_value=(parsed, []),
    )

    # Poll N: schedule load (pending). Poll N+1: still None -> ERROR + mark failed.
    strategy._on_config_reload(event=None)
    with caplog.at_level(logging.ERROR, logger="scripts.bybit_recorder.strategy"):
        strategy._on_config_reload(event=None)

    assert INSTRUMENT_ID_NEW in strategy._failed_instrument_ids
    assert any(str(INSTRUMENT_ID_NEW) in r.getMessage() for r in caplog.records)
    parent.subscribe_trade_ticks.assert_not_called()

    # Poll 3 with the SAME toml signature: failed id is NOT re-loaded.
    strategy._on_config_reload(event=None)
    client.instrument_provider.load.assert_called_once_with(INSTRUMENT_ID_NEW)


def test_failed_resets_on_change(mocker, mock_cache, tmp_path):
    # After a failed id, changing the parsed config SIGNATURE clears
    # _failed_instrument_ids so the id is retried (D-07 reset).
    client = _make_mock_client(mocker, find_returns=[None])
    strategy, _ = _build_strategy(
        mocker,
        mock_cache,
        [INSTRUMENT_ID_LINEAR],
        linear_instrument_ids=[INSTRUMENT_ID_LINEAR],
        reload_config_path=str(tmp_path / "recorder.toml"),
        data_client=client,
    )
    _seed_running(strategy, INSTRUMENT_ID_LINEAR)
    _patch_all_feed_methods(mocker, strategy)

    parsed_fail = _make_parsed_cfg(
        [
            _make_entry(INSTRUMENT_ID_LINEAR, 50, ["1-MINUTE"], "linear"),
            _make_entry(INSTRUMENT_ID_NEW, 50, ["1-MINUTE"], "linear"),
        ],
    )
    load_mock = mocker.patch(
        "scripts.bybit_recorder.strategy.load_recorder_config",
        return_value=(parsed_fail, []),
    )

    # Drive the failure (poll N schedule, poll N+1 mark failed).
    strategy._on_config_reload(event=None)
    strategy._on_config_reload(event=None)
    assert INSTRUMENT_ID_NEW in strategy._failed_instrument_ids
    assert client.instrument_provider.load.call_count == 1

    # Now the toml signature changes (depth edit on the failed id) -> reset.
    parsed_changed = _make_parsed_cfg(
        [
            _make_entry(INSTRUMENT_ID_LINEAR, 50, ["1-MINUTE"], "linear"),
            _make_entry(INSTRUMENT_ID_NEW, 200, ["1-MINUTE"], "linear"),
        ],
    )
    load_mock.return_value = (parsed_changed, [])

    strategy._on_config_reload(event=None)

    # Failed set cleared and the load re-attempted under the new signature.
    assert INSTRUMENT_ID_NEW not in strategy._failed_instrument_ids
    assert client.instrument_provider.load.call_count == 2


def test_threshold_warning(mocker, mock_cache, caplog):
    # Direct check: when _hot_added_count exceeds max_hot_added_instruments, the
    # threshold helper logs a WARNING (D-08, informational-only).
    strategy, _ = _build_strategy(
        mocker,
        mock_cache,
        [INSTRUMENT_ID_LINEAR],
        max_hot_added_instruments=1,
    )
    strategy._hot_added_count = 2

    with caplog.at_level(logging.WARNING):
        strategy._check_hot_added_threshold()

    assert any(
        "max_hot_added_instruments" in rec.message or "hot-add" in rec.message.lower()
        for rec in caplog.records
    )


def test_threshold_warning_through_add_branch_does_not_block(mocker, mock_cache, tmp_path, caplog):
    # End-to-end through the ADD branch: with max_hot_added_instruments already
    # exceeded, a hot-add still WARNs AND still subscribes (never blocks, D-08).
    new_instrument = _new_instrument_stub()
    client = _make_mock_client(mocker, find_returns=[None, new_instrument])
    strategy, _ = _build_strategy(
        mocker,
        mock_cache,
        [INSTRUMENT_ID_LINEAR],
        linear_instrument_ids=[INSTRUMENT_ID_LINEAR],
        reload_config_path=str(tmp_path / "recorder.toml"),
        max_hot_added_instruments=1,
        data_client=client,
    )
    _seed_running(strategy, INSTRUMENT_ID_LINEAR)
    # Pretend the lifetime count is already at the knob; the next add exceeds it.
    strategy._hot_added_count = 1
    parent = _patch_all_feed_methods(mocker, strategy)

    parsed = _make_parsed_cfg(
        [
            _make_entry(INSTRUMENT_ID_LINEAR, 50, ["1-MINUTE"], "linear"),
            _make_entry(INSTRUMENT_ID_NEW, 50, ["1-MINUTE"], "linear"),
        ],
    )
    mocker.patch(
        "scripts.bybit_recorder.strategy.load_recorder_config",
        return_value=(parsed, []),
    )

    strategy._on_config_reload(event=None)  # schedule
    with caplog.at_level(logging.WARNING, logger="scripts.bybit_recorder.strategy"):
        strategy._on_config_reload(event=None)  # confirm + subscribe

    # WARNING logged ...
    assert any("max_hot_added_instruments" in r.getMessage() for r in caplog.records)
    # ... but the subscribe still proceeded (never blocked).
    parent.subscribe_trade_ticks.assert_called_once_with(INSTRUMENT_ID_NEW)
    assert INSTRUMENT_ID_NEW in strategy._subscribed_params


def test_linear_gating(mocker, mock_cache, tmp_path):
    # Arrange: a SPOT instrument removal must NOT touch mark/index/funding; a
    # LINEAR removal MUST.
    strategy, _ = _build_strategy(
        mocker,
        mock_cache,
        [INSTRUMENT_ID_SPOT, INSTRUMENT_ID_LINEAR],
        reload_config_path=str(tmp_path / "recorder.toml"),
    )
    strategy._subscribed_params[INSTRUMENT_ID_SPOT] = (50, frozenset({"1-MINUTE"}))
    strategy._subscribed_params[INSTRUMENT_ID_LINEAR] = (50, frozenset({"1-MINUTE"}))
    strategy._product_types[INSTRUMENT_ID_SPOT] = "spot"
    strategy._product_types[INSTRUMENT_ID_LINEAR] = "linear"
    parent = _patch_all_feed_methods(mocker, strategy)

    # --- Spot removal: parsed config keeps only the linear instrument. ---
    mocker.patch(
        "scripts.bybit_recorder.strategy.load_recorder_config",
        return_value=(
            _make_parsed_cfg([_make_entry(INSTRUMENT_ID_LINEAR, 50, ["1-MINUTE"], "linear")]),
            [],
        ),
    )
    strategy._on_config_reload(event=None)

    # Spot removal does NOT touch mark/index/funding.
    assert all(
        INSTRUMENT_ID_SPOT not in [a for call in spy.call_args_list for a in call.args]
        for spy in (
            parent.unsubscribe_mark_prices,
            parent.unsubscribe_index_prices,
            parent.unsubscribe_funding_rates,
        )
    )

    # --- Linear removal: parsed config now empty. ---
    parent.reset_mock()
    mocker.patch(
        "scripts.bybit_recorder.strategy.load_recorder_config",
        return_value=(_make_parsed_cfg([]), []),
    )
    strategy._on_config_reload(event=None)

    parent.unsubscribe_mark_prices.assert_called_once_with(INSTRUMENT_ID_LINEAR)
    parent.unsubscribe_index_prices.assert_called_once_with(INSTRUMENT_ID_LINEAR)
    parent.unsubscribe_funding_rates.assert_called_once_with(INSTRUMENT_ID_LINEAR)
