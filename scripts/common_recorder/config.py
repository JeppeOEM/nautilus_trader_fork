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
Exchange-agnostic recorder config helpers shared by every venue recorder.

Holds the path-resolution, streaming-config, and positive-threshold-validation
helpers that are identical across venues. These helpers are duck-typed against a
venue config object (they only read ``streaming_path`` and
``rotation_interval_minutes``), so they work for any venue's recorder config
alike WITHOUT importing it (DYDX-01).
"""

from datetime import time
from pathlib import Path

import pandas as pd

from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import IndexPriceUpdate
from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.data import TradeTick
from nautilus_trader.persistence.config import StreamingConfig
from nautilus_trader.persistence.writer import RotationMode


# Anchor for resolving relative `catalog_path` / `streaming_path` values so the
# catalog always lands in the same place regardless of the process's cwd
# (parents[2] from scripts/common_recorder/config.py is the repo root — same
# scripts/<pkg>/config.py depth as every venue recorder).
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _resolve_catalog_path(raw_path: str) -> str:
    """
    Resolve a configured catalog/streaming path against the repo root.

    A relative path in `recorder.toml` (e.g. ``"catalog"``) must always resolve
    to the same on-disk location regardless of the cwd the recorder is launched
    from (e.g. systemd `WorkingDirectory`, repo root, or `scripts/<venue>_recorder/`).
    Absolute paths are returned unchanged.
    """
    path = Path(raw_path)
    if path.is_absolute():
        return str(path)

    return str(_REPO_ROOT / path)


def _validate_positive_thresholds(
    heartbeat_interval_seconds,
    stale_threshold_default_seconds,
    stale_threshold_seconds,
    restart_gap_threshold_seconds,
    max_hot_added_instruments,
) -> None:
    """
    Fail fast on any non-positive interval/threshold value (V5 input validation).

    An absurd interval/threshold would either spam logs (too small) or never warn
    (too large/negative), so reject any non-positive value at load time, mirroring
    the depth validation in each venue's `load_recorder_config`. The message
    wording here is asserted on by the recorder test suite (substrings like
    "must be positive" plus the offending value), so it must not change.

    Parameters
    ----------
    heartbeat_interval_seconds : int
        The heartbeat timer interval in seconds.
    stale_threshold_default_seconds : int
        The fallback stale threshold in seconds.
    stale_threshold_seconds : dict[str, int]
        The per-stream-label stale thresholds in seconds.
    restart_gap_threshold_seconds : int
        The restart-gap WARNING threshold in seconds.
    max_hot_added_instruments : int
        The lifetime hot-add WARNING threshold.

    Raises
    ------
    ValueError
        If any value is non-positive.

    """
    # V5 fail-fast (T-3-05 DoS-of-logs mitigation): an absurd interval/threshold
    # would either spam logs (too small) or never warn (too large/negative), so
    # reject any non-positive value at load time, mirroring the depth validation.
    if heartbeat_interval_seconds <= 0:
        raise ValueError(
            f"Invalid heartbeat_interval_seconds {heartbeat_interval_seconds}: must be positive",
        )

    if stale_threshold_default_seconds <= 0:
        raise ValueError(
            f"Invalid stale_threshold_default_seconds {stale_threshold_default_seconds}: "
            "must be positive",
        )

    for stream, threshold in stale_threshold_seconds.items():
        if threshold <= 0:
            raise ValueError(
                f"Invalid stale_threshold_seconds[{stream!r}] {threshold}: must be positive",
            )

    if restart_gap_threshold_seconds <= 0:
        raise ValueError(
            f"Invalid restart_gap_threshold_seconds {restart_gap_threshold_seconds}: "
            "must be positive",
        )

    if max_hot_added_instruments <= 0:
        raise ValueError(
            f"Invalid max_hot_added_instruments {max_hot_added_instruments}: must be positive",
        )


def build_streaming_config(recorder_cfg) -> StreamingConfig:
    """
    Build the `StreamingConfig` used by the recorder's `TradingNode`.

    Reads only ``recorder_cfg.streaming_path`` and
    ``recorder_cfg.rotation_interval_minutes`` (duck-typed), so it works for any
    venue's recorder config without importing it.

    Parameters
    ----------
    recorder_cfg : object
        The parsed recorder configuration (any object exposing ``streaming_path``
        and ``rotation_interval_minutes``).

    Returns
    -------
    StreamingConfig
        A daily-rotating streaming configuration with `include_types` covering
        all six auto-written types recorded by Phase 2 (REC-02 to REC-04, REC-06).

    """
    return StreamingConfig(
        # `catalog_path` is set to the recorder's single shared streaming/catalog
        # root (Pitfall 5 / A4) so the conversion `ParquetDataCatalog` later finds
        # the feather files written under `{root}/live/{instance_id}/`.
        catalog_path=recorder_cfg.streaming_path,
        fs_protocol="file",
        # WHY: day-partitioning is a consequence of daily feather rotation, not a
        # free catalog property (Pitfall 1). `rotation_interval_minutes` defaults
        # to 1440 (1 day) but is configurable for testing the conversion path
        # without waiting a full day.
        rotation_mode=RotationMode.SCHEDULED_DATES,
        rotation_interval=pd.Timedelta(minutes=recorder_cfg.rotation_interval_minutes),
        rotation_time=time(0, 0, 0),
        rotation_timezone="UTC",
        # FundingRateUpdate is intentionally OMITTED here: it is deduped on
        # value-change by the strategy and persisted via a separate writer
        # (D-01 / Plan 02), not auto-written through this passthrough.
        # NOTE: the live DataEngine always publishes the plural `OrderBookDeltas`
        # container on the message bus (even for a single delta with
        # buffer_deltas=False) -- StreamingFeatherWriter.write() filters on
        # `obj.__class__` BEFORE any OrderBookDeltas->OrderBookDelta schema
        # mapping, so the singular `OrderBookDelta` here would silently drop
        # all order-book data.
        include_types=[
            TradeTick,
            QuoteTick,
            OrderBookDeltas,
            Bar,
            MarkPriceUpdate,
            IndexPriceUpdate,
        ],
    )
