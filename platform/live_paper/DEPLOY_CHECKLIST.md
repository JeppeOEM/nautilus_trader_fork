# live_paper Manual Test Checklist (pre-deploy)

Grounded in the current code: `node.py` (paper mode always uses
`SandboxExecutionClientConfig` — no real funds reachable regardless of
`[venues.*]`), `strategy.py` (`DummyStrategy`, Story 3.2), `bot_status.py`
(`bots:status`/`bots:control`), `config.py`, `docker-compose.yml` /
`live_paper.dockerfile`.

Baseline before starting: `make test-live-paper` passes (34/34 as of writing).

## Pre-flight (before touching the container)

- [ ] `platform/live_paper/config.toml` has no `mode` key, and
      `LIVE_PAPER_REAL_MONEY_CONFIG` is **unset** on the host — this is the
      only thing standing between paper and a real venue exec client (either
      `mode`: `real_money` or `exchange_demo`).
- [ ] `bot_id` in config.toml doesn't collide with another running bot's id —
      status/control are keyed on it.
- [ ] Sanity-check `trade_size` against current instrument price ×
      min-notional — not accidentally huge or below the venue's min order
      size.
- [ ] Each `[venues.*]` table's `starting_balances` / `account_type` give enough margin that a single
      order won't get rejected by the sandbox exec client.
- [ ] `mkdir -p live_paper/data` **before** the first `docker compose up`/`run` for
      `live-paper`, owned by the host user. If Docker creates this bind-mount target
      itself (directory doesn't exist yet), it lands `root:root` — the container runs
      as `user: "1000:1000"`, so every `fills.db` write then fails with "unable to open
      database file". The `bots:history` refresh timer degrades gracefully (AC4: logs a
      warning, keeps stale data), but confirmed live 2026-09-02 that the on-fill write
      path does not — an unhandled exception in the synchronous `OrderFilled` handler
      crashed the whole node on the very first real fill (now fixed to catch-and-log
      instead, but a wrong-permission data dir still means **no trade history gets
      recorded at all**, silently). Same gotcha applies to any other bind-mounted
      writable dir first created by a container run as a non-default UID.

## Build & static checks

- [ ] `make build-base`, then build the `live-paper` image cleanly (kernel
      + live_paper + observability copied in, no dydx_collector, no ml_signals).
- [ ] `ruff check`, `ruff format --check`, `mypy` over `platform/live_paper/`
      and `platform/kernel/` (the indicators the live strategy imports moved to
      the shared kernel in Story 23.2; `ml_signals` is no longer in this image).
- [ ] `make test-live-paper` inside the actual image, not just host pytest —
      catches missing deps in `requirements.txt`.

- [ ] Multi-venue: each bot's `instrument_id` venue suffix is DYDX, BYBIT or HYPERLIQUID; Bybit spot and linear bots may share the node (one MARGIN account fills both -- see README).

## Credentials — which env var each mode reads

The explicit path (`LIVE_PAPER_REAL_MONEY_CONFIG`) never reads a credential in Python:
each adapter's Rust client picks the pair matching the `environment` it was built with,
straight out of the process environment. `docker-compose.yml`'s `live-paper` service
forwards each of these only when the host exports it (an unset var stays unset in the
container -- never an empty string, which the Rust clients would take as a key), so
exporting them on the host (or putting them in `platform/.env`) is all that is needed.

| Venue | `environment` | Env vars read |
|---|---|---|
| BYBIT | `mainnet` | `BYBIT_API_KEY`, `BYBIT_API_SECRET` |
| BYBIT | `demo` | `BYBIT_DEMO_API_KEY`, `BYBIT_DEMO_API_SECRET` |
| BYBIT | `testnet` | `BYBIT_TESTNET_API_KEY`, `BYBIT_TESTNET_API_SECRET` |
| HYPERLIQUID | `mainnet` | `HYPERLIQUID_PK`, `HYPERLIQUID_VAULT`, `HYPERLIQUID_ACCOUNT_ADDRESS` |
| HYPERLIQUID | `testnet` | `HYPERLIQUID_TESTNET_PK`, `HYPERLIQUID_TESTNET_VAULT`, `HYPERLIQUID_ACCOUNT_ADDRESS` |
| DYDX | `mainnet` | `DYDX_PRIVATE_KEY`, `DYDX_WALLET_ADDRESS` |
| DYDX | `testnet` | `DYDX_TESTNET_PRIVATE_KEY`, `DYDX_TESTNET_WALLET_ADDRESS` |

`HYPERLIQUID_ACCOUNT_ADDRESS` is deliberately **not** environment-suffixed — the same
name is read on both networks.

- [ ] **Pre-flight, every explicit run:** confirm the right pair is actually exported
      (`docker compose --profile live-paper run --rm live-paper printenv BYBIT_DEMO_API_KEY`).
      A missing key does **not** fail startup — the client comes up *unauthenticated* and
      only the first order tells you, so check before, not after.

## Bybit Demo (`mode = "exchange_demo"`, `environment = "demo"`)

Bybit Demo is **not** Bybit Testnet: it is a demo *account* hanging off the production
exchange (`api-demo.bybit.com`, `wss://stream-demo.bybit.com/v5/private`), so the public
market data is real mainnet data and only the private/account side is play money. A
testnet key will not authenticate against it and vice versa.

- [ ] Create the demo API key from Bybit's **Demo Trading** page (the key created under
      the normal API-management page is a mainnet key; the testnet site's key is a third,
      separate one). Export it as `BYBIT_DEMO_API_KEY`/`BYBIT_DEMO_API_SECRET`.
- [ ] Fund the demo account — `POST /v5/account/demo-apply-money` (or the "request funds"
      button on the demo-trading page). A fresh demo account has a zero balance and every
      order is rejected until it is funded.
- [ ] Write the config to its own file, e.g. `live_paper/bybit_demo.toml`:
      `mode = "exchange_demo"`, `environment = "demo"`,
      `instrument_id = "BTCUSDT-LINEAR.BYBIT"` (the id suffix picks the product type).
      Never commit it.
- [ ] Run it with `LIVE_PAPER_REAL_MONEY_CONFIG` pointed at that file, mounted read-only.
- [ ] Orders go over HTTP: Bybit Demo has no WS Trade API, so `use_ws_execution_fast`
      stays `False` (the code leaves it at its default — do not turn it on here).
- [ ] Confirm one order end to end: submit → fill/ack → `bots:status` shows
      `mode: "demo"` with the position and PnL fields moving.

## Hyperliquid Testnet (`mode = "exchange_demo"`, `environment = "testnet"`)

- [ ] Export `HYPERLIQUID_TESTNET_PK` (and `HYPERLIQUID_TESTNET_VAULT` only if trading a
      vault). `HYPERLIQUID_ACCOUNT_ADDRESS` is shared with mainnet — set it if the signing
      key is an API wallet rather than the account itself.
- [ ] Get testnet USDC from the faucet (`claimDrip`, 1,000 mock USDC). **It requires a
      prior mainnet deposit from the same address** — a never-used address cannot claim,
      so budget for that step rather than discovering it at the faucet.
- [ ] Expect thin testnet books: the strategy's thresholds and `trade_size` may need
      loosening to get a fill at all. That is a mechanics run, not a P&L run.
- [ ] Confirm the same end-to-end order as above, with `mode: "demo"` in `bots:status`.

## Dry run — dYdX testnet, paper (Sandbox) execution

`SandboxExecutionClientConfig` makes paper mode 100% simulated money on
*either* network — `[venues.*] environment` only controls which market data the data
client pulls from. Testnet proves the plumbing works without real BTC price
action driving the strategy yet. No prior testnet usage exists anywhere in
this codebase, so treat this as unverified territory.

- [ ] Make a scratch copy, e.g. `config.testnet.toml`, with
      `[venues.DYDX] environment = "testnet"` (everything else the same).
- [ ] Run it standalone, bypassing compose's single hardcoded mount:
      ```
      docker compose --profile live-paper run --rm \
        -v $(pwd)/live_paper/config.testnet.toml:/app/live_paper/config.toml:ro \
        live-paper
      ```
- [ ] If `instrument_id` doesn't exist on testnet, `on_start`'s instrument
      check stops the node cleanly and logs it — just run it and see, no
      pre-check needed.
- [ ] Confirm the full chain: quote/book/bar subscriptions populate,
      `obi`/`mlofi`/`trend` all reach `initialized`, `bots:status` heartbeats
      every 5s with `mode: "paper"`.
- [ ] Force at least one trade signal (testnet is thin/choppy, thresholds may
      trip faster than mainnet — fine, this run is about mechanics not P&L
      realism) — confirm `submit_order` → sandbox fill → `bots:status` PnL
      fields update, no errors.
- [ ] Exercise `bots:control` start/stop against this run too (see below).
- [ ] Tear down, delete the scratch config, move to the real `config.toml`
      (`[venues.DYDX] environment = "mainnet"`) for the checks below.

## First run — mainnet paper (watch it live via dozzle + Redis)

- [ ] `make up-live-paper`, tail dozzle: instrument lookup succeeds, threshold
      and positivity validations pass (all in `on_start`).
- [ ] Confirm `subscribe_quote_ticks`, `subscribe_order_book_deltas(L2_MBP)`,
      and `subscribe_bars(1-MINUTE-LAST-INTERNAL)` all actually receive data
      — the strategy's module docstring flags INTERNAL bar aggregation as
      *not yet verified live*; watch for `on_bar` firing within a few
      minutes.
- [ ] Confirm the 1s book-snapshot timer fires and `obi`/`mlofi` reach
      `initialized` (needs `ofi_window` snapshots, ~20s by default).
- [ ] `redis-cli subscribe bots:status` — payload every 5s, `mode: "paper"`,
      `bot_id` matches config, `running: true`.
- [ ] Confirm whether the strategy auto-starts on `TradingNode.run()` or sits
      idle until a `bots:control start` arrives — know which, so a "healthy
      but stopped" bot isn't mistaken for a working one.

## Control-loop / dashboard integration

- [ ] `redis-cli publish bots:control '{"bot_id":"bot-01","action":"stop"}'`
      — strategy stops, status flips `running: false`, no more orders.
- [ ] Send `action: "start"` again — confirm it resumes (this is the whole
      point of `bot_status.run` living outside the Strategy's own lifecycle).
- [ ] A message with the wrong `bot_id` is silently ignored — no crash, no
      cross-bot control.

## Order/PnL correctness

- [ ] Let a real long/short signal fire — confirm exactly one order per
      `_maybe_trade` call (in-flight-orders guard), not a duplicate per tick
      while the signal stays true.
- [ ] Reversal is two steps (flatten, then re-enter next cycle) as
      documented — not a same-tick double-size flip.
- [ ] Cross-check `bots:status`'s `net_exposure`/`realized_pnl`/
      `unrealized_pnl` against fills seen in the dozzle log.
- [ ] Stop the bot mid-position via `bots:control` — confirm it does **not**
      auto-flatten (no `on_stop` flatten logic exists) — decide if that's
      acceptable before leaving it stopped overnight with an open position.

## Resilience

- [ ] Kill/restart the `redis` container mid-run — confirm `bot_status.run`'s
      reconnect-in-2s loop recovers (heartbeat + control both resume).
- [ ] Watch a natural dYdX WS reconnect — confirm no double-subscribe or
      silently dropped signals afterward.
- [ ] Deliberately break `config.toml` once (e.g.
      `trend_buy_threshold <= trend_sell_threshold`) — confirm it hits
      `self.stop()` cleanly rather than crash-looping, and that
      `restart: on-failure:5` doesn't hammer dYdX's API 5x in a row on a
      persistent bad config.

## Soak test — the one that matters most

`live_paper` is the one sanctioned place in this repo using real
`TradingNode`/`DataEngine` (AD-8 exempts it from the `platform/CLAUDE.md`
`TradingNode`/`DataEngine` ban — it does not fix the underlying bug). The
documented unbounded-queue-growth + shutdown-wedge bug that OOM-crashed the
earlier collector is still present in the engine here.

- [ ] Run for several hours minimum before trusting it unattended. Watch
      container memory (`docker stats`) — unbounded growth under
      quote/book-delta volume is that same failure mode resurfacing.
- [ ] Confirm a clean `docker compose stop live-paper` actually exits (not
      wedged) — the shutdown half of the same bug.
