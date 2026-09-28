---
title: 'Story 29.3: Venue cutover: Bybit BTCUSDT/ETHUSDT and Hyperliquid SOL proven, then dYdX stopped'
type: 'chore'
created: '2026-09-28'
status: 'done'
baseline_revision: '6d7d159345d147ede16f7e259d8585afd0e410d4'
final_revision: '9deba3df25719f4e789c7f7843d0d838da91ddbb'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-29-context.md'
warnings: [oversized]
---

<intent-contract>

## Intent

**Problem:** The operator is moving collection off dYdX. Bybit keeps `BTCUSDT`/`ETHUSDT` linear and spot, and Hyperliquid moves from BTC/ETH to `SOL`. Today every `make up` starts the dYdX `collector` service, and `redeploy-all` restarts it by name. The docs and KB use `.DYDX` ids as their generic example, and nothing tells the operator how to prove the new venues before stopping dYdX.

**Approach:**
- Change Hyperliquid's committed plan to `SOL-USD-PERP.HYPERLIQUID`.
- Gate the dYdX compose service behind `profiles: ["dydx"]`, with explicit `make up-dydx`/`down-dydx`, and take it out of every default start/restart target.
- Swap the generic `.DYDX` examples for `BTCUSDT-LINEAR.BYBIT`.
- Add a DEPLOY_CHECKLIST §8 "Venue cutover" runbook, plus a Deferred operator actions entry. The operator runs the runbook later; the story finalizes `done`.

## Boundaries & Constraints

**Always:**
- The archive is never deleted by the cutover. `archive/config.toml` keeps `DYDX` in `venues`, so the nightly saga keeps verifying and pruning dYdX days. The checklist states when `DYDX` may be dropped: once `verified_days` holds no dYdX day younger than the trade retention.
- `ranking`'s dYdX volume poll stays. A stopped venue's rows age out as stale, then vanish (DATA-01), and are never hidden early.
- Bybit keeps its four ids. Its config comment says why both markets stay: linear carries mark, funding and OI; spot carries the spot book.
- Hyperliquid's comment records the 2026-09-26 decision (SOL on Hyperliquid, BTC/ETH on Bybit; reversible by config). It also says the existing `hold_back_seconds`/`stale_book_seconds` measurements came from BTC/ETH/PURR and that the owed D-63 VPS run covers SOL.
- Frozen names stay: the service `collector`, container `dydx-collector`, env vars, and bind-mount paths.
- `make` targets that `run` the `collector` service (test, nightly, consolidate, prune, build-candles, hotpath-baseline) keep working. Compose auto-enables the profile of an explicitly targeted service; this was verified with Compose v5.1.3.
- `make down-dydx` removes the container (`rm -sf`) instead of only stopping it. A stopped `restart: always` container is restarted when the Docker daemon starts, so a mere stop would bring dYdX back on reboot.
- MR4: docs and the Makefile change in the same commit.

**Block If:**
- Gating dYdX would need a renamed service, env var or mount path, or a change under `nautilus_trader/` or `crates/`.

**Never:**
- No catalog or candle-store deletion, and no edit to `data/dydx_config.toml`.
- No code-default changes in `research/` (argparse or function defaults, notebook parameters, `scripts/bench_candles.py`). These are behaviour over the retained dYdX archive, not example strings.
- Do not park the story `awaiting-operator`, and write no `operator_actions:` (OPS-01). Never write `sprint-status.yaml`.
- `.DYDX` ids stay where the text is about dYdX: the dYdX plan-file samples, the bots config, the dYdX adapter code, the width examples, and the id-format lists.

</intent-contract>

## Code Map

- `platform/capture/venues/hyperliquid/config.toml`, `platform/capture/venues/bybit/config.toml` -- the committed static plans.
- `platform/docker-compose.yml:44-94` -- the dYdX `collector` service. `live-paper` (:339) and `bot_tui` (:285) show the profile pattern.
- `platform/Makefile` -- `up`, `redeploy`, `redeploy-all`, `redeploy-no-paper`, `frontend-dev`, `logs`, and `.PHONY`.
- `platform/archive/config.toml` -- `venues = ["DYDX", "BYBIT", "HYPERLIQUID"]`, the scheduler's nightly list. It replaced the host cron line (DEPLOY_CHECKLIST §1).
- `platform/tests/test_images.py` -- the indentation-based compose parser and `_makefile_recipes()`. The image has no YAML library.
- `platform/docs/DEPLOY_CHECKLIST.md` -- numbered sections (§7 is reserved for Story 28.1) and "Deferred operator actions" (:481; the 29-1 and 29-2 entries show the format).
- `platform/docs/DATA_DICTIONARY.md` §1 (:18) -- gains the collected-set table.
- Generic `.DYDX` examples to change:
  - `frontend/src/pages/AlertsPage.tsx:12` (comment).
  - `frontend/src/pages/docs/kbData.ts:57` and `:73`. The stale-feed banner example becomes `SOL-USD-PERP.HYPERLIQUID`, a collected id.
  - `README.md:192-193`.
  - `candles/rebuild.py:20` and `archive/rebuild_seconds.py:20` (usage docstrings: the id and `--venue BYBIT`).
  - `research/run_backtest.py:19` (usage docstring only).
  - `research/BACKTESTING.md:24`.
- `platform/README.md` (:5, :23 make table, :93, :142, :180) and `platform/CLAUDE.md` "Adding a venue" (c) (:100) -- describe which collectors `make up`/`redeploy-all` start.

## Tasks & Acceptance

**Execution:**
- [x] `platform/capture/venues/hyperliquid/config.toml` -- Set `instruments = ["SOL-USD-PERP.HYPERLIQUID"]` and add the decision and measurement comment. -- AC 1.
- [x] `platform/capture/venues/bybit/config.toml` -- Keep the four ids and add the why-both-markets comment. -- AC 1.
- [x] `platform/capture/venues/{hyperliquid,bybit}/tests/test_committed_config.py` -- Load the committed file through the venue's own loader, the one `build_capture_from_file` uses. Assert the exact instrument tuple. -- This guards the committed plan.
- [x] `platform/docker-compose.yml` -- Add `profiles: ["dydx"]` to `collector`, with a comment (why, `make up-dydx`/`down-dydx`, same pattern as `live-paper`). -- AC 2.
- [x] `platform/Makefile` -- Make these changes:
  - Add `up-dydx` (`$(COMPOSE) --profile dydx up -d --build collector`) and `down-dydx` (`$(COMPOSE) --profile dydx rm -sf collector`, with the reboot reason in its comment).
  - Drop `collector` from `redeploy-all`.
  - `frontend-dev` starts `bybit_collector hyperliquid_collector` instead of `collector`.
  - `logs` tails all three collectors.
  - Update the comments on `up`, `redeploy-all` and `frontend-dev`, and extend `.PHONY`.
  - AC 2.
- [x] `platform/archive/config.toml` -- Comment that `DYDX` stays until its days age out, pointing to DEPLOY_CHECKLIST §8. -- AC 2.
- [x] `platform/tests/test_compose_profiles.py` -- New tests, with no YAML dependency:
  - `collector` carries `profiles: ["dydx"]`.
  - `bybit_collector`, `hyperliquid_collector` and `archive` carry no profile.
  - `up-dydx` and `down-dydx` target `collector` under `--profile dydx`, and `down-dydx` uses `rm -sf`.
  - No recipe of `up`, `redeploy`, `redeploy-all`, `redeploy-no-paper` or `frontend-dev` names `collector` in an `up` command.
  - `archive/config.toml`'s `venues` still contains `DYDX`.
  - AC 2 regression guard.
- [x] Generic examples (the Code Map list) -- Replace the `.DYDX` example with `BTCUSDT-LINEAR.BYBIT` (the stale-feed banner uses the Hyperliquid id). Leave dYdX-specific text alone. -- AC 2.
- [x] `platform/docs/DATA_DICTIONARY.md` §1 -- Add a collected-set table per venue: ids, market, and what each yields (Bybit spot gives trades and book only). Note that dYdX's set is operator data, gated by the `dydx` profile. -- AC 1.
- [x] `platform/docs/DEPLOY_CHECKLIST.md` -- Make these changes:
  - Add §8 "Venue cutover", before "Deferred operator actions". It lists the seven ordered checks from the epic AC, each with its command or URL and the expected result.
  - Add a `DYDX` retirement note: when to remove `DYDX` from `archive/config.toml` `venues`.
  - Add a line recording the date and the last dYdX day collected.
  - Append a `### 29-3 Venue cutover (commit: this story's)` entry of unchecked items that point to §8.
  - AC 3.
- [x] `platform/README.md`, `platform/CLAUDE.md` (:100 (c)), `platform/docs/BOT_OPERATIONS.md` (only if it says `make up` starts dYdX) -- Describe the profile: `make up` starts Bybit and Hyperliquid only, and dYdX runs through `make up-dydx`. Add both targets to the README make table. -- MR4.

**Acceptance Criteria:**
- Given the committed configs, when the loaders read them, then Hyperliquid collects exactly `SOL-USD-PERP.HYPERLIQUID` and Bybit its four ids, and the existing `ranking/` and `views` tests pass unchanged.
- Given a fresh `make up`, when compose starts the default services, then `collector` (dYdX) is not started. `make up-dydx` starts it, `make down-dydx` removes it, and `redeploy-all`/`redeploy-no-paper` never touch it. The archive's nightly list still includes `DYDX`.
- Given `docs/DEPLOY_CHECKLIST.md`, when read, then §8 lists the seven ordered checks with commands and expected results, and a 29-3 Deferred operator actions entry points to it.
- Given the generic doc, KB and example strings, when searched for `.DYDX`, then only dYdX-specific text keeps it.

## Spec Change Log

## Review Triage Log

### 2026-09-28 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 15: (high 1, medium 4, low 10)
- defer: 0
- reject: 6: (high 0, medium 0, low 6)
- addressed_findings:
  - `[high]` `[patch]` Compose tags one image per service, and `make test`, `nightly`, `consolidate`, `prune`, `build-candles` and `hotpath-baseline` all `run` the `collector` image. `up` and `redeploy-all` had been its only builders, so without a fix those targets would have run stale code. Both now run `$(COMPOSE) build collector`, which builds the image without starting the service (verified on Compose v5.1.3). Tested.
  - `[medium]` `[patch]` Reconciliation compares the venue's whole UTC day, so a partially collected day fails. That covers day D for SOL and for Hyperliquid BTC/ETH, and the down day for every `.DYDX` id. §8 gained a "Partial days" paragraph explaining the expected failures and how to confirm them from `data/errors/archive.jsonl`, plus a `Known limit:` (failed days keep their raw trades; the upgrade path is a per-instrument collection window).
  - `[medium]` `[patch]` "dYdX's archive ages out" was false. Only raw trades (verified, older than 7 days) and opted-in deltas (14 days) age out; snapshots, mark/index, funding, OI and instrument definitions are permanent. §8's intro, the Hyperliquid config comment and the retirement text now say so.
  - `[medium]` `[patch]` The retirement check used `max(day)` from `verified_days`, which could pass early. It now uses the recorded last dYdX day `L`: today must be more than `L`+8, with no `unverified` kept lines, and every `failed` day either `L` or root-caused. It also guards against a missing store.
  - `[medium]` `[patch]` Check 4's baseline window overlapped the post-redeploy hour. The pre-redeploy count is now computed as the difference of the two `since_ns` queries.
  - `[low]` `[patch]` Check 5 read `reconcile.*` site counts that the `DYDX` saga shares. It now greps `archive.jsonl` by day and by Bybit/Hyperliquid id.
  - `[low]` `[patch]` Check 2 now expects a volume for all five ids, since Bybit's source covers USD-quoted spot too.
  - `[low]` `[patch]` Check 2 now tolerates the 503 that `/api/rankings` returns before the first `rankings:live` message.
  - `[low]` `[patch]` Check 7 now checks `COMPOSE_PROFILES` in `.env` and inspects the containers after the reboot, before running `make up` and again after it.
  - `[low]` `[patch]` `test_compose_profiles.py` now strips Make's `@`/`-` prefixes and also rejects `start`/`restart`/`create` of `collector`.
  - `[low]` `[patch]` Two stale Makefile comments were fixed: `redeploy`'s "reach for `make up`" and `hotpath-baseline`'s "`make up` / `make build`".
  - `[low]` `[patch]` The Hyperliquid config comment's restart advice was `make redeploy-all`. It is now `docker compose restart hyperliquid_collector`, and the comment names the test that pins the set.
  - `[low]` `[patch]` The README and KB "Configure instruments" sections still presented dYdX as the default. They now say dYdX runs only after `make up-dydx`, and point to the Bybit/Hyperliquid files.
  - `[low]` `[patch]` SOL's lag had no tracked measurement. The 29-3 deferred entry now owes SOL in the D-63 Hyperliquid lag run, and also the partial-day confirmation.
  - `[low]` `[patch]` Check 6 now notes that the TUI's DYDX section turns stale and its actions have no consumer.
- rejected:
  - `make down` leaving a profiled container: verified that plain `down` removes it.
  - `build-insecure` building only `collector`: a pre-existing gap, already documented in `platform/CLAUDE.md`.
  - `profiles:` string-match fragility: it fails loudly, not silently.
  - `run_backtest` argparse default and example dates: code defaults are out of scope per the spec's Never.
  - live-paper bots on dYdX: bots use their own adapter, independent of the collector.
  - `research/README.md` `INSTRUMENTS`: it documents a code default.

### 2026-09-28 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 11: (high 0, medium 3, low 8)
- defer: 1: (high 0, medium 1, low 0)
- reject: 12: (high 0, medium 0, low 12)
- addressed_findings:
  - `[medium]` `[patch]` §8's intro said opted-in deltas go "after 14 days" and called snapshots, mark/index, funding, OI and definitions permanent without a condition. Delta retention is actually the dYdX plan's per-coin `retain_hours` (unset: kept). The `DYDX` saga's plan retention also deletes every non-trade type of a `.DYDX` id the plan no longer lists, once it is older than `non_config_retain_hours` (4 h in the plan). Rewritten against `archive/prune_catalog.py`, with an explicit "never trim the dYdX plan" warning.
  - `[medium]` `[patch]` The retirement check allowed dropping `DYDX` at `L`+9, before a finite delta `retain_hours` longer than that had run. The `DYDX` saga is the only job that prunes `.DYDX` files. Condition 1 is now `max(8, R + 1)` days, where `R` is the plan's largest finite window.
  - `[medium]` `[patch]` Found by verification, not by the reviewers: `tests/test_legacy_names.py` failed on two committed test names containing `dydx_collector`. The prior pass ran while the file was untracked, and the guard uses `git grep`. Both test functions were renamed.
  - `[low]` `[patch]` The "Partial days" Known limit named only the `.DYDX` resolution. It now says that Hyperliquid BTC/ETH day D's `kept ... failed` lines recur nightly and have no clearing step.
  - `[low]` `[patch]` `make up`/`redeploy-all` built the `collector` image after replacing the running services. They now build it first, so a failed build stops the target before anything is replaced.
  - `[low]` `[patch]` Check 7 checked `COMPOSE_PROFILES` only in `.env`. It now checks the shell environment too.
  - `[low]` `[patch]` Check 4 printed `None` for a service with no ledger file, easy to misread as clean. It now prints `MISSING` and says that this fails the check.
  - `[low]` `[patch]` Check 5's sqlite loop aborted on a missing candle store. It now prints `MISSING` per venue.
  - `[low]` `[patch]` Check 2's second curl piped an empty body into `json.load`, which gave a traceback. It now fetches once and parses only a non-empty body.
  - `[low]` `[patch]` `test_compose_profiles.py` now fails any default-target recipe line mentioning `dydx` (`--profile=dydx`, a `COMPOSE_PROFILES=` prefix, a raw `docker compose`, `$(MAKE) up-dydx`). A mutation check confirmed it fails on an injected `COMPOSE_PROFILES=dydx` prefix.
  - `[low]` `[patch]` The Hyperliquid `config.toml` comment line of 114 characters was wrapped.
- deferred: `bot_tui`'s `command_refusal` ignores `plan_is_stale`, so DYDX commands are published with no consumer after `make down-dydx` (pre-existing; ledgered in deferred-work.md).
- rejected: `build-insecure` listing only `collector` (pre-existing, rejected before); `run_backtest`/`_params.py` dYdX defaults (the spec's Never); gating on the SOL lag measurement (the spec defers it to D-63); change-detector config tests (the spec requires them); `make down` and profiled containers (verified before); YAML parser brittleness (fails loudly); a third build's load (builds are sequential and cached); live-paper's dYdX bots (independent adapter); the reboot in check 7 (an epic AC); check 5's log rotation (`verified_days` is the primary evidence); `make logs` naming an absent `collector` (verified on Compose v5.1.3: it streams the other services, exit 0); the KB's dYdX TOML example (it sits under dYdX-specific text).

## Design Notes

- **"The nightly cron line":** since Story 25.1b there is no host cron line. The `archive` service's `venues` list is its successor, so "keeps its `VENUE=DYDX` step" means `DYDX` stays in that list.
- **Step (1) "dYdX still running":** `redeploy-all` no longer names `collector`, so it leaves the running dYdX container on its old image. `make up` without a profile also leaves an existing dYdX container untouched, while `make down` removes every container, including profiled ones (verified).
- **Profile auto-enable:** `docker compose run collector ...` and `up collector` start a profiled service without `--profile`. The maintenance targets therefore keep using `collector`'s image and mounts, and the `redeploy-all` removal is what keeps dYdX from being restarted.

## Verification

**Commands:**
- `cd platform && python3 -m pytest tests/test_compose_profiles.py tests/test_images.py tests/test_skew_constants.py capture/venues ranking/tests views/tests -q` -- expected: all pass, with no new warnings.
- `cd platform && docker compose -f docker-compose.yml config --services` -- expected: no `collector`. With `--profile dydx` it is listed.
- `cd platform/frontend && npx vitest run src/pages` -- expected: pass.
- `cd platform && ruff check <changed .py> && ruff format --check <changed .py>` -- expected: clean.


## Auto Run Result

Status: done

**Summary:** Story 29.3 stages the venue cutover:
- Hyperliquid collects `SOL-USD-PERP.HYPERLIQUID`, and Bybit keeps its four ids.
- dYdX's `collector` runs behind the `dydx` compose profile, through `make up-dydx`/`down-dydx`. No default target starts it, and `up`/`redeploy-all` still build its image for the maintenance targets.
- `archive/config.toml` keeps `DYDX`, and the generic `.DYDX` examples now use `BTCUSDT-LINEAR.BYBIT`.
- DEPLOY_CHECKLIST §8 holds the seven-check runbook and a 29-3 Deferred operator actions entry.

This run was a follow-up review pass on the committed work (`4f9fc14d0d`).

**Files changed in this pass** (under `platform/`):
- `docs/DEPLOY_CHECKLIST.md` §8:
  - Retention text corrected (per-coin delta `retain_hours`, the plan-trim hazard).
  - Retirement condition 1 is now `max(8, R + 1)`.
  - The Hyperliquid BTC/ETH partial-day note.
  - Checks 2, 4, 5 and 7 hardened.
- `Makefile`: `up`/`redeploy-all` build `collector` before replacing services.
- `tests/test_compose_profiles.py`: a spelling-proof `dydx` gate test, and two test names renamed off the legacy `dydx_collector` token.
- `capture/venues/hyperliquid/config.toml`: a comment wrapped.

**Review:** 11 patches (3 medium, 8 low), 1 deferred (the TUI's DYDX commands after `down-dydx`), 12 rejected. See the follow-up triage entry.

**Follow-up review recommended:** false. The fixes are localized to runbook text, a two-line Makefile reorder and a test. The retention statements were checked line by line against `archive/prune_catalog.py`, `archive/nightly.py` and `collection_control/infrastructure/plan_store.py`.

**Verification:**
- `python3 -m pytest tests/test_compose_profiles.py tests/test_images.py tests/test_skew_constants.py tests/test_legacy_names.py capture/venues ranking/tests views/tests archive/tests candles/tests -q`: 1054 passed. Before the rename, `test_legacy_names` failed at HEAD.
- `ruff check` and `ruff format --check` on `tests/test_compose_profiles.py`: clean.
- A mutation check: injecting `COMPOSE_PROFILES=dydx` into `up` makes the new gate test fail. The injection was reverted.
- `docker compose config --services`: `collector` is absent by default.
- `docker compose logs -f` on a profiled service with no container: checked on a throwaway project (Compose v5.1.3), which streamed the others and exited 0.
- The frontend was not touched in this pass, so vitest was not re-run.
- Nothing was run against the VPS: that is §8, the deferred operator action.

**Residual risks:**
- Partial days fail reconciliation and keep their raw trades. For Hyperliquid BTC/ETH day D, they are reported nightly with no clearing step (`Known limit:` in §8).
- Hyperliquid's `hold_back_seconds`/`stale_book_seconds` are unmeasured for SOL until the owed D-63 run.
- The TUI can still publish DYDX commands that nothing consumes (deferred).
