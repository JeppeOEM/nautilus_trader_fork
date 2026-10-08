import type { CandlestickData, Time } from "lightweight-charts";

import type { ChartDatum } from "../hooks/useCandles";

// Story 33.9: Heikin Ashi, a pure display transform of the real candles (AD-F6). Its output is only
// ever handed to the main series' `setData`/`update`; every indicator, drawing, alert, profile,
// measurement and gap computation keeps reading the real OHLC (`docs/DATA_INTEGRITY_AUDIT.md` D-206).

type Ohlc = Pick<CandlestickData<Time>, "time" | "open" | "high" | "low" | "close">;

const isCandle = (d: ChartDatum): d is CandlestickData<Time> => "open" in d;

/**
 * One Heikin Ashi bar from the real `bar` and the previous HA bar: close (o+h+l+c)/4, open the
 * previous HA bar's (open+close)/2 -- or the bar's own (o+c)/2 with no previous HA bar (the first bar,
 * or the first after a gap: the chain restarts, it never bridges a hole) -- high/low the real extremes
 * widened to the HA open/close. Also the live update: the forming bar recomputed from the last closed
 * HA bar, so earlier bars never change.
 */
export function heikinAshiNext(prevHa: Ohlc | null, bar: Ohlc): CandlestickData<Time> {
  const close = (bar.open + bar.high + bar.low + bar.close) / 4;
  const open = prevHa === null ? (bar.open + bar.close) / 2 : (prevHa.open + prevHa.close) / 2;
  return {
    time: bar.time,
    open,
    high: Math.max(bar.high, open, close),
    low: Math.min(bar.low, open, close),
    close,
  };
}

/**
 * The Heikin Ashi series of `data`, slot for slot: whitespace stays whitespace and restarts the chain.
 *
 * Known limit: the chain is seeded at the oldest loaded bar (and after each gap), so an older history
 * page arriving re-seeds it and the HA opens of the bars that were oldest shift. The shift halves with
 * every bar after the seed, so it is only visible on the first few bars of a run. Upgrade path: seed
 * from a fixed warm-up of real bars before the first drawn slot, so loading more history never moves a
 * drawn bar.
 */
export function heikinAshi(data: readonly ChartDatum[]): ChartDatum[] {
  let prev: CandlestickData<Time> | null = null;
  return data.map((d) => {
    if (!isCandle(d)) {
      prev = null;
      return d;
    }
    prev = heikinAshiNext(prev, d);
    return prev;
  });
}
