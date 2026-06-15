# Deferred Items — Phase 06 (hot-reload-new-instruments)

Out-of-scope discoveries logged during execution (not fixed; not caused by the current task's changes).

## Plan 06-02 (Wave 2)

- **`scripts/bybit_recorder/inspect_catalog.py` — ruff D213 + I001.**
  Pre-existing in the Phase-2 ad-hoc smoke script (last touched commit `ab7dfeae65`,
  "fix(02): point inspect_catalog.py at catalog/streaming root"). Not in this plan's
  `files_modified`; not edited by Plan 06-02. The file's own docstring marks it as a
  throwaway inspection script ("Not part of the plan's tracked files — safe to delete
  afterwards"). The plan's `<verification>` block runs `ruff check scripts/bybit_recorder/`
  which surfaces it. Out of scope for this plan.

- **`scripts/bybit_recorder/config.py:182` — ruff C901 (`load_recorder_config` complexity 11 > 10).**
  Pre-existing from Wave 1's `max_hot_added_instruments` validation branch (the config
  knob added in Plan 06-01). Not edited by Plan 06-02. The targeted Task-3 verify gate
  (pytest `-k`) and the per-file ruff on this plan's changed files are clean. Out of scope
  for this plan; candidate for a follow-up refactor (extract per-section parsing/validation
  into helpers).
