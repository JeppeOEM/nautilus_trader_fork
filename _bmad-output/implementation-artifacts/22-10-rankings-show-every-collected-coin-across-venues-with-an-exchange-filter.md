# Story 22.10: Rankings show every collected coin across venues, with an exchange filter

Status: awaiting-operator

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the dashboard operator,
I want the rankings page to list every coin any collector is currently collecting — dYdX, Bybit (linear + spot) and Hyperliquid — by default, and to narrow it by exchange with one click,
so that a multi-venue watchlist is the normal view and a single venue is a filter, not the other way round.

## Acceptance Criteria

1. **All collected coins by default.** `rankings:live` (and therefore the web rankings page and the bot_tui coins pane) contains one row per fresh instrument on `snapshots:raw` from **every** venue; nothing is hidden by venue. A coin absent from the list means "not collected / feed stale" (the existing freshness rule), never "filtered out silently".
2. **Per-venue 24h USD volume.** `ranking_engine` sources `volume24h` (USD) for Bybit (`GET /v5/market/tickers?category=linear` and `category=spot`, field `turnover24h`) and Hyperliquid (`POST /info {"type":"metaAndAssetCtxs"}`, field `dayNtlVlm`) alongside dYdX's `perpetualMarkets.volume24H`, polled on the same cadence. A row whose venue volume is unavailable is left out of volume mode **loudly** (`error_ledger`, DATA-01: no volume is not zero volume), never ranked at 0. OBS-03 holds: USD, never token units.
3. **Exchange filter, web.** The rankings page gets a venue chip row (one chip per venue present in the payload, e.g. `DYDX · BYBIT · HYPERLIQUID`), all selected by default; deselecting hides that venue's rows; it composes with the existing `FilterPanel` conditions and the tab/sort state; the selection is a per-viewer convenience persisted in `localStorage` (try/catch, renders correctly without it). Story 19.5's `venue = X` text condition keeps working.
4. **Exchange filter, TUI (SSOT-04).** The bot_tui coins pane shows the venue for every row without overflow (TUI-02: HL ids are 24 chars, the column is 20) and can be narrowed by venue with the existing filter mechanism (`coins_pane.py:80-101` substring filter — `.BYBIT` already works; the header/help text says so). Same rows, same order as the web page (SSOT-02: both read `rankings:live` verbatim).

## Tasks / Subtasks

- [ ] Task 0 — preconditions: 22.1 (Bybit/HL publish `snapshots:raw`) and 22.4 (`market` field, `market_kind`) merged. Runs **last** in Epic 22 except the optional 22.9.
- [ ] Task 1 — `ranking_engine/engine.py`: per-venue volume (AC: #1, #2)
  - [ ] Keep `parse_volume_24h` (dYdX) as is. Add `parse_bybit_volume_24h(tickers_json, product_type) -> dict[str, float]` (`turnover24h` is USDT-quoted USD; ids `f"{symbol}-LINEAR.BYBIT"` / `f"{symbol}-SPOT.BYBIT"` — same id construction as `bybit_collector/open_interest.py:115-126`) and `parse_hyperliquid_volume_24h(meta_and_ctxs_json) -> dict[str, float]` (`[meta, ctxs]` pair; `meta.universe[i].name` ↔ `ctxs[i].dayNtlVlm`; id `f"{name}-USD-PERP.HYPERLIQUID"`). Pure functions, unit-tested on captured fixtures; unparseable → `error_ledger.record("ranking_engine.volume24h", ...)` + skip, exactly like the dYdX parser (`:198-210`).
  - [ ] Fetchers via stdlib `urllib` in `asyncio.to_thread` (the dYdX fetcher's shape, `:179-216`): Bybit `https://api.bybit.com/v5/market/tickers?category=linear|spot`, Hyperliquid `POST https://api.hyperliquid.xyz/info` (weight 2 — negligible at `VOLUME_POLL_SECONDS`). `_volume_loop_task` polls all three and `.update()`s `_VOLUME_24H` per venue; one venue failing must not drop the others' volumes (record + keep last good values, and mark that venue's rows stale-volume rather than 0 — see next item).
  - [ ] Volume mode: `_ACTIVE_MODE == "volume"` ranks by `_VOLUME_24H`; a fresh iid with **no** entry (venue poll never succeeded, or the venue doesn't list it) is excluded from the ranked list with one `error_ledger` count per instrument per poll cycle, not silently placed last at 0 (`engine.py:430-448`). Volatility mode is unaffected (computed from snapshots).
  - [ ] `ranking_engine/tests/test_engine.py`: three-venue fixtures → rows for all three venues; missing venue volume → row excluded + ledger count; `market`/`venue` present per row (22.4).
- [ ] Task 2 — web: venue chips (AC: #3)
  - [ ] `RankingsPage.tsx`: derive `venuesPresent = unique(rows.map(r => r.venue)).sort()`; a chip row above `FilterPanel` (reuse the terminal/ANSI tag style from 15.9; no new dependency); state `selectedVenues: Set<string>` initialised to all present (and re-synced when a *new* venue appears so a newly added exchange is shown by default, never hidden by stale state); rows filtered by `selectedVenues.has(row.venue)` **before** the `FilterPanel` conditions and the sort. Persist to `localStorage` under one key, wrapped in try/catch; absent/blocked storage → all venues.
  - [ ] Keep 19.5's `venue`/`venue_kind` text filter fields — they are the general mechanism; chips are the fast path.
  - [ ] `RankingsPage.test.tsx`: default shows DYDX/BYBIT/HYPERLIQUID rows; deselecting BYBIT hides only Bybit rows; a venue appearing later is auto-selected; chips + a `venue = DYDX` condition compose. `vitest` + `tsc -b` clean.
- [ ] Task 3 — bot_tui parity (AC: #4)
  - [ ] `coins_pane.py`: the `{'INSTRUMENT':<20}` column (`:110, :128`) must `fit()` (bots_pane's helper, TUI-02) — HL ids overflow it today and misalign every column after. Either widen to 24 or `fit(iid, 20)` with ellipsis; pick the one that keeps the row inside 80 columns with `RANKING_COLS`, and say which in Completion Notes. The `.VENUE` suffix stays visible either way (venue lives in the id — SIGNAL-01/19.1, no extra column needed).
  - [ ] Filter hint: wherever the coins pane exposes its filter input, the help/header text mentions `.BYBIT` / `.DYDX` / `.HYPERLIQUID` as venue filters (substring match already implemented at `:80-101`). No new keybinding unless one already exists for filters.
  - [ ] `bot_tui/tests`: `fit` on a 24-char HL id; substring filter `.bybit` returns only Bybit rows (case-insensitive, `:101`).
- [ ] Task 4 — verification
  - [ ] Local with all three collectors publishing: `redis-cli subscribe rankings:live` shows rows for every venue with non-zero USD volume; web page lists all by default, chips narrow; TUI coins pane aligned with HL ids present; `/api/errors` shows no `ranking_engine.volume24h` entries in steady state.
  - [ ] VPS: `ranking_engine` memory stays flat with ~3× the instrument count (epic 13 baseline; nifelheim is a 2 vCPU / 3.7 GB box — note the new poll count).

## Dev Notes

### What already exists — do not rebuild it

- Web venue column + typed `venue = X` / `venue_kind = cex|dex` filter conditions (story 19.5, `RankingsPage.tsx:176-177, 390-391`, `filters.ts` text-match). This story adds the chip row on top; the condition mechanism is untouched.
- `market` column/filter + chart badge (22.4). `venue_of` (`ml_signals/venue.py`), `venue_kind`/`market_kind` (`common/venues.py`).
- Rows are already whatever is fresh on `snapshots:raw` (`engine.py:430`); once 22.1 lands, Bybit/HL rows appear on their own. The gap is **volume**, not membership.

### Volume is the only venue-specific computation, and it stays in `ranking_engine` (SSOT-02, AD-9)

`ranking_engine` is the sole computer of ranking metrics; web and TUI are pure readers of `rankings:live`. Do not compute or fetch volume in `data_api`, the frontend or `bot_tui`. Bybit's `turnover24h` and Hyperliquid's `dayNtlVlm` are already USD-denominated (OBS-03); dYdX's `volume24H` likewise. Spot vs linear volume on Bybit are separate rows (`-SPOT.BYBIT` vs `-LINEAR.BYBIT`), never summed.

### "No volume" ≠ "zero volume" (DATA-01)

The dYdX parser already leaves an unparseable coin out, loudly. Extend that rule to a whole venue whose poll fails: keep the last good values and count the failure; if there never was a good value, the venue's coins are absent from volume mode and the error bar says why. Never fabricate a 0.

### Filter state is a per-viewer convenience

`localStorage` is the right home for chip selection (Artifact/browser-storage rule: conveniences only, try/catch, page must render without it). It is not shared state and never reaches the backend.

### Project Structure Notes

- Modified: `troll/ranking_engine/engine.py` (+ `tests/test_engine.py`, fixtures), `troll/frontend/src/pages/RankingsPage.tsx` (+ test), `troll/bot_tui/coins_pane.py` (+ test), possibly `troll/bot_tui/app.py` (help text).
- Unchanged: collectors, `data_api` (rows pass through; `RankingsResponse` already carries `venue`/`market`), catalog, `crates/**`.

### References

- [Source: user request 2026-09-20 — "rankings show all collected coins by default, filter by exchange"; added as the last Epic 22 story].
- [Source: troll/ranking_engine/engine.py:167-225 (mode, dYdX volume poll), :430-460 (row build, volume mode)].
- [Source: troll/frontend/src/pages/RankingsPage.tsx:176-177, 240-252, 390-391; troll/frontend/src/pages/FilterPanel.tsx:5-40; filters.ts] — 19.5's filter mechanism.
- [Source: troll/bot_tui/coins_pane.py:80-101 (substring filter), :104-130 (20-char instrument column)].
- [Source: troll/bybit_collector/open_interest.py:101-126] — Bybit tickers fetch + id construction to mirror.
- [Source: _bmad-output/implementation-artifacts/19-5-*.md Completion Notes] — "ranking_engine still only ranks dYdX snapshots" caveat this story closes.
- [Source: ARCHITECTURE-SPINE.md#AD-9; troll/CLAUDE.md SSOT-01..05, OBS-03, DATA-01, TUI-01/02, SIGNAL-01, MEM-02] — rules applied.

## Dev Agent Record

### Agent Model Used

claude-opus-5 (bmad-dev-auto, run d4b2)

### Debug Log References

### Completion Notes List

- Implemented against `spec-22-10-rankings-show-every-collected-coin-across-venues-with-an-exchange-filter.md` (the executable spec; see its Auto Run Result for the review triage).
- **Volume:**
  - `ranking_engine` polls four sources concurrently every 60 s: `dydx`, `bybit-linear`, `bybit-spot` and `hyperliquid`.
  - Each source is replaced whole on success. A failure, timeout (45 s), Bybit `retCode != 0` or empty parse is ledgered and the source keeps its last good values, which expire after 180 s.
  - `volume24h` is `None` when unknown. Volume mode leaves such a row out and ledgers `ranking_engine.volume24h` once per instrument per cycle. Volatility mode keeps the row.
  - Bybit spot counts only USDT/USDC-quoted pairs (`Known limit:`, audit D-55).
- **TUI column (Task 3 decision):** the story asked to keep the row inside 80 columns, which is impossible. The row was already about 192 chars (rank + instrument + 15 `RANKING_COLS` × 11). The instrument column was widened from 20 to 26 (`_INSTRUMENT_WIDTH`), which fits every Hyperliquid id (22–26 chars) and every Bybit id up to 26. Longer ids go through `fit_instrument_id`, which keeps the `-MARKET.VENUE` tail and elides the symbol with `…`, e.g. `1000000MOGUS…-LINEAR.BYBIT`, so perp vs spot and the venue always stay visible. Header and rows share one constant.
- **Web:** a venue chip row stores only the *deselected* venues in `localStorage["rankings-deselected-venues"]`, so a new venue is shown by default. It composes with the FilterPanel and the tab, and ranks stay message ranks.
- **Live smoke (2026-09-21, host network):**

  | Source | Markets | BTC 24h USD volume |
  |---|---|---|
  | dydx | 296 | 4.0 M |
  | bybit-linear | 883 | 3.52 B |
  | bybit-spot | 480 | 367 M |
  | hyperliquid | 234 | 1.80 B |

  The ledger stayed empty.
- **Not done here (operator):** the Task 4 live three-collector check and the VPS memory check. See the spec's `operator_actions`.

### File List

- troll/ranking_engine/engine.py
- troll/ranking_engine/tests/test_engine.py
- troll/frontend/src/pages/RankingsPage.tsx
- troll/frontend/src/pages/RankingsPage.test.tsx
- troll/frontend/src/pages/docs/data.ts
- troll/bot_tui/coins_pane.py
- troll/bot_tui/app.py
- troll/bot_tui/tests/test_coins_pane.py
- troll/docker-compose.yml
- troll/CLAUDE.md
- troll/docs/DATA_DICTIONARY.md
- troll/docs/DATA_INTEGRITY_AUDIT.md
