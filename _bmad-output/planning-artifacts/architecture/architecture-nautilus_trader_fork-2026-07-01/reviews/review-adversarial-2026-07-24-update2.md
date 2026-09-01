# Adversarial Review — ARCHITECTURE-SPINE.md, AD-10 "Durable trade/PnL history" (2026-07-24, second update pass)

**Reviewer stance:** independent adversary, no prior context on this conversation. Goal: construct
concrete pairs of implementers — two developers, or a developer and an AI coding agent — who each
obey every AD to the letter yet still build incompatible systems: clashing wire shapes, two owners
of one entity, conflicting mutation paths, or Rule wording that supports two structural readings.

**Target:** the section of AD-10 added in today's second update pass — "Durable trade/PnL history
(FR26)" and "Staleness (history)" — the new `bots:history:{bot_id}:{range}` Redis-key convention,
the matching Consistency Conventions table row, and the updated mermaid diagram / Structural Seed
entries for `live_paper`. Cross-checked against AD-9's `rankings:live` schema (the project's own
prior bar for wire-contract rigor), the PRD (FR-15, FR-18, FR-23, FR-24), and `epics.md`
(Stories 4.6–4.7, FR26, UX-DR6) to ground every finding in what a real implementer would actually
start from.

**Relationship to prior review:** `review-adversarial.md` (earlier today) already covers Findings
1–14 against AD-1–AD-9 and AD-10's pre-existing status/control-plane rules (schema, addressing,
the FR-15 bypass gate, heartbeat/staleness) — all verified resolved in the current spine and not
re-litigated here. This pass is new ground: only the "Durable trade/PnL history" text added in the
second 2026-07-24 update, which no prior review round has stress-tested. Findings below continue
the numbering from that file (15+).

---

## Finding 15 — `bot_id` uniqueness across a paper/live pair of the "same" bot is never guaranteed anywhere in the spine, PRD, or epics — and AD-10's own FR-15 bypass fix depends on it silently

**Scenario:** AD-10's Rule states the load-bearing safety property as: whether a "start" runs paper
or real-money "is determined solely by `live_paper`'s own local config gate (FR-15's existing
separate-config-file mechanism), entirely independent of and unreachable from the control channel."
This closes the *command*-payload bypass Finding 13 (prior review) identified. But it says nothing
about the *addressing* dimension: every one of `bots:status`, `bots:control`, and now
`bots:history:{bot_id}:*` is keyed purely by `bot_id`, with mode (paper/live) carried only as a
displayed field inside the status payload (`epics.md` UX-DR4: "bot_id, ... mode, ..."), not as part
of the key/channel identity itself.

Nothing in AD-10, FR-15, or FR-23 specifies who assigns `bot_id`, whether it must be derived
deterministically from the config file, or that it must differ between a paper config and a live
config of what a user considers "the same" bot (e.g. same strategy class, same instrument, only the
broker/config-gate differing per FR-15). Two implementers, each fully AD-10-compliant:

- Implementer A builds `live_paper`'s bot-id assignment as a value read literally from each config
  file (`bot_id = "btc-momentum"` in both `config.toml` and whatever file
  `LIVE_PAPER_REAL_MONEY_CONFIG` points at) — natural, since the strategy/instrument is genuinely
  the same and the two files are plausibly created by copying one to the other and only flipping
  the mode-gate fields FR-15 requires to differ.
- Implementer B (or the same implementer, six months later, spinning up a live variant of an
  existing paper bot) copies the working paper config file to bootstrap the live one, changes only
  the fields FR-15's loader actually rejects on mismatch, and never touches `bot_id` because
  nothing in the spine flags it as something that must be unique across the pair.

Either way, a paper bot and a live bot end up publishing to the identical `bots:status` payload
key, the identical `bots:control` address, and — new in this update — the identical
`bots:history:{bot_id}:day/week/month/all` Redis keys. The paper bot's fills and the live bot's
fills interleave into one blotter and one `pnl_series`; whichever process's refresh timer or
on-fill trigger fires last wins the key. `bot_tui`/dashboard would show a single row whose PnL
mixes real money and paper money, and a Bot-detail view (FR26, Story 4.7) whose trades blotter
cannot be attributed to either — with no error, crash, or schema violation anywhere, because
nothing in AD-10 demands `bot_id` be unique across mode.

This is the same class of failure Finding 13 closed for the command channel, reopened one
dimension over: FR-15/FR-23's isolation guarantee was fixed at the *payload* level ("no mode field
in `bots:control`") but never extended to the *addressing* level, and this update's new
`bots:history:*` keys make the blast radius larger — durable, persisted, and now covering trade/PnL
history, not just a transient status ping.

**AD(s) exposed:** AD-10 (no `bot_id` minting/uniqueness rule; the Addressing bullet says "every
message on either carries a `bot_id` field" but never says the field is guaranteed distinct across
concurrently-running bots, let alone across a paper/live pair of the same underlying strategy).

**Verdict:** Real hole, **critical severity** — this is a financial-state-integrity ambiguity
directly analogous to the review's prior highest-severity finding, and the new durable
`bots:history:*` keys make it durable/persistent rather than transient. Recommend AD-10 add an
explicit rule: `bot_id` must be unique across every concurrently-running `live_paper` process
regardless of paper/live mode, and the mechanism that guarantees this (e.g. derived from the config
file's absolute path or a mandatory required field the FR-15 loader itself validates as
non-duplicate against other known bots) should be named, not left to convention.

---

## Finding 16 — `pnl_series[].pnl` and `trades[].realized_pnl` semantics (cumulative running total vs. per-fill/per-period delta) are unspecified, and the one acceptance criterion that depends on them ("the numbers always match" across dashboard and bot_tui) has no shared code path to enforce it

**Scenario:** AD-10's schema is: `trades: [{ts, side, price, qty, realized_pnl}]`, `pnl_series:
[{period_start, pnl}]`. Nothing states whether `realized_pnl` on a given trade is that fill's own
realized P&L, or the running cumulative realized P&L of the bot as of that fill; likewise nothing
states whether `pnl_series[].pnl` is the bot's cumulative total P&L at `period_start`, or the
period's own incremental delta P&L. Both readings are internally consistent with the JSON shape as
written — the field name `pnl` alone does not disambiguate "total so far" from "P&L this bucket."

This matters because of who builds what: `live_paper` (the sole writer, per AD-10) computes and
serializes these fields exactly once, so the ambiguity does not create a writer-vs-writer clash
here. It does create a **reader-vs-reader** clash, because AD-4/AD-8 keep `bot_tui` and
`ml_signals.dashboard` as two independently-built, non-sharing consumers (cross-namespace imports
are data-types-and-pure-utilities only — there is no shared "history renderer" module named
anywhere in the Structural Seed). Two implementers, each a faithful `GET` client of the same key:

- The dashboard implementer renders `pnl_series` as a running equity curve — plots `pnl` directly
  against `period_start` as-is, assuming cumulative.
- The `bot_tui` implementer (building the sparkline named in Story 4.6's acceptance criteria)
  assumes `pnl` is the period's own delta and renders a step/bar chart of per-bucket P&L, or worse,
  cumulatively *sums* the already-cumulative values it received — silently doubling/curving the
  displayed trend if `live_paper` in fact emitted cumulative totals.

Either misreading produces a chart that is not merely differently-styled but structurally wrong —
monotonically increasing when it should show drawdowns, or vice versa — for the exact feature whose
own acceptance criterion (`epics.md` Story 4.7: "both surfaces reading the same underlying Cache
history via Story 4.6's read surface, so the numbers always match") is that dashboard and bot_tui
agree. AD-10's schema, taken literally, cannot deliver that guarantee: it specifies field names and
container shape, not the arithmetic meaning of the numeric fields inside them. The same ambiguity
applies identically to `trades[].realized_pnl` for the blotter view (Story 4.7 region 2).

**AD(s) exposed:** AD-10 (schema names fields but not their arithmetic semantics — contrast AD-9,
which pins `rankings:live`'s `ranks[]` entries to concretely named, unambiguous fields like
`volume24h`/`volatility_score` that need no further interpretation to render correctly).

**Verdict:** Real hole, **high severity** — directly threatens the one cross-surface consistency
guarantee this feature is built to provide. Recommend AD-10 state explicitly: `pnl_series[].pnl` is
[cumulative bot P&L as of `period_start` | this period's incremental P&L] (pick one), and
`trades[].realized_pnl` is the realized P&L of that specific fill only, never a running total —
matching Nautilus's own per-fill `realized_pnl` semantics on `PositionEvent`, which is the more
natural choice given `live_paper` derives these fields from `cache.orders_closed()`/
`cache.positions_closed()` in the first place.

---

## Finding 17 — `trades[].ts` / `pnl_series[].period_start` have no defined unit or format, unlike AD-9's `rankings:live` which explicitly pins its timestamp to "ns timestamp"

**Scenario:** AD-9's Rule text is explicit: `rankings:live` carries `updated_at` (ns timestamp)".
AD-10's own pre-existing `bots:status` inherits that same discipline by cross-reference ("mirroring
the project's existing never-show-stale-as-live discipline"). But the new history blob's *inner*
timestamp fields — `trades[].ts` and `pnl_series[].period_start` — are given no unit or format at
all; only the blob's own top-level `updated_at` is covered by the "identical staleness discipline"
sentence, which addresses freshness, not the format of timestamps *inside* the payload.

Two implementers of the two independent readers:

- The dashboard implementer, already handling nanosecond `ts_event`/`ts_init` integers everywhere
  else in `ml_signals` (Nautilus's pervasive convention, and the same convention every other
  timestamp in this spine uses), assumes `ts`/`period_start` are ns-since-epoch integers and
  formats them accordingly for the historical chart's x-axis.
- The `bot_tui` implementer, building a JSON blob meant for **direct, human-facing display** in a
  terminal blotter (not a Nautilus-internal event pipeline), reasonably assumes `live_paper`
  pre-formatted these fields for display convenience — an ISO-8601 string, or a millisecond
  epoch matching what a `GET`-only, no-further-processing client would expect from a "just render
  this JSON" contract, since AD-10 explicitly rejects a request/response pattern precisely because
  this is meant to be simple.

If `live_paper` in fact emits raw Nautilus ns-integers (the more likely real implementation, since
that is what `cache.orders_closed()` naturally yields) and `bot_tui` guesses ms-epoch or an
ISO-8601 parse, every trade's displayed time is off by a factor of 10^6 or fails to parse at all —
while dashboard, built by whoever correctly guessed ns, renders identical data correctly. Both
implementers followed AD-10's text to the letter; the text simply never says.

**AD(s) exposed:** AD-10 (the new history schema is markedly less rigorous than its own sibling
`rankings:live` schema on exactly the property — timestamp unit — that AD-9 took the trouble to pin
down explicitly).

**Verdict:** Real hole, **high severity** — a silent, structural formatting mismatch between the
two reader surfaces that would only surface as "the times look wrong" in one of the two UIs, easy
to miss in review since both surfaces would otherwise appear to work. Recommend AD-10 state
`ts`/`period_start` are ns-since-epoch integers, consistent with every other timestamp field this
spine defines (`updated_at` everywhere, Nautilus's own `ts_event` convention), leaving
"format for display" as strictly a reader-side responsibility — matching the boundary the spine
already draws elsewhere (readers render, writers never pre-format for one specific UI).

---

## Finding 18 — "day"/"week"/"month" bucket boundaries (rolling window vs. calendar-aligned, and in what timezone) are unspecified, and the UX-facing preset toggle's label implies a semantic AD-10 never commits to

**Scenario:** AD-10 names exactly four keys — `:day`, `:week`, `:month`, `:all` — with no
definition of what "day" bounds mean. `epics.md`'s Story 4.7 acceptance criterion says: "the
time-range preset cycles day → week → month → all → day…, never free-form date scrubbing, and the
sparkline redraws for the new window." This is a UX-facing label ("Day") that implies a
user-legible, probably calendar-aligned meaning (e.g. "today, since midnight") — but AD-10 never
commits to that reading over the equally-plausible "rolling last-24-hours" reading, nor does it
name a timezone (server UTC vs. the operator's local zone) for whichever is chosen.

Because `live_paper` is the sole writer, this is not strictly a two-writer clash — but it is still
a hole with real, testable consequences, because the choice materially changes what a single, fixed
implementation shows: at 00:05 UTC, a calendar-aligned `:day` bucket has 5 minutes of history and
looks "broken" (nearly empty chart, single dot) to anyone who doesn't know the semantics, while a
rolling-24h `:day` bucket always shows a full day's worth regardless of wall-clock time. Nothing in
AD-10, the PRD, or `epics.md`'s Story 4.7 resolves which behavior is correct, so the UX
acceptance criterion ("the sparkline redraws for the new window") is satisfiable by either
implementation while producing visibly different, mutually-surprising behavior — and there is no
way for a QA pass against Story 4.7's text alone to tell whether an implementation is "wrong."

**AD(s) exposed:** AD-10 (bucket semantics for `:day`/`:week`/`:month` are entirely unstated; `:all`
is the only unambiguous bucket of the four).

**Verdict:** Real hole, **medium severity** — a single-writer ambiguity rather than a two-owner
clash, but still an acceptance-criteria gap the spine should close before Story 4.7 is built, since
"correct" behavior here cannot currently be derived from the architecture text at all. Recommend
AD-10 state explicitly which semantic applies (rolling window is the simpler, timezone-agnostic
choice and matches the project's existing "rolling-window-in-memory" convention named elsewhere in
Consistency Conventions) and, if calendar-aligned, which timezone anchors it.

---

## Finding 19 — `:month`/`:all` `trades` arrays have no bound, pagination, or summarization rule — in direct tension with the spine's own "no unbounded accumulation" convention

**Scenario:** AD-10's Rule gives all four ranges the identical shape: `{bot_id, range, updated_at,
trades: [...], pnl_series: [...]}`. Nothing caps, paginates, or summarizes `trades` for the
longer-lived buckets. For a bot that has been running for months, `:all`'s (and eventually
`:month`'s) `trades` array grows without bound — and per AD-10's own Rule, this same
ever-growing array is fully re-serialized into one Redis value on every refresh: "refreshed on a
timer (~30–60s) and **on each fill**." That means every single fill, for the life of a long-running
bot, triggers a full rewrite of the entire historical trade list into one Redis key.

This is the exact shape of problem the spine already names a rule against elsewhere: the
Consistency Conventions "Memory" row bans `catalog.trade_ticks()` with no time bounds and requires
"non-configured coins are rolling-window-in-memory only, no unbounded accumulation." AD-10's
history blob is not covered by that rule (it targets the collector's catalog reads, not
`live_paper`'s Redis values) but is a structurally identical failure mode one layer up: an
unbounded, wholesale-rewritten Redis value that grows forever and gets fully re-transmitted/re-
serialized on every fill.

Two implementers, each compliant with AD-10's literal text:

- Implementer A takes the schema at face value: every range's `trades` is the complete, unfiltered
  fill history for that range, including `:all` — accepting the unbounded growth and the
  per-fill full-rewrite cost as the trade-off, since nothing says otherwise.
- Implementer B, aware of the project's existing memory-boundedness discipline (and of Redis's own
  practical guidance against very large single values), caps or omits the full `trades` list for
  `:month`/`:all` — e.g. `:all`'s `trades` field contains only the most recent N fills or is empty,
  with `pnl_series` carrying the long-range trend instead — reasoning that the blotter view (Story
  4.7 region 2) is inherently a "recent activity" view and the full-fidelity fill-by-fill breakdown
  only matters for `:day`/`:week`.

Both implementations satisfy AD-10's stated JSON shape (the `trades` key exists in both, per-item
field names match). But Implementer A's bot silently becomes the exact kind of unbounded-growth
hazard the project has already been burned by once (per the spine's own stated motivation for this
whole sub-project: "a documented unbounded-queue-growth... bug... that previously OOM-crashed an
earlier recorder"), and a dashboard/`bot_tui` implementer who assumes (reasonably, from the schema)
that `:all`'s `trades` is the complete fill history would build a blotter view that silently starts
choking or truncating client-side once that array gets large enough — a failure mode invisible in
early testing and only surfacing months into a bot's runtime.

**AD(s) exposed:** AD-10 (no cap/pagination/summarization rule for `:month`/`:all`'s `trades`
field); indirectly, the Consistency Conventions "Memory" row (states a principle this new key
convention doesn't visibly follow, without being written broadly enough to cover it).

**Verdict:** Real hole, **medium-high severity** given the project's stated sensitivity to exactly
this failure class. Recommend AD-10 either (a) explicitly cap `trades` for `:month`/`:all` to a
fixed most-recent-N window (with `pnl_series` remaining the authoritative long-range signal), or
(b) state that `:all`'s full-fidelity trade list is intentionally accepted as unbounded with a
named revisit trigger (mirroring how the spine's Deferred section already handles other accepted
trade-offs, e.g. "Buffer durability") — either is fine, but silence is not, given this project's own
history with exactly this failure mode.

---

## Finding 20 — No atomicity guarantee across a bot's four sibling keys; `:day`/`:week`/`:month`/`:all` can be independently and visibly inconsistent with each other for the same bot at the same instant

**Scenario:** AD-10 describes the refresh as "`live_paper` computes and refreshes four well-known
Redis keys per bot... refreshed on a timer (~30–60s) and on each fill" — but says nothing about
whether all four keys for one bot are updated together, in one atomic operation (e.g. a Redis
`MULTI`/pipeline), or independently, one `SET` per key, in whatever order the refresh loop happens
to iterate them. Redis guarantees atomicity per single key, not across a set of keys touched by
separate commands.

Two implementers:

- Implementer A refreshes all four keys inside one `MULTI`/pipeline per bot per trigger, so from
  any reader's perspective the four blobs for one bot are always mutually consistent as of one
  instant.
- Implementer B (the more naturally-written version of "compute range X, `SET` its key; compute
  range Y, `SET` its key...", especially since day/week/month/all likely reuse overlapping,
  incrementally-computed windows rather than being independent queries) loops over the four ranges
  and issues four separate `SET`s. Between the first and the last, `bot_tui`/dashboard can `GET`
  a snapshot where, say, `:day` already reflects the newest fill (bumped by the on-fill trigger)
  while `:week` — which should be a strict superset of `:day`'s data for the overlapping period —
  still reflects the pre-fill state, so the two views momentarily disagree in a way that is
  internally contradictory if a user flips the toggle mid-refresh (a bot's `:week` PnL appearing
  lower than its own `:day` PnL for an overlapping period).

Both implementers comply with AD-10's literal text ("refreshed on a timer and on each fill" says
nothing about cross-key atomicity). The inconsistency window is narrow and this is a single-user
tool, so the practical severity is low — but it is a genuine, unremarked-upon hole exactly of the
kind this review is tasked to surface, and it costs one sentence in AD-10 to close.

**AD(s) exposed:** AD-10 (no cross-key atomicity statement for the four sibling `bots:history:*`
keys of one bot).

**Verdict:** Real hole, **low-medium severity**. Recommend a one-line addition: the four keys for
one bot are refreshed together (single pipeline/transaction) on any given trigger, so a reader never
observes a partially-updated set for the same bot.

---

## Finding 21 — AD-10's new history schema is markedly less rigorous than its own sibling, AD-9's `rankings:live` schema, on every axis that matters for two independent implementers to interoperate

**Scenario (synthesis, not a new standalone scenario):** Findings 16–19 above share a common root
cause worth naming explicitly, since the prompt for this review calls out the comparison directly.
AD-9's `rankings:live` schema is fully closed on every question an implementer would need answered
to build a compatible reader: field names, types, units (`updated_at` is explicitly "ns
timestamp"), the invariant that both score fields are always present regardless of mode (so readers
never need mode-specific branches), and an explicit staleness/heartbeat rule. There is essentially
no ambiguity left for two independent implementers of `rankings:live` consumers to diverge on.

AD-10's new history schema, added in the same spine under the same "durable trade/PnL history"
banner and explicitly modeled on the same GET-only, no-request/response philosophy, gives field
names and a container shape but leaves open: timestamp unit/format (Finding 17), the arithmetic
meaning of the one numeric field the whole feature is built around, `pnl` (Finding 16), bucket
boundary semantics (Finding 18), and array-growth bounds (Finding 19) — none of which AD-9 left
open for its own, structurally simpler payload. The new schema reads as though it inherits AD-9's
rigor by proximity and shared prose style ("a JSON message with...", "each a JSON blob {...}"),
without actually matching it field-for-field.

This matters because `rankings:live` is a pure, ephemeral, always-fully-recomputed live feed (get
it wrong and the next heartbeat self-corrects), while `bots:history:*` is described as *durable*
history built from Nautilus's own persistent `Cache` — the one payload in this spine's entire
Redis-channel table where a semantic mismatch between two readers produces a durably wrong picture
of real (or paper) trading performance, not just a transient display glitch. It is the schema that
can least afford to be the less-rigorous of the two.

**AD(s) exposed:** AD-10, by comparison with AD-9.

**Verdict:** Not a standalone bug — a pattern-level observation tying Findings 16–19 together.
Recommend AD-10's history sub-section be brought to the same field-by-field rigor as AD-9's
`rankings:live` bullet before implementation starts, given the schema's persistence and the
project's explicit acceptance criterion that the two reading surfaces must agree ("the numbers
always match").

---

## Finding 22 (supporting, lower severity) — Two loose ends adjacent to this update, worth a one-line mention each

- **FR-24 vs. `bot_tui` reading `bots:history:*`.** PRD FR-24 states plainly: "The TUI shows only
  current/latest state; it provides no historical replay or scrollback... Historical/graph analysis
  remains the web dashboard's responsibility." AD-10's Rule, as amended today, makes `bot_tui` a
  `GET` client of `bots:history:{bot_id}:day/week/month/all` — precisely a historical-replay
  surface, by any plain reading of FR-24's own text. `implementation-readiness-report-2026-07-24.md`
  already flags this (its issue #1) as a PRD-text scoping fix ("no historical replay... for market
  data" was the actual intent, per the finalized UX pass) — a documentation-only gap, not a
  structural one, and not re-litigated here in depth. Worth noting only because the architecture
  spine itself, in the very AD that creates the tension, never once acknowledges it — a reader of
  AD-10 alone, without also having read the implementation-readiness report, would not know this
  contradiction is already tracked and considered resolved-in-intent.

- **`FR26` identifier reuse.** The PRD's own `.memlog.md` records that `FR-26` was previously
  assigned (auto-surfacing "good-to-trade" coins by color) and explicitly **retired** in a later
  scope-change decision ("removed FR-26... coin ranking list is now plain/sorted only"). AD-10 and
  `epics.md` now cite `FR26` again, for a completely unrelated feature (bot-detail trade/PnL
  history), because the PRD itself was never amended to formally add it (already flagged by
  `implementation-readiness-report-2026-07-24.md` as a required PRD amendment). The specific
  adversarial risk beyond what that report already names: anyone later grepping this project's
  history for "FR-26" — to understand what it means, or to check whether it's still active — will
  find a retired, unrelated requirement of the same number in the PRD's memlog before finding the
  live one, since the live one doesn't exist in `prd.md` yet at all. Recommend the eventual PRD
  fold-in use a fresh, never-before-assigned FR number rather than reusing 26, precisely because 26
  already has documented history attached to a different feature.

---

## Summary Table

| # | Finding | AD(s) | Severity | Status |
|---|---|---|---|---|
| 15 | `bot_id` uniqueness across a paper/live pair of the "same" bot is never guaranteed — a copied config file can collide `bots:status`/`bots:control`/the new durable `bots:history:*` keys between real-money and paper trading, reopening the FR-15 isolation guarantee via the addressing dimension instead of the payload dimension the prior review's fix closed | AD-10 | **Critical** | Real hole |
| 16 | `pnl_series[].pnl` / `trades[].realized_pnl` semantics (cumulative vs. per-period/per-fill delta) unspecified — dashboard and bot_tui, built independently per AD-4, can each pick a different reading and render structurally wrong, mutually disagreeing charts for the one feature whose acceptance criterion requires them to match | AD-10 | High | Real hole |
| 17 | `trades[].ts`/`pnl_series[].period_start` timestamp unit/format unspecified, unlike AD-9's explicit "ns timestamp" for `rankings:live`'s `updated_at` — a plausible ns-vs-ms/ISO8601 mismatch between the two reader implementations silently mis-renders one surface's timestamps | AD-10 | High | Real hole |
| 18 | `:day`/`:week`/`:month` bucket boundary semantics (rolling window vs. calendar-aligned, timezone) unspecified — the UX preset toggle's label implies a semantic AD-10 never commits to, so "correct" behavior for Story 4.7 cannot be derived from the architecture text | AD-10 | Medium | Real hole |
| 19 | No bound/pagination/summarization rule for `:month`/`:all`'s `trades` array — a literal reading produces an unbounded array fully re-serialized into one Redis value on every fill, the same failure class this whole sub-project exists to avoid | AD-10 | Medium-High | Real hole |
| 20 | No cross-key atomicity guarantee across a bot's four sibling `bots:history:*` keys — independently-refreshed keys can be momentarily, visibly inconsistent with each other for the same bot | AD-10 | Low-Medium | Real hole |
| 21 | Synthesis: AD-10's new history schema leans on AD-9's `rankings:live` rigor by prose-style association without matching it field-for-field, on exactly the payload with the least room for a durable, silent mismatch | AD-10 (vs. AD-9) | — (pattern) | Observation |
| 22 | Two supporting loose ends: FR-24 (no-history-in-TUI) textually contradicts `bot_tui` reading `bots:history:*` (already tracked elsewhere, not re-litigated); `FR26` reuses a PRD identifier previously assigned and explicitly retired for an unrelated feature | AD-10 / PRD | Low | Documentation gap |

**Overall verdict:** AD-10's pre-existing status/control-plane half (schema, addressing, the FR-15
bypass gate, heartbeat/staleness — Findings 12–14 from the prior review) has held up well. The
"Durable trade/PnL history" text added in today's second update pass has not yet had the same
tightening pass applied: it establishes *ownership* correctly (single writer, `Cache`-backed, no
bespoke store, no internals leak) but leaves open exactly the class of ambiguity — wire-format
units, numeric-field semantics, bucket boundaries, growth bounds, cross-key atomicity, and (the
most severe) addressing uniqueness across the very paper/live boundary this whole AD exists to
protect — that AD-9 and AD-10's earlier sections were already tightened to close. None of this is a
reason to reject the durable-history design; it is the same next layer of rigor the rest of the
spine has already been through, arriving one AD later than the rest.
