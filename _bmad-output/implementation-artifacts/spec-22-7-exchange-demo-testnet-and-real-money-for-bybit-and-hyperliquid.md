---
title: 'Story 22.7: Exchange demo/testnet and real money for Bybit and Hyperliquid'
type: feature
created: '2026-09-20'
status: awaiting-operator
baseline_revision: 6ee57aadb27e28b0cd511552db7d9d13259631a0
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/troll/CLAUDE.md'
warnings: [oversized]
---

<intent-contract>

## Intent

**Problem:** `live_paper`'s explicit (non-Sandbox) execution path is still dYdX-only: `RealMoneyConfig` carries a `network: DydxNetwork` and `build_node` hard-codes `DydxExecClientConfig`, so there is no way to reach Bybit Demo or Hyperliquid Testnet — the exchange-owned paper environments where real order signing and execution reports must be proven before real funds are used.

**Approach:** Generalise the separately-loaded config to two explicit modes (`real_money`, `exchange_demo`) over a venue-agnostic `environment` string, extend `VenueSpec` with the exec-side columns story 22.6 deferred, and drive `build_node`'s explicit branch off that table. Credentials stay env-var-only (the Rust clients resolve them from the environment flag), so no secret ever enters a config file or a log line.

## Boundaries & Constraints

**Always:** Preserve story 3.1's two-signal design — a separate file behind `LIVE_PAPER_REAL_MONEY_CONFIG` (no default) plus an explicit `mode` key inside it; the new `mode`/`environment` cross-check is a third signal, never a replacement. Every validation failure names both offending values. Credentials are passed as `None` so the Rust client reads the per-environment env var; `load_paper_config` keeps rejecting any `mode` key.

**Block If:** A change would let a single edited key promote a demo config to mainnet, or would require reading a secret in Python.

**Never:** Place a real order on a mainnet account in this story (config path only). Never add credential fields to any config dataclass. Never touch `strategy.py`, the collectors, or `crates/**`. Never commit a demo or real-money config file.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Real money, mainnet | `mode = "real_money"`, `environment = "mainnet"`, dYdX id | `ExecConfig` loads; `DydxExecClientConfig(environment=MAINNET, subaccount=…)` | No error expected |
| Bybit demo | `mode = "exchange_demo"`, `environment = "demo"`, `BTCUSDT-LINEAR.BYBIT` | `BybitExecClientConfig(environment=DEMO, product_types=(LINEAR,))`, `api_key=None` | No error expected |
| Bybit spot demo | same, `BTCUSDT-SPOT.BYBIT` | `product_types == (SPOT,)` | No error expected |
| HL testnet | `mode = "exchange_demo"`, `environment = "testnet"`, `…​.HYPERLIQUID` | `HyperliquidExecClientConfig(environment=TESTNET)`, `private_key=None` | No error expected |
| Mode/env mismatch | `real_money` + `testnet`, or `exchange_demo` + `mainnet` | Load fails | `ValueError` naming both `mode` and `environment` |
| Env not allowed for venue | `exchange_demo` + `demo` on a DYDX or HYPERLIQUID id | Load fails | `ValueError` naming the environment and the venue's allowed set |
| Missing / unknown mode | no `mode`, or `mode = "paper"` | Load fails | `ValueError` listing the two accepted modes |
| dYdX-only key elsewhere | `subaccount` set on a Bybit/HL file | Load fails | `ValueError` — `subaccount` is dYdX-only |
| Paper loader sees `mode` | `mode` key in `config.toml` | Load fails (unchanged) | existing `ValueError` |

</intent-contract>

## Code Map

- `troll/live_paper/venues.py` -- `VenueSpec`/`VENUES` table; gains exec-side columns (22.6 left them out deliberately).
- `troll/live_paper/config.py` -- `RealMoneyConfig` + `load_real_money_config` (`:107-118`, `:227-250`); the two-signal rationale lives in the module docstring.
- `troll/live_paper/node.py` -- `build_node`'s dYdX-only explicit branch (`:113-131`) and the `mode` label (`:197`).
- `troll/live_paper/tests/{test_config,test_node}.py` -- existing real-money cases use `network = "mainnet"`; they move to `environment`.
- `troll/common/venues.py` -- `market_kind()`; Bybit product type comes from the same id suffix.
- `troll/docker-compose.yml` -- `live-paper` service `environment:` block (`:233-239`).
- `troll/live_paper/{README,DEPLOY_CHECKLIST}.md` -- operator docs.
- Reference only: `nautilus_trader/adapters/bybit/config.py:172-195`, `factories.py:86-99`; `crates/adapters/bybit/src/common/credential.rs:33-35`; `crates/adapters/hyperliquid/src/common/credential.rs:41-45`; `crates/adapters/hyperliquid/src/account.rs:37-90`.

## Tasks & Acceptance

**Execution:**
- [x] `troll/live_paper/venues.py` -- add `exec_config_cls`, `exec_factory`, and `exec_kwargs: Callable[[ExecConfig], dict]` (default `{}`) to `VenueSpec`; fill all three entries; add a Bybit product-type-from-instrument helper using the id suffix -- keeps per-venue knowledge in the one table instead of an `if venue ==` chain in `node.py`.
- [x] `troll/live_paper/config.py` -- rename `RealMoneyConfig` to `ExecConfig`; `mode: str` accepts `"real_money"` and `"exchange_demo"`; replace `network: DydxNetwork` with validated lowercase `environment: str`; keep `subaccount` but reject it for non-dYdX venues; validate in the documented order, all fail-closed; update the module docstring so it describes both explicit-path modes.
- [x] `troll/live_paper/node.py` -- drive the explicit branch off `VENUES[venue_of(config.instrument_id)]` (data config + exec config + factory + per-venue kwargs, credentials left `None`); `mode` label becomes `live`/`demo`/`paper`.
- [x] `troll/docker-compose.yml` -- pass the Bybit/Hyperliquid credential env vars through to `live-paper` with empty defaults (`"${VAR:-}"`), no new ports (SEC-01).
- [x] `troll/live_paper/tests/test_config.py` -- the mode × environment × venue matrix from the I/O table, plus `subaccount`-on-Bybit and paper-loader-still-rejects-`mode`.
- [x] `troll/live_paper/tests/test_node.py` -- construction-only assertions for Bybit demo (LINEAR and SPOT), Hyperliquid testnet, and the unchanged dYdX real-money case; dispose via the existing `_dispose`.
- [x] `troll/live_paper/DEPLOY_CHECKLIST.md` -- "Bybit Demo" and "Hyperliquid Testnet" sections + a per-venue/environment env-var table; keep the dYdX testnet dry-run section.
- [x] `troll/live_paper/README.md` -- the two modes and the promotion rule.

**Acceptance Criteria:**
- Given a config whose `mode` and `environment` disagree, when it is loaded, then startup fails with a message naming both values and no client is constructed.
- Given a Bybit `exchange_demo` config, when `build_node` runs, then the BYBIT exec client is a `BybitExecClientConfig` with `environment == BybitEnvironment.DEMO`, `use_ws_execution_fast` left `False` (Bybit Demo has no WS Trade API), and `api_key`/`api_secret` both `None`.
- Given a Hyperliquid `exchange_demo` config, when `build_node` runs, then the HYPERLIQUID exec client is a `HyperliquidExecClientConfig` with `environment == HyperliquidEnvironment.TESTNET` and `private_key is None`.
- Given the existing dYdX real-money config shape (with `network` renamed to `environment`), when `build_node` runs, then behaviour is unchanged: `DydxExecClientConfig` with the configured subaccount.
- Given `bots:status` for an `exchange_demo` run, when a payload is published, then `mode == "demo"` and it renders in `bot_tui`'s bots pane without pushing later columns out of alignment.

## Spec Change Log

## Review Triage Log

## Design Notes

Bybit Demo is not Bybit Testnet: Demo is mainnet public data plus a demo private account at `api-demo.bybit.com` / `wss://stream-demo.bybit.com/v5/private`, funded by `POST /v5/account/demo-apply-money`, with its own API key. Nautilus models both as `BybitEnvironment` values and picks the URLs itself.

Credentials never appear in Python. `get_cached_bybit_http_client` turns `environment` into `demo`/`testnet` flags and the Rust client picks the pair: `BYBIT_API_KEY`/`SECRET`, `BYBIT_DEMO_API_KEY`/`SECRET`, `BYBIT_TESTNET_API_KEY`/`SECRET`. Hyperliquid: `HYPERLIQUID_PK` vs `HYPERLIQUID_TESTNET_PK` (plus un-suffixed `HYPERLIQUID_ACCOUNT_ADDRESS`, and `HYPERLIQUID_VAULT`/`HYPERLIQUID_TESTNET_VAULT`). A missing key yields an *unauthenticated* client rather than an error, so the checklist must make "keys exported" a pre-flight step.

PyO3 enums are resolved by upper-cased member name, the same trick `VenueSpec.parse_environment` already uses -- `getattr(BybitProductType, "LINEAR")`, not `BybitProductType("linear")`.

The live end-to-end runs (Bybit Demo order placed and cancelled; Hyperliquid testnet faucet) need exchange accounts, API keys and a funded wallet, so they are operator actions recorded in the checklist, not agent work.

## Verification

**Commands:**
- `cd troll && python -m pytest live_paper/tests/test_config.py live_paper/tests/test_node.py -q` -- expected: all pass (the pre-existing `rediss://myhost` test hangs in a sandbox; deselect it if so and say so).
- `cd troll && ruff check live_paper && ruff format --check live_paper` -- expected: clean.
- `cd troll && docker compose config -q` -- expected: no output (compose file still valid).
