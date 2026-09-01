# Adversarial Review — ARCHITECTURE-SPINE.md (Gatekeeper paradigm, 2026-07-24 revision)

**Reviewer stance:** independent adversary, no prior context on this conversation. Goal: find
concrete pairs of implementers (two developers, or a developer and an AI coding agent) who each
obey every AD to the letter yet still build incompatible systems — clashing wire shapes, two
owners of one entity, conflicting mutation paths, or Rule wording that supports two structural
readings.

**Target:** `_bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md`
(2026-07-24 revision, adding AD-9 "Ranking Engine sole computer/publisher" and AD-10 "`live_paper`
control-plane isolation").

**Method:** read the full spine, the `.memlog.md` decision trail, the PRD sections for the Ranking
Engine (FR-6/FR-16) and Bot Monitoring TUI (FR-17–FR-24), the trading-mode-isolation requirement
(FR-15), and the actual code for `dashboard.py`'s ranking logic and `live_paper/node.py`/`config.py`
— to ground every finding in what a real implementer would actually be starting from, not just the
prose in isolation.

---

## Part 1 — Prior findings (2026-07-15 round): verified closed

The previous adversarial pass (Findings 1–8, preserved in git history of this file) is fully
addressed in the current spine:

| # | Prior finding | Resolution verified in current spine |
|---|---|---|
| 1 | Empty top-of-book could pass the gate | AD-2 now names "non-empty top-of-book" explicitly, grounded at `collector.py:271` |
| 2 | Deferred "dashboard cleanup" conflated two different checks | Split into two separate Deferred entries; gap-render explicitly marked permanent/load-bearing |
| 3 | AD-1 has no structural enforcement inside `dydx_collector/` | Now explicitly disclosed in AD-1's own text ("no compiler/lint-level mechanism... holds by there being exactly one writer today") rather than silently assumed — honest, not fixed, but no longer misleading |
| 4 | AD-1 "same code path" satisfiable by queue-fanout | Rule tightened: "synchronously, before control returns to the ingestion loop — not fanned out to independent async consumers" |
| 5 | Gate-logic version skew | Named in Deferred as an accepted trade-off with a stated revisit trigger |
| 6 | AD-4 data-vs-logic boundary ambiguous (`classify_liquidity`) | Rule now gives an explicit test ("pure, side-effect-free... no I/O and no shared mutable state") and names `classify_liquidity` as the worked example |
| 7 | AD-5 scoped only to `dydx_collector` | Broadened to "any code in `troll/`" |
| 8 | Rejection audit trail not queryable | Named in Deferred as "Rejection-rate observability for research use" |

No new stress-test found a regression in this set. The rest of this review is new ground: AD-9 and
AD-10, added in this revision, are the least battle-tested ADs and are the focus below, per the
review brief.

---

## Finding 9 — `rankings:live` has no defined wire schema; "the ranked list plus the active mode" is prose, not a contract

**Scenario:** AD-9's Rule says ranking_engine "publishes the ranked list plus the active mode over
Redis channel `rankings:live`." That is the entirety of the schema specification in the whole spine.
Two implementers — say, the engineer building `ranking_engine`'s publisher and the engineer (or
agent) building `bot_tui`'s subscriber — each fully compliant with AD-9's letter, can land on
incompatible shapes, and the existing code that AD-9 itself says to relocate makes this
concrete rather than hypothetical: `dashboard.py:900-976` (the logic AD-9 names for relocation)
already contains **three different, mutually incompatible "ranked list" shapes**, none of them a
single obvious source of truth:

- `_watchlist_ids()` → a bare ordered `list[str]` of instrument IDs (rank = position in list)
- `_current_ranks()` → a `dict[instrument_id, int]` of 1-indexed ranks
- `_rankings_json()` → a list of `{"instrument_id": ..., "cells": {col: {"text", "color", "raw"}, ...}}`
  dicts — a **presentation-formatted** payload (HTML-ready `text`/`color` strings for the
  dashboard's own JS table), not domain data at all

An implementer relocating this code literally could just as easily re-publish `_rankings_json()`'s
presentation-cell format on `rankings:live` (it's the one already called "the human-facing table")
as they could design a clean `{"mode": "volume", "ranks": [{"instrument_id": "BTC-USD-PERP.DYDX",
"rank": 1, "score": 4821003.5}, ...]}` payload. `bot_tui` is a completely different renderer (urwid,
not a browser) and has no use for `{"text": "1,234", "color": "#f85149"}` cells — a subscriber
written against the clean-domain-data assumption will fail to parse (or silently mis-render) a
publisher that shipped the presentation-cell format, and vice versa. Nothing in AD-9 rules either
shape out.

This is compounded by a second, narrower ambiguity: the spine's own "Module dependencies"
Consistency Convention lists `dydx_collector` as the one namespace whose *data types* may be
imported cross-namespace (e.g. `DydxMinuteBar`); it does **not** list `ranking_engine` as a
type-import source. So even the natural fix — a shared `RankingMode`/`RankedCoin` dataclass that
`ranking_engine` defines and `dashboard`/`bot_tui` import, mirroring the exact pattern already used
for `dydx_collector`'s shared types — isn't authorized by the letter of the convention table. One
implementer could add that import (consistent with the *spirit* of AD-4/AD-9); another could treat
`rankings:live` as opaque untyped JSON with no shared type at all, because the convention table
never says `ranking_engine` is allowed to export types the way `dydx_collector` is.

Also unspecified, and independently capable of producing disagreement: publish cadence (every 1s
tick regardless of change, vs. only on rank-order/mode change) and universe scope (all subscribed
coins vs. only the top-N "Watchlist," which is exactly the `_watchlist_ids()` vs. `_rankings_json()`
distinction dashboard's own code already draws — "unlike `_rankings_json` [...] stale/delisted
coins are excluded outright").

**AD(s) exposed:** AD-9 (no payload schema at all), AD-4/Consistency Conventions "Module
dependencies" row (doesn't authorize `ranking_engine` as a type-export source the way
`dydx_collector` is).

**Verdict:** Real hole, high severity — this is precisely the "two units one level down, each
obeying the letter, building incompatible things" failure the review brief asks for, and it is
grounded in code that already contains three incompatible candidate shapes. Recommend the spine (or
a companion data-contracts doc) pin: field names and types for the `rankings:live` payload
(explicitly: is "rank" 0- or 1-indexed; is the raw score included or just order; full universe or
Watchlist-filtered; instrument ID format), publish cadence/heartbeat expectation, and an explicit
statement that `ranking_engine` may export a shared type the same way `dydx_collector` does (naming
it in the Module dependencies convention table).

---

## Finding 10 — AD-9 promises mode-switching "from either the dashboard or bot_tui" but defines no channel or mechanism for it

**Scenario:** AD-9's Rule states: "switching mode from either the dashboard or `bot_tui` changes
what every reader of `rankings:live` sees." This requires some request path *into* `ranking_engine`
— yet the Consistency Conventions' "Redis channel conventions" row, which is the spine's own
enumeration of every channel in the system, lists exactly four channels: `snapshots:raw`,
`rankings:live`, `bots:status`, `bots:control`. All four are unidirectional single-producer
channels per the same row's rule ("one producer per channel, a channel's other named party only
ever subscribes"). `rankings:live`'s producer is `ranking_engine`; nothing publishes *to*
`ranking_engine`. There is no `ranking:control`-style channel, no HTTP endpoint on `ranking_engine`,
no shared Redis key — the mechanism AD-9's own sentence depends on does not exist anywhere in the
spine.

Two implementers each following AD-9 to the letter ("this must be possible") but with nothing else
to go on will each *invent* a mechanism, and there is no reason to expect the same one:

- Implementer A adds a new pub/sub channel, e.g. `ranking:control`, with `dashboard`/`bot_tui` as
  producers and `ranking_engine` as the (undocumented, AD-9-violating-by-the-letter-of-the-channel-
  table) subscriber.
- Implementer B has `dashboard`/`bot_tui` `SET` a plain Redis key (e.g. `ranking:mode:requested`)
  that `ranking_engine` polls or watches via keyspace notifications.
- Implementer C (an AI coding agent given only the spine, no PRD) might add an HTTP `POST` endpoint
  on `ranking_engine` mirroring `dashboard.py`'s existing `aiohttp` `/api/*` routes, since that's the
  only "control" precedent that already exists in the codebase.

All three are compatible with AD-9's prose; none is ruled out, and they are not interoperable with
each other, nor is the resulting design consistent with the channel table's own "one producer per
channel" rule (which would need a fifth entry, direction reversed).

**AD(s) exposed:** AD-9 (asserts a capability with no corresponding mechanism in the Redis channel
convention table, which is otherwise the spine's exhaustive enumeration of IPC surfaces).

**Verdict:** Real hole, high severity. Recommend adding the missing channel (or key) to the
Consistency Conventions table explicitly, naming its direction and producer(s)/consumer, consistent
with how `bots:control` already documents the `bot_tui → live_paper` direction for an analogous
"UI requests a state change in a backend service" pattern.

---

## Finding 11 — Global Ranking Mode has no conflict-resolution rule for concurrent switch requests

**Scenario:** Even once Finding 10's missing mechanism is added, AD-9 states Ranking Mode is
"GLOBAL shared state owned by `ranking_engine`," but gives no sequencing/conflict rule. Two
concrete operators (or the same operator with both surfaces open, which the PRD explicitly
anticipates — UJ-4 has the builder using the TUI while the dashboard graphs stay open in a browser
tab) each send a switch request within the same short window: dashboard operator clicks "volatility
mode," `bot_tui` operator simultaneously presses the mode-toggle key for "volume mode." AD-9 doesn't
say:

- whether requests are ordered by arrival at `ranking_engine` (last-write-wins) or something else,
- whether a `rankings:live` payload carries any version/sequence number a reader could use to detect
  "the mode I just requested was immediately overridden by someone else" versus "my request
  succeeded and is authoritative,"
- what a client should show in the gap between issuing a switch request and receiving the next
  `rankings:live` publish confirming it (an "optimistic UI showing the requested-but-not-yet-
  confirmed mode" vs. "wait for confirmation" choice that two implementers could resolve
  oppositely, producing visibly different UI behavior under contention even though both are
  spine-compliant).

This directly targets SM-4 ("the TUI's coin list and the dashboard's coin list never diverge in
ranking order... under either Ranking Mode") — SM-4 is only actually satisfied if both surfaces
converge to the *same* final mode after a race, which nothing in AD-9 guarantees is even
deterministic, let alone specifies how.

**AD(s) exposed:** AD-9 (global mutable state with an unspecified last-writer/ordering rule and no
version/sequence field for readers to detect override).

**Verdict:** Real hole, medium severity (low probability of frequent contention given this is a
single-operator tool per UJ-4, but the failure mode when it does occur — a TUI silently reverting a
mode switch a human just made, with no indication why — is confusing enough to be worth a one-line
rule: e.g. last-publish-wins is what `rankings:live`'s next message reflects, full stop, and clients
render only what they last received, never an optimistic local guess).

---

## Finding 12 — `bots:status`/`bots:control` have no wire schema, and no stated single-bot-vs-multi-bot addressing scheme

**Scenario:** AD-10's entire schema specification is: `live_paper` "exposes bot PnL/status via Redis
publish on `bots:status`" and "consumes start/stop commands via Redis subscribe on `bots:control`."
No field names, no types, no addressing scheme. This matters because the PRD and the current code
disagree about how many bots exist:

- FR-18 (PRD): "The TUI shows **a list of running bots** with PnL and other per-bot metrics" — plural,
  implying multiple concurrently addressable bot instances.
- `live_paper/config.py` (current code): a single `PaperConfig`/`RealMoneyConfig` pair with one
  `instrument_id`, one `trade_size` — the actual system today runs exactly one bot.

Two implementers of the `bots:status`/`bots:control` contract, each reading AD-10 to the letter,
can reasonably diverge:

- Implementer A (builds toward FR-18's plural framing): every message carries a `bot_id` field;
  `bots:status` publishes a list of per-bot status dicts; `bots:control` commands are addressed
  (`{"bot_id": "...", "action": "start"}`).
- Implementer B (builds toward the current single-bot code): `bots:status` publishes one flat
  status object with no `bot_id` at all; `bots:control` commands are unaddressed
  (`{"action": "start"}` implicitly means "the one bot").

If `live_paper` ships B and `bot_tui` is written against A's assumption (because the PRD it read
says "list of running bots"), `bot_tui` either crashes parsing a flat object where it expected a
list, or renders a single anonymous row with no way to reconcile it against a future second bot.
Neither implementer is wrong under AD-10's text — the AD never resolves whether "bots" (plural, in
the channel names themselves) means "the system is multi-bot-addressable now" or "plural naming for
a future-proofed single-bot channel."

**AD(s) exposed:** AD-10 (no payload schema, no addressing-scheme decision, despite the channel
names already being plural and the PRD already describing plural behavior against code that is
currently singular).

**Verdict:** Real hole, medium-high severity. Recommend the spine state explicitly whether
`bots:status`/`bots:control` messages are bot-addressed (with a `bot_id`/equivalent key) from day
one, even while exactly one bot exists — cheaper to decide once now than to have `bot_tui` and
`live_paper` converge on incompatible shapes and require a breaking channel-schema change the day a
second bot is added.

---

## Finding 13 — AD-10 says nothing about the paper/real-money safety gate a "start" command interacts with; this is the review's highest-severity finding

**Scenario:** FR-23 (PRD) requires: "The user can start and stop a bot directly from the TUI, for
both paper-mode (FR-14) and live-mode execution, **gated by the same isolation/config-gate as
FR-15**." FR-15 requires real-money execution be "not reachable by any default or accidental config
state." The actual, already-built `live_paper/config.py` implements this as a deliberately
*un-message-driven*, launch-time gate: two independent signals — (1) which config file the process
loads at startup (`config.toml` by default; a different file only via the `LIVE_PAPER_REAL_MONEY_CONFIG`
env var, which has no default value), and (2) that file's own loader rejecting an unexpected `mode`
key outright — must *both* agree before real money is reachable, specifically so "a single
toggleable field would let a stray `mode = "real_money"` line... silently promote to real money"
(the module's own docstring, `live_paper/config.py:16-24`) cannot happen.

AD-10 never mentions this gate. Its Rule text is just "start/stop commands... as its ONLY external
interface." Two implementers of the `bots:control` consumer inside `live_paper`, each fully AD-10-
compliant, can disagree about something safety-critical:

- Implementer A (matches the existing config.py design's spirit): `bots:control`'s "start"/"stop"
  commands carry **no** mode-selecting field at all; paper-vs-real-money is fixed entirely by which
  config file the process loaded at launch, completely outside Redis's reach — a `bots:control`
  message can never promote a bot to real money, full stop.
- Implementer B (reads FR-23 literally — "for both paper-mode and live-mode execution... from the
  TUI"): adds a `mode`/`real_money: bool` field to the start command so the TUI can request live-mode
  execution at runtime, since FR-23 explicitly frames the TUI as the surface for *both* modes.

Implementer B's reading is not an unreasonable misreading of FR-23 in isolation — but it reintroduces
exactly the "single toggleable field" footgun that Story 3.1's config design went to deliberate
lengths to preclude (two independent, launch-time, file-based signals, not one runtime parameter).
If `bots:control` accepts any mode-selecting key, then a `bot_tui` bug, a fat-fingered keybinding, or
a crafted Redis message from anything that can publish to that channel becomes a single-message path
to real-money trading — precisely the "not reachable by any default or accidental config state"
property FR-15 was written to guarantee, undone one layer up by a control-plane AD that never
acknowledges the gate it's adjacent to.

**AD(s) exposed:** AD-10 (silent on the FR-15/FR-23 interaction — the single highest-stakes
ambiguity in the whole spine, because the two structural readings differ by "real funds
structurally unreachable" vs. "real funds reachable via a Redis message").

**Verdict:** Real hole, **critical severity** — this is a capital-safety-relevant ambiguity, not a
cosmetic one. Recommend AD-10 add an explicit rule: `bots:control`'s start/stop commands are
schema-forbidden from carrying any paper/real-money mode selector; the paper-vs-real-money decision
remains exclusively the launch-time config-file/env-var mechanism in `live_paper/config.py`, and
`bots:control` can only start/stop *the mode the running process was already launched in*. This
should be stated in the architecture, not left to be inferred correctly by whoever implements the
`bots:control` consumer.

---

## Finding 14 — No source-of-truth/staleness story for `bots:status` across a `live_paper` crash or restart; the project already solved this exact problem once for market data and didn't apply the lesson here

**Scenario:** Redis pub/sub is fire-and-forget: a subscriber that wasn't connected at publish time
never receives that message, and there is no built-in "retained last message" semantic (unlike, say,
MQTT retained messages or a Redis key with a TTL). AD-10 describes `bots:status` purely as a
`publish`. Concretely:

- `live_paper` crashes (OOM, unhandled exception, host restart). It was mid-way through a position;
  its last `bots:status` publish said `"running"`. Nothing further is ever published on that channel
  until the process comes back.
- `bot_tui`, still running, has no way to distinguish "the bot is healthy and simply hasn't had a
  status-worthy event in a while" from "the process is dead" — there's no heartbeat cadence
  specified, no TTL'd key, no "I am alive" ping, unlike `snapshots:raw`'s data, which the project
  *already* built explicit staleness detection for (`_STALE_BOOK_NS` at the gate, `_CHART_GAP_
  THRESHOLD_MS` at the dashboard, `troll/CLAUDE.md`'s DATA-01/OBS-01 rules explicitly naming "zero
  updates... is a failure mode, not [an acceptable state]"). AD-10 doesn't extend that same
  discipline to the control plane it introduces.
- When `live_paper` restarts, does it republish current status immediately on start-up (so a live
  `bot_tui` recovers within one publish), or only on the next state *transition* (in which case a
  `bot_tui` that was already displaying "running" before the crash keeps displaying stale "running"
  indefinitely, silently, through the entire crash-and-restart cycle, until some unrelated future
  event happens to trigger a publish)? AD-10 doesn't say.
- Separately: does a restarted `live_paper` resume whatever bot(s) were running before the crash, or
  always come up with everything stopped (requiring manual restart via `bot_tui`)? Two implementers
  could reasonably pick either — "auto-resume for uptime" vs. "always cold, no unattended state
  changes across a crash" — and this is exactly the kind of state-ownership question AD-10 is
  supposed to close but doesn't touch.

Also worth naming: AD-10's Rule is phrased as "`bots:status`... as its ONLY external interface,"
which if read strictly as "publish/subscribe only, nothing else" would forbid the simplest fix (a
plain Redis key with a short TTL that `live_paper` refreshes periodically and `bot_tui` reads
directly for liveness) as a "second IPC mechanism" — even though that's arguably still "Redis," just
not pub/sub. Whether a TTL'd key counts as within-bounds or a forbidden second mechanism is itself
ambiguous under the current wording.

**AD(s) exposed:** AD-10 (no heartbeat/liveness/staleness convention; no restart-recovery-state
rule; "ONLY external interface" ambiguous about whether it means "pub/sub only" or "Redis only").

**Verdict:** Real hole, high severity — directly the scenario named in the review brief
("does `bot_tui`'s view of bot status go stale silently?" — yes, as currently specified, with no
mechanism to detect it). Recommend: (a) a heartbeat cadence on `bots:status` (publish on every state
tick, not only on transitions, mirroring `snapshots:raw`'s continuous-cadence design) plus a
client-side staleness threshold `bot_tui` applies (the same DATA-01/OBS-01 pattern already used for
market data, just pointed at a new channel), and (b) an explicit restart-recovery rule — does
`live_paper` persist and resume its running-bot set across a restart, or always cold-start stopped.

---

## Summary Table

| # | Finding | AD(s) | Severity | Status |
|---|---|---|---|---|
| 9 | `rankings:live` payload has no defined schema; dashboard's own existing ranking code already contains three incompatible candidate shapes (presentation cells vs. plain ID list vs. rank dict) | AD-9 | High | Real hole |
| 10 | AD-9 asserts mode-switching "from either surface" but no channel/mechanism for the switch request exists anywhere in the spine's channel table | AD-9 | High | Real hole |
| 11 | Global Ranking Mode has no conflict-resolution/ordering rule for concurrent switch requests from two surfaces; SM-4's "never diverge" guarantee isn't actually backed by a deterministic rule | AD-9 | Medium | Real hole |
| 12 | `bots:status`/`bots:control` have no wire schema and no stated single-bot-vs-addressed-multi-bot scheme, despite plural channel names, plural PRD framing (FR-18), and singular current code | AD-10 | Medium-High | Real hole |
| 13 | AD-10 is silent on the FR-15/FR-23 paper-vs-real-money safety gate; a plausible-but-wrong reading of "start/stop... for both paper-mode and live-mode" lets a Redis message carry a mode selector, undoing the deliberate two-independent-signals, launch-time-only gate already built in `live_paper/config.py` | AD-10 | **Critical** | Real hole |
| 14 | No heartbeat/staleness/restart-recovery story for `bots:status` — `bot_tui`'s view of a crashed `live_paper` goes stale silently, with no mechanism to detect it, unlike the market-data path which already solved this exact class of problem (DATA-01/OBS-01) | AD-10 | High | Real hole |

**Findings 1–8 (2026-07-15 round):** verified closed, see Part 1 — no regression found.

**Overall verdict:** AD-1 through AD-8 have hardened well across the previous review cycle and hold
up under a fresh adversarial pass. AD-9 and AD-10, as flagged going in, are the weak points of this
revision: both correctly establish *ownership* (single writer/gate, single control-plane owner) but
neither specifies the *wire contract* two independent implementers would need to agree on to
actually interoperate, and AD-10 additionally fails to acknowledge the one safety-critical
interaction (FR-15's real-money gate) it sits directly next to. None of these are reasons to
reject the paradigm — they're the natural next layer of tightening the same "Binds/Prevents/Rule"
discipline already applied to AD-1–AD-8 needs to reach, before `ranking_engine`, `bot_tui`, and the
control-plane half of `live_paper` are actually built.
