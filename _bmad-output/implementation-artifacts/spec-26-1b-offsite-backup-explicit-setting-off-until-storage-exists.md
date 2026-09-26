---
title: 'archive: the off-site catalog backup is an explicit setting, off until a storage target exists'
type: 'feature'
created: '2026-09-26'
status: done
baseline_revision: '9dd696be89d26f12ebf4668dbe83869e8867a455'
final_revision: '2bfa74ae282e5dc10e3f102c28aacca36b2e4013'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-26-context.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** The `archive` service always runs `archive.backup_catalog` after the nightly consolidate. When `RCLONE_REMOTE`/`RCLONE_BUCKET` is unset, that step records `archive.backup_not_configured` and exits 1. The operator has no cloud storage yet, so every night shows a FAILED run and the ledger fills with an error about a deliberate choice. That noise would also hide a real backup failure later.

**Approach:** Add a required `backup_enabled` key to `archive/config.toml`, committed as `false`. When it is off, the chains have no backup step, the service logs one start WARNING, and `archive:status` carries `"backup": "disabled"`, which both the web panel and the TUI show. When it is on, behaviour is unchanged, except that a missing remote or bucket now refuses the service at start instead of failing every night. A manual `make backup-catalog` stays unchanged.

## Boundaries & Constraints

**Always:** Every config key is required and an unknown key refuses start (existing loader rule). Store the value as a strict `bool`. Only `backup_enabled` decides whether a backup job runs and what the status reports. The composition root builds `Chains.backup = None` when the backup is off, and `ArchiveScheduler` refuses a config/chains mismatch. Readers stay tolerant: an `archive:status` without `backup` is still valid, and a `backup` value other than `"enabled"`/`"disabled"` is malformed. The start refusal reuses `backup_catalog`'s own target rule (both values set after `.strip()`), never a copy of it. DATA-07: an enabled-but-unconfigured backup is loud (a ledger entry plus exit 1 at start), never skipped.

**Block If:** None expected. Everything the story needs is in the repo.

**Never:** Never silence or ledger-filter `archive.backup_not_configured` in `backup_catalog.py`: the manual run keeps it verbatim. Never default `backup_enabled` silently in the loader. No new dependency. Never touch `nautilus_trader/` or `crates/`, and never write `sprint-status.yaml`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Off | `backup_enabled = false`, no RCLONE env | Full runs have no `backup_catalog` step; one WARNING at `start()`; every status has `"backup": "disabled"` | none |
| On, configured | `true`, both env values set | Unchanged sequence ending in `backup_catalog`; status has `"backup": "enabled"` | none |
| On, unconfigured | `true`, remote or bucket empty/blank | `main` returns 1 before serving | `archive.config_invalid` ledgered, message names RCLONE_REMOTE/RCLONE_BUCKET |
| Bad key | `backup_enabled = "no"`, `1`, or missing | `ValueError` from the loader | start refused (same path) |
| Manual run | `python -m archive.backup_catalog`, no remote | exit 1, `archive.backup_not_configured` | unchanged |
| Reader | status with `backup: "maybe"` | views bus keeps the previous cache | `views.archive_status` ledgered |

</intent-contract>

## Code Map

- `platform/archive/config.toml` -- the service schedule; gains `backup_enabled = false` with the AC's comment.
- `platform/archive/infrastructure/scheduler_config.py` -- the strict loader (`_KEYS`, `parse_scheduler_config`).
- `platform/archive/application/scheduler.py` -- `Chains`, `SchedulerConfig`, `ArchiveScheduler` (`start`, `_full_run`, `status`, `_covers` docstring).
- `platform/archive/scheduler.py` -- composition root (`build_chains`, `build_scheduler`, `main`).
- `platform/archive/backup_catalog.py` -- `backup()` reads the target from env; extract the rule as a shared function.
- `platform/views/archive_status_bus.py` -- `valid_status` shape check.
- `platform/data_api/routes/archive.py` -- `ArchiveStatusResponse`; `frontend/openapi.json` + `src/api/schema.ts` are generated from it.
- `platform/frontend/src/components/ArchiveStatus.tsx` (+ `.test.tsx`) -- the web maintenance panel.
- `platform/bot_tui/collector_pane.py` (`format_archive_line`) + its tests -- the TUI archive line.
- Docs: `platform/README.md` "Nightly maintenance", `docs/DEPLOY_CHECKLIST.md` (§3 object storage bullet, Deferred 25-1b entry), `docs/DATA_INTEGRITY_AUDIT.md` D-33, `docs/DATA_DICTIONARY.md` §1.13, `docker-compose.yml` archive env comment, `.env-example`.

## Tasks & Acceptance

**Execution:**
- [x] `platform/archive/backup_catalog.py` -- add `configured_target(env) -> str | None` (the stripped remote/bucket rule plus `remote_target`), and have `backup()` use it with the same ledger text -- one definition of "configured".
- [x] `platform/archive/application/scheduler.py` -- add `SchedulerConfig.backup_enabled: bool` (keyword-only, no default); make `Chains.backup` optional (`None` = no backup job); `__init__` raises `ValueError` when `config.backup_enabled != (chains.backup is not None)`; `_full_run` skips the backup job when it is off; `start()` logs `off-site backup disabled: the catalog has no copy off this host` at WARNING once when it is off; `status()` appends `"backup": "enabled"|"disabled"` as its last key. Update the docstrings that say "then the backup".
- [x] `platform/archive/infrastructure/scheduler_config.py` -- require `backup_enabled` as a strict bool.
- [x] `platform/archive/scheduler.py` -- `build_chains(..., backup_enabled)` returns `backup=None` when it is off; `build_scheduler` raises `ValueError` when the backup is on and `configured_target(env)` is None; `main` catches `ValueError` from `build_scheduler`, ledgers `archive.config_invalid` and returns 1. Update the docstring env list.
- [x] `platform/archive/config.toml` -- add the key with the AC comment, and fix the header note about the backup target.
- [x] `platform/archive/tests/` -- config tests (committed value is `False`; non-bool or missing refused); scheduler tests (off: sequence without `backup`, WARNING logged once, `backup: "disabled"` in every status; on: `"enabled"`; mismatch refused); composition-root tests (`build_chains` backup None/present; `build_scheduler` refuses on+unconfigured, accepts off+unconfigured; `main` returns 1 with the ledger entry). Existing tests are updated for the new key and the new status key.
- [x] `platform/views/archive_status_bus.py` + `views/tests/test_archive_status_bus.py` -- `backup` is optional; when present it must be `"enabled"`/`"disabled"`.
- [x] `platform/data_api/routes/archive.py` -- add `backup: Literal["enabled", "disabled"] | None = None`; regenerate `frontend/openapi.json` (`PYTHONPATH=. python3 -m data_api.export_openapi`) and `src/api/schema.ts` (`npm run codegen`).
- [x] `platform/frontend/src/components/ArchiveStatus.tsx` + test -- when `backup === "disabled"`, show ` · backup off` in the warn colour with a title explaining there is no off-site copy; make the confirm text say the backup runs only when enabled.
- [x] `platform/bot_tui/collector_pane.py` + test -- append `backup off` to the archive line when the status says `disabled`.
- [x] Docs and comments -- README: the key in the table, the backup described as optional and off by default, the "Status" key list, and the setup steps ending in `backup_enabled = true`. DEPLOY_CHECKLIST: in the 25-1b entry, replace the "run `make backup-catalog` once" item with an unchecked item "when off-site storage exists: configure rclone, set `backup_enabled = true`", fix the §3 object-storage bullet, and add a 26-1b deferred entry (pull, `make up`, confirm `backup: disabled` and no new `archive.backup_not_configured`). Audit D-33: note that the missing backup is now an explicit, visible choice, and keep it OPEN. DATA_DICTIONARY §1.13: add the `backup` key. Update the compose and `.env-example` comments.

**Acceptance Criteria:**
- Given the committed `config.toml`, when the service starts, then the full-run chain has no `backup_catalog` step, one WARNING is logged, and `archive:status` carries `"backup": "disabled"`, which the web panel and TUI show.
- Given `backup_enabled = true` without `RCLONE_REMOTE`/`RCLONE_BUCKET`, when `python -m archive.scheduler` starts, then it exits 1 with an `archive.config_invalid` ledger entry and never serves.
- Given `make backup-catalog` with no remote, when run, then it exits 1 with the unchanged `archive.backup_not_configured` message.
- Given the story is merged, when the docs are read, then README, DEPLOY_CHECKLIST, DATA_DICTIONARY and audit D-33 describe the backup as an explicit setting that is off by default, and D-33 stays OPEN.

## Spec Change Log

## Review Triage Log

### 2026-09-26 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 6: (high 0, medium 1, low 5)
- defer: 1: (high 0, medium 0, low 1)
- reject: 12: (high 0, medium 0, low 12)
- addressed_findings:
  - `[medium]` `[patch]` A refused start under compose's `restart: always` is a restart loop (panel "status unavailable", TUI stale, one `archive.config_invalid` per restart). Documented in README "Nightly maintenance" and DEPLOY_CHECKLIST §5.4.
  - `[low]` `[patch]` `main` caught only `ValueError`, so a missing or unreadable `ARCHIVE_CONFIG` escaped as a traceback. It now catches `(ValueError, OSError)`, with the same ledger entry and exit 1, and a new test covers the missing file.
  - `[low]` `[patch]` The `test_scheduler_root._config` fixture's `.replace` could silently not flip the key. It now asserts the committed key line is present exactly once.
  - `[low]` `[patch]` `test_with_the_backup_on_no_warning_is_logged` asserted that no WARNING at all was logged. It now checks only for the backup warning.
  - `[low]` `[patch]` The web panel test did not cover `backup: "enabled"`. It is now parametrised over `enabled` and not reported.
  - `[low]` `[patch]` The 26-1b deferred checklist entry said no bind mount changed. It now says the mounted `config.toml` gains a required key, so a locally edited copy without it refuses start.

### 2026-09-26 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 1: (high 0, medium 0, low 1)
- defer: 0
- reject: 20: (high 0, medium 0, low 20)
- addressed_findings:
  - `[low]` `[patch]` The `format_archive_line` docstring line added by this story was 123 characters, over the 100-character limit. Reflowed.

## Design Notes

The scheduler and the composition root each make a single decision from the same config value. `Chains.backup is None` and `config.backup_enabled` must agree, and `ArchiveScheduler.__init__` enforces that, so a test rig or a future caller cannot configure "off" while still running a backup. The start refusal belongs in the composition root because it depends on the environment, which the application layer never reads. The previous story (26.1) touched only capture, so it carries no continuity here.

## Verification

**Commands:**
- `cd platform && python3 -m pytest archive/tests views/tests/test_archive_status_bus.py data_api/tests bot_tui/tests tests/test_images.py tests/test_boundaries.py -q` -- expected: all pass (except any tests already listed as known-failing)
- `cd platform/frontend && npx vitest run src/components/ArchiveStatus.test.tsx && npx tsc --noEmit` -- expected: pass
- `cd platform && ruff check archive views data_api bot_tui && ruff format --check archive views data_api bot_tui && mypy archive/scheduler.py archive/application/scheduler.py archive/infrastructure/scheduler_config.py archive/backup_catalog.py` -- expected: clean

## Auto Run Result

Status: done

**Summary:** A follow-up review pass on the finished 26-1b change (the off-site backup is now the required `backup_enabled` setting, committed `false`). It found no intent or spec defects and one low-severity patch.

**Files changed in this pass:**
- `platform/bot_tui/collector_pane.py`: reflowed a docstring line this story made longer than 100 characters.

**Review:** Blind Hunter and Edge Case Hunter findings: 1 patch applied, 0 deferred, 20 rejected. The rejected findings fall into four groups:
- already addressed or already deferred in the first pass: the restart loop under `restart: always`, and the codegen dropping the string enum;
- choices the spec made on purpose: the switch lives in `config.toml`, the start check reuses the backup step's own env-target rule, and a missing `backup` key is tolerated;
- pre-existing behaviour: rclone remote syntax;
- transient or cosmetic: a persisted FAILED backup step stays visible until the next nightly run.

**Verification:**
- `python3 -m pytest -o addopts="" --rootdir=. archive/tests views/tests/test_archive_status_bus.py bot_tui/tests/test_collector_pane.py -q`: 468 passed.
- `ruff check` and `ruff format --check` at line length 100 on `bot_tui/collector_pane.py`: clean.

**Residual risks:** Unchanged from the first run. The VPS needs `make up`, recorded as the deferred 26-1b entry in DEPLOY_CHECKLIST. Audit D-33 (no copy of the catalog off the host) stays OPEN until storage exists.
