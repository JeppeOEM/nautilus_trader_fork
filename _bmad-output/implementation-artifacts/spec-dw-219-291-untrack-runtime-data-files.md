---
title: 'DW-219/DW-291: untrack the runtime-rewritten preference and alert TOMLs, seed defaults at startup'
type: 'chore'
created: '2026-10-08'
status: 'done'
final_revision: '9ddb8d41a3'
baseline_revision: 'dee9ea2aad'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
warnings: [oversized]
---

<intent-contract>

## Intent

**Problem:** data_api rewrites `platform/data/alerts/alerts.toml` and every `platform/data/preferences/*.toml` (`chart_drawings`, `chart_indicators`, `chart_layouts`, `screener_columns`) in place at runtime, yet all five are git-tracked. So any upstream change to a tracked copy makes the VPS `git pull` refuse the rewritten file. The 25.2, 32-5 and DW-197 deploy steps each needed `git checkout --` surgery for exactly this.

**Approach:** This follows the human decision of 2026-10-05 (DW-219), applied the same way to DW-291:
- `git rm --cached` the five files and gitignore everything in both directories.
- Keep each directory through a tracked `.gitkeep`.
- Ship `*.default.toml` seeds that data_api copies into the preferences directory at startup, but only when the live file is missing.
- Document the one-time VPS transition as a deferred operator action.

## Boundaries & Constraints

**Always:**
- Seeding never overwrites, truncates or rewrites an existing live file, including one that is empty or malformed.
- A seed lands atomically, so a crash never leaves a partial live file: write a sibling temp file, then hard-link it into place (the link fails if the target exists), then remove the temp.
- A seed is shipped only where the default differs from the loader's missing-file state. That is `screener_columns` alone (4 default Technicals columns).
  - `chart_drawings`, `chart_indicators`, `chart_layouts` and `alerts` default to empty, which their loaders already produce for a missing file, so they get no seed.
  - The mechanism seeds every `<name>.default.toml` in the seed directory, so adding a seed later needs no code change.
- Seeds live in `platform/data_api/seeds/`. They do not go under `data/`, because `.dockerignore` excludes `platform/data/` and a seed there would never reach the image.
- Both directories stay in a fresh checkout through a tracked `.gitkeep`, so Docker never creates them root-owned.

**Block If:** none foreseen.

**Never:**
- Edit the deferred-work ledger.
- Touch `data/dydx_config.toml`, which is an operator-kept plan, not runtime-written.
- Rewrite the historical DEPLOY_CHECKLIST entries (25.2, 32-5, DW-197).
- Create the preferences directory from data_api: a missing directory means the bind mount is missing.
- Commit the live state (drawings or layouts) as a seed.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Seed on missing | preferences dir exists, no `screener_columns.toml` | The file is created with the seed's exact bytes and the seeded name is logged | No error expected |
| Never overwrite | live `screener_columns.toml` exists (customised, empty or malformed) | The file is byte-identical afterwards and nothing is reported as seeded | No error expected |
| No temp left | either case above | No `.*.seed.tmp` remains in the directory | Temp is removed in `finally` |
| Missing directory | `CHART_PREFERENCES_DIR` does not exist | Nothing is created; one warning names the directory | Startup proceeds: the PUT routes already answer 500 per request there |
| Shipped seed valid | `data_api/seeds/screener_columns.default.toml` | It parses through `views.preferences.load_screener_columns` into 4 columns | n/a |

</intent-contract>

## Code Map

- `platform/data_api/app.py:108` -- `lifespan()`, where the seeding call goes. It runs before the request routes, which read the preference paths on every request.
- `platform/data_api/settings.py:67` -- `CHART_PREFERENCES_DIR`.
- `platform/views/preferences.py:288` -- `load_screener_columns`; a missing file gives `[]`.
- `platform/alerting/infrastructure/toml_store.py:61` -- `AlertStore._load`; a missing file gives `[]`, so alerts need no seed.
- `platform/data/preferences/screener_columns.toml` -- the current default content. Its header comment points at a dead `screener_columns_config.py`.
- `.gitignore:178-195` (repo root) -- the `platform/data` ignore rules.
- `platform/tests/_source_tree.py:259-264` -- the no-git walk yields `data/preferences/*.toml` as "committed".
- `platform/docker-compose.yml:319-329` -- the mount comments say "committed files".
- `platform/Makefile:203-276` -- the verify-up/verify-wipe comments say "committed plan, preferences and alerts".
- `platform/docs/DEPLOY_CHECKLIST.md` -- "Deferred operator actions". New entries are appended at the end of the file, in the DW-197 entry's format.
- `platform/CLAUDE.md:241` (SSOT-06) and `platform/docs/DATABASE_SETUP.md:211-215` -- the preferences and alerts directory docs.

## Tasks & Acceptance

**Execution:**
- [x] `platform/data_api/seeds/screener_columns.default.toml` -- create it from the committed `screener_columns.toml` content, with the header comment corrected (a copy: the live file stays on disk, untracked).
- [x] `platform/data_api/preference_seeds.py` -- add `SEEDS_DIR` and `seed_missing(target_dir: Path, seeds_dir: Path = SEEDS_DIR) -> list[str]`. It links each missing `<name>.toml` from `<name>.default.toml` atomically, returns the seeded names, and warns and returns `[]` if `target_dir` is missing.
- [x] `platform/data_api/app.py` -- call it first thing in `lifespan()` with `Path(CHART_PREFERENCES_DIR)` and log each seeded file.
- [x] `platform/data_api/tests/test_preference_seeds.py` -- cover the I/O matrix: seed-on-missing, never overwrite (customised, empty, malformed), no temp left, missing directory, and the shipped seed parses.
- [x] `git rm --cached` all five live files (keep them on disk); add `platform/data/{preferences,alerts}/.gitkeep`.
- [x] `.gitignore` -- add `/platform/data/preferences/*` and `/platform/data/alerts/*`, each with a `!.../.gitkeep` negation. The `/dir/*` form is needed because a negation cannot re-include a file under an ignored directory. This subsumes the `.alerts.toml.tmp` line.
- [x] `platform/tests/_source_tree.py` -- drop the `preferences` glob and fix both comments: the seeds are walked under `data_api/`.
- [x] `platform/docker-compose.yml`, `platform/Makefile` -- reword the "committed" comments to say the directories are kept by `.gitkeep`, and that the files are live state, untracked and seeded.
- [x] `platform/CLAUDE.md` SSOT-06, `platform/docs/DATABASE_SETUP.md` -- add one sentence each on untracked live files and seeding.
- [x] `platform/docs/DEPLOY_CHECKLIST.md` -- append a deferred operator action covering the one-time VPS transition. It keeps the live copies: stop data_api, back up to `~/`, `git checkout --` the modified tracked copies, pull (the pull deletes them), restore, chown 1000, `make up`, check. Add a rollback step for the reverse case, where an untracked live file blocks the checkout.

**Acceptance Criteria:**
- Given a fresh clone, when it is checked out, then `platform/data/preferences/` and `platform/data/alerts/` exist, each holding only `.gitkeep`.
- Given the running stack rewrites any of the five live files, when `git status` runs, then it is clean (the files are ignored).
- Given `make test`'s platform suites, when they run, then they pass, including `test_legacy_names.test_walk_reads_every_tracked_text_file`.

## Spec Change Log

## Review Triage Log

### 2026-10-08 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 8: (high 0, medium 1, low 7)
- defer: 1: (high 0, medium 0, low 1)
- reject: 11: (high 0, medium 0, low 11)
- addressed_findings:
  - `[medium]` `[patch]` The lifespan hook `_seed_preferences` had no test (removing the call stayed green). Added tests for seeding the configured directory and for a failure being ledgered under `data_api.preference_seed` rather than raised.
  - `[low]` `[patch]` A directory sitting at the target path was skipped silently as "existing", and the loader then failed. It now raises `IsADirectoryError` (ledgered at startup), with a test.
  - `[low]` `[patch]` The fixed temp name could collide between two seeding processes. It is now a random, exclusively created `.<name>.<hex>.seed.tmp`, which keeps the umask mode the other writers use (not `mkstemp`'s 0600).
  - `[low]` `[patch]` The `os.link` dependency on a filesystem with hard links is now documented as a `Known limit:` with its upgrade path.
  - `[low]` `[patch]` DATABASE_SETUP's rw preference file list was stale. It now names all six files.
  - `[low]` `[patch]` DEPLOY_CHECKLIST: the backup and rollback steps refuse a leftover destination (a `cp -a`/`mv` would nest into it), `make up` is noted to rebuild the data_api image that carries the seed, and the never-tracked watchlist and filter-preset files are mentioned.
  - `[low]` `[patch]` Rewrapped the over-long Makefile `verify-wipe` help comment.

### 2026-10-08 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 2: (high 0, medium 0, low 2)
- defer: 0
- reject: 17: (high 0, medium 0, low 17)
- addressed_findings:
  - `[low]` `[patch]` The lifespan's `_seed_preferences()` call itself was still unguarded: the two earlier tests call `_seed_preferences` directly, so deleting the call from `lifespan()` stayed green. Added `test_lifespan_seeds_before_the_first_request`, which enters the real lifespan (bus loops idled) and checks the seed is in place inside it; it fails with the call removed.
  - `[low]` `[patch]` DEPLOY_CHECKLIST rollback: the restore `cp -a ~/*.post-dw219/.` copied the two `.gitkeep` files back into a reverted tree that neither tracks nor ignores them, leaving `git status` dirty. The step now removes them before `make up`.

## Design Notes

The hard link is used instead of `open(..., "x")` because a crash during an exclusive-create write leaves a partial file that is never re-seeded and makes the loader raise (500) for good. A link either places the complete file or fails with `FileExistsError`.

Seeding happens in `lifespan`, not at import, because `settings` is imported by the tests. The three lifespan tests run with the default `/app/preferences`, which does not exist on the host; that is the missing-directory row.

## Verification

**Commands:**
- `cd platform && python3 -m pytest data_api/tests/test_preference_seeds.py tests/test_legacy_names.py tests/test_compose_verify.py views/tests -q` -- expected: all pass
- `git check-ignore -v platform/data/preferences/chart_layouts.toml platform/data/alerts/alerts.toml` -- expected: both ignored; `git ls-files platform/data` lists `dydx_config.toml` and the two `.gitkeep` files only
- `ruff check` + `ruff format --check` + `mypy` on the changed Python -- expected: clean


## Auto Run Result

Status: done

**Summary:** Follow-up review pass of the committed change (b982c7ff38). The five runtime-rewritten TOMLs stay untracked and gitignored, with `.gitkeep`-kept directories and the one `screener_columns` seed placed by `data_api`'s lifespan only where the live file is missing. This pass added a lifespan-level test and fixed one rollback step in DEPLOY_CHECKLIST.

**Files changed (this pass):**
- `platform/data_api/tests/test_preference_seeds.py`: new `test_lifespan_seeds_before_the_first_request`, which runs the real `lifespan()` with the five bus loops idled.
- `platform/docs/DEPLOY_CHECKLIST.md`: the DW-219/DW-291 rollback step removes the restored `.gitkeep` files.

**Review:** 2 patches applied (both low), 0 deferred, 17 rejected. The rejects were:
- re-raised items the previous pass had already rejected: one faulty seed stopping the loop (a single seed ships), the exact-seed-set test, the missing seeds directory, the post-link directory-fsync misreport, verify-wipe keeping the directories, and DATABASE_SETUP's file list;
- spec decisions: silent re-seeding of a lost `screener_columns.toml` (the checklist warns about it), and a dangling symlink kept as present;
- not real: `(commit: this change's)` is the checklist's house heading format; `make up` does run `up -d --build`; the ledger-reset pattern matches `test_data_api.py`; a symlink-to-directory raising is correct, since the loader would fail on it too;
- negligible: a stale temp left only by a kill inside the create-to-unlink window, the "copies" wording, other worktrees' data dirs, the checklist step chaining, and `_source_tree`'s narrowed walk.

**Verification:**
- `python3 -m pytest data_api/tests/test_preference_seeds.py tests/test_legacy_names.py tests/test_compose_verify.py views/tests data_api/tests/test_alerts.py`: 1058 passed.
- `python3 -m pytest data_api/tests`: 460 passed against a throwaway `redis:7-alpine`. Without Redis, the same 9 Redis-only tests fail.
- The new test fails when the lifespan's `_seed_preferences()` call is removed (checked, then restored).
- `ruff check`/`ruff format --check` are clean on the test, and `mypy` is clean on the module and its test.

**Residual risks:**
- The VPS transition is still manual (DEPLOY_CHECKLIST). A pull without the backup step loses the live preferences, and the screener columns fall back to the seed with no warning.
- A filesystem without hard links cannot be seeded (`Known limit:`).
