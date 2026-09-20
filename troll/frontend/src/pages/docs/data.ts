// Ported verbatim (content-wise) from troll/ml_signals/docs_page.py's CADENCE_META/IND_GROUPS/
// INDICATORS/KB_GROUPS/KB data, as read 2026-09-13 (branch bmad). This is Story 15.1's Docs-page
// content port -- see that story's Dev Notes/Completion Notes for the section-by-section checklist.
// Fields containing markup (tagline, notes, gdesc, KB html) are trusted, self-authored HTML strings
// rendered via TrustedHtml -- never user input.

export type Cadence = "live" | "rolling" | "slow" | "static";

export const CADENCE_META: Record<Cadence, { label: string; sub: string }> = {
  live: { label: "Live", sub: "updates every ~1s snapshot" },
  rolling: { label: "Rolling", sub: "updates on a sliding window of recent ticks" },
  slow: { label: "Slow", sub: "refreshed on a fixed 60s+ cycle" },
  static: { label: "Chart-only", sub: "computed on demand, not part of the live cadence" },
};

export interface IndicatorGroup {
  id: string;
  name: string;
  desc: string;
}

export const IND_GROUPS: IndicatorGroup[] = [
  {
    id: "book",
    name: "Live Book State",
    desc: "Pure functions of the current 1-second DydxSecondSnapshot — no memory of prior ticks. Shown on both the ranking table and the coin-detail page unless noted.",
  },
  {
    id: "flow",
    name: "Order Flow",
    desc: "Derived from a rolling window of recent snapshots or tick-to-tick book changes. ranking_engine is the sole live computer of every one of these — the dashboard and bot_tui only ever read its output (SSOT-02).",
  },
  {
    id: "vol",
    name: "Volatility — three distinct measures, by design",
    desc: "This system deliberately runs three separate volatility computations with different windows and purposes. None is a scaled copy of another — conflating them is the single most common source of “why does Vol say something different here” confusion.",
  },
  {
    id: "market",
    name: "Market",
    desc: "Price-history fields refreshed on the same slow cycle as catalog volatility, plus 24h USD volume polled independently from the venue.",
  },
  {
    id: "chart",
    name: "Chart & Strategy Only",
    desc: "Computed at read-time for a specific view (the dashboard's per-coin chart, a backtest strategy) — never published to rankings:live, never shown on the ranking table.",
  },
];

export interface Indicator {
  id: string;
  group: string;
  name: string;
  cadence: Cadence;
  window: string;
  owner: string;
  shownIn: string[];
  tagline: string;
  formula: string | null;
  notes: string[];
  refs: string[];
  related: string[];
}

export const INDICATORS: Indicator[] = [
  {
    id: "microprice", group: "book", name: "Microprice", cadence: "live", window: "per snapshot (≈ 1s)",
    owner: "stateless — pure function, no shared-owner concern",
    shownIn: ["Coin detail (web + bot_tui)", "/data/live/{id} JSON"],
    tagline: "Size-weighted mid price, pulled toward whichever side of the book is thinner.",
    formula: "microprice = (bid_price×ask_size + ask_price×bid_size) / (bid_size + ask_size)",
    notes: [
      "Uses level-0 (best bid/ask) price and size only. When one side has more resting size than the other, microprice sits closer to the <em>thinner</em> side — the side more likely to move first — making it a better short-horizon fair-value estimate than the plain mid.",
      "Two implementations exist on purpose: a stateless function (<code>indicators.py:409</code>) for one-off reads, and a stateful <code>Microprice</code> <code>Indicator</code> class (<code>indicators.py:116</code>) with <code>.initialized</code> semantics for streaming contexts (chart replay, live_paper). Same formula, different call shape — never a second, independently-written formula.",
      'Is <b>not</b> a ranking-table column in either UI — only its derivative, <a data-nav="i:microprice_lean">Microprice Lean</a>, appears there indirectly (moved to history-only, see that page). Raw microprice is coin-detail only.',
    ],
    refs: ["ml_signals/indicators.py:116 (Microprice class)", "ml_signals/indicators.py:409 (microprice() function)"],
    related: ["microprice_lean", "spread", "mid_price"],
  },
  {
    id: "microprice_lean", group: "book", name: "Microprice Lean (“u lean”)", cadence: "live", window: "per snapshot (≈ 1s)",
    owner: "ranking_engine (sole live computer)",
    shownIn: ["Coin detail (web + bot_tui)", "31-day history chart (/history/{id})"],
    tagline: "How far the size-weighted fair value has drifted from the plain mid — a directional book-tilt signal.",
    formula: "microprice_lean = microprice − mid_price",
    notes: [
      "Positive = book pressure tilts the fair value above the plain mid (bid side thinner — more supportive of price rising); negative = the opposite.",
      '<span class="callout">Moved off the cross-instrument ranking table on 2026-09-13.</span> Before that date it sat on the ranking table as a bare column labeled “u lean” next to a dozen other columns — useful as a per-coin signal, but not something you scan across instruments to rank them, and it crowded the table. It now lives only on the single-coin page (web <code>/coin/{id}</code> and bot_tui\'s coin detail), plus the 31-day history chart, via <code>ranking_columns.py</code>\'s <code>_HISTORY_ONLY_COLS</code> — the same mechanism already used to keep <code>rank</code> off the live table.',
      "Displayed in basis points client-side (<code>bpsFromPriceUnits(raw, price)</code>) — the value stored/published is a raw price-unit delta, not already scaled to bps. A script reading <code>/api/rankings</code> or <code>/data/live/{id}</code> directly must do that scaling itself.",
    ],
    refs: ["ranking_engine/engine.py:364 (fast metrics)", "ml_signals/ranking_columns.py (_HISTORY_ONLY_COLS)", "ml_signals/dashboard.py (IND_GROUPS “Live (1s book state)”)"],
    related: ["microprice", "mid_price"],
  },
  {
    id: "spread", group: "book", name: "Spread", cadence: "live", window: "per snapshot (≈ 1s)",
    owner: "stateless — pure function",
    shownIn: ["Ranking table (web + bot_tui)", "Coin detail"],
    tagline: "Best ask minus best bid, in raw price units.",
    formula: "spread = ask_prices[0] − bid_prices[0]",
    notes: [
      "None when either side of the book is empty (thin/no book) — never fabricated as zero.",
      "Displayed in basis points client-side on both the ranking table and coin detail (<code>bpsFromPriceUnits</code>), same normalization as Microprice Lean.",
    ],
    refs: ["ml_signals/indicators.py:431"],
    related: ["microprice", "mid_price"],
  },
  {
    id: "mid_price", group: "book", name: "Mid Price (“Price”)", cadence: "live", window: "per snapshot (≈ 1s)",
    owner: "stateless — pure function",
    shownIn: ["Ranking table (web + bot_tui)", "Coin detail"],
    tagline: "Plain average of best bid and best ask — the “Price” column shown everywhere.",
    formula: "mid_price = (bid_prices[0] + ask_prices[0]) / 2",
    notes: [
      "This is <em>not</em> microprice — no size weighting. It's the baseline every normalized bps/USD figure on the tables (spread, lean, CVD, volume delta) is scaled against.",
    ],
    refs: ["ml_signals/indicators.py:440"],
    related: ["microprice", "spread"],
  },
  {
    id: "obi", group: "book", name: "Order Book Imbalance (OBI 3 / 5 / 10)", cadence: "live", window: "per snapshot (≈ 1s)",
    owner: "ranking_engine — 3 live MultiLevelOBI instances per instrument (levels 3, 5, 10)",
    shownIn: ["Ranking table (web + bot_tui)", "Coin detail"],
    tagline: "Fraction of resting size on the bid side, across the top N price levels.",
    formula: "OBI(N) = sum(bid_sizes[:N]) / (sum(bid_sizes[:N]) + sum(ask_sizes[:N]))",
    notes: [
      "Range 0–1: <b>1.0</b> = all visible depth on the bid, <b>0.5</b> = balanced, <b>0.0</b> = all ask. The ranking table colors it green above 0.5, red below.",
      "Three separate instances run per instrument — <code>obi_3</code>/<code>obi_5</code>/<code>obi_10</code> — not one computation resliced three ways. A thin top level can disagree sharply with the 10-level view during a large resting order a few ticks back.",
    ],
    refs: ["ml_signals/indicators.py:234 (MultiLevelOBI)", "ranking_engine/engine.py:348"],
    related: ["ofi_raw", "ofi_z"],
  },
  {
    id: "ofi_raw", group: "flow", name: "Order Flow Imbalance — raw (OFI 3 / 5 / 10)", cadence: "rolling", window: "rolling sum over the last 300 updates",
    owner: "ranking_engine — 3 live MultiLevelOFI instances per instrument (levels 3, 5, 10; window=300)",
    shownIn: ["Ranking table (web + bot_tui, informational only)", "Coin detail"],
    tagline: "Cont–Kukanov–Stoikov order flow imbalance, summed across the top N price levels.",
    formula: "at each level: price improves → count full new size\nprice unchanged → count the size delta\nprice worsens → count a full withdrawal (negative)\ncontribution = bid_term − ask_term, summed across levels and over the rolling window",
    notes: [
      "Positive = net buy-side pressure at the top of book over the window; negative = net sell-side pressure.",
      'Informational column only — <b>OFI never affects an instrument\'s rank.</b> The ranking table\'s sort key is exclusively 24h volume or the cross-sectional <a data-nav="i:vol_score">Volatility Score</a>, depending on Ranking Mode.',
      'The engine also runs a fourth, differently-configured OFI instance for the z-scored variant — see <a data-nav="i:ofi_z">OFI10z</a>, not a rescaling of this one.',
    ],
    refs: ["ml_signals/indicators.py:269 (MultiLevelOFI)", "ranking_engine/engine.py:346"],
    related: ["ofi_z", "obi", "cvd"],
  },
  {
    id: "ofi_z", group: "flow", name: "OFI10z (z-scored)", cadence: "rolling", window: "window=50, z-scored over the last 3600 readings",
    owner: "ranking_engine — one MultiLevelOFI(levels=10, window=50, zscore_window=3600) instance",
    shownIn: ["Ranking table (web + bot_tui, leading column, informational only)", "Coin detail"],
    tagline: "Level-10 OFI, standardized against its own recent history so it's comparable across instruments of any scale.",
    formula: "z = (raw_ofi − mean(last 3600 raw readings)) / std(last 3600 raw readings)\n(returns 0.0 when std is 0)",
    notes: [
      'A genuinely separate indicator instance from <a data-nav="i:ofi_raw">ofi_10</a> — different <code>window</code> (50 vs 300) as well as the z-score wrapper. Don\'t expect ofi_10 and ofi_10_z to move in lockstep.',
      "Colored green above 0 / red below — the sign is what most viewers actually scan for, the magnitude tells you how unusual the current imbalance is versus the last hour or so of readings.",
    ],
    refs: ["ranking_engine/engine.py:345"],
    related: ["ofi_raw", "obi"],
  },
  {
    id: "cvd", group: "flow", name: "CVD (Cumulative Volume Delta)", cadence: "rolling", window: "rolling 300-snapshot buffer",
    owner: "ranking_engine — fed from the shared _SECOND_ROLLING buffer",
    shownIn: ["Ranking table (web + bot_tui)", "Coin detail"],
    tagline: "Total buy volume minus total sell volume across the engine's rolling 300-snapshot window.",
    formula: "cvd = sum(buy_volume for last 300 snapshots) − sum(sell_volume for last 300 snapshots)",
    notes: [
      "Raw base-token units server-side; normalized to USD client-side via <code>usdFromTokens(raw, price)</code> on both the ranking table and coin detail.",
      'Shares its rolling window with <a data-nav="i:volume_counts">buy/sell count and avg trade size</a> — all four come from the same <code>trade_aggregates()</code> reduction over the same 300-entry buffer.',
    ],
    refs: ["ml_signals/indicators.py:454 (trade_aggregates)", "ranking_engine/engine.py:349"],
    related: ["volume_delta", "volume_counts"],
  },
  {
    id: "volume_delta", group: "flow", name: "Volume Delta (“Vol d 60s”)", cadence: "live", window: "single latest snapshot only — not a rolling sum",
    owner: "stateless — pure function of the latest snapshot",
    shownIn: ["Ranking table (web + bot_tui)", "Coin detail"],
    tagline: "Buy minus sell volume for the single most recent 1-second snapshot — despite sitting next to CVD, this one is not a rolling figure.",
    formula: "volume_delta = latest_snapshot.buy_volume − latest_snapshot.sell_volume",
    notes: [
      '<span class="callout">Label note:</span> the ranking table\'s column header reads “Vol d 60s” — that “60s” describes the table\'s own poll/refresh interval, <em>not</em> the underlying window. The value itself is always exactly one snapshot tick (≈ 1 second), the fastest-updating field on the whole table. Don\'t confuse this with <a data-nav="i:cvd">CVD</a>, which genuinely does sum over 300 snapshots.',
      "Raw base-token units; normalized to USD client-side, same as CVD.",
    ],
    refs: ["ml_signals/indicators.py:449"],
    related: ["cvd"],
  },
  {
    id: "volume_counts", group: "flow", name: "Buy / Sell Count & Avg Trade Size", cadence: "rolling", window: "rolling 300-snapshot buffer (same as CVD)",
    owner: "ranking_engine",
    shownIn: ["Coin detail only"],
    tagline: "Trade counts per side, and mean trade size, over the same rolling window CVD uses.",
    formula: "avg_trade_size = (buy_volume + sell_volume) / (buy_count + sell_count), over the last 300 snapshots",
    notes: ["Not shown on the ranking table in either UI — coin-detail only, in the “order flow (~5m rolling)” group."],
    refs: ["ml_signals/indicators.py:454 (trade_aggregates)"],
    related: ["cvd"],
  },
  {
    id: "vol_fast", group: "vol", name: "Volatility — Fast", cadence: "rolling", window: "last 300 live ticks (≈ 5 minutes)",
    owner: "ranking_engine, per instrument",
    shownIn: ["Coin detail only (“Volatility & Market” group)"],
    tagline: "stdev of mid-price returns over the last 300 live snapshots — the fastest-reacting of the app's three volatility numbers.",
    formula: "volatility_fast = statistics.stdev(pct_returns) over the trailing 300 live-tick mid prices",
    notes: ['Distinct from both <a data-nav="i:vol_catalog">Vol(catalog)</a> and <a data-nav="i:vol_score">Vol Score</a> by explicit design — three separate volatility computations exist in this codebase on purpose, not by accident. See the group note above.'],
    refs: ["ranking_engine/engine.py:352–359"],
    related: ["vol_catalog", "vol_score"],
  },
  {
    id: "vol_catalog", group: "vol", name: "Volatility — Catalog (“Vol(catalog)”)", cadence: "slow", window: "60s refresh; window grows from 0 up to a 25-hour cap",
    owner: "ranking_engine.PriceSeriesStore → ml_signals.catalog_stats.price_stats_from_series(), called every 60s by _slow_loop_task",
    shownIn: ["Ranking table (web + bot_tui)", "Coin detail", "31-day history chart"],
    tagline: "stdev of consecutive per-second return percentages, over an in-memory price series backfilled from the Parquet catalog and retained up to 25 hours.",
    formula: "returns = diff(close_prices) / close_prices[:-1]\nvolatility = stdev(returns)   — over whatever history is retained (0–25h)",
    notes: [
      '<span class="callout">This is the column that used to be genuinely ambiguous.</span> Before 2026-09-13 the ranking table\'s header for this exact field was a bare <code>“Vol”</code> — one glance away from <code>“Vol Score”</code> and <code>“Vol24h”</code> on the very same row, with no indication of which of the app\'s three volatility numbers it was or how far back it looked. Relabeled to <b>Vol(catalog)</b> everywhere — ranking table, and matching the label the coin-detail page (both web and bot_tui) already used for this same field, so the name is now identical wherever it appears.',
      "The 25-hour figure is <code>PRICE_LOOKBACK_HOURS</code> (<code>ml_signals/metrics_computer.py</code>) — a memory/backfill cap, not a fixed calendar window like “1h” or “24h”. Right after a fresh start (or for a newly-added instrument) the window is whatever history has accumulated so far, growing toward the 25h cap over time.",
      "Also the field persisted to <code>metrics.db</code>'s <code>volatility</code> column, powering the 31-day per-coin history chart.",
    ],
    refs: ["ml_signals/catalog_stats.py:222 (price_stats_from_series)", "ranking_engine/price_series.py:164 (PriceSeriesStore.stats)", "ml_signals/metrics_computer.py:38 (PRICE_LOOKBACK_HOURS = 25.0)"],
    related: ["vol_fast", "vol_score"],
  },
  {
    id: "vol_score", group: "vol", name: "Volatility Score", cadence: "slow", window: "age-based rolling window, default 3600s (1h)",
    owner: "ranking_engine.VolatilityTracker (volatility.py) — a fourth, deliberately separate volatility computation",
    shownIn: ["Ranking table (web + bot_tui)", "Coin detail"],
    tagline: "Cross-sectional stdev of consecutive mid-price % returns, ranked against every other subscribed instrument. The sort key when Ranking Mode = “volatility.”",
    formula: "per instrument: stdev of mid-price % returns over an age-based window (default 3600s)\n→ rows sorted by volatility_score descending when mode = “volatility”",
    notes: [
      "“Age-based” (not a fixed-length deque) specifically so the effective time span stays ≈ constant even if the live snapshot rate varies — a fixed-length buffer would silently shrink its real time coverage under a faster tick rate.",
      "Lookback is overridable via the <code>RANKING_VOLATILITY_LOOKBACK_SECONDS</code> environment variable without a code change.",
      "Always computed and published regardless of the active Ranking Mode — it's the sort key only when mode is “volatility”; under the default “volume” mode it's purely informational, same as OFI/OBI.",
    ],
    refs: ["ranking_engine/volatility.py:16–24", "ranking_engine/engine.py (RANKING_VOLATILITY_LOOKBACK_SECONDS)"],
    related: ["vol_fast", "vol_catalog", "volume24h"],
  },
  {
    id: "pct_change", group: "market", name: "pct_1h / pct_24h (+ pct_1w / pct_1m from metrics_store)", cadence: "slow", window: "60s refresh, read from the same 25h price series as Vol(catalog)",
    owner: "ml_signals.catalog_stats.price_stats_from_series()",
    shownIn: ["Ranking table (web + bot_tui)", "Coin detail"],
    tagline: "Percent change in mid price over the trailing 1h / 24h.",
    formula: "pct_change(H) = (latest_price − price_at(now − H)) / price_at(now − H) × 100",
    notes: ["<code>None</code> — not zero, not extrapolated — until the retained price series genuinely spans that long. A freshly-added instrument shows <code>pct_24h = None</code> for up to 24 hours, by design (DATA-01: never fabricate a value that isn't really known yet)."],
    refs: ["ml_signals/catalog_stats.py:222"],
    related: ["vol_catalog", "volume24h"],
  },
  {
    id: "volume24h", group: "market", name: "Volume24h (“Vol24h”)", cadence: "slow", window: "independent 60s poll",
    owner: "ranking_engine — direct dYdX indexer poll, deliberately not reused from the collector's own liquidity poll (AD-4: no cross-module network-I/O reuse)",
    shownIn: ["Ranking table (web + bot_tui)", "Coin detail"],
    tagline: "24-hour USD volume straight from the venue. The sort key when Ranking Mode = “volume” — the default.",
    formula: "polled every 60s from dYdX indexer's /v4/perpetualMarkets, field volume24H (already USD)",
    notes: ["This is the actual default ranking order for the whole system: with Ranking Mode left on “volume,” every OFI/OBI/CVD/microprice column on the table is informational only — none of them move an instrument's position in the list."],
    refs: ["ranking_engine/engine.py:152–167 (_fetch_volume_24h_json)"],
    related: ["vol_score"],
  },
  {
    id: "book_features", group: "chart", name: "Book Microstructure (depth, imbalance, liquidity distance, cancel pressure)", cadence: "static", window: "computed on demand from a live-replayed OrderBook",
    owner: "ml_signals/book_features.py — chart_data.py's read-time replay",
    shownIn: ["Web dashboard per-coin chart page only"],
    tagline: "A second, independent set of L2-derived features, built by replaying raw order-book deltas — not the stored DydxSecondSnapshot fields OBI/OFI use.",
    formula: null,
    notes: [
      "<b>depth_profile</b> — top-N bid/ask prices+sizes from a live <code>OrderBook</code> object (levels 1–10 default).",
      "<b>book_imbalance</b> — the same bid/(bid+ask) formula as OBI, per level plus an aggregate, but computed from a live replayed book object instead of the stored snapshot lists.",
      "<b>liquidity_distance</b> — price distance from best to where cumulative depth reaches a threshold (default 80%) of one side's total. Small = dense support/resistance nearby; large = a liquidity vacuum.",
      "<b>cancel_pressure</b> (<code>CancellationTracker</code>) — <code>(deleted_size − added_size) / (deleted_size + added_size)</code> at the best bid/ask over a rolling 200-event window. +1 = all cancellations, −1 = all additions. Only tracks ADD/DELETE at the <em>current</em> best price — UPDATE is ambiguous-direction and skipped.",
      "None of these reach <code>rankings:live</code> or either ranking table — chart page only.",
    ],
    refs: ["ml_signals/book_features.py", "ml_signals/chart_data.py (compute_chart_series)"],
    related: ["footprint", "obi"],
  },
  {
    id: "footprint", group: "chart", name: "Footprint (resting order-book flow)", cadence: "static", window: "per-candle, per-price-band buckets",
    owner: "ml_signals/footprint.py",
    shownIn: ["Web dashboard footprint chart only"],
    tagline: "Buckets resting order-book size changes — not executed trades — into per-candle, per-price-band cells.",
    formula: null,
    notes: [
      "dYdX's L2 deltas carry no order IDs, so a shrinking price level can't be told apart from a cancel vs. a fill — this is resting-size flow, explicitly not a trade footprint, by the module's own documented caveat.",
      "Each cell tracks gross <code>bid_added</code> / <code>bid_removed</code> / <code>ask_added</code> / <code>ask_removed</code> size (not just the net), so a churning level stays visible instead of netting to zero.",
      "No ranking/live-tick consumer — dashboard footprint chart only.",
    ],
    refs: ["ml_signals/footprint.py:16–29"],
    related: ["book_features"],
  },
  {
    id: "ema_trend", group: "chart", name: "EMA Trend Lines (fast/slow)", cadence: "static", window: "computed over internally-aggregated 1-minute candles",
    owner: "ml_signals/chart_data.py, using Nautilus's own ExponentialMovingAverage indicator",
    shownIn: ["Web dashboard per-coin chart page only"],
    tagline: "EMA-8 / EMA-21 crossover lines on 1-minute candles — reuses nautilus_trader's built-in indicator, not a custom EMA.",
    formula: null,
    notes: ["Entirely a read-time chart-page replay — nothing here is persisted or fed into ranking. Consistent with the project rule to reach for a Nautilus built-in (<code>nautilus_trader.indicators</code>) before writing a custom one."],
    refs: ["ml_signals/chart_data.py (compute_chart_series)"],
    related: ["book_features"],
  },
  {
    id: "logistic_trend", group: "chart", name: "OnlineLogisticTrend", cadence: "static", window: "online, one SGD step per bar",
    owner: "strategy-only — fed manually by a Strategy subclass",
    shownIn: ["Backtest / live_paper strategies only (example_strategy.py)"],
    tagline: "Online (incremental) logistic regression predicting P(next bar's return > 0) from the last N bar-to-bar returns.",
    formula: "z = weights · features + bias\nP(up) = 1 / (1 + e⁻ᶻ)\neach new bar: one SGD step trains on the PREVIOUS prediction now that the true outcome is known, then predicts the next probability",
    notes: [
      "<code>self.value</code> is that probability, 0.5 until <code>initialized</code> (i.e. until <code>lookback</code> returns have accumulated).",
      "Deliberately minimal: a single online SGD step per bar, no batch retraining, no persistence across restarts. Documented in-code as a known limit with a named upgrade path (<code>sklearn.linear_model.SGDClassifier</code> + periodic refit) if it drifts on a long-running deployment.",
      "Never published to <code>rankings:live</code> — this is a strategy-internal signal, not a UI metric.",
    ],
    refs: ["ml_signals/indicators.py:41–113"],
    related: [],
  },
];

export interface KbGroup {
  id: string;
  name: string;
}

export const KB_GROUPS: KbGroup[] = [
  { id: "start", name: "Start Here" },
  { id: "ops", name: "Setup & Operations" },
  { id: "data", name: "Data & Storage" },
  { id: "backtest", name: "Backtesting & Strategies" },
  { id: "postmortem", name: "Postmortems" },
  { id: "fixed", name: "Fixed & Resolved" },
];

export type KbStatus = "open" | "deferred" | "done";

export interface KbDoc {
  id: string;
  group: string;
  name: string;
  tagline: string;
  status?: KbStatus;
  html: string;
  refs: string[];
}
