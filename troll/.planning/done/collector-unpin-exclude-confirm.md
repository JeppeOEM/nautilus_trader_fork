# Collector: fold `unpinned` into `exclude`, surface `exclude` in the TUI, confirm before unpin

**Status: implemented.** All three changes below landed as described, including the
"share one confirm implementation" resolution to the open decision at the bottom
(`_open_collector_confirm`/`_close_collector_confirm`/`_handle_collector_confirm_key`/
`_submit_collector_confirm` in `bot_tui/app.py`, parameterized by action). Wire message
key stayed `unpinned_ids` (see decision below). Kept as a record of the design, not a
pending TODO.

Builds on top of the start/unpin/stop/pin_top_liquid work already landed (see
`dydx_collector/collector.py`'s module docstring and `bot_tui/app.py`'s Collector-pane
section for the current state before this change).

## What changes

1. **`unpin` writes to `config.exclude`, not a separate `config.unpinned` field.**
   Today `CollectorConfig.unpinned` is its own frozenset, independent of the pre-existing
   `exclude` denylist. Drop `unpinned` entirely and have the `"unpin"` control action add
   the id straight into `exclude` instead. `"start"` (re-adding a coin by name) removes it
   from `exclude` again, same as it currently removes it from `unpinned`.

2. **The TUI's "unpinned" section reflects `config.exclude` as a whole**, not just ids that
   went through the `p` key. A coin someone hand-added to `exclude` in `config.toml` (no TUI
   action involved) should show up there too — there is no longer a distinction between
   "excluded by hand" and "excluded via unpin"; it's the same list now.

3. **Confirm before unpinning.** Pressing `p` currently fires `"unpin"` immediately, no
   guard (see `_toggle_pin`'s comment: "unpinning is reversible via :start <ID>" — true, but
   the user wants a mistake-guard anyway, especially now that unpin writes into the same
   `exclude` list that also permanently blocks `pin_top_liquid` from re-suggesting it).
   Mirror the existing type-to-confirm pattern already used for `x`/stop
   (`_open_collector_stop_confirm` / `_handle_collector_stop_confirm_key` /
   `_submit_collector_stop_confirm` in `bot_tui/app.py`) rather than inventing a new one.

## Why this is a real behavior change, not just a rename

`classify_liquidity` (`dydx_collector/open_interest.py`) already treats every id in
`exclude` as **permanently illiquid, regardless of volume** — that's its pre-existing job
(a hand-curated denylist for stablecoins, deranked garbage markets, etc.). Folding `unpin`
into `exclude` means an unpinned coin now also gets permanently labeled "illiquid" in
`collector:status`, not just "not currently collected." That's presumably the intent
(unpin should feel as strong as a manual exclude), but call it out explicitly when
implementing — it's a small semantic widening beyond "remember this id," and worth a
one-line mention in the module docstring so a future reader isn't surprised by an unpinned
coin's liquid/illiquid label.

## File-by-file changes

### `dydx_collector/config.py`
- Remove `CollectorConfig.unpinned` field entirely (and its `load_config`/`save_config`
  wiring, and the `"unpinned"` TOML key).
- `InstrumentEntry.pinned`'s comment can stay as-is (unaffected by this change).

### `dydx_collector/collector.py`
- Module docstring: `unpin` bullet should say "adds the id to `config.exclude`" instead of
  "remembers it in `config.unpinned`".
- `_handle_control_message`:
  - `"start"`: change `unpinned=self._config.unpinned - {iid}` to
    `exclude=self._config.exclude - {iid}`.
  - `"unpin"`: change `unpinned=self._config.unpinned | {iid}` to
    `exclude=self._config.exclude | {iid}`.
- `_pin_top_liquid`: the candidate-exclusion set simplifies back to
  `self._config.exclude | existing_ids` (drop the `| self._config.unpinned` term — folding
  unpin into `exclude` means this guarantee ("never re-add an explicitly-unpinned coin") now
  falls out of the existing `exclude` exclusion for free, no separate union needed).
- `_publish_status`: the aggregate broadcast currently sends
  `{"unpinned_ids": sorted(self._config.unpinned)}`. Change the source to
  `sorted(self._config.exclude)` — keep the wire key name `unpinned_ids` (that's what the
  TUI section is called) unless you'd rather rename it for clarity now that it's really
  "the exclude list"; either is fine, just keep the two ends in sync.

### `bot_tui/collector_state.py`
- No structural change needed — it already just stores whatever list arrives under
  `unpinned_ids` into `_LATEST_UNPINNED_IDS`. Consider renaming that module global to
  `_LATEST_EXCLUDED_IDS` for clarity now that it's sourced from `exclude`, purely cosmetic.

### `bot_tui/collector_pane.py`
- `format_unpinned_line` needs no change (it already just renders whatever id list it's
  given) — maybe update its docstring's "past unpin" phrasing to "in config.exclude
  (whether via unpin or a hand-edit)".

### `bot_tui/app.py` — confirmation guard
- Add an unpin-confirm flow mirroring the stop-confirm one exactly:
  - New state fields alongside `_collector_stop_confirm_active`/`_collector_stop_confirm_id`
    (or generalize both stop and unpin onto one shared "pending collector action" state
    with an action-name field — cleaner, avoids duplicating four near-identical methods;
    worth doing since this is the second copy of the same pattern).
  - `_toggle_pin` (bound to `p`) should open the confirm prompt instead of calling
    `_publish_collector_action("unpin", ...)` directly.
  - Confirm text: something like `unpin {id}? type 'unpin' + enter, esc to cancel:` —
    match the existing stop prompt's exact wording style
    (`stop collecting {instrument_id}? type 'stop' + enter, esc to cancel: `).
  - Update `_COLLECTOR_FOOTER_HINT_TEXT` if the hint text should hint at the confirm step
    (current stop hint already just says "x stop", not "x stop (confirm)", so probably no
    change needed there — the confirm behavior speaks for itself).
- Update the COLLECTOR PANE section of `_HELP_TEXT`: `p` now says "unpin highlighted
  instrument (asks for confirmation)", matching the `x` line's existing wording.

## Tests to update/add

`dydx_collector/tests/test_collector_control.py`:
- `test_start_clears_id_from_unpinned` → assert it clears from `exclude`, not `unpinned`.
- `test_unpin_removes_entry_and_remembers_id` → assert `exclude` gains the id; drop the
  `config.unpinned` assertions.
- `test_pin_top_liquid_never_re_adds_an_unpinned_id` → seed via `exclude=frozenset({...})`
  instead of `unpinned=frozenset({...})`.
- Remove any test that only exists to cover the now-deleted `CollectorConfig.unpinned`
  field/TOML key.
- Add: a hand-added `exclude` entry (never touched by any control action) also shows up in
  the `unpinned_ids` broadcast from `_publish_status` — proves the TUI section reflects
  `exclude` as a whole, not just TUI-driven unpins.

`bot_tui/tests/`:
- `test_app_collector.py`: add a confirm-flow test mirroring whatever
  `test_collector_stop_confirm_*` tests already exist for `x` (check first — if none exist
  today because `_toggle_pin`/`_open_collector_stop_confirm` publish real asyncio tasks and
  are out of scope per this file's own docstring, the new confirm-prompt *state transitions*
  (open/cancel/submit) are still testable without a running loop, same as the stop-confirm
  ones presumably are).
- `test_app_navigation.py` / `test_collector_pane.py`: no expected changes, but re-run after
  the rename to make sure nothing implicitly depended on the `unpinned` config field.

## Open decisions for the implementer

- Wire message key: keep `unpinned_ids` or rename to something like `excluded_ids`? Either
  is fine; just keep collector.py's publish side and bot_tui's parse side consistent.
- Share one generic confirm-prompt implementation between `x`/stop and `p`/unpin, or keep
  them as two parallel copies? Sharing is more in line with this repo's
  deletion-over-addition preference (`troll/CLAUDE.md` DESIGN-03) given they'd otherwise be
  near-identical methods differing only in the action string and confirm keyword.
