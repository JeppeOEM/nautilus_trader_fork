---
title: 'Story 31.10: Fault injection proves every loss is accounted for'
type: 'feature'
created: '2026-09-30'
status: done
baseline_revision: '153629f4f6'
final_revision: 'c43672a7da'
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

**Problem:** No production failure has ever been injected on purpose. Nothing proves that a crash, a restart, a stall, a network cut, a failed catalog write or a Redis outage leaves only losses that are backfilled, gap-marked or ledgered. Three gaps are known from planning:
- `verification.conservation` only reconciles whole closed UTC days, so it cannot judge one scenario's window.
- No tool injects the faults or records when they happened.
- A crash or restart gap is never trade-backfilled (D-61; D-75 is OPEN), because `TradeIntake.last_trade_ts` is not seeded from the archive. The SIGKILL and restart scenarios would therefore leave unexplained trades.

**Approach:**
1. Add a whole-second window mode to `verification.conservation`.
2. Build `python3 -m verification.chaos`. It injects each scenario on the verify stack, logs start and end, and evaluates each closed scenario window against an expected-outcome table, using windowed conservation plus ledger evidence.
3. Fix D-61: seed the archive baseline at startup and backfill the restart gap.
4. Run every scenario the agent can run on the verify stack. Root-cause each mismatch, then fix it or register it OPEN.

## Boundaries & Constraints

**Always:**
- **Windowed conservation:**
  - `verification.conservation` gains `--start ISO --end ISO` (UTC, whole seconds), mutually exclusive with `--day`. It reuses `conserve`'s logic through one shared window function; `conserve(day)` becomes that function over the day, and day output stays unchanged.
  - Trades are counted when their venue time is in `[start, end)`, on both sides. Seconds are counted in `[start_s, end_s)`. Coverage runs and windows are clipped to the window.
  - The raw hours read are those the window touches, plus their neighbours.
  - A window is evaluable only once its last touched hour has ended and `end + WINDOW_SETTLE_NS` (10 min: a flush, the backfill settle, one more flush) has passed. Otherwise it is refused.
  - The JSON carries `start` and `end`.
- **Chaos tool, run mode:**
  - `python3 -m verification.chaos --scenario S --venue BYBIT|HYPERLIQUID [--log PATH] [--json]`.
  - Scenarios:
    - `sigkill_flush`: `docker kill -s KILL` at wall-clock second :02 + 0.25 s of a minute, then `docker start`.
    - `graceful_restart`: `docker restart`.
    - `pause_15s`, `pause_45s`: `docker pause`, sleep, `docker unpause`.
    - `network_cut`: 60 s. It runs `sudo -n iptables`/`ip6tables` `OUTPUT -m owner --uid-owner 1000 -d <ip> -j DROP` with a `verify-chaos` comment. The IPs are the venue's REST/WS hosts from `kernel.venue_http`, resolved through `getaddrinfo`. The rules are removed afterwards. The recorder's uid 1001 is unaffected.
    - `catalog_readonly`: `chmod a-w` on this venue's leaf directories `<catalog>/data/*/<iid>` (iid ending `.<VENUE>`) from second :55 of one minute to :10 of the next, so exactly one :02 flush is hit. Their exact prior modes are restored afterwards.
    - `redis_stop`: `docker stop verify-redis`, 60 s, `docker start`. It is evaluated for both venues.
  - The docker/sudo runner, the sleeper and the clock are injected (the pattern of `archive/nightly.py`), so tests never execute docker.
  - The fault is always undone in `finally`. A failed undo is ledgered and the run exits 1.
  - **Log file:** `<VERIFY_DATA_DIR>/chaos/scenarios.jsonl`, appended and fsynced. A `start` line is written before the fault and an `end` line after it is undone. Each line has `scenario`, `venues`, the `target` container/path, `fault_start_ns`/`fault_end_ns`, and every command with its return code. A collector image redeploy made by this story is logged as `scenario: "deploy"`, so Story 31.11 can exclude that window too.
- **Chaos tool, refusals:** each is ledgered at `sites.CHAOS_REFUSED` and exits 1; a usage error exits 2. The tool refuses:
  - when `data/.verify-stack` is absent;
  - when the target container is not `verify-*`;
  - when the log's last scenario is still open (a `start` without an `end`);
  - when fewer than `SCENARIO_SPACING_NS` (6 min, greater than the window margins) have passed since the last `end`;
  - when `sudo -n` is not permitted (`network_cut`). No fault is applied in that case.
- **Evaluate mode:** `--evaluate --venue V [--json]`.
  - Every scenario whose window is settled is evaluated over `[fault_start - 90 s, fault_end + 180 s)`.
  - Unsettled scenarios are listed as pending. They never count as a pass.
  - Per scenario it checks:
    - the windowed conservation counts;
    - the seconds explained per reason;
    - the collector ledger sites seen in the window (`<ERROR_LEDGER_DIR>/<venue>_collector.jsonl`);
    - backfilled and unrecoverable trade counts;
    - the match against the expected-outcome table (`verification/domain/chaos.py`, pure).
  - Exit 0 iff at least one scenario was evaluated, every evaluated one has 0 unexplained trades, 0 unexplained seconds, 0 `archived_twice`/`duplicate_rows`/`row_and_reason`, and it matches its table row. Otherwise exit 1.
- **Expected-outcome table:** per scenario (and per venue where they differ), the required second reasons, the forbidden second reasons, the required ledger sites, and whether backfilled trades or unrecoverable trades must be greater than 0. It is derived from the capture code and written with its reasoning in DATA_DICTIONARY §1.23. Starting rows:

  | Scenario | Required second reasons | Forbidden second reasons | Required ledger sites | Trade counts |
  |---|---|---|---|---|
  | `sigkill_flush` | `restart` | `write_failed` | `collector.restart_gap`, `collector.trade_backfill` | |
  | `graceful_restart` | `restart` | `write_failed` | `collector.restart_gap`, `collector.trade_backfill` | |
  | `pause_15s` | | `restart`, `catch_up_cap`, `write_failed` | `collector.stale_trade`, `collector.trade_backfill` | |
  | `pause_45s` | `catch_up_cap` | `restart`, `write_failed` | `collector.skipped_seconds`, `collector.stale_trade`, `collector.trade_backfill` | |
  | `network_cut` | `stale` | `restart`, `write_failed` | `collector.trade_backfill` | Bybit: backfilled > 0. Hyperliquid: unrecoverable > 0 (D-48) |
  | `catalog_readonly` | `write_failed` | `restart` | `collector.flush_write` | |
  | `redis_stop` | | `restart`, `write_failed`, `catch_up_cap` | `collector.snapshot_publish` | |

  A row is changed only with the root cause written next to it, never to fit an observation.
- **D-61 fix (capture, shared service):**
  - At `_prepare_ids`, each instrument's `TradeIntake.last_trade_ts` is seeded from the newest archived trade's `ts_event` in the dedup horizon. The read failure is ledgered at `collector.dedup_seed`.
  - After the first `apply` in `run()`, one backfill per feed is scheduled with the reason `restart: archived baseline`. Its since-map comes from those seeds, so the restart gap, including a SIGKILLed buffer, is fetched or noted as `trades_unrecoverable` (`depth`/`fetch_failed`).
  - The module-docstring `Known limit:` and D-61/D-75 are updated with this story's evidence.
- Verification boundaries hold (DATA-02): `verification.chaos` imports no capture code and joins `VERIFICATION_ROOTS` and the runtime probe. `test_sites.py`/`test_tool_ledgers.py` cover the new site.
- Code rules:
  - LGPL header, full typing, functions of about 30 lines or fewer, complexity ≤ 10.
  - No module-level mutable state and no new dependency.
  - Every comparator or matcher ships a planted-defect test.
- **Runs (agent-executed on the running verify stack):**
  1. Rebuild and redeploy the two verify collectors with the D-61 fix (`--no-deps`, logged as `deploy`).
  2. Run `sigkill_flush`, `graceful_restart`, `pause_15s`, `pause_45s` and `catalog_readonly` for both venues, and `redis_stop` once, spaced by the spacing rule.
  3. Attempt `network_cut`. It is refused without sudo.
  4. Once the windows settle, run `--evaluate` for both venues.
  5. Root-cause every mismatch and every non-zero unexplained count. Fix it with a test and an audit row, or register it OPEN from D-136 with the reproducing scenario.
  6. Record the numbers in the `VERIFICATION_REPORT.md` fault-injection row, the audit, DATA_DICTIONARY §1.23, the DEPLOY_CHECKLIST 31-10 entry and `platform/CLAUDE.md` DATA-02's tool list.

**Block If:** none. Anything that needs a human is finalized as `awaiting-operator` with `operator_actions`:
- the `network_cut` scenario needs sudo;
- the verify stack is down or cannot rebuild;
- a mismatch needs a product decision.

**Never:**
- Never touch the production stack, `nautilus_trader/`, `crates/` or `sprint-status.yaml`.
- Never use docker to gain the root that `sudo` withholds.
- Never loosen conservation's pass rules.
- Never edit a table row to fit an observation.
- Do not build 31.11's `verify_day` or the verdict table.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected |
|---|---|---|
| Window clean | rows or coverage for every second, trades archived | pass, exit 0 |
| Planted second | one rowless, reasonless second inside the window | unexplained 1, exit 1 |
| Outside window | an unexplained second just before `start` | not counted |
| Unsettled window | `end` in the current hour | refused / pending |
| Required reason missing | `sigkill_flush` window without `restart` | mismatch, exit 1 |
| Forbidden reason present | `redis_stop` window with `write_failed` | mismatch, exit 1 |
| Required site missing | `catalog_readonly` without `collector.flush_write` | mismatch |
| Undo fails | `docker unpause` returns 1 | ledgered, `end` line records it, exit 1 |
| Open scenario | log's last line is `start` | refused, no fault |
| No sudo | `sudo -n` fails | refused, no rule inserted |
| Restart seeding | archive holds a trade at T, restart | backfill requested since T; the `depth` window noted when the venue's history starts after T |

</intent-contract>

## Code Map

- `platform/verification/conservation.py`, `application/conservation.py:136-340`, `domain/conservation.py:608-740` -- the CLI, `conserve`/`_trade_counts`/`collect_coverage`, and `tally_trades`/`tally_seconds`/`reasons`, which hardcode the day.
- `platform/verification/infrastructure/raw_store.py:174` `RawReader(root, venue, day_hours)`, `catalog_reader.py:364` `CoverageFiles` -- the inputs.
- `platform/verification/candles.py`, `bot_parity.py` -- the thin-root pattern (`Refused`, `error_ledger.job_service`, exits).
- `platform/verification/application/sites.py`, `tests/test_sites.py:25-34`, `tests/test_tool_ledgers.py:39-46` -- sites and ledger tests.
- `platform/verification/tests/test_conservation.py:78-214` -- synthetic day builders to reuse for window tests.
- `platform/tests/test_boundaries.py:2033-2046,2167-2189` -- `VERIFICATION_ROOTS`, the runtime probe.
- `platform/kernel/venue_http.py` -- the venue hosts.
- `platform/archive/nightly.py:181-190` -- the injected subprocess-runner pattern.
- `platform/capture/application/capture_service.py:92-93` (Known limit), `:1740-1830` (`_baselines`, `_schedule_backfill`, `_run_backfill`), `:1832` (`_backfill_instrument`), `:2007` (`_prepare_ids`), `:2239` (`run`); `capture/domain/trade_intake.py:129-280` (`last_trade_ts`, `seed`); `capture/domain/feed_group.py:91-120`; `capture/infrastructure/parquet_writer.py:99` (`recent_trade_ids`).
- `platform/docker-compose.verify.yml:93-160`, `Makefile:212-270` -- containers `verify-{bybit,hyperliquid}-collector` (uid 1000), `verify-redis`, recorders (uid 1001), the marker `data/.verify-stack`.
- `platform/docs/VERIFICATION_REPORT.md:105` (the pending row), `docs/DATA_INTEGRITY_AUDIT.md` (D-61 :189, D-75 :222, next D-136), `docs/DATA_DICTIONARY.md` (§1.22 last), `docs/DEPLOY_CHECKLIST.md` (31-9 last), `platform/CLAUDE.md:56`.

## Tasks & Acceptance

**Execution:**
- [x] `platform/verification/domain/conservation.py`, `application/conservation.py`, `verification/conservation.py`, `tests/test_conservation.py` -- the window mode. Tests: clean window, planted second, outside-window, clipped runs, trades at the window edges, unsettled refusal, and day output unchanged.
- [x] `platform/verification/domain/chaos.py` (new) -- the scenarios, the windows and the expected table, plus a pure `evaluate(observed, expected) -> mismatches`.
- [x] `platform/verification/application/chaos.py` (new) -- run and undo (injected runner, clock and sleeper), the spacing and open-scenario rules, and evaluation over the conservation `Inputs` plus a ledger reader port.
- [x] `platform/verification/infrastructure/chaos_io.py` (new) -- the subprocess runner, scenario-log append and fsync, the ledger JSONL reader, `getaddrinfo`, and the chmod walker.
- [x] `platform/verification/chaos.py` (new root) plus `application/sites.py` -- the CLI.
- [x] `platform/verification/tests/test_chaos.py` (new), `test_sites.py`, `test_tool_ledgers.py` -- every I/O row, the argv per scenario, undo on exception, refusals, and planted mismatches.
- [x] `platform/tests/test_boundaries.py` -- the root and the probe.
- [x] `platform/capture/application/capture_service.py`, `capture/infrastructure/parquet_writer.py` (the newest archived trade `ts_event`), and capture tests -- the D-61 fix, with tests: the baseline is seeded, a restart backfill is scheduled, the `depth` window is noted, and a read failure is ledgered.
- [ ] Runs, then docs: `VERIFICATION_REPORT.md`, `DATA_DICTIONARY.md` §1.23, `DATA_INTEGRITY_AUDIT.md` (D-61/D-75 updates, D-136+), `DEPLOY_CHECKLIST.md` 31-10 (the `network_cut` run with sudo, the VPS rollout of the D-61 fix), and `platform/CLAUDE.md` DATA-02.

**Acceptance Criteria:**
- Given the verify stack, when `python3 -m verification.chaos --scenario S --venue V` runs each agent-runnable scenario, then the fault is applied and undone and `scenarios.jsonl` holds its start and end.
- Given the settled scenario windows, when `--evaluate` runs per venue, then every scenario reports 0 unexplained trades and seconds and matches its table row, or each mismatch is fixed or registered OPEN (D-136+) naming the scenario that reproduces it.
- Given the story ships, when the spec is finalized, then the `network_cut` run (needs sudo) and every other human-only step are unchecked items in `docs/DEPLOY_CHECKLIST.md` "Deferred operator actions" entry 31-10, no `operator_actions:` frontmatter is written, and the status is `done` (OPS-01).

## Spec Change Log

- 2026-09-30 (resume, operator note): `platform/CLAUDE.md` OPS-01 binds over this spec's `Block If` ("finalized as `awaiting-operator` with `operator_actions`"). Every human-only step (the `network_cut` run with sudo, a stack that cannot rebuild, a product decision) is deferred to DEPLOY_CHECKLIST entry 31-10 and the story finalizes `done`. AC3 amended to match. The intent-contract text is left as frozen; this entry supersedes its `Block If` line. KEEP: everything else in the contract.

## Review Triage Log

### 2026-09-30 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 5: (high 2, medium 2, low 1)
- defer: 0
- reject: 19: (high 0, medium 6, low 13)
- addressed_findings:
  - `[high]` `[patch]` SIGTERM/SIGHUP to the tool mid-fault (a closed terminal, a `kill`) skipped the undo: a collector could stay paused, leaves read-only, DROP rules in. `verification.chaos.main` now turns both into the same unwind as Ctrl-C (undo, `end` line, ledgered `verification.chaos.fault_failed`, exit 1) and restores the prior handlers. Test: a SIGTERM during `pause_15s`'s hold.
  - `[high]` `[patch]` `network_cut`'s uid-1000 rule is host-wide, so a production collector running on the same host would be cut too. It is now refused while any collector service of another compose project runs (`docker ps` labels), with a `Known limit:` for other uid-1000 host processes. Test: a `platform` project's `bybit_collector` refuses the run before any sudo call.
  - `[medium]` `[patch]` `--container` was not checked against `--venue`, so a fault on one venue's collector could be logged and judged as the other's. Now refused. Test added.
  - `[medium]` `[patch]` The D-136 audit row and the report cell held a result placeholder while the row said FIXED. The live re-test was run (`redis_stop` 17:06:48Z, then both collectors' next status publish over the dead pooled connection arrived) and recorded.
  - `[low]` `[patch]` DATA_DICTIONARY §1.23 and DEPLOY_CHECKLIST said "one restart backfill per feed". Reworded: each instrument is backfilled once, joined to the request of the feed it first speaks on, and there is one ledger entry per feed request.
  - Rejected (by spec, hypothetical, or caught by conservation):
    - the OUTPUT chain missing bridge traffic (the collectors run `network_mode: host`);
    - the restart rows not requiring the `restart: archived baseline` reason (a D-61 regression shows as unexplained trades in conservation);
    - collector-wide site matching;
    - a book feed starting the backfill (the fetch is per instrument over REST, and the feed learns the id first);
    - the kill offset and the read-only hold timing;
    - the Redis retry on `BusyLoading`/refused;
    - no lock or a different `--log` (single-operator tool);
    - a misleading undo after a failed pause;
    - exit 0 alongside an open run (by spec);
    - the seed horizon (a Known limit);
    - margin pins;
    - an `end` append failing on a full disk;
    - a leaf created during the hold;
    - an armed id that never speaks (loud in conservation);
    - an id added later backfilling from an old seed (fills a real gap);
    - one unreadable window failing the whole evaluation (fail-closed);
    - a neighbour hour's torn tail;
    - a malformed log refusing everything (by spec).

### 2026-09-30 — Review pass (follow-up review)
- intent_gap: 0
- bad_spec: 0
- patch: 5: (high 0, medium 3, low 2)
- defer: 0
- reject: 17: (high 0, medium 4, low 13)
- addressed_findings:
  - `[medium]` `[patch]` A second SIGTERM, SIGHUP or Ctrl-C during the undo raised `KeyboardInterrupt` inside `execute`'s `finally`. That skipped the remaining undos (for example two of three `network_cut` rule deletes) and the `end` line. Fixed in two places:
    - `verification.chaos._interrupt` is now one-shot. It is installed for SIGINT too, and ignores every later signal until the tool exits.
    - `_undo` records an undo that an interrupt hit as -1 ("interrupted mid-undo") and still runs the remaining undos.
    - Tests: a network cut whose first rule delete is interrupted still deletes the other two; a SIGTERM and a SIGINT sent during the unpause are ignored. The latter test was mutation-checked: it fails without the one-shot handler.
  - `[medium]` `[patch]` An interrupted run ledgered "any applied fault was undone" unconditionally, and a failed undo went unledgered, because the exception bypassed `_ledger_failures`.
    - `execute` now catches the interrupt during the apply or the hold, undoes the fault, writes the `end` line, and returns `RunOutcome.interrupted`.
    - The CLI then ledgers undo failures at `undo_failed`, and the interrupt at `fault_failed` with whether the undo held.
    - A `KeyboardInterrupt` that still escapes (before any fault is applied, or in the narrow gap between two undo commands) is ledgered with that honest wording.
  - `[medium]` `[patch]` `network_cut`'s uid-1000 rule would silently cut `verify-live-paper` (uid 1000, host network, 31.9's parity input) and any production paper bot. It is now refused while a `live-paper` service of any project runs. The `Known limit:` now names the verify archive's kline fetches, which the archive ledgers and retries itself. DATA_DICTIONARY §1.23 and the DEPLOY_CHECKLIST 31-10 network-cut step (`docker stop verify-live-paper` first) are updated. Test added.
  - `[low]` `[patch]` An `end` line that failed to write after the undo (`OSError`) used to be reported as "chaos refused", which that site defines as "no fault applied". It is now `EndLineLost`, carrying the outcome: undo failures are ledgered, `fault_failed` says the run stays open and must be closed by hand, and it exits "chaos failed". Tests at the service and CLI levels. (The previous pass rejected this; it is fixed here because a mislabelled refusal after a fault is misleading.)
  - `[low]` `[patch]` `_leaf_step.restore` raised `KeyError` (a false UNDO FAILED) when an interrupt landed before `freeze` had read the mode. It now returns an unchanged no-op. Test added.
  - Rejected:
    - the OUTPUT chain missing bridge traffic (every collector runs `network_mode: host`);
    - required sites not tied to the fault (a D-61 regression fails on unexplained trades);
    - the kill offset not checked;
    - flaky expectations that depend on market activity (the table is fixed by spec; flapping fails closed, never passes falsely);
    - HL restart `depth` windows over re-delivered trades (they explain only missing ids);
    - `_prepare_ids` seeding re-added ids (the intake already persisted across a remove/re-add before this story);
    - every run judged forever and no `--since` (fail-closed, operator-rotatable log);
    - the Redis retry on `AuthenticationError`/`BusyLoadingError` (one harmless extra attempt);
    - a leaf created during the hold;
    - the shared 1800 s command timeout;
    - the substring venue check (the compose label still gates it);
    - `deploy` ignoring `--venue`/`--container`;
    - `graceful_restart` sharing sigkill's row;
    - a torn last log line (refused loudly, by spec);
    - no fsync of the log's directory;
    - an unreadable window aborting the evaluation (a documented refusal);
    - `archived_twice` across the window edge.

## Design Notes

**Why a window mode inside conservation, not a copy:** the AC names `verification.conservation` as the judge. Two implementations of the explanation rules would diverge (SSOT-01).

**Why the D-61 fix is in scope:** without it the SIGKILL and restart scenarios can never reach unexplained = 0, and the fix is the upgrade path the Known limit already names. A REST depth shortfall still ends as a `depth` window, which is explained and never silent.

**Why chmod rather than a `:ro` remount:** a remount recreates the container, which would turn the scenario into a restart. The collector writes as the directory owner (uid 1000), so `a-w` makes `write_data` fail with EACCES exactly as a read-only mount would.

**Prior attempt (2026-09-30, run 20260929-113859-0838 dev-1, stopped by `bmad-loop stop` at 11:23):** branch `31-10-prior-attempt` carries the whole WIP: commit `bd7a4c324f` (chaos tool, windowed conservation, D-61 restart backfill, tests: Tasks 1-8 code-complete but unverified) plus the uncommitted redis_bus/redis.py D-136 retry-once fix with `test_status_bus.py` in archive and collection_control. Task 9 (runs + docs) was not started. Reuse it: `git read-tree -m -u HEAD 31-10-prior-attempt && git reset -q`, then re-verify every task and AC rather than trusting checkboxes.

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. verification/tests capture/tests capture/venues/bybit/tests capture/venues/hyperliquid/tests tests/test_boundaries.py -q` -- expected: pass, apart from the known pre-existing failures, which must be unchanged.
- `cd platform && ruff format --check <touched> && ruff check <touched> && mypy verification capture` -- expected: clean.
- `cd platform && python3 -m verification.chaos --evaluate --venue BYBIT` (and `HYPERLIQUID`) -- expected: exit 0, or each failure registered.


## Auto Run Result

Status: done

**Follow-up review** of `535e737c5d` (baseline `153629f4f6`). Blind Hunter and Edge Case Hunter ran over the whole story diff and produced 22 unique findings: 5 patched, 0 deferred, 17 rejected (see the second Review Triage Log entry). No intent_gap or bad_spec was found, and the D-61 capture change drew no real finding.

**What changed in this pass** (all in the chaos tool, the verify-stack-only fault injector):
- **Interrupts:**
  - An interrupt during the apply or the hold ends the fault, undoes it, writes the `end` line and returns `RunOutcome.interrupted`.
  - A second signal is ignored (the handler is one-shot, and now covers SIGINT too).
  - An interrupt during an undo command never skips the later undos.
  - The ledger message now says truthfully whether the undo held, and failed undos are ledgered.
- **`network_cut`** is refused while a `live-paper` bot runs, in any project.
- **`EndLineLost`:** an `end` line that fails to write after the undo is no longer mislabelled as a refusal.
- **Leaf restore:** `_leaf_step.restore` no longer raises a false UNDO FAILED when the mode was never read.

**Files:**
- `platform/verification/application/chaos.py`: execute/undo interrupt handling, `EndLineLost`, the live-paper refusal, the leaf no-op restore.
- `platform/verification/chaos.py`: the one-shot handler (SIGINT/SIGTERM/SIGHUP), interrupted-outcome ledgering, `EndLineLost` handling, docstring.
- `platform/verification/application/sites.py`: the `fault_failed` site's meaning widened (interrupt, lost end line).
- `platform/verification/tests/test_chaos.py`: 6 new tests and 2 updated (a mid-undo interrupt, a leaf interrupted before its read, a lost end line at service and CLI level, the live-paper refusal, a second SIGTERM+SIGINT mid-undo).
- `platform/docs/DATA_DICTIONARY.md` §1.23 and `platform/docs/DEPLOY_CHECKLIST.md` 31-10: the refusal and interrupt behaviour, and stopping `verify-live-paper` before `network_cut`.

**Verification:**
- `python3 -m pytest verification tests/test_boundaries.py tests/test_chaos_contract.py`: 779 passed, 5 skipped (verify-stack/soak-gated).
- The new second-signal test was mutation-checked: it fails with the `SIG_IGN` step removed.
- `ruff format` and `ruff check` are clean on the touched files; `mypy` shows no issues in the 3 touched source/test files.
- Not run on the live verify stack: the changes touch only the tool's interrupt and refusal paths, which no scenario exercises.

**Follow-up review recommended:** no. The fixes are local to the tool's signal, undo and refusal paths, every one is covered by a unit test, and no production (capture) code changed in this pass.

**Residual risks:**
- A first signal that lands exactly between two undo commands (outside any command) still escapes and leaves the run open. It is ledgered with an instruction to check by hand.
- After the first signal, only SIGKILL can stop a hung undo (bounded by the 1800 s command timeout).
- `network_cut` has still never run (sudo; operator step in DEPLOY_CHECKLIST 31-10).
- D-137 and the D-75 reason-loss half remain OPEN, as before.
