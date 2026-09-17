# Spec: TradingView-style Chart + Crypto Coins Screener — Multi-Exchange Revision

This is a revision of an external build brief (`collected-spec.md`) against
this project's actual codebase. **Every original section is carried through
in full** — nothing is trimmed or summarized away. Additions are marked with
a `> **Grounded in this project:**` or `> **New for multi-exchange:**`
callout immediately after the section they apply to, so the original spec
text stays intact and it's always clear what's original vs. added.

**What's genuinely new here, relative to the original brief:**
- A "Connection" section at the top of Part B, since this is the one place
  the real app's shape (a single flat Rankings table) and the spec's shape
  (a tabbed screener) diverge — not just in completeness, but in structure.
- A new **Part D — Multi-exchange support**, covering Bybit and Hyperliquid
  alongside the existing dYdX collector.
- Grounding callouts throughout Part A/B pointing at the real files that
  already implement a piece of the spec (Epic 15, `troll/frontend/`), so this
  reads as "extend this" rather than "build from zero."

Everything else — layout, toolbar ordering, drawing-tool mechanics, indicator
mechanics, bar replay, alerts, the volume-profile family, and the full
placement/operation-parity tables — is the original spec, unmodified.

---

## Part 0 — Shared foundation

### 0.1 Tech stack
- React 18+ (function components, hooks)
- **Chart (Part A):** `lightweight-charts` v5.x (`npm i lightweight-charts`)
- **Screener (Part B):** hand-rendered table (no data-grid library — see
  correction under §B5) for sorting/custom column management — no charting
  library needed here, no chart-view mode is in scope
- Plain CSS variables for theming; colors/visual style are yours, not
  TradingView's — see Part A §A8 for what "matches TradingView" actually
  means once you're supplying your own look
- Mock/static OHLCV data for both parts; one `useMarketData`-style hook is
  the seam for a real feed later, shared by both
- React state/context only — no Redux needed in either part

> **Grounded in this project:** the stack is already real, not hypothetical.
> `troll/frontend/` runs React 19, `lightweight-charts` 5.2.1, and
> `@tanstack/react-query` for server state (confirmed in
> `troll/frontend/package.json`). There is **no TanStack Table today**, and
> per the correction under §B5, none should be added — `RankingsPage.tsx`
> is a hand-rendered table and stays hand-rendered through Part B's
> tab/filter/column additions.
> The visual system is not "yours to design" in the abstract — it already
> exists: `troll/frontend/src/theme.css` implements a full 16-color VGA/ANSI
> palette with a semantic token layer on top (Story 15.9), fixed dark
> `color-scheme`, no light/dark toggle. Data is not mock/static — it's live,
> continuously collected market data from `ParquetDataCatalog` via
> `troll/data_api` (FastAPI facade over the catalog + Redis pub/sub).

**lightweight-charts facts to design around (Part A only):**
- v5 creates every series via `chart.addSeries(SeriesType, options)`, e.g.
  `chart.addSeries(CandlestickSeries, {...})` — not `addCandlestickSeries()`.
- Built-in series types: Candlestick, Bar, Line, Area, Baseline, Histogram.
  Part A only ever instantiates Candlestick and Line (§A2).
- Native multi-pane support (`chart.addPane()`, or a `paneIndex` on
  `addSeries`) — use this for the volume pane and indicator panes.
- Volume Profile (§A7) has **no native support at all** — it's a from-
  scratch canvas overlay (a custom Primitive). Budget real effort here.
- Drawing tools beyond a plain price line are **not built into the
  library** — trendlines and the measurement tool are custom Primitives.
- ES2020 module output, no CommonJS.

> **Grounded in this project:** `troll/frontend/src/components/chart/LightweightChart.tsx`
> already owns a single `createChart()` instance with a keyed pane registry
> using `chart.addPane()` — the multi-pane mechanic this section describes is
> already built and working (used today for the volume pane and per-coin
> indicator panes, Stories 15.3–15.4). §A3's drawing-tool Primitives and
> §A7's Volume Profile Primitive are the genuinely new pieces — build them
> against this existing chart/pane infrastructure, not a fresh setup.

### 0.2 Shared data model
Both parts read from the same underlying series shape — the screener's
per-coin Technicals/Performance columns are computed from a coin's candle
history exactly the way a chart indicator is computed from the symbol
it's attached to:

```ts
type Candle = {
  time: number;   // unix seconds, UTC
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

// Part A: one Candle[] per symbol, at the base timeframe;
// other timeframes are derived client-side by resampling
// (open=first, high=max, low=min, close=last, volume=sum).

// Part B: one Coin per screener row, wrapping its own Candle[] history.
type Coin = {
  id: string;           // e.g. "BTC"
  name: string;         // e.g. "Bitcoin"
  price: number;
  marketCap: number;
  candles: Candle[];
};
```

> **New for multi-exchange:** this project already has a real identifier
> convention to key `Candle`/`Coin` data by, and it should be used instead of
> inventing a new one: Nautilus's `InstrumentId` = `"{SYMBOL}.{VENUE}"`
> (confirmed: `crates/model/src/identifiers/instrument_id.rs`, parsed via
> `rsplit_once('.')` so venue is always the suffix after the last dot — e.g.
> `BTC-USD-PERP.DYDX`, `BTCUSDT.BYBIT`, `BTC-PERP.HYPERLIQUID`). Two changes
> to the shapes above:
> - `Coin.id` should be the full `instrument_id` (`"{SYMBOL}.{VENUE}"`), not
>   a bare symbol — otherwise the same symbol on two venues collides.
> - Add an explicit `venue: string` field to `Coin` (and to any API response
>   shape derived from it). Today, nothing in `troll/frontend/src/api/schema.ts`
>   or `troll/data_api`'s routes surfaces venue as its own field — it's only
>   present as an implicit suffix inside `instrument_id` strings. Surfacing
>   it explicitly is what lets the screener filter/group/label by exchange
>   without string-parsing IDs on the frontend. See Part D for the full data
>   model change.

### 0.3 Shared indicator engine — build once, use in both parts
Part A's chart overlay/pane indicators (§A4) and Part B's Technicals
columns (§B2) are **the same underlying calculation**, just rendered to a
different destination — a chart pane vs. a table cell. Don't write the
indicator math twice:

```ts
// One pure function per indicator, shared by both parts.
type IndicatorFn = (candles: Candle[], params: Record<string, number>) =>
  number | Record<string, number>;   // single value (RSI) or multi (MACD)
```

- Part A calls this once per bar (to plot a series across the visible
  range) and re-renders as bars are added/replayed.
- Part B calls this once per coin (to get the *latest* value for that
  row's column) and recalculates only when that coin's data or the
  indicator's params change.
- The **catalog** (which indicators exist, their param schemas, their
  display names) is also shared: Part A's Indicators dialog (§A4.1) and
  Part B's "Edit columns" dialog (§B2.1) are the same interaction pattern
  — search/browse → click a result → it's added immediately with default
  params, no confirm step — applied to two different targets (a chart pane
  vs. a table column). Build one `IndicatorPicker` component parameterized
  by `onAdd: (indicator) => void` and reuse it in both places.
- Where the two parts' MVP indicator sets differ (Part A ships SMA + RSI;
  Part B ships RSI, SMA, EMA, MACD, Bollinger Bands), that's just which
  catalog entries are registered — the mechanism is identical.

> **Grounded in this project:** this rule is not aspirational — the chart
> side already proves half of it. `troll/ml_signals/indicators.py` is the
> shared indicator-function module, and `troll/frontend/src/components/chart/IndicatorPicker.tsx`
> is exactly the "search → click → adds immediately, no confirm" component
> this section describes (Story 15.6). What doesn't exist yet is the table
> side: `RankingsPage.tsx` has fixed columns only, no add/configure/remove
> mechanic. Building §B2 means **reusing** `IndicatorPicker.tsx` and
> `ml_signals/indicators.py` as the destination-agnostic engine this section
> already calls for — not writing a second indicator system.

---
## Part A — Chart

## A1. Layout (right sidebar intentionally excluded)

```
┌─────────────────────────────────────────────────────────┐
│ TOP TOOLBAR                                              │
├──────┬────────────────────────────────────────────────┤
│ LEFT │              MAIN CHART PANE                    │
│ TOOL │  (candlesticks/line + overlay indicators +      │
│ BAR  │   Volume Profile histogram overlay, §A7)         │
│      ├────────────────────────────────────────────────┤
│      │           VOLUME PANE (own y-axis)               │
│      ├────────────────────────────────────────────────┤
│      │        INDICATOR PANE(S) (own y-axis, stacked)   │
├──────┴────────────────────────────────────────────────┤
│ BOTTOM STATUS BAR + REPLAY CONTROL BAR (when active)     │
└─────────────────────────────────────────────────────────┘
```

No right sidebar, ever, in this build (see §A9).

> **Grounded in this project:** the main chart pane + volume pane + stacked
> indicator pane(s) stack already exists in `LightweightChart.tsx` and
> `ChartPage.tsx` (Stories 15.3–15.4). The top toolbar and left toolbar in
> this diagram do **not** exist yet in the real app — see the placement
> reconciliation note under §A8.1.

## A2. Chart type — restricted set

Only two chart types are selectable, via a simple toggle (not a dropdown
menu of 13 types like TradingView):

- **Candlestick** (default) — standard OHLC candles only.
- **Line** — plots close price only.

**Explicitly not offered:** Bar, Area, Baseline, Heikin Ashi, Renko, Kagi,
Point & Figure, Range bars, Hollow candles, Volume candles.

### Candle appearance — deliberately fixed, not customizable

- One up color, one down color, taken from your own theme/design system —
  not TradingView's specific hex values, not a per-user setting. No
  settings dialog for body/border/wick color, no "color based on previous
  close" toggle, no hollow/filled choice. TradingView exposes all of this
  in Settings → Symbol → Candles; this build exposes none of it — the two
  colors are hardcoded constants, full stop.

> **Grounded in this project:** this toggle already exists — Story 15.7
> ("Lines mode") built the Candlestick ↔ Line switch, and candle colors are
> already fixed constants sourced from `theme.css`'s ANSI palette, not a
> settings dialog. No new work needed here beyond what's already shipped.

---

## A3. Left toolbar — drawing tools (restricted to 3)

Only these three tools exist. No trendline variants, no Fibonacci, no
shapes/text, no Elliott wave/Gann tools, no pitchfork, no brush/highlighter.

1. **Line (trendline)** — click-drag between two points on the price/time
   plane. Implemented as a custom Primitive holding two `{time, price}`
   anchors, redrawn on each `subscribeCrosshairMove`/pan/zoom via the
   primitive's `updateAllViews`.
2. **Horizontal line** — a single click sets a price level. This one *is*
   natively supported: use `series.createPriceLine({ price, color, lineWidth,
   axisLabelVisible: true, title })`. Draggable = update the price line's
   `price` on drag.
3. **Measurement tool** — click-drag a rectangle across any two points and
   overlay a small label showing: price delta (absolute + %), number of
   bars/time spanned, and (if over the volume pane) summed volume across the
   selected bars. This is also a custom Primitive; there is no built-in
   equivalent in lightweight-charts, unlike TradingView where it's a native
   ruler tool.
4. Keep a simple "select/cursor" default mode and an "Esc cancels the active
   tool" behavior, same as before.

No eraser-all, no tool grouping/menus, no favorites list of tools.

> **Grounded in this project:** none of this exists yet — this is a genuinely
> new epic. Build the two custom Primitives (trendline, measurement) and the
> native `createPriceLine` horizontal-line tool against the existing
> `LightweightChart.tsx` chart instance rather than a new chart setup.

---

## A4. Indicators — exact mechanics for choosing and stacking

You already have the indicator *calculations*. This section specs only the
**UI and placement mechanics**, copied from how TradingView's own
"Indicators, metrics, and strategies" flow works, so the LLM implements the
same interaction model:

### A4.1 Adding an indicator
- A single button ("Indicators") opens a **search/browse dialog**, not a
  settings panel. The dialog has:
  - A text search box (filters by name as you type)
  - A short, flat category list (for MVP: just "Overlays" and "Oscillators" —
    real TradingView has many more categories, you don't need them)
  - A list of results; clicking a result **immediately adds it to the chart**
    with default parameters — there is no "confirm" step. Settings are
    changed *afterward*, not during selection.
- Each already-added indicator appears as an entry in a **chart legend**
  strip (top-left of whichever pane it lives in), showing: indicator name,
  its current key values updated live off the crosshair position, a gear
  icon (opens settings), an eye icon (show/hide without removing), and an ×
  (remove).

### A4.2 Where an indicator renders — the overlay-vs-pane rule
This is the core mechanic to copy exactly:
- Every indicator definition declares, at creation time, whether it is an
  **overlay** (renders on the main price pane, sharing price's y-axis — e.g.
  a moving average) or **non-overlay** (renders in its own new pane below,
  with an independent y-axis — e.g. RSI, MACD, a 0–100-bounded oscillator).
- This is a static property of the indicator itself, not a per-use choice
  the user makes at add-time. (TradingView's Pine Script equivalent is the
  `overlay = true/false` parameter on the `indicator()` declaration; mirror
  that idea — each indicator config object in your code has an
  `overlay: boolean` field.)
- **Multiple overlay indicators** stack in the *same* main pane, sharing one
  y-axis, differentiated only by color/legend — no separate panes are
  created for them.
- **Multiple non-overlay indicators** each get their *own* separate pane by
  default, stacked vertically below the main chart and below the volume
  pane, in the order they were added. Each new pane is resizable by dragging
  its top border; panes divide the available vertical space proportionally.
- **Merging two non-overlay indicators into one shared pane** should be
  possible via a "move to pane" action (drag the legend entry onto another
  pane, or a right-click → "Move to → Pane N" menu) — this mirrors
  TradingView's "how to place two indicators on one pane" behavior, where
  panes are a placement target independent of how many indicators live in
  them.
- A separator/handle is rendered between stacked panes so the user can
  resize each pane's height by dragging.

### A4.3 Indicator set — already built, no MVP subset needed

> **Correction — supersedes the original brief's "ship exactly two" MVP
> framing:** this project does not need to curate a small MVP indicator set
> the way a from-scratch build would. The full catalog already exists and is
> already wired into the chart's Indicators dialog:
> - `troll/ml_signals/chart_indicators.py`'s `INDICATOR_CATALOG` — **34
>   native indicators** mirroring `nautilus_trader.indicators` (moving
>   averages: Simple/Exponential/Weighted/Hull/Adaptive/Double-Exponential/
>   VIDYA/Wilder; bands/channels: Bollinger, Keltner, Donchian, Keltner
>   Position; oscillators: RSI, MACD, Stochastics, CCI, ATR, Volatility
>   Ratio, Aroon, Directional Movement, ROC, CMO, Psychological Line, Bias,
>   Relative Volatility Index, Vertical Horizontal Filter; plus VWAP, OBV,
>   Pressure, Klinger Volume Oscillator, Archer MA Trends, Ichimoku Cloud,
>   Linear Regression, Efficiency Ratio — confirmed via direct count).
> - `troll/ml_signals/custom_indicators.py`'s `CUSTOM_INDICATOR_CATALOG` —
>   **3 custom indicators**: CumulativeVolumeDelta, CancelPressure,
>   OrderFlowImbalance.
>
> Every chart-side epic in this document (and the Technicals tab, §B2.4)
> should expose this **entire existing 37-entry catalog**, not a hand-picked
> subset — there is no calculation work left to do here, only UI/placement
> work (§A4.1/§A4.2) and, for Part B, wiring the same catalog to table
> columns instead of chart panes.

> **Grounded in this project:** the add-dialog → immediate-add → legend
> gear/eye/× mechanic (§A4.1) and the overlay-vs-pane placement rule
> (§A4.2) are already built: `IndicatorPicker.tsx` + `LightweightChart.tsx`'s
> pane registry (Stories 15.4/15.6), with per-coin indicator configuration
> already working, already serving the full 37-entry catalog above. The
> "move to pane" merge action is **not yet built** — that's a real gap
> against this section if you want it. Reuse this exact engine and catalog
> for Part B's Technicals tab per Part 0.3, rather than building a second
> one or a second, smaller catalog.

---

## A5. Bar Replay — build this fully

Mirrors TradingView's actual interaction model:

1. **Entry point:** a "Replay" button in the top toolbar (rewind-style icon).
   Clicking it puts the chart into "pick a start bar" mode: cursor becomes a
   crosshair with a vertical guide line following it across the chart.
2. **Start point selection:** clicking a candle sets that bar as the replay
   start. A vertical marker line is drawn at that bar. Everything after it
   is visually hidden (simplest implementation: slice the dataset fed to
   `setData()` down to the start index).
3. **Replay control bar** appears (bottom of chart, above the status bar)
   with, left to right: Play/Pause, Step-back, Step-forward, a speed
   selector (e.g. 0.5×/1×/2×/5×, cycling through a fixed set — not a
   continuous slider), a "Go to…" button that re-enters start-point-picking
   mode without losing replay state, and an Exit-replay (×) button.
4. **Playback:** Play reveals one additional bar at fixed intervals scaled
   by the speed setting (e.g. base interval 700ms ÷ speed). Step-forward /
   step-back move exactly one bar and pause autoplay if it was running.
5. **Interaction during replay:** drawing tools (§A3) and indicators (§A4)
   must keep working and keep recalculating live as bars are revealed —
   don't special-case them out.
6. **Exiting replay** restores the full dataset and removes the control bar
   and vertical marker.

Deliberately not built (matches real TradingView limitations, so no need to
over-engineer): no tick-level/sub-bar playback, no automatic trade
simulation/PnL tracking (a user could manually place a horizontal-line
"order" and eyeball it, but there's no order ticket, fills, or journal), no
synchronized multi-chart replay (this build only ever has one chart pane
anyway).

> **Grounded in this project:** not built yet — a genuinely new epic. Note
> that `useCandles.ts`/`useLiveCandle.ts` already separate "historical bars"
> from "the forming live bar" (Story 15.5's live-candle-edge work) — the
> replay slice-to-start-index mechanic in step 2 should reuse that same
> historical/live split rather than inventing a second one.

---

## A6. Alerts — Telegram delivery (spec only, partially built)

> **Deferred — build this last, as its own final epic, after everything
> else in this document (chart epics, screener epics, and multi-exchange
> support) is done.** It's a backend-first addition (the local alert engine
> + webhook POST described below), not a priority for the current build
> sequence — noted here so it isn't accidentally pulled forward when
> epics/stories are created from this spec.

**Important reality check to give the LLM:** TradingView has no native
Telegram integration. Alerts are delivered generically via a **Webhook URL**
field in the alert dialog — TradingView POSTs a JSON/text payload to
whatever URL you give it. Getting that payload into Telegram requires an
external relay (a small bot/server you run) that receives the webhook and
calls the Telegram Bot API. That relay is infrastructure, not a frontend
feature — do not try to build a hosted Telegram bot as part of this React
app.

**What to actually build (frontend only):**
- An "Create Alert" dialog, opened from a clock/bell icon, with:
  - Condition builder: `<series/indicator> <crosses/greater than/less
    than> <value or another series>` (for MVP, just support price crossing
    a static value, and price crossing a horizontal-line drawing from §A3)
  - Frequency: Once per bar close / Once per bar / Only once
  - An expiration setting (date, or "never")
  - A **message template** textarea supporting placeholder variables the
    same way TradingView does, e.g. `{{ticker}}`, `{{close}}`, `{{time}}`,
    `{{interval}}` — these get string-substituted at fire time
  - A **Webhook URL** field (plain text input, no validation beyond "looks
    like a URL") — this is the field a user would point at their own
    Telegram-relay bot's endpoint
- A local alert engine: on every new bar/tick, evaluate all active alert
  conditions against the current data; when one fires, do a `fetch(POST)` to
  its Webhook URL with the templated JSON body, and also show an in-app
  toast/notification (since you have no push/SMS/email infra either).
- An Alerts list view (just a simple list, not the full manager) showing
  each alert's condition, status (active/triggered/expired), and a delete
  button.

**Not built:** the Telegram bot itself, chat_id lookup flow, Discord/Slack/
email/SMS delivery, "Notify on price change % " and other exotic condition
types, alert templates library, multi-condition (AND/OR) logic.

> **Grounded in this project:** not built yet — a genuinely new epic. Since
> `troll/data_api` already runs a live Redis-subscriber loop (`ws/live.py`,
> `redis_bus.py`) for pushing live data to the frontend, the "evaluate
> conditions on every new bar/tick" alert engine described here fits
> naturally as a consumer of that same live feed rather than a second
> polling loop — check `redis_bus.py` before building a new subscription
> path for this.

---

## A7. Volume Profile family — BUILD all of these (except Anchored)

Unlike §A6, this is not deferred — build the full family this round:
**Fixed Range, Visible Range, Session, Session HD, and Periodic**. Only
**Anchored Volume Profile** and **Volume Candles** (the chart-type variant)
are excluded — see §A9.

### A7.0 Shared engine — build this once, all five variants consume it

Every variant is the *same calculation* run over a different window of
bars. Build one function and reuse it:

```ts
type ProfileRow = { priceLow: number; priceHigh: number; upVolume: number; downVolume: number };
type VolumeProfile = {
  rows: ProfileRow[];      // one per price bucket, low→high
  poc: number;             // price of the row with max total volume
  vah: number;             // top of value area
  val: number;             // bottom of value area
  totalVolume: number;
};

function buildVolumeProfile(candles: Candle[], rowCount: number, valueAreaPct = 0.7): VolumeProfile
```

Algorithm:
1. Find the min/max price across the given `candles` slice; divide that
   range into `rowCount` equal buckets.
2. For each candle, distribute its `volume` across the buckets its
   `low..high` range spans (simplest correct approach for mock data: split
   volume evenly across every bucket the candle's range touches; a real
   feed with intrabar ticks could weight this more precisely, but don't
   over-engineer it here).
3. Classify each candle as **up** (`close >= open`) or **down**, and add its
   volume to that bucket's `upVolume` or `downVolume` respectively — this
   drives the two-color histogram bars.
4. **POC** = the bucket with the highest `upVolume + downVolume`.
5. **Value Area**: starting from the POC bucket, repeatedly add whichever
   *adjacent* bucket (above or below the current VA range) has more volume,
   until accumulated volume ≥ `valueAreaPct` of `totalVolume`. VAH/VAL are
   the price bounds of the final included range.

### A7.1 Rendering — one Primitive, parameterized

Build a single `VolumeProfilePrimitive` that takes a `VolumeProfile` object
and draws it as a horizontal histogram: bars grow rightward from the
right-hand price axis (or from a fixed anchor x-position for Fixed
Range/Anchored-style placement), one bar per `ProfileRow`, split into an
up-colored segment and a down-colored segment. Draw the POC row in a
distinct highlight color/thicker line, and shade the Value Area band (VAH to
VAL) with a translucent overlay so it reads as a "zone." All five variants
below just call this one primitive with a different `VolumeProfile` input
and a different x-anchor/width.

### A7.2 The five variants — what differs is only the window + trigger

| Variant | Bars passed to `buildVolumeProfile` | Recompute trigger | Placement (left toolbar vs. Indicators dialog, per §A4) |
|---|---|---|---|
| **Fixed Range Volume Profile (FRVP)** | Candles between two user-clicked timestamps | Once, on drag-release; recompute only if the user drags an edge to resize | Drawing tool — lives in the left toolbar (§A3), not the Indicators dialog. Click-drag like the measurement tool, but confirms into a persistent profile object instead of a transient label. |
| **Visible Range Volume Profile (VRVP)** | Whatever candles are currently in the visible time-scale range | On every pan/zoom — subscribe to `chart.timeScale().subscribeVisibleTimeRangeChange()` and rebuild | Indicators dialog (§A4.1), added like any other indicator; `overlay: true` since it renders on the main price pane |
| **Session Volume Profile (SVP)** | Candles grouped by calendar day (session), one profile per day, using the base/finest timeframe data regardless of the chart's current timeframe | Once per session boundary; recompute only the current (in-progress) session's profile as new bars arrive | Indicators dialog, `overlay: true` |
| **Session Volume Profile HD** | Same grouping as SVP, but with a higher `rowCount` (e.g. 100+ vs SVP's default ~24) and the primitive's redraw wired to zoom level so bar thickness stays legible at any zoom | Same trigger as SVP, plus a redraw (not recompute) on zoom | Indicators dialog, `overlay: true`. Ship as a second config preset of the *same* component as SVP — don't build a separate code path, just different default `rowCount` + a `respondsToZoom: true` flag |
| **Periodic Volume Profile (PVP)** | Candles grouped by a user-chosen recurring period (weekly, 4-hourly, monthly — a simple dropdown, not a full cron-like scheduler) | On period boundary, same pattern as SVP | Indicators dialog, `overlay: true`, with one settings field: `period: 'daily' \| 'weekly' \| '4h' \| 'monthly'` |

### A7.3 Settings each variant should expose (in its gear-icon settings panel, per §A4.1)
- **Row size** (number of buckets) — direct `rowCount` input
- **Value area %** — default 70, adjustable
- **Up/down volume colors**
- **Show/hide POC line**, **show/hide Value Area shading**
- For SVP/PVP only: **number of past sessions/periods to render** (e.g. "show
  last 5 sessions" — each with its own independent POC/VAH/VAL, not merged
  into one)

### A7.4 What NOT to do here
- Don't build true tick-level footprint math (that's Volume Footprint, a
  Premium-tier feature and out of scope — see §A9)
- Don't try to make FRVP/VRVP share one "live" component that also handles
  the fixed-range case dynamically — keep FRVP's "confirm once" interaction
  model distinct from VRVP's "always recompute" model, they're genuinely
  different UX even though they share the calculation engine

### A7.5 Backend requirement — confirm `data_api` can actually supply this

> **New — explicit backend check, since Volume Profile is a real data
> requirement, not just a frontend rendering problem.** `buildVolumeProfile()`
> (§A7.0) only needs `Candle[]` (OHLCV) — no raw order-book or tick data —
> so the existing `GET /api/candles/{instrument_id}` route
> (`troll/data_api/routes/candles.py`) is the one backend dependency for all
> five variants, not a new endpoint. Confirmed relevant to each variant:
> - **FRVP** needs arbitrary historical range (user click-drags between two
>   past timestamps, possibly far back). `routes/candles.py` already
>   supports cursor-paginated scroll-back (`before_ns`/`limit`, AD-F3) —
>   verify in practice that scrolling back to whatever range FRVP needs
>   doesn't hit the route's server-enforced caps (`_MAX_CANDLES_LIMIT`,
>   `_MAX_QUERY_SPAN_SECONDS` in that file) before assuming it "just works."
> - **VRVP** needs whatever's in the currently-visible range — already what
>   the chart loads today.
> - **SVP/SVP-HD/PVP** need calendar-boundary-grouped candles (daily/weekly/
>   4h/monthly) — these are just different client-side groupings of the same
>   `/api/candles` response, not a new server-side aggregation.
> - **No new route, no raw-snapshot/tick data needed** — don't reach for
>   `/api/snapshots` (per-second order-book data) for this; it's the wrong
>   granularity and unnecessary for the profile algorithm as specified.

> **Grounded in this project:** not built yet — a genuinely new epic, and
> the most implementation-heavy one in Part A. Build `VolumeProfilePrimitive`
> against the existing `LightweightChart.tsx` chart/pane instance. Session
> boundaries (SVP/SVP-HD) should use the same calendar-day grouping logic
> this project already applies for daily rollups in `ranking_engine`, rather
> than a second date-bucketing implementation — check there before writing
> a new one.

---

## A8. Placement & operation parity (visual style is entirely yours)

You're using your own retro/ANSI-style rendering, not TradingView's visual
design, so §A8 is not about pixels, colors, or screenshots at all anymore.
"Matches TradingView" here means exactly two things: **(a)** each tool
lives in the same relative slot/group as it does on TradingView, and
**(b)** operating the chart — mouse and keyboard behavior — works the same
way once you interact with it. Nothing below asks for a particular font,
size, spacing unit, or color.

> **Grounded in this project:** "your own retro/ANSI-style rendering" is not
> hypothetical — it's exactly `theme.css`'s 16-color VGA/ANSI palette +
> semantic token layer (Story 15.9), already applied across every existing
> page. Build §A8's toolbars/legend/status bar against that existing token
> system rather than introducing a second visual language.

### A8.1 Placement — relative order and grouping only
Order and grouping is the only thing that needs to match; how you render a
"toolbar" (a menu bar, a row of bracketed labels, a status-line strip —
whatever your ANSI aesthetic calls for) is entirely up to you.

**Top toolbar**, left to right, in this order:
1. Symbol selector
2. Timeframe selector (1m/5m/15m/1H/4H/1D/1W) — grouped together as one
   unit, immediately after the symbol selector
3. *(group boundary)*
4. Chart-type toggle (Candlestick / Line)
5. *(group boundary)*
6. Indicators entry point
7. Fit-content control
8. Jump-to-latest control
9. *(group boundary)*
10. Theme toggle

The "group boundary" markers are the only structural fact — TradingView
visually separates these into clusters with dividers; represent that
clustering however fits an ANSI layout (a literal `|` divider character, a
blank cell, a different background band — your call), just keep the same
four clusters in the same order: [symbol+timeframe] [chart type]
[indicators+fit+jump] [theme].

> **Reconciliation needed — the one place the real app's shape and this
> spec's shape actually conflict:** this section assumes an in-page symbol
> selector and timeframe selector. The real app doesn't have either today —
> navigation is table-first (Rankings row click → `navigate(/chart/${instrument_id})`,
> confirmed in `RankingsPage.tsx`), and bar size is a fixed constant
> (`BAR_SECONDS` in `useCandles.ts`), not a UI control. There's also no
> theme toggle — `theme.css` is deliberately fixed dark, no light/dark
> switch (Story 15.9 explicitly excludes one). Recommended resolution, to
> pick explicitly rather than silently import the generic model: keep slot
> 1 ("Symbol selector") as a **read-only label + "back to Rankings" link**
> bound to the current `instrument_id`, not a free-text/dropdown picker,
> since coin selection already happens on the Rankings/screener page (see
> Part B's new Connection section); build the timeframe selector (slot 2)
> for real if multi-timeframe viewing is wanted — nothing today provides
> it; and drop slot 10 (theme toggle) as inapplicable, since this project
> has deliberately chosen not to have one.

**Left toolbar**, top to bottom, in this order:
1. Cursor (default/select mode)
2. Crosshair toggle
3. *(group boundary)*
4. Line (trendline) tool
5. Horizontal line tool
6. Measurement tool

Same principle: cursor/crosshair are one cluster, the three drawing tools
are the second cluster, in that relative order top-to-bottom.

**Legend** (§A4.1): top-left corner of whichever pane an indicator lives in
— i.e. above/over the candles for an overlay indicator, at the top of its
own pane for a non-overlay one. That's the only placement fact; whether it
renders as an overlaid text block, a fixed header row, or an ANSI-style
bracketed status line is your design.

**Status bar** (§A1): bottom of the chart area, above the replay control
bar when one is active. Same "which edge" fact only.

**Panes, top to bottom** (§A1, §A4.2): main chart pane, then volume pane,
then indicator pane(s) in the order each indicator was added. That vertical
stacking order is the structural fact; a resizable divider between panes
matters as a *behavior* (see §A8.2), not as a visual element.

### A8.2 Operation — the mouse/keyboard behavior to copy exactly
This is the part that actually matters most for "operates the same,"
consolidated here from where each behavior is specified elsewhere in this
doc, so it reads as one checklist:

| Action | Behavior to copy | Spec'd in |
|---|---|---|
| Pan | Click-drag horizontally anywhere on the main pane | native to lightweight-charts |
| Zoom (time axis) | Mouse wheel over the chart | native |
| Zoom (price axis) | Click-drag vertically on the price axis | native |
| Fit view | Button click → snap visible range to fit all loaded data | §A8.1 (Fit-content control) |
| Jump to latest | Button click → scroll to the most recent bar | §A8.1 (Jump-to-latest control) |
| Crosshair readout | Hover → status bar updates with O/H/L/C/time at that bar | §A1, `subscribeCrosshairMove` |
| Resize a pane | Click-drag the divider between two stacked panes; hit-zone should be wider than the visible divider line so it's easy to grab | §A4.2 |
| Add an indicator | Click Indicators → click a result → it's added immediately with default settings, no confirm step | §A4.1 |
| Change indicator settings | Click the gear icon on its legend entry, after it's already on the chart | §A4.1 |
| Toggle indicator visibility | Click the eye icon on its legend entry | §A4.1 |
| Remove an indicator | Click the × on its legend entry | §A4.1 |
| Draw a line/trendline | Select the tool, click-drag between two points | §A3 |
| Place a horizontal line | Select the tool, single click sets the price level; the line is draggable afterward to change price | §A3 |
| Measure | Select the tool, click-drag a rectangle; shows price delta, bar count, and volume sum for the selection | §A3 |
| Cancel an in-progress tool | `Esc` | §A3 |
| Enter Bar Replay | Click Replay → cursor becomes a picker → click a bar to set the start point | §A5 |
| Step through replay | Step-forward/back buttons move exactly one bar; Play/Pause runs it at the selected speed | §A5 |
| Change replay start point mid-session | "Go to…" re-enters picker mode without losing replay state | §A5 |
| Create an alert | Click the alert icon → condition builder → set frequency/expiration/webhook → save | §A6 |

Anything not in this table (visual styling, spacing, exact colors, fonts)
is not part of "operates the same" and is entirely your call.

## A9. What we deliberately do NOT do, even though TradingView does

This is the explicit "do not build" list for the LLM — call this out loud in
the code review if any of these sneak back in:

- **Right sidebar** — no watchlist, no alerts-manager panel, no community
  idea stream, no news feed, no DOM/order-entry panel. This entire vertical
  strip does not exist in this build.
- **Watchlist and Screener** — excluded entirely this round; different task.
- **Candle customization** — no body/border/wick color pickers, no
  "color based on previous close" toggle, no hollow-candle or volume-candle
  variants. One fixed up/down color pair, period.
- **Extra chart types** — no Bar, Area, Baseline, Heikin Ashi, Renko, Kagi,
  Point & Figure, Range bars, Hollow/Volume candles. Candlestick and Line
  only.
- **Extra drawing tools** — no Fibonacci retracement/extension tools, no
  shapes, no text annotations, no pitchforks, no Gann/Elliott wave tools, no
  brush/highlighter, no pattern-drawing helpers. Line, horizontal line, and
  the measurement tool only.
- **Full indicator library** — no 100+ built-in indicator catalog, no
  Pine-Script-style custom scripting editor, no public/community script
  library or "Add to favorites" indicator list.
- **Candlestick pattern auto-recognition** — no automated bullish/bearish
  pattern scanner or pattern tooltips.
- **Multi-chart grid layouts** — no 2/4/6/8-pane workspace of different
  symbols/timeframes; this build is a single chart.
- **Anchored Volume Profile** — the one Volume Profile variant we don't
  build (§A7 builds all the others). No "click one bar and grow forward
  indefinitely" mode.
- **Volume Candles** — the chart-type variant that encodes volume into
  candle width/shape. Not one of the two chart types in §A2, and not
  confused with the Volume Profile *indicators* in §A7, which are a
  completely different feature despite the similar name.
- **Volume Footprint** — per-bar bid/ask order-flow breakdown inside each
  individual candle. This is a different, heavier feature than anything in
  §A7 and stays out of scope.
- **Real-time data / WebSocket feed** — mock/static data only.
- **Full alert delivery infra** — no built-in Telegram bot, no Discord/
  Slack/email/SMS delivery; only the generic Webhook URL field + local
  browser toast (see §A6).
- **Trade execution / DOM / order tickets** — no simulated or live order
  placement anywhere, including during Bar Replay.
- **Tick-level Bar Replay** — replay is bar-by-bar only, no sub-bar/tick
  scrubbing.

> **New for multi-exchange:** nothing above needs to change — venue/exchange
> support is additive to Part A (an indicator/chart already keys off a fully-
> qualified `instrument_id`, so a Bybit or Hyperliquid symbol needs no new
> chart-type, tool, or feature exclusion). One line to add to this list for
> clarity: **"Real-time data / WebSocket feed"** above is stated as excluded
> by the original brief (mock/static data only) — this project's chart is
> already live (Redis-backed, Story 15.5's live-candle-edge), so that
> exclusion does not apply here; it's listed as an intentional divergence,
> not an oversight.

---

## A10. Suggested build order for the LLM

1. `ChartPane` with a bare candlestick series over the mock dataset, resize
   handling
2. Chart-type toggle (Candlestick ↔ Line), timeframe switching
3. Volume pane (histogram, own pane)
4. Status bar wired to `subscribeCrosshairMove`
5. Fit/jump/theme controls
6. Indicator dialog + legend + overlay-vs-pane placement logic (§A4) with SMA
   and RSI as the two proof cases
7. Horizontal line tool (native `createPriceLine`)
8. Line (trendline) primitive, then the measurement-tool primitive (§A3) —
   these two are the genuinely hard, custom-plugin parts
9. Bar Replay (§A5)
10. Volume Profile shared engine (§A7.0) + rendering primitive (§A7.1), then
    wire up FRVP first (simplest — one-shot calc, no recompute-on-scroll),
    then VRVP (recompute on viewport change), then SVP → SVP HD → PVP
    (calendar-windowed, same session-boundary trigger pattern)
11. Alert dialog + local alert engine + webhook POST (§A6)
12. localStorage persistence of layout/settings (chart type, timeframe,
    theme, indicators + their settings, volume profile settings, pane sizes)
13. Placement & operation pass (§A8): check every tool sits in the right
    relative slot/group (§A8.1) and every listed interaction in the §A8.2
    checklist actually behaves that way, before calling the build done —
    no visual/screenshot comparison needed, this is a functional checklist

> **Grounded in this project:** steps 1–6 are already done (Stories
> 15.3–15.7/15.9, plus live data instead of mock). The remaining build order
> for the *next* epics starts at step 7: horizontal line tool → trendline →
> measurement tool (§A3) → Bar Replay (§A5) → Volume Profile family (§A7) →
> the §A8 placement/operation pass, folding in the reconciliation note under
> §A8.1 and the multi-exchange venue selector (Part D) at that same pass.
> **Alerts (§A6) moves to dead last**, its own final epic after everything
> else in this document (chart, screener, and multi-exchange) — see the
> deferral note under §A6.

---

## Part B — Crypto Coins Screener (Performance + Technicals only)

Mimics https://www.tradingview.com/crypto-coins-screener/ — the **Coins**
model (one row per coin, aggregated across all its markets), not the CEX
screener (one row per exchange pair) or DEX screener (one row per
liquidity pool). Only two tabs exist: Performance and Technicals; every
other tab TradingView ships (Overview, Valuation, Derivatives, Addresses,
Transactions, Sentiment) is excluded — see §B6.

### B0. Connection — how this becomes the real Rankings page

> **New — this is the piece that ties the real app together, added ahead of
> §B1 since it changes how the rest of Part B should be read.**
>
> `troll/frontend/src/pages/RankingsPage.tsx` (Story 15.2) is today one flat
> live table with fixed columns, no tabs. **It becomes this screener
> directly** — same page, same URL (`/`), same live-update mechanism
> (`useLiveChannel.ts`) — gaining the §B1 tab structure rather than being
> replaced by a separate "Screener" page:
>
> - **Performance tab (§B3)** needs price tracked over multiple lookback
>   windows (1H/4H/1D/1W/1M/YTD/1Y in the original brief). This is the
>   *same underlying need* as the parked Story 15.8 (31-day metrics
>   history): both require historical values per instrument over time.
>   They must share one query path against `troll/ranking_engine/metrics_store.py`
>   — already keyed by the *full* `instrument_id` (`PRIMARY KEY (ts,
>   instrument_id)`, confirmed by reading the file), already storing
>   `pct_1h`/`pct_24h` among its columns — rather than computing deltas
>   twice in two independently-maintained places. **Explicit decision to
>   make, not an incidental side effect:** `metrics_store.write()` currently
>   defaults `retain_days=31`, so any Performance-tab window beyond 31 days
>   (YTD, 1Y) requires deliberately extending that retention — decide the
>   actual windows you want before building this tab, rather than
>   discovering the 31-day ceiling mid-implementation.
> - **Technicals tab (§B2)** reuses the *same* indicator engine and add-
>   dialog already built for the chart — `ml_signals/indicators.py` +
>   `IndicatorPicker.tsx` (Stories 15.4/15.6) — per Part 0.3's "build once,
>   use in both" rule. The chart side already proves this pattern works;
>   Technicals is that same pattern's second consumer, not a new one.
> - `/history/:iid` (Story 15.8 — fully scoped, zero code written,
>   deliberately parked 2026-09-16; `HistoryPage.tsx` is currently a
>   placeholder) becomes the natural **drill-down destination reached from
>   the Performance tab** — e.g. a small "history" affordance on each
>   Performance row, or on the coin's row generally. This link does not
>   exist today: confirmed that `RankingsPage.tsx`'s row click only calls
>   `navigate(/chart/${row.instrument_id})`; `App.tsx` registers
>   `/history/:iid` but nothing links to it, so reaching it currently
>   requires typing the URL by hand. Wiring this link is part of building
>   this Connection, not an optional extra.
>
> Net effect: **Rankings, the Part B screener, and the 31-day History page
> are one coherent feature**, not three — a live current-value view
> (today's Rankings), a fixed-window historical-delta view (Performance
> tab, reusing `metrics_store`), a user-configurable indicator view
> (Technicals tab, reusing the chart's indicator engine), and a per-coin
> deep-dive (History page), all keyed by the same `instrument_id` and all
> reading from the same `metrics_store`/catalog.

## B1. Layout

```
┌───────────────────────────────────────────────────────────┐
│ FILTER PANEL (add/remove filter conditions)                │
├───────────────────────────────────────────────────────────┤
│ [ Performance ]  [ Technicals ]        ← column-set tabs   │
├───────────────────────────────────────────────────────────┤
│ Symbol | Name │ ...columns for the active tab...           │
│ (pinned, always visible regardless of active tab)          │
│ BTC    | ...  │                                             │
│ ETH    | ...  │                                             │
│ ...                                                          │
└───────────────────────────────────────────────────────────┘
```

- **Symbol/Name column is pinned** — stays visible no matter which tab is
  active; only the metric columns to its right change per tab. This
  matches how TradingView's own screener behaves: switching column sets
  never hides the identity of the row you're looking at.
- Switching tabs swaps the visible metric columns; it does not refetch or
  re-filter the row set — rows stay whatever the filter panel currently
  matches, only the *columns shown* change.

> **Grounded in this project:** the pinned symbol/name concept maps onto
> `RankingsPage.tsx`'s existing row structure (keyed by `instrument_id`,
> staleness-aware). What's new is the tab bar itself and the filter panel —
> neither exists today; the live table has no tabs and no filter UI.

## B2. Technicals tab — the core feature: user-managed indicator columns

This is the mechanic that makes this different from a fixed column set.
Reuse the same **add → configure → remove** pattern as chart indicators
(the "Indicators" dialog + legend gear/eye/× from the charting spec) —
same interaction model, different destination (a table column instead of
a chart pane).

### B2.1 Adding an indicator column
- An "Edit columns" control on the Technicals tab (a `+` button, matching
  TradingView's own "+ button to add a custom column set") opens the same
  kind of search/browse dialog as the chart's Indicators dialog.
- Clicking a result **adds it immediately** as one or more new columns,
  with default parameters — no confirm step, same as the chart version.
- Some indicators are single-value (RSI → one column). Some are
  multi-value (MACD → line/signal/histogram; Bollinger Bands → upper/
  mid/lower) — these add as a small group of adjacent columns under one
  shared header, not three unrelated columns scattered around.

### B2.2 Configuring a column
- Each indicator column's header has a gear icon → opens a settings
  popover for that indicator's parameters (period, source price, standard
  deviation multiplier, overbought/oversold thresholds — whatever that
  indicator takes). Changing a setting recalculates that column for every
  row immediately.
- Where TradingView itself exposes expanded parameter sets (RSI, Stoch
  RSI, Momentum, EMA, SMA, Keltner Channels are the ones documented as
  having deeper customization), mirror that — but since the whole point
  here is user control, don't artificially limit *other* indicators to
  fewer parameters than they actually have; expose whatever inputs the
  underlying calculation takes.

### B2.3 Removing / reordering columns
- An × on the column header (or in a column-manager side panel, your
  choice) removes it.
- Columns are drag-reorderable by their header, same as most data grids.
- Removing/reordering is purely a per-user view preference — it doesn't
  touch the underlying data or other users' views (if this is ever
  multi-user).

### B2.4 Indicator catalog — reuse the chart's full catalog, not a curated MVP

> **Correction — supersedes the original brief's curated 5-indicator MVP
> list:** don't build or curate a separate Technicals catalog. Expose the
> exact same 37-entry catalog already described in §A4.3
> (`chart_indicators.INDICATOR_CATALOG` + `custom_indicators.CUSTOM_INDICATOR_CATALOG`)
> as table columns. This is Part 0.3's "build once, use in both" rule
> applied literally — the catalog, param schemas, and calculation functions
> are 100% shared with the chart; only the destination (table cell vs. chart
> pane/legend) differs. Multi-value entries (MACD, Bollinger Bands, Keltner
> Channel, Donchian Channel, Ichimoku Cloud, Directional Movement, etc.) add
> as a small group of adjacent columns under one shared header, per the
> original brief's grouping rule below.

### B2.5 Rating columns — optional, decide before building
Real TradingView also ships aggregate **Overall / Moving Average /
Oscillator Rating** columns (each a Buy/Neutral/Sell rollup of ~15 MAs and
~11 oscillators). These are a legitimate *addable catalog entry* under
this same mechanic (their "indicator" is just "compute N sub-indicators,
average their -1/0/+1 signal, bucket the result") rather than a separate
system — include them in §B2.4's catalog only if you want that rollup
behavior; they're more work than any single indicator above since each
one wraps a whole basket of others.

> **Grounded in this project:** §B2.4's catalog is not new calculation work
> at all — it's the existing 37-entry catalog (§A4.3) already implemented in
> `chart_indicators.py`/`custom_indicators.py`. §B2.1–§B2.3's add/configure/
> remove/reorder mechanic is the only net-new work here, built on top of the
> existing `IndicatorPicker.tsx` component (per Part 0.3/§B0), applied to
> table columns instead of a chart legend.

---

## B3. Performance tab — fixed columns, no user editing here

Unlike Technicals, Performance is a plain fixed column set (matches how
TradingView treats it — "a historical view of the coin's profitability and
losses over time," not a user-configurable panel):
- % change: 1H, 4H, 1D, 1W, 1M, YTD, 1Y (pick the lookback windows you
  care about; all computed the same way — `(latest close − close N
  periods back) / close N periods back`)
- Each cell colored/signed by direction (positive/negative), no other
  interaction

> **Grounded in this project (see §B0 for the full design):** compute these
> deltas from `ranking_engine/metrics_store.py`'s stored history, not a
> separate calculation — `pct_1h`/`pct_24h` already exist as stored columns;
> other windows need either deriving from stored `price` history or
> extending what's stored. Decide the retention window before committing to
> which of 1H/4H/1D/1W/1M/YTD/1Y are actually buildable today vs. requiring
> a retention change.

---

## B4. Filter panel

- `+` button (or a keyboard shortcut, matching TradingView's `Shift+F`)
  opens a condition builder: `<field> <operator> <value>`.
- **Filterable fields include any currently-added Technicals column**, not
  just the base fields (price, market cap) — once you've added an RSI
  column in §B2, "RSI < 30" becomes available as a filter condition. This
  matches how TradingView's own screener treats columns and filters as
  drawing from the same underlying metric set.
- Filters apply to the row set regardless of which tab (Performance vs.
  Technicals) is currently displayed — filtering and column display are
  independent, per §B1.
- Multiple filter conditions combine with AND (don't build OR/grouped
  logic for MVP).

---

## B5. Table mechanics (baseline, not otherwise specified)
- Sort by clicking any column header, ascending/descending toggle
- A symbol/name search box above the table, filters rows by text match
- No pagination assumption either way — infinite-scroll or simple
  pagination, your call, not a functional requirement either of us has
  specified

> **Correction — no grid framework, ever, for this:** stay hand-rendered.
> `RankingsPage.tsx` is a plain hand-rendered table today (confirmed — no
> TanStack Table or equivalent installed), and it stays that way through the
> tab/filter/column-management additions above. Sort, drag-reorder, and
> column add/remove are all small enough to hand-roll directly against React
> state; a data-grid dependency is not warranted for this. The original
> brief's "e.g. TanStack Table" suggestion in Part 0.1 does not apply to this
> project.

---

## B6. What we deliberately do NOT build

- **Every other tab TradingView ships**: Overview, Valuation, Derivatives,
  Addresses (on-chain), Transactions (on-chain), Sentiment. Only
  Performance and Technicals exist in this build.
- **Chart view mode** — no per-row mini candlestick/line chart toggle.
  Table view only.
- **CEX/DEX row models** — this is the Coins model (one row per coin).
  Not one row per exchange pair, not one row per liquidity pool.
- **Watchlist scanning, watchlist add** — no watchlist integration at all
  this round.
- **Saved screens, screen sharing, autosave/undo-redo** — filter/column
  state lives in memory (or localStorage if you want it to survive a
  reload) for the current session only, not a saved-screens system.
- **Pine Screener / custom scripting console** — the indicator catalog in
  §B2.4 is hand-built entries, not a script editor.
- **AI Filter** (natural-language filter generation) — out of scope.
- **Real-time refresh / live data** — mock/static candle data only, same
  as the charting spec.
- **Row click → open chart** — not specified either way; add it only if
  you're building this alongside the charting spec and want the two
  connected, otherwise leave rows non-navigating.

> **Grounded in this project:** "Row click → open chart" is already decided
> and built — `RankingsPage.tsx` rows navigate to `/chart/:iid` today. "Real-
> time refresh / live data" is also already live (`useLiveChannel.ts`), not
> mock — same intentional divergence noted under §A9. "CEX/DEX row models"
> stays correct as written: this is still the Coins model even once Bybit/
> Hyperliquid are added (Part D) — one row per coin, aggregated, with venue
> as a column/filter, not a second row-per-exchange model.

---

## B7. Suggested build order

1. Table shell: pinned symbol/name column, mock `Coin[]` data, sort +
   search
2. Performance tab (fixed columns, §B3) — simplest, proves the tab-switch
   mechanic before tackling the hard part
3. Technicals tab skeleton: tab switch shows an empty state ("no columns
   yet — click + to add one")
4. Indicator catalog (§B2.4) — calculation functions first, reused from the
   charting spec if applicable
5. Add-column dialog (§B2.1) wired to the catalog
6. Column settings popover (§B2.2) + live recalculation on param change
7. Remove (§B2.3) and drag-reorder (§B2.3)
8. Filter panel (§B4), including making added Technicals columns filterable
9. Decide on §B2.5 (rating columns) last, since it's the most work relative
   to value — only build it if the simpler indicators in step 4 aren't
   enough

> **Grounded in this project:** step 1's "table shell" already exists as
> `RankingsPage.tsx` with real (not mock) data — start at step 2 (add the
> tab bar + Performance tab), reusing `metrics_store` per §B0/§B3, then
> proceed through steps 3–9 as written.

---

## Part C — Integration notes (only relevant if building both together)

- **Shared code, not shared UI.** Part A renders a chart; Part B renders a
  table. They share the `Candle` type, the indicator calculation functions,
  and the `IndicatorPicker` add-dialog component (Part 0.3) — nothing about
  their layouts, toolbars, or interaction models overlaps otherwise. Don't
  try to merge their toolbars or navigation; they're separate pages/views
  in the same app.
- **Optional link between them:** clicking a row in the screener (Part B)
  could navigate to that coin's chart (Part A) — this isn't specified
  either way in §A9/§B6 (both mark "row click → open chart" as optional),
  so decide it once and apply it consistently rather than leaving it
  ambiguous.
- **Suggested combined build order**, if building both from scratch as one
  app: build Part 0's shared engine and data model first (indicator
  functions + `Candle`/`Coin` types + the `IndicatorPicker` component),
  then build Part A and Part B in parallel or either order — neither
  depends on the other being finished, they only depend on Part 0.

> **Grounded in this project:** this decision is already made and shipped —
> Rankings rows navigate to `/chart/:iid` — and Part 0 is already half-built
> (`ml_signals/indicators.py`, `IndicatorPicker.tsx`). The "build order"
> above is moot here; what's left is filling in Part A's new epics (§A3,
> §A5–§A7) and Part B's tab structure (§B0–§B2), both against the existing
> Part 0 foundation, plus Part D below.

---

## Part D — Multi-exchange support (new)

The whole system above is dYdX-only today: no `venue`/`exchange` field
exists anywhere in `troll/data_api`'s routes, `troll/frontend/src/api/schema.ts`,
or the frontend UI. This part specifies what changes to add **Bybit** and
**Hyperliquid** alongside dYdX, plus a path to a CEX/DEX distinction, since
dYdX and Hyperliquid are both on-chain perp DEXes and Bybit is a CEX — a real
distinction, not a hypothetical one, once these three coexist.

### D1. Data model

Keep Nautilus's own `InstrumentId` convention as the one identifier used
everywhere: `"{SYMBOL}.{VENUE}"` (confirmed:
`crates/model/src/identifiers/instrument_id.rs`, parsed via
`rsplit_once('.')`, e.g. `BTC-USD-PERP.DYDX`, `BTCUSDT.BYBIT`,
`BTC-PERP.HYPERLIQUID`). `Venue` itself is an open string in Nautilus, not a
closed enum — each adapter declares its own constant (`DYDX_VENUE`,
`BYBIT_VENUE`, and a Hyperliquid equivalent in
`nautilus_trader/adapters/{dydx,bybit,hyperliquid}/constants.py`) — so there
is no registry to extend on the Nautilus side; this project owns its own
venue list.

Stop treating venue as an implicit suffix. Add an explicit `venue: string`
field to:
- `troll/frontend/src/api/schema.ts` (currently has no such field at all —
  confirmed via grep)
- Every `data_api` response shape that includes an `instrument_id` today
  (`routes/candles.py`, `routes/snapshots.py`, `routes/indicators.py`,
  `routes/indicator_series.py`, `routes/rankings.py`)

### D2. Catalog

Already structurally ready for this — `ParquetDataCatalog`'s own
partitioning is `catalog/data/<data_type>/<instrument_id>/...` (confirmed
today: `catalog/data/custom_dydx_open_interest/XLM-USD-PERP.DYDX/`), keyed
by the *full* `SYMBOL.VENUE` id. A Bybit collector's `BTCUSDT.BYBIT` output
and a Hyperliquid collector's `BTC-PERP.HYPERLIQUID` output partition
cleanly alongside dYdX's with **zero catalog schema change**, as long as all
three collectors write into (or `data_api` reads across) the same catalog
root(s).

### D3. `data_api`

Today, six places independently redeclare the same single-venue default:

```python
CATALOG_PATH: str = os.environ.get("CATALOG_PATH", "troll/dydx_collector/catalog")
```

in `app.py`, `routes/candles.py`, `routes/snapshots.py`, `routes/indicators.py`,
`routes/indicator_series.py` (confirmed via grep — each file redeclares this
independently to avoid a circular import from `app.py`, per each file's own
comment). Consolidate to one shared, non-duplicated setting pointed at a
catalog root every collector writes into — this is simpler than inventing a
venue→path registry, and consistent with this project's YAGNI stance, since
the catalog already partitions correctly by instrument_id (§D2). Add
`venue` as a queryable/filterable dimension on `routes/rankings.py` so the
screener (Part B) can filter its row set by exchange.

### D4. Collector layer

New exchange collectors mirror `troll/dydx_collector/`'s own architecture —
**not** the `TradingNode`/`Strategy`-based pattern used by the existing
`scripts/bybit_recorder/` on the `gg` branch (that pattern is the one
`troll/CLAUDE.md`'s FORK-02 documents as the actual cause of the historical
OOM/shutdown-wedge bug; treat it as prior art only, not something to port).

Both new venues already have the same kind of direct Rust/PyO3 client dYdX
uses — confirmed in `nautilus_trader/core/nautilus_pyo3.pyi`:
- `BybitHttpClient` / `BybitWebSocketClient` (backed by `crates/adapters/bybit/`)
- `HyperliquidHttpClient` / `HyperliquidWebSocketClient` (backed by `crates/adapters/hyperliquid/`)

So the "own asyncio loop, own callback, direct PyO3 client, never
`TradingNode`/`DataEngine`" pattern dYdX's collector uses is directly
buildable for both, with no missing native-client blocker. Structure:

- `troll/bybit_collector/` and `troll/hyperliquid_collector/` as siblings of
  `troll/dydx_collector/`, each owning its own asyncio loop, buffer, and
  flush timer against `ParquetDataCatalog.write_data()`.
- **No shared base class imposed up front.** Per this project's DESIGN-01
  (YAGNI), extract common helpers (e.g. the open-interest-poll shape from
  `dydx_collector/open_interest.py`, the `DydxSecondSnapshot`-equivalent
  schema) only once a second real collector makes the duplication concrete
  — not speculatively before either Bybit or Hyperliquid collector exists.
  This mirrors why `scripts/common_recorder/` (built for the
  `TradingNode`-based pattern) is *not* a relevant shared base here — it
  belongs to the architecture this project deliberately avoids.
- Each collector re-derives whatever precision/re-stamping caveats are
  venue-specific — dYdX's `_at_fixed_precision()` workaround
  (`troll/dydx_collector/client.py`) exists because of a dYdX-specific
  wire-format quirk (mark/index price precision derived from trailing-zero
  count); don't assume it applies to Bybit/Hyperliquid without checking
  their own wire formats first.

### D5. Frontend

- **Rankings/screener (Part B)**: gains a `venue` column and a venue filter
  (currently assumes a single dYdX feed — confirmed no `venue` concept
  exists in `RankingsPage.tsx` today).
- **Chart (Part A)**: `ChartPage.tsx` needs no structural change — it
  already keys purely off the `instrument_id` URL param
  (`/chart/:iid`) and displays it as-is (`<h1>{instrumentId}</h1>`), so a
  fully-qualified `BTCUSDT.BYBIT` or `BTC-PERP.HYPERLIQUID` id already flows
  through correctly once §D1's `venue` field exists upstream.
- **Top toolbar symbol slot (§A8.1)**: once multiple venues exist, the
  "back to Rankings" resolution recommended under §A8.1 should show the
  venue alongside the symbol (e.g. `BTC-USD-PERP · DYDX`), not just the raw
  instrument_id string, so it's legible which exchange is being viewed.

### D6. CEX vs. DEX distinction

Nautilus has no usable native flag for this at the adapter level:
`Venue.is_dex()` (`crates/model/src/defi`) only fires on a
`"<Chain>:<DexType>"`-formatted venue string, gated behind the `defi` cargo
feature — and none of dYdX's, Bybit's, or Hyperliquid's adapter constants
use that format (all three are flat strings: `"DYDX"`, `"BYBIT"`, and
Hyperliquid's own constant). So this project must self-maintain the
distinction rather than relying on any Nautilus mechanism:

```python
# troll/common/venues.py (new, small, self-maintained — not a Nautilus lookup)
VENUE_KIND: dict[str, str] = {
    "DYDX": "dex",
    "HYPERLIQUID": "dex",
    "BYBIT": "cex",
}
```

Use this to power a CEX/DEX filter or label in the screener (Part B) once
wanted — it's a plain dict, not a class hierarchy or plugin system, per
DESIGN-01.

---

## Open decisions for the next bmad pass

Flagged throughout this document at the point they arise; collected here for
convenience before this spec is handed to epic/story creation:

1. **Performance tab lookback windows** (§B0/§B3) — which of 1H/4H/1D/1W/1M/
   YTD/1Y are actually wanted, and whether `metrics_store`'s `retain_days=31`
   needs extending to support the longer ones.
2. **Top-toolbar symbol/timeframe reconciliation** (§A8.1) — confirm the
   recommended resolution (read-only symbol label + back-to-Rankings link,
   real timeframe selector if wanted, no theme toggle) before building it.
3. **Rankings → History link** (§B0) — exact UI affordance (icon, button,
   whole-row secondary action) for reaching `/history/:iid` from the
   Performance tab.
4. ~~TanStack Table vs. hand-rendered~~ — decided: hand-rendered, no grid
   framework (§B5).
5. **`venue` field rollout order** (Part D) — schema/API first, then
   collectors, or collectors first with the frontend catching up; both are
   valid, pick one before story-splitting.
6. **Move-to-pane merge action** (§A4.2) — not built today; confirm whether
   it's in scope for the upcoming indicator work or deferred further.
7. **FRVP scroll-back ceiling** (§A7.5) — verify `routes/candles.py`'s
   `_MAX_CANDLES_LIMIT`/`_MAX_QUERY_SPAN_SECONDS` don't cut off the range a
   real Fixed Range Volume Profile selection needs; raise them if so.
8. ~~Alerts (§A6) priority~~ — decided: dead last, its own final epic, after
   chart/screener/multi-exchange are all done.
9. ~~Indicator catalog scope~~ (§A4.3/§B2.4) — decided: reuse the existing
   37-entry catalog everywhere, no curated MVP subset.
