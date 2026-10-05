---
title: 'DW bundle capture-script-and-docs: generic WS capture branch, stale in-app docs, gap-marker pre-rollout check'
type: 'chore'
created: '2026-10-05'
status: 'done'
final_revision: '6912dac7ebc7176af0ff6a62c29c5a8430fc56c7'
baseline_revision: 'cffe382014c51cca3f822efc9a3424bfb79131b2'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
warnings: ['multiple-goals']
---

<intent-contract>

## Intent

**Problem:** Five deferred-work entries (DW-169, DW-172, DW-221, DW-239, DW-182): the raw-frame capture harness `platform/scripts/capture_hl_ws.py` cannot capture a venue it does not know (closed `--venue` choices, hard two-way subscribe branch), so "Adding a venue" step 1 has no usable tool; the in-app knowledge base (`kbData.ts`) and `DATA_DICTIONARY.md`/`data.ts` still describe the retired dashboard, a dYdX-only collector, a TUI that reads rankings, a deleted constant `_CROSSED_RESYNC_NS` as if live, a footprint chart that no longer exists, and a dYdX `[[instruments]]` example with `bar_intervals`, a key the strict loader rejects; and the rollout checklist has no check that already-deployed `_archive_gaps/*.jsonl` lines still decode under Story 23.2's stricter `decode` (an inverted span written by the pre-fix `record_gap` now refuses that instrument's rebuild).

**Approach:** Add an explicit `--venue other` branch to the capture script driven by `--url` plus repeatable literal `--subscribe JSON` payloads (and a generic summary for frames it does not recognise), update the stale doc text to the current three-venue, web-only-rankings, `data_api` reality while keeping clearly historical postmortem/changelog prose historical, and add a DEPLOY_CHECKLIST "Deferred operator actions" entry that decodes every gap-marker line on the VPS and says how to repair an inverted one.

## Boundaries & Constraints

**Always:** `--venue other` refuses to start (argparse error, exit 2) without `--url` and at least one `--subscribe`; `--subscribe` is parsed as JSON at argument time and a non-JSON value is an argparse error naming it; Hyperliquid and Bybit default behaviour (URLs, payloads, summaries) stays byte-identical when the new flags are absent; `--subscribe` given with `hyperliquid`/`bybit` replaces that venue's default payloads (documented in the module docstring, which also documents Bybit spot via `--url .../public/spot`). Doc edits state only facts verified in the code. The gap-check command runs read-only inside an existing image and prints every bad line with file and line number, exiting non-zero when any is found. Inverted-line repair = swap `from_ns`/`to_ns` (what the post-23.2 `record_gap` writes), keeping key order.

**Block If:** none expected.

**Never:** edit `_bmad-output/implementation-artifacts/deferred-work.md`; touch `nautilus_trader/` or `crates/`; rewrite historical postmortem/changelog entries (pm-crossed-book, pm-nifelheim, the 2026-09-13 relabel entry, the stale-book postmortem) beyond a factual "(now X)" pointer; add a new archive tool for the gap check (the ledger asks for a checklist step); template `{coin}` into `--subscribe` payloads.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Known venue default | `--venue bybit --coin BTCUSDT` | Bybit linear URL + the existing subscribe payload | none |
| Generic venue | `--venue other --url wss://x --subscribe '{"op":"sub"}'` | that URL, that payload | none |
| Generic missing url/payload | `--venue other --url wss://x` | argparse error, exit 2 | message names the missing flag |
| Bad JSON | `--subscribe '{bad'` | argparse error, exit 2 | names the value |
| Override known venue | `--venue hyperliquid --subscribe '{...}'` | HL URL, only the given payload | none |
| Summary of unknown frames | JSONL whose frames are neither Bybit `topic` nor HL `channel` (incl. non-object frames) | generic summary: frame count + arrival-gap median/min/max | no crash on list/str frames |

</intent-contract>

## Code Map

- `platform/scripts/capture_hl_ws.py` -- the harness: `capture()` branch, `summarize()` detection, argparse.
- `platform/CLAUDE.md` -- "Adding a venue" step 1 says the script cannot be pointed at a new venue; must now describe `--venue other`.
- `platform/frontend/src/pages/docs/kbData.ts` -- architecture caption (:8), "One paragraph" (:9), Redis channel table readers (:19-20), dYdX example (:49-50), "Run the dashboard"/tunnel text (:43, :51-52), staleness banner (:73), `make remote-web` (:114), `_CROSSED_RESYNC_NS` (:181), `dashboard._coin_chart_json` (:211), docs-page tagline (:238).
- `platform/frontend/src/pages/docs/data.ts` -- footprint entry (:276, :282) claims a dashboard footprint chart; `build_footprint` has no non-test caller.
- `platform/docs/DATA_DICTIONARY.md` -- §2.4 footprint consumer (:2359), §3.4 "dashboard's per-coin history page" (:2894), §3.5 "web dashboard's coin-picker" (:2911); :2236 quotes the epic, keep.
- `platform/docs/DEPLOY_CHECKLIST.md` -- "Deferred operator actions" (end of file) gets the DW-182 entry.
- `platform/kernel/archive_markers.py` -- `decode` (refuses malformed/inverted), `GAPS_DIRNAME`.
- `platform/tests/_source_tree.py` -- `PLATFORM_DIR` for loading the script in tests.

## Tasks & Acceptance

**Execution:**
- [x] `platform/scripts/capture_hl_ws.py` -- add `other` to `--venue` choices, repeatable `--subscribe` (type = JSON parser raising `argparse.ArgumentTypeError`), a pure `subscribe_plan(venue, coin, url, subs) -> (url, subs)` used by `capture()`, validation via `ap.error`, generic summary + detection that tolerates non-dict frames, docstring usage lines; type-hint the signatures -- DW-169.
- [x] `platform/tests/test_capture_ws_script.py` -- load the script by path (`PLATFORM_DIR / "scripts"`), test `subscribe_plan` for the matrix rows, the argparse refusals (exit 2), and the generic summary on mixed frames -- covers the I/O matrix.
- [x] `platform/CLAUDE.md` -- rewrite the step-1 sentence about the harness to the new `--venue other --url --subscribe` usage -- keep the rule accurate.
- [x] `platform/frontend/src/pages/docs/kbData.ts` -- the edits listed in the Code Map (one paragraph: three venues, web UI via `data_api` reads `rankings:live`/`snapshots:raw`, `bot_tui` reads only bot/collector/archive status and `markets:live`); drop `bar_intervals` -- DW-172, DW-221, DW-239.
- [x] `platform/frontend/src/pages/docs/data.ts` -- footprint `shownIn`/note: no current consumer (the retired dashboard's chart was not ported).
- [x] `platform/docs/DATA_DICTIONARY.md` -- the three stale lines -- DW-172.
- [x] `platform/docs/DEPLOY_CHECKLIST.md` -- new "Deferred operator actions" entry `DW-182 archive-gap markers decode under the strict reader` with a `docker compose run --rm --no-deps archive python3 -c ...` check and the swap repair -- DW-182.

**Acceptance Criteria:**
- Given the edited tree, when grepping `kbData.ts` for `bar_intervals` and `terminal UI both read`, then nothing matches.
- Given `--venue other`, when run without `--subscribe`, then the process exits 2 before any network I/O.
- Given the checklist command run against a catalog holding one inverted marker line, then it prints that file:line and exits 1; against valid markers (or no `_archive_gaps` dir) it prints a count and exits 0.

## Verification

**Commands:**
- `cd platform && python3 -m pytest tests/test_capture_ws_script.py -q` -- expected: pass.
- `cd platform && ruff check scripts/capture_hl_ws.py tests/test_capture_ws_script.py && ruff format --check scripts/capture_hl_ws.py tests/test_capture_ws_script.py` -- expected: clean.
- `cd platform/frontend && npx tsc --noEmit -p . && npx vitest run src/pages/DocsPage.test.tsx` -- expected: pass.
- The checklist's python one-liner run locally against a temp catalog with a good and an inverted marker -- expected: exit 1 naming the inverted line; exit 0 with only good lines.

## Review Triage Log

### 2026-10-05 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 13: (high 0, medium 4, low 9)
- defer: 1: (high 0, medium 0, low 1)
- reject: 14: (high 0, medium 0, low 14)
- addressed_findings:
  - `[medium]` `[patch]` a failed connect left an empty `--out` file that blocked the retry as an overwrite: `capture()` now removes the file when no frame was written and re-raises.
  - `[medium]` `[patch]` `--subscribe` was promised "sent verbatim" but went through `json.loads`/`json.dumps` (`0.10` -> `0.1`, duplicate keys collapsed): `_json_payload` now validates and returns the exact text (a JSON string's decoded content), sent with `send_str`.
  - `[medium]` `[patch]` the DW-182 check passed as "0 checked" when the catalog root was not mounted: it now fails naming the path.
  - `[medium]` `[patch]` the repair step forbade only `make nightly`/`consolidate`, but `make prune` appends `pruned` markers to the same files: now named.
  - `[low]` `[patch]` a refused send (socket closed by the venue) escaped as a traceback: `_pump` reports it as an early end with the close code.
  - `[low]` `[patch]` `--seconds nan`/`inf` passed validation: now refused as not finite.
  - `[low]` `[patch]` `--coin ''` silently became `BTC`: now refused.
  - `[low]` `[patch]` `--summarize` silently ignored `--coin`/`--url`/`--subscribe`: now refused as capture-only flags.
  - `[low]` `[patch]` a valid-JSON non-object line, or a row without an integer `recv_ns`, crashed the summary: `read_capture` counts it as unparseable.
  - `[low]` `[patch]` an empty first trades frame (`data: []`) raised `IndexError` in both venue summaries: reported as "0 trades (nothing replayed)".
  - `[low]` `[patch]` the Hyperliquid summary printed nothing for a capture with no trades and under two `l2Book` frames: it now says so.
  - `[low]` `[patch]` the checklist heading carried a placeholder commit reference: now `3f8328d048`.
  - `[low]` `[patch]` the architecture caption called the collectors the "only writers" of the catalog although `archive` consolidates/prunes/rebuilds it: reworded to "only sources", naming what `archive` does.

## Auto Run Result

**Summary:** Follow-up review pass of the bundle that resolved DW-169, DW-172, DW-221, DW-239 and DW-182 (`3f8328d048`). It hardened the capture harness (exact-text `--subscribe`, empty-file cleanup on a failed connect, refused sends reported as an early end, finite `--seconds`, non-empty `--coin`, capture-only flags refused with `--summarize`, summaries robust to non-row lines and empty replay frames) and the DW-182 checklist entry (refuses a missing catalog root, names `make prune`, real commit hash). It also corrected the architecture caption's "only writers" claim.

**Files changed:**
- `platform/scripts/capture_hl_ws.py` -- review patches listed in the triage log; docstring matches the exact-text and cleanup behaviour.
- `platform/tests/test_capture_ws_script.py` -- 35 tests (10 new) covering every patched path; `_FakeWs` can refuse a send.
- `platform/docs/DEPLOY_CHECKLIST.md` -- DW-182 entry: catalog-root guard, `make prune` forbidden during repair, commit hash.
- `platform/frontend/src/pages/docs/kbData.ts` -- architecture caption wording.

**Review:** 13 patches applied (4 medium, 9 low), 1 deferred (the KB Redis channel table misses five live channels; appended to the ledger), 14 rejected: `decode` already catches `TypeError`/`KeyError`; `load_gaps` uses the same `splitlines` numbering; the swap repair is the spec's mandated repair; Bybit's `BTC` default coin and wall-clock `recv_ns` are pre-existing behaviour that must stay byte-identical; ack checking, detection on look-alike frames, the closed venue-suffix map, the `_CSS` body (historical narrative), the 10s default note (factual), the footprint `Known limit:` request and the check-then-open race were judged noise for a manual dev harness.

**Verification:** `python3 -m pytest tests/test_capture_ws_script.py -q`: 35 passed. ruff check/format and mypy are clean on both .py files. `npx tsc --noEmit` exits 0, and `DocsPage.test.tsx` has 4 passed. A real CLI run against `ws://127.0.0.1:1` raised `ClientConnectorError` and left no `--out` file. The checklist's check block was extracted and run against temp catalogs: valid marker gave exit 0; an inverted line was named with file:line, exit 1; a missing catalog root failed naming the path, exit 1; no `_archive_gaps` gave 0 checked, exit 0.

**Residual risks:** the DW-182 check still has to run on the VPS (owed in DEPLOY_CHECKLIST); the harness still sends no application-level ping and does not inspect subscribe acks, so a rejected payload is visible only by reading the captured frames.
