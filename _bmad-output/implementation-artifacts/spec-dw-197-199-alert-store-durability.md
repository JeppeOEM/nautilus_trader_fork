---
title: 'DW-197/DW-199: alert store durability (atomic save + rollback)'
type: 'bugfix'
created: '2026-10-06'
status: 'done'
baseline_revision: '3af891886c1c49b036d3db933b44ec2eb8ef721e'
final_revision: 'a95b240810b91ac2c66c1d254eb5cf6036849d2c'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
warnings: []
---

<intent-contract>

## Intent

**Problem:** `AlertStore._save` truncates `alerts.toml` in place (`open("wb")` then dump), so a crash or full disk mid-write leaves a torn file that `_load` refuses, and `data_api` then fails at import (DW-197). `add`/`delete` mutate the in-memory list before `_save()`, so a failed write returns 500 while the unsaved alert keeps firing, or the deleted alert stays gone until restart and `engine.forget` is skipped (DW-199).

**Approach:** Write `alerts.toml` atomically (sibling temp, fsync, `os.replace`, parent-directory fsync) and restore the previous in-memory list when `_save` raises in `add`/`delete`, re-raising. Because a rename cannot replace a single-file bind mount (EBUSY), move the file into its own mounted directory `platform/data/alerts/` (same pattern Story 32.5 used for preferences), with the operator migration recorded as a deferred operator action.

## Boundaries & Constraints

**Always:** `alerts.toml` text/key set unchanged (AD-D12; `test_file_text_is_frozen` keeps passing). A failed save leaves the previous file byte-identical and no temp file behind. `record_fire` keeps its current semantics (in-memory fire kept, persist failure ledgered at `alerting.store.persist`). `alerting` imports only `kernel`, `observability`, stdlib and `tomli_w` (boundary test). Every doc naming the old path/mount is updated.

**Block If:** none expected.

**Never:** edit the deferred-work ledger; import `views` from `alerting`; change `views/preferences.py`'s writer; swallow the `add`/`delete` save error.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Save ok | `add`/`delete`/`record_fire` | file replaced atomically, reload round-trips | none |
| Write fails mid-dump (add) | temp write raises `OSError` | original file unchanged, no `.alerts.toml.tmp`, `list()` excludes the new alert | error re-raised |
| Save fails (delete) | `_save` raises | alert still in `list()` and still in file | error re-raised; service skips `engine.forget` |
| Save fails (record_fire) | `_save` raises | fire kept in memory | ledgered, not raised (unchanged) |

</intent-contract>

## Code Map

- `platform/alerting/infrastructure/toml_store.py` -- `AlertStore`; `_save`, `add`, `delete`; `ALERTS_PATH` default.
- `platform/alerting/tests/test_toml_store.py` -- store tests.
- `platform/views/preferences.py:265` -- `_write_atomic`, the reference pattern (alerting may not import it).
- `platform/docker-compose.yml:306,319-320` -- `ALERTS_PATH` env + single-file bind mount.
- `platform/data_api/alerts.toml` -- committed empty file, to `git mv` to `platform/data/alerts/alerts.toml`.
- `platform/docs/DEPLOY_CHECKLIST.md` "Deferred operator actions"; `platform/docs/DATA_DICTIONARY.md` §2.11; `platform/docs/DATABASE_SETUP.md:180`; `platform/ARCHITECTURE.md` -- path mentions.

## Tasks & Acceptance

**Execution:**
- [x] `platform/alerting/infrastructure/toml_store.py` -- serialize to bytes first, then a private `_write_atomic(path, data)` (temp `.alerts.toml.tmp` beside the target, write+flush+fsync, `os.replace`, unlink temp on any failure, then fsync the parent directory); `add`/`delete` snapshot the list and restore it on `BaseException` from `_save`, re-raising; `ALERTS_PATH` default -> `platform/data/alerts/alerts.toml`, comment updated -- DW-197/DW-199.
- [x] `platform/alerting/tests/test_toml_store.py` -- tests for the matrix rows: failed add (monkeypatch `os.fsync` or the write to raise) keeps file bytes and memory, no temp left; failed delete keeps the alert; temp never left on success.
- [x] `platform/data_api/alerts.toml` -> `platform/data/alerts/alerts.toml` (`git mv`); `platform/docker-compose.yml` -- mount `./data/alerts/:/app/alerts_dir/:rw`, `ALERTS_PATH=/app/alerts_dir/alerts.toml`, comment why a directory.
- [x] `platform/docs/DEPLOY_CHECKLIST.md` -- deferred operator action entry `DW-197` (stop data_api, keep live file, checkout, pull, copy into `data/alerts/`, remove old, `make up`, verify alerts list). Update DATA_DICTIONARY §2.11, DATABASE_SETUP, ARCHITECTURE path mentions.

**Acceptance Criteria:**
- Given a store with saved alerts, when a save fails at any point before the rename, then `alerts.toml` is byte-identical to before and no temp file remains.
- Given `add` or `delete` whose save raises, when the error propagates, then `list()` equals the list before the call.
- Given the compose file, when `data_api` saves an alert, then the target lives in a mounted directory (rename-safe).

## Spec Change Log

## Review Triage Log

### 2026-10-06 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 6: (high 0, medium 1, low 5)
- defer: 1: (high 0, medium 1, low 0)
- reject: 13
- addressed_findings:
  - `[medium]` `[patch]` An interrupt after `os.replace` (or in the directory fsync) rolled memory back behind the published file: split `_save` into `_publish` + `_confirm_durable`; `_save_or_restore` guards only the publish. Test `test_an_interrupt_after_the_rename_keeps_memory_with_the_file` added.
  - `[low]` `[patch]` A failing temp unlink in cleanup masked the original save error: unlink wrapped in `contextlib.suppress(OSError)`.
  - `[low]` `[patch]` Writer duplication from `views/preferences.py` was an undocumented simplification: `Known limit:` with the shared-helper upgrade path added.
  - `[low]` `[patch]` Docstring overclaimed "no temp behind" for a killed process: wording states the temp may survive a kill and is reused/never read.
  - `[low]` `[patch]` DEPLOY_CHECKLIST entry: staged the live file in `~` instead of `/tmp`, added `chown` remediation and a rollback step.
  - `[low]` `[patch]` DATA_DICTIONARY §2.11 over-long line rewrapped.

### 2026-10-06 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 4: (high 0, medium 0, low 4)
- defer: 1: (high 0, medium 1, low 0)
- reject: 21
- addressed_findings:
  - `[low]` `[patch]` A killed save's `.alerts.toml.tmp` would dirty the VPS checkout: `/platform/data/alerts/.alerts.toml.tmp` added to `.gitignore`.
  - `[low]` `[patch]` DEPLOY_CHECKLIST rollback step would hit git refusing the modified tracked `data/alerts/alerts.toml`: rewritten as stop, stash the live file in `~`, `git checkout --`, revert, copy back, `make up`.
  - `[low]` `[patch]` DATABASE_SETUP described a single rw file where the whole `data/alerts/` directory is rw: wording fixed and rewrapped.
  - `[low]` `[patch]` DATA_DICTIONARY §2.11 still had a 144-character line: rewrapped.

## Design Notes

The writer is duplicated (~15 lines) from `views/preferences.py` rather than shared: `alerting` may not import `views`, and moving a filesystem helper into `kernel` is out of scope for this bundle. Directory fsync is added (preferences omits it) so the rename itself is durable, as `archive.infrastructure.catalog_files.write_json_atomic` does.

## Verification

**Commands:**
- `cd platform && python3 -m pytest alerting data_api/tests/test_alerts.py tests/test_boundaries.py -q` -- expected: all pass.
- `cd platform && ruff check alerting && ruff format --check alerting && mypy alerting/infrastructure/toml_store.py` -- expected: clean.


## Auto Run Result

**Summary:** `AlertStore` saves `alerts.toml` atomically (serialize, sibling `.alerts.toml.tmp`, fsync, `os.replace`, directory fsync) and `add`/`delete` restore the previous in-memory list when the publish fails, re-raising (so `AlertService.delete` skips `engine.forget`). Because a rename cannot replace a single-file bind mount, `alerts.toml` moved to the mounted directory `platform/data/alerts/`, with a deferred operator action for the VPS migration. This run was a fresh follow-up review of the done spec (baseline `3af891886c`).

**Files changed (whole story):**
- `platform/alerting/infrastructure/toml_store.py` -- atomic writer, publish/confirm split, rollback, new `ALERTS_PATH` default.
- `platform/alerting/tests/test_toml_store.py` -- failed add/delete/rename, no-temp, fsync-dir ledger, post-rename interrupt tests.
- `platform/data_api/tests/test_alerts.py` -- failed delete never calls `engine.forget`.
- `platform/data_api/alerts.toml` -> `platform/data/alerts/alerts.toml` (`git mv`).
- `platform/docker-compose.yml` -- directory mount `./data/alerts/:/app/alerts_dir/:rw`, `ALERTS_PATH`.
- `platform/docs/DEPLOY_CHECKLIST.md` -- deferred operator action DW-197/DW-199 (rollback step fixed this pass).
- `platform/docs/DATA_DICTIONARY.md` §2.11, `platform/docs/DATABASE_SETUP.md` -- path and save semantics (rewrapped/clarified this pass).
- `.gitignore` -- ignore the save temp (this pass).

**Deviation from spec (kept):** a failed directory fsync after the rename is ledgered at `alerting.store.fsync_dir`, not raised -- raising would roll memory back behind an already-published file.

**Review (this pass):** 4 low patches applied, 1 deferred (tracked runtime data files; appended to the ledger as a new entry), 21 rejected -- among them the bytecode-level interrupt window between `os.replace` and the `_publish` return (the store runs in uvicorn's threadpool, where no async `KeyboardInterrupt` is delivered), fixed temp name / multi-process sharing (one wiring, boundary test), mode/owner reset on rename and stale foreign-owned temp (`umask` default equals the checked-out file's mode; a root-run store is not a deployment shape), symlinked `ALERTS_PATH`, cwd-relative default (pre-existing), non-OSError in `record_fire` (unchanged semantics), global `os` monkeypatching in tests, startup mount check for a stale `ALERTS_PATH` (covered by the checklist), and duplication of the atomic writer (already a documented `Known limit:`).

**Follow-up review recommended:** false -- this pass changed only docs and one `.gitignore` line.

**Verification:** `cd platform && python3 -m pytest alerting data_api/tests/test_alerts.py tests/test_boundaries.py -q` -> 146 passed; `uvx ruff check alerting` / `uvx ruff format --check alerting` clean; `mypy alerting/infrastructure/toml_store.py` clean; `docker compose -f docker-compose.yml config -q` OK; `git check-ignore` confirms the temp is ignored.

**Residual risks:** the VPS keeps reading the old path until the operator runs the DW-197 checklist entry; deploying this commit without the migration starts `data_api` with the committed empty `alerts.toml` (saved alerts not lost, still in `data_api/alerts.toml` on the host). The tracked-runtime-file `git pull` conflict persists until the deferred item is done.
