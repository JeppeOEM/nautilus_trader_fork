# Rubric Walker Review — ARCHITECTURE-SPINE.md

**Reviewer:** fresh, context-isolated (no prior conversation with the spine's author)
**Subject:** `architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md`
**Method:** read the spine in full, then independently verified every grounding citation, every named file, and the actual `docker-compose.yml`/`troll-requirements.txt` against the live repo (not against the `.memlog.md` narrative, which was consulted only for intent, not as a substitute for checking code).

## Verdict

The paradigm and the ten ADs are individually sound and mostly well-reasoned, but the spine fails the "ratifies the brownfield codebase" and "operational envelope" checklist items on verifiable, not stylistic, grounds: several `[ADOPTED] — confirmed` citations point at the wrong lines or assert an import relationship that doesn't exist in the code today, and the Deployment & Environments section describes a four-service deployment when the real `docker-compose.yml` already runs five (it omits the pre-existing `live-paper` service entirely) while giving zero operational story for the two brand-new components this update is nominally about, `ranking_engine` and `bot_tui`.

## Findings

### 1. Deployment & Environments section is stale and silent on the two new components (High)

The spine's Deployment & Environments section describes exactly four services — `collector`, `dashboard`, `redis:8-alpine`, `dozzle` — and the Structural Seed's `docker-compose.yml` comment says the same: "collector (rw), dashboard (catalog :ro), redis, dozzle."

The actual `troll/docker-compose.yml` has **five** services, including one this spine's own scope statement claims to cover:

```yaml
live-paper:
  container_name: dydx-live-paper
  profiles: ["live-paper"]        # opt-in only, per Story 3.1
  build:
    dockerfile: troll/live_paper.dockerfile
  ...
  restart: on-failure:5           # deliberately NOT "always" — see comment
```

This service, and the `troll/live_paper.dockerfile` it builds from, are never mentioned anywhere in the spine — not in the Structural Seed, not in Deployment & Environments, not in the Stack table. The scope line explicitly includes `live_paper` ("the derived-data layer built on top of it (ranking_engine, bot_tui, live_paper)"), and AD-10 is entirely about `live_paper`'s control-plane isolation, so this isn't out of scope — it's a real component with real operational decisions already made in code (opt-in profile, capped restart count, its own dockerfile) that the architecture document simply doesn't reflect.

Worse: `ranking_engine` and `bot_tui` — the two components this update round exists to introduce — have **no deployment story at all**. Nothing says whether `ranking_engine` gets its own Docker service (analogous to `live-paper`'s profile-gated pattern) or piggybacks on an existing container; nothing says whether `bot_tui`, an interactive `urwid` terminal app, runs inside Docker at all (an interactive TUI attached to a container is an unusual and worth-deciding pattern) or is meant to run on the host directly against the Dockerized Redis. This is exactly the gap the review brief asked to check for by name, and the answer is: yes, silently missing.

### 2. AD-1, AD-2, and AD-6's grounding citations for `collector.py` point at the wrong code (High)

Checked against the current `troll/dydx_collector/collector.py` (891 lines):

- **AD-1** cites `collector.py:296-319` as where `self._on_data(snapshot)` and the Redis-batch append happen "within the same synchronous per-instrument iteration." Lines 296-319 are inside `__init__` (constructing `_crossed_since_ns`, `_last_sequence`, `_resync_buffers`, etc.) — nothing to do with `_second_loop`. The actual code the citation is describing is at **lines 719-735** (`batch.append(snapshot)` / `self._on_data(snapshot)` / `await _publish_snapshot_batch(...)`).
- **AD-2** cites three ranges for its three named invariant checks: `collector.py:271` (non-empty top-of-book), `274-281` (crossed-book skip + warning), `288-294` (staleness skip + warning). All three are wrong — line 271 is `self._liquid: set[str] = set()` inside `__init__`, and 274-294 is likewise constructor bookkeeping (delta-store config, trade-volume accumulators). The real checks are at **lines 660-661** (empty top-of-book), **662-696** (crossed book), **699-710** (staleness).
- **AD-6** cites `collector.py:172,190,343` as "the only `write_data()` call sites in `troll/`." The actual (and still only production) call sites are **lines 549 and 792** — two sites, not three, and not at those line numbers.

The invariants these ADs describe do exist in the code roughly as described (verified independently by reading the real `_second_loop`), so the *substance* of AD-1/AD-2/AD-6 holds up — but the citations themselves, tagged `[ADOPTED] — confirmed`, are not confirmable at the lines given. This is the exact failure mode the "ratifies rather than contradicts" checklist item exists to catch: `collector.py` has clearly grown substantially (constructor alone now spans ~150 lines with sequence-gap resync, watchdog, ring-buffer housekeeping) since these citations were written, and nobody re-verified the line numbers before re-tagging them `[ADOPTED]` in this update round.

### 3. AD-4's import-grounding claim is false as of today's code (Medium-High)

AD-4 states: `"[ADOPTED]` — confirmed: `ml_signals/catalog_stats.py` and `chart_data.py` import only `DydxMinuteBar`; no reverse imports exist."

Checked directly:
```
troll/ml_signals/catalog_stats.py: imports IndexPriceUpdate, MarkPriceUpdate from nautilus_trader.model.data,
                                     ParquetDataCatalog from nautilus_trader.persistence.catalog —
                                     zero imports from dydx_collector, DydxMinuteBar or otherwise.
troll/ml_signals/chart_data.py:     imports from nautilus_trader.*, ml_signals.book_features,
                                     ml_signals.candles, ml_signals.indicators —
                                     zero imports from dydx_collector.
```
Neither file imports `DydxMinuteBar`, or anything from `dydx_collector`, at all. The "no reverse imports" half of the claim is still true, but the specific, falsifiable claim used as evidence for AD-4's module-boundary rule doesn't match reality. (The files that *do* correctly import a shared type from `dydx_collector` per AD-4's intent — `backtest_snapshot.py` and `snapshot_strategy.py`, importing `DydxSecondSnapshot` — aren't the ones cited, and aren't named in AD-3's Binds list at all; see finding 5.)

### 4. Broken internal cross-reference: Stack table points to a Deferred entry that doesn't exist (Medium)

The Stack table's `nautilus_trader` row reads: *"1.229.0 (pinned — bump requires re-validating PyO3 dYdX precision bindings; note: this build is PyPI-tagged Beta and was 6 days old at time of pin — **see Deferred**)."*

There is no corresponding entry anywhere in the Deferred section. The six Deferred bullets cover: shared validator extraction, dashboard crossed-book cleanup, dashboard staleness-gap rendering, buffer durability, gate-logic version skew, rejection-rate observability, and the open_interest/volume24h namespace split. None of them discuss the Beta-pin / 6-day-freshness risk. This is a real, load-bearing risk left with a dangling pointer — a future reader following "see Deferred" to find out what the accepted trade-off or revisit-trigger is will find nothing, meaning the actual decision (why is a 6-day-old Beta release an acceptable pin?) is undocumented anywhere in the spine.

### 5. AD-3's Binds list and the Structural Seed are stale relative to the actual `ml_signals/` directory (Medium)

`ml_signals/` currently contains 15 Python modules; AD-3's Binds and the Structural Seed's file list both name only 6: `dashboard`, `backtest_dydx`, `backtest_ofi`, `catalog_stats`, `metrics_computer`, `chart_data`. Not named anywhere: `book_features.py`, `candles.py`, `footprint.py`, `metrics_store.py`, `ofi_strategy.py`, `watchlist.py`, `rank_history.py`, `backtest_snapshot.py`, `snapshot_strategy.py`, `example_strategy.py`. Several of these are readers in the same sense AD-3 cares about (`backtest_snapshot.py`/`snapshot_strategy.py` both import `DydxSecondSnapshot` and drive backtests against catalog data). AD-4's namespace-level Binds (`ml_signals` as a whole) still covers them for the module-boundary rule, but AD-3's "readers trust the gate completely" rule is scoped to named modules, not the namespace — so, read literally, half of `ml_signals`'s actual reader surface has no explicit "don't re-validate" obligation. A future implementer adding a data-quality check to `watchlist.py` or `ofi_strategy.py` wouldn't be contradicting any named rule.

### 6. No staleness/liveness contract for `rankings:live`, unlike the pattern already established for `snapshots:raw` (Medium)

`snapshots:raw` has an explicit staleness story end-to-end: the collector's `_STALE_BOOK_NS` (5s) skip, and downstream, `dashboard._rankings_json` computes `age_s`/`stale` from `_LAST_INGEST_TS` so a dead feed is visually flagged rather than showing a frozen value forever (this is literally `troll/CLAUDE.md`'s DATA-01: "never display stale or fabricated values as live market data... the gap must be flagged visually," which explicitly says "when adding new data sources or display paths, apply the same principle").

AD-9 introduces `rankings:live` as a new, analogous live-data channel — Coin Ranking + active Ranking Mode, read by both `dashboard` and `bot_tui` — but says nothing about what happens if `ranking_engine` dies, stalls, or falls behind. Nothing in AD-9 or the Consistency Conventions table specifies whether readers must detect and flag a stale/absent ranking, or whether `ranking_engine` publishes a heartbeat/staleness signal alongside the ranked list. Given this project's own stated principle applies by name to "new data sources," this is a real, non-obvious dimension left silent rather than decided or explicitly deferred.

### 7. Minor: enforcement is convention-only for most ADs, which the spine is honest about but never totals up (Low)

AD-1 explicitly admits "no compiler/lint-level mechanism... holds by there being exactly one writer today, not by structural enforcement." AD-4, AD-5, AD-6, AD-7, AD-9, and AD-10 have no structural enforcement either (no import-linter rule, no CI check) — the only structural enforcement anywhere in the spine is AD-3's `dashboard` catalog `:ro` volume mount, and that only covers the one reader that happens to run in the `dashboard` container (not `backtest_dydx`/`catalog_stats`/etc., which presumably run outside Docker with read-write catalog access). This is a defensible trade-off for a solo-maintained, YAGNI-styled codebase, and the spine is transparent about it AD-by-AD — but it's worth naming as a pattern: the spine is asking the same "one maintainer, no automated gate" discipline to now hold across five namespaces (`dydx_collector`, `ml_signals`, `live_paper`, `ranking_engine`, `bot_tui`) instead of two, with zero net new structural enforcement introduced anywhere in this update round.

### Not flagged (checked, found consistent)

- Redis channel names (`snapshots:raw`, `rankings:live`, `bots:status`, `bots:control`) are consistent across the mermaid diagram, prose, AD rules, and Consistency Conventions table, and `snapshots:raw` matches the actual channel name in `collector.py`/`dashboard.py` (the memlog's account of a prior `snapshots:1s` fix checks out).
- AD-8's "explicitly not banned" carve-out for `live_paper` matches `troll/CLAUDE.md`'s own carve-out language almost verbatim — no contradiction between the spine and the project's working rules.
- AD-7's citation (`open_interest.py:109`, `classify_liquidity`) is accurate.
- AD-9's citation (`dashboard.py:900-976` for `_watchlist_ids`/`_current_ranks`/`_rankings_json`) is accurate.
- The paired-dependency-version Consistency Convention (Redis client vs. broker image) is actually implemented in both `troll-requirements.txt:3` and `docker-compose.yml`'s redis service comment, cross-referencing each other as the convention describes.
- `urwid==4.0.2` carries an explicit, dated version-verification note; it's the only genuinely new dependency this round, and it's the one row that does the verification work the checklist asks for.
- Deferred items are all legitimately non-load-bearing (buffer durability is data loss, not corruption; the two dashboard cleanup items are correctly split so gap-rendering isn't accidentally deleted alongside the redundant crossed-book check; the open_interest/volume24h namespace split is explained and bounded).
