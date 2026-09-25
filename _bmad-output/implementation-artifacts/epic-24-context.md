# Epic 24 Context: Derived data and read models on one fold

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

This is the second epic of the DDD migration of `platform/`. With the shared kernel, the
observability subdomain and the boundary/image enforcement tests already in place, this epic
moves every *derived* reader into its own bounded context, in a fixed order: candles first, so
the single seconds→bars fold exists before anything consumes it; then the read models both UIs
share; then alerting as an observer of the forming bar; then research as a pure consumer. When
it lands, the chart's forming candle, the stored candle, the ranking sparkline and an alert's
bar close are all produced by one fold; the web UI and the TUI compute nothing of their own and
therefore cannot disagree; the reader-side crossed-book re-validation that second-guessed the
capture gate is gone; and research can no longer leak a computation back into the live path.

## Stories

- Story 24.1: `candles/` context behind capture's `SecondSink` port
- Story 24.2: `views/` read models for both UIs, and the reader-side re-validation removed
- Story 24.3: `alerting/` context as an observer of the forming bar
- Story 24.4: `research/` as a pure consumer, with its broken tests repaired

## Requirements & Constraints

- **One context moves per story, each deployable alone.** Nothing that crosses a process
  boundary may change: Parquet schemas and catalog directory names, every Redis channel
  payload, the SQLite and TOML store schemas and key sets, compose service names, env vars and
  the host bind-mount paths. A move is a relocation plus wiring, never a contract change.
- **Every moved import path leaves a pure re-export shim** — re-export plus a deprecation
  warning plus a `REMOVE_AFTER` story key, defining nothing itself (a copied class body would
  register a second Arrow class). A shim dies no later than two stories after it appears, and
  its removal deadline is enforced against sprint status.
- **Same-commit consistency.** Each move updates, in the same commit, the project working-rules
  citations, the architecture doc, the data dictionary, all three dockerfiles' `COPY` sets, the
  compose `command:` lines and both Makefile test lists. Every entrypoint's import closure must
  remain a subset of its image's copied packages.
- **Strike what you resolve.** A parent-spine deferred item this epic actually fixes is struck
  with an amendment by the story that lands it — specifically the writer→reader import item
  (finished by the candles move) and the reader-side crossed-book skip item (the views move).
- **Broken tests are repaired, never deleted or loosened**, and the root cause of each break is
  recorded. A deprecation or future warning surfaced by a move is a failure, not noise.

## Technical Decisions

- **Three-layer shape per context.** `domain/` is pure (stdlib, numpy, kernel and Nautilus
  model/core types only — no I/O, asyncio, SQLite, Parquet or the Nautilus runtime); `application/`
  declares ports as `typing.Protocol`, holds services and asyncio process managers;
  `infrastructure/` implements ports and is imported only by a composition root. Contexts are
  top-level packages with `platform/` on `sys.path` — never imported with a `platform.` prefix.
- **Legal import edges only.** `candles` and `views` may import `kernel` and `observability`;
  `views` may additionally call the candles and ranking *query services*; `data_api` and
  `bot_tui` may import `views`, `kernel`, `observability` and alerting's application service,
  and nothing else. Importing another context's private name fails the boundary test from day
  one, judged by target context even while a module still sits at its old path.
- **Exactly two folds exist in the platform** when this epic is done: trades→second, and
  seconds→bars (closed or forming, any bar width). Every other aggregation helper is retired.
  The forming bar is a candles query service over second rows, and an equivalence test must
  prove it reproduces the stored closed bars across multiple bar widths.
- **Candles are downstream of capture behind a port.** Capture declares a second-sink port;
  candles implements it; the venue entrypoint constructs and injects the adapter. The sink
  receives the *flushed* batch — only rows whose catalog write succeeded — so the store can
  never run ahead of the archive. A candle series applies each second exactly once via a
  per-instrument watermark and records observed/partial coverage, so every bar stays
  rebuildable from raw seconds. The candle store is the sole read-write opener of its SQLite
  file; the retention prune becomes a candles process manager; archive-side tooling receives a
  verified-days port rather than a database connection.
- **Read models are the only thing interfaces see.** Every value both UIs show comes from one
  function over one input; routes and TUI state modules format and transport only. Views owns
  the two UI preference files (full-rewrite TOML, one loader each, frozen key sets) and the
  catalog series reads, which go through the kernel's read helpers. A raw snapshot payload is
  parsed only through the kernel snapshot type's constructor — hand-indexing it anywhere is a
  boundary-test failure.
- **The reader never re-validates the gate.** Empty-top and crossed-book skips on the read path
  are deleted, with a test proving a crossed row the gate accepted is returned unchanged. The
  gap-marker insertion survives as a *rendering* rule inside the view layer.
- **Alerting observes, it does not reach.** Alerting implements a bar-observer port declared by
  views and is attached to the live-candle bus only in the API's composition root, so no import
  edge from alerting into views exists beyond the port type. An alert names a channel, never a
  transport: delivery goes through the one outbound notifier, whose transport is chosen by env.
- **Research owns no computation.** It reads the catalog (time-bounded, via kernel helpers or
  streaming backtest configs), ranking history and the rankings HTTP API only; rolling metrics
  such as pct-change and volatility come from ranking, never recomputed. Strategies stay
  referenceable by importable string path, with paths updated in docs.
- **Naming and state conventions.** Ports are protocol nouns naming a capability; adapters are
  named by technology; one aggregate per domain module; tests live under each context. No
  module-level mutable runtime state. Every aggregate and port docstring names the invariant or
  the decoupling it exists for; no DI container, service locator or event-bus library.

## UX & Interaction Patterns

- Both surfaces must show identical numbers by construction, because they call the same view
  functions over the same inputs.
- The chart renders what the gate approved: crossed or thin-book seconds are no longer hidden
  by the reader; only the explicit gap-marker rendering rule affects what is drawn.
- Alerting's user-visible surface is unchanged — the event stream, the alerts API contract and
  the frontend dialog stay byte-for-byte identical, and the existing firing-frequency semantics
  (per bar close, per bar, once only) must still hold.
- The frontend docs page and the architecture doc gain the new context names.

## Cross-Story Dependencies

- Order inside the epic is fixed: 24.1 → 24.2 → 24.3 → 24.4. Candles must precede views and
  alerting because both consume the forming-bar query service; alerting must follow views
  because it implements a port views declares.
- The epic depends on the preceding migration epic having landed the shared kernel, the
  observability subdomain with its outbound notifier, and the boundary/image/namespace
  enforcement tests that every story here must keep green.
- Downstream: the archive context (next epic) consumes the verified-days port introduced by
  24.1; the legacy signals package is finally retired two stories into the next epic, which is
  the removal deadline several shims here point at; the research notebooks epic depends on 24.4
  having created the research context.
