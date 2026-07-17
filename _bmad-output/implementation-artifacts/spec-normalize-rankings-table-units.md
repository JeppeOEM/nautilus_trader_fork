---
title: 'Normalize CVD/Vol d/Spread/u lean units in the dashboard (frontend)'
type: 'feature'
created: '2026-07-17'
status: 'done'
review_loop_iteration: 0
context: []
baseline_commit: 'c8bd7f3e06eca13cb6c35067a4181da7bbbcfd27'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The rankings table (`/`) and coin-detail Indicators table (`/coin/{id}`) show `CVD`/`Vol d` as raw base-asset-token deltas and `Spread`/`u lean` as raw price-unit differences — neither scaled by price, so identical real-world flow/spread looks huge for a sub-cent coin and tiny for BTC, with no comparability across rows or between the two views.

**Approach:** Backend stays raw — no new computed fields, no changes to `_LIVE_FAST`, `RANKING_COLS`'s existing format/color functions, or anything persisted. The rankings table already ships each row's raw value *and* raw `price` to the browser; the coin-detail panel needs one line added (expose the already-computed `price` field it currently omits). All actual normalization (CVD/Vol d → USD notional; Spread/u lean → basis points of price) happens in shared JS helpers in the dashboard's existing inline `<script>` block, applied identically on both views. `/history/{id}` and `/live` are unchanged (confirmed with the user — `/history` is server-rendered Plotly with no client-side data layer, a bigger change out of scope for now).

## Boundaries & Constraints

**Always:** No backend Python change beyond adding `"price"` to `live_coin_json_handler`'s `ind_keys`. Normalization math lives in shared JS functions (`usdFromTokens`, `bpsFromPriceUnits`, plus formatters) used by both `renderRankings()` and `renderCoin()` — not duplicated. Guard every normalization against `price` being `null`/`<= 0` → render `—`. Preserve the rankings table's existing `"!"` row-error marker untouched (must still show `!`, never fall through to a bogus normalized value). Reuse each cell's existing server-computed `.color` (unaffected by a positive linear rescale) rather than recomputing color in JS.

**Ask First:** none remaining — scope (rankings + coin-detail only, `/history`/`/live` excluded) and units (USD, bps) were confirmed with the user before finalizing this spec.

**Never:** Do not touch `metrics_store.py`, `metrics_computer.py`, `_render_history_page`, or any persisted value's meaning. Do not touch `_render_live_page`/`_SERIES` (an unrelated Plotly signals page that doesn't display these values).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Normal CVD (rankings) | cell.raw=-1_200_000, price.raw=0.02 | text = "-24,000" | N/A |
| Normal spread (rankings) | cell.raw=1.5, price.raw=60000 | text = "+0.25" | N/A |
| Missing price | cell.raw=500, price.raw=null | text = "—" | N/A |
| Zero/negative price | cell.raw=1.5, price.raw=0 | text = "—", not `Infinity` | N/A |
| Row fetch error (rankings) | cell.text="!" (existing marker) | still shows "!" in error color | N/A |
| Coin-detail panel | ind.cvd=-500, ind.price=0.01 | Indicators row shows "-5" | N/A |

</frozen-after-approval>

## Code Map

- `troll/ml_signals/dashboard.py:232-246` -- JS `COLS`/`IND` arrays -- add unit-suffixed labels for the 4 affected keys
- `troll/ml_signals/dashboard.py` (shared `<script>` block, near `fmtP` at :286) -- add shared normalization/formatting helpers
- `troll/ml_signals/dashboard.py:400-421` -- `renderRankings()`'s `COLS.map` cell builder -- special-case the 4 keys
- `troll/ml_signals/dashboard.py:471-478` -- `renderCoin()`'s `IND.map` row builder -- same special-casing
- `troll/ml_signals/dashboard.py:1116-1126` -- `live_coin_json_handler`'s `ind_keys` -- add `"price"`

## Tasks & Acceptance

**Execution:**
- [x] `troll/ml_signals/dashboard.py` -- add `"price"` to `live_coin_json_handler`'s `ind_keys` -- the coin-detail panel's JSON currently omits price, leaving the frontend nothing to normalize against there
- [x] `troll/ml_signals/dashboard.py` -- add shared JS helpers `usdFromTokens(raw,price)`, `bpsFromPriceUnits(raw,price)`, `fmtUsd(v)`, `fmtBps(v)` (each returns `null`/`—` when `price` is `null`/`<= 0` or `raw` is `null`)
- [x] `troll/ml_signals/dashboard.py` -- update `renderRankings()`'s cell builder: for `cvd`/`volume_delta` compute `usdFromTokens` against `row.cells['price'].raw`, for `spread`/`microprice_lean` compute `bpsFromPriceUnits`; keep the existing `"!"` error branch untouched; reuse the cell's existing `.color`
- [x] `troll/ml_signals/dashboard.py` -- update `renderCoin()`'s `IND.map` with the equivalent special-casing against `ind['price']`
- [x] `troll/ml_signals/dashboard.py` -- update the four affected `COLS`/`IND` labels to include units (e.g. "CVD($)", "Spread(bps)", "u lean(bps)", "Vol d($)")

**Acceptance Criteria:**
- Given the rankings table with a low-price coin's raw cvd=-1_200_000 and price=0.02, when rendered, then the CVD cell shows a small USD figure (~-24,000), not the raw six-figure token count.
- Given the coin-detail Indicators table for the same coin, when rendered, then CVD/Vol d/Spread/u lean show the same normalized units as the rankings table.
- Given a row with missing or non-positive price, when either table renders, then the affected cells show "—", never `Infinity`/`NaN`.
- Given a rankings row with the existing fetch-error marker, when rendered, then it still shows "!", not "—" or a bogus number.
- Given `/history/{id}` and `/live`, when rendered after this change, then their output is unchanged.

## Design Notes

Pure client-side JS with no backend computation change (beyond the one-line `ind_keys` addition, which is trivial glue -- no unit test needed per this project's TEST-02 convention). Verification is manual since there's no JS test harness in this repo.

## Verification

**Commands:**
- `docker compose -f troll/docker-compose.yml run --rm --no-deps collector python3 -m pytest ml_signals/tests/test_dashboard_rankings.py ml_signals/tests/test_dashboard_ingest.py -q` -- expected: no regression (backend logic/keys otherwise untouched)

**Manual checks (if no CLI):** (see below for what was actually verified)

## Suggested Review Order

**Normalization core**

- Start here: the math, guarded against non-finite input and the "-0"/"+0" sign edge case a reviewer's edge-case pass caught.
  [`dashboard.py:308`](../../troll/ml_signals/dashboard.py#L308)

- Shared sign-safe formatter — rounds at display precision *before* deciding the sign prefix.
  [`dashboard.py:323`](../../troll/ml_signals/dashboard.py#L323)

- Single dispatch point used by both render functions below — the fix for a reviewer-flagged duplication risk.
  [`dashboard.py:345`](../../troll/ml_signals/dashboard.py#L345)

**Render call sites**

- Rankings table cell builder — note the `"!"`/`"ERR"` marker check *before* normalization (a reviewer caught this ordering bug).
  [`dashboard.py:464`](../../troll/ml_signals/dashboard.py#L464)

- Coin-detail Indicators table — same normalization, different raw-value shape (no error-marker concept here).
  [`dashboard.py:541`](../../troll/ml_signals/dashboard.py#L541)

**Backend plumbing (the only non-JS change)**

- One field added so the coin-detail panel has what it needs to normalize against.
  [`dashboard.py:1189`](../../troll/ml_signals/dashboard.py#L1189)

**Documentation**

- Unit contract for anyone hitting the JSON endpoints directly, since normalization now lives only in the embedded JS.
  [`dashboard.py:96`](../../troll/ml_signals/dashboard.py#L96)

- USD/BPS key lists — comment warns against ever adding `"price"` itself (self-reference footgun a reviewer flagged).
  [`dashboard.py:251`](../../troll/ml_signals/dashboard.py#L251)
- Rebuild (`make build-insecure`), run the dashboard, open `/` and a `/coin/{id}` page for both a high-price coin (e.g. BTC) and a low-price coin; confirm CVD/Vol d/Spread/u lean show small, comparable, unit-labeled numbers consistently across both views; confirm a row in an error state still shows "!"; confirm `/history/{id}` and `/live` are visually unchanged.

**What was actually verified this run:** rebuilt the `dashboard`/`collector` images and ran the full existing dashboard suite (`test_dashboard_rankings.py`, `test_dashboard_ingest.py`, `test_dashboard_chart.py`, `test_dashboard_metrics.py`) — 55 passed, no regression. Extracted the new inline JS and ran it under Node in isolation against every I/O-matrix scenario (`node --check` for syntax, then direct calls to `usdFromTokens`/`bpsFromPriceUnits`/`fmtUsd`/`fmtBps` and a simulated cell-builder) — all match the spec's expected outputs, including the FLOKI-shaped example (cvd=-1,200,000 @ price=0.02 → "-24,000"; a synthetic 2%-wide book → spread renders "+200.00" bps, correctly proportional). Also injected a synthetic batch directly into the running `dashboard` container's `_ingest_batch()` and confirmed `_rankings_json()` still emits the same raw `cvd`/`spread`/`price` values the frontend needs — backend truly unchanged. Full browser-rendered visual check (real BTC vs. a real low-price coin side by side) was not possible in this sandbox: the collector has no outbound network access here (TLS/cert failures on dYdX's indexer), so no live rows ever reach the dashboard. Recommend a quick visual confirmation once deployed with real data.
