---
title: 'DW bundle: data-api input validation (rankings/indicators PUT routes)'
type: 'bugfix'
created: '2026-10-05'
status: 'done'
baseline_revision: '4477a72915cb6c7081f08305db1f6287979f5d99'
final_revision: '808cdb1a48e4591c1cb1c9c9177f53ca5da4d8fb'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
warnings: ['multiple-goals', 'oversized']
---

<intent-contract>

## Intent

**Problem:** The two indicator-list PUTs (`PUT /api/coin/{iid}/indicators`, `PUT /api/rankings/technicals-columns`) check indicator names only, so a bad param value (`period: 0`, `pattern: "hammer"`, an unknown `ma_type`, an unknown param key) is saved with 200 and then fails every later values GET (the whole Technicals tab 400s, DW-133/DW-249). Neither PUT declares its body in OpenAPI, so `client.ts`'s request types have no codegen anchor (DW-120). A corrupt `chart_indicators.toml` read by the indicators PUT is answered 400 "invalid indicator config payload", while the layout routes answer the same server-side condition 500 (DW-280).

**Approach:** One shared validator `views.indicator_picker.check_params(name, params)` that refuses what the replay would refuse, by building the indicator exactly as the replay does (native: enum names against `_choices`, then `spec.cls(**resolved)`; custom: a per-spec param check) after rejecting unknown keys and oversized integers; both PUTs call it and answer 400. Declare each PUT's body via `openapi_extra` (keeping the raw-body 400 contract). Map a corrupt/unreadable file in `_store_entries` to 500 like `layout._file_errors`.

## Boundaries & Constraints

**Always:** Validation lives in `views` (one rule, used by both routes and by the layout seed/reset through `check_indicator_entries`); routes stay format + transport. Invalid params are 400 with a message naming the indicator and param. The PUT bodies stay raw-JSON-read (malformed → 400, never 422). Regenerate the committed `platform/frontend/openapi.json`. LGPL headers, ruff, mypy, functions under ~30 lines, cognitive complexity ≤ 10.

**Block If:** a currently shipped picker default (the catalog's own `params`) fails the new validator.

**Never:** No change to `nautilus_trader/` or `crates/`. No change to the chart's per-entry error behaviour on `GET /api/coin/{iid}/indicator-values` (one bad entry must not blank the others). No editing of the deferred-work ledger. No new dependencies.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Valid save | catalog-default params, valid enum names | 200, persisted | none |
| Zero/negative period | `SimpleMovingAverage {period: 0}` on either PUT | 400 naming `period`, file unchanged | `ValueError` → 400 |
| Bad enum value | `CandlePattern {pattern: "hammer"}`, `BollingerBands {ma_type: "NOPE"}`, `ma_type: "ADAPTIVE"`, non-string enum | 400 listing the choices | → 400 |
| Unknown param key | `SimpleMovingAverage {perod: 20}` | 400 naming the key | → 400 |
| Oversized integer | `HullMovingAverage {period: 10**9}` | 400 before any construction | → 400 |
| Bad custom param | `OrderFlowImbalance {window: 0}`, `CancelPressure {window: -1}` | 400 | → 400 |
| OBV default | `OnBalanceVolume {period: 0}` | 200 (0 is its valid default) | none |
| Non-object params | indicators PUT `params: []` | 400 | → 400 |
| Corrupt file on indicators PUT | `chart_indicators.toml` malformed | 500 "chart_indicators.toml is corrupt" | → 500 |

</intent-contract>

## Code Map

- `platform/views/indicator_picker.py` -- `INDICATOR_CATALOG`, `_choices`, `_resolve_enum_params`, `CustomIndicatorSpec`/`CUSTOM_INDICATOR_CATALOG`; home of the new `check_params`.
- `platform/data_api/routes/indicators.py` -- `_parse_config_entry`, `check_indicator_entries` (shared with layout seed/reset), `_store_entries` (DW-280), `put_coin_indicator_config`.
- `platform/data_api/routes/rankings.py` -- `_require_known_indicators`, `put_technicals_columns`, `get_technicals_values`.
- `platform/data_api/routes/layout.py` -- `_file_errors`: the 500 mapping to mirror.
- `platform/frontend/openapi.json` -- committed schema; `data_api/tests/test_app_frontend.py` asserts it equals `app.openapi()`.
- `platform/data_api/tests/test_indicators_config.py`, `test_screener_columns.py`, `platform/views/tests/` -- tests.

## Tasks & Acceptance

**Execution:**
- [x] `platform/views/indicator_picker.py` -- add `check_params(name, params) -> None` raising `ValueError`: params must be a dict; keys ⊆ the spec's default keys; any non-bool `int` > `MAX_INT_PARAM` (10_000, a `Known limit:` comment: protects construction/replay memory, e.g. HMA weights) refused; native: each enum param must be a string in `_choices`, then construct `spec.cls(**_resolve_enum_params(...))`, re-raising `TypeError`/`ValueError`/`OverflowError` as `ValueError` naming the indicator; custom: an optional `check_params` callable on `CustomIndicatorSpec` (CancelPressure/OFI: `window` a positive int; CVD: none). Unknown name raises `ValueError`. -- one rule shared by every writer.
- [x] `platform/data_api/routes/indicators.py` -- `check_indicator_entries` calls `check_params` per entry (400 `invalid params for <name>: ...`) after the name check; `_parse_config_entry` refuses non-dict `params` (TypeError → 400); `_store_entries` maps corrupt load (`TOMLDecodeError`/`UnicodeDecodeError`/`KeyError`/`TypeError`) to 500 "chart_indicators.toml is corrupt" and a load `OSError` to 500; PUT gets `openapi_extra` requestBody `{"type":"array","items":{"$ref":"#/components/schemas/IndicatorConfigEntry"}}`, docstring updated.
- [x] `platform/data_api/routes/rankings.py` -- PUT and `get_technicals_values` validate params via a shared helper (400); PUT gets `openapi_extra` requestBody with `TechnicalsColumn` items.
- [x] `platform/frontend/openapi.json` -- regenerate (`PYTHONPATH=. python3 -m data_api.export_openapi > frontend/openapi.json` from `platform/`); `schema.ts` unchanged (components unchanged; verify with `npm run codegen` diff if node is available).
- [x] tests -- `views/tests` unit tests for `check_params` (every catalog default passes; each matrix error row); route tests for both PUTs (400 + file unchanged), corrupt file → 500 on the indicators PUT, and an OpenAPI test that both PUTs have a requestBody whose `$ref` resolves in `components.schemas`.

**Acceptance Criteria:**
- Given every entry in `merged_catalog()`, when `check_params(name, entry["params"])` runs, then it does not raise.
- Given a bad param saved via either PUT, when the PUT returns, then the status is 400 and the stored file is unchanged.
- Given the generated `openapi.json`, when inspecting both PUT operations, then each has a JSON `requestBody` whose item `$ref` exists in `components.schemas`.

## Spec Change Log

## Review Triage Log

### 2026-10-05 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 5: (high 0, medium 2, low 3)
- defer: 0
- reject: 14: (high 0, medium 0, low 14)
- addressed_findings:
  - `[medium]` `[patch]` An int beyond float range for a float param (`k: 10**400`) made `math.isfinite` raise `OverflowError` → 500; the float branch now compares ints exactly. Test added.
  - `[medium]` `[patch]` Float params had no magnitude bound (`k: 1e308` replays to `inf`, which the values JSON cannot carry); added `MAX_ABS_FLOAT_PARAM = 1e6` under the same `Known limit:`. Test added.
  - `[low]` `[patch]` A constructor that refuses with a non-`TypeError`/`ValueError` exception escaped as a 500; construction now catches broadly (as `values_by_time` does), documented.
  - `[low]` `[patch]` `check_params` docstring overclaimed "exactly what the replay refuses"; reworded with a `Known limit:` (update-time failures still surface per request).
  - `[low]` `[patch]` `MAX_INT_PARAM` comment only justified bar periods; now covers the custom event-count windows. `_parse_config_entry` reads `params` once.

### 2026-10-05 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 3: (high 0, medium 0, low 3)
- defer: 1: (high 0, medium 1, low 0)
- reject: 21: (high 0, medium 0, low 21)
- addressed_findings:
  - `[low]` `[patch]` The integer cap applied to any int value, so a float param sent as an int (`k: 20000`) was refused while `k: 20000.0` passed; the cap now lives in `_check_param_type`'s integer branch, and a float param sent as an int is bounded by the float ceiling. Test added.
  - `[low]` `[patch]` A constructor refusal echoed the whole params dict (`refuses params {...}`), which also made the "names the param" route tests pass trivially; the message now carries only the constructor's own text (which names the param). Test added.
  - `[low]` `[patch]` The new unreadable-file (`OSError`) 500 branch in `_store_entries` had no test; added one.

## Design Notes

Validating by construction (the same `spec.cls(**resolved)` the replay does) keeps the save-time rule identical to the replay-time rule with no per-indicator table to drift: nautilus constructors already enforce `Condition.positive_int` etc. Probed: every native constructor refuses `period` 0/-1/"x"/2.5 except OBV (0 = its unbounded default, correctly accepted); `HullMovingAverage(period=10**9)` takes ~16 s and allocates its weights eagerly, hence the integer cap ahead of construction.

## Verification

**Commands:**
- `cd platform && python3 -m pytest views/tests data_api/tests -q -p no:cacheprovider` -- expected: all pass
- `cd platform && ruff check views data_api && ruff format --check views data_api && mypy views/indicator_picker.py data_api/routes/indicators.py data_api/routes/rankings.py` -- expected: clean


## Auto Run Result

Status: done

**Summary:** This was a follow-up review of the finished bundle. Both indicator-list PUTs (`/api/coin/{iid}/indicators`, `/api/rankings/technicals-columns`) and the technicals-values GET check param values through one rule, `views.indicator_picker.check_params`. It refuses unknown keys, wrong types, out-of-range numbers and enum names outside the choices, then builds the native indicator exactly as the replay does. Both PUTs declare their body in OpenAPI. A corrupt or unreadable `chart_indicators.toml` on the indicators PUT is a 500. The review fixed three small issues:
- The integer cap was also applied to float params sent as an int.
- A constructor refusal echoed the whole params dict back to the client.
- The unreadable-file 500 had no test.

**Files changed (this pass):**
- `platform/views/indicator_picker.py` -- the integer cap moved into `_check_param_type`'s integer branch; a constructor refusal no longer echoes the params.
- `platform/views/tests/test_indicator_picker_params.py` -- tests: a float param sent as an int is bounded by the float ceiling; a constructor refusal names the param without echoing the params.
- `platform/data_api/tests/test_indicators_config.py` -- test: an unreadable stored file on the PUT is a 500.

**Review:** 3 patches applied (all low), 1 deferred, 21 rejected. The deferred item is that the chart's `indicator-values` GET still builds uncapped params. It predates this bundle and is appended to the ledger. Rejected:
- Stored lists that predate the new rules: already listed under residual risks.
- Design choices from the spec: per-entry chart errors, raw-body reads, opt-in custom checks, helpers kept in the route module.
- Undeclared 400/500 responses in OpenAPI and the 422 schema FastAPI lists: the existing pattern across routes.
- `openapi.json` hand-edited: disproved, a fresh export is identical.
- Non-enum string params unchecked: the catalog has none.
- The GET's missing `OSError` branch: predates this bundle, and is a 500 either way.
- A table-valued TOML instrument: it already raises the caught `TypeError`.
- Smaller items: per-poll construction cost, the 1e6 ceiling's rationale, the timing test, a duplicate dict check, an unbounded PUT list size.

**Verification:**
- `python3 -m pytest views/tests data_api/tests -q` with a throwaway redis on 6379: 634 passed.
- `ruff check` and `ruff format --check` are clean on the touched files. Ruff findings elsewhere in `views`/`data_api` are in files this bundle does not touch.
- `mypy views/indicator_picker.py`: its 4 errors are older ones at lines 585–664, outside this change.
- A fresh `export_openapi` is identical to the committed `frontend/openapi.json`.

**Residual risks:**
- Lists saved before the bundle that break the new rules are still served by the GETs. Such a list makes the technicals-values poll 400, the layout seed/reset 500 and its next save 400 until the entry is fixed in the picker.
- The chart values GET can still be asked to build a huge-period indicator (deferred).
