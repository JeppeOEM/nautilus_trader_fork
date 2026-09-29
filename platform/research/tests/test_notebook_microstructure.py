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
`research.application.microstructure` over the session fixture archive (Story 27.3): the frames
behind `02_microstructure` keep the planted outage and crossed second as NaN gaps, the OFI replay
is the strategy's own, and the notebook's own namespace shows them the same way.
"""

import math

import numpy as np
import pandas as pd
import pytest
from kernel.clocks import NS_PER_S
from kernel.indicators import OFI_GAP_NS
from kernel.indicators import MultiLevelOFI
from kernel.indicators import microprice
from kernel.indicators import mid_price
from kernel.second_snapshot import DydxSecondSnapshot

from nautilus_trader.model.identifiers import InstrumentId
from research.application import inspection
from research.application import microstructure
from research.application.frames import OBI_LEVELS
from research.application.frames import CatalogFrames
from research.application.ports import window_ns
from research.strategies.ofi_strategy import OFIStrategyConfig
from research.tests.fixture_catalog import CROSSED_SECOND
from research.tests.fixture_catalog import DATA_START_NS
from research.tests.fixture_catalog import HOLE_SECONDS
from research.tests.fixture_catalog import SECONDS
from research.tests.fixture_catalog import FixturePaths
from research.tests.test_notebooks import NOTEBOOKS_DIR
from research.tests.test_notebooks import _run


BOOK_SERIES = ("mid", "spread", "microprice", *(f"obi_{n}" for n in OBI_LEVELS))


def _config(iid: str) -> OFIStrategyConfig:
    return OFIStrategyConfig(instrument_id=InstrumentId.from_str(iid))


def _read(fixture: FixturePaths, iid: str) -> microstructure.InstrumentMicrostructure:
    start, end = window_ns(fixture.start, fixture.end)
    frames = CatalogFrames(fixture.catalog_path, fixture.candles_dir)
    definitions = inspection.instrument_definitions(fixture.catalog_path)
    tick = microstructure.tick_size(definitions, iid)
    return microstructure.read_instrument(frames, iid, start, end, _config(iid), tick)


def _at(second: int) -> pd.Timestamp:
    return pd.Timestamp(DATA_START_NS + second * NS_PER_S, unit="ns", tz="UTC")


def _outage() -> list[pd.Timestamp]:
    return [_at(s) for s in HOLE_SECONDS]


def _values(frame: pd.DataFrame, stamps: list[pd.Timestamp], columns: list[str]) -> np.ndarray:
    """Return the frame's `columns` at `stamps` (grid seconds), as floats."""
    return frame[columns].reindex(pd.DatetimeIndex(stamps)).to_numpy(dtype="float64")


def _value(frame: pd.DataFrame, stamp: pd.Timestamp, column: str) -> float:
    return float(_values(frame, [stamp], [column])[0, 0])


def test_the_outage_is_nan_in_every_book_and_flow_series(fixture_archive: FixturePaths) -> None:
    data = _read(fixture_archive, fixture_archive.defects.outage_instrument)
    flow = microstructure.trade_flow(data.grid)
    assert np.isnan(_values(data.grid, _outage(), list(BOOK_SERIES))).all()
    assert np.isnan(_values(data.ofi, _outage(), list(data.ofi.columns))).all()
    assert np.isnan(_values(flow, _outage(), list(flow.columns))).all()


def test_the_outage_breaks_the_returns(fixture_archive: FixturePaths) -> None:
    data = _read(fixture_archive, fixture_archive.defects.outage_instrument)
    returns = microstructure.second_returns(data.grid)
    by_ts = pd.DataFrame(
        {"r": np.asarray(returns.values)}, index=pd.to_datetime(returns.ts_ns, unit="ns", utc=True)
    )
    after = _at(HOLE_SECONDS.stop)  # the first sampled second after the hole
    assert np.isnan(_values(by_ts, [*_outage(), after], ["r"])).all()
    assert math.isfinite(_value(by_ts, _at(HOLE_SECONDS.start - 1), "r"))


def test_the_outage_is_nan_in_the_basis(fixture_archive: FixturePaths) -> None:
    data = _read(fixture_archive, fixture_archive.defects.outage_instrument)
    assert np.isnan(_values(data.basis, _outage(), ["basis"])).all()


def test_the_basis_is_last_mark_minus_last_index(fixture_archive: FixturePaths) -> None:
    data = _read(fixture_archive, fixture_archive.defects.outage_instrument)
    assert data.tick is not None
    assert _value(data.basis, _at(10), "basis") == pytest.approx(
        -data.tick
    )  # index = mark + 1 tick


def test_the_crossed_second_is_nan_in_every_book_series(fixture_archive: FixturePaths) -> None:
    data = _read(fixture_archive, fixture_archive.defects.crossed_instrument)
    crossed = _at(CROSSED_SECOND)
    assert np.isnan(_values(data.grid, [crossed], list(BOOK_SERIES))).all()
    assert np.isnan(_values(data.ofi, [crossed], list(data.ofi.columns))).all()
    assert np.isnan(_values(data.obi_z, [crossed], list(data.obi_z.columns))).all()


def test_the_crossed_second_keeps_its_trades(fixture_archive: FixturePaths) -> None:
    data = _read(fixture_archive, fixture_archive.defects.crossed_instrument)
    trades = _values(data.grid, [_at(CROSSED_SECOND)], ["buy_volume", "sell_volume"])
    assert np.isfinite(trades).all()


def test_nothing_but_the_crossed_second_is_blank_inside_the_data(
    fixture_archive: FixturePaths,
) -> None:
    data = _read(fixture_archive, fixture_archive.defects.crossed_instrument)
    inside = _values(data.grid, [_at(s) for s in range(SECONDS)], ["mid"])
    assert int(np.isnan(inside).sum()) == 1  # nothing else inside the data is blanked


def _strategy_replay(fixture: FixturePaths, iid: str) -> list[float]:
    """Replay in the strategy's `on_data` order, re-typed from `OFIStrategy` (not the service)."""
    config = _config(iid)
    ofi = MultiLevelOFI(
        levels=config.ofi_levels,
        window=config.ofi_window,
        usd_notional=True,
        zscore_window=config.ofi_zscore_window,
    )
    start, end = window_ns(fixture.start, fixture.end)
    frames = CatalogFrames(fixture.catalog_path, fixture.candles_dir)
    values, last_ts = [], None
    for snapshot in frames.objects(DydxSecondSnapshot, iid, start=start, end=end):
        if not snapshot.bid_prices or not snapshot.ask_prices:
            continue
        reset = last_ts is not None and snapshot.ts_event - last_ts > OFI_GAP_NS
        if reset:
            ofi.clear_prev_state()
        first = last_ts is None
        last_ts = snapshot.ts_event
        ofi.update_raw(
            snapshot.bid_prices, snapshot.bid_sizes, snapshot.ask_prices, snapshot.ask_sizes
        )
        values.append(math.nan if first or reset else ofi.value)
    return values


@pytest.mark.parametrize("which", ["outage_instrument", "crossed_instrument"])
def test_the_ofi_replay_is_the_strategys(fixture_archive: FixturePaths, which: str) -> None:
    iid = getattr(fixture_archive.defects, which)
    start, end = window_ns(fixture_archive.start, fixture_archive.end)
    seconds = CatalogFrames(fixture_archive.catalog_path, fixture_archive.candles_dir).seconds(
        iid, start=start, end=end
    )
    replay = microstructure.ofi_readings(seconds, _config(iid))
    assert np.array_equal(
        replay["ofi_z"].to_numpy(), _strategy_replay(fixture_archive, iid), equal_nan=True
    )


def test_the_outage_rebaselines_the_ofi(fixture_archive: FixturePaths) -> None:
    data = _read(fixture_archive, fixture_archive.defects.outage_instrument)
    assert math.isnan(_value(data.ofi, _at(HOLE_SECONDS.stop), "ofi"))  # a baseline, not a delta
    assert math.isfinite(_value(data.ofi, _at(HOLE_SECONDS.stop + 1), "ofi"))


def test_the_tick_size_is_the_definitions_price_increment(fixture_archive: FixturePaths) -> None:
    definitions = inspection.instrument_definitions(fixture_archive.catalog_path)
    assert microstructure.tick_size(definitions, "BTC-USD-PERP.DYDX") == 0.1
    assert microstructure.tick_size(definitions, "NOPE-USD-PERP.DYDX") is None


def test_the_spread_is_two_ticks_on_the_fixture(fixture_archive: FixturePaths) -> None:
    data = _read(fixture_archive, "BTC-USD-PERP.DYDX")
    ticks = microstructure.spread_frame(data.grid, data.tick)["spread_ticks"].dropna()
    assert ticks.round(9).unique().tolist() == [2.0]  # best bid = mid - 1 tick, ask = mid + 1


def test_the_depth_sample_takes_one_snapshot_a_minute(fixture_archive: FixturePaths) -> None:
    data = _read(fixture_archive, "ETH-USD-PERP.DYDX")
    assert data.depth.totals.notna().all(axis=1).sum() == 10  # the fixture's ten minutes
    assert data.depth.by_level["bid_snapshots"].tolist() == [10] * 20
    assert data.depth.by_level["ask_snapshots"].tolist() == [10] * 20


def test_a_stream_not_collected_prints_a_line_not_an_error() -> None:
    empty = pd.DataFrame(columns=["ts_event", "rate"])
    line = microstructure.coverage_line("BTCUSDT.BYBIT", "funding", empty)
    assert line == "BTCUSDT.BYBIT: funding not collected in this window"


def test_the_basis_of_a_mark_less_instrument_is_all_nan() -> None:
    empty = pd.DataFrame({"ts_event": pd.Series(dtype="int64"), "mark": [], "index": []})
    basis = microstructure.basis_frame(empty, 0, 10 * NS_PER_S)
    assert (len(basis), int(basis["basis"].notna().sum())) == (10, 0)


def test_the_notebook_shows_the_gaps_it_claims(
    fixture_archive: FixturePaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What `02_microstructure` itself computed (its namespace), not a re-run of the service."""
    shown = _run(NOTEBOOKS_DIR / "02_microstructure.py", fixture_archive, monkeypatch)
    defects = fixture_archive.defects
    outage = shown["data"][defects.outage_instrument]
    crossed = shown["data"][defects.crossed_instrument]
    assert np.isnan(_values(outage.grid, _outage(), ["mid"])).all()
    assert math.isnan(_value(crossed.grid, _at(CROSSED_SECOND), "mid"))
    assert list(shown["overview"]["instrument_id"]) == list(fixture_archive.instruments)


def test_the_cvd_restarts_after_the_outage(fixture_archive: FixturePaths) -> None:
    data = _read(fixture_archive, fixture_archive.defects.outage_instrument)
    after = _at(HOLE_SECONDS.stop)
    flow = microstructure.trade_flow(data.grid)
    assert _value(flow, after, "cvd") == _value(data.grid, after, "volume_delta")


def test_a_microprice_at_the_mid_but_rounded_off_it_is_a_flat_predictor() -> None:
    book = {
        "bid_prices": [13437.29],
        "ask_prices": [13437.3],
        "bid_sizes": [3.0],
        "ask_sizes": [3.0],
    }
    frame = pd.DataFrame({"mid": [mid_price(book), 13437.3], "microprice": [microprice(book), 1.0]})
    assert microprice(book) != mid_price(book)  # the kernel values differ by float rounding
    assert microstructure.microprice_edge(frame)["predictor"][0] == 0.0


def test_the_first_obi_reading_has_no_zscore() -> None:
    frame = pd.DataFrame({f"obi_{n}": [math.nan, 0.4, 0.6, 0.5] for n in OBI_LEVELS})
    z = microstructure.obi_zscores(frame, window=3)
    assert np.isnan(z["obi_1_z"].to_numpy()[:2]).tolist() == [True, True]


def test_the_strategy_warmup_ends_warmup_seconds_after_the_first_row(
    fixture_archive: FixturePaths,
) -> None:
    data = _read(fixture_archive, fixture_archive.defects.outage_instrument)
    first_second = int(data.grid["mid"].dropna().index[0].value)
    expected = first_second + _config(data.instrument_id).warmup_seconds * NS_PER_S
    assert data.warmup_end_ns is not None
    assert expected <= data.warmup_end_ns < expected + NS_PER_S  # the row is inside its second


def _acf(rho: float, pairs: int) -> pd.DataFrame:
    return pd.DataFrame(
        {"horizon_s": [1], "lag": [1], "lag_s": [1], "rho": [rho], "pairs": [pairs]}
    )


@pytest.mark.parametrize(
    ("rho", "pairs", "sign"),
    [
        (0.5, 23, "+"),  # outside +-2/sqrt(23) ~ 0.417
        (-0.5, 23, "-"),
        (0.3, 23, "~0"),  # inside the white-noise band: no evidence of either sign
        (0.9, 9, "n/a"),  # under the minimum pair count
        (math.nan, 0, "n/a"),
    ],
)
def test_the_acf_sign_is_read_only_outside_the_white_noise_band(
    fixture_archive: FixturePaths, rho: float, pairs: int, sign: str
) -> None:
    data = _read(fixture_archive, "BTC-USD-PERP.DYDX")
    assert microstructure.observations(data, _acf(rho, pairs))["acf_sign_1s"] == sign


def test_depth_asymmetry_without_ask_depth_is_nan(fixture_archive: FixturePaths) -> None:
    data = _read(fixture_archive, "BTC-USD-PERP.DYDX")
    no_ask = data.depth.totals.assign(total_ask=0.0)
    depth = microstructure.DepthSummary(data.depth.by_level, data.depth.by_bps, no_ask)
    row = microstructure.observations(
        microstructure.InstrumentMicrostructure(**{**vars(data), "depth": depth}), _acf(0.0, 0)
    )
    assert math.isnan(row["depth_bid_over_ask"])


@pytest.mark.parametrize(
    ("warmup_end", "span"),
    [(None, (10, 50)), (30, (10, 30)), (80, (10, 50)), (5, (10, 10))],
)
def test_the_warmup_span_is_clamped_to_the_window(
    warmup_end: int | None, span: tuple[int, int]
) -> None:
    assert microstructure.warmup_span(warmup_end, 10, 50) == span
