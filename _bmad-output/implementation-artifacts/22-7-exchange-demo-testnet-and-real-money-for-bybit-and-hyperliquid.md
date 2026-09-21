---
status: awaiting-operator
operator_actions:
  - Bybit Demo live check (Task 5): export `BYBIT_DEMO_API_KEY`/`BYBIT_DEMO_API_SECRET` (a demo-trading key, not testnet; fund via `POST /v5/account/demo-apply-money`), run `live_paper` with `LIVE_PAPER_REAL_MONEY_CONFIG=<file with mode = "exchange_demo", environment = "demo", instrument_id = "BTCUSDT-LINEAR.BYBIT">`, force one signal (tiny `trade_size`, relaxed thresholds), confirm `OrderSubmitted -> OrderAccepted -> fill/cancel` in the node log and the order in `bots:status`/`bots:history`, cancel any resting order, and record the order id under Completion Notes.
  - Hyperliquid Testnet (Task 5): only if the faucet prerequisite is met (a mainnet deposit from the same address, then `claimDrip`); otherwise leave the sub-task unticked with the blocking step named. No demo/testnet key of either venue exists on the dev host, which is what blocked both checks here.
  - Deploy on the VPS with `make redeploy` and confirm the paper path still starts with none of the new credential env vars exported (they default to empty in docker-compose.yml).
  - Run `ruff --fix` and `mypy` over `troll/live_paper`; neither is installed on the dev host (the story's files carry exactly the baseline's pre-existing findings, no new ones). [done locally 2026-09-21 -- ruff committed as 154d9898ad; mypy over live_paper reports 1 finding before and after, identical (node.py unused type-ignore comment)]
followup_review_recommended: false
baseline_revision: 6ee57aadb27e28b0cd511552db7d9d13259631a0
---

# Story 22.7: Exchange demo/testnet and real money for Bybit and Hyperliquid

Status: awaiting-operator

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

- [x] Task 0 — precondition: story 22.6's `live_paper/venues.py` (`VenueSpec` with exec config class + factory + allowed environments) is merged.
- [x] Task 1 — loader: `RealMoneyConfig` → `ExecConfig` with `mode` (AC: #1)
  - [x] `live_paper/config.py`: the separately-loaded config gains `mode: Literal["real_money", "exchange_demo"]`, `environment: str` (replaces the dYdX-only `network: DydxNetwork`; parsed per venue via `VenueSpec.parse_environment`), keeps `subaccount` (dYdX-only; reject it for other venues), `instrument_id`, sizing/thresholds, `bot_id`, `log_level`. Venue = `venue_of(instrument_id)`. Validation, in this order and all fail-closed with the offending values in the message: `mode` missing/unknown → error; `mode == "real_money" and environment != "mainnet"` → error; `mode == "exchange_demo" and environment == "mainnet"` → error; `environment not in VENUES[venue].allowed_environments` → error; unknown keys → error (`_reject_unknown_keys`). Keep the function name `load_real_money_config` (story 3.1's tests + `resolve_config` + `node.main` use it) — its docstring explains it now loads both explicit-path modes.
  - [x] `load_paper_config` is unchanged: still rejects any `mode` key. The env var `LIVE_PAPER_REAL_MONEY_CONFIG` stays the single explicit gate for **both** modes — a demo file cannot promote to mainnet without editing both `mode` and `environment`, which is the two-signal property being preserved.
- [x] Task 2 — `build_node` explicit-path branch (AC: #2, #3)
  - [x] Replace the dYdX-only `DydxExecClientConfig` branch: `spec = VENUES[venue]`; `exec_clients[venue] = spec.exec_config_cls(environment=spec.parse_environment(config.environment), instrument_provider=instrument_provider, **venue_kwargs)` where `venue_kwargs` is `{"subaccount": config.subaccount}` for dYdX, `{"product_types": (bybit_product_type_from_symbol(symbol),)}` for Bybit (LINEAR or SPOT from the bot's instrument), `{}` for Hyperliquid; `exec_factory = spec.exec_factory`. Data client as in 22.6 for that one venue. Credentials: **never** passed from config — `api_key=None`/`private_key=None` so the Rust clients read `BYBIT_API_KEY`/`BYBIT_API_SECRET` (mainnet), `BYBIT_DEMO_API_KEY`/`BYBIT_DEMO_API_SECRET` (demo), `BYBIT_TESTNET_API_KEY`/`BYBIT_TESTNET_API_SECRET` (testnet), `HYPERLIQUID_PK`/`HYPERLIQUID_TESTNET_PK` (+ optional `HYPERLIQUID_ACCOUNT_ADDRESS`, `HYPERLIQUID_VAULT`/`HYPERLIQUID_TESTNET_VAULT`) — names verified in `crates/adapters/{bybit,hyperliquid}/src`.
  - [x] Bybit demo specifics: no WS Trade API → leave `use_ws_execution_fast=False` (default) so orders go over HTTP; `BybitEnvironment.DEMO` makes Nautilus pick the demo base URLs (`factories.py:89-97`). Demo funds are applied by the operator (checklist), not by code.
  - [x] `bots:status` `mode` label: `"live"` for real_money, `"demo"` for exchange_demo, `"paper"` otherwise (`node.py:177`); `bot_status.run(mode=...)` already takes a string. Check `bot_tui`'s bots pane renders a 4-char `demo` without overflow (TUI-02: bounded enum, `:<N` is fine).
- [x] Task 3 — deployment plumbing (AC: #2, #3)
  - [x] `docker-compose.yml` `live-paper` service `environment:` — pass through the credential env vars with empty defaults (`BYBIT_DEMO_API_KEY: "${BYBIT_DEMO_API_KEY:-}"` etc.) so an unset var is empty, never a literal placeholder; nothing is logged (adapter rule: credentials never printed). No new ports (SEC-01).
  - [x] `live_paper/DEPLOY_CHECKLIST.md`: a "Bybit Demo" section (create a demo API key on the demo-trading page, not testnet; fund via `POST /v5/account/demo-apply-money`; public market data still comes from mainnet; HTTP-only orders) and a "Hyperliquid Testnet" section (a mainnet deposit from the same address must exist before `claimDrip` grants 1,000 mock USDC; testnet liquidity is thin, thresholds may need loosening; `HYPERLIQUID_TESTNET_PK`), plus an env-var table per venue/environment. Keep the existing dYdX testnet dry-run section.
  - [x] `live_paper/README.md`: the two modes and the promotion rule (edit both keys, separate file, env var).
- [x] Task 4 — tests (TEST-01: config paths that gate real funds)
  - [x] `tests/test_config.py`: matrix — `real_money`+`mainnet` ok; `real_money`+`testnet` rejected; `exchange_demo`+`mainnet` rejected; `exchange_demo`+`demo` ok for Bybit, rejected for dYdX/Hyperliquid (not in their allowed set); `exchange_demo`+`testnet` ok for all three; `subaccount` on a Bybit file rejected; missing `mode` rejected; paper loader still rejects `mode`.
  - [x] `tests/test_node.py`: `exchange_demo` Bybit config → `BybitExecClientConfig` with `environment == BybitEnvironment.DEMO`, `product_types == (LINEAR,)` for a `-LINEAR` id and `(SPOT,)` for a `-SPOT` id, `BybitLiveExecClientFactory` registered; Hyperliquid testnet → `HyperliquidExecClientConfig(environment=TESTNET)`; existing dYdX real-money test unchanged. No network: construction only, with `_dispose`.
- [ ] Task 5 — live verification (AC: #2, #3)
  - [ ] Bybit Demo: with demo keys in env, run `LIVE_PAPER_REAL_MONEY_CONFIG=<demo file>`; force one signal (or a tiny `trade_size` + relaxed thresholds), observe `OrderSubmitted → OrderAccepted → (fill or cancel)` in the node log and the order/fill in `bots:status` + `bots:history`; cancel any resting order; record the order id in Completion Notes.
  - [ ] Hyperliquid Testnet: only if the faucet prerequisite is met — otherwise record exactly which step blocked (DATA-02 end state 2: labelled open, not silently skipped).
  - [x] Real money on Bybit/Hyperliquid: **config path only** in this story — no real order is placed; the checklist's pre-flight covers the first real run.

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

claude-opus-5 via bmad-loop run 20260920-182035-6b7a (dev attempt 1, hit the 90-minute session
timeout with Tasks 1-4 complete but unrecorded), then claude-fable-5-1 interactively after the host
crashed during attempt 2: restored attempt 1's tree from `refs/attempt-preserve-dirty/20260920-182035-6b7a-6ee57aad-1`,
verified it, and parked the story by hand because the run's engine was dead.

### Debug Log References

- The full `live_paper` suite hangs at `NautilusKernel.__init__` unless `REDIS_URL` points at the
  running Redis: the compose stack binds it to host port 16379 (`REDIS_PORT`), while the tests'
  default is 6379. Run with `REDIS_URL=redis://127.0.0.1:16379`. Pre-existing, not this story's.
- `ruff check` / `ruff format --check` over the five changed source files report exactly the same
  13 findings as the baseline commit (import order, docstring style, one S105 in a test) -- nothing new.
- Two pre-existing `test_node.py` tests are host-dependent, not this story's: `test_build_node_configures_redis_backed_cache`
  asserts port 6379 (fails under the `REDIS_URL` override above), and `test_build_node_passes_redis_credentials_and_ssl_from_url`
  builds a node against `rediss://myhost:6380`, which blocks in `NautilusKernel.__init__` on this host
  until pytest's faulthandler timeout. Both were deselected for the story's run; each is a candidate
  for a `Known limit:` note in story 4.6's follow-ups (the kernel connects to Redis at construction).

### Completion Notes List

- **Task 1.** `RealMoneyConfig` -> `ExecConfig` (`mode` in {`real_money`, `exchange_demo`},
  `environment` string parsed per venue by `VenueSpec.parse_environment`). `load_real_money_config`
  keeps its name and validates in the story's order, each failure naming the offending values:
  unknown/missing `mode`; `mode`/`environment` disagreement via `_MODE_ENVIRONMENTS`
  (`real_money` -> mainnet only, `exchange_demo` -> demo/testnet); environment not in the venue's
  `allowed_environments`; `subaccount` on a non-dYdX venue; unknown keys. `load_paper_config` is
  untouched and still rejects any `mode` key, so `LIVE_PAPER_REAL_MONEY_CONFIG` remains the single
  gate for both explicit modes.
- **Task 2.** `build_node`'s explicit branch is venue-generic: `VENUES[venue]` supplies
  `exec_config_cls`, `exec_factory` and `exec_kwargs` (dYdX -> `subaccount`, Bybit ->
  `product_types` from the instrument id's `-LINEAR`/`-SPOT` suffix, Hyperliquid -> nothing).
  No credential is passed from config; the Rust clients read the env-var pair their environment
  selects. `use_ws_execution_fast` stays default False so Bybit Demo orders go over HTTP. The
  `bots:status` mode label is `paper` / `live` / `demo`; `bot_tui` renders it in a `:<5` column.
- **Task 3.** `docker-compose.yml` passes every venue credential through with empty defaults;
  `DEPLOY_CHECKLIST.md` gains a per-venue credential table plus Bybit Demo and Hyperliquid Testnet
  sections; `README.md`, `ARCHITECTURE.md` and `docs/BOT_OPERATIONS.md` describe the two modes and
  the promotion rule (edit both `mode` and `environment`, in a separate file, behind the env var).
- **Task 4.** `tests/test_config.py`: the mode/environment/venue matrix from the story;
  `tests/test_node.py`: Bybit demo exec client (LINEAR and SPOT), Hyperliquid testnet exec client,
  only-that-venue clients, existing dYdX real-money test unchanged. Construction only, `_dispose`.
- **Task 5.** Not runnable here: no `BYBIT_DEMO_API_KEY` or `HYPERLIQUID_TESTNET_PK` on the dev
  host, so both live checks are owed to the operator (see `operator_actions`). Real money on
  Bybit/Hyperliquid is config-path only, as the story requires.
- **Review (interactive, replacing the dead run's review stage).** Two defects found and fixed, see
  the Review Triage Log below. Both fixes are covered by the suite (`test_bybit_exec_config_requires_a_product_type_suffix`
  and the rendered compose block checked with `docker compose --profile '*' config`).

### File List

- troll/live_paper/config.py, troll/live_paper/node.py, troll/live_paper/venues.py
- troll/live_paper/tests/test_config.py, troll/live_paper/tests/test_node.py
- troll/live_paper/DEPLOY_CHECKLIST.md, troll/live_paper/README.md
- troll/docker-compose.yml, troll/ARCHITECTURE.md, troll/docs/BOT_OPERATIONS.md
- _bmad-output/implementation-artifacts/spec-22-7-exchange-demo-testnet-and-real-money-for-bybit-and-hyperliquid.md (new)

## Review Triage Log

### 2026-09-20 — Interactive review (claude-fable-5-1), full diff against 6ee57aad

- intent_gap: 0
- bad_spec: 0
- patch: 2

1. **docker-compose credential passthrough exported unset vars as empty strings** (`VAR: "${VAR:-}"`).
   Traced into the adapters: Bybit's `Credential::resolve` and Hyperliquid's `with_credentials` both
   use `std::env::var(..).ok()`, so a present-but-empty value counts as a key. For the *paper* path
   that means the public Hyperliquid data client would be built with `HYPERLIQUID_PK=""` and fail at
   construction (`EvmPrivateKey::new`: "must be 32 bytes"), and the Bybit instrument provider would
   sign its fee-rate call with an empty secret and log a rejection warning on every load. Fixed by
   forwarding each var value-less (`VAR:`), which `docker compose config` renders as `null` when the
   host does not export it -- unset stays unset. The checklist wording was corrected to match.
2. **Bybit product type derived from the instrument id without validation** -- an id with no
   `-LINEAR`/`-SPOT` suffix reached `getattr(BybitProductType, ...)` and died with an `AttributeError`
   inside `build_node`. Now `_bybit_exec_kwargs` raises a `ValueError` naming the accepted suffixes,
   and `load_real_money_config` runs the venue's `exec_kwargs` once at load so the failure carries
   the file path (fail-closed at the same place as every other config error). Test added.

Checked and left as-is: validation order matches the story; `load_paper_config` untouched;
`bot_tui` renders `mode` in a `:<5` column so `demo` fits; `use_ws_execution_fast` stays default
False; no credential is read in Python; SEC-01 (no new ports) holds.
