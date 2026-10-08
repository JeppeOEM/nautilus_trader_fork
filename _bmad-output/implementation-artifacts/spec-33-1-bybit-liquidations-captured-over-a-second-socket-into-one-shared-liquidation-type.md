---
title: 'Story 33.1: Bybit liquidations captured over a second socket into one shared Liquidation type'
type: 'feature'
created: '2026-10-05'
status: 'done'
final_revision: '738f214e25e2903b3234f66fab9839a08ce57c81'
baseline_revision: 'f070a5f1a27f2b971cd3064bf196fac1dc5a1e43'
review_loop_iteration: 0
followup_review_recommended: true
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-33-context.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** Liquidations exist nowhere in `platform/`. Bybit publishes them on `allLiquidation.{symbol}`, but the Rust Bybit handler drops unknown topics, so forced flow can't reach the chart, screener, alerts or backtests.

**Approach:** Add one shared kernel `Liquidation(Data)` type with exact integer units. Capture Bybit linear liquidations over a second, generic `nautilus_pyo3.WebSocketClient`, decoded in Python. Push the rows straight into capture's flush buffer through a new `CaptureService.ingest_rows`, and publish them on `liquidations:raw`. Record dead-feed and not-running windows as unrecoverable coverage. Report a nightly self-check that matches each archived liquidation against the raw trade archive.

## Boundaries & Constraints

**Always:**
- FORK-01: `nautilus_trader/` and `crates/` stay untouched.
- NFR12: no new dependency (`nautilus_pyo3.WebSocketClient`, stdlib `json`/`decimal`).
- DATA-04 integrity:
  - Precision comes from the instrument definition (`price_precision`/`size_precision` of the pyo3 instrument), never from the value's digits.
  - Wire text becomes `Decimal`, then a raw value at `FIXED_PRECISION`, then `units_of`. An inexact value raises `SnapshotEncodingError` and is ledgered `collector.unencodable`, never rounded.
  - No `float` anywhere.
- Side mapping: wire `S == "Buy"` is `LiquidatedSide.LONG` (a long force-closed, so the forced order is a sell), and `"Sell"` is `SHORT`. The docstring and a test both state it.
- DATA-07: an undecodable frame or unknown topic goes through `report_unknown_message`. Subscribe-ack failures (other than `already subscribed`), publish failures and feed transitions to `down` are ledgered at new `sites` constants. Liveness comes from `is_active()`, never from row arrival.
- AD-D12: fields are appended only.
  - `collector:status`'s aggregate gains `liquidations` as its last key: `"connected"|"reconnecting"|"down"`, or `null` for a venue without the feed.
  - Coverage gains a new kind without touching the existing ones.
- The verification context never imports `kernel.liquidation` or capture; it reads `custom_liquidation` Parquet raw with pyarrow.
- The liquidation report never gates a day's verdict and never asserts 100 %.
- Every simplification is a `Known limit:` with its upgrade path.

**Block If:**
- The live capture shows the wire contradicting the epic's facts: no `allLiquidation` frames on a liquid symbol over ≥30 min, or entries lacking `T/s/S/v/p`.
- `p` or `v` is finer than the definition's precision in a share that makes integer units unusable (>1 % of entries).

**Never:**
- Hyperliquid liquidations (Story 33.2).
- `price_kind`/`confirmed` columns (33.2).
- Candle aggregates (33.3), API, read models or `/ws/live` (33.4), frontend.
- Spot or inverse subscriptions.
- Routing liquidation rows through `_on_data`/`_process_data`.
- Adding `liquidations` to `verdict.TOOLS`, which would make every venue without the feed unverifiable.
- Third-party liquidation APIs.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Long liquidated | `{"T":1759..,"s":"BTCUSDT","S":"Buy","v":"0.010","p":"60000.10"}`, BTCUSDT p-prec 2, s-prec 3 | `Liquidation(side=LONG, price_units=6000010, size_units=10, ts_event=T*1e6)` | none |
| Inexact value | `p` with digits beyond the definition's precision | that entry is not archived; the other entries of the frame are | ledgered `collector.unencodable` |
| Unknown symbol | entry for a symbol with no LINEAR definition | not archived | ledgered `collector.unencodable` ("no instrument definition") |
| Bad frame | non-JSON, missing `data`, missing a key, unknown topic | `Malformed` | `report_unknown_message` on the liquidation feed |
| Cross-frame duplicate | same `(T,S,v,p)` for one id again within 5 s | dropped | none (counted in a debug log) |
| Identical entries in one frame | two equal tuples in one frame | both kept: ids `key` and `key#1` | none |
| Spot id subscribed | `BTCUSDT-SPOT.BYBIT` | no liquidation topic; reason logged once | none |
| Liquidation subscribe fails | `send_text` raises | id stays applied, book kept; capture's 30 s retry resends the topic only | ledgered `collector.liquidation_feed` |
| Ack `success:false` | `ret_msg` other than `already subscribed` | none | ledgered `collector.liquidation_feed` |
| Socket not active | reconnecting or closed while topics held | status `reconnecting`/`down`; on recovery one `liquidations_unrecoverable` (`feed_down`) line per held id | ledgered on transition to `down` |
| Collector restarted | snapshot restart span for a liquidation id | a `liquidations_unrecoverable` (`not_running`) line for the same span | none |
| Publish fails | Redis down | rows still archived | ledgered `collector.liquidation_publish` |

</intent-contract>

## Code Map

- `platform/scripts/capture_hl_ws.py`: the wire-investigation tool. `--topic`/`--coin` and the liquidation summary are restored from the prior attempt and re-verified, including the precision-from-text fix.
- `platform/kernel/open_interest.py`, `platform/kernel/second_snapshot.py` (`units_of`, `FIXED_PRECISION`, `SnapshotEncodingError`): the custom `Data` and integer-unit precedents.
- `platform/capture/venues/bybit/client.py`, `__main__.py`: the client routing (`_product_type`, `WireChannels`) and the composition root.
- `platform/capture/application/capture_service.py`: `poll_loop` (~2342), `_subscribe_one` (~2234), `_note_restart_gaps` (~1342), `capture_status` (~2308), coverage pending lines.
- `platform/capture/application/{sites.py,feed.py,ports.py}`, `platform/capture/domain/coverage.py`, `platform/capture/infrastructure/redis_stream.py`.
- `platform/collection_control/application/status.py` (`plan_aggregate`) and its tests (`test_status_replay.py` `_APPENDED_KEYS`, `test_control.py:242`).
- `platform/verification/domain/conservation.py` (`_COVERAGE_KEYS`, `parse_coverage_line`), `verification/application/conservation.py` (`_file_entry`), `verification/infrastructure/catalog_reader.py`/`derivs_reader.py` (raw readers), `verification/domain/verdict.py`, `archive/verify_day.py`.
- `platform/tests/test_namespace.py`, `test_boundaries.py` (`KERNEL_MODULES`, `VERIFICATION_ROOTS`, `VERIFICATION_DENIED_MODULES`), `test_coverage_contract.py`.

## Tasks & Acceptance

**Execution:**
- [x] `platform/scripts/capture_hl_ws.py`: keep the restored `--topic`/`--coin`/summary. Run a ≥30 min capture of `allLiquidation` + `publicTrade` on 5 liquid linear symbols and summarise it. Rationale: the wire facts decide the dedup window, the id rule and the units.
- [x] `platform/kernel/liquidation.py` (+ `kernel/__init__.py` docstring), with `kernel/tests/test_liquidation.py`:
  - `LiquidatedSide`, `Liquidation(Data)` with the AC's fields and schema, and `to_dict`/`from_dict`.
  - `from_wire_text(...)` doing Decimal → raw → `units_of`.
  - Properties `price`/`size` (`Price`/`Quantity.from_raw`) and `notional_units()`.
  - A single `register_arrow`; the catalog directory is `custom_liquidation`.
- [x] `platform/capture/application/sites.py`: add `LIQUIDATION_FEED` and `LIQUIDATION_PUBLISH`.
- [x] `platform/capture/application/feed.py`: add `ChannelRetry` (an optional channel failed and was already ledgered; capture keeps the id applied and retries it).
- [x] `platform/capture/application/capture_service.py`:
  - Extract `ingest_rows(rows, site, *, plan_only=True, malformed=())` from `poll_loop`, which calls it.
  - Make `_subscribe_one` treat `ChannelRetry` as applied plus queued for retry.
  - Add `note_coverage(lines)`.
  - Have `_note_restart_gaps` add `not_running` liquidation lines for ids the client reports via `liquidation_ids()`.
  - Have `capture_status` carry `liquidations` from the client's `liquidation_state()` when the client has it.
- [x] `platform/capture/application/ports.py`: add a defaulted `liquidations: str | None = None` field to `CaptureStatus`.
- [x] `platform/capture/domain/coverage.py`: add `LiquidationsUnrecoverable` (`kind: liquidations_unrecoverable`, `instrument_id`, `reason: feed_down|not_running`, `from_ns`, `to_ns`) to `CoverageLine`.
- [x] `platform/capture/infrastructure/redis_stream.py`: add `LIQUIDATIONS_CHANNEL = "liquidations:raw"`, `publish_liquidation_batch` and `RedisLiveStream.publish_liquidations`.
- [x] `platform/capture/venues/bybit/liquidations.py`:
  - `parse_liquidation_frame(frame_json, definitions, ts_init) -> list[Liquidation | Unencodable] | Malformed`.
  - `definitions_of(instruments)`.
  - `BybitLiquidationFeed`, which owns the socket (`kernel.venue_http.bybit_ws_url(env, "linear")`, heartbeat with `{"op":"ping"}`, and a `post_reconnection` that resubscribes every held topic in requests of 10 args).
  - The feed's `subscribe`/`unsubscribe` go through its own `WireChannels(BYBIT_WS_FRAMES_PER_SECOND)`.
  - Acks are handled, frames are deduped within a 5 s window, and rows go to ingest and then publish.
  - A monitor loop handles state transitions, the `down` ledger, `feed_down` coverage, and reconnecting after a failed initial connect.
- [x] `platform/capture/venues/bybit/client.py`:
  - An optional `liquidations` feed: connect, disconnect, and LINEAR-only routing in `subscribe`/`unsubscribe` (failure is ledgered, then `ChannelRetry`).
  - `liquidation_state()` and `liquidation_ids()`.
- [x] `platform/capture/venues/bybit/__main__.py`: build the feed in the client factory, attach `capture.ingest_rows`/`note_coverage`, publish through the same `RedisLiveStream`, and add the monitor loop.
- [x] `platform/collection_control/application/status.py`: append `"liquidations"` after `last_refusal`. Update `test_status_replay.py`'s `_APPENDED_KEYS` and `test_control.py`'s last-keys assertion.
- [x] `platform/capture/venues/bybit/tests/test_liquidations.py`: cover the parser on frames recorded from the capture, the side mapping, the dedup window and in-frame ids, LINEAR-only routing, units at precision 2 and 6 with real `Price`/`Quantity`, unencodable/malformed ledgering, and status and coverage transitions.
- [x] `platform/capture/tests/`: tests for `ingest_rows` (the plan filter, bypassing liveness), the `ChannelRetry` path, the `not_running` lines and the status field.
- [x] Verification:
  - `verification/domain/conservation.py`: the new kind, parsed by every coverage reader. `application/conservation.py` ignores it. Update `tests/test_coverage_contract.py`.
  - New verifier:
    - `verification/liquidations.py`, the composition root (CLI `--venue --day --json --catalog`).
    - `verification/application/liquidations.py`.
    - `verification/domain/liquidation_check.py`, the pure match: same size, forced aggressor side, |Δt| ≤ 2 s, each trade used once.
    - `verification/infrastructure/liquidation_reader.py`.
  - The report is per instrument: total, matched, share, unmatched ids (capped) and unrecoverable seconds. A venue other than BYBIT reports `applicable: false`.
  - Register the tool in `verification/application/sites.py`, `test_sites.py`, `test_tool_ledgers.py` and `test_boundaries.py` `VERIFICATION_ROOTS`, and add `kernel.liquidation` to the denied modules.
  - Tests: `verification/tests/test_liquidations.py`.
- [x] `platform/verification/domain/verdict.py` + `platform/archive/verify_day.py`:
  - `LIQUIDATION_VENUES` and `summarise_liquidations(code, body)`.
  - `verify_day` runs the report child for a liquidation venue on both the reference and no-reference paths and stores it under the result key `liquidations`, never in `types` or the verdict.
  - Tests in `archive/tests/test_verify_day.py`.
- [x] `platform/tests/test_namespace.py`, `test_boundaries.py`: add `Liquidation`/`kernel.liquidation`.
- [x] Docs:
  - `docs/DATA_DICTIONARY.md`: a new §1.26 (type, units, side, bankruptcy price, dedup rule, channel, coverage, capture findings table with date and method), plus amendments to §1.12, §1.16 and §1.24.
  - `docs/DATA_INTEGRITY_AUDIT.md`: D-147 side sign, D-148 bankruptcy vs fill price, D-149 no backfill, D-150 dedup-key collision, and a D-65-style frames/s note.
  - `CLAUDE.md`: "What is collected".
  - `platform/CLAUDE.md`: "Adding a venue" step 1.
  - `docs/DEPLOY_CHECKLIST.md`: the deferred operator action.

**Acceptance Criteria:**
- Given a batch of `Liquidation`s, when written with `ParquetDataCatalog.write_data` and read back, then the rows are identical and stored under `custom_liquidation/`.
- Given a running Bybit collector with a LINEAR id planned, when a liquidation frame arrives, then its rows are in the next flush and on `liquidations:raw`, and `tests/test_hotpath.py` is unchanged.
- Given the feed is not active, when `collector:status` publishes, then the aggregate's last key `liquidations` reads `reconnecting` or `down`. Once the feed recovers, the coverage record holds a `liquidations_unrecoverable` window per held id.
- Given a catalog day with liquidations and trades, when `python3 -m verification.liquidations --venue BYBIT --day D --json` runs, then it prints the per-instrument matched share and the unmatched ids, exits 0, and `archive.verify_day` stores the summary under `liquidations` without changing `verification`.

## Spec Change Log

## Review Triage Log

### 2026-10-05 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 21 (high 8, medium 7, low 6)
- defer: 1 (medium 1)
- reject: 1 (low 1)
- addressed_findings:
  - `[high]` `[patch]` A resubscribe chunk that failed after a reconnect stayed "held" and was never resent, with no coverage window. Fixed: the failed chunk is released and the monitor resends it; its windows stay open.
  - `[high]` `[patch]` A subscribe refused by Bybit (`success:false`) stayed held and was never retried. Fixed: requests carry a `req_id` mapping back to their ids, and a refusal releases them for a resend every 10 s.
  - `[high]` `[patch]` A reconnect shorter than the 1 s monitor tick, and the time between reconnect and resubscribe, left no window. Fixed: `post_reconnection` opens every held id's window from the last known-good tick, and only Bybit's acks close windows, never `is_active()`.
  - `[high]` `[patch]` There was one feed-wide window, so an id removed mid-outage lost its window and an id added mid-outage got one from before it existed. Fixed: per-id windows, and an unsubscribe writes that id's open window first.
  - `[high]` `[patch]` An exact key-set check meant an extra wire field would drop every frame. Fixed: the five keys are required as a subset; extra keys are kept, with one warning per new key set.
  - `[high]` `[patch]` A half-open socket would stay "connected" with nothing arriving. Fixed: `idle_timeout_ms = 60 000` (3 × heartbeat; Bybit's pong counts as data).
  - `[high]` `[patch]` A SIGKILL lost an open window. Fixed: windows open longer than 60 s are written up to now and restarted.
  - `[high]` `[patch]` (found in my own pass) A subscribe whose ack never arrived kept its gap open forever and was never resent, and `_requests` leaked. Fixed: after `ACK_TIMEOUT_NS` (30 s) the request is ledgered and released, then resent with its window still open. Test added.
  - `[medium]` `[patch]` A window started at detection time, missing in-flight entries. Fixed: windows start `WIRE_LAG_NS` (3 s, against a measured 2,867 ms max lag) before the known-good instant and are documented as an upper bound.
  - `[medium]` `[patch]` A reopen did not close the previous socket, and a failed reopen kept a dead socket. Fixed: the old socket is closed first, and a failed reopen leaves `None`.
  - `[medium]` `[patch]` An exception in the frame handler was not ledgered. Fixed: it is ledgered at `collector.liquidation_feed`.
  - `[medium]` `[patch]` A `T` in the wrong unit could hit a uint64 overflow at flush. Fixed: an entry more than `MAX_TS_INIT_SKEW_NS` from its arrival is `Unencodable`.
  - `[medium]` `[patch]` Greedy nearest-fill matching undercounted. Fixed: liquidations are taken in time order, each with the earliest unused eligible trade (optimal for equal-width windows). Test added.
  - `[medium]` `[patch]` The verifier held the whole coverage history and every instrument ever seen (MEM-01). Fixed: only day-overlapping windows are kept, and only ids with that day's rows or windows are reported.
  - `[medium]` `[patch]` The nightly summary dropped `coverage_present`. Fixed: it is carried, so a missing record reads as unknown, not 0.
  - `[low]` `[patch]` `LIQUIDATION_VENUES` was defined twice. Fixed: one owner, `verification.domain.verdict`.
  - `[low]` `[patch]` The DEPLOY_CHECKLIST wording was wrong about the nightly summary's contents. Corrected.
  - `[low]` `[patch]` An id with no definition got `ChannelRetry` every 30 s. Fixed: ledgered once, no raise.
  - `[low]` `[patch]` A raised main-channel release skipped the liquidation unsubscribe. Fixed: it now runs in `finally`.
  - `[low]` `[patch]` An in-process re-add was noted `not_running`. Fixed: only an id's first verdict in a process is noted. Added a Known limit on the stale-book upper bound.
  - `[low]` `[patch]` Test hygiene: tests mutated a module-level dict and imported another test module's private stub. Both fixed.

### 2026-10-05 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 15 (high 2, medium 4, low 9)
- defer: 0
- reject: 4 (low 4)
- addressed_findings:
  - `[high]` `[patch]` While the socket stayed down for more than 60 s, every monitor tick pulled the checkpointed gap back to the outage's start, so each tick wrote one more full-span line (62 lines in 120 s). A reconnect after a checkpoint did the same. Fixed: a gap never reopens before the end of that id's last written line (`_written_through`). Test added (150 s outage → 2 lines).
  - `[high]` `[patch]` A half-open socket stays `is_active()` until the 60 s idle timeout, so its window started at the reconnect and understated the gap by up to 60 s. A reconnect before the first tick started at the reconnect too. Fixed: the known-good instant is now also capped by the socket's last received message (pongs included), seeded at open. Test added.
  - `[medium]` `[patch]` `already subscribed` on a resubscribe of up to 10 topics confirmed all of them, although Bybit names only the refused topic. Fixed: a one-topic request is confirmed; of a batch, only the topics `ret_msg` names; the rest are released and resent one by one with their gaps open. Test added.
  - `[medium]` `[patch]` A permanently refused topic (`handler not found`, e.g. delisted) was resent and ledgered every 10 s forever. Fixed: per-id backoff doubling from 10 s to 600 s, reset by a success ack or a fresh hold. Test added.
  - `[medium]` `[patch]` Capture's retry of an already-held topic registered a request without sending it, which gave a false "no ack" ledger line 30 s later and a needless release/resend. Fixed: the request is registered only when the frame actually goes out. Test added.
  - `[medium]` `[patch]` After a failed unsubscribe, a re-add found the topic still held, sent nothing and kept its gap open forever. Fixed: the topic is forgotten after the ledgered failure, so the re-add subscribes again. Test added.
  - `[low]` `[patch]` A subscribe while the socket was reconnecting raised `ChannelRetry` per id, adding to the feed's own resend. Fixed: it only holds the id; the reconnect's resubscribe or the monitor's resend sends it. Test added.
  - `[low]` `[patch]` Requests in flight across a Rust-side reconnect expired 30 s later as false "no ack" lines. Fixed: `_on_reconnected` forgets them. Test added.
  - `[low]` `[patch]` A reconnect callback landing after `disconnect()` reopened gaps and spawned an uncancelled task. Fixed: ignored while closing. Test added.
  - `[low]` `[patch]` An id unsubscribed while `_resubscribe` slept between chunks was still resent. Fixed: each chunk is filtered by the currently held topics. Test added.
  - `[low]` `[patch]` An in-flight ack of a subscribe made before an unsubscribe and re-add closed the re-add's gap. Fixed: unsubscribe removes the id from pending requests. Test added.
  - `[low]` `[patch]` The class invariant claimed "archived or windowed", but malformed and unencodable entries are only ledgered. Docstring corrected.
  - `[low]` `[patch]` `_note_restart_gaps` built the client's liquidation-id set on every per-second verdict. Fixed: it returns early when no id is new.
  - `[low]` `[patch]` DATA_DICTIONARY §1.26 said the nightly match pairs the "nearest" trade, but it pairs the earliest unused one. Corrected; the ack, backoff and window rules above are documented there too.
  - `[low]` `[patch]` The `VenueFeed` port documented `liquidation_state() -> str`, but the client returns `str | None`. Corrected.

### 2026-10-05 — Review pass (second follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 10 (high 2, medium 4, low 4)
- defer: 0
- reject: 7 (medium 1, low 6)
- addressed_findings:
  - `[high]` `[patch]` On a restart, an id whose main subscribe failed at start was not liquidation-held at its first verdict, so its `restart` span got no `not_running` window. It was still marked first-verdict-noted, and its retried subscribe opened a gap only from that subscribe. Fixed: the service hands every first-verdict restart span to the client (`note_liquidation_restart`, replacing `liquidation_ids()`). The feed writes the `not_running` window for any id with a LINEAR definition, held yet or not. A not-yet-held id's window runs on from the span's end (its subscribe opens the gap there; an unsubscribe or the shutdown writes it as `feed_down`). Tests added.
  - `[high]` `[patch]` A liquidation batch whose catalog write failed was ledgered as LOST, but no coverage window recorded it, so the nightly self-check saw fewer liquidations and 0 unrecoverable seconds. Fixed: a new liquidation reason, `write_failed`, over the lost rows' `ts_event` span, added to the capture domain and to the verifier's restated set. Test added.
  - `[medium]` `[patch]` `_TOPIC_IN_TEXT` stopped at `-`, so a dated future (`BTCUSDT-26DEC25`) named in `already subscribed` read as `BTCUSDT`: it confirmed the perpetual without proof and never confirmed the future. Fixed: the class admits `-`. Test added.
  - `[medium]` `[patch]` `already subscribed` confirmed the only id left in a request even when an unsubscribe had trimmed it to one, so the trimmed-away id's name confirmed the other id. Fixed: the named topics are confirmed; a message naming none confirms only a request *sent* with one topic (its original size is now kept). Tests added.
  - `[medium]` `[patch]` Releases of refused or lost topics were awaited through `WireChannels.release` with a no-op send. Each spent a pacing slot and re-read `self._wire`, so a release still running across a reconnect could unmark a topic the new socket had just subscribed. Fixed: `WireChannels.forget` (synchronous, no wire call, no pacing) replaces `_release` and `_nothing`.
  - `[medium]` `[patch]` At shutdown, frames the Rust client had already queued could run after `disconnect()`. Their rows landed in a buffer the final flush had already taken, under a confirmed id with no window. And the next process's `not_running` window had no wire-lag head. Fixed: `disconnect()` closes the socket, then writes every held id's window (a confirmed id's last `WIRE_LAG_NS`), and `_on_message` handles nothing after it. `note_restart` widens the head by `WIRE_LAG_NS`. Test added.
  - `[low]` `[patch]` A refusal backed off every id of a several-topic request, so one delisted symbol delayed its healthy neighbours on every reconnect. Fixed: only the topics the refusal names back off (all, when it names none); the rest are resent at the next round. Test added.
  - `[low]` `[patch]` During a long connect outage every reopen (every 10 s) ledgered a line. Fixed: the outage's first failed open is ledgered, the repeats are logged, and the success logs the attempt count. Test added.
  - `[low]` `[patch]` Checkpoint coverage growth during a long outage had no ceiling stated. Fixed: `Known limit:` with the upgrade path on `CHECKPOINT_NS`.
  - `[low]` `[patch]` `_subscribe_twin`'s comment said capture's retry loop never retries the twin, but a `ChannelRetry` from the liquidation topic now re-runs `subscribe`, which resends it. Comment corrected.

## Design Notes

- **Not-running windows reuse the restart-gap signal.** A quiet hour has no rows, so the last archived liquidation cannot say when the collector stopped. The newest archived snapshot second, already used for the `restart` seconds span, can.
- **`ChannelRetry` keeps the id applied.** The epic wants the topic retried by capture's existing loop. A plain exception would have un-applied the id and forgotten its book, losing trades over a liquidation hiccup.
- **Id rule:** `venue_event_id = f"{T}:{S}:{v}:{p}"` (wire texts), with a `#k` suffix for the k-th identical entry inside one frame. Dedup only drops an id already seen within 5 s.

## Verification

**Commands:**
- `cd platform && python3 -m pytest kernel/tests capture/tests capture/venues/bybit/tests collection_control/tests verification/tests archive/tests bot_tui/tests tests -q`: expected all pass (deselect only the documented TLS node test if present).
- `uvx ruff@0.15.16 format --check` and `uvx ruff@0.15.16 check` on the touched files: expected clean, apart from the 5 findings already present in `capture_hl_ws.py`.


## Auto Run Result

**Status:** done

**Summary.** This was the second follow-up review of Story 33.1, which captures Bybit linear liquidations over a second `nautilus_pyo3.WebSocketClient` into the shared `kernel.liquidation.Liquidation`. Blind Hunter and Edge Case Hunter reviewed the whole diff since baseline `f070a5f1a2`. Ten findings were patched, all in liquidation coverage completeness and the feed's ack and release bookkeeping. Seven were rejected. No spec or intent change was needed.

**Files changed in this pass (under `platform/`):**
- `capture/venues/bybit/liquidations.py`:
  - `note_restart` writes the `not_running` window, with its wire-lag head, for every id that has a LINEAR definition, held yet or not.
  - A not-yet-held id's window runs on until its subscribe, its unsubscribe or the shutdown.
  - The topic regex now admits `-`.
  - `already subscribed` trusts only named topics, or a request sent with one topic.
  - A refusal backs off only the topics it names.
  - Releases are synchronous (`_forget`).
  - The shutdown tail window is written and no frame is handled after close.
  - A connect outage is ledgered once.
  - A `Known limit:` comment is added on `CHECKPOINT_NS`.
- `capture/application/wire_channels.py`: `forget(key)`, an unpaced unmark for a socket with no Rust-side bookkeeping.
- `capture/application/capture_service.py`:
  - Every first-verdict restart span goes to the client's `note_liquidation_restart`, which replaces the `liquidation_ids()` filter.
  - A failed `Liquidation` batch write is a `write_failed` window.
- `capture/venues/bybit/client.py`: `note_liquidation_restart` replaces `liquidation_ids`, and the twin-retry comment is corrected.
- `capture/application/ports.py`: the optional capability is documented.
- `capture/domain/coverage.py`, `verification/domain/conservation.py`: `write_failed` is now a liquidation reason.
- `capture/venues/bybit/tests/test_liquidations.py`, `capture/tests/test_liquidation_seams.py`: 10 new regression tests, and 3 updated for the new capability.
- `docs/DATA_DICTIONARY.md` §1.16/§1.26: the restart, shutdown, `write_failed`, ack, refusal and outage-ledger rules.

**Review findings:**
- Patches applied: 10 (high 2, medium 4, low 4).
- Deferred: 0.
- Rejected: 7.
  - Wall-clock timers (step-back). Rejected in the previous pass too: rare and bounded.
  - Missing intraday merge of `custom_liquidation`. Rejected in the previous pass too: the nightly merge folds the files.
  - Box-versus-venue clock skew in window bounds. Rejected in the previous pass too: NTP is assumed platform-wide.
  - Overlapping `_open` calls. The monitor awaits each round, and the Rust connect is bounded by its 10 s `reconnect_timeout`.
  - A liquidation connect hang blocking start. Bounded by the same Rust 10 s connect timeout.
  - An error in `client.unsubscribe`'s `finally` masking another. `feed.unsubscribe` cannot raise in practice: its send failure is caught and its windows cannot invert.
  - A per-entry `collector.unencodable` ledger line. Bounded by the ledger's per-site cap; this is a once-per-wire-change event.

**Follow-up review recommended:** yes. Both high fixes change coverage semantics: a new liquidation reason that the verifier parses strictly, a new client capability, and a new shutdown tail window. That is data-integrity code changed after the reviewers last looked at it.

**Verification:**
- `python3 -m pytest capture/venues/bybit/tests/test_liquidations.py`: 67 passed.
  - With the previous `liquidations.py` restored, 8 of the new feed tests fail.
  - The 2 new seam tests (the restart hand-off and the `write_failed` window) need the new service code.
- Full suite (`kernel capture capture/venues/{bybit,dydx,hyperliquid} collection_control verification archive bot_tui tests`): 3172 passed, 18 failed, 5 skipped.
  - The 18 failures are the pre-existing `verification/tests/test_candles.py` ×17 and `tests/test_legacy_names.py` ×1, the same set as both earlier runs.
- `uvx ruff@0.15.16 format --check` and `check` are clean on the 9 touched Python files.
- `mypy@1.20.2` reports no type errors on the touched sources. It reports 5 `unused-ignore` notes, on pre-existing lines about pyo3 typing that this environment lacks.

**Residual risks:**
- A brand-new id (never archived) whose subscribe fails at start, and an in-process add whose main subscribe fails, still get no liquidation window before the subscribe. That time is planned but not collected.
- Every shutdown now writes a 3 s window per held id: an upper bound by design.
- From earlier passes:
  - There is no liquidation backfill (D-149).
  - A single topic that stops silently is visible only to the nightly match.
  - The VPS steps remain in `docs/DEPLOY_CHECKLIST.md` (OPS-01).
