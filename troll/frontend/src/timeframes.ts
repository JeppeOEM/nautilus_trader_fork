// Spec §A8.1 slot 2: the top toolbar's timeframe selector. Bars > 1h are served from the
// minute rollup server-side (Story 16.2), so 4H/1D/1W stay cheap. No 1s: a 1s bar exists
// only for a second with a trade and dYdX delivers trades 1-3 s late, so it read as a
// frozen, time-nonlinear chart (see spec-21-x-candlestick-chart-correctness.md's backlog).
export const TIMEFRAMES = [
  { label: "1m", seconds: 60 },
  { label: "5m", seconds: 300 },
  { label: "15m", seconds: 900 },
  { label: "1H", seconds: 3600 },
  { label: "4H", seconds: 14400 },
  { label: "1D", seconds: 86400 },
  { label: "1W", seconds: 604800 },
] as const;
