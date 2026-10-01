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
"""
Fill models buildable from an `ImportableFillModelConfig`.

`FillModelFactory.create` calls `fill_model_cls(config=...)`, but `MarketHoursFillModel`,
`VolumeSensitiveFillModel` and `CompetitionAwareFillModel` take no `config` argument in this
Nautilus version (their `__init__` only has the three keyword fields), so the factory raises
`TypeError` for them. Each class here subclasses its upstream model and accepts the same
`FillModelConfig`, so `RunSpec(fill_model={"name": "market_hours", ...})` builds.

Invariant: each adapter behaves exactly as its upstream parent constructed with
`prob_fill_on_limit`, `prob_slippage` and `random_seed` taken from the config; none adds behaviour.
Known limit: `CompetitionAwareFillModel.liquidity_factor` is not in `FillModelConfig`, so it stays
at the upstream default (0.3); upgrade path: a config class with that field, named in
`ports.FILL_MODELS`.
"""

from nautilus_trader.backtest.models import CompetitionAwareFillModel as _CompetitionAware
from nautilus_trader.backtest.models import MarketHoursFillModel as _MarketHours
from nautilus_trader.backtest.models import VolumeSensitiveFillModel as _VolumeSensitive


class MarketHoursFillModel(_MarketHours):
    """Upstream `MarketHoursFillModel`, constructible from a `FillModelConfig`."""

    def __init__(self, config: object) -> None:
        super().__init__(config.prob_fill_on_limit, config.prob_slippage, config.random_seed)  # type: ignore[attr-defined]


class VolumeSensitiveFillModel(_VolumeSensitive):
    """Upstream `VolumeSensitiveFillModel`, constructible from a `FillModelConfig`."""

    def __init__(self, config: object) -> None:
        super().__init__(config.prob_fill_on_limit, config.prob_slippage, config.random_seed)  # type: ignore[attr-defined]


class CompetitionAwareFillModel(_CompetitionAware):
    """Upstream `CompetitionAwareFillModel`, constructible from a `FillModelConfig`."""

    def __init__(self, config: object) -> None:
        super().__init__(config.prob_fill_on_limit, config.prob_slippage, config.random_seed)  # type: ignore[attr-defined]
