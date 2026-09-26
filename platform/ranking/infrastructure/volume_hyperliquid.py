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
Hyperliquid's USD 24 h volume source (moved from `ranking_engine/engine.py` in Story 25.2): the
public `info` endpoint's `metaAndAssetCtxs` `dayNtlVlm`, requested through `kernel.venue_http`.
"""

import asyncio

from kernel.venue_http import http_json
from kernel.venue_http import hyperliquid_info_url
from kernel.venue_http import post_json_request
from observability import error_ledger

from ranking.domain.values import parse_usd_volume


def _hyperliquid_universe_and_ctxs(meta_and_ctxs: object) -> tuple[list, list]:
    """Validate the `[meta, ctxs]` pair shape; ValueError on anything else."""
    if not isinstance(meta_and_ctxs, list) or len(meta_and_ctxs) != 2:
        raise ValueError(
            f"metaAndAssetCtxs is not a [meta, ctxs] pair: {type(meta_and_ctxs).__name__}"
        )
    meta, ctxs = meta_and_ctxs
    universe = meta.get("universe") if isinstance(meta, dict) else None
    if not isinstance(universe, list) or not isinstance(ctxs, list) or len(universe) != len(ctxs):
        raise ValueError("metaAndAssetCtxs universe/ctxs are not two equal-length lists")
    return universe, ctxs


def parse_hyperliquid_volume_24h(meta_and_ctxs: object) -> dict[str, float]:
    """
    Parse dayNtlVlm (USD notional) per instrument from a Hyperliquid metaAndAssetCtxs response --
    `meta.universe[i].name` pairs with `ctxs[i]` by index.

    Known limit: a bare metaAndAssetCtxs request covers only the default perp dex, which is all
    hyperliquid_collector collects today; a builder-deployed (HIP-3) perp would have no volume here
    and be counted missing every cycle. Upgrade path: one extra request per dex (`"dex": <name>`)
    keyed by that dex's own instrument ids, when such a perp is collected.

    A malformed payload raises ValueError (the whole poll failed); one unparseable coin is
    ledgered and skipped.
    """
    universe, ctxs = _hyperliquid_universe_and_ctxs(meta_and_ctxs)
    result: dict[str, float] = {}
    for asset, ctx in zip(universe, ctxs, strict=True):
        name = asset.get("name") if isinstance(asset, dict) else None
        if not name:
            continue
        iid = f"{name}-USD-PERP.HYPERLIQUID"
        raw = ctx.get("dayNtlVlm") if isinstance(ctx, dict) else None
        try:
            result[iid] = parse_usd_volume(raw)
        except (ValueError, TypeError) as exc:
            error_ledger.record(
                "ranking_engine.volume24h", f"{iid}: unparseable dayNtlVlm {raw!r}", exc
            )
    return result


class HyperliquidVolumeSource:
    """
    `VolumeSource` for Hyperliquid perpetuals. Invariant: ids are `{name}-USD-PERP.HYPERLIQUID`,
    exactly as the Hyperliquid adapter builds them.
    """

    name = "hyperliquid"

    def __init__(self, environment: str) -> None:
        self._environment = environment

    async def fetch(self) -> dict[str, float]:
        request = post_json_request(
            hyperliquid_info_url(self._environment), {"type": "metaAndAssetCtxs"}
        )
        meta_and_ctxs = await asyncio.to_thread(http_json, request)
        return parse_hyperliquid_volume_24h(meta_and_ctxs)
