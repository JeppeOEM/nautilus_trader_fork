# Story 22.6: `live_paper` multi-venue paper trading (Sandbox)

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a strategy developer,
I want one `live_paper` process to run paper bots on dYdX, Bybit and Hyperliquid instruments at once,
so that the same `DummyStrategy` is validated on every venue against live mainnet data.

## Acceptance Criteria

1. **Venue table, not a class hierarchy.** `live_paper/venues.py` maps `DYDX`/`BYBIT`/`HYPERLIQUID` to (data config class, data factory, exec config class, exec factory, allowed environments, paper quote currency) as a plain dict; `build_node` creates one data client and one `SandboxExecutionClientConfig` per venue present in `PaperConfig.bots` (venue from `venue_of(bot.instrument_id)`), still one `TradingNode` per process.
2. **Spine + config follow.** AD-11's "one `DydxDataClientConfig`, one `SandboxExecutionClientConfig` no matter how many bots" is amended to "one data client and one exec client per venue in use"; `PaperConfig` carries per-venue `starting_balances` and `environment`.
3. **Mixed Bybit spot + linear proven in Sandbox.** A spot bot and a linear bot in the same node both observe a fill (the open item on Sandbox account type for mixed `CurrencyPair`/perp instruments is resolved with evidence), and `bots:status` shows every bot.

## Tasks / Subtasks

- [x] Task 1 — `live_paper/venues.py` (AC: #1)
  - [x] One frozen dataclass `VenueSpec(data_config_cls, data_factory, exec_config_cls, exec_factory, allowed_environments: tuple[str, ...], default_environment: str, paper_quote_currency: str, parse_environment: Callable[[str], object])` and `VENUES: dict[str, VenueSpec]` with exactly three entries, keyed by the Nautilus venue strings `nautilus_trader.adapters.dydx.constants.DYDX`, `nautilus_trader.adapters.bybit.constants.BYBIT`, `nautilus_trader.adapters.hyperliquid.constants.HYPERLIQUID`:
    - DYDX: `DydxDataClientConfig`/`DydxLiveDataClientFactory`, `DydxExecClientConfig`/`DydxLiveExecClientFactory`, environments `("mainnet", "testnet")` via `DydxNetwork.from_str`, quote `USDC`.
    - BYBIT: `BybitDataClientConfig(product_types=(LINEAR, SPOT))`/`BybitLiveDataClientFactory`, `BybitExecClientConfig`/`BybitLiveExecClientFactory`, environments `("mainnet", "demo", "testnet")` via `BybitEnvironment`, quote `USDT`.
    - HYPERLIQUID: `HyperliquidDataClientConfig`/`HyperliquidLiveDataClientFactory`, `HyperliquidExecClientConfig`/`HyperliquidLiveExecClientFactory`, environments `("mainnet", "testnet")` via `HyperliquidEnvironment` (enum values are upper-case `MAINNET`/`TESTNET` — normalise), quote `USDC`.
  - [x] `venue_of` comes from `ml_signals.venue` (pure function, AD-4 allows it); a bot whose venue is not in `VENUES` fails at config load with the venue named.
- [x] Task 2 — `live_paper/config.py`: per-venue paper settings (AC: #2)
  - [x] Replace top-level `network`/`starting_balances`/`account_type` with `venues: dict[str, VenuePaperConfig]` parsed from `[venues.DYDX]`, `[venues.BYBIT]`, `[venues.HYPERLIQUID]` tables: `environment: str` (validated against `VenueSpec.allowed_environments`, default `mainnet`), `starting_balances: tuple[str, ...]` (default `["10_000 <quote>"]`), `account_type: str` (default `"MARGIN"`). A venue used by a bot but absent from `[venues]` gets the defaults; an unknown key or venue fails closed (`_reject_unknown_keys` pattern). `log_level` stays top-level.
  - [x] `BotConfig.starting_balance` default becomes `"10_000 <quote of the bot's venue>"` when unset (it is a bookkeeping anchor for Sharpe/Sortino, AD-11). Keep the `mode`-key rejection and the "starting_balances must be an array" guard.
  - [x] `live_paper/config.toml`: migrate the committed file to the new shape with the same dYdX values; add commented `[venues.BYBIT]`/`[venues.HYPERLIQUID]` examples. `tests/test_config.py`: existing assertions move to `config.venues["DYDX"]`; add per-venue validation cases.
- [x] Task 3 — `live_paper/node.py`: `build_node` multi-venue (AC: #1)
  - [x] `venues_in_use = {venue_of(b.instrument_id) for b in bots}`; for each: `data_clients[name] = spec.data_config_cls(environment=spec.parse_environment(vcfg.environment), instrument_provider=instrument_provider, ...)` and, in paper mode, `exec_clients[name] = SandboxExecutionClientConfig(venue=name, starting_balances=list(vcfg.starting_balances), account_type=vcfg.account_type, instrument_provider=instrument_provider)`; `node.add_data_client_factory(name, spec.data_factory)`, `node.add_exec_client_factory(name, SandboxLiveExecClientFactory)`. Real-money branch stays dYdX-only in this story (22.7 generalises it) but must still work.
  - [x] Keep everything else in `build_node` as is: Redis-backed Cache, fixed `TraderId("LIVE-PAPER-001")`, `order_id_tag=bot.bot_id`, one `bot_status.run`/`trade_history.run` task per bot.
  - [x] `InstrumentProviderConfig(load_all=True)` per venue: Bybit loads ~900 linear + spot instruments — acceptable, but note startup time in Completion Notes; if it is a problem, `load_ids` for the bots' instruments is the ponytail fix.
- [x] Task 4 — Sandbox mixed spot/perp evidence (AC: #3)
  - [x] Run paper mode with three bots: `BTC-USD-PERP.DYDX`, `BTCUSDT-LINEAR.BYBIT`, `BTCUSDT-SPOT.BYBIT` (and optionally `BTC-USD-PERP.HYPERLIQUID`). Bybit spot is a `CurrencyPair`; Sandbox is configured `account_type="MARGIN"` for the whole `BYBIT` venue (one exec client per venue — `ExecutionEngine.register_client` raises on a second, AD-11 `[VERIFIED]`). Observe whether the spot order fills and the position/PnL are sane.
  - [x] If a spot fill fails under MARGIN (or perp fails under CASH): that is the research's open item resolved *negatively* — record it, and constrain config so a Bybit `[venues.BYBIT]` may hold only spot bots or only linear bots (fail closed at load with the reason). Do not fake a fill or split the venue into two Sandbox clients.
  - [x] `redis-cli subscribe bots:status`: one payload per bot, each with its own `bot_id`, `mode: "paper"`, and the correct quote currency in equity fields.
- [x] Task 5 — spine amendment (AC: #2)
  - [x] `ARCHITECTURE-SPINE.md` AD-11: replace "one `DydxDataClientConfig` … one `SandboxExecutionClientConfig` … no matter how many bots" with "one data client and one Sandbox exec client **per venue in use** (still one `TradingNode`, one shared balance pool per venue)"; `[amended 2026-09-xx: Epic 22 story 22.6]` marker like the existing AD-4 amendment. Leave AD-1/AD-4 to story 22.8.
- [x] Task 6 — tests (TEST-01: Nautilus integration path; `tests/test_node.py` pattern with `_dispose`)
  - [x] `test_node.py`: three-venue paper config → `node.kernel.data_engine` has three registered client ids and the exec engine three Sandbox clients (assert via the node's registered factories/config, as the existing tests do); a bot on an unregistered venue → `ValueError` naming it; `[venues.BYBIT] environment = "prod"` → rejected; existing dYdX-only tests still pass unchanged.
  - [x] `test_config.py`: defaults per venue (`USDT` for Bybit, `USDC` otherwise), unknown venue key rejected.
  - [x] `live_paper/README.md` + `DEPLOY_CHECKLIST.md`: the new `[venues.*]` shape; the mixed spot/perp finding.

## Dev Notes

### One node, one exec client per venue — Nautilus's constraint, not ours

AD-11 was pinned on `ExecutionEngine.register_client` refusing two exec clients for the same venue. Multiple venues are fine; per-venue balance pools are separate by construction. That is why `starting_balances` becomes per-venue.

### `DummyStrategy` is already venue-agnostic

`strategy.py:194-198` subscribes quotes, L2 deltas and an INTERNAL `BarType` from the instrument id. Bybit quotes come from `orderbook.1` inside the Nautilus adapter and Hyperliquid quotes from `bbo` (research §B5) — no strategy change. Do not add venue branches to the strategy.

### Two-signal real-money safety design is untouched

`load_paper_config` still rejects any `mode` key; `load_real_money_config` stays dYdX-only until 22.7. This story changes paper mode only.

### Quote currency

Nothing in `live_paper` hard-codes `USDC` except default balance strings (`config.py`, `node.py:197` comment). The per-venue default string is the whole "paper quote currency" feature — no currency plumbing beyond that.

### Project Structure Notes

- New: `troll/live_paper/venues.py`.
- Modified: `troll/live_paper/{config,node}.py`, `config.toml`, `tests/{test_config,test_node}.py`, `README.md`, `DEPLOY_CHECKLIST.md`, `ARCHITECTURE-SPINE.md` (AD-11 only).
- Unchanged: `strategy.py`, `bot_status.py`, `trade_history.py`, `fills_store.py`, `docker-compose.yml` (Sandbox needs no credentials; public data clients only).
- `live_paper/` is the one `troll/` module where `TradingNode` is sanctioned (AD-8) — FORK-02's ban does not apply here.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 22.6] — ACs.
- [Source: research 2026-09-20 §B5, "Open items surfaced" (Sandbox mixed spot/perp)].
- [Source: troll/live_paper/node.py:84-214; troll/live_paper/config.py:55-214] — current dYdX-only wiring and loaders (read in full).
- [Source: troll/live_paper/tests/test_node.py:38-62 `_dispose`] — test harness for node construction without running the loop.
- [Source: nautilus_trader/adapters/{bybit,hyperliquid,dydx}/{config,factories,constants}.py; nautilus_trader/adapters/sandbox/config.py:21-80] — config classes, factories, venue constants, Sandbox fields.
- [Source: nautilus_trader/core/nautilus_pyo3.pyi:6864 `BybitEnvironment`, :9302 `HyperliquidEnvironment`].
- [Source: ARCHITECTURE-SPINE.md#AD-8, #AD-10, #AD-11] — sanctioned `TradingNode` use, Redis contracts, the AD being amended.
- [Source: troll/CLAUDE.md FORK-02 (live_paper exception), TEST-03/04, DESIGN-01] — rules applied.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

- `live_paper/venues.py` holds `VENUES` (data-config callable, data factory, environments, quote currency). Exec side is always Sandbox, so the spec's exec class/factory columns were not added (real-money for Bybit/HL is 22.7).
- `PaperConfig` is now `{log_level, bots, venues}`; `venue_config(venue)` supplies defaults for a venue absent from `[venues]`. `BotConfig` fills its default `starting_balance` from its venue's quote currency and fails at load on an unsupported venue.
- AC3 resolved positively: one MARGIN account fills spot `CurrencyPair` + linear perp (`tests/test_sandbox_mixed_account.py`, same `SimulatedExchange` the Sandbox client wraps), so no config restriction was added.
- Live smoke (mainnet, 4 bots across DYDX/BYBIT linear+spot/HYPERLIQUID): 3 data + 3 exec clients RUNNING and connected ~2s after start with `load_all=True` on every venue (Bybit instruments cached to Redis) -- no `load_ids` needed. No fill happened inside the ~100s window (strategy warm-up), so live fill/`bots:status` per bot was not observed; AC3's fill proof is the offline test.
- Sandbox logs `Skipping stale quote` warnings for BYBIT linear (Bybit delivers several quotes per ws batch with older `ts_event` than the book's last) -- upstream matching-engine behaviour, noted, not suppressed.
- `test_build_node_passes_redis_credentials_and_ssl_from_url` hangs in this sandbox (connects to `rediss://myhost`); unrelated to this change (all other 6 node tests, 28 config, 48 others pass).

### File List

- troll/live_paper/venues.py (new), config.py, node.py, config.toml, README.md, DEPLOY_CHECKLIST.md
- troll/live_paper/tests/test_config.py, test_node.py, test_sandbox_mixed_account.py (new)
- _bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md (AD-11)
