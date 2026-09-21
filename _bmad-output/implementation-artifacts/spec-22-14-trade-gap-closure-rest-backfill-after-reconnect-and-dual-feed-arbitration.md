---
title: 'Story 22.14: Trade gap closure: REST backfill after reconnect and dual-feed arbitration'
type: 'feature'
created: '2026-09-21'
status: 'awaiting-operator'
baseline_revision: 'bc3221b85c1ec8a98f7c2a06d532e43b3fd01ca3'
final_revision: 'e47ae52b689cbf3408a77736121268d2149e85df'
review_loop_iteration: 0
followup_review_recommended: true
context:
  - '{project-root}/_bmad-output/implementation-artifacts/22-14-trade-gap-closure-rest-backfill-after-reconnect-and-dual-feed-arbitration.md'
  - '{project-root}/troll/CLAUDE.md'
warnings: [oversized]
operator_actions:
  - "Before changing anything on nifelheim, record each venue's current compare_klines pass rate (instruments and minutes, trade_feeds = 1) from the latest `make nightly VENUE=...` run as the 'before' figure in troll/docs/DATA_INTEGRITY_AUDIT.md D-47 (Bybit, dYdX) and D-48 (Hyperliquid)."
  - "Deploy this branch on nifelheim (`make redeploy-all` plus `docker compose up -d --build bybit_collector hyperliquid_collector`), then set `trade_feeds = 2` in troll/bybit_collector/config.toml and troll/hyperliquid_collector/config.toml and restart those two collectors."
  - "After 24 h, for each venue copy the last 'Trade feed arbitration (cumulative)' log line (per feed: first copies, only-this-feed, both) and the count and content of the `collector.trade_backfill` ledger entries into DATA_INTEGRITY_AUDIT.md D-47/D-48, and record the compare_klines pass rate 'after' for the same venues (troll/docs/DEPLOY_CHECKLIST.md §3)."
  - "One week later, re-record each venue's compare_klines pass rate in the audit and name the cause of every remaining `reconcile.kline_mismatch` (unrecoverable reconnect gap from the backfill entry, stale-gate orphan seconds, or a new finding row). Never add a tolerance."
---

<intent-contract>

## Intent

**Problem:** The Rust WS clients reconnect and resubscribe silently, so trades executed while a socket was down are missing from the raw trade archive (22.13), the rebuilt seconds and every bar, and nothing reports how many (D-47). Hyperliquid cannot be backfilled deeply at all (D-48).

**Approach:** The core tags every message with the WS connection ("feed") it came on, detects a reconnect per feed on evidence, and after a short settle fetches each affected instrument's recent trades over stdlib REST (`collector_core/trade_backfill.py`), archiving only unseen ones (not folded live). An optional second, trades-only connection per feed group (`trade_feeds = 2`, Bybit + Hyperliquid) is unioned through the existing bounded `trade_id` dedup. Per-feed liveness drives a one-sided-outage alert. The 24 h / 1-week VPS measurements are operator actions, so the story ends `awaiting-operator`.

## Boundaries & Constraints

**Always:**
- Reconnect evidence, per feed, is any of three signals:
  - the client's `feed_states()` shows the feed go inactive → active (Bybit and HL `is_active()`; dYdX's `is_connected()` is true during Reconnect, so it cannot be used);
  - a book-carrying feed is silent for longer than `feed_stale_seconds or stale_book_seconds` and then delivers a message;
  - the same feed delivers a `trade_id` it already delivered (a subscribe replay), after the 60 s startup grace.
- One backfill per feed is pending at a time. Detections coalesce into it, so one reconnect gets one backfill and one `collector.trade_backfill` ledger entry. The backfill runs 3 s after the first detection.
- Backfill window per instrument: `[last_trade_ts - 5 s, fetch time]`. `last_trade_ts` is the max `ts_event` of the instrument's archived trades, live or backfilled.
- Venue values are converted exactly from their decimal strings at the instrument's precisions (`Price.from_str`/`Quantity.from_str` of a string checked to be exact). A value that is not representable is an error, never rounded.
- A backfilled trade gets `ts_init` = fetch time and is registered in the dedup map. It goes to `_buffer` only, never to `_second_trades`.
- `ts_init - ts_event ≤ ARRIVAL_MARGIN_NS` (300 s) stays an archive invariant: the 22.13 rebuild and prune depend on it. An unseen trade older than that is refused and counted.
- Dedup stays one bounded map per instrument, `seen_trade_ids` (2000) deep: `trade_id` → the feed of the first copy (MEM-02).
- A duplicate from the same feed counts as `duplicate` (a replay). A duplicate from another feed, including the REST source `rest`, counts as `duplicate_feed`.
- Feed-level staleness (`_last_feed_message_ns`) is updated only by book-carrying feeds.
- REST-polled data never goes through `_on_data`.
- Real Nautilus objects in tests.
- Recorded real venue JSON is committed as fixtures.

**Block If:** detecting a reconnect or opening a second connection needs a change to `nautilus_trader/` or `crates/`.

**Never:**
- modify `nautilus_trader/`, `crates/`, `data_api`, `frontend` or `ranking_engine`;
- fold backfilled trades into the live second;
- round or float-convert a venue value;
- add a tolerance anywhere;
- page dYdX further back than `min(since, fetch time - margin)`, or beyond 20 pages;
- ledger a backfill that had no detection behind it;
- open a second dYdX connection (the AC makes it optional; dYdX's 32-subscriptions-per-connection cap and 2/s subscribe throttle make it a separate investigation).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Flip | `feed_states()` shows `linear`: True → False → True | one backfill for `linear`'s instruments, 3 s later | none |
| Silence | book feed silent 7 s (threshold 5 s), then a message | backfill scheduled, reason `feed silent 7.0s` | none |
| Replay | same feed re-delivers an archived id after the grace period | `duplicate`+1; backfill scheduled | none |
| Coalesce | flip, then silence within the 3 s settle | one backfill, one ledger entry naming both reasons | none |
| No baseline | instrument has no archived trade | skipped and counted `no baseline` (rebuild coverage also starts at the first trade) | none |
| Already archived | REST id is in the dedup map | counted `already archived`, not buffered | none |
| New | REST id unseen, within margin | buffered with venue `ts_event`, `ts_init` = fetch time; `trade_backfill[iid]`+1; `last_trade_ts` advanced | none |
| Too old | unseen, `ts_init - ts_event` > 300 s | not buffered, counted `refused` | in the ledger entry |
| Shallow venue | Bybit/HL page full and oldest trade > `last_trade_ts` | `unrecoverable {iid: seconds}` = oldest − last_trade_ts | in the ledger entry |
| dYdX paging | pages of 1000, newest first | page backwards with `createdBeforeOrAt` = oldest `createdAt`; stop at a short page, at `since`, at the margin floor, or at 20 pages (then unrecoverable) | none |
| Bad value | price not exact at `price_precision` | trade skipped, instrument listed under `errors` | in the ledger entry |
| Fetch failure | HTTP / URL / JSON / Bybit `retCode` error for one instrument | listed under `errors`, the other instruments continue | in the ledger entry |
| Dual feed | same id on `linear` then `linear-trades` | first archived; second → `duplicate_feed`, overlap `(linear, linear-trades)`+1 | none |
| One-sided | a feed's last trade more than 30 s behind a sibling's in its group | OBS-01 `_notify`; reminder after 10 min; `recovered` on catch-up | none |
| Config | `trade_feeds = 3` | `ValueError` at load | fail closed |

</intent-contract>

## Code Map

- `troll/collector_core/collector.py`:
  - client contract docstring (:32-50);
  - `_on_data`/`_ingest_loop`/`_process_data`/`_is_duplicate_trade` (:498-555);
  - `_report_stale_trades` (:557);
  - `_stale_reason` (`_last_feed_message_ns`, :782);
  - `_watchdog_transition` (:207) / `_watchdog_loop` (:910);
  - `run()` (:938): instruments converted via `instruments_from_pyo3` and dropped, and `loops`.
- `troll/collector_core/config.py`: `CoreConfig` and `core_config_from_dict`. dYdX's `load_config` does not use it, so dYdX keeps the default `trade_feeds = 1`.
- `troll/collector_core/compare_klines.py`:
  - `_BYBIT_URLS`, `_HYPERLIQUID_URLS`, `_DYDX_NETWORKS`, `_USER_AGENT`, `http_json`, `_bybit_category`, `HttpJson`: move them to a shared module;
  - `units`: the exact-conversion precedent.
- `troll/collector_core/archive_gaps.py`: the `ARRIVAL_MARGIN_NS` comment says only the live age filter bounds the arrival lag.
- `troll/bybit_collector/client.py`: `_ws_linear` and `_ws_spot`, both `new_public`; `_handle_message`.
- `troll/bybit_collector/collector.py`: builds the client.
- `troll/hyperliquid_collector/client.py`: one `HyperliquidWebSocketClient`; `connect(loop, instruments, cb)` then `wait_until_active`.
- `troll/hyperliquid_collector/collector.py`: builds the client.
- `troll/dydx_collector/collector.py`:
  - `_open_interest_loop` sends REST data through `_on_data`, which would fake feed liveness;
  - `_publish_status` builds the per-instrument `collector:status` payload. `collector:status` is dYdX-only.
- `troll/collector_core/tests/test_collector.py`: helpers `_collector`, `_trade`, `_deltas`, `_tick`, which call `_process_data` directly.
- `troll/collector_core/tests/test_watchdog.py`: the `_watchdog_transition` message asserts.
- Wire formats, verified live 2026-09-21:
  - **dYdX** `GET {indexer}/v4/trades/perpetualMarket/{ticker}?limit=1000&createdBeforeOrAt=<iso>` returns `{"trades":[{id, side BUY|SELL, size, price, createdAt ISO ms}]}`, newest first; `limit=1000` works. The WS parser uses the same `id` and `createdAt`.
  - **Bybit** `GET /v5/market/recent-trade?category=linear|spot&symbol=&limit=1000` returns `result.list[{execId, price, size, side Buy|Sell, time ms}]`, newest first. Linear returned 1000 trades (about 64 s of BTCUSDT); **spot returns at most 60**. The WS id is `i`: equality with `execId` must be wire-verified.
  - **Hyperliquid** `POST /info {"type":"recentTrades","coin"}` returns `[{coin, side B|A, px, sz, time ms, tid}]`, newest first, **exactly the last 10 trades**. `startTime` is ignored. The WS id is `tid`.

## Tasks & Acceptance

**Execution:**
- [x] `troll/collector_core/feed.py` (NEW):
  - frozen `Feed(name: str, group: str, trades_only: bool = False)`;
  - `MAIN_FEED = Feed("main", "main")`;
  - `REST_FEED_NAME = "rest"`.
- [x] `troll/collector_core/venue_http.py` (NEW):
  - public `BYBIT_URLS`, `HYPERLIQUID_URLS`, `DYDX_NETWORKS`, `USER_AGENT`, `HttpJson`, `http_json`, `bybit_category(iid)`, moved out of `compare_klines`;
  - `bybit_category` raises `ValueError`, and `compare_klines` wraps it into `KlineError`;
  - `compare_klines` imports these, and its tests stay green.
- [x] `troll/collector_core/trade_backfill.py` (NEW):
  - `BackfillError`;
  - `exact_text(text, precision)`: the decimal string at exactly `precision` places, or `BackfillError`;
  - `parse_dydx_trades` / `parse_bybit_trades` / `parse_hyperliquid_trades(payload, instrument, ts_init) -> list[TradeTick]`, newest first as the venue sends them. Aggressor comes from the taker side (`BUY`/`Buy`/`B` → BUYER). `ts_event` comes from venue time with exact integer ns; the dYdX ISO parse uses no float;
  - `fetch_trades(instrument, since_ns, floor_ns, environment, ts_init, http=http_json) -> Fetched(trades oldest-first with ts_event ≥ since_ns, reached_since: bool, oldest_ns: int | None)`;
  - the venue comes from the id suffix, the symbol from `raw_symbol`, and the Bybit category from `bybit_category`;
  - `reached_since` is true when the oldest returned trade is ≤ `since_ns` or the page is not full;
  - dYdX pages as in the matrix.
- [x] `troll/collector_core/config.py`: `trade_feeds: int = 1`, parsed; anything other than 1 or 2 raises `ValueError`.
- [x] `troll/collector_core/collector.py`:
  - **Contract:** `on_data(data, feed=MAIN_FEED)`. The queue carries `(data, feed)` and `_process_data(data, feed=MAIN_FEED)`. The optional client method `feed_states() -> dict[Feed, bool]`.
  - **Per-feed state:** `_feeds`, `_feed_last_ns`, `_feed_last_trade_ns`, `_feed_instruments` (only from `TradeTick`/`OrderBookDeltas`), `_last_trade_ts`, and `_instruments` (the Cython instruments kept by `run()`).
  - **Dedup map:** returns the first-copy feed.
  - **Detection:** the three signals, feeding `_schedule_backfill(feed_name, now_ns, reason)` (coalescing).
  - **Loops:** `_feed_state_loop` polls every 0.1 s, and only runs when the client has `feed_states`; it outpaces the 250 ms minimum Rust reconnect delay. `_trade_backfill_loop` runs due requests: `asyncio.to_thread(fetch_trades)` per instrument, sequentially, then `_apply_backfill` and one ledger entry per request (`collector.trade_backfill`: feed, reasons, instruments, backfilled, already archived, refused, unrecoverable, no baseline, errors).
  - **Counters:** cumulative `_trade_backfill_counts`, `_duplicate_feed_dropped`, `_feed_first[feed]` and `_feed_overlap[(first, second)]`, reported each flush. When more than one feed delivered trades, the report adds a cumulative line: only-A, only-B, both.
  - **Watchdog:** `_watchdog_loop` adds the per-group one-sided check. A generic alert transition backs both checks and keeps `_watchdog_transition`'s messages.
  - **Docstrings:** state the `Known limit:` notes below.
- [x] `troll/bybit_collector/client.py`:
  - `trade_feeds` param. With 2, add `_ws_linear_trades` and `_ws_spot_trades`: cache instruments, connect, `subscribe_trades`/`unsubscribe_trades` only, and disconnect them;
  - messages tagged `Feed("linear","linear")`, `Feed("spot","spot")`, `Feed("linear-trades","linear",True)`, `Feed("spot-trades","spot",True)`;
  - `feed_states()`.
- [x] `troll/bybit_collector/collector.py`: pass `config.trade_feeds`.
- [x] `troll/hyperliquid_collector/client.py`:
  - `trade_feeds` param, adding a second `HyperliquidWebSocketClient` for trades only;
  - tagged `MAIN_FEED` and `Feed("main-trades","main",True)`;
  - `feed_states()`.
- [x] `troll/hyperliquid_collector/collector.py`: pass `config.trade_feeds`.
- [x] `troll/hyperliquid_collector/client.py` `_at_exact_millis` (added during wire verification): each live trade's `ts_event` is re-stamped to the venue's exact millisecond in integer arithmetic. The adapter's `millis_to_nanos(millis as f64)` puts it up to 128 ns off, and REST copies are exact (audit D-62).
- [x] `troll/dydx_collector/collector.py`:
  - `_open_interest_loop` buffers directly, like Bybit;
  - the `_publish_status` payload adds `"trade_backfill": n`.
- [x] `troll/collector_core/archive_gaps.py`: the comment says the backfill cap bounds the arrival lag too.
- [x] `troll/bybit_collector/config.toml`, `troll/hyperliquid_collector/config.toml`: an explicit `trade_feeds = 1` with a comment. The operator flips it to 2 for the measurement.
- [x] Tests:
  - NEW `collector_core/tests/test_trade_backfill.py`: parsers on committed real fixtures (`tests/fixtures/{dydx,bybit_linear,bybit_spot,hyperliquid}_trades_*.json`), exactness, paging and stop rules through a fake `http`, `reached_since`;
  - extended `test_collector.py`: every matrix row (detections, coalescing, the apply rules, dedup categories, feed-level staleness ignoring trades-only feeds, one-sided transitions);
  - `test_config.py`, `test_watchdog.py`, `test_compare_klines.py` (still green);
  - Bybit/HL client tests: feed tags and subscription routing with `trade_feeds=2`, using a stub WS.
- [x] Docs:
  - `troll/docs/DATA_DICTIONARY.md`: venue capability table (venue, endpoint, depth, what a reconnect gap costs), with `Known limit:` and the upgrade path = dual feed;
  - `troll/docs/DATA_INTEGRITY_AUDIT.md`: D-47 and D-48 → written (evidence and measurement owed); a new row for Bybit spot's 60-trade depth;
  - `troll/docs/DEPLOY_CHECKLIST.md`: the measurement procedure;
  - `troll/CLAUDE.md` DATA-06: reconnect backfill and `duplicate_feed`.

**Acceptance Criteria:**
- Given the collector image, when `pytest collector_core/tests dydx_collector/tests bybit_collector/tests hyperliquid_collector/tests ml_signals/tests` runs, then the new and changed tests pass and the only failures are the 22.13 baseline set.
- Given the Bybit and Hyperliquid clients run briefly against mainnet, when their WS trade ids are compared with a REST fetch of the same interval, then the ids match. This is wire evidence that dedup works across sources, and it is recorded in Completion Notes.
- Given a Bybit and a dYdX collector running locally into a scratch catalog, when their network is cut for 30 s and restored, then a `collector.trade_backfill` entry appears. The archived trade ids for the outage window must then equal the venue REST ids for that window, or the difference is reported as unrecoverable. If this cannot be run locally, it becomes an operator action.
- Given the 24 h and 1-week measurements (AC 5) need the VPS, when the story ends, then the spec is `awaiting-operator` with those steps under `operator_actions`.

## Spec Change Log

## Review Triage Log

### 2026-09-21 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 14 (high 2, medium 5, low 7)
- defer: 0
- reject: 6 (low 6)
- addressed_findings:
  - `[high]` `[patch]` **An unexpected exception lost the whole backfill and its ledger entry.** For example, a non-object Bybit body raised `AttributeError`. Now:
    - each instrument catches everything into `errors`;
    - the summary entry is written in a `finally`, marked `interrupted after N instruments` on cancellation;
    - the Bybit and dYdX payload shapes are checked.
  - `[high]` `[patch]` **dYdX's replay signal almost never fired.** Replayed trades older than 10 s were dropped by the age filter before the dedup check. The same-feed id check now runs first, so a replay of any age is reconnect evidence.
  - `[medium]` `[patch]` **Silence was judged on processing time.** A queue backlog looked like a dead socket. Feed liveness now uses arrival (`ts_init` at receipt).
  - `[medium]` `[patch]` **A live copy processed after its REST copy never reached the live fold.** It is now folded live, still never archived twice.
  - `[medium]` `[patch]` **`unrecoverable` was under-reported when dYdX stopped at the 300 s floor.** It now reaches through the newest refused trade.
  - `[medium]` `[patch]` **The optional second socket was a new single point of failure, and a failed connect leaked sockets:**
    - trades-only connect/subscribe failures are ledgered (`collector.trade_feed`) and the socket is dropped (`feed.optional_feed_step`);
    - the core closes what did connect before re-raising;
    - `disconnect` closes every socket even when one close fails.
  - `[medium]` `[patch]` **At shutdown, a pending or in-flight backfill disappeared silently.** Pending requests are now ledgered `abandoned at shutdown`. The cancelled loops are awaited before the final flush, so an interrupted backfill ledgers itself and its buffered trades are written.
  - `[low]` `[patch]` Overlap was double-counted when a secondary feed replayed an id. The dedup map now keeps the list of feeds per id, so a repeat on any feed is one replay.
  - `[low]` `[patch]` A malformed row time failed the whole instrument. It is now rejected alone.
  - `[low]` `[patch]` A missing instrument definition was counted as `no baseline`. It is now an error.
  - `[low]` `[patch]` An inactive state seen on the first poll now also captures the baseline.
  - `[low]` `[patch]` `trade_feeds` is type-checked strictly: `2.5` and `true` used to be truncated through `int()` and pass.
  - `[low]` `[patch]` The one-sided alert wording now says "behind in trade arrivals".
  - `[low]` `[patch]` Docs:
    - the WS id == REST id wire evidence (the reviewer believed it still unproven);
    - dYdX ignores `trade_feeds` (troll/CLAUDE.md DATA-01);
    - `Known limit:` notes for sequential fetches and dYdX blocks of more than 1000 trades on one `createdAt`.
- rejected:
  - the ERROR-level ledger entry per backfill (AC 2 mandates one `collector.trade_backfill` entry per reconnect; the `NoneType` traceback line is the ledger's pre-existing behaviour);
  - re-archiving ids evicted from the 2000-id window (needs more than 2000 trades within the settle; the rebuild dedups by `trade_id`);
  - a missed flip on a trades-only feed (its trades are also on the primary);
  - `ts_init` including scheduler lag (by design: the archive stamp);
  - the restart gap (documented `Known limit:`, D-61);
  - a "zero already archived" canary (made moot by the wire proof).

## Design Notes

**Why detection is evidence-based rather than a hook:** none of the pyo3 clients passes `Reconnected` to Python. Bybit's `websocket.rs:426` and dYdX's `:803` only log it; HL's `client.rs:331` consumes it. The flip signal catches Bybit and HL. For dYdX, the replayed-id signal is the reliable one: dYdX replays recent trades on every trades subscribe, so after a short outage the replay overlaps the archive. The silence signal is the net for long outages on every venue.

**Why the 300 s cap:** `rebuild_seconds` queries trades by `ts_init` with an `ARRIVAL_MARGIN_NS` margin, and prune's previous-day check uses the same margin. A backfilled trade older than that would be invisible to the rebuild, or deletable. `Known limit:` a dYdX outage longer than 5 minutes stays partly unrecovered, and says so. The upgrade path is a backfill-span marker (like `archive_gaps`) that the rebuild and prune read in order to widen their window.

**Where the counts go:**
- `collector:status` exists for dYdX only (epic decision), so only its payload carries `trade_backfill`.
- Bybit and HL report the counts in the per-flush log line and the ledger. D-30 already tracks publishing collector counts.

**The pre-gap baseline is captured at detection (found by the network-cut test):**
- `_BackfillRequest.since` holds, per instrument, the newest archived `ts_event` before the gap. It is taken at the earliest evidence and only ever lowered:
  - the silence signal fires before the resuming message is processed;
  - a flip uses the snapshot from when the feed was first seen inactive;
  - a replay lowers it to the replayed trade's own `ts_event`.
- Reading `_last_trade_ts` when the backfill ran, 3 s later, is the bug the first Bybit run hit: trades after the resume had already advanced the baseline, so ADAUSDT fetched none of its 368 missed trades.
- This is how AC 1's `last_trade_ts` is read: the last trade archived before the gap.

**Other `Known limit:` notes:**
- A crash or restart gap is not backfilled, because `last_trade_ts` is in memory. Upgrade path: seed it from the newest archived trade at startup.
- Seconds skipped by the stale-book gate during an outage have no snapshot row. Their backfilled trades are then rebuild orphans, and those minutes can still mismatch the venue klines; that is the named cause for AC 5.
- The one-sided alert compares trade arrival within a group only. A group where both feeds are silent is the book watchdog's case.

## Verification

**Commands:**
- `docker run --rm -v "$PWD":/app -w /app -e HOME=/tmp -e USER=collector troll-collector:latest python3 -m pytest collector_core/tests dydx_collector/tests bybit_collector/tests hyperliquid_collector/tests ml_signals/tests -q` (from `troll/`). Expected: only the baseline failures.
- `ruff check`, `ruff format --check` and `mypy --disallow-incomplete-defs` on the new and changed files. Expected: no new findings.

## Auto Run Result

Status: awaiting-operator

**Summary:** Trades missed while a WebSocket was down are now recovered from the venue, and the rest of the gap can be closed with a second independent feed. How it works:
- Every message is tagged with its connection (`Feed`).
- A reconnect is detected per feed from any of three signals:
  - `feed_states()` inactive → active (Bybit and Hyperliquid);
  - book-feed silence, judged on arrival time;
  - a same-feed trade-id replay, of any age.
- Detections are coalesced into one backfill per feed, run 3 s later from a pre-gap baseline captured at detection.
- `collector_core/trade_backfill.py` fetches each instrument's trades over stdlib REST, with exact decimal-string conversion.
- Only unseen ids are archived: never folded live, never older than the 300 s arrival margin. One `collector.trade_backfill` ledger entry per reconnect says what was recovered, already archived, refused, unrecoverable or failed.
- With `trade_feeds = 2` (Bybit and Hyperliquid), a trades-only twin socket is unioned through the one bounded dedup. Its failure never affects the primary. Arbitration is logged each flush, and an OBS-01 alert fires on a one-sided outage.
- The venue capability table is written: dYdX recoverable within 5 minutes, Bybit linear 1000 / spot 60 trades, Hyperliquid 10 trades.

Found and fixed along the way:
- **Hyperliquid's live trade clock:** the adapter's f64 ms→ns conversion (D-62). The client re-stamps each trade to the exact millisecond.
- **A backfill-window bug**, found by the live network-cut test.

**Files changed:**
- New in `troll/collector_core/`:
  - `feed.py`: the feed tag and the optional-feed step;
  - `trade_backfill.py`: the three venue fetchers and parsers;
  - `venue_http.py`: shared venue URLs, moved out of `compare_klines`.
- `troll/collector_core/collector.py`: per-feed liveness, the detection signals, the backfill loop and apply, arbitration, the one-sided alert, and connect/shutdown hardening.
- `troll/collector_core/config.py`: `trade_feeds`.
- `troll/collector_core/compare_klines.py`: imports the shared venue HTTP module.
- `troll/collector_core/archive_gaps.py`: margin comment.
- `troll/bybit_collector/client.py`, `troll/hyperliquid_collector/client.py`: feed tags, trades-only twin sockets, `feed_states`. The Hyperliquid client also re-stamps trades to the exact millisecond.
- The two venue `collector.py` files and `config.toml` files: `trade_feeds`.
- `troll/dydx_collector/collector.py`: the open-interest poll no longer goes through `_on_data`; `collector:status` carries `trade_backfill`.
- Tests:
  - new: `test_trade_backfill.py` plus 4 real venue fixtures, and the Bybit and Hyperliquid `test_client.py`;
  - extended: collector, config and watchdog tests, and `dydx test_collector_resilience`.
- Docs: `DATA_DICTIONARY.md` §1.1 capability table; `DATA_INTEGRITY_AUDIT.md` (D-47 and D-48 written, D-60, D-61 and D-62 new); `DEPLOY_CHECKLIST.md` §3; `troll/CLAUDE.md` DATA-01 and DATA-06.

**Review:** 14 patches applied (2 high, 5 medium, 7 low), 0 deferred, 6 rejected. The review pass made broad, behaviour-level changes, so a follow-up review is recommended.

**Verification:**
- Collector image, all collector test directories plus `ml_signals`: 545 passed. The only failures are the untouched-tree baseline: 4 `test_ofi_strategy*` and 3 `ml_signals` collection errors.
- `ruff check` and `ruff format --check`: clean on every new or changed file. The one remaining finding (Bybit `collector.py` D401) was already there.
- `mypy --disallow-incomplete-defs` (1.20.2): no issues on the new and changed files.
- **Live wire check (mainnet):** WS id == REST id on every venue, with price, size and aggressor equal:

  | Venue | Ids matched |
  |---|---|
  | Bybit linear | 999/999 |
  | Bybit spot | 60/60 |
  | Hyperliquid | 19/19 |
  | dYdX | 3/3 |

  The dual feeds delivered identical id sets.
- **Local 30 s network cut:** the Rust reconnect made the real gap about 57 s.

  | Instrument | Result |
  |---|---|
  | dYdX BTC, ETH, SOL | archive == venue REST for the outage window |
  | Bybit ADAUSDT | 155/155 recovered |
  | Bybit BTCUSDT linear | 472 backfilled, 36 s reported `unrecoverable` (depth) |
  | Bybit ETHUSDT spot | 27 backfilled, 28 s reported `unrecoverable` (depth) |
  | Hyperliquid | 57-60 s reported `unrecoverable` (depth) |

**Residual risks:**
- No VPS measurement yet: the 24 h run with `trade_feeds = 2` and the 1-week re-record are operator actions.
- The committed configs ship `trade_feeds = 1`.
- A whole-network outage defeats the dual feed; only REST depth helps there. Bybit spot and Hyperliquid stay mostly unrecoverable for anything but short gaps.
- The restart gap is not backfilled (D-61).
- Seconds the stale gate skipped during an outage have no snapshot row, so their backfilled trades become rebuild orphans and those minutes can still fail `compare_klines`.
- The Docker bridge on this dev box stalled TLS handshakes. Unrelated to the code: an MTU-1400 network was used for the test.
