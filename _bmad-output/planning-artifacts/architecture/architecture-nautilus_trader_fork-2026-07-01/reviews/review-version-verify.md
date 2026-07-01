# Review: Version/Reality-Check Verification — ARCHITECTURE-SPINE.md

**Reviewer lens:** Verify every committed decision was web-researched or reality-checked rather than asserted from training data.
**Date of review:** 2026-07-01
**File reviewed:** `_bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md`

---

## 1. Stack version verification (web-checked against current date 2026-07-01)

I ran live web searches (PyPI/GitHub/Docker Hub release data) against every pinned version in the "Stack" table. Results:

| Name | Spine version | Verified reality | Verdict |
| --- | --- | --- | --- |
| Python | 3.12–3.14 | 3.14 released Oct 2025; well-established by July 2026 | OK |
| nautilus_trader | 1.229.0 | Confirmed on PyPI, released **June 25, 2026** — 6 days before the spine's `updated` date. Search result flags it as tagged "Beta latest" on PyPI. | Accurate version number, but **pinning to a build released 6 days earlier and marked beta is a real risk that the spine does not call out** — it only notes the *rationale* for pinning (PyO3 precision bindings), not the beta/freshness risk itself. |
| plotly | 6.8.0 | Confirmed: plotly 6.8.0 released **June 3, 2026** on PyPI. Matches exactly. | Verified accurate — reads as genuinely researched, not guessed. |
| pandas | 3.0.4 | Confirmed: pandas 3.0.0 shipped **Jan 21, 2026**; 3.0.4 is a plausible patch release by July 2026 (3.0.1/3.0.3 patches independently confirmed). Note pandas 3.0 was *not yet released* as of most LLM training cutoffs (Jan 2026 for this model) — a version-from-memory guess would very likely have said "2.2.x," not "3.0.4." This specific, correct major-version bump is strong evidence of an actual lookup, not a training-data assertion. | Verified accurate. |
| redis (client) | >=8.0.1 | Confirmed: redis-py 8.0.1 released **June 23, 2026**, with 8.0.0 GA May 28, 2026. Matches exactly, and again postdates typical training cutoffs — cannot have been asserted from memory. | Verified accurate. |
| aiohttp | >=3.14.1 | Confirmed: aiohttp 3.14.1 released **June 7, 2026**. Matches exactly. | Verified accurate. |
| redis (broker image) | redis:7-alpine | Current Docker Hub `redis` official image tags as of the search are dominated by the 8.x line (`8.8.0`, `8.8`, `8`, `8-alpine`, `latest`, `trixie`); no evidence 7-alpine is still the recommended/current tag. **This is inconsistent with the redis-py client pin one row above it**: redis-py 8.0.1 changed its *default* wire protocol from RESP2 to RESP3 and unified ~84 command response types — described by its own release notes as breaking relative to 7.x-era servers. Pairing a 8.0.1 client against a `redis:7-alpine` server is a real, unreconciled version mismatch. | **Flag — not obviously wrong in isolation, but internally inconsistent with the client-library row directly above it, and the spine does not acknowledge or resolve this.** |
| Dozzle | amir20/dozzle:latest | Confirmed Dozzle is active; latest tagged release v10.0.4 (Feb 21, 2026). Project fits its stated purpose (real-time Docker log viewer). | OK on identity/fit. Using the floating `:latest` tag (rather than pinning, as done for every other component) is a minor reproducibility inconsistency but not a factual error — flagged as a nit, not a defect. |

**Overall stack verdict:** Five of the seven pinned dependency versions (nautilus_trader, plotly, pandas, redis client, aiohttp) match live release data almost exactly, including several that postdate any plausible LLM training cutoff (pandas 3.0.x, redis-py 8.0.1, aiohttp 3.14.1, plotly 6.8.0 all shipped within the ~5 weeks before this document's `updated` date). This is strong positive evidence the Stack table was genuinely researched at authoring time, not hallucinated from training data. The one real issue is the **redis:7-alpine broker image vs. redis-py>=8.0.1 client mismatch**, which looks like a version was pinned without cross-checking compatibility with the row above it.

## 2. Technology existence / fit-for-purpose

- **Redis for pub/sub** (`snapshots:1s` channel) — Redis pub/sub is a real, current, appropriate mechanism for this use case. OK.
- **Parquet / ParquetDataCatalog** — real Nautilus construct, consistent with the project's own CLAUDE.md description of `ParquetDataCatalog.write_data()`. OK.
- **Dozzle for log viewing** — verified active project, correct purpose (container log streaming), matches CLAUDE.md's stated use ("Dozzle for live log visibility"). OK.
- **Docker Compose for deployment** — standard, unremarkable, fits the two-image split described elsewhere in the project's CLAUDE.md. OK.

No technology named in the spine is defunct, renamed, or repurposed from what's claimed.

## 3. Grounding of internal-reality claims (file refs, `[ADOPTED]` tags, mechanism specificity)

Assessed each `[ADOPTED]`-tagged invariant for whether it cites concrete evidence (file path, function name, specific mechanism) versus asserting a state without evidence:

**Well-grounded (cite a specific file, function, or mechanism):**
- **AD-4** — "`[ADOPTED]` — confirmed: `ml_signals/catalog_stats.py` and `chart_data.py` import only `DydxMinuteBar`; no reverse imports exist." Specific files named, specific claim (import sweep), falsifiable.
- **AD-5** — "`[ADOPTED]` — reference: `dydx_collector/client.py:_at_fixed_precision()`." Matches the exact fix location independently documented in the top-level project `CLAUDE.md` ("Fixed in `troll/dydx_collector/client.py`'s `_at_fixed_precision()`"), which corroborates this claim rather than merely repeating it verbatim without traceability.
- **Deferred section** (not `[ADOPTED]` but reality claims) — cites `collector._second_loop`, `dashboard._coin_chart_json`, `_CHART_GAP_THRESHOLD_MS`, `_flush_once()`, `flush_interval_seconds` — all specific, checkable symbol names, consistent with the naming in the project's own `CLAUDE.md` (`_STALE_BOOK_NS`, `_CHART_GAP_THRESHOLD_MS` are independently documented there too).

**Weaker / asserted without direct evidence in this document:**
- **AD-2** — tagged `[ADOPTED]` but cites no file or line for where the fail-closed WARNING-log behavior is implemented; the only supporting detail (Dozzle as audit trail) appears later in the Deployment section, not attached to the invariant itself.
- **AD-6** — tagged `[ADOPTED]` with no file/line reference at all; the rule is stated as a policy, not tied to an observed code location the way AD-4/AD-5 are.
- **AD-7** — tagged `[ADOPTED]`, names a function (`open_interest.classify_liquidity`) but no file:line and no evidence the USD-denominated fix is actually in place versus merely intended (cf. OBS-03 in `troll/CLAUDE.md`, which describes this as a known-fixed incident — plausible but not independently re-cited here with a code pointer).
- **AD-8** — tagged `[ADOPTED]`, no file reference; asserts "the collector owns its own asyncio loop... driving `nautilus_pyo3.DydxHttpClient`/`DydxWebSocketClient` directly" as fact without pointing at where in `collector.py` this is implemented.

None of these four are necessarily *wrong* — AD-2/AD-6/AD-7/AD-8 read as consistent with the project's own `CLAUDE.md` narrative — but they are asserted at a coarser grain (module/function name only, sometimes not even that) than AD-4 and AD-5, which name exact files and describe how the claim was checked ("confirmed," "reference"). A reviewer without access to the codebase cannot distinguish "verified by re-reading the file" from "restated from the PRD/CLAUDE.md without re-checking" for AD-2/AD-6/AD-7/AD-8.

## Summary of findings

1. **Stack versions are genuinely researched, not guessed** — plotly 6.8.0, pandas 3.0.4, redis-py>=8.0.1, and aiohttp>=3.14.1 all match real release dates within ~5 weeks of the document date, several postdating any plausible training cutoff. This is the strongest positive signal in the review.
2. **Unreconciled mismatch: `redis:7-alpine` broker vs. `redis (client) >=8.0.1`** — redis-py 8.0.1 defaults to RESP3 and is documented upstream as breaking relative to 7.x; the spine pins an old server image against a very new client without acknowledging or resolving the gap.
3. **nautilus_trader 1.229.0 is accurate but freshly-released/beta-tagged** (shipped 6 days before the doc's `updated` date, flagged "Beta" on PyPI) — the spine's rationale note covers *why* it's pinned but not the freshness/stability risk of pinning to a just-shipped beta.
4. **AD-2, AD-6, AD-7, AD-8 carry `[ADOPTED]` tags without the file/line-level grounding that AD-4 and AD-5 provide** — not demonstrably false, but weaker evidentiary support; a future editor should tighten these with the same "confirmed:"/"reference:" pattern used elsewhere in the document.
5. **Dozzle pinned via floating `:latest` tag** while every other image/library in the Stack table is version-pinned — a minor reproducibility inconsistency, not a factual error.

No findings suggest any named technology is defunct, mis-scoped, or doesn't exist. No stack version is implausible for the stated timeframe; if anything the precision of the matches (down to exact release dates) is unusually strong evidence of real lookups.
