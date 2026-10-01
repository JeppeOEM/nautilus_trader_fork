// Ported verbatim (content-wise) from the retired aiohttp dashboard's docs_page.py's
// CADENCE_META/IND_GROUPS/INDICATORS/KB_GROUPS/KB data, as read 2026-09-13 (branch bmad). This is Story 15.1's Docs-page
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
    desc: "Derived from a rolling window of recent snapshots or tick-to-tick book changes. ranking_engine is the sole live computer of every one of these — the dashboard only ever reads its output (SSOT-02).",
  },
  {
    id: "vol",
    name: "Volatility — three distinct measures, by design",
    desc: "This system deliberately runs three separate volatility computations with different windows and purposes. None is a scaled copy of another — conflating them is the single most common source of “why does Vol say something different here” confusion.",
  },
  {
    id: "market",
    name: "Market",
    desc: "Price-history fields refreshed on the same slow cycle as the 24h trade-close volatility, plus 24h USD volume polled independently from the venue.",
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
    shownIn: ["Coin detail (web)", "/data/live/{id} JSON"],
    tagline: "Size-weighted mid price, pulled toward whichever side of the book is thinner.",
    formula: "microprice = (bid_price×ask_size + ask_price×bid_size) / (bid_size + ask_size)",
    notes: [
      "Uses level-0 (best bid/ask) price and size only. When one side has more resting size than the other, microprice sits closer to the <em>thinner</em> side — the side more likely to move first — making it a better short-horizon fair-value estimate than the plain mid.",
      "Two implementations exist on purpose: a stateless function (<code>indicators.py:491</code>) for one-off reads, and a stateful <code>Microprice</code> <code>Indicator</code> class (<code>indicators.py:135</code>) with <code>.initialized</code> semantics for streaming contexts (chart replay, the live bots). Same formula, different call shape — never a second, independently-written formula.",
      'Is <b>not</b> a ranking-table column in either UI — only its derivative, <a data-nav="i:microprice_lean">Microprice Lean</a>, appears there indirectly (moved to history-only, see that page). Raw microprice is coin-detail only.',
    ],
    refs: ["kernel/indicators.py:135 (Microprice class)", "kernel/indicators.py:491 (microprice() function)"],
    related: ["microprice_lean", "spread", "mid_price"],
  },
  {
    id: "microprice_lean", group: "book", name: "Microprice Lean (“u lean”)", cadence: "live", window: "per snapshot (≈ 1s)",
    owner: "ranking_engine (sole live computer)",
    shownIn: ["Coin detail (web)", "31-day history chart (/history/{id})"],
    tagline: "How far the size-weighted fair value has drifted from the plain mid — a directional book-tilt signal.",
    formula: "microprice_lean = microprice − mid_price",
    notes: [
      "Positive = book pressure tilts the fair value above the plain mid (bid side thinner — more supportive of price rising); negative = the opposite.",
      '<span class="callout">Moved off the cross-instrument ranking table on 2026-09-13.</span> Before that date it sat on the ranking table as a bare column labeled “u lean” next to a dozen other columns — useful as a per-coin signal, but not something you scan across instruments to rank them, and it crowded the table. It now lives only on the single-coin page (web <code>/coin/{id}</code>; bot_tui\'s coin detail also showed it until rankings went web-only on 2026-09-26), plus the 31-day history chart, via <code>views/ranking_columns.py</code>\'s <code>_HISTORY_ONLY_COLS</code> — the same mechanism already used to keep <code>rank</code> off the live table.',
      "Raw price units (quote currency per base token), stored, published and displayed as is — nothing converts it to bps anywhere. To compare instruments, divide by the row's own <code>price</code> (the mid) and multiply by 10 000 yourself.",
    ],
    refs: ["ranking/domain/board.py (InstrumentMetrics.fast_metrics)", "views/ranking_columns.py (_HISTORY_ONLY_COLS)"],
    related: ["microprice", "mid_price"],
  },
  {
    id: "spread", group: "book", name: "Spread", cadence: "live", window: "per snapshot (≈ 1s)",
    owner: "stateless — pure function",
    shownIn: ["Ranking table (web)", "Coin detail"],
    tagline: "Best ask minus best bid, in raw price units.",
    formula: "spread = ask_prices[0] − bid_prices[0]",
    notes: [
      "None when either side of the book is empty (thin/no book) — never fabricated as zero.",
      "Raw price units, shown as is on the ranking table and coin detail — not converted to bps (same as Microprice Lean).",
    ],
    refs: ["kernel/indicators.py:514"],
    related: ["microprice", "mid_price"],
  },
  {
    id: "mid_price", group: "book", name: "Mid Price (“Price”)", cadence: "live", window: "per snapshot (≈ 1s)",
    owner: "stateless — pure function",
    shownIn: ["Ranking table (web)", "Coin detail"],
    tagline: "Plain average of best bid and best ask — the “Price” column shown everywhere.",
    formula: "mid_price = (bid_prices[0] + ask_prices[0]) / 2",
    notes: [
      "This is <em>not</em> microprice — no size weighting. It is the live mid or <code>None</code>: with no two-sided book yet the ranking row shows “—”, never the last trade close (Story 31.3). Spread, lean, CVD and volume delta are published in raw units; divide by this price yourself to compare instruments.",
    ],
    refs: ["kernel/indicators.py:535"],
    related: ["microprice", "spread"],
  },
  {
    id: "obi", group: "book", name: "Order Book Imbalance (OBI 3 / 5 / 10)", cadence: "live", window: "per snapshot (≈ 1s)",
    owner: "ranking_engine — 3 live MultiLevelOBI instances per instrument (levels 3, 5, 10)",
    shownIn: ["Ranking table (web)", "Coin detail"],
    tagline: "Fraction of resting size on the bid side, across the top N price levels.",
    formula: "OBI(N) = sum(bid_sizes[:N]) / (sum(bid_sizes[:N]) + sum(ask_sizes[:N]))",
    notes: [
      "Range 0–1: <b>1.0</b> = all visible depth on the bid, <b>0.5</b> = balanced, <b>0.0</b> = all ask. The ranking table colors it green above 0.5, red below.",
      "Three separate instances run per instrument — <code>obi_3</code>/<code>obi_5</code>/<code>obi_10</code> — not one computation resliced three ways. A thin top level can disagree sharply with the 10-level view during a large resting order a few ticks back.",
    ],
    refs: ["kernel/indicators.py:309 (MultiLevelOBI)", "ranking/domain/board.py (InstrumentMetrics.feed_book)"],
    related: ["ofi_raw", "ofi_z"],
  },
  {
    id: "ofi_raw", group: "flow", name: "Order Flow Imbalance — raw (OFI 3 / 5 / 10)", cadence: "rolling", window: "rolling sum over the last 300 updates",
    owner: "ranking_engine — 3 live MultiLevelOFI instances per instrument (levels 3, 5, 10; window=300)",
    shownIn: ["Ranking table (web, informational only)", "Coin detail"],
    tagline: "Cont–Kukanov–Stoikov order flow imbalance, summed across the top N price levels.",
    formula: "at each level: price improves → count full new size\nprice unchanged → count the size delta\nprice worsens → count a full withdrawal (negative)\ncontribution = bid_term − ask_term, summed across levels and over the rolling window",
    notes: [
      "Positive = net buy-side pressure at the top of book over the window; negative = net sell-side pressure.",
      'Informational column only — <b>OFI never affects an instrument\'s rank.</b> The ranking table\'s sort key is exclusively 24h volume or the cross-sectional <a data-nav="i:vol_score">Volatility Score</a>, depending on Ranking Mode.',
      'The engine also runs a fourth, differently-configured OFI instance for the z-scored variant — see <a data-nav="i:ofi_z">OFI10z</a>, not a rescaling of this one.',
    ],
    refs: ["kernel/indicators.py:347 (MultiLevelOFI)", "ranking/domain/board.py (InstrumentMetrics.feed_book)"],
    related: ["ofi_z", "obi", "cvd"],
  },
  {
    id: "ofi_z", group: "flow", name: "OFI10z (z-scored)", cadence: "rolling", window: "window=50, z-scored over the last 3600 readings",
    owner: "ranking_engine — one MultiLevelOFI(levels=10, window=50, zscore_window=3600) instance",
    shownIn: ["Ranking table (web, leading column, informational only)", "Coin detail"],
    tagline: "Level-10 OFI, standardized against its own recent history so it's comparable across instruments of any scale.",
    formula: "z = (raw_ofi − mean(last 3600 raw readings)) / std(last 3600 raw readings)\n(returns 0.0 when std is 0)",
    notes: [
      'A genuinely separate indicator instance from <a data-nav="i:ofi_raw">ofi_10</a> — different <code>window</code> (50 vs 300) as well as the z-score wrapper. Don\'t expect ofi_10 and ofi_10_z to move in lockstep.',
      "Colored green above 0 / red below — the sign is what most viewers actually scan for, the magnitude tells you how unusual the current imbalance is versus the last hour or so of readings.",
    ],
    refs: ["ranking/domain/board.py (InstrumentMetrics.feed_book)"],
    related: ["ofi_raw", "obi"],
  },
  {
    id: "cvd", group: "flow", name: "CVD (Cumulative Volume Delta)", cadence: "rolling", window: "rolling 300-snapshot buffer",
    owner: "ranking_engine — fed from the shared _SECOND_ROLLING buffer",
    shownIn: ["Ranking table (web)", "Coin detail"],
    tagline: "Total buy volume minus total sell volume across the engine's rolling 300-snapshot window.",
    formula: "cvd = sum(buy_volume for last 300 snapshots) − sum(sell_volume for last 300 snapshots)",
    notes: [
      "Raw base-token units (buy minus sell size), shown as is on the ranking table and coin detail — nothing converts it to USD. <code>None</code> (“—”) while the rolling window holds no snapshot yet, never a 0.",
      'Shares its rolling window with <a data-nav="i:volume_counts">buy/sell count and avg trade size</a> — all four come from the same <code>trade_aggregates()</code> reduction over the same 300-entry buffer.',
    ],
    refs: ["kernel/indicators.py:549 (trade_aggregates)", "ranking/domain/board.py (InstrumentMetrics.fast_metrics)"],
    related: ["volume_delta", "volume_counts"],
  },
  {
    id: "volume_delta", group: "flow", name: "Volume Delta (“Vol d 60s”)", cadence: "rolling", window: "rolling 60-snapshot window (≈ 1 minute)",
    owner: "ranking_engine — the last 60 entries of the same rolling buffer CVD uses",
    shownIn: ["Ranking table (web)", "Coin detail"],
    tagline: "Buy minus sell volume over the last 60 snapshots — a one-minute flow, next to CVD's five.",
    formula: "volume_delta = sum(buy_volume − sell_volume) over the last 60 snapshots",
    notes: [
      'The “60s” in the header is the window: 60 one-second snapshots (<code>VOLUME_DELTA_WINDOW</code>). A single-second delta almost never landed on a second with a trade, so it read +0.00 nearly everywhere. <a data-nav="i:cvd">CVD</a> sums the full 300-snapshot buffer.',
      "Raw base-token units, shown as is — nothing converts it to USD. <code>None</code> (“—”) while the buffer holds no snapshot.",
    ],
    refs: ["ranking/domain/board.py (InstrumentMetrics.fast_metrics, VOLUME_DELTA_WINDOW)"],
    related: ["cvd"],
  },
  {
    id: "volume_counts", group: "flow", name: "Buy / Sell Count & Avg Trade Size", cadence: "rolling", window: "rolling 300-snapshot buffer (same as CVD)",
    owner: "ranking_engine",
    shownIn: ["Coin detail only"],
    tagline: "Trade counts per side, and mean trade size, over the same rolling window CVD uses.",
    formula: "avg_trade_size = (buy_volume + sell_volume) / (buy_count + sell_count), over the last 300 snapshots",
    notes: ["Not shown on the ranking table in either UI — coin-detail only, in the “order flow (~5m rolling)” group."],
    refs: ["kernel/indicators.py:549 (trade_aggregates)"],
    related: ["cvd"],
  },
  {
    id: "vol_fast", group: "vol", name: "Volatility — Fast (volatility_fast)", cadence: "rolling", window: "last 300 live snapshots (≈ 5 minutes)",
    owner: "ranking_engine, per instrument",
    shownIn: ["Coin detail only (“Volatility & Market” group)"],
    tagline: "The third volatility: sample stdev of mid-price % returns over the last 300 live snapshots — the fastest-reacting of the three.",
    formula: "volatility_fast = stdev (ddof=1) of consecutive mid % returns, last 300 snapshots",
    notes: ['The third of three distinct measures, next to <a data-nav="i:vol_catalog">Vol 24h σ (trade closes)</a> and <a data-nav="i:vol_score">Vol 1h σ (mids)</a>: a different window and, unlike the 24h figure, mids rather than trade closes. Not a ranking-table column.'],
    refs: ["ranking/domain/board.py (_fast_volatility)"],
    related: ["vol_catalog", "vol_score"],
  },
  {
    id: "vol_catalog", group: "vol", name: "Volatility — 24h of trade closes (“Vol 24h σ (trade closes)”)", cadence: "slow", window: "60s refresh; the last 24 hours of trade closes",
    owner: "ranking.domain.price_series.PriceSeriesStore → ranking.domain.metrics.price_stats_from_series(), called every 60s by RankingEngine.slow_loop_once",
    shownIn: ["Ranking table (web)", "Coin detail", "31-day history chart"],
    tagline: "Population stdev of consecutive trade-close % returns over the last 24 hours, from an in-memory price series backfilled from the Parquet catalog.",
    formula: "returns = diff(close_prices) / close_prices[:-1], over the points within 24h of the latest\nvolatility = stdev (ddof=0) of returns",
    notes: [
      '<span class="callout">Relabeled twice.</span> A bare <code>“Vol”</code> until 2026-09-13, then <code>“Vol(catalog)”</code>, which still did not say what it measured. Since Story 31.3 every volatility label names its window and its input: this one is 24h of <b>trade closes</b>; <a data-nav="i:vol_score">Vol 1h σ (mids)</a> is an hour of mids.',
      "The series is retained 25 hours (<code>PRICE_LOOKBACK_HOURS</code>, a backfill margin); the volatility uses its last 24. Right after a fresh start the window is whatever trade history exists. Only trade closes enter it — a window without a trade is an empty series (the value is <code>None</code>), never mark prices.",
      "Also the field persisted to <code>metrics.db</code>'s <code>volatility</code> column, powering the 31-day per-coin history chart.",
    ],
    refs: ["ranking/domain/metrics.py (price_stats_from_series, VOLATILITY_WINDOW_NS)", "ranking/domain/price_series.py (PriceSeriesStore.stats, PRICE_LOOKBACK_HOURS = 25.0)"],
    related: ["vol_fast", "vol_score"],
  },
  {
    id: "vol_score", group: "vol", name: "Volatility — 1h of mids (“Vol 1h σ (mids)”)", cadence: "slow", window: "age-based rolling window, default 3600s (1h)",
    owner: "ranking.domain.volatility.VolatilityTracker — a deliberately separate volatility computation",
    shownIn: ["Ranking table (web)", "Coin detail"],
    tagline: "Sample stdev of consecutive mid-price % returns over the last hour. The sort key when Ranking Mode = “volatility.”",
    formula: "per instrument: stdev (ddof=1) of mid-price % returns over the snapshots of the last 3600s\n→ rows sorted by volatility_score descending when mode = “volatility”",
    notes: [
      "“Age-based” (not a fixed-length deque) specifically so the effective time span stays ≈ constant even if the live snapshot rate varies — a fixed-length buffer would silently shrink its real time coverage under a faster tick rate.",
      "Lookback is overridable via the <code>RANKING_VOLATILITY_LOOKBACK_SECONDS</code> environment variable without a code change.",
      "Always computed and published regardless of the active Ranking Mode — it's the sort key only when mode is “volatility”; under the default “volume” mode it's purely informational, same as OFI/OBI. A row with no score yet (under two returns) sorts last in volatility mode and shows “—”.",
    ],
    refs: ["ranking/domain/volatility.py", "ranking/__main__.py (RANKING_VOLATILITY_LOOKBACK_SECONDS)"],
    related: ["vol_fast", "vol_catalog", "volume24h"],
  },
  {
    id: "pct_change", group: "market", name: "pct_1h / pct_24h (+ pct_1w / pct_1m from metrics_store)", cadence: "slow", window: "60s refresh, read from the same 25h trade-close series as Vol 24h σ",
    owner: "ranking.domain.metrics.price_stats_from_series() (the only implementation in platform/)",
    shownIn: ["Ranking table (web)", "Coin detail"],
    tagline: "Percent change in the trade-close price over the trailing 1h / 24h.",
    formula: "pct_change(H) = (latest_close − base) / base × 100\nbase = the first close at or after latest_ts − H, at most 300s after it",
    notes: [
      "<code>None</code> — not zero, not extrapolated — until the retained price series genuinely spans that long. A freshly-added instrument shows <code>pct_24h = None</code> for up to 24 hours, by design (DATA-01: never fabricate a value that isn't really known yet).",
      "Also <code>None</code> when a gap in the series straddles the cutoff (the first close after it lies more than 300s past it): the change would otherwise cover a silently shorter horizon than its name.",
      "<code>pct_1w</code>/<code>pct_1m</code> compare with the price stored in <code>metrics.db</code> at or before 7/30 days ago, within one hour of that time — <code>None</code> otherwise.",
    ],
    refs: ["ranking/domain/metrics.py (PCT_MAX_SHORTFALL_NS)", "ranking/infrastructure/metrics_store.py (price_near_days_ago)"],
    related: ["vol_catalog", "volume24h"],
  },
  {
    id: "volume24h", group: "market", name: "Volume24h (“Vol24h”)", cadence: "slow", window: "independent 60s poll",
    owner: "ranking — one VolumeSource adapter per venue (dYdX, Bybit, Hyperliquid), every request built with kernel.venue_http",
    shownIn: ["Ranking table (web)", "Coin detail"],
    tagline: "24-hour USD volume straight from the venue. The sort key when Ranking Mode = “volume” — the default.",
    formula: "polled every 60s per venue, all already USD: dYdX indexer /v4/perpetualMarkets volume24H; Bybit /v5/market/tickers turnover24h (linear, and spot for USDT/USDC quotes only, stablecoin at par); Hyperliquid /info metaAndAssetCtxs dayNtlVlm",
    notes: ["A coin whose venue has no current volume for it is left out of volume mode (never ranked at 0) and shows “—” in volatility mode; each one is counted in the error bar under ranking_engine.volume24h.", "This is the actual default ranking order for the whole system: with Ranking Mode left on “volume,” every OFI/OBI/CVD/microprice column on the table is informational only — none of them move an instrument's position in the list."],
    refs: ["ranking/infrastructure/volume_{dydx,bybit,hyperliquid}.py (parse_volume_24h / parse_bybit_volume_24h / parse_hyperliquid_volume_24h)", "ranking/application/engine.py (RankingEngine.volume_cycle)"],
    related: ["vol_score"],
  },
  {
    id: "book_features", group: "chart", name: "Book Microstructure (depth, imbalance, liquidity distance, cancel pressure)", cadence: "static", window: "computed on demand from a live-replayed OrderBook",
    owner: "views/chart_series.py (book features + compute_chart_series' read-time replay; moved there in Story 24.2)",
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
    refs: ["views/chart_series.py (DepthProfile, book_imbalance, CancellationTracker)", "views/chart_series.py (compute_chart_series)"],
    related: ["footprint", "obi"],
  },
  {
    id: "footprint", group: "chart", name: "Footprint (resting order-book flow)", cadence: "static", window: "per-candle, per-price-band buckets",
    owner: "views/chart_series.py (build_footprint; moved there in Story 24.2)",
    shownIn: ["Web dashboard footprint chart only"],
    tagline: "Buckets resting order-book size changes — not executed trades — into per-candle, per-price-band cells.",
    formula: null,
    notes: [
      "dYdX's L2 deltas carry no order IDs, so a shrinking price level can't be told apart from a cancel vs. a fill — this is resting-size flow, explicitly not a trade footprint, by the module's own documented caveat.",
      "Each cell tracks gross <code>bid_added</code> / <code>bid_removed</code> / <code>ask_added</code> / <code>ask_removed</code> size (not just the net), so a churning level stays visible instead of netting to zero.",
      "No ranking/live-tick consumer — dashboard footprint chart only.",
    ],
    refs: ["views/chart_series.py (Footprint section, build_footprint)"],
    related: ["book_features"],
  },
  {
    id: "ema_trend", group: "chart", name: "EMA Trend Lines (fast/slow)", cadence: "static", window: "computed over internally-aggregated 1-minute candles",
    owner: "views/chart_series.py (moved there in Story 24.2), using Nautilus's own ExponentialMovingAverage indicator",
    shownIn: ["Web dashboard per-coin chart page only"],
    tagline: "EMA-8 / EMA-21 crossover lines on 1-minute candles — reuses nautilus_trader's built-in indicator, not a custom EMA.",
    formula: null,
    notes: ["Entirely a read-time chart-page replay — nothing here is persisted or fed into ranking. Consistent with the project rule to reach for a Nautilus built-in (<code>nautilus_trader.indicators</code>) before writing a custom one."],
    refs: ["views/chart_series.py (compute_chart_series)"],
    related: ["book_features"],
  },
  {
    id: "logistic_trend", group: "chart", name: "OnlineLogisticTrend", cadence: "static", window: "online, one SGD step per bar",
    owner: "strategy-only — fed manually by a Strategy subclass",
    shownIn: ["Backtest / live bot strategies only (research/strategies/example_strategy.py)"],
    tagline: "Online (incremental) logistic regression predicting P(next bar's return > 0) from the last N bar-to-bar returns.",
    formula: "z = weights · features + bias\nP(up) = 1 / (1 + e⁻ᶻ)\neach new bar: one SGD step trains on the PREVIOUS prediction now that the true outcome is known, then predicts the next probability",
    notes: [
      "<code>self.value</code> is that probability, 0.5 until <code>initialized</code> (i.e. until <code>lookback</code> returns have accumulated).",
      "Deliberately minimal: a single online SGD step per bar, no batch retraining, no persistence across restarts. Documented in-code as a known limit with a named upgrade path (<code>sklearn.linear_model.SGDClassifier</code> + periodic refit) if it drifts on a long-running deployment.",
      "Never published to <code>rankings:live</code> — this is a strategy-internal signal, not a UI metric.",
    ],
    refs: ["kernel/indicators.py:60–133"],
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
  { id: "chart", name: "Chart" },
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
