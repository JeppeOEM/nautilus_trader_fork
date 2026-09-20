# Epic 22 Context: Multi-Exchange Trading, Paper Trading and Collection on One Collector Core

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Make dYdX, Bybit (linear perps *and* spot) and Hyperliquid perps first-class across collection, paper trading and real trading by folding three sibling collectors into one shared core (`troll/collector_core/`) and parameterising `live_paper` by venue, so adding a fourth exchange is a thin client + config + entrypoint + one venue-registry line. On top of that consolidation the epic raises the correctness bar for the data itself: because the 1 s snapshot is today the only surviving record of trades, it adds a raw trade archive carrying both clocks, one exact integer fold shared by the live loop and a nightly rebuild, exchange-time bucketing, nightly reconciliation against each venue's own klines, gap closure at the source, and nightly catalog consolidation. The result is a multi-venue archive whose bars are either proven equal to the exchange's numbers or loudly flagged, plus rankings, screener and trading surfaces where venue and perp/spot are explicit, filterable facts.

## Stories

- Story 22.1: `troll/collector_core/` extracted from the Bybit and Hyperliquid collectors
- Story 22.2: dYdX collector onto the core
- Story 22.3: Shared data types and a single `OpenInterest`
- Story 22.4: Bybit spot collection and explicit perp/spot everywhere
- Story 22.5: Order book validation per venue
- Story 22.6: `live_paper` multi-venue paper trading (Sandbox)
- Story 22.7: Exchange demo/testnet and real money for Bybit and Hyperliquid
- Story 22.8: Spine, rules and docs updated for the multi-venue core
- Story 22.9 (optional): Historical bar backfill for Bybit and Hyperliquid
- Story 22.10: Rankings show every collected coin across venues, with an exchange filter
- Story 22.11: Nightly catalog consolidation for every venue
- Story 22.12: Exchange-time bucketing for trades and the Bybit/Hyperliquid book
- Story 22.13: Raw trade archive, exact fold, nightly rebuild and kline reconciliation
- Story 22.14: Trade gap closure — REST backfill after reconnect and dual-feed arbitration

## Requirements & Constraints

- **One write gate for every venue.** All three collectors share the same ingest → validate → sample → publish/write path, with the same crossed-book, empty-book, staleness and precision gates, trade stale-age filter and bounded id dedup, lag canary and watchdog. A venue may override venue-specific book behaviour, never bypass the gate. Cadence is exactly 1.0 s everywhere, and snapshot class names stay unchanged so existing catalog directories remain valid.
- **Correctness over recovery.** A forced resync is a documented last resort, never the fix. Mismatches and gaps are root-caused and registered in the integrity audit — never absorbed by a tolerance, filtered, or silently dropped. Deliberate simplifications are in-code `Known limit:` comments naming the ceiling and the upgrade path.
- **Every venue needs an independent source of truth.** The live book is cross-checked against the venue's own REST snapshot; each trade channel is checked for subscribe-time replay before its rows count as live; "quiet feed" must be distinguishable from "dead or post-reconnect stale" on evidence, not assumption.
- **Exactness for aggregates.** Volume accumulates as integer raw quantities (never float sums); the rebuild buckets trades by exchange time into half-open one-second windows; a rebuilt day's 1 m bars must equal the venue's klines exactly (integer volume, raw-unit OHLC) wherever the trade archive is complete.
- **Provisional live, authoritative rebuild.** The live loop stays arrival-timed and instant; correctness comes from the nightly rebuild, not a live hold-back. Any hold-back knob defaults off and must never make a healthy feed look stale.
- **Retention is earned.** Raw trades are kept until their day is verified against klines, then released; unverified or failed days are retained and reported.
- **Archive hygiene.** Closed UTC days consolidate to one file per (data type, instrument), row counts verified before sources are deleted, today untouched, interrupted runs self-healing, and measured runtime/RSS/file counts recorded within the collector's memory headroom.
- **Venue is a fact, not a default filter.** Rankings list every collected instrument from every venue by default; a missing coin means "not collected / stale", never "hidden by venue". Every venue's rows carry USD 24 h volume from that venue's own source; unavailable volume excludes a row loudly, never ranks it at zero.
- **Trading safety.** Sandbox paper trading on live mainnet data is primary. Exchange demo/testnet and real money are reachable only via a separate config file, separate loader and explicit mode; a mode/environment mismatch fails closed, and credentials come only from environment variables.
- **Tests and verification.** Financial and catalog-touching paths need tests against real Nautilus objects. Migrated tests move with the code and pass with import-path updates only — no behavioural rewrites. Collector changes are live-verified on the VPS at 1 s spacing, and the fork stays untouched.

## Technical Decisions

- **Core shape:** one concrete collector class plus a run-forever entrypoint, taking a duck-typed venue client (fetch instruments, connect/disconnect, subscribe/unsubscribe, optional global subscribe, optional resync) and a tuple of extra periodic loops for venue-specific jobs. No registries or interfaces; dYdX is the only subclass, overriding exactly its two book-specific methods.
- **Venue book behaviour differs and is named:** dYdX keeps per-level message-id tagging and Indexer-style uncrossing (crossed books there are architectural); Bybit is the only venue where silent desync is possible, so it owns an update-id monotonicity/gap canary plus resync; Hyperliquid's messages are full authoritative 20-level books needing no delta bookkeeping.
- **Precision:** Bybit and Hyperliquid have instrument-constant precision and need no re-stamping; only dYdX re-stamps, always via exact integer arithmetic rather than the buggy price constructor.
- **Shared types move, names don't change.** Snapshot/integrity modules and venue-neutral operator scripts move into the core with class names intact; the three identical open-interest classes collapse into one, with an idempotent catalog migration that rewrites Arrow type metadata and no-ops on re-run.
- **Perp/spot is explicit end-to-end:** a pure market-kind function derived from Nautilus id suffixes, surfaced next to `venue` in every API response that carries it, in ranking entries, the frontend schema, a screener column/filter and a chart badge. Bybit runs one public WebSocket per product type; spot has no ticker, funding or open interest by construction.
- **Redis contract:** all collectors publish to the existing raw-snapshot channel with entries disjoint by instrument id; "one producer per channel" becomes "one producer per (channel, venue)", with no consumer change. Collector status/control stay dYdX-only for now.
- **`live_paper` venue table:** a plain dict mapping venue → (data config class, data factory, exec config class, exec factory, allowed environments, paper quote currency). One node per process, one data client and one exec client *per venue in use* (Nautilus rejects two exec clients for one venue), per-venue balances and environment, bot identity pinned explicitly rather than auto-assigned.
- **Data pipeline additions:** raw trades appended to the flush buffer and written through the catalog's own API as standard trade ticks; one pure fold function shared by live and rebuild; a day-scoped rebuild that rewrites files temp-then-rename, touches only trade-derived columns, is idempotent and reports how many seconds changed; kline comparison recording per-day verification status and every mismatch; one nightly pipeline (rebuild → consolidate → build candles → compare → prune) where each step halts on failure.
- **Backfilled venue bars** land in Parquet as external bar types only — never in the derived candle store or on the chart — serving backtests and reconciliation references.
- Venue endpoints the Python bindings don't expose are called with plain stdlib HTTP, following the existing open-interest poll pattern; each venue's reconnect-gap recoverability is stated as a capability fact verified against the live API.

## UX & Interaction Patterns

- Rankings default to all venues visible, with a venue chip row (all selected by default, per-viewer persistence) composing with existing filter conditions and sort; a newly appearing venue shows up selected, not hidden.
- Perp vs spot must be visually unambiguous wherever an instrument appears, so a spot row is never mistaken for its linear-perp namesake.
- The ranking page is one feature with two renderers (web and TUI): new columns, filters and ranking changes land in both from the same shared source, and longer venue-qualified ids must truncate to fit their TUI column rather than breaking alignment.

## Cross-Story Dependencies

- 22.1 is foundational — the core class, config and guards underpin 22.2–22.5 and 22.11–22.14. It is built from the two newer collectors first and live-verified before 22.2 migrates dYdX (the highest-risk step).
- 22.3 touches every importer across API, signal, ranking and collector packages; its catalog migration must run before readers expect the merged directory.
- 22.4 depends on 22.1's client contract and supplies the market field 22.10's filtering builds on. 22.5 depends on the core's extra-loop hook and on clients exposing (or explicitly declining) resync.
- 22.6 must land before 22.7: demo/testnet and real money extend the venue table and config-loading path multi-venue Sandbox trading establishes.
- 22.8 is documentation-of-record written against whatever 22.1–22.7 shipped — it amends the spine's single-write-gate, module-boundary and one-node/many-bots decisions and widens the working-rules scope from the dYdX collector to the core plus every venue collector.
- Candle-accuracy order is 22.13 → 22.14 → 22.12: archive and exact fold first, gap closure next, exchange-time bucketing last because it depends on the rebuild and kline comparison existing. 22.9 is optional but gives that reconciliation a stored kline reference instead of a live venue call.
- 22.10 depends on all three collectors publishing to the shared live channel and on per-venue USD volume sources; 22.11 runs against the shared catalog and must stay compatible with its readers.
