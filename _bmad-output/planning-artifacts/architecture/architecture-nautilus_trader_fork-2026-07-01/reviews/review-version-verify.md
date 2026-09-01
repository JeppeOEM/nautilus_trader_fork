# Review: Version/Reality-Check Verification — ARCHITECTURE-SPINE.md

**Reviewer lens:** Verify every committed decision was web-researched or reality-checked rather than asserted from training data.
**Date of this review pass:** 2026-07-24 (supersedes/extends the 2026-07-01 pass archived below)
**File reviewed:** `_bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md`
**Trigger for this pass:** one new Stack entry added this run — `urwid | 4.0.2 (verified current on PyPI 2026-07-24; native AsyncioEventLoop, compatible with Python 3.7+, fits the 3.12–3.14 pin)` — plus a light spot-check of the pre-existing entries.

---

## PRIMARY FINDING — `urwid 4.0.2` is NOT current on PyPI; the "verified current on PyPI 2026-07-24" claim is false

**Spine text (Stack table):**
> `urwid | 4.0.2 (verified current on PyPI 2026-07-24; native AsyncioEventLoop, compatible with Python 3.7+, fits the 3.12–3.14 pin)`

**Fresh checks performed** (PyPI project page, PyPI JSON API, upstream changelog cross-checked against two independent sources, GitHub releases page):

| Version | Release date |
|---|---|
| 4.0.0 | 2026-03-30 |
| 4.0.1 | 2026-05-26 |
| **4.0.2** | **2026-06-02** |
| 4.0.3 | 2026-06-25 |
| 4.0.4 | 2026-07-13 |
| 4.0.5 | 2026-07-20 |
| **4.0.6** | **2026-07-23** |

All sources (`pypi.org/project/urwid/`, `pypi.org/pypi/urwid/json` → `info.version`, `urwid.org/changelog.html`, the project's raw `docs/changelog.rst`, and `github.com/urwid/urwid/releases`) independently agree: **4.0.6 is current as of the review date**, not 4.0.2. The pin is four patch releases stale. More pointedly, 4.0.6 shipped 2026-07-23 — one day *before* the date the spine claims to have verified 4.0.2 as current on live PyPI. Either that check was not actually performed against live data on the stated date, or it was performed and the result was misread/mis-recorded. Either way, a specific, falsifiable "verified current" claim in the document does not hold up under a same-day re-check — exactly the failure mode this gate exists to catch.

**The two sub-claims bundled into the same entry:**

- *"native `AsyncioEventLoop`"* — **holds up**. Confirmed via `urwid.readthedocs.io/en/latest/reference/main_loop.html` and the changelog: `AsyncioEventLoop` is a first-party class shipped in urwid's own `event_loop` module (present since urwid 1.3.0), not a third-party add-on. No issue.
- *"compatible with Python 3.7+"* — **stale/inaccurate**. The current package's actual floor, confirmed twice (PyPI classifiers and the JSON API's `requires_python` field), is `Python >=3.9.0`. 3.7 was the floor several major versions back (changelog shows a "Python <3.7 support dropped" note around the 2.2.0 era); by 4.x the floor moved to 3.9. This doesn't break anything for this project — 3.9 is still below the 3.12–3.14 pin — but it's a second inaccurate factual claim riding on the same line that says "verified."

**Net assessment:** Block-worthy. Recommend either (a) bump the pin to 4.0.6, having skimmed the 4.0.3–4.0.6 changelog entries (typing improvements, a ListBox pagination bugfix, column-width optimization, scrollbar layout, session-id security hardening for the web display — all additive/bugfix, nothing that reads as breaking for a TUI-only use case), or (b) if there's a deliberate reason to stay on 4.0.2, rewrite the claim honestly ("pinned to 4.0.2 as of <date> for <reason>, not tracking latest") and correct "Python 3.7+" to "Python 3.9+."

## Spot-checks on pre-existing Stack entries (light pass, as instructed — these predate this run)

| Entry | Spine claim | Fresh check | Verdict |
|---|---|---|---|
| `nautilus_trader` | 1.229.0, PyPI-tagged Beta, 6 days old at pin | Re-confirmed: 1.229.0 exists, released ~2026-06-25/26, still Beta-tagged. Consistent with the spine's own self-flagged caveat from the prior pass (see Appendix below) — no new issue. | OK (previously flagged, still accurate) |
| `plotly` | 6.8.0 | Current PyPI version is now **6.9.0** (released 2026-07-09). Matched exactly at the prior review pass (2026-07-01); one minor version has since shipped. No "verified current" claim is attached to this entry, so this is ordinary drift, not a broken claim. | Minor — refresh on next pass |
| `pandas` | 3.0.4 | Current PyPI version is now **3.0.5** (released 2026-07-22). Matched at the prior pass; one patch has since shipped. Same caveat — no explicit "as of" claim attached. | Minor — refresh on next pass |
| `redis` (client) | `>=8.0.1` | Current PyPI version is exactly **8.0.1** (2026-06-23). Floor pin (`>=`), so trivially still satisfied regardless of any future patch. | OK |
| `aiohttp` | `>=3.14.1` | Current PyPI version is **3.14.3** (2026-07-23). Floor pin, still satisfied. | OK |
| `redis` (broker image) | `redis:8-alpine` | Confirmed via Docker Hub search that `redis:8-alpine` remains an actively maintained floating tag on the 8.x line. Floating tags track forward automatically, unlike the exact-pinned libraries above, so there's no "went stale" risk the way there is for urwid/plotly/pandas. Still coherently paired with `redis-py>=8.0.1` per the RESP3 convention already documented in the spine's Consistency Conventions table (this pairing was the subject of the 2026-07-01 pass's one real finding, since resolved — see Appendix). | OK |

None of these are suspicious or contradictory; `plotly` and `pandas` are each one release behind current, but neither carries a specific "verified as of date X" claim the way `urwid` does, so this is routine drift rather than a false statement.

## Searches performed this pass (audit trail)

- WebFetch `https://pypi.org/project/urwid/` — current version, release date, Python classifiers
- WebSearch "urwid 4.0.2 PyPI release AsyncioEventLoop"
- WebFetch `https://raw.githubusercontent.com/urwid/urwid/master/docs/changelog.rst` — full version/date table, AsyncioEventLoop + Python-compat history
- WebFetch `https://github.com/urwid/urwid/releases` — cross-check of release dates
- WebFetch `https://pypi.org/project/urwid/#history` — release history cross-check
- WebFetch `https://urwid.org/changelog.html` — per-version changelog detail (4.0.0–4.0.2)
- WebFetch `https://pypi.org/pypi/urwid/json` — authoritative `info.version` / `info.requires_python`
- WebFetch `https://urwid.readthedocs.io/en/latest/reference/main_loop.html` — confirms AsyncioEventLoop is native to the package
- WebSearch "nautilus_trader 1.229.0 PyPI release"
- WebSearch "plotly 6.8.0 pandas 3.0.4 release PyPI 2026"
- WebFetch `https://pypi.org/project/pandas/` — current version/date
- WebFetch `https://pypi.org/project/plotly/` — current version/date
- WebSearch "aiohttp 3.14.1 redis-py 8.0.1 redis:8-alpine docker hub"
- WebFetch `https://pypi.org/project/redis/` — current version/date
- WebFetch `https://pypi.org/project/aiohttp/` — current version/date

## Recommendation

**Block/return for correction** on the `urwid` Stack line specifically: fix the version number (bump to 4.0.6, or relabel the pin as an intentional, non-latest choice) and fix the "Python 3.7+" sub-claim to "Python 3.9+" (the current package's real floor). **Non-blocking:** refresh `plotly` → 6.9.0 and `pandas` → 3.0.5 whenever convenient; no action needed on the two floor-pinned (`>=`) client libraries or the floating Docker broker tag.

## Summary of findings (this pass)

1. **`urwid 4.0.2`'s "verified current on PyPI 2026-07-24" claim is false** — current is 4.0.6, four patch releases ahead, with the newest of those four shipping the day before the claimed verification date. This is the headline finding.
2. **`urwid`'s "compatible with Python 3.7+" sub-claim is also stale** — the current package's actual floor is Python 3.9+, not 3.7+ (doesn't break the project's 3.12–3.14 pin, but it's a second inaccuracy on the same line).
3. **`urwid`'s "native AsyncioEventLoop" sub-claim is accurate** — genuinely first-party, present since urwid 1.3.0.
4. **`plotly` (6.8.0 → current 6.9.0) and `pandas` (3.0.4 → current 3.0.5) are each one release behind** as of this review, but neither makes an explicit "verified as of" claim, so this is ordinary drift rather than a broken assertion — low severity, worth a routine refresh.
5. **Floor-pinned entries (`redis` client `>=8.0.1`, `aiohttp` `>=3.14.1`) and the floating `redis:8-alpine` broker tag all check out** with no drift risk inherent to how they're pinned.

---

## Appendix — prior pass archived (2026-07-01, pre-`urwid`)

The following is the full text of the previous version-verify review, retained for context since it covered the rest of the Stack table and several `[ADOPTED]` invariant citations not re-litigated in depth this pass (the task instructions asked for depth on `urwid` and only a light spot-check on the rest).

### 1. Stack version verification (web-checked against date 2026-07-01)

| Name | Spine version | Verified reality | Verdict |
| --- | --- | --- | --- |
| Python | 3.12–3.14 | 3.14 released Oct 2025; well-established by July 2026 | OK |
| nautilus_trader | 1.229.0 | Confirmed on PyPI, released June 25, 2026 — 6 days before the spine's `updated` date. Tagged "Beta latest" on PyPI. | Accurate version number, but pinning to a build released 6 days earlier and marked beta is a real risk the spine notes the *rationale* for (PyO3 precision bindings) but not the freshness/stability risk itself. |
| plotly | 6.8.0 | Confirmed: plotly 6.8.0 released June 3, 2026 on PyPI. Matches exactly. | Verified accurate at the time. |
| pandas | 3.0.4 | Confirmed: pandas 3.0.0 shipped Jan 21, 2026; 3.0.4 a plausible patch by July 2026. Postdates typical training cutoffs — strong evidence of a real lookup, not a guess. | Verified accurate at the time. |
| redis (client) | >=8.0.1 | Confirmed: redis-py 8.0.1 released June 23, 2026, 8.0.0 GA May 28, 2026. Matches exactly. | Verified accurate. |
| aiohttp | >=3.14.1 | Confirmed: aiohttp 3.14.1 released June 7, 2026. Matches exactly. | Verified accurate. |
| redis (broker image) | redis:7-alpine | Docker Hub tags dominated by the 8.x line at the time of that search; no evidence 7-alpine was still current. Inconsistent with the redis-py 8.0.1 client pin (RESP3 default, ~84 command response types changed) — a real, unreconciled mismatch. | Flagged. **Resolved 2026-07-01** per the spine's own Deferred section: broker bumped to `redis:8-alpine`. |
| Dozzle | amir20/dozzle:latest | Confirmed active; latest tagged release v10.0.4 (Feb 21, 2026). Fits stated purpose. | OK on identity/fit; floating `:latest` tag is a minor reproducibility nit, not an error. |

### 2. Technology existence / fit-for-purpose (2026-07-01 pass)

Redis pub/sub, `ParquetDataCatalog`, Dozzle, and Docker Compose were all confirmed real, current, and fit for their stated purposes. No technology named was defunct, renamed, or repurposed from what was claimed.

### 3. Grounding of internal-reality claims (2026-07-01 pass)

- **Well-grounded** (`[ADOPTED]` tags citing a specific file/function): AD-4, AD-5, and the Deferred section's symbol references (`collector._second_loop`, `_CHART_GAP_THRESHOLD_MS`, `_flush_once()`, etc.) — all specific, checkable, and consistent with the project's own `CLAUDE.md`.
- **Weaker / asserted without direct evidence**: AD-2, AD-6, AD-7, AD-8 — tagged `[ADOPTED]` but without file:line grounding at the same level of specificity as AD-4/AD-5. Not demonstrably false, just harder to independently verify from the document alone.

*(End of archived 2026-07-01 material.)*
