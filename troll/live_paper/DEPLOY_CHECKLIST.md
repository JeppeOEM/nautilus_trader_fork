# live_paper Manual Test Checklist (pre-deploy)

Grounded in the current code: `node.py` (paper mode always uses
`SandboxExecutionClientConfig` — no real funds reachable regardless of
`network`), `strategy.py` (`DummyStrategy`, Story 3.2), `bot_status.py`
(`bots:status`/`bots:control`), `config.py`, `docker-compose.yml` /
`live_paper.dockerfile`.

Baseline before starting: `make test-live-paper` passes (34/34 as of writing).

## Pre-flight (before touching the container)

- [ ] `troll/live_paper/config.toml` has no `mode` key, and
      `LIVE_PAPER_REAL_MONEY_CONFIG` is **unset** on the host — this is the
      only thing standing between paper and real money.
- [ ] `bot_id` in config.toml doesn't collide with another running bot's id —
      status/control are keyed on it.
- [ ] Sanity-check `trade_size` against current instrument price ×
      min-notional — not accidentally huge or below the venue's min order
      size.
- [ ] `starting_balances` / `account_type` give enough margin that a single
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

- [ ] `make build-base`, then build the `live-paper` image cleanly (ml_signals
      + live_paper copied in, no dydx_collector).
- [ ] `ruff check`, `ruff format --check`, `mypy` over `troll/live_paper/`
      and `troll/ml_signals/` (indicators are imported directly into the
      live strategy now).
- [ ] `make test-live-paper` inside the actual image, not just host pytest —
      catches missing deps in `troll-requirements.txt`.

## Dry run — testnet first

`SandboxExecutionClientConfig` makes paper mode 100% simulated money on
*either* network — `network` only controls which market data the data
client pulls from. Testnet proves the plumbing works without real BTC price
action driving the strategy yet. No prior testnet usage exists anywhere in
this codebase, so treat this as unverified territory.

- [ ] Make a scratch copy, e.g. `config.testnet.toml`, with
      `network = "testnet"` (everything else the same).
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
      (`network = "mainnet"`) for the checks below.

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
`TradingNode`/`DataEngine` (AD-8 exempts it from the `troll/CLAUDE.md`
`TradingNode`/`DataEngine` ban — it does not fix the underlying bug). The
documented unbounded-queue-growth + shutdown-wedge bug that OOM-crashed the
earlier collector is still present in the engine here.

- [ ] Run for several hours minimum before trusting it unattended. Watch
      container memory (`docker stats`) — unbounded growth under
      quote/book-delta volume is that same failure mode resurfacing.
- [ ] Confirm a clean `docker compose stop live-paper` actually exits (not
      wedged) — the shutdown half of the same bug.
