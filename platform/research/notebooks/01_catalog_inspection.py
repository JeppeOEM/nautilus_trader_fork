# %% [raw]
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

# %% [markdown]
# # 01 Catalog inspection
#
# What the archive actually holds, per venue and instrument, over one window -- before any
# research result built on it is trusted. Every number below comes from
# `research.application.inspection` over bounded reads (`CatalogFrames`, every call with
# `start=`/`end=`); this notebook holds no analysis logic of its own. Defects are shown, never
# hidden: a gap stays a gap in every plot, a crossed second is counted and blanked, and a fold
# mismatch or precision disagreement is printed.
#
# Each section re-reads its window one instrument at a time, so memory holds one instrument's
# window at once (MEM-01) at the cost of reading it more than once. Widen `START`/`END` with care:
# a day of one busy instrument is already large.

# %% [markdown]
# ## 1. Parameters
#
# Read from the environment (`research/notebooks/_params.py`, the only place defaults live):
# `CATALOG_PATH`, `CANDLES_DIR`, `METRICS_DB_PATH`, `ERRORS_DIR`, `INSTRUMENTS` (comma-separated),
# `START`, `END` (ISO, naive = UTC). Unset, they point at `platform/data/` and yesterday's UTC day.

# %%
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from _params import Params
from plotly.subplots import make_subplots
from research.application import inspection
from research.application.frames import CatalogFrames
from research.application.ports import window_ns

from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.data import TradeTick


params = Params.from_env()
start_ns, end_ns = window_ns(params.start, params.end)
frames = CatalogFrames(params.catalog_path, params.candles_dir)
print(params)

# %% [markdown]
# ## 2. Instrument inventory
#
# Every instrument definition in the catalog (`ParquetDataCatalog.instruments()`, metadata), as
# the class's own `to_dict` (`CryptoPerpetual.to_dict(i)` -- a staticmethod, not `i.to_dict()`),
# with venue and market kind from `kernel.venues`. An instrument in `INSTRUMENTS` with no
# definition is listed under the table.

# %%
inventory = inspection.instrument_inventory(params.catalog_path)
definitions = inspection.instrument_definitions(params.catalog_path)
print(inventory.to_string())
print("INSTRUMENTS without a definition:", sorted(set(params.instruments) - set(definitions)))

# %% [markdown]
# ## 3. Coverage and gaps
#
# The timeline is the snapshot files written (file names only, `kernel.catalog_files`); a stretch
# no bar covers had no snapshot. Gaps are `archive.application.diagnostics`' heuristics over the
# window's own timestamps. Each snapshot gap is classified whole by the mark stream over it (a
# second feed, independent of the book's gate): a **likely outage** when the marks were silent for
# at least half of it too, a **book gap** when marks kept flowing (the gate skipped a stale or
# crossed book), **no mark coverage** when there is no mark stream around it to tell (Bybit spot
# has none). A **quiet market** is a trade gap the book was sampled across; a trade gap
# overlapping a snapshot gap, or reaching past the window's first or last snapshot, is not
# reported (the snapshot gap row and the timeline show that stretch). Holes under 30 s are not
# flagged (the heuristic's floor).

# %%
timeline = go.Figure()
gaps: dict[str, pd.DataFrame] = {}
for iid in params.instruments:
    spans = inspection.file_coverage(params.catalog_path, iid, start_ns, end_ns)
    timeline.add_trace(
        go.Bar(
            base=spans["start"],
            x=spans["duration_ms"],
            y=spans["instrument_id"],
            orientation="h",
            name=iid,
        )
    )
    seconds = frames.seconds(iid, start=start_ns, end=end_ns)
    trades = frames.trades(iid, start=start_ns, end=end_ns)
    marks = frames.objects(MarkPriceUpdate, iid, start=start_ns, end=end_ns)
    gaps[iid] = report = inspection.gap_report(
        seconds["ts_event"], trades["ts_event"], inspection.ts_events(marks)
    )
    print(f"{iid}: {len(seconds)} snapshot rows, {len(trades)} trades, {len(marks)} marks")
    print(report.to_string() if len(report) else "  no gap over the heuristic's threshold")
timeline.update_xaxes(type="date")
timeline.update_layout(title="Snapshot files written", barmode="overlay")
timeline.show()

# %% [markdown]
# ## 4. Day status
#
# Each instrument-day's candle-store status: **verified** (the day was rebuilt from the raw trade
# archive and its klines reconciled with the venue's), **failed**, **provisional** (live or not
# yet rebuilt -- its seconds fold trades by arrival) or **store absent**.

# %%
days = inspection.day_status(
    params.candles_dir, params.instruments, inspection.utc_days(start_ns, end_ns)
)
print(days.to_string())

# %% [markdown]
# ## 5. Trades versus folded seconds
#
# The raw trade archive re-folded per second with the one fold (`kernel.fold.fold_trades`) and
# compared, exactly, with each snapshot's stored trade columns. A day with `agrees` False has a
# mismatched second, a trade second no snapshot holds (orphan) or two snapshots in one second; on a
# provisional day a live arrival-time fold may legitimately disagree (read it beside section 4).

# %%
agreement: dict[str, pd.DataFrame] = {}
for iid in params.instruments:
    agreement[iid] = table = inspection.fold_agreement(
        frames.objects(TradeTick, iid, start=start_ns, end=end_ns),
        frames.seconds(iid, start=start_ns, end=end_ns),
        window=(start_ns, end_ns),
    )
    print(iid)
    print(table.to_string() if len(table) else "  no snapshot or trade in the window")

# %% [markdown]
# ## 6. Snapshot sanity
#
# Rows the gate should never have written, counted with their timestamps; the `price_precision`
# labels of every price stream's files, read from file metadata (one label per stream, or the
# catalog refuses to read the files together -- a catalog read of such a window would raise);
# the mid price on the 1 s grid, blank on every missing or crossed second; and how long after its
# second each snapshot was written (`ts_init - ts_event`).

# %%
sanity: dict[str, dict[str, Any]] = {}
precision: dict[str, pd.DataFrame] = {}
mids = make_subplots(rows=len(params.instruments), cols=1, subplot_titles=params.instruments)
lags = go.Figure()
for row, iid in enumerate(params.instruments, start=1):
    seconds = frames.seconds(iid, start=start_ns, end=end_ns)
    sanity[iid] = inspection.snapshot_sanity(seconds)
    precision[iid] = labels = inspection.precision_labels(
        params.catalog_path, definitions.get(iid), iid, start_ns, end_ns
    )
    mid = inspection.mid_series(seconds, start_ns, end_ns)
    mids.add_trace(
        go.Scattergl(x=inspection.plot_axis(mid.index), y=mid.to_numpy(), name=iid, mode="lines"),
        row,
        1,
    )
    lags.add_trace(go.Histogram(x=inspection.receive_lag_ms(seconds), name=iid))
    crossed_ts = sanity[iid]["crossed_ts"]
    print(iid, {key: value for key, value in sanity[iid].items() if key != "crossed_ts"})
    print(f"  first crossed seconds (of {len(crossed_ts)}):", crossed_ts[:20])
    print(labels.to_string())
mids.update_layout(
    title="Mid price (gaps and crossed seconds blank)", height=250 * len(params.instruments)
)
mids.show()
lags.update_layout(title="ts_init - ts_event (ms)", barmode="overlay")
lags.show()

# %% [markdown]
# ## 7. Error ledger
#
# The durable error ledger (Story 23.3, `observability.error_ledger`) over the same window: every
# site where our code rejected or dropped something and carried on, per service, with the
# suppressed carry folded in, each service's restarts, and each one-shot job's runs (the nightly's
# `archive.<step>_<venue>`, whose every run writes a `process_start`; Story 31.8). `absent` means
# no ledger directory, `empty` a ledger with nothing in the window -- a quiet window, not a missing
# one -- and `unreadable` a ledger file that could not be read (the counts are then a floor).

# %%
ledger = inspection.ledger_window(params.errors_dir, start_ns, end_ns)
print("ledger:", ledger.state)
print(ledger.counts.to_string())
print(ledger.restarts.to_string())
print(ledger.runs.to_string())

# %% [markdown]
# ## 8. Summary
#
# One row per instrument from the sections above (nothing re-read). Before trusting a result over
# this window, every outage, provisional or failed day, disagreeing fold day, crossed second and
# non-uniform precision here is either explained or excluded from it.

# %%
overview = inspection.summary(params.instruments, gaps, days, agreement, sanity, precision)
print(overview.to_string())
print(
    "ledger:",
    ledger.state,
    "| sites:",
    len(ledger.counts),
    "| restarts:",
    len(ledger.restarts),
    "| job runs:",
    len(ledger.runs),
)
