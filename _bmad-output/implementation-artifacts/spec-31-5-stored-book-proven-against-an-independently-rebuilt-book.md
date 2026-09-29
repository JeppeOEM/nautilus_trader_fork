---
title: 'Story 31.5: The stored book proven against an independently rebuilt book'
type: 'feature'
created: '2026-09-29'
status: 'done'
baseline_revision: '46ba96b7bc'
final_revision: '976cd92bf0'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-31-context.md'
  - '{project-root}/platform/docs/DATA_DICTIONARY.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** Nothing proves that the stored top-20 book of each `second_snapshot` row is the venue's actual book at that second. OFI, OBI and microprice all read it. The only cross-check today is capture's own REST check (`book_check.py`), which cannot see a level missing below the best. DATA-08's zero-level `u` question is also unsettled, and so is Bybit spot's `u` behaviour.

**Approach:** Add `python3 -m verification.book --venue V --day D` with its own pure Decimal book builder, `verification/domain/reference_book.py`:
1. Replay the recorder's raw book frames per instrument.
2. Validate that reference against the recorder's REST polls, and report that first.
3. Build the top 20 per side for every exchange second under DATA-01's close rule.
4. Compare it with the stored integer rows, classifying every difference.

Then settle DATA-08 and the cross-check blind spot with evidence and code.

## Boundaries & Constraints

**Always:**
- **Independence (DATA-02):** non-test `verification` code never imports `capture`, `kernel.second_snapshot`, `nautilus_pyo3` or `nautilus_trader`.
  - The rows are read raw with pyarrow in a new `verification/infrastructure/snapshot_book.py`. It requires the integer layout: a file without `price_precision` is refused and named, with a pointer to `archive.tools.migrate_snapshot_ints`.
  - Decode: element 0 is the best price in units, and each later element is a gap that must be > 0. Bids subtract the gap and asks add it. Sizes are units at `size_precision`. Every value is a Python `int`.
  - `test_boundaries.py`'s `_GAP_LAYOUT_READERS` gains exactly that module. Its comment: the oracle must decode independently.
- **Reference book (`reference_book.py`, pure):**
  - **Levels:** `Decimal` from the wire strings. A size of 0 deletes the level. A non-string or non-decimal value is a `MalformedLine`.
  - **Bybit:** keyed by topic and symbol.
    - A `snapshot` re-baselines the book (clear, then add).
    - A `delta` applies only if `u == last_u + 1`. Otherwise it counts `u_breaks` and the book is unavailable until the next snapshot.
    - A delta with empty `b` and `a` counts `zero_level_messages`, and its `u` still advances the baseline.
  - **Hyperliquid:** each `l2Book` replaces the book. A `time` below the previous one counts `time_regress`.
  - **Connections:** a `close`/`error` line of the endpoint makes the book unavailable until the next snapshot (Bybit) or the next message (Hyperliquid). Reuse `trade_check.recorder_gaps` semantics.
  - **Venue time:** Bybit's frame `ts` and Hyperliquid's `data.time`, both ms, as ns. The Rust adapters stamp exactly these as `ts_event` (`crates/adapters/bybit/src/websocket/parse.rs:238`).
  - **Order:** messages apply in arrival order, which the `u` check authenticates for Bybit.
- **Close rule (DATA-01):**
  - `ref(S)` is the top 20 per side after every message with venue time `< (S+1)` s.
  - `ref⁻(S)` is the same without the last message included; keep that message's undo, not a copy of the book.
  - `ref⁺(S)` is the same plus the first message excluded.
  - Units come from `Decimal.scaleb(precision)` at the row's `price_precision`/`size_precision`. A non-integral value is `off_grid`, which fails.
- **Replay start:**
  - Bybit: look back over the retained raw hours before the day for the topic's latest `snapshot`, then replay forward from it.
  - Hyperliquid: look back one hour for the last `l2Book`.
  - When no baseline exists, the seconds before the day's first baseline are `reference_unavailable`.
  - `Known limit:` the replay cost grows with the connection's age, up to `VERIFY_RETAIN_DAYS`. Upgrade path: an end-of-day reference checkpoint, continuity-checked by `u`.
- **REST agreement (reported first, per instrument):**
  - **Aligning:** a Bybit poll is placed by `seq` between the reference messages. REST `u` is a different counter from WS `u`, measured about 29M against 192M. A Hyperliquid poll is placed by `time`.
  - **Classes:** each `status == 200` poll (Bybit `retCode == 0`) compares the top 20 per side exactly.

    | Class | Condition | Fails |
    |---|---|---|
    | `agree_key` | a message has the same key and equal books | no |
    | `disagree_key` | a message has the same key and unequal books | yes |
    | `agree_bracket` | no key match; equal to the state just before or just after | no |
    | `between_pushes` | no key match; equal to neither | no |
    | `unaligned` | outside the reference's availability | no |
    | `failed` | status ≠ 200 | no |

  - **`persistent_disagreement`** (fails): the same (side, price) mismatches in two consecutive `between_pushes` polls, with the same REST value, while no reference message touched that price in between.
  - **`reference_unvalidated`** (fails): judged polls > 0 and `agree_key + agree_bracket == 0`, or no poll at all while rows exist. A reference that is invalid or unvalidated fails the instrument, whatever the row classes say.
- **Second classes:** one per UTC second of the day, per plan instrument.

  | Class | Condition | Fails |
  |---|---|---|
  | `exact` | row == ref(S) | no |
  | `boundary_late` | row == ref⁻(S), and the omitted message's recorder `recv_ns` is ≥ row `ts_init − LATE_ARRIVAL_MARGIN_NS` (1 s, with a comment giving the measured recv−venue p99s and the justification) | no |
  | `boundary_unexplained` | row == ref⁻(S), but that message arrived earlier | yes |
  | `boundary_early` | row == ref⁺(S): the row holds a message from after its second, a DATA-01 violation | yes |
  | `content_differs` | any other difference; record per side the first differing level index and its kind: `missing`/`extra`/`size`/`price` | yes |
  | `off_grid` | ref(S) is not integral at the row's precision | yes |
  | `duplicate_row` | more than one row in the second | yes |
  | `missing_row` | reference available, no row, no coverage `seconds` run (reuse conservation's `Explanations`/`CoverageFiles`) | yes |
  | `missing_row_explained` | as `missing_row`, but a coverage run covers the second | no |
  | `reference_unavailable` | row exists, reference unavailable | no |
  | not judged | no row and no reference | — |

  An instrument whose rows exist but `judged == 0` fails with "nothing verified". Float noise is retired by Epic 30.2: the report states that the integer layout is exact, and a float-layout file is refused.
- **Refusals** are ledgered at the new site `verification.book.refused` (`sites.py` plus `test_sites.py`). The refused inputs mirror `verification.trades`, minus `--stage`. Missing raw hours of the day and a missing coverage record fail the day, as in conservation.
- **Reuse** conservation's `RawReader`, `is_closed`, `day_hours`, `wire_index`, `collect_coverage`, `plan_of`, `directory` and `Refused`, and the pruning in `catalog_reader._read_window`.
- **Code rules:** the LGPL header, full typing, functions of about 30 lines or fewer, complexity of 10 or less, no module-level mutable state, and the property tests use seeded stdlib `random`.
  - Memory holds one instrument-day of the book columns in Arrow, plus one book; a `Known limit:` covers it (MEM-01).

**Block If:** the raw frames contradict the measured shape: a Bybit book frame without `u`/`seq`/`ts`, or a Hyperliquid `l2Book` without `time`/`levels`.

**Never:**
- Never modify `nautilus_trader/`, `crates/`, `sprint-status.yaml` or a venue `config.toml`. Never add a dependency.
- Never use a tolerance on a book value. Never import the production decoder on the reference side.
- Never set `awaiting-operator`. The real-day verdict belongs to 31.11 (OPS-01).
- Do not build 31.6–31.10.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected | Error handling |
|---|---|---|---|
| Clean day | the rows equal the replay and REST agrees | every failing count is 0; exit 0 | — |
| Planted: size ±1 unit | one stored size +1 | `content_differs` 1 (`size` at index i); exit 1 | — |
| Planted: level deleted | one row loses a level | `content_differs` (`missing`); exit 1 | — |
| Planted: row shifted 1 s | the row for S holds S−1's book, with ≥2 messages between | `content_differs` ≥ 1; exit 1 | — |
| Late message | the last included message's recv_ns is after ts_init−1s, and row == ref⁻ | `boundary_late`; pass | — |
| Early message | row == ref⁺ | `boundary_early`; exit 1 | — |
| u gap | a delta with u = last+2 | `u_breaks` 1; seconds until the next snapshot are `reference_unavailable` | — |
| Zero-level delta | `b` = `a` = [] | `zero_level_messages` 1, the baseline advances, no break | — |
| Missing row, covered | no row, a coverage `crossed` run covers it | `missing_row_explained` | — |
| REST key disagrees | a same-seq REST level differs | `disagree_key` 1; exit 1 | — |
| Float layout file | no `price_precision` column | refused and ledgered | exit message, 1 |

</intent-contract>

## Code Map

- `platform/verification/trades.py`, `application/trades.py` -- the CLI root and orchestration pattern to mirror.
- `platform/verification/application/conservation.py` -- `Inputs`, `collect_coverage`, `day_hours`, `is_closed`, `missing_raw`, `hour_label`.
- `platform/verification/domain/trade_check.py:298` -- `recorder_gaps`, and `units`/`OffGrid` for Decimal-to-units.
- `platform/verification/domain/conservation.py` -- `MalformedLine`, `Intervals`, `Explanations`, `TradeChannel`/`wire_index`.
- `platform/verification/domain/subscriptions.py:76-299` -- the channel names `<cat>.orderbook.50`, `<cat>.rest.orderbook`, `l2Book`, `rest.l2Book`; the polls every 60 s.
- `platform/verification/infrastructure/catalog_reader.py:94-135` -- `_row_groups`/`_read_window`/`SNAPSHOT_DIR`.
- `platform/kernel/second_snapshot.py:179-210,581` -- the stored layout, documentation only (never imported).
- `platform/capture/domain/live_book.py:136-185` -- `apply`/`_check_sequence`, the DATA-08 locus.
- `platform/capture/venues/bybit/policies.py:36-73` -- `sequence_verdict`/`message_u`.
- `platform/capture/application/book_check.py:60` -- `top_levels_mismatch`, the blind spot.
- `platform/tests/test_boundaries.py:1555-1614` -- the gap-layout readers, plus the verification roots list and the import-smoke subprocess.
- Soak: `platform/data/verification/raw/{bybit,hyperliquid}` since 2026-09-29 12:59Z; `data/catalog/data/custom_dydx_second_snapshot`; `data/errors/*.jsonl`.

## Tasks & Acceptance

**Execution:**
- [x] `platform/verification/domain/reference_book.py` (new, pure) -- the frame parsers (Bybit WS/REST, Hyperliquid WS/REST); `ReferenceBook` (apply, undo of the last message, `top(n)`, availability, counters); `BookUnits` plus `to_units`; the `rest_agreement`, `classify_second` and `level_diff` functions; the `InstrumentBook`/`BookDayReport` dataclasses with `passed`.
- [x] `platform/verification/infrastructure/snapshot_book.py` (new) -- `SnapshotBooks.rows(iid, start, end)` returns, per hour, `second -> [StoredBook(ts_init, precisions, bids, asks as int units)]`; the float layout is refused.
- [x] `platform/verification/application/book.py` (new) -- the ports, the look-back start, the per-instrument replay and second loop, `render_text` (REST agreement first) and `report_json`.
- [x] `platform/verification/book.py` (new root) -- `--venue --day [--json] [--raw-dir] [--catalog]`, exit 0/1, refusals ledgered.
- [x] `platform/verification/application/sites.py`, `verification/tests/test_sites.py` -- `BOOK_REFUSED`.
- [x] `platform/tests/test_boundaries.py` -- register `verification.book` as a root and in the import smoke; add `_GAP_LAYOUT_READERS` += `verification.infrastructure.snapshot_book`.
- [x] `platform/verification/tests/test_book.py` (new) -- end to end over a real catalog (`write_data` of snapshots) and raw lines: every I/O row, including the three planted defects (each asserting a non-zero failing count and exit 1); unit tests of the replay (u rules, zero-level, reconnect, look-back), the REST classes including `persistent_disagreement`, the decode (gaps, a non-positive gap refused), and a seeded-random property that the encode→decode of a random book round-trips.
- [x] `platform/capture/domain/live_book.py` plus a test in `capture/tests` -- a snapshot message that carries no level (Clear only, `message_u` None) resets `last_u` to None, because a snapshot always re-baselines. Evidence: `LiveBook._check_sequence` returns before the reset.
- [x] `platform/capture/application/book_check.py` plus `capture/tests/test_book_check.py` -- close the blind spot: REST levels among the first `min(len)` whose price is absent from live are reported as `absent from live <price>:` under the same tolerance count, which is symmetric.
- [x] Real-data smoke -- run `main(..., clock=<closed>)` over the soak day for both venues. Record the REST agreement per instrument, the class counts, `u_breaks` and `zero_level_messages` for linear and spot, and the `collector.book_sequence` ledger entries in the window. Investigate every non-zero failing count to root cause: fix it, or register it OPEN with a follow-up story.
- [x] Docs:
  - `platform/docs/DATA_DICTIONARY.md` §1.18 (the tool, its classes, rules and repro);
  - `docs/VERIFICATION_REPORT.md` -- a row "Book top-20 vs rebuilt book", with the smoke numbers, pending 31.11;
  - `docs/DATA_INTEGRITY_AUDIT.md` from D-96: the DATA-08 evidence (zero-level deltas never reach Python, so the Rust `new_checked` rejects them; the wire count; the snapshot re-baseline fix), the blind spot closed, spot `u` measured (D-41's scope), the REST alignment finding, and the float-noise class retired;
  - `platform/CLAUDE.md` DATA-08 amended to match.

**Acceptance Criteria:**
- Given a closed day, when `python3 -m verification.book --venue V --day D` runs, then per instrument it prints the REST agreement first, then every second class with the level details, and exits 1 exactly when a failing count is non-zero, the reference is invalid or unvalidated, or the inputs are incomplete.
- Given `tests/test_boundaries.py`, when run, then `verification.book` imports nothing denied, and exactly the two old readers plus `snapshot_book` read the gap columns.
- Given the story ships, then DATA-08's zero-level question, Bybit spot's `u` behaviour and the cross-check blind spot each have recorded evidence and an audit row.

## Design Notes

Why Bybit REST needs the `between_pushes` class: over 13:00–16:00Z, 58 polls matched a WS `seq` exactly and 0 of them disagreed. Of the rest, 460 equalled the neighbouring state and 202 caught an intermediate engine state. Levels change several times inside one 20 ms push, so up to 13 levels differ even though neither book is wrong. A lost reference message instead persists across polls, and that is what `persistent_disagreement` catches without any tolerance.

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. verification/tests tests/test_boundaries.py capture/tests/test_book_check.py capture -q -k "book or boundar or live_book or sites"` -- expected: all pass.
- `cd platform && ruff format --check verification capture tests && ruff check verification capture tests/test_boundaries.py && mypy verification capture/domain capture/application/book_check.py` -- expected: clean.

## Spec Change Log

## Review Triage Log

### 2026-09-29 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 12: (high 0, medium 4, low 8)
- defer: 0
- reject: 8: (high 0, medium 0, low 8)
- addressed_findings:
  - `[medium]` `[patch]` A float-layout file outside the window blocked every day. The layout is now checked only on files with rows in `[start, end)`, and a test covers it.
  - `[medium]` `[patch]` A raw hour missing inside the day or the look-back left seconds closed against the stale pre-gap book. A missing hour now breaks the reference like a disconnect, with a Hyperliquid test.
  - `[medium]` `[patch]` `LATE_ARRIVAL_MARGIN_NS` was 1 s, wider than Bybit's 0.5 s hold-back, so a message capture must already have held could pass as `boundary_late`. It is now 500 ms, bounded by the measured maximum difference between the two connections' receive times (394 ms, Story 31.4). The comment is rewritten, and a test shows a message received 0.7 s before sampling is `boundary_unexplained`. The smoke's `boundary_late` count is unchanged at 0.
  - `[medium]` `[patch]` An instrument with no reference messages in the day passed with nothing verified. It now fails as unvalidated with "no reference data", and a test covers it.
  - `[low]` `[patch]` The bare `next()` in `_feed` surfaced as a RuntimeError; it is now a clear refusal.
  - `[low]` `[patch]` A frame whose `raw` is not text passed the substring pre-filter; it is now refused as `MalformedLine`.
  - `[low]` `[patch]` A null `ts_init`/`ts_event`/precision, or a decoded price ≤ 0, is now refused instead of crashing or showing up as `content_differs`.
  - `[low]` `[patch]` The replay counters now count only messages inside the day.
  - `[low]` `[patch]` `book_check`'s tolerance is per direction. The docstring, test name, D-97 and DATA-08 now say so, and D-97 names the operational effect.
  - `[low]` `[patch]` New Known limits and docs: HL validation rests on key matches (`persistent_disagreement` practically cannot fire there); a `u` break or recorder gap shrinks the verified set, traced by `reference_unavailable` and the judged count; after a zero-level snapshot the next delta is unjudged.
  - `[low]` `[patch]` The evidence numbers in DATA-08, D-96, D-98, D-99 and VERIFICATION_REPORT are labelled with their window and what they count.
  - `[low]` `[patch]` The new `test_domain` test builds prices from strings, not floats.
  - Rejected as by design, measured never to occur, or as in 31.4:
    - `ValueError` breadth in the refusal catch;
    - the same `before` across a multi-second close, which follows the definition;
    - arrival-order closing under `time_regress`, which was 0 on the wire and is counted;
    - duplicate HL `time` keys, which never repeat on the wire;
    - HL `error` lines arming the reply skip, which cannot cause a false verdict;
    - the next-hour-absent tail, already traced as `reference_unavailable`;
    - the pending-resync test wish;
    - the `KeyError` crash path, which the P5 fix covers.

### 2026-09-29 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 7: (high 0, medium 2, low 5)
- defer: 0
- reject: 11: (high 0, medium 0, low 11)
- addressed_findings:
  - `[medium]` `[patch]` `persistent_disagreement` watched the wrong messages, off by one. `BookReplay.message` applied a message's touch before placing the polls it closes. So the message right after a poll never cleared the watch, and a message after the second poll cleared it before that poll was judged: a level lost from the reference and touched only after both polls was never caught. The touch now lands after the placement, and the test that asserted the wrong case is corrected, with a new case for a touch after both polls.
  - `[medium]` `[patch]` When the look-back hour's last event was a connection line, `replay_start` returned the day's first hour and skipped that line. The day's first Hyperliquid `l2Book`, the recorder's own subscribe reply (D-101), was then judged as a push. The hour holding the connection line is now replayed. The unit test is updated, a Hyperliquid test is added, and DATA_DICTIONARY §1.18 is amended.
  - `[low]` `[patch]` A null `ts_event` was silently filtered out by `read_window`, so the "refused" comment in `snapshot_book` was dead. `read_window` now refuses such a file, and the comment is corrected.
  - `[low]` `[patch]` A null book side read as zero levels, while the production decoder refuses it. It is now refused, with tests.
  - `[low]` `[patch]` The test name `test_a_zero_level_message_carries_no_u_and_leaves_the_baseline` contradicted the snapshot re-baseline. It is renamed.
  - `[low]` `[patch]` The claim "zero-level snapshot reaches Python as a lone `Clear`" had no citation. It now cites `crates/adapters/bybit/src/websocket/parse.rs:259` in D-96 and in the `live_book` comment.
  - `[low]` `[patch]` VERIFICATION_REPORT's "smoke: 0" for the linear instruments now states that the tool still counts their D-76 `missing_row` as failing (exit 1).
  - Rejected:
    - the `book_check` deepest-level case, which follows the spec's `min(len)` rule and per-direction tolerance;
    - the 500 ms margin, already justified with a Known limit in the prior pass;
    - `ValueError` breadth, the multi-second `before`, and `time_regress` closing, all rejected before;
    - the minimum-coverage verdict, which belongs to 31.11 and is traced by `reference_unavailable`;
    - the strength of Hyperliquid validation, a documented Known limit;
    - truncated neighbour tails, because a recorder restart always writes an `open` line, which breaks the book;
    - the `seq` reset mid-day, measured 0 (D-98) and a loud fail, never a false pass;
    - an empty plan, the established 31.4 pattern;
    - test-coverage wishes for the REST error paths.

## Auto Run Result

Follow-up review pass over `46ba96b7bc..8ab019a527` plus this pass's fixes.

- **Change:** Story 31.5's `verification.book` tool, which checks the stored top-20 book against an independently rebuilt reference book, validates that reference against REST, and classifies every second. Also the DATA-08 re-baseline fix and the closure of the `book_check` blind spot. All were already implemented; this pass hardened them.
- **Files changed in this pass:**
  - `platform/verification/domain/reference_book.py`: the persistent-watch touch now comes after poll placement.
  - `platform/verification/application/book.py`: `replay_start` replays a look-back connection line.
  - `platform/verification/infrastructure/snapshot_book.py`: a null side is refused, and the docstring is corrected.
  - `platform/verification/infrastructure/catalog_reader.py`: `read_window` refuses a null `ts_event`.
  - `platform/verification/tests/test_book.py`: corrected persistent test, new Hyperliquid look-back test, null-side cases.
  - `platform/capture/venues/bybit/tests/test_sequence_canary.py`: test renamed.
  - `platform/capture/domain/live_book.py`, `platform/docs/DATA_INTEGRITY_AUDIT.md`: Rust citation.
  - `platform/docs/DATA_DICTIONARY.md`: replay-start rule.
  - `platform/docs/VERIFICATION_REPORT.md`: failing-count wording.
- **Findings:** 7 patches applied (2 medium, 5 low), 0 deferred, 11 rejected.
- **Verification:**
  - `python3 -m pytest -o addopts="" --rootdir=. verification/tests capture/tests capture/venues/bybit/tests tests/test_boundaries.py -q`: 797 passed.
  - `ruff format --check verification capture tests`: clean.
  - `mypy verification capture/domain capture/application/book_check.py`: clean.
  - `ruff check verification capture tests/test_boundaries.py` reports 3 errors, all in files this story never touched (`capture/venues/bybit/tests/test_collector.py:101,103` PT018; `capture/venues/dydx/tests/test_integration.py:77` ASYNC240).
- **Residual risks:**
  - The real-day smoke numbers were not re-run after the two verdict-logic fixes. They are expected to leave the smoke's recorded counts unchanged. `persistent_disagreement` was 0, and the watch fix only changes which messages clear a watch. The soak began at 2026-09-29 12:59Z, so the smoked day has no raw hour before it to look back into.
  - The real-day verdict remains Story 31.11's.
