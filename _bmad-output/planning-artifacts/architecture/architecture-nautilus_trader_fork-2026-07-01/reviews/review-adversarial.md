# Adversarial Review — ARCHITECTURE-SPINE.md (Gatekeeper paradigm)

**Reviewer stance:** independent adversary, no prior context on this conversation. Goal: find scenarios where two implementers, each obeying every AD to the letter, still produce incompatible or corrupt outcomes — or where the spine's stated invariants are not actually enforceable as written.

**Target:** `_bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md`

---

## Finding 1 — AD-3 blind spot: gate validates *staleness of the last update*, not *shape of the current snapshot*

**Scenario:** dYdX enforces a documented 2-subscriptions/sec throttle per WS connection (already diagnosed in project history as the root cause of a prior dashboard bid/ask-gap bug). During a reconnect storm across N liquid instruments, the Rust WS client re-subscribes instruments one at a time. Consider the window between "instrument re-subscribed, connection open" and "first book snapshot/refresh message received": if the collector's per-instrument staleness check (`_STALE_BOOK_NS`, keyed off *time since last delta*) treats "no delta received yet because we just resubscribed" as *not stale* (timestamp is fresh, there's simply no prior delta to compare against), a `DydxSecondSnapshot` with **empty `bid_prices`/`ask_prices` lists** (zero levels) can pass the gate and be written to Parquet and published to Redis. Nothing in AD-2's invariant list (crossed book, staleness, precision) explicitly covers "book has fewer than 1 level."

Every reader downstream computes off level 0 unconditionally: `ml_signals/indicators.py`'s `Microprice().update_raw(bp, bs, ap, as_)` assumes `bid_prices[0]`/`ask_prices[0]` exist; `dashboard.py`'s live ticker and `chart_data.py`'s spread/microprice derivation do the same (per the spine's own "computed on read" design). An empty-level snapshot slipping past the gate causes an `IndexError` (or worse, a silent `None`/NaN propagating into a rolling OFI/OBI window) in every one of the six reader modules simultaneously — precisely because AD-3 forbids any of them from defensively checking `len(bid_prices) > 0` before indexing.

**AD(s) exposed:** AD-2 (invariant list is incomplete — "empty book" is a distinct failure mode from "stale book" and isn't named), AD-3 (forbids the exact defensive check that would prevent the crash).

**Verdict:** Real architectural hole, not an acceptable deferral. It is not mentioned anywhere in the Deferred section, and it directly contradicts the project's own OBS-01 rule ("zero book updates ... is a failure mode, not market behavior") by giving the gate no explicit obligation to check for it. Recommend either adding "non-empty top-of-book" to AD-2's invariant list, or explicitly carving out a narrow AD-3 exception for shape/existence guards (as distinct from re-implementing *quality* checks).

---

## Finding 2 — Deferred "Dashboard cleanup" conflates two different concerns; following it literally reintroduces a DATA-01 violation

**Scenario:** The Deferred section states that `dashboard._coin_chart_json`'s crossed-book skip *and* its `_CHART_GAP_THRESHOLD_MS` staleness-gap rendering are both "redundant duplication ... under AD-3," flagged as a single follow-up cleanup story. These are not the same kind of check:

- The **crossed-book skip** genuinely is redundant *if* the gate is correct — the collector never writes a crossed book, so a reader can never encounter one in catalog data. Removing it is safe (modulo Finding 5 below).
- The **staleness-gap render** solves a different problem entirely: it detects *time-range gaps between adjacent rows the reader queried*, and inserts a `None` so Plotly draws a break instead of interpolating a straight line across missing time. This is not "re-validating a data-quality check the gate already performed" — it is read-time handling of the *fact* that the gate legitimately skipped writing snapshots during a stale window (per DATA-01/`_STALE_BOOK_NS`). The gate operates at write time and has no mechanism to prevent a plotting library from joining two temporally-distant points with a straight line; only the reader, at render time, can know it queried a range that spans a gap.

If a future story executes the Deferred item literally — "this is redundant under AD-3, remove it" — deleting `_CHART_GAP_THRESHOLD_MS` gap-insertion would cause the dashboard to silently interpolate through a legitimate stale-data gap (a flatline or slanted line implying continuous price action), which is exactly the failure mode DATA-01 was written to prevent ("the gap must be flagged visually rather than papered over with a flatline").

**AD(s) exposed:** AD-3 (its stated rationale — "duplicated, independently-drifting validation logic" — doesn't actually describe what the gap-render code does), and the Deferred section itself, which is the deferred item quietly reintroducing a divergence/regression risk elsewhere in the spine (item 3 of the review brief).

**Verdict:** Real hole. The spine should split this Deferred bullet into two: (a) crossed-book skip — safe to remove, true duplication; (b) gap-rendering — reclassify as a *permanent, required* reader responsibility, not cleanup debt, and reword so a future implementer doesn't delete a DATA-01 control under an AD-3 banner it doesn't actually fall under.

---

## Finding 3 — AD-1 has no structural enforcement inside `dydx_collector/`; only the container boundary is enforced

**Scenario:** The Deployment section notes the dashboard container mounts the catalog `:ro`, calling this "a structural, not just logical, enforcement of AD-3." That's true, and it's good — but it only prevents `ml_signals` (a different container) from writing. It does nothing to prevent a *second writer inside the collector's own container/module*. Nothing stops a future contributor from adding `dydx_collector/backfill.py` or extending `prune_catalog.py` to call `ParquetDataCatalog.write_data()` directly against raw WS payloads (e.g., "quick backfill script to patch a gap after an outage") without routing through `collector._second_loop`'s gate. AD-1's `Binds` field is scoped to `dydx_collector.collector` — a *module*, not "any code that calls `write_data()` or publishes to `snapshots:1s`." The paradigm's opening sentence makes a much stronger, unscoped claim ("collector.py is the only component in troll/ that writes market data anywhere") than AD-1's Rule actually constrains.

**AD(s) exposed:** AD-1 (Binds scope narrower than the paradigm's claim; Rule is prose-only, "must route through this same gate," with zero mechanism — no shared "already-validated" wrapper type that only the gate can construct, no lint/import-boundary check, no runtime guard on the catalog/Redis client objects).

**Verdict:** Real hole, and it directly answers item 5 of the review brief: **no**, AD-1 does not structurally prevent a new writer or sink from being added later — it relies entirely on developer discipline plus the general "don't add abstractions" YAGNI culture of the codebase. The one piece of structural enforcement that exists (docker `:ro` mount) protects only the `ml_signals` boundary, not the intra-`dydx_collector` boundary the paradigm actually depends on.

---

## Finding 4 — AD-1's "same code path" is satisfiable by two materially different, incompatible implementations

**Scenario:** Two engineers implement AD-1 independently, each fully compliant with the literal Rule text ("both sinks fed from the same already-validated object, in the same code path"):

- **Implementer A:** validates once, then synchronously calls `catalog.write_data(obj)` followed by `redis.publish(json.dumps(obj))` in the same function, same await chain.
- **Implementer B:** validates once, then pushes the validated object onto an `asyncio.Queue`; two independent async consumers drain it — one batches into `_flush_once()` on a timer, one publishes to Redis immediately. Also technically "the same already-validated object," and arguably still "the same code path" up to the point of validation.

Both satisfy AD-1's Rule as written. But they produce different systems: under B, a dashboard live-tick (from Redis, near-real-time) can reference a data point that does not yet exist in the catalog for potentially `flush_interval_seconds`, and if the flush worker crashes or the process is killed between the Redis publish and the next flush, that item is visible to the dashboard's Redis-fed live ticker but permanently absent from Parquet — a live/historical divergence that's invisible to either sink individually and not covered by the explicitly-scoped "buffer durability = data loss" deferral (that deferral is about *both* sinks losing data on an unclean crash together; this scenario is about the two sinks silently disagreeing even in the *normal*, non-crash case, because "same code path" wasn't specific enough to require synchronous/atomic delivery to both sinks).

**AD(s) exposed:** AD-1 (Rule vague enough for incompatible implementations — item 1 of the review brief), and indirectly AD-3 (readers of Redis vs. Parquet implicitly assume they're seeing "the same approved data," which B breaks under partial delivery).

**Verdict:** Real hole. Recommend tightening AD-1's Rule to specify: both sink writes happen from the same validated object *before* control returns to the ingestion loop (i.e., synchronous/ordered, not fanned out to independent async consumers), or explicitly permit the queue-fan-out pattern but require idempotent reconciliation.

---

## Finding 5 — No mechanism to handle gate-logic version skew on already-written catalog data

**Scenario:** AD-2/AD-3 assume the gate is (and always was) correct. In practice the gate's invariant checks will be patched over the project's life (e.g., Finding 1's "empty book" case gets added as a new check next month). Any snapshot written to Parquet *before* that fix, under an older, buggier gate, permanently contains the defect — there is no re-audit, quarantine-and-reprocess, or version-tagging mechanism described anywhere in the spine. AD-3 explicitly forbids readers from adding the very check that would let them detect or filter such rows retroactively ("no reader may re-implement a data-quality check ... against catalog or stream data"). Since AD-2 also forbids a validity-flag field, there isn't even a passive marker distinguishing "written under gate v1 (buggy)" from "written under gate v2 (fixed)."

**AD(s) exposed:** AD-2 (no flag field — reasonable for *rejected* data, but has the side effect of making *accepted-but-later-found-invalid* data unidentifiable after the fact), AD-3 (forbids the only mechanism — reader-side re-checking — that could work around this).

**Verdict:** Partially acceptable, partially a hole. The spine correctly scopes out "buffer durability = data loss" as an explicit non-goal; this is a *different* risk (silent historical corruption surviving a gate bug fix, not data loss) and is not mentioned anywhere, so it isn't an explicitly-scoped-out risk — it's an unacknowledged gap. At minimum this should be named as a known limitation with a suggested mitigation (e.g., record the gate/validator version alongside written rows for future backfill/audit, without adding a per-row "validity" semantic).

---

## Finding 6 — AD-4 "shared types ... and similar" is ambiguous about where logic vs. data-type boundaries sit

**Scenario:** `dydx_collector/open_interest.py` houses both `DydxOpenInterest` (a data type) and `classify_liquidity` (AD-7-governed *logic*, USD-denominated liquidity tiering). Suppose `ml_signals/dashboard.py` needs to show each coin's liquidity tier (a plausible, even likely, requirement — the collector already distinguishes "subscribed vs. illiquid" per project history). Two paths, both defensible under a literal reading of AD-4:

- Import `classify_liquidity` from `dydx_collector.open_interest` — arguably "collector logic," which AD-4's Rule explicitly forbids ("Never import collector logic from `ml_signals`").
- Reimplement the tiering formula in `ml_signals` — exactly the kind of independently-drifting duplication AD-3's own rationale calls out as "already occurred once."

AD-4's Rule doesn't say which shared-type/logic split is authoritative, and "and similar" gives no test for classifying a given symbol. Two implementers who both "follow AD-4 to the letter" could reasonably land on opposite answers.

**AD(s) exposed:** AD-4 (vague boundary — item 1 of the review brief), interacting with AD-7 (creates a real incentive to duplicate USD-liquidity logic, the exact incident AD-7 exists to prevent).

**Verdict:** Real hole. Recommend either explicitly re-homing pure functions like `classify_liquidity` as a shared, side-effect-free utility importable by both namespaces (distinct from "collector logic" = stateful ingestion/buffer/gate code), or stating plainly that such display-only derived fields must be precomputed and stored by the collector rather than computed by readers.

---

## Finding 7 — AD-5's precision-safety invariant is scoped only to `dydx_collector`, not to `ml_signals`

**Scenario:** AD-5's `Binds` field is "any code constructing or re-stamping a `Price`/`Quantity` in `dydx_collector`." But `ml_signals/indicators.py` and `chart_data.py` also construct/manipulate `Price`/`Quantity`-typed values read from the catalog (e.g., spread = `ask_prices[0] - bid_prices[0]`, microprice computation, any cross-instrument comparison requiring precision alignment). If a reader ever needs to re-stamp a value at a different precision (plausible for cross-instrument signal work, e.g., normalizing BTC's precision against an altcoin's for a ratio signal), the spine gives it zero guidance — the exact `Price(decimal, precision)` float-trip bug this project already suffered once (per `troll/CLAUDE.md`'s NAUT-01, which *does* apply project-wide) has no corresponding spine-level AD binding readers. An implementer who only reads the spine (not the separate `CLAUDE.md`) could reintroduce the bug in `ml_signals` believing AD-5 doesn't apply to them, since its Binds field says so explicitly.

**AD(s) exposed:** AD-5 (binding scope narrower than the actual risk surface — the underlying `nautilus_trader` bug doesn't care which namespace calls the buggy constructor).

**Verdict:** Real hole, low-to-moderate severity (mitigated somewhat by NAUT-01 existing in the sibling `troll/CLAUDE.md`, but the spine is supposed to be the authoritative build-substrate and shouldn't rely on a reader having also read a different document). Recommend broadening AD-5's `Binds` to "any code in `troll/` constructing or re-stamping a `Price`/`Quantity`."

---

## Finding 8 — Rejected-data audit trail (Dozzle logs) is not queryable, so `catalog_stats`/backtests can't distinguish "no market activity" from "gate rejected data here"

**Scenario:** AD-2 mandates WARNING-level logging as the *sole* audit trail for rejected items, with no flag field and no quarantine store. `catalog_stats.py` (a reader whose whole purpose is presumably to report catalog coverage/gaps) has no way to query "how many snapshots were rejected for instrument X between T1 and T2" — that information lives only in ephemeral container logs (Dozzle), not in any structured, time-queryable store. For backtest research integrity, a time window with a high rejection rate (e.g., a WS reconnect storm producing many crossed-book rejects) is indistinguishable from a genuinely quiet market — both simply show up as "no rows" in the catalog. This matters directly for the project's own OBS-01/OBS-02 rules about not mistaking a dead pipeline for market behavior, but at the *research* layer (backtests silently trained/evaluated over windows with unknown rejection density) rather than the live-dashboard layer those rules were written for.

**AD(s) exposed:** AD-2 (log-only audit trail is a real design choice, but its implication for downstream research-integrity readers isn't addressed anywhere in the spine or Deferred section).

**Verdict:** Borderline — leans toward real gap rather than acceptable deferral, because it isn't named as a deferred/out-of-scope risk anywhere (unlike buffer durability, which is explicitly named and scoped). At minimum this should be an explicit Deferred entry ("rejection-rate observability for research use") rather than silently absent.

---

## Summary Table

| # | Finding | AD(s) | Severity | Status |
|---|---|---|---|---|
| 1 | Empty/zero-level book snapshot passes gate (staleness check ≠ shape check), crashes readers indexing level 0 | AD-2, AD-3 | High | Real hole |
| 2 | Deferred "dashboard cleanup" conflates crossed-book dedup (safe to remove) with gap-rendering (load-bearing DATA-01 control) | AD-3, Deferred | High | Real hole |
| 3 | AD-1 has no structural enforcement inside `dydx_collector/`; only the ml_signals container boundary is enforced | AD-1 | High | Real hole |
| 4 | AD-1 "same code path" satisfiable by sync-shared-call vs. queue-fanout, producing live/historical divergence not covered by the buffer-durability deferral | AD-1 | Medium-High | Real hole |
| 5 | No mechanism for gate-logic version skew — pre-fix bad rows survive silently forever, and AD-3 forbids readers from detecting them | AD-2, AD-3 | Medium | Real hole (unacknowledged, distinct from the scoped-out buffer-durability risk) |
| 6 | AD-4 "shared types ... and similar" doesn't resolve data-vs-logic boundary (e.g. `classify_liquidity`), inviting exactly the duplication AD-7 exists to prevent | AD-4, AD-7 | Medium | Real hole |
| 7 | AD-5 binds only `dydx_collector`; `ml_signals` precision manipulation has no spine-level guardrail against the known float-trip bug | AD-5 | Medium | Real hole |
| 8 | Rejection audit trail is log-only/ephemeral; research readers can't distinguish "quiet market" from "gate rejected data here" | AD-2 | Low-Medium | Real hole, should at least be named in Deferred |

**Explicitly NOT re-flagged:** the "buffer durability" deferral (whole-flush-interval data loss on unclean crash) is correctly scoped as data *loss*, not corruption, and is out of scope for this run as written — findings 4 and 5 above are deliberately distinguished from it (partial-sink divergence and gate-version skew are different failure classes than "lost the whole buffer").
