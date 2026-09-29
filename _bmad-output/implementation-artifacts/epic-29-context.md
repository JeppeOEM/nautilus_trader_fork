# Epic 29 Context: Rankings web-only across exchanges, the TUI as a control surface, and the venue cutover from dYdX to Bybit + Hyperliquid

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

The operator is moving collection off dYdX to Bybit `BTCUSDT`/`ETHUSDT` (linear + spot) and Hyperliquid `SOL`, and switches only once the new venues are proven end to end. Rankings already list every venue, but the same coin cannot be lined up across exchanges because the exchange sits only in the id suffix and there is no base-symbol field. This epic adds Exchange and Symbol columns to the web rankings. It turns the TUI into the one control surface for every venue: all plans are shown, Bybit and Hyperliquid get runtime control, and a market browser lets the operator add coins. It stages the dYdX shutdown behind a proven-first runbook and never deletes archived data. It also puts each bot's stop-loss, take-profit and position details on the Bots pane, and gives `DummyStrategy` optional bracket exits so those columns can be seen working.

## Stories

- Story 29.1: Exchange and Symbol on the web rankings, sortable and filterable
- Story 29.2: Collector pane shows every venue's plan
- Story 29.3: Venue cutover: Bybit `BTCUSDT`/`ETHUSDT` and Hyperliquid `SOL` proven, then dYdX stopped
- Story 29.4: Runtime collection control for Bybit and Hyperliquid
- Story 29.5: Market browser in the Collector pane: search a venue's coins by name and add them
- Story 29.6: Bots pane shows each bot's live take-profit, stop-loss and position details

## Requirements & Constraints

- **Wire rule:** the Redis payloads (`rankings:live`, `collector:status`, `collector:control`, `bots:status`) only gain fields. Fields are never renamed or removed, and existing fields stay byte-identical. Replay tests against recorded pre-story messages prove it. A `collector:control` message without the new `venue` field means dYdX.
- **Derive, don't store (SIGNAL-01):** venue and base symbol are derived from the instrument id on read by one kernel helper (`kernel/venues.py`'s `base_symbol`). They are never stored twice. Any heuristic ceiling gets a `Known limit:` comment naming the upgrade path.
- **No stale or hidden data (DATA-01):** when a venue is stopped or an instrument is removed, its rows age out as stale and then disappear. They are never hidden or filtered out early, and a venue with no fresh rows publishes nothing.
- **Archive is never deleted by a cutover or a removal.** Only retention deletes catalog files. The nightly keeps verifying and pruning dYdX's days until they age out.
- **Price integrity:** price and quantity values sent to the TUI are strings built from `Price`/`Quantity` (`str(...)`) and never pass through `float`. Exit prices are computed in `Decimal` at the instrument's precision.
- **No coin caps for Bybit or Hyperliquid.** The operator decides the count from outside research: no cap, no capacity estimate. dYdX keeps its cap of 30 and its "cap reached" refusal.
- **Venue limits are measured, not inherited.** WebSocket subscribe/unsubscribe limits are measured live for each venue, recorded with date and method, and enforced by a named pacing constant per venue.
- **Docs, images and Makefile change in the same commit as the code (MR4).** `docs/DATA_DICTIONARY.md`, `docs/BOT_OPERATIONS.md`, `docs/DEPLOY_CHECKLIST.md` and `platform/README.md` are touched as stories require.
- **Operator steps are deferred, never parked.** Runbooks go into `docs/DEPLOY_CHECKLIST.md`'s "Deferred operator actions" section, and stories finalize `done`. A dev-run check that fails is a defect to fix inside the story.
- **TEST-04:** warnings are defects, not noise.

## Technical Decisions

- **Rankings:** `RankingBoard` in `ranking/` is the only thing that computes and publishes rankings, over `rankings:live`. `views/ranking_columns.py`'s `RANKING_COLS` is the single column-metadata list the web mirrors.
- **Collection plans:** each venue has one `CollectionPlan` aggregate in `collection_control/`. It owns instruments, excludes, pins and an optional `cap`, with the invariants `exclude ∩ collected = ∅` and `|collected| ≤ cap`. Commands `add`/`remove`/`pin`/`unpin`/`exclude`/`reload` produce a plan diff.
- **Plan vs applied:** the plan is the intent and the applied set is the fact. `CaptureService.apply(diff)` returns `Applied(subscribed, unsubscribed, failed)`. A failed instrument shows as `pending` on `collector:status` and gets one `collector.subscribe_failed` ledger entry per attempt.
- **Control and status services:** `ControlService` consumes `collector:control` and `StatusPublisher` publishes `collector:status`. Today only dYdX has a live plan; Bybit and Hyperliquid have static tuples applied once at startup, and Story 29.4 lifts that limit.
- **Plan files:** each venue's plan file has one loader. It is written back through `CollectionPlanStore` with its key set frozen, and the rewrite loses comments (a known limit). For Bybit and Hyperliquid, `instruments` stays a flat list.
- **Contexts and wiring:** bounded contexts communicate across boundaries only through the Redis published language or the kernel. Ports are `typing.Protocol`, and wiring is explicit in composition roots.
- **Frozen names:** compose service names (`bybit_collector`, `hyperliquid_collector`, `ranking_engine`), env vars and bind-mount paths are published language and are not renamed.
- **dYdX compose profile:** dYdX's compose service gets `profiles: ["dydx"]`, like `live-paper` and `bot_tui`, with explicit `make up-dydx`/`down-dydx` targets.
- **Bots context:** `bots/infrastructure/cache_reader.py`'s `StrategyCacheReader` is the context's only, strategy-scoped view of Nautilus. Protective orders are classified by the order's own properties (reduce-only flag, type, closing side), never by strategy class. Emulated orders count as protective.
- **Bracket exits:** use Nautilus built-ins (`order_factory.bracket`) for `DummyStrategy`'s exits. New config keys extend the frozen key sets as optional keys with `None` defaults, so existing `config.toml` files still parse.
- **New channel:** `markets:live` is published by `ranking` as `{venue, ts, markets: [{instrument_id, symbol}]}`. It carries names only: no volume or metrics.

## UX & Interaction Patterns

- **Web rankings:** `Symbol` and `Exchange` are pinned columns between Rank and Instrument. The exchange cell carries a market tag (`BYBIT · linear`). Both columns are sortable (symbol sort breaks ties by rank) and filterable with `=`. The existing venue chips stay unchanged.
- **TUI shape:** two panes, Bots and Collector. Rankings and the `m` mode toggle live on the web only (moved in Story 25.1a).
- **Collector pane:** one section per venue, headed `<VENUE>: N collected +P pending`.
  - Actions are disabled with the reason shown until a venue's plan accepts commands.
  - `/` searches `markets:live` (case-insensitive substring). Rows show the id plus a `collected` marker.
  - `a` adds the focused row behind the type-to-confirm guard. The row shows `pending` until the add is applied or fails.
  - A cold open shows "waiting for markets:live".
- **Bots pane:** new `entry`/`sl`/`tp` columns show each price with its signed % distance from mark.
  - An unprotected position shows `none` in the warning colour.
  - A flat bot shows blank exit cells, and a message from an older producer shows `n/a`.
  - A column header aligns to the row widths. Bot-detail gains the position and exit lines.
- **TUI-01:** keep the persistent `ListBox` and update its walker in place; never swap in a fresh walker.
- **TUI-02:** every fixed-width variable-length field is truncated with `fit()`, not just padded.

## Cross-Story Dependencies

- **Runs after Epic 25:** 29.1 needs the `RankingBoard` rank entry from 25.2, and 29.2 and 29.4 need 25.4's per-venue `CollectionPlan`. The epic is independent of Epics 26–28.
- **Order:** 29.1 → 29.2 → 29.3 → 29.4 → 29.5 → 29.6.
- **Within the epic:**
  - 29.3's runbook uses 29.1's Exchange filter.
  - 29.5 reuses 29.1's `base_symbol` and needs 29.4's control plane for Bybit and Hyperliquid adds.
  - 29.2's read-only Bybit and Hyperliquid sections become actionable once 29.4 lands.
- **29.6:** it is independent of the collector stories. A resting ATR stop from 27.8's `CandlePatternStrategy` shows in 29.6's columns without any change to 27.8.
