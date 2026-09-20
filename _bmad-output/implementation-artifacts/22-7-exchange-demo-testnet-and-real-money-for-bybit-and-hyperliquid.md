# Story 22.7: Exchange demo/testnet and real money for Bybit and Hyperliquid

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the platform operator,
I want the explicit exec-config path to reach Bybit Demo, Hyperliquid Testnet and (separately) real mainnet execution on both venues,
so that real order signing and reports are validated on the exchange's own paper environment before any real funds are used.

## Acceptance Criteria

1. **Two explicit modes, both fail closed.** The separate-file loader (story 3.1's two-signal design: separate file + separate loader + explicit `mode`) accepts `mode = "exchange_demo"` requiring a non-mainnet `environment` (Bybit `demo`/`testnet`, Hyperliquid `testnet`, dYdX `testnet`), and `mode = "real_money"` requiring `environment = "mainnet"`; any mismatch fails with a clear error naming both values.
2. **Bybit Demo works end to end.** Bybit Demo uses `api-demo.bybit.com` + `wss://stream-demo.bybit.com` for private streams only, has no WS Trade API, and takes funds via `POST /v5/account/demo-apply-money` (docs `v5/demo`). A Bybit demo run uses `BybitEnvironment.DEMO`, credentials come only from the env vars Nautilus's factory reads, and one order is placed and cancelled with its reports visible in `bots:status`.
3. **Hyperliquid Testnet works end to end, prerequisites documented.** A Hyperliquid testnet run uses `HyperliquidEnvironment.TESTNET` with `HYPERLIQUID_TESTNET_PK`; `DEPLOY_CHECKLIST.md` documents that the faucet (`claimDrip`, 1,000 mock USDC) needs a prior mainnet deposit from the same address, and the thin-liquidity caveat.

## Tasks / Subtasks

- [ ] Task 0 — precondition: story 22.6's `live_paper/venues.py` (`VenueSpec` with exec config class + factory + allowed environments) is merged.
- [ ] Task 1 — loader: `RealMoneyConfig` → `ExecConfig` with `mode` (AC: #1)
  - [ ] `live_paper/config.py`: the separately-loaded config gains `mode: Literal["real_money", "exchange_demo"]`, `environment: str` (replaces the dYdX-only `network: DydxNetwork`; parsed per venue via `VenueSpec.parse_environment`), keeps `subaccount` (dYdX-only; reject it for other venues), `instrument_id`, sizing/thresholds, `bot_id`, `log_level`. Venue = `venue_of(instrument_id)`. Validation, in this order and all fail-closed with the offending values in the message: `mode` missing/unknown → error; `mode == "real_money" and environment != "mainnet"` → error; `mode == "exchange_demo" and environment == "mainnet"` → error; `environment not in VENUES[venue].allowed_environments` → error; unknown keys → error (`_reject_unknown_keys`). Keep the function name `load_real_money_config` (story 3.1's tests + `resolve_config` + `node.main` use it) — its docstring explains it now loads both explicit-path modes.
  - [ ] `load_paper_config` is unchanged: still rejects any `mode` key. The env var `LIVE_PAPER_REAL_MONEY_CONFIG` stays the single explicit gate for **both** modes — a demo file cannot promote to mainnet without editing both `mode` and `environment`, which is the two-signal property being preserved.
- [ ] Task 2 — `build_node` explicit-path branch (AC: #2, #3)
  - [ ] Replace the dYdX-only `DydxExecClientConfig` branch: `spec = VENUES[venue]`; `exec_clients[venue] = spec.exec_config_cls(environment=spec.parse_environment(config.environment), instrument_provider=instrument_provider, **venue_kwargs)` where `venue_kwargs` is `{"subaccount": config.subaccount}` for dYdX, `{"product_types": (bybit_product_type_from_symbol(symbol),)}` for Bybit (LINEAR or SPOT from the bot's instrument), `{}` for Hyperliquid; `exec_factory = spec.exec_factory`. Data client as in 22.6 for that one venue. Credentials: **never** passed from config — `api_key=None`/`private_key=None` so the Rust clients read `BYBIT_API_KEY`/`BYBIT_API_SECRET` (mainnet), `BYBIT_DEMO_API_KEY`/`BYBIT_DEMO_API_SECRET` (demo), `BYBIT_TESTNET_API_KEY`/`BYBIT_TESTNET_API_SECRET` (testnet), `HYPERLIQUID_PK`/`HYPERLIQUID_TESTNET_PK` (+ optional `HYPERLIQUID_ACCOUNT_ADDRESS`, `HYPERLIQUID_VAULT`/`HYPERLIQUID_TESTNET_VAULT`) — names verified in `crates/adapters/{bybit,hyperliquid}/src`.
  - [ ] Bybit demo specifics: no WS Trade API → leave `use_ws_execution_fast=False` (default) so orders go over HTTP; `BybitEnvironment.DEMO` makes Nautilus pick the demo base URLs (`factories.py:89-97`). Demo funds are applied by the operator (checklist), not by code.
  - [ ] `bots:status` `mode` label: `"live"` for real_money, `"demo"` for exchange_demo, `"paper"` otherwise (`node.py:177`); `bot_status.run(mode=...)` already takes a string. Check `bot_tui`'s bots pane renders a 4-char `demo` without overflow (TUI-02: bounded enum, `:<N` is fine).
- [ ] Task 3 — deployment plumbing (AC: #2, #3)
  - [ ] `docker-compose.yml` `live-paper` service `environment:` — pass through the credential env vars with empty defaults (`BYBIT_DEMO_API_KEY: "${BYBIT_DEMO_API_KEY:-}"` etc.) so an unset var is empty, never a literal placeholder; nothing is logged (adapter rule: credentials never printed). No new ports (SEC-01).
  - [ ] `live_paper/DEPLOY_CHECKLIST.md`: a "Bybit Demo" section (create a demo API key on the demo-trading page, not testnet; fund via `POST /v5/account/demo-apply-money`; public market data still comes from mainnet; HTTP-only orders) and a "Hyperliquid Testnet" section (a mainnet deposit from the same address must exist before `claimDrip` grants 1,000 mock USDC; testnet liquidity is thin, thresholds may need loosening; `HYPERLIQUID_TESTNET_PK`), plus an env-var table per venue/environment. Keep the existing dYdX testnet dry-run section.
  - [ ] `live_paper/README.md`: the two modes and the promotion rule (edit both keys, separate file, env var).
- [ ] Task 4 — tests (TEST-01: config paths that gate real funds)
  - [ ] `tests/test_config.py`: matrix — `real_money`+`mainnet` ok; `real_money`+`testnet` rejected; `exchange_demo`+`mainnet` rejected; `exchange_demo`+`demo` ok for Bybit, rejected for dYdX/Hyperliquid (not in their allowed set); `exchange_demo`+`testnet` ok for all three; `subaccount` on a Bybit file rejected; missing `mode` rejected; paper loader still rejects `mode`.
  - [ ] `tests/test_node.py`: `exchange_demo` Bybit config → `BybitExecClientConfig` with `environment == BybitEnvironment.DEMO`, `product_types == (LINEAR,)` for a `-LINEAR` id and `(SPOT,)` for a `-SPOT` id, `BybitLiveExecClientFactory` registered; Hyperliquid testnet → `HyperliquidExecClientConfig(environment=TESTNET)`; existing dYdX real-money test unchanged. No network: construction only, with `_dispose`.
- [ ] Task 5 — live verification (AC: #2, #3)
  - [ ] Bybit Demo: with demo keys in env, run `LIVE_PAPER_REAL_MONEY_CONFIG=<demo file>`; force one signal (or a tiny `trade_size` + relaxed thresholds), observe `OrderSubmitted → OrderAccepted → (fill or cancel)` in the node log and the order/fill in `bots:status` + `bots:history`; cancel any resting order; record the order id in Completion Notes.
  - [ ] Hyperliquid Testnet: only if the faucet prerequisite is met — otherwise record exactly which step blocked (DATA-02 end state 2: labelled open, not silently skipped).
  - [ ] Real money on Bybit/Hyperliquid: **config path only** in this story — no real order is placed; the checklist's pre-flight covers the first real run.

## Dev Notes

### Preserve the story 3.1 safety design — this story widens it, it does not loosen it

Two signals were required to reach real execution: a separate file (pointed to by an env var with no default) **and** an explicit `mode` inside it. Adding `exchange_demo` as a second value of the same explicit key keeps both signals; the added `environment` cross-check is a third. A demo config with `environment = "mainnet"` must fail, and a real-money config with `environment = "demo"` must fail — "refusing to start rather than guessing operator intent" (`config.py:179`).

### Credentials are env-only — and Nautilus already knows which env var per environment

The Rust clients select the key pair from the environment flag (`BYBIT_DEMO_*` when `demo=True`, `BYBIT_TESTNET_*` when `testnet=True`, else `BYBIT_*`; `HYPERLIQUID_TESTNET_PK` vs `HYPERLIQUID_PK`). Do not add config keys for secrets and do not read them in Python.

### Bybit Demo ≠ Bybit Testnet

Research §A: Demo is mainnet public data + a demo private account (`api-demo.bybit.com`), separate demo API key, funded via `demo-apply-money`; Testnet is a separate exchange. Nautilus models both as `BybitEnvironment` values. The checklist must say "do not use testnet's demo".

### `mode` labels reach the TUI

`bots:status.mode` is displayed by `bot_tui`; `"demo"` is a new bounded value — no `fit()` needed (TUI-02), but grep `bot_tui` for a `mode in ("paper", "live")` assumption before shipping.

### Project Structure Notes

- Modified: `troll/live_paper/{config,node}.py`, `tests/{test_config,test_node}.py`, `DEPLOY_CHECKLIST.md`, `README.md`, `troll/docker-compose.yml` (env passthrough only), possibly `troll/bot_tui/bots_pane.py` (mode label).
- New: none (a demo config file is operator-created, never committed — same rule as the real-money file).
- Unchanged: `strategy.py`, collectors, `crates/**`.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 22.7] — ACs.
- [Source: research 2026-09-20 §A "Paper environments", §B5 "Exchange demo/testnet"].
- [Source: troll/live_paper/config.py:15-50 (two-signal rationale), :172-214 (`load_real_money_config`, `resolve_config`); troll/live_paper/node.py:84-118, :177, :217-231].
- [Source: nautilus_trader/adapters/bybit/factories.py:38-100; nautilus_trader/adapters/bybit/config.py:93-160 (`BybitExecClientConfig` fields incl. `use_ws_execution_fast`); nautilus_trader/adapters/hyperliquid/config.py:53-110].
- [Source: crates/adapters/bybit/src (env var names `BYBIT_API_KEY/SECRET`, `BYBIT_DEMO_API_KEY/SECRET`, `BYBIT_TESTNET_API_KEY/SECRET`); crates/adapters/hyperliquid/src (`HYPERLIQUID_PK`, `HYPERLIQUID_TESTNET_PK`, `HYPERLIQUID_ACCOUNT_ADDRESS`, `HYPERLIQUID_VAULT`, `HYPERLIQUID_TESTNET_VAULT`)] — grepped at story creation.
- [Source: troll/live_paper/DEPLOY_CHECKLIST.md] — sections to extend.
- [Source: _bmad-output/implementation-artifacts/22-6-*.md] — `VenueSpec` this story consumes.
- [Source: troll/CLAUDE.md SEC-01, TUI-02, TEST-01, GIT-01] — rules applied.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
