---
title: 'Story 31.1: Verification context, independent reference recorder and a clean-slate side-by-side stack'
type: 'feature'
created: '2026-09-29'
status: 'done'
baseline_revision: 'b37be5775a'
final_revision: 'db07035111'
review_loop_iteration: 0
followup_review_recommended: true
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-31-context.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** Every existing check of our stored market data either re-runs our own code on both sides or uses toy inputs. We have no independent source of truth for Bybit and Hyperliquid wire data. There is also no way to run a clean-slate capture next to the live epic runs without port or container-name collisions.

**Approach:** Add a new DDD context, `platform/verification/`, whose reference recorder shares no code with capture. It uses aiohttp, `json` and stdlib only, plus URL builders from `kernel/venue_http.py`. The recorder writes every raw WS frame and REST poll response verbatim, with the local receive time, to hourly zstd JSONL files. A `docker-compose.verify.yml` override and `make verify-*` targets run a `verify` stack side by side. The story leaves that stack running from an empty `platform/data/`, with its start time and the recorder footprint recorded in `docs/VERIFICATION_REPORT.md`.

## Boundaries & Constraints

**Always:**
- **Import rules for `verification/` (non-test modules):**
  - Never import `nautilus_pyo3` (any path), `capture`, `candles`, `ranking`, `views`, `kernel.fold` or `kernel.second_snapshot`. `tests/test_boundaries.py` enforces this.
  - It may import `kernel.venue_http`, `kernel.venues` and `observability`.
  - No other context imports `verification`. The allow-list is empty today and is reserved for `archive`'s nightly composition root (31.11).
  - `verification/domain` stays pure: stdlib and kernel only.
- Read instruments from the venue's `config.toml` with `tomllib`, never from a copied list. Use the same env vars and defaults as the collectors (`BYBIT_COLLECTOR_CONFIG` / `HYPERLIQUID_COLLECTOR_CONFIG`). Recorded set = `instruments` minus `exclude`, deduped in order.
  - Re-read the file every 30 s, as the collector does. On a change, reconnect with the new set and write connection lines tagged with the reason.
- **Record format:**
  - Take the receive time as `time.time_ns()` before any parsing. Store each frame verbatim as a JSON string field.
  - Write one line per frame to `<VERIFY_DATA_DIR>/raw/<venue>/<channel>/<YYYY-MM-DDTHH>.jsonl.zst` (UTC hour of the receive time).
- **Connection events:**
  - Every open, close and error writes a `{"kind":"connection","event":...}` line. These lines go to the venue's `connection` channel and also into every data channel of that endpoint, so any single channel file shows its own gaps.
  - Every failure goes to `observability.error_ledger.record` at a `verification.recorder.*` constant in `verification/application/sites.py`. Silent `continue`/`return` is never allowed.
- **Keepalive and reconnect:**
  - Bybit sends `{"op":"ping"}` every 20 s. Hyperliquid sends `{"method":"ping"}` every 30 s.
  - A feed silent for longer than a stated bound forces a reconnect and a ledger entry.
  - Reconnect uses exponential backoff, capped. The backoff resets after a connection has been healthy for a stated time.
- Build REST URLs only with `kernel.venue_http` builders. Add the WS URL maps and builders there too, with tests.
- **zstd output:**
  - Write through `pyarrow.CompressedOutputStream(..., "zstd")`; add no new dependency.
  - Flush at least every second, so a crash loses at most about 1 s of frames.
  - On reopen, append a new zstd frame. If the existing file has a truncated tail from a crash, first rewrite its complete lines atomically, then ledger the repair.
  - SIGTERM closes the streams cleanly.
- The recorder process uses `umask 002`. Recorders run as `1001:1000`, so a different uid from the collectors' `1000:1000`, but the same group, so the host user and `verify-wipe` can delete their files.
- Every file carries the LGPL header. Type hints everywhere, and functions stay under about 30 lines.
- Any deliberate simplification gets a `Known limit:` comment. Tests are plain pytest functions that return `-> None`, with no mocking of Nautilus internals. Async tests use `asyncio.run`.

**Block If:**
- The Bybit or Hyperliquid public endpoints are unreachable from the dev box, so the fixtures cannot be recorded. Do not fabricate fixtures.
- The base image `nautilus-trader-base:1.229.0` is missing and cannot be built.

**Never:**
- Never modify `nautilus_trader/` or `crates/`.
- Never change `docker-compose.yml`'s services, apart from what `test_images` requires.
- Never start the dYdX `collector` in the verify stack.
- Never bind a port to anything other than `127.0.0.1`.
- Never change any venue `config.toml`.
- Never add a dependency.
- Never touch `sprint-status.yaml`.
- No comparator logic. That belongs to later stories.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Frame routing | Bybit `{"topic":"orderbook.50.BTCUSDT",...}` on the linear socket | channel `linear.orderbook.50`; spot socket → `spot.orderbook.50` | none |
| HL routing | `{"channel":"l2Book",...}` | channel `l2Book`; `subscriptionResponse`/`pong` → `control` | none |
| Non-JSON frame | text that fails `json.loads` | stored verbatim in channel `unparsed` | ledger `verification.recorder.unparsed` |
| Hour rollover | receive time crosses HH:00 | old streams closed; new files; bytes/day per venue logged; prune older than `VERIFY_RETAIN_DAYS` | prune failure ledgered |
| Restart mid-hour | file exists, clean | new zstd frame appended; reader sees old + new lines | none |
| Crash tail | file ends in truncated frame | complete lines kept (atomic rewrite), reader reports truncation | ledger `verification.recorder.truncated_tail` |
| Server closes socket | WS close | `close` line, backoff, `open` line on reconnect, resubscribe | ledger `verification.recorder.connection` |
| Plan edit | config `instruments` changes | reconnect with new set, lines tagged `plan_changed` | unreadable config → ledger, keep last good set |
| REST failure | non-2xx / timeout | line with status or error written, next poll on schedule | ledger `verification.recorder.rest` |

</intent-contract>

## Code Map

- `platform/tests/test_boundaries.py`: registers `CONTEXTS`/`COMPOSITION_ROOTS` and the `GRAPH`. Its domain-purity, venue-URL and `_ID_SUFFIX` guards already apply to the new context.
- `platform/kernel/venue_http.py`: the `BYBIT_URLS`/`HYPERLIQUID_URLS` maps and `bybit_url`/`hyperliquid_info_url`. WS maps go here.
- `platform/kernel/venues.py`: `venue_of`, `bybit_category`, `base_symbol` and `market_kind`, used to turn ids into topics and coins.
- `platform/observability/error_ledger.py`: `start(service)` and `record(site, detail, exc)`.
- `platform/capture/infrastructure/config.py:170-185`: the flat-plan semantics (`instruments` deduped, `exclude`) to mirror.
- `platform/docker-compose.yml`: services, `${REDIS_PORT}`/`${DATA_API_PORT}`/`${DOZZLE_PORT}` uses, the `x-logging` anchor.
- `platform/Makefile`: the `test` target list and the `.PHONY` list.
- `platform/collector.dockerfile`: needs `COPY platform/verification ./verification`.
- `platform/tests/test_images.py`, `tests/test_compose_profiles.py`: existing guards that must stay green.
- `scripts/capture_hl_ws.py`, `scripts/measure_ws_limits.py`: prior aiohttp WS precedents.

## Tasks & Acceptance

**Execution:**
- [x] `platform/kernel/venue_http.py` (+ `kernel/tests`) -- add `BYBIT_WS_URLS` (`wss://stream.bybit.com/v5/public`, testnet `wss://stream-testnet.bybit.com/v5/public`), `bybit_ws_url(environment, category)`, `HYPERLIQUID_WS_URLS` (`wss://api.hyperliquid.xyz/ws`, testnet `wss://api.hyperliquid-testnet.xyz/ws`) and `hyperliquid_ws_url(environment)`. Unknown environment or category raises. -- Keeps all URLs in the kernel.
- [x] `platform/verification/{__init__,domain/__init__,application/__init__,infrastructure/__init__,tests/__init__}.py` -- context package. Docstrings name the invariant "the reference side never imports the code it checks".
- [x] `platform/verification/domain/subscriptions.py` -- pure plan code:
  - Turn instrument ids into endpoints. Bybit: one socket per category (linear/spot), carrying `orderbook.50.S`, `publicTrade.S`, and `tickers.S` for linear only, in args chunks of 10 or fewer. Hyperliquid: one socket carrying `l2Book`, `trades` and `activeAssetCtx` per coin.
  - Also covers the REST poll schedule and frame → channel classification.
- [x] `platform/verification/domain/plan_file.py` -- parse a venue `config.toml` into the recorded id set. Rejects a non-list `instruments`.
- [x] `platform/verification/application/sites.py` -- `verification.recorder.*` ledger site constants.
- [x] `platform/verification/application/recorder.py` -- the supervision loops: WS connection with ping, stale-feed watchdog and backoff; the REST pollers; the plan re-read. Ports (`Protocol`) cover the socket, the HTTP client and the sink.
- [x] `platform/verification/infrastructure/raw_store.py` -- the hourly zstd JSONL writer (flush, append-on-reopen, truncated-tail repair, prune, bytes/day) and the `iter_records` reader, which surfaces a truncated tail.
- [x] `platform/verification/infrastructure/aiohttp_io.py` -- aiohttp adapters for the ports.
- [x] `platform/verification/recorder.py` -- the composition root, `python3 -m verification.recorder --venue {BYBIT,HYPERLIQUID}`. Env vars: `VERIFY_DATA_DIR`, `VERIFY_RETAIN_DAYS` (default 7) and the config path. Starts the ledger, handles SIGTERM, sets umask.
- [x] `platform/verification/tools/record_fixtures.py` + `verification/tests/fixtures/` -- record a few minutes per venue with one forced reconnect, then trim into committed fixtures. Bybit needs a snapshot plus at least 200 deltas; the total stays small.
- [x] `platform/verification/tests/test_*.py` -- cover subscriptions/classification, plan-file parsing, raw-store round trip (rotation, append, crash-tail repair, prune, bytes/day), recorder loop against a local aiohttp WS server (subscribe, record, reconnect, connection lines, ledger), and fixture-parser tests (every frame classifies to a known channel; Bybit snapshot + ≥200 deltas with contiguous `u` per topic inside a connection; HL full two-sided books; a reconnect present).
- [x] `platform/tests/test_boundaries.py` -- register `VERIFICATION` and the `verification.recorder`/`verification.tools.record_fixtures` roots as needed. Add the denylist test and the "nobody imports verification" test.
- [x] `platform/docker-compose.verify.yml` + `platform/tests/test_compose_verify.py`:
  - Override every service with a `verify-` container name.
  - Restate every `${REDIS_PORT}`/`${DATA_API_PORT}`/`${DOZZLE_PORT}` use with the defaults 26379/29100/28080 (`!override` for `ports`).
  - Add the two recorder services: collector image, host network, `1001:1000`, logging anchor equal to the base's, ledger env, `./data/verification` mount, and the venue config mounted `ro`.
  - The test pins all of this against the base file.
- [x] `platform/Makefile` -- add `verification/tests` to `test`. Add `verify-up` (mkdir plus group-writable dirs, explicit service list without `collector`), `verify-down`, and `verify-wipe` (refuses while any `verify` container runs, lists what it deletes, keeps the three toml files). All use `-p verify -f docker-compose.yml -f docker-compose.verify.yml`.
- [x] `platform/collector.dockerfile` -- `COPY platform/verification ./verification`.
- [x] `platform/docs/VERIFICATION_REPORT.md` (new), `docs/DATA_DICTIONARY.md` §1.15 -- skeleton verdict table, soak start time, measured recorder footprint; raw recording format documented.

**Acceptance Criteria:**
- Given the boundary test, when a `verification/` module imports any denied package, then `test_boundaries.py` fails. It also fails when another context imports `verification`.
- Given a Bybit or Hyperliquid config file, when the recorder runs, then it records exactly that file's collected set on the documented channels and REST cadences. The cadences are: Bybit instruments-info, open-interest (linear only) and recent-trade every 30 s, orderbook every 60 s; Hyperliquid metaAndAssetCtxs every 30 s, l2Book every 60 s.
- Given `make test`, when it runs, then `verification/tests` runs and passes using only committed fixtures, with no network access.
- Given `make verify-up` from an empty `platform/data/`, when it completes, then the verify stack runs under `verify-*` names on 26379/29100/28080. That stack is the two collectors, archive, ranking_engine, data_api, redis, dozzle and both recorders, and no dYdX collector. Raw files appear under `data/verification/raw/`. `VERIFICATION_REPORT.md` records the start time and the measured bytes/day/venue, RSS and CPU.

## Design Notes

Line shapes (one JSON object per line):
```
{"kind":"frame","recv_ns":1759150000123456789,"endpoint":"linear","raw":"{\"topic\":...}"}
{"kind":"rest","sent_ns":...,"recv_ns":...,"endpoint":"linear","request":"/v5/market/orderbook?category=linear&symbol=BTCUSDT&limit=50","status":200,"raw":"..."}
{"kind":"connection","event":"open","ts_ns":...,"endpoint":"linear","url":"wss://...","subscriptions":[...],"reason":"startup"}
```
`raw` as a JSON string keeps each frame byte-verbatim, including malformed frames, at a small escaping cost that zstd absorbs. Bybit linear and spot share topic names, so the channel carries the endpoint (`linear.publicTrade`). REST request paths: `instruments-info?category=C&symbol=S`, `open-interest?category=linear&symbol=S&intervalTime=5min&limit=1`, `recent-trade?category=C&symbol=S&limit=60` (the spot max), `orderbook?category=C&symbol=S&limit=50`.

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. verification/tests kernel/tests tests/test_boundaries.py tests/test_images.py tests/test_compose_profiles.py tests/test_compose_verify.py -q`: expected all pass.
- `cd platform && make verify-up && docker ps --filter name=verify-`: expected 9 containers running.

## Spec Change Log

## Review Triage Log

### 2026-09-29 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 20 (high 0, medium 10, low 10)
- defer: 0
- reject: 1 (low 1)
- addressed_findings:
  - `[medium]` `[patch]` `nautilus_pyo3` reached the recorder transitively through `kernel.venue_http`. Fixed by moving the dYdX pieces to the new `kernel/dydx_http.py`. The boundary test now walks the transitive import closure and also checks `sys.modules` in a subprocess.
  - `[medium]` `[patch]` `record_fixtures` wiped the good fixtures when a recording failed. Fixed: the recording is validated first, and fixtures are replaced by staging plus rename.
  - `[medium]` `[patch]` The Bybit OI poll read a different source from the collector (`/v5/market/tickers`). Fixed by adding a linear `tickers` REST poll, and the dictionary now documents the source.
  - `[medium]` `[patch]` Docker's stop grace was shorter than the recorder's shutdown path. Fixed with `stop_grace_period: 45s`, pinned by a test.
  - `[medium]` `[patch]` One slow REST poll delayed all the others. Fixed: each poll now runs on its own independent schedule.
  - `[medium]` `[patch]` SIGTERM during a plan change orphaned the old sessions. Fixed: sessions stay tracked until they have stopped.
  - `[medium]` `[patch]` A corrupt hour file blocked its channel for the whole hour. Fixed: the file is moved aside as `.corrupt-<ns>` with one ledger entry, and the `.repair.tmp` cleanup is now guaranteed.
  - `[medium]` `[patch]` Retention never ran for a recorder that restarts more often than hourly. Fixed: it now prunes at startup.
  - `[medium]` `[patch]` An `OSError` during rotation accounting crashed the recorder. Fixed: it is guarded and ledgered.
  - `[medium]` `[patch]` `verify-wipe`/`verify-up` did not guard against another stack using the same `data/`. Fixed with a mount-inspection guard.
  - `[low]` `[patch]` A cancelled REST poll wrote no line. Fixed: it now writes a `cancelled` line.
  - `[low]` `[patch]` A reconnect requested mid-connect was lost. Fixed: it is kept and tags the next open.
  - `[low]` `[patch]` A Hyperliquid frame with a non-string `channel` crashed classification. Fixed: it is filed as unknown and ledgered.
  - `[low]` `[patch]` A Hyperliquid JSON `null` body counted as a good poll. Fixed: it is now a refusal.
  - `[low]` `[patch]` Late lines cost one full-file scan and one zstd frame each. Fixed: one late stream is cached per channel and hour.
  - `[low]` `[patch]` `VERIFY_RETAIN_DAYS` was not validated. Fixed.
  - `[low]` `[patch]` A text frame with invalid UTF-8 had no documented handling. Now a `Known limit:`, with the close recorded and ledgered.
  - `[low]` `[patch]` Added `Known limit:` notes for per-endpoint staleness, the looser plan parser with its re-read phase, and the risks shared through the kernel helpers.
  - `[low]` `[patch]` The `recent-trade` limit is now set per category (linear 1000, spot 60) and documented as a sample.
  - `[low]` `[patch]` The report's code revision is now the story commit.

### 2026-09-29 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 17: (high 1, medium 7, low 9)
- defer: 0
- reject: 3: (low 3)
- addressed_findings:
  - `[high]` `[patch]` `make verify-wipe` typed in the live checkout (its stack stopped) would `rm -rf` live `data/`. `verify-up` now marks its data/ (`data/.verify-stack`, kept by the wipe) and `verify-wipe` refuses an unmarked data/.
  - `[medium]` `[patch]` A plan re-read landing mid-save (the plan store truncates, then writes) parsed as an empty plan and stopped every session. A file with no `instruments` key is now refused (last good plan kept, ledgered); an explicit `instruments = []` is still a valid empty plan.
  - `[medium]` `[patch]` The recorders' single-file config mount kept the old inode after a host-side replacement, so they silently diverged from a restarted collector. They now mount the config's directory read-only; compose, the dictionary and the report document the collector's restart rule.
  - `[medium]` `[patch]` `recv_ns` skew under event-loop stalls (crash-tail repair, rotation, large REST bodies) was undocumented. Added a `Known limit:` with the upgrade path (a writer thread) in `aiohttp_io.py` and the dictionary.
  - `[medium]` `[patch]` The boundary test was a denylist only, so a future `kernel.indicators` import would pass. Added an allowlist test of every in-repo module verification reaches.
  - `[medium]` `[patch]` A plan refused at start was never ledgered, so a crash-looping container left no cause in the durable ledger. `startup_plan` ledgers `verification.recorder.plan` before re-raising.
  - `[medium]` `[patch]` `verify-up`/`verify-down` in a second checkout would recreate or remove the first checkout's `verify` project. Both now refuse while a `verify` project from another working dir runs.
  - `[medium]` `[patch]` A zero-filled tail after a power loss made the whole hour file `corrupt` and set it aside. `zstd_frames.scan` now reports an all-zero run after the last whole frame as a truncated tail, so the file is repaired.
  - `[low]` `[patch]` A server close's `detail` came from `close_code`, not from the close frame itself. It now uses the CLOSE message's own code and reason.
  - `[low]` `[patch]` A forced reconnect that lost the race to a stale feed or server close was dropped. The next `open` is now tagged with it.
  - `[low]` `[patch]` A failed deliberate close left `_deliberate` set, so the next real close looked deliberate. It is now cleared.
  - `[low]` `[patch]` `verify-up` now refuses a host primary group other than 1000, which the recorders write as.
  - `[low]` `[patch]` `record_fixtures`:
    - It creates `--out` before recording.
    - It validates `0 < --reconnect-after < --seconds`.
    - `_swap_in` restores the committed fixtures if its second rename fails, or if an earlier swap died between its renames.
  - `[low]` `[patch]` A Bybit 200 with an empty `result.list` (an unlisted symbol) counted as a good poll. It is now a refusal.
  - `[low]` `[patch]` `VERIFICATION_REPORT.md`: the revision note now explains why either revision reproduces the collectors, and the endpoints row names the linear `tickers` REST poll.
  - `[low]` `[patch]` `kernel.dydx_http` imported the private `_rooted`. It is now the public `kernel.venue_http.rooted_path`.
  - `[low]` `[patch]` `verify-up` created `$(RCLONE_CONFIG_DIR)` from inside `data/`, so a relative value landed in the wrong place. It is now created from the platform dir, as in `up`.

### 2026-09-29 — Review pass (follow-up 2)
- intent_gap: 0
- bad_spec: 0
- patch: 17: (high 1, medium 7, low 9)
- defer: 0
- reject: 7: (low 7)
- addressed_findings:
  - `[high]` `[patch]` A forced reconnect requested while not connected, or one that lost the race to a real close, skipped every later backoff until a connect succeeded. During a venue outage that became a tight connect loop, hammering the venue, the ledger and the `connection` files. Now the request skips one pause only (`_skip_pause`), and a lost race keeps the normal backoff.
  - `[medium]` `[patch]` A transient `OSError` while reading an intact hour file (EACCES, EMFILE, EIO) set the file aside as `.corrupt-<ns>`, which hid it from readers. Only a decode failure (a `ValueError`, or pyarrow's errno-less decompress `OSError`) sets a file aside now; anything else is ledgered as a lost line, and the file is left in place.
  - `[medium]` `[patch]` After a failed open or write, every later line for that channel reopened the file, re-read the whole hour file and tried to rewrite it on the event loop (for example with the disk full). A failed (channel, hour) is now retried once per tick, and the lines lost meanwhile are counted in one `write` entry.
  - `[medium]` `[patch]` A crash tail in a file whose hour ended before the restart stayed truncated forever, so `iter_records` raised `TruncatedTail` on every read. At start the store now repairs each channel's two newest files.
  - `[medium]` `[patch]` A session cancelled at the stop grace wrote no `close` line. `_cycle` now writes `close`/`cancelled` before re-raising.
  - `[medium]` `[patch]` The verify stack's archive inherited the live `RCLONE_REMOTE`/`RCLONE_BUCKET`, so enabling backup would `rclone sync` a clean-slate catalog over the live bucket. The override blanks both, so an enabled backup refuses start instead. A test pins this.
  - `[medium]` `[patch]` `verify-up` in a live checkout whose stack was stopped marked the live `data/`, and a later `verify-wipe` would then delete it. `verify-up` now refuses an unmarked `data/` that holds anything but the three toml files.
  - `[medium]` `[patch]` A `docker ps` failure (daemon down, socket permission) printed nothing, so every "is anything running" guard passed. Each verify target now checks that the daemon answers first.
  - `[low]` `[patch]` `environment = "Mainnet"` crash-looped the recorder, although the collector accepts any case. It is now lower-cased, as `core_config_from_dict` does.
  - `[low]` `[patch]` An invalid `VERIFY_DATA_DIR`/`VERIFY_RETAIN_DAYS` exited before the ledger started. The ledger now starts first and the refusal is ledgered at `plan`. A retention value over 4300 digits now raises the intended `SystemExit`, not a bare `ValueError`.
  - `[low]` `[patch]` The checkout guards compared compose's unresolved `working_dir` label and the mount sources against `realpath` only, so a symlinked checkout failed them. They now match both the given and the resolved path.
  - `[low]` `[patch]` A container bind-mounting a directory above `data/` (platform/ or the repo) passed the data-users guard. Ancestor mounts now match too.
  - `[low]` `[patch]` The Hyperliquid fixture trim counted frames per channel, not per coin, so with more than one coin the first coin used up the budget. The key is now channel plus coin.
  - `[low]` `[patch]` `record_fixtures` accepted `--frames`/`--book-frames`/`--rest-lines` of 0 or below, so a data-less trim replaced the fixtures. These are now refused before recording.
  - `[low]` `[patch]` A crashed session was ledgered `crash` twice, once by `_raise_if_ended` and again at shutdown. Now it is ledgered once.
  - `[low]` `[patch]` The backoff doubled even when the pause was skipped. It now doubles only after a pause that was actually waited.
  - `[low]` `[patch]` `VERIFICATION_REPORT.md` did not say which commit reproduces the recorder bytes of each interval. Each interval now names its commit, and recorder gap 3 is recorded.

## Auto Run Result

Status: done

**Summary:** A second follow-up review of Story 31.1: the `platform/verification/` context, the reference recorder and the `verify` stack. It found 17 real defects. All were patched, and none needed a spec change. The worst was a tight reconnect loop during a venue outage after a forced reconnect. The two recorder containers were recreated on the patched code with `--no-deps`. The collectors kept running, and the 2 s gap is recorded as "Recorder gap 3" in `VERIFICATION_REPORT.md`.

**Files changed (this pass):**
- `platform/verification/application/recorder.py`:
  - a pause skipped once only (`_skip_pause`), and the backoff doubles only after a real wait;
  - a `close`/`cancelled` line on a cancel at the stop grace;
  - a crash ledgered once.
- `platform/verification/infrastructure/raw_store.py`:
  - crash tails repaired at start;
  - a transient read error is no longer a corrupt verdict;
  - a failed (channel, hour) is retried per tick, with lost lines counted.
- `platform/verification/application/sites.py`: the `plan`/`write` comments.
- `platform/verification/domain/plan_file.py`: `environment` lower-cased.
- `platform/verification/recorder.py`: the ledger starts before the env parsing, and a refusal is ledgered; the retention digit limit.
- `platform/verification/tools/record_fixtures.py`: the Hyperliquid per-coin trim key, and trim counts of at least 1.
- `platform/Makefile`:
  - `VERIFY_DOCKER_UP` on all three targets;
  - `VERIFY_HERE`, with both paths matched;
  - ancestor mounts;
  - `VERIFY_UNMARKED_DATA` refusal in `verify-up`.
- `platform/docker-compose.verify.yml`: the archive's `RCLONE_REMOTE`/`RCLONE_BUCKET` blanked.
- `platform/tests/test_compose_verify.py` and `platform/verification/tests/{test_recorder_loop,test_raw_store,test_plan_file,test_record_fixtures}.py`: tests for each change.
- `platform/docs/DATA_DICTIONARY.md` §1.15 and `platform/docs/VERIFICATION_REPORT.md`: the behaviour above, the per-interval recorder revisions and gap 3.

**Review:** 17 patches (1 high, 7 medium, 9 low), 0 deferred. Seven were rejected:
- A plan change reconnects every endpoint. That is the spec's stated behaviour, and the `plan_changed` lines record it.
- The uid guard of the wipe. A partial wipe fails loudly.
- `record_fixtures` reads the committed config. That is deliberate, for reproducible fixtures.
- Prune of an open late stream. The retention is in days and a late stream holds only the previous hour.
- The clock-jump prune, which was already rejected.
- A REST poll stagger. A few requests every 30 s is far under Bybit's IP limit.
- A failed close in `stop`. It is bounded by the stop grace and ledgered.

**Verification:**
- The spec's test set plus `archive/tests` passes: 1169 tests.
- With the `recorder.py` fix reverted, its three new tests fail. With the `raw_store.py` fix reverted, its three new tests fail, along with the updated failed-repair test.
- ruff 0.15.16 format and check are clean, and mypy 1.20.2 is clean on `verification`.
- `make -n verify-up/verify-down/verify-wipe` shows the new guards. The data-users pipeline, run against the live stack, lists only `verify` containers. The other-checkout pipeline is empty for this checkout.
- After the recreation, all 9 `verify-*` containers run. The recorders' image carries the patch, and both `connection` channels show `shutdown` at 11:35:31.61Z and `startup` at 11:35:33.7Z.

**Residual risks:**
- `verify-archive` keeps its old environment until it is next recreated. Backup is disabled, so nothing differs meanwhile.
- The earlier residuals still stand:
  - the collectors' single-file config mount;
  - `recv_ns` skew;
  - Bybit recording volume on nifelheim (31.11);
  - per-endpoint staleness;
  - Hyperliquid collecting SOL only;
  - the 24 h byte figure, still owed.
