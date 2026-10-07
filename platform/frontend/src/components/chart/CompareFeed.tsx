import type { IChartApi } from "lightweight-charts";
import { useEffect } from "react";

import { type ChartDatum, useCandles } from "../../hooks/useCandles";
import { type LiveBar, useLiveCandle } from "../../hooks/useLiveCandle";
import type { InstrumentPrecision } from "../../lib/drawings";

/** What one compare symbol's feed holds: its real candles, its forming bar and its definition's
 * decimals (null until its first candles response). */
export interface CompareFeedState {
  candles: ChartDatum[];
  liveBar: LiveBar | null;
  precision: InstrumentPrecision | null;
  loadFailed: boolean;
}

interface CompareFeedProps {
  instrumentId: string;
  chart: IChartApi | null;
  barSeconds: number;
  /** Reports the feed's state on every change, and null when the feed unmounts (the compare removed). */
  onState: (instrumentId: string, state: CompareFeedState | null) => void;
}

/**
 * Story 33.9: one compare symbol's data, renderless. A component per symbol, keyed by its id, because
 * hooks cannot be called in a loop of variable length. History comes from its own `useCandles` on the
 * main chart's timeframe and cursor rule (it pages on the shared chart's visible range); the forming bar
 * from its own `useLiveCandle`, wired like the main one: a closed bar is promoted into history and a
 * reconnect refetches the newest page.
 *
 * Known limit: the compare pages from the same visible-range trigger as the main series, at most one
 * page per main page, so when its pages cover less time than the main's (a sparser or younger market,
 * or a gap-heavy history) the compare's leftmost slots stay whitespace, and the deficit can persist
 * rather than being transient. Ceiling: one compare page per main page. Upgrade path: page the compare
 * by time until its earliest bar reaches the main series' earliest.
 */
export default function CompareFeed({ instrumentId, chart, barSeconds, onState }: CompareFeedProps) {
  const { candles, precision, loadFailed, refreshNewest, appendBar } = useCandles(instrumentId, chart, true, barSeconds);
  const liveBar = useLiveCandle(instrumentId, barSeconds, {
    onReconnect: () => void refreshNewest(),
    onBarClosed: appendBar,
  });
  useEffect(() => {
    onState(instrumentId, { candles, liveBar, precision, loadFailed });
  }, [instrumentId, candles, liveBar, precision, loadFailed, onState]);
  useEffect(() => () => onState(instrumentId, null), [instrumentId, onState]);
  return null;
}
