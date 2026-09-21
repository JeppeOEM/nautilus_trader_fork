---
title: 'Story 22.10: Rankings show every collected coin across venues, with an exchange filter'
type: feature
created: '2026-09-21'
status: awaiting-operator
baseline_revision: 94f5cd5fe63adc587aedecb91808bf929be38ede
review_loop_iteration: 0
final_revision: bb58127f4b05c3a944ed0b0a3f5504f3ac4b7016
followup_review_recommended: false
context:
  - '{project-root}/troll/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/22-10-rankings-show-every-collected-coin-across-venues-with-an-exchange-filter.md'
warnings: [oversized]
operator_actions:
  - "Deploy to nifelheim with all three collectors running (make up), then run `redis-cli subscribe rankings:live` and confirm rows for DYDX, BYBIT (-LINEAR and -SPOT) and HYPERLIQUID, each with a non-zero USD volume24h."
  - "Open the web rankings page through the data_api tunnel. Confirm that all venue chips are selected by default and that deselecting one hides only that venue's rows and survives a reload."
  - "Run make tui with Hyperliquid and Bybit rows present. Confirm the coins-pane columns stay aligned and that `/` + `.bybit` narrows to Bybit rows."
  - "After about 10 minutes of steady state, check GET /api/errors and confirm the ranking_engine.volume24h count is not growing (a growing count means a collected instrument has no USD volume from its venue)."
  - "On nifelheim, watch `docker stats ranking_engine` over about an hour and confirm memory stays flat at about 3x the instrument count (epic 13 baseline). The engine now makes 4 volume polls a minute instead of 1."
---

<intent-contract>

## Intent

**Problem:** `ranking_engine` polls USD 24h volume from dYdX only and reads it back as `_VOLUME_24H.get(iid, 0.0)`, so every Bybit/Hyperliquid row on `rankings:live` carries a fabricated `volume24h = 0` (DATA-01) and sinks to the bottom of the default volume mode. The web rankings page has no one-click venue filter, and the TUI coins pane's 20-char instrument column overflows on Hyperliquid/Bybit ids (TUI-02).

**Approach:** Poll each venue's own USD 24h volume inside `ranking_engine` (the only computer of ranking metrics, SSOT-02/AD-9). A venue whose volume is unavailable leaves its rows out of volume mode loudly, never at 0. Add a venue chip row to the web page, persisted per viewer in `localStorage`. Make the TUI instrument column fit every venue's ids and name the venue filter in the help text.

## Boundaries & Constraints

**Always:**
- Volume is USD (OBS-03): dYdX `volume24H`, Bybit `turnover24h` (linear, plus spot only when the quote is USDT/USDC), Hyperliquid `dayNtlVlm`.
- Bybit `-LINEAR` and `-SPOT` rows stay separate and are never summed.
- Every failure that continues goes through `error_ledger.record("ranking_engine.volume24h", ...)` (DATA-07). One venue failing never drops another venue's volumes.
- The `rankings:live` wire schema is unchanged apart from `volume24h` being `None` on a row with no volume in volatility mode. Existing readers already render `None` as "—".
- Web and TUI both read `rankings:live` verbatim (SSOT-02/04). The web rank column stays the message rank.
- Chip state lives in `localStorage` wrapped in try/catch. The page renders all venues when storage is empty or blocked.

**Block If:** none. Every decision is resolvable from the story and the code.

**Never:**
- Fetch or compute volume in `data_api`, the frontend or `bot_tui`.
- Rank a coin at 0 because its volume is missing.
- Change `parse_volume_24h` (dYdX), the collectors, `data_api`, `nautilus_trader/` or `crates/`.
- Add a new dependency, or a new TUI keybinding.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Three venues healthy | fresh dYdX/Bybit linear+spot/HL iids; every poll OK | every row ranked by its own USD volume, all venues interleaved | none |
| Venue poll fails once | Bybit fetch raises; the last good Bybit poll is younger than the max age | Bybit rows keep their last good volumes; other venues are updated | ledger `ranking_engine.volume24h` for the failed venue |
| Venue volume too old / never fetched | no successful Bybit poll within `_VOLUME_MAX_AGE_NS` | Bybit rows are absent from the volume-mode ranks and present in volatility mode with `volume24h: None` | ledger once per venue per cycle, plus once per fresh iid missing volume per cycle |
| Coin not listed by its venue | fresh iid with no entry in that venue's payload | same as above, for that iid only | ledger once per iid per poll cycle |
| Bybit spot, non-USD quote | spot `ETHBTC` with `turnover24h` in BTC | not in the volume dict, so no volume for that iid | no ledger at parse time; the per-iid missing ledger fires only if it is collected |
| Unparseable Bybit/HL value | `turnover24h: ""`, `dayNtlVlm: null` | that iid is skipped, never set to 0 | ledger per iid |
| Malformed HL payload | not a `[meta, ctxs]` pair | the venue poll counts as failed | ledger (venue poll failed) |
| Web: venue deselected | BYBIT chip toggled off | Bybit rows hidden; FilterPanel conditions and tab still apply | none |
| Web: new venue appears | the stored deselected set does not contain HYPERLIQUID | HL shown (only deselected venues are stored) | none |
| Web: storage throws | `localStorage.getItem` throws | all venues shown; toggles still work in memory | swallowed in try/catch |
| TUI: long id | `1000000MOGUSDT-LINEAR.BYBIT` (27 chars) | the 26-char cell keeps `.BYBIT` visible and elides the middle of the symbol with `…` | none |

</intent-contract>

## Code Map

- `troll/ranking_engine/engine.py` -- `_VOLUME_24H` (:177), dYdX fetch/parse (:180-217), `_volume_loop_task` (:220), `_current_ranks` (:424-463), `main` (:770)
- `troll/ranking_engine/tests/test_engine.py` -- `_reset_state` (:30), volume-mode tests (:186-260)
- `troll/bybit_collector/open_interest.py:31-55` -- Bybit `_URLS` map and the `{symbol}-LINEAR.BYBIT` id construction to mirror
- `troll/frontend/src/pages/RankingsPage.tsx` -- `rows`/`visibleRows` (:275-299), `FilterPanel` placement (:302); the `.filter-panel`/`.tabbtn` styles are in `troll/frontend/src/index.css:133-151`
- `troll/frontend/src/pages/RankingsPage.test.tsx` -- `liveMessage`/`renderPage` helpers, `useLiveChannelMock`
- `troll/bot_tui/coins_pane.py` -- `filter_rows` (:78), `coin_header_text` (:104), `format_coin_row` (:115); `troll/bot_tui/bots_pane.py:53` has `fit()`, a pure helper with no urwid import
- `troll/bot_tui/app.py:152` -- help line for `/`
- `troll/CLAUDE.md` "Adding a venue" step 7 -- currently says 22.10 owns the fabricated-zero fix

## Tasks & Acceptance

**Execution:**
- [x] `troll/ranking_engine/engine.py`
  - Add pure `parse_bybit_volume_24h(tickers_json, product_type)`:
    - reads `result.list[].turnover24h` and builds ids `f"{symbol}-{LINEAR|SPOT}.BYBIT"`
    - for spot, keeps only symbols ending in `USDT`/`USDC`, with a `Known limit:` comment: stablecoin at par, non-USD quotes have no USD volume
    - an unparseable value is ledgered and skipped
  - Add pure `parse_hyperliquid_volume_24h(meta_and_ctxs)`:
    - pairs `meta.universe[i].name` with `ctxs[i].dayNtlVlm` and builds ids `f"{name}-USD-PERP.HYPERLIQUID"`
    - a bad shape raises `ValueError`; a bad value is ledgered and skipped
  - Add stdlib `urllib` fetchers run via `asyncio.to_thread`:
    - Bybit: GET `/v5/market/tickers?category=linear|spot`
    - Hyperliquid: POST `/info` `{"type":"metaAndAssetCtxs"}` with JSON content-type
    - `BYBIT_ENVIRONMENT`/`HYPERLIQUID_ENVIRONMENT` env vars (default `mainnet`) pick the mainnet/testnet URL, mirroring `DYDX_NETWORK`
  - Replace `_volume_loop_task` with a per-venue loop:
    - `_VENUE_VOLUMES[source] = (fetched_at_ns, volumes)`, replaced whole on success so delisted coins drop out
    - a failure ledgers and keeps the previous entry
    - then rebuild `_VOLUME_24H` from the sources younger than `_VOLUME_MAX_AGE_NS = 3 × VOLUME_POLL_SECONDS` (ledger each expired source)
    - then ledger each fresh iid that has no volume, once per cycle
    - split into small testable functions of at most ~30 lines each
  - In `_current_ranks`: `volume24h = _VOLUME_24H.get(iid)`. Volume mode drops the rows where it is `None`; volatility mode keeps every row.
  - Point `main` at the new loop.
- [x] `troll/ranking_engine/tests/test_engine.py` -- add tests on inline captured-shape fixtures:
  - both new parsers: happy path, spot quote filter, unparseable value skipped + ledgered, bad HL shape raises
  - three-venue ranks, each row with `venue`/`market`
  - missing volume: row excluded in volume mode, kept with `None` in volatility mode
  - one venue fails → the others update and the failed venue keeps its last good values
  - a venue past max age expires with a ledger entry
  - the per-iid missing-volume ledger counts once per cycle
  - extend `_reset_state` to clear `_VENUE_VOLUMES`
- [x] `troll/frontend/src/pages/RankingsPage.tsx`
  - Chip row above `FilterPanel`: one `tabbtn` toggle per venue in `unique(rows.venue).sort()`, with `aria-pressed`, inside a `.filter-panel` container.
  - State is the *deselected* venue set, stored in `localStorage["rankings-deselected-venues"]` as a JSON array with try/catch on read and write. Storing only deselected venues is what makes a new venue selected by default.
  - Drop rows from deselected venues before `applyFilters`. Ranks stay message ranks.
  - Show a dim note when every row is hidden by venue.
- [x] `troll/frontend/src/pages/RankingsPage.test.tsx` -- tests:
  - default shows DYDX/BYBIT/HYPERLIQUID rows
  - deselecting BYBIT hides only Bybit rows
  - a venue appearing later is shown
  - chips compose with a `venue = DYDX` condition
  - the selection persists to and restores from localStorage
  - a throwing storage still renders all rows
  - clear localStorage between tests
- [x] `troll/bot_tui/coins_pane.py`
  - Add `fit_instrument_id(iid, width)`: pads via `bots_pane.fit`. When too long, it keeps the `.VENUE` suffix and elides the middle of the symbol.
  - The instrument column becomes 26 wide (`_INSTRUMENT_WIDTH`) in both the header and the rows.
  - Update the `filter_rows` docstring to name venue filtering.
- [x] `troll/bot_tui/app.py:152` -- help text names `.DYDX` / `.BYBIT` / `.HYPERLIQUID` as venue filters.
- [x] `troll/bot_tui/tests/test_coins_pane.py` -- tests:
  - a 24-char HL id fits exactly
  - a 27-char Bybit id truncates to 26 and keeps `.BYBIT`
  - `format_coin_row` and `coin_header_text` instrument cells share one width
  - `filter_rows(rows, ".bybit")` returns only Bybit rows
- [x] `troll/CLAUDE.md` "Adding a venue" step 7 -- rewrite from "22.10 owns the fix" to the current state: a new venue must add its USD volume parser/fetcher to `ranking_engine`, or its rows are loudly absent from volume mode.

**Acceptance Criteria:**
- Given all three collectors are publishing, when `rankings:live` is built in volume mode, then it holds a row for every fresh iid of every venue that has USD volume, and each missing one is visible as a `ranking_engine.volume24h` ledger count, never as a 0.
- Given the web rankings page, when it loads, then all venues' chips are selected. Toggling one hides only that venue's rows, composes with FilterPanel conditions and the tab, and survives a reload.
- Given the TUI coins pane with HL and Bybit ids, when rendered, then every row's columns align and `/` + `.bybit` narrows to Bybit rows. The help text says so.

## Design Notes

Storing *deselected* venues rather than selected ones makes AC#3's "a newly added exchange is shown by default, never hidden by stale state" hold by construction, with no re-sync effect.

The story asked for the TUI row to stay "inside 80 columns". It cannot: the pane is already 192 chars wide (`#`, instrument and 15 `RANKING_COLS` × 11). The column is therefore widened to 26, which fits every HL 1-5-letter coin and every Bybit id up to 26, and anything longer is middle-elided so the venue suffix never disappears. Completion Notes records this.

## Auto Run Result

Status: awaiting-operator

**Summary:**
- `ranking_engine` now sources USD 24h volume per venue: dYdX `volume24H`, Bybit linear + USDT/USDC spot `turnover24h`, and Hyperliquid `dayNtlVlm`.
- The sources are polled concurrently and each is bounded by a timeout.
- A source keeps its last good values through a failure and expires after 3 missed polls.
- A row with no volume is left out of volume mode loudly (one `ranking_engine.volume24h` ledger count per instrument per cycle) instead of being ranked at a fabricated 0. It stays in volatility mode with `volume24h: null`.
- The web rankings page gets a venue chip row. It stores the deselected venues per viewer in `localStorage`, so a new venue is shown by default, and it composes with the FilterPanel and the tabs.
- The TUI instrument column is 26 wide. Longer ids keep their `-MARKET.VENUE` tail. The help text names `.DYDX`/`.BYBIT`/`.HYPERLIQUID` as venue filters.

**Files changed:**
- `troll/ranking_engine/engine.py`: per-venue parsers and fetchers, source map, poll/rebuild/missing-ledger cycle, and exclusion of volume-less rows from volume mode.
- `troll/ranking_engine/tests/test_engine.py`: about 30 new tests (parsers, three-venue ranking, failure/expiry/empty/timeout, ledger cadence, request construction). Six existing tests now set an explicit volume instead of relying on the old 0.0 default.
- `troll/frontend/src/pages/RankingsPage.tsx` and `.test.tsx`: venue chips, persistence and composition, with 9 tests.
- `troll/bot_tui/coins_pane.py`, `troll/bot_tui/app.py` and `troll/bot_tui/tests/test_coins_pane.py`: `fit_instrument_id`, the 26-wide column, venue-filter help text and tests.
- `troll/docker-compose.yml`: `BYBIT_ENVIRONMENT` and `HYPERLIQUID_ENVIRONMENT` on `ranking_engine`.
- `troll/CLAUDE.md`: "Adding a venue" step 7 rewritten.
- `troll/docs/DATA_DICTIONARY.md`, `troll/docs/DATA_INTEGRITY_AUDIT.md` (D-54 FIXED, D-55 accepted limit) and `troll/frontend/src/pages/docs/data.ts`: docs.

**Review:** 9 patches applied (1 high, 1 medium, 7 low), 1 deferred (the dYdX parser's missing-field → 0, which predates this story and which the spec kept unchanged), and 17 rejected (spec'd behaviour, by-design loud ledgering, or noise).

**Verification:**
- `pytest ranking_engine/tests bot_tui/tests -W error` in the troll-collector image: 368 passed.
- `vitest run src/pages`: 108 passed. `tsc -b` is clean and `oxlint` is clean on the changed files.
- `ruff`: no new findings on the changed lines. The findings that remain were already there.
- `mypy`: only the existing untyped `_value(ind)`.
- Live smoke against the real APIs (host network): dydx 296, bybit-linear 883, bybit-spot 480 and hyperliquid 234 markets. BTC was non-zero on all four and the ledger stayed empty.

**Residual risks:**
- Right after a restart, the volume mode list is empty until the first poll cycle finishes, typically about 1–2 s and at most one fetch timeout.
- Bybit spot volume assumes USDT/USDC trade at par with USD (D-55).
- The live three-collector check and the VPS memory check are operator actions; see `operator_actions`.

## Verification

**Commands:**
- `cd troll && python -m pytest ranking_engine/tests bot_tui/tests -q` -- expected: all pass, no new warnings
- `cd troll/frontend && npx vitest run src/pages && npx tsc -b` -- expected: pass, clean
- `ruff check troll/ranking_engine troll/bot_tui && mypy` on the changed files -- expected: clean

**Manual checks (if no CLI):**
- Live smoke from the host: call the three new fetch+parse functions against the real APIs. Expect non-empty dicts with non-zero USD volume for `BTC-USD-PERP.DYDX`, `BTCUSDT-LINEAR.BYBIT`, `BTCUSDT-SPOT.BYBIT` and `BTC-USD-PERP.HYPERLIQUID`.
- Operator (VPS): run `redis-cli subscribe rankings:live` and check that `/api/errors` is quiet in steady state, then watch `ranking_engine` memory at about 3× the instrument count.
