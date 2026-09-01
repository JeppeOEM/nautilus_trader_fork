# Review: Version/Reality-Check Verification — ARCHITECTURE-SPINE.md (update pass 2)

**Reviewer lens:** Verify every committed decision was web-researched or reality-checked rather than asserted from training data — current library/framework versions, that each named technology still exists and fits, and that the specific APIs a decision leans on actually exist and behave as claimed.
**Date of this pass:** 2026-07-24 (second update pass; follows the same-day `review-version-verify.md` urwid pass)
**File reviewed:** `_bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md`
**Trigger for this pass:** AD-10, retitled "`live_paper` control-plane isolation (status, control, and durable trade/PnL history)" — a new claim that `nautilus_trader` 1.229.0's `Cache`/`CacheConfig` provide a ready-made durable Redis-backed trade/PnL history query surface.
**Method:** Direct reality-check against this project's actual dependency — this is not a third-party wheel, `pyproject.toml` pins `version = "1.229.0"` and the collector/live-paper Docker build (`.docker/nautilus_trader.dockerfile`) builds *this exact checked-out source tree* into the wheel that ships. Reading `nautilus_trader/` in this repo is ground truth for the pin, not a proxy for it — stronger than a PyPI/web check would be here.

---

## Summary verdict

The three headline API claims in AD-10 (`CacheConfig(database=DatabaseConfig(type="redis"))`, `TradingNodeConfig` accepting a `cache` field, and `cache.orders_closed()`/`positions_closed()`/`position_snapshots()` existing as named) are all **literally true** in this codebase — this part of the claim was not fabricated from training-data plausibility, it holds up against the actual pinned source. However, one load-bearing sub-claim is **incomplete in a way that would silently fail if implemented as written**: `position_snapshots()` requires a separate, unmentioned `ExecEngineConfig(snapshot_positions=True)` opt-in to return anything beyond NETTING-mode reopen/flip snapshots, and nothing in AD-10 (or in `troll/live_paper/` today) references this. That gap should be closed before this AD is implemented, not treated as settled.

---

## Findings

### 1. [HIGH] `position_snapshots()` is claimed as ready-to-use query surface, but is empty by default — the required `ExecEngineConfig(snapshot_positions=True)` companion setting is never mentioned

**Spine text (AD-10, Rule, durable history bullet):**
> `cache.orders_closed()`/`cache.positions_closed()`/`cache.position_snapshots()` are the query surface `live_paper` reads to build it.

**Reality-checked against `nautilus_trader/execution/engine.pyx` and `nautilus_trader/execution/config.py` (this repo, 1.229.0 pin):**
- `ExecEngineConfig.snapshot_positions: bool = False` and `snapshot_orders: bool = False` (`execution/config.py:94-95`) — both default off.
- `Cache.snapshot_position()` (the method that actually populates `self._position_snapshots`, which `position_snapshots()` reads from) is only called from `ExecutionEngine` in three places: `engine.pyx:1763` and `:1815` (both gated behind `if self.snapshot_positions:`), and `:1779`/`:1871` (NETTING-mode position reopen/flip only, unconditional on the flag, but this only fires for positions that get reopened or flipped under the same `PositionId` — not the general case of a position that opens and closes once, which is the common shape for a Dummy-Strategy scalping bot).
- Net effect: with `live_paper` calling `TradingNodeConfig(cache=CacheConfig(database=DatabaseConfig(type="redis")))` exactly as AD-10 describes, but *not* also passing `exec_engine=ExecEngineConfig(snapshot_positions=True, snapshot_orders=True)`, `cache.position_snapshots()` will return an **empty list** for ordinary HEDGING-mode or non-reopened NETTING-mode trades — i.e., for the Dummy Strategy's actual trading pattern today.
- Confirmed via grep that `troll/live_paper/config.py`, `troll/live_paper/config.toml`, and `troll/live_paper/node.py` currently contain **no** reference to `ExecEngineConfig`, `snapshot_positions`, or `snapshot_orders` at all — so nothing already in the codebase compensates for this gap.

**Why this matters:** `cache.orders_closed()`/`cache.positions_closed()` alone are actually sufficient for building the `bots:history:*` payload described in AD-10 (a closed `Position` retains its full `_events: list[OrderFilled]` — walkable via `position.events` for per-fill `{ts, side, price, qty}` rows, and `position.realized_pnl` is populated on close regardless of the snapshot flag) — so the underlying mechanism AD-10 wants (durable per-trade/PnL history from Nautilus's own `Cache`) is achievable. But the AD's own text names `position_snapshots()` as part of "the query surface," which is misleading as written: implementing exactly what AD-10 says, without independently discovering the `ExecEngineConfig` flag, produces silently-empty history for the closed-position-snapshot path. This is exactly the class of claim this review lens exists to catch: plausible-sounding, partially verified, but not fully checked against the actual behavior.

**Recommendation:** Either (a) drop `position_snapshots()` from the Rule text and rely purely on `orders_closed()`/`positions_closed()` + walking `position.events`/`order.events` for the trade list (simpler, no extra config surface), or (b) keep `position_snapshots()` but add `exec_engine=ExecEngineConfig(snapshot_positions=True, snapshot_orders=True)` to the Rule text and to `troll/live_paper/node.py`'s `TradingNodeConfig` construction, and note that NETTING-mode-only auto-snapshotting (lines `engine.pyx:1779`/`1871`) is not sufficient on its own.

### 2. [LOW / process note] AD-10 lacks the `[ADOPTED] — confirmed (re-verified ...)` citation discipline every other AD in this document uses

AD-1 through AD-9 each close with a specific `[ADOPTED] — confirmed (re-verified 2026-07-24, ...)` line citing exact file:line evidence for their claims (e.g. AD-1's `collector.py:643`, `collector.py:735`). AD-10 — the newest, most technically load-bearing addition — has no such citation trail; it reads as an asserted design decision rather than a reality-checked one. Finding 1 above is precisely the kind of gap that citation discipline is meant to surface before merge. Recommend bringing AD-10 up to the same standard: cite the exact `engine.pyx`/`config.py`/`kernel.py` lines that make the Redis-backed `Cache` mechanism work, and explicitly note the `snapshot_positions` dependency once resolved.

### 3. [Verified — no issue] `CacheConfig.database: DatabaseConfig | None` and `DatabaseConfig.type: str = "redis"` exist exactly as claimed

Confirmed directly in `nautilus_trader/cache/config.py:63` (`database: DatabaseConfig | None = None`) and `nautilus_trader/common/config.py:309-347` (`class DatabaseConfig`, `type: str = "redis"`, docstring: `type : str, {'redis'}, default 'redis'`). Additionally verified the mechanism is actually wired up and not just a docstring-only field: `nautilus_trader/system/kernel.py:310-329` shows `NautilusKernel.__init__` builds a `CacheDatabaseAdapter` when `config.cache.database.type == "redis"`, and **raises `ValueError` for any other type** — Redis is in fact the *only* supported cache-database backend in this pin, which is stronger than "fits" — it's the sole option. No issue with this sub-claim.

### 4. [Verified — no issue] `TradingNodeConfig` accepts a `cache: CacheConfig` field

Confirmed: `TradingNodeConfig` (`nautilus_trader/live/config.py:284`) inherits from `NautilusKernelConfig` (`nautilus_trader/system/config.py:39`), which declares `cache: CacheConfig | None = None` at line 109. `TradingNodeConfig`'s own docstring (line 292-293) redundantly documents this inherited field. The claim holds.

### 5. [Verified — no issue] `cache.orders_closed()`, `cache.positions_closed()`, `cache.position_snapshots()` all exist under these exact names

Confirmed in `nautilus_trader/cache/cache.pyx`: `orders_closed()` at line 4745, `positions_closed()` at line 5620, `position_snapshots()` at line 5470 — all `cpdef` public methods on the `Cache` class with query-filter signatures matching what a per-bot/per-instrument history reader would need (`venue`, `instrument_id`, `strategy_id`, `account_id` filters). No fabrication here; the method names and general shape are real. (Caveat: see Finding 1 for `position_snapshots()`'s behavior, which is real but incompletely described.)

### 6. [Verified — matches spine's own claim] `troll/live_paper/node.py` currently builds `TradingNodeConfig` with no `cache=` override

Confirmed by reading `troll/live_paper/node.py:98-103` — `TradingNodeConfig(trader_id=..., logging=..., data_clients=..., exec_clients=...)` — no `cache` argument passed, so it defaults to `cache=None` (in-memory only, no Redis persistence). Matches AD-10's implicit premise that this is a currently-open gap to close, not an already-shipped mechanism.

---

## Spot-check of pre-existing Stack table entries

Per the task's instruction to only re-verify pre-existing entries if something looks newly suspicious: nothing in this pass's investigation (which was scoped to AD-10's `Cache`/`CacheConfig`/`TradingNodeConfig` claims) surfaced new doubt about the `urwid`/`plotly`/`pandas`/`nautilus_trader` 1.229.0 pin entries already covered by the same-day `review-version-verify.md` pass. Not re-verified here; deferring to that pass's findings (which already flagged and got the `urwid` pin corrected from 4.0.2 → 4.0.6, and confirmed the 1.229.0/plotly/pandas/redis entries).

One incidental re-confirmation from this pass: `pyproject.toml:3` pins `version = "1.229.0"`, and `nautilus_trader/__init__.py:30` sources `__version__` from `nautilus_pyo3.NAUTILUS_VERSION` — consistent with the Stack table's pin, and consistent with the Docker build (`.docker/nautilus_trader.dockerfile:47-74`) building this exact source tree into the installed wheel rather than pulling a PyPI release — i.e., checking this repo's `nautilus_trader/` source directly *is* checking the pin, not merely a proxy for it.

---

## Files/lines checked (audit trail)

- `nautilus_trader/cache/config.py:23-75` (`CacheConfig`)
- `nautilus_trader/common/config.py:309-349` (`DatabaseConfig`)
- `nautilus_trader/cache/cache.pyx:4745-4778` (`orders_closed`), `:5620-5650` (`positions_closed`), `:5470-5500` (`position_snapshots`), `:1762-1871` (snapshot call sites, NETTING reopen/flip logic)
- `nautilus_trader/execution/config.py:44-105` (`ExecEngineConfig` — `snapshot_positions`/`snapshot_orders`/purge defaults)
- `nautilus_trader/execution/engine.pyx:170-186` (config wiring), `:1750-1871` (position open/update/reopen/flip snapshot gating)
- `nautilus_trader/system/config.py:39-119` (`NautilusKernelConfig.cache`)
- `nautilus_trader/live/config.py:284-330` (`TradingNodeConfig`)
- `nautilus_trader/system/kernel.py:307-360` (Redis-only cache-database wiring, `ValueError` on non-redis type)
- `nautilus_trader/model/position.pyx:68,140,231-263,334-346` (`Position._events`/`.events`/`.realized_pnl` — confirms `positions_closed()` alone carries fill-level history)
- `troll/live_paper/node.py:1-141` (full file — confirms no `cache=`/`ExecEngineConfig` override)
- `troll/live_paper/config.py`, `troll/live_paper/config.toml` (grepped — no `cache`/`snapshot` references)
- `pyproject.toml:3`, `nautilus_trader/__init__.py:30`, `.docker/nautilus_trader.dockerfile:31-81` (version pin provenance)
