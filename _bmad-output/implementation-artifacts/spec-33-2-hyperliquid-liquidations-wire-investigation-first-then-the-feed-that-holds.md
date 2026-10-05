---
title: 'Story 33.2: Hyperliquid liquidations: the wire investigation first, then the feed that holds'
type: 'feature'
created: '2026-10-05'
status: 'done'
baseline_revision: 'f940bfa9adb483dbe553d1f6fbe7cd04643cabd8'
final_revision: '457bf1480bee79f5ba1822176cc426e9ad6c8abd'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-33-context.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** Hyperliquid has no market-wide liquidation feed. The epic named two public-data hypotheses that might still yield Hyperliquid liquidations in the shared `Liquidation` type, and forbade building anything before they were settled on captured frames.

**Approach:** Run the investigation first and keep its tools: a ≥ 60 min `trades` capture plus a one-off probe that cross-queries the public `userFillsByTime`. Adopt a hypothesis only at ≥ 99 % confirmed on the capture with a measured false-negative rate. Ship the design the evidence allows. The planning probe already shows (a) refuted: every market-liquidation fill found carries an ordinary transaction hash, and the all-zero-hash trades are TWAP slices. Backstop fills (b) are rare and bursty, so the planned outcome is **neither**. That means no feed code, the refutation recorded with its numbers, and the upgrade path deferred.

## Boundaries & Constraints

**Always:**
- The investigation tools stay in `platform/scripts/` and are never shipped in an image or run in production.
- Every number in the docs comes from the full probe report (`probe_btc_eth.json`, see Design Notes) or the capture summary. Each comes with its date, window, method and the documented `/info` limit.
- A hypothesis counts as adopted only under the probe's `verdicts` rule. Otherwise it is recorded as refuted, numbers included.
- Docs stay true (DESIGN-03): every place that says what is collected for Hyperliquid must agree.
- The commit order is the investigation first (scripts plus the §1.26 findings), then the outcome's consequences.

**Block If:**
- The full probe's `verdicts.a.adopted` or `verdicts.b.adopted` is `true`. That outcome needs feed code this spec does not plan, so HALT for a re-plan.

**Never:**
- Third-party liquidation APIs, or a Hyperliquid login or user channel.
- `nautilus_trader/` or `crates/` edits, or a new dependency.
- A Hyperliquid liquidation socket, poll or `Liquidation` rows.
- `price_kind`/`confirmed` columns (they belong to design (a) only).
- Adding HYPERLIQUID to `LIQUIDATION_VENUES`.
- dYdX liquidation work (out of scope: not deployed since Story 29.3).

</intent-contract>

## Code Map

- `platform/scripts/capture_hl_ws.py`: the capture harness. `summarize_hashes` (added in planning) counts each coin's live trades that carry the all-zero hash.
- `platform/scripts/hl_liquidation_probe.py` (new, written in planning): the probe. It has the `Info` weight pacing, `resolve`/`classify` (candidates and control), `census` (liquidation fills by hash), `backstop` (HLP child vaults, in window and in history, with confirmation) and `verdicts`.
- `platform/docs/DATA_DICTIONARY.md` §1.26 (~2278-2427): the type's section. Its intro line 2281 names Story 33.2, and its "Capture findings" table is the precedent.
- `platform/docs/DATA_INTEGRITY_AUDIT.md`: the last row is D-150, in section "2026-10-05 — Story 33.1" (~314-321), before "## 3. Conclusions".
- `CLAUDE.md` (repo root): "What is collected" (the Story 33.2 sentence) and the per-venue "Open interest" list (~77-86).
- `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md`: "## Deferred" (~508).
- `platform/kernel/liquidation.py`: the module docstring, which says "shared by every venue that publishes them".
- `platform/verification/domain/verdict.py:56-57`: `LIQUIDATION_VENUES = ("BYBIT",)`.
- `crates/adapters/dydx/src/websocket/messages.rs:480` (`DydxTrade.trade_type`) and `websocket/parse.rs:754` (`parse_trade_ticks` builds `TradeTick` without it): the dYdX fact, read-only.

## Tasks & Acceptance

**Execution:**
- [x] `platform/scripts/capture_hl_ws.py`, `platform/scripts/hl_liquidation_probe.py`: keep both as written in planning. They must pass `ruff format --check`, and `ruff check` must show only `capture_hl_ws.py`'s 5 pre-existing findings. The module docstrings carry the run commands. Commit them with the §1.26 findings below as **commit 1** (`docs(33-2): Hyperliquid liquidation wire investigation`).
- [x] `platform/docs/DATA_DICTIONARY.md` §1.26:
  - Amend the intro: Hyperliquid is not captured, Story 33.2 settled it, with an amendment tag.
  - Add a "**Hyperliquid investigation (Story 33.2)**" block with a findings table taken from the probe report:
    - window and coins;
    - live trades, and the non-transaction-hash trades per coin;
    - candidate classes and confirmed liquidations among the candidates;
    - control size, classes and liquidations found;
    - census: addresses, distinct liquidation trades, by method and hash class;
    - backstop: fills in the window, per-vault history (count, newest age, span) and confirmed/checked;
    - `/info` calls, weight and hours, against the documented 1200/min limit.
  - Add the planning pre-probe, a `userFills` scan of small-taker addresses, stopped before its 150th: 202 market-liquidation fills from 22 addresses, 0 with a non-transaction hash, 182 the liquidated taker (`crossed: true`) and 20 the maker, whose fill carries the marker too.
  - Give each hypothesis its verdict and the reason. The dYdX row is in the CLAUDE.md list.
  - State the upgrade path: a non-validator node's fill stream.
- [x] `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md` "## Deferred": add "**Hyperliquid liquidations from a non-validator node's fill stream**" `[amended 2026-10-05: Story 33.2]`. Say why (both public hypotheses refuted, §1.26), what it needs (node ops, disk, a fill-stream reader feeding the same `Liquidation` type with `price_kind="mark"`), and when to revisit it.
- [x] `CLAUDE.md` (root):
  - Add a sibling "**Liquidations** arrive differently per venue" list after the "Open interest" list:
    - Bybit: `allLiquidation` over a second socket, Story 33.1.
    - Hyperliquid: none, with both hypotheses' evidence in one line each and the deferred node path.
    - dYdX: `type: LIQUIDATED` is carried on REST trades and on the `v4_trades` WS payload, but `parse_trade_ticks` drops it from `TradeTick`; not deployed since 29.3, out of scope.
  - Update the "What is collected" Story 33.2 sentence to the outcome.
- [x] `platform/docs/DATA_INTEGRITY_AUDIT.md`: add a section "2026-10-05 — Story 33.2", with:
  - D-151: Hyperliquid forced flow is absent, not zero. A cross-venue reader must treat Hyperliquid's liquidation values as null, never as 0 (the epic's null-vs-0 rule; 33.3/33.4 enforce it).
  - D-152: the all-zero `hash` on a public trade is a TWAP slice (the taker's fill is absent from `userFills`), never a liquidation marker. Inferring liquidations from it would mislabel TWAP flow as forced flow.
  - D-153: the backstop subset (vault fills) is real but rare and bursty. Shipping it alone would present a few events per month as "Hyperliquid liquidations", so it is not shipped.
  - Each row carries its evidence numbers and status.
- [x] `platform/kernel/liquidation.py`: one docstring sentence saying Hyperliquid writes no rows (Story 33.2, §1.26). No code change.
- [x] `platform/verification/domain/verdict.py`: comment on `LIQUIDATION_VENUES` saying Hyperliquid is absent by Story 33.2's refutation. No behaviour change.
- [x] Commit the refutation consequences as **commit 2**.

**Acceptance Criteria:**
- Given the 75-min capture and the full probe, when §1.26 is read, then it holds every figure the epic's AC lists for the findings table, the date and method, and an explicit verdict for (a) and (b).
- Given the outcome **neither**, when the repo is grepped, then no Hyperliquid liquidation socket, poll or row exists, `LIQUIDATION_VENUES` is unchanged, and the spine's Deferred section names the node fill stream.
- Given the root `CLAUDE.md`, when read, then the "Liquidations" list gives all three venues' answers next to "Open interest", and "What is collected" agrees with §1.26.
- Given the change, when `cd platform && python3 -m pytest kernel/tests verification/tests tests -q` runs, then the result matches the baseline (the pre-existing `verification/tests/test_candles.py` ×17 and `tests/test_legacy_names.py` ×1 failures only).

## Spec Change Log

## Review Triage Log

### 2026-10-05 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 16 (high 0, medium 3, low 13)
- defer: 0
- reject: 3 (low 3)
- addressed_findings:
  - `[medium]` `[patch]` The census tested *fill* hashes while hypothesis (a) is about the *public trade's* hash. I measured that the two are the same field: 120 of 120 fills on a seeded sample of 40 ordinary and 40 all-zero trades. `trade_side` now reports `fill_hash_equals_trade_hash`, and §1.26 has the row.
  - `[medium]` `[patch]` "The all-zero hash marks TWAP slices" was an inference. It is now measured: on a seeded sample of 40 candidates, the absent side was found in `userTwapSliceFills`, with a `twapId`, 40 of 40 times. §1.26, D-152 and the `classify` comment state it, and the 13 `no_fill_found` are called unresolved.
  - `[medium]` `[patch]` `Info.fills` paging skipped fills that shared the last millisecond of a full page. Fixed: the next page restarts at that millisecond, deduplicated by `(coin, tid)`, and the loop stops when a page adds nothing new.
  - `[low]` `[patch]` `Info.post` retried every exception and did not count failed attempts. Fixed: only URL errors, timeouts, 429 and 5xx are retried; a 4xx raises; each attempt is paced, and a failed one charges its base weight.
  - `[low]` `[patch]` The census deduplicated by whichever fill came last. Fixed: each `(coin, tid)` is classified from all of its fills, and disagreement is reported as `mixed`.
  - `[low]` `[patch]` (b)'s confirmed share counted `market` fills from the window. Fixed: backstop fills only.
  - `[low]` `[patch]` Control-sample liquidations were not folded into (a)'s false-negative rate. Fixed. The numbers are unchanged, since the control found 0.
  - `[low]` `[patch]` The docs said "20 confirmations per vault" and implied the sample covered the subset generally. Corrected: 20 in total, 2/2, 16/16 and 2/2 across three vaults, none from the 223-fill vault.
  - `[low]` `[patch]` `userFills` was assumed newest first. It is now sorted, and the order was measured as newest first.
  - `[low]` `[patch]` The replay cutoff was global. It is now per coin (that coin's first `trades` frame) in both scripts, and the counts were re-derived unchanged for all 10 coins.
  - `[low]` `[patch]` A self-trade queried one address twice, and more than 2 fills fell through to `no_fill_found`. Fixed: `set(users)` and an `unexpected_multi_fill` class. The capture holds 0 self-trades.
  - `[low]` `[patch]` A `vaultDetails` shape change crashed the run after an hour of work. Fixed with guarded keys.
  - `[low]` `[patch]` An empty capture or a truncated last line crashed `load_trades`. Fixed: a truncated line is skipped, and an empty capture exits with a message.
  - `[low]` `[patch]` The arguments were not validated (spaces in `--coins`, zero or negative counts). Fixed.
  - `[low]` `[patch]` Reproducibility was not stated. §1.26 now says the census reads the latest fills as of the run, that the raw capture and report are not committed, and which probe version produced the numbers.
  - `[low]` `[patch]` The root `CLAUDE.md` dYdX line had no evidence. It now gives the `crates/` file:line references.

### 2026-10-05 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 11 (high 0, medium 2, low 9)
- defer: 0
- reject: 12 (low 12)
- addressed_findings:
  - `[medium]` `[patch]` The 100 % false-negative rate for (a) read the census's *fill* hashes, but fill hash = trade hash had been measured only on non-liquidation fills, and no census liquidation fell inside the capture (re-checked: neither did any of the pre-probe's 202 on a captured coin). §1.26 and D-152 now say the figure rests on that measured identity, and that (a)'s refutation stands on the candidates alone (0 % confirmed fails the rule).
  - `[medium]` `[patch]` The TWAP row (40/40) and the hash row (120/120) came from an uncommitted script. It is now the probe's `samples` phase (`--sample 40 --sample-seed 7`), re-run on the same capture at 20:27 UTC: 115/115 hashes equal, 0 disagreeing; 40 of 41 absent sides in `userTwapSliceFills`, the 41st a maker fill `userFillsByTime` stopped serving after the first run. Both runs are in §1.26 and D-152.
  - `[low]` `[patch]` `userFillsByTime` serves only an address's 10,000 most recent fills: now a `Known limit:` in the probe docstring and in §1.26 (a possible cause of the 13 `no_fill_found`); the re-run shows it biting.
  - `[low]` `[patch]` §1.26 now states the census population (addresses that traded BTC/ETH in the hour, each read as its latest <= 2000 fills).
  - `[low]` `[patch]` `Info.post` now also retries `ConnectionError`, `http.client.HTTPException` and `json.JSONDecodeError`, does not sleep after the last attempt, and chains the last error.
  - `[low]` `[patch]` A full page in a single millisecond used to stop paging silently. It is now counted (`truncated_pages` in the report) and warned.
  - `[low]` `[patch]` A self-trade's two fills collapsed to one under the `(coin, tid)` key. Paging now dedups by `(coin, tid, side)`, and `resolve` keeps every fill of a `tid`.
  - `[low]` `[patch]` An empty `--coins` selection produced verdicts that read as measured. It now exits.
  - `[low]` `[patch]` A non-list `/info` fills response is now refused by name, not iterated.
  - `[low]` `[patch]` Root `CLAUDE.md` dYdX reference: `models.rs:182` (the doc comment) -> `:184` (the field).
  - `[low]` `[patch]` The spine's revisit trigger for the node fill stream contradicted the operator's 2026-10-05 decision (liquidations from Bybit only; node, S3 archive and paid streams declined on cost). The spine, root `CLAUDE.md`, §1.26 and D-151 now record that decision, and the spine names the other routes.

## Design Notes

- **Evidence files.** These are in the session scratchpad and are not committed (16 MB raw): `/tmp/claude-1000/-home-mrqdt-code-nautilus-trader-fork-epic33/7771ba48-f324-4db9-a2ea-0ddc4bb23f90/scratchpad/`.
  - `hl_trades_main.jsonl`: `trades` for BTC, ETH, SOL, HYPE, XRP, DOGE, SUI, FARTCOIN, PUMP and ENA, 2026-10-05 17:31:57-18:46:56 UTC.
  - `probe_btc_eth.json`: `--coins BTC,ETH`, control 200, census 150, seed 332, 10-minute buckets.
  - `liqscan.out`: the planning pre-probe.
- **Planning evidence.**
  - Smoke probe on the first minutes:
    - All 194 BTC/ETH zero-hash candidates were `one_fill_ordinary`: the maker's fill is found and the taker's is absent, the TWAP signature (a sampled taker's `userFills` ended 33 min earlier).
    - The census found 32 market liquidations, all with a transaction hash.
  - Backstop history: the HLP children hold backstop fills in bursts (e.g. 223 within 0.2 days, about 47 days before the capture).
- **No `DEPLOY_CHECKLIST` entry.** Outcome **neither** changes nothing on the VPS: no service, image content that runs, config or mount. OPS-01 entries are for operator actions, and none is owed.
- **The "Open interest" list is in the root `CLAUDE.md`.** The epic says `platform/CLAUDE.md`, but the list lives in the root file, so the sibling goes there. `platform/CLAUDE.md` "Adding a venue" step 1 already says Hyperliquid has no market-wide feed.
- **The hour was not chosen for volatility.** The census covers each sampled address's latest 2000 fills (days to weeks), which supplies the liquidations a quiet hour lacks. That is what the false-negative measurement rests on.

## Verification

**Commands:**
- `cd platform && uvx ruff@0.15.16 format --check scripts/hl_liquidation_probe.py scripts/capture_hl_ws.py kernel/liquidation.py verification/domain/verdict.py`: expected clean.
- `cd platform && uvx ruff@0.15.16 check scripts/hl_liquidation_probe.py kernel/liquidation.py verification/domain/verdict.py`: expected clean.
- `cd platform && python3 -m pytest kernel/tests verification/tests tests -q`: expected baseline failures only (run with `-o addopts=""` if adding `-p no:cacheprovider`). Got: 18 failed (the known 17+1), 1507 passed, 5 skipped.
- `grep -n "LIQUIDATION_VENUES = " platform/verification/domain/verdict.py`: expected `("BYBIT",)`, unchanged.

## Auto Run Result

**Status:** done

**Summary:** This was a follow-up review of the done Story 33.2 (outcome **neither**: no Hyperliquid liquidation feed; both public hypotheses refuted on the 2026-10-05 capture, and the upgrade path deferred). The review found no spec or intent problem. Fixes:
- The evidence is tightened. (a)'s false-negative figure is qualified, and the two sample rows are now reproducible from the committed probe, re-run, with both runs recorded.
- The probe is hardened: retries, paging truncation, self-trade fills, an empty selection and a non-list response.
- The operator's Bybit-only decision is recorded where the deferred node path is named.

**Files changed (this pass):**
- `platform/scripts/hl_liquidation_probe.py`: a `samples` phase (hash identity, TWAP slices), the 10,000-fill `Known limit:`, broader transient retries, `truncated_pages`, `(coin, tid, side)` paging, every fill kept per `tid`, an empty-selection exit and a non-list guard.
- `platform/docs/DATA_DICTIONARY.md` §1.26: sample provenance and the re-run figures, known limits (fill reach, census population), the qualified false-negative row and verdict, and the operator decision.
- `platform/docs/DATA_INTEGRITY_AUDIT.md`: D-151 (operator decision) and D-152 (the identity qualification, re-run figures).
- `_bmad-output/planning-artifacts/architecture/.../ARCHITECTURE-SPINE.md`: the Deferred entry names the other routes and the operator's decision as its revisit condition.
- `CLAUDE.md`: the dYdX line reference (`:184`) and the operator decision on the Hyperliquid line.

**Review:** 11 patches applied (medium 2, low 9), 0 deferred, 12 rejected (all low):
- The report is not committed and was produced by the commit-1 probe. Already stated in §1.26, as the spec's design.
- (b) is called "refuted". This is the spec's rule wording, and the reason is spelled out.
- The backstop confirmation order and the in-window budget. Documented; 0 in-window fills.
- A crash loses the run. One-off tool.
- Clock skew in the replay cutoff. Negligible on an NTP-synced box, and the verdicts do not depend on it.
- The window is taken from every row. Same start for every captured coin.
- No tests for the probe. TEST-02, as in the prior pass.
- No guard for D-151 in this diff. 33.3/33.4 own it.
- `price_kind="mark"` in the spine. The epic defines it.
- Rate-limit rounding and a shared IP.
- Duplication in `CLAUDE.md`. Intended by the spec.
- The pre-probe cannot be reproduced. The spec requires it.

**Follow-up review recommended:** no. The changes are in an unshipped one-off script and in doc wording, no runtime code changed, and the re-run confirmed the recorded figures, with the differences explained.

**Verification:**
- `ruff format --check` and `ruff check` are clean on the probe, `kernel/liquidation.py` and `verdict.py`. `uvx mypy --disallow-incomplete-defs` on the probe: no issues.
- `python3 -m pytest kernel/tests verification/tests tests -q -o addopts="" -p no:cacheprovider`: 18 failed (the known `test_candles.py` ×17 and `test_legacy_names.py` ×1), 1507 passed, 5 skipped. Matches the baseline.
- The probe's new `samples` phase was run live against Hyperliquid `/info` on the 2026-10-05 capture: 201 calls, weight 7,754, 0 truncated pages.
- `LIQUIDATION_VENUES` is unchanged at `("BYBIT",)`.

**Residual risks:**
- The sample and census evidence decays as fills leave `userFillsByTime`'s 10,000-fill reach, so a later re-run reads less.
- A liquidation's own public trade hash was never compared directly, because none fell inside the capture.
- The evidence files stay in a session scratchpad and are not committed.
