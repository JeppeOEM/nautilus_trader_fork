import type { IChartApi, Time } from "lightweight-charts";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { LiquidationItem } from "../api/schema";
import { chartVar } from "../components/chart/chartTheme";
import {
  type SpecContext,
  basisData,
  basisSpecs,
  candleSlots,
  cutPoints,
  fundingCountdown,
  fundingData,
  fundingSpec,
  liquidationData,
  liquidationSpecs,
  markIndexData,
  markIndexSpecs,
  openInterestSpec,
} from "../components/chart/derivativePanes";
import type { IndicatorPaneSpec } from "../components/chart/LightweightChart";
import { type MarkerSpec, buildLiquidationMarkers, markersHiddenAt } from "../components/chart/LiquidationMarkers";
import type { DerivativesLayout } from "../lib/chartLayout";
import {
  type LiveSlots,
  NO_LIVE_SLOTS,
  addLiveTick,
  bucketPoints,
  eventsUpTo,
  fundingPerBar,
  overlayMarkIndex,
  overlayOpenInterest,
  pruneLiveSlots,
  withLiveFunding,
} from "../lib/derivativeSeries";
import { useDerivativePages } from "./useDerivativePages";
import { MARKER_MAX_ROWS, useLiquidationEvents } from "./useLiquidationEvents";
import { useLiveDerivs } from "./useLiveDerivs";
import { type LiveLiquidation, useLiveLiquidations } from "./useLiveLiquidations";

// Story 33.5: everything the chart's Derivatives group draws, from Story 33.4's routes and live
// channels: the pane specs, the liquidation markers and the tape's rows.

export interface ChartDerivativesInput {
  instrumentId: string;
  barSeconds: number;
  chart: IChartApi | null;
  /** The coin's saved Derivatives settings. */
  settings: DerivativesLayout;
  /** The candles response's `market` (null until it arrived): spot draws, fetches and subscribes nothing. */
  market: string | null;
  /** Candles mode: the panes share the candles' bar axis, so Lines mode draws none. */
  candlesMode: boolean;
  /** The displayed bars (a replay's cut included), gap slots as whitespace. */
  bars: readonly { time: Time }[];
  /** The forming bar's slot (chart seconds); null under a replay, which withholds every live value. */
  liveTime: number | null;
  /** The replay cutoff bar (chart seconds), or null. */
  cutoff: number | null;
  /** The time scale's bar spacing (px): markers hide at or below `MARKER_MIN_BAR_SPACING_PX`. */
  barSpacing: number;
  /** The Liquidation tape panel is open (view state). */
  tapeOn: boolean;
}

export interface ChartDerivatives {
  panes: IndicatorPaneSpec[];
  markers: readonly MarkerSpec[];
  tape: { rows: LiquidationItem[]; loaded: boolean; noFeed: boolean; error: string | null };
  /** Spot: the group is disabled and nothing is fetched. */
  spot: boolean;
  /** The group can draw: the market is known and not spot, and the chart is in Candles mode. */
  available: boolean;
  /** Re-read every route's newest page (a socket came back). */
  refreshNewest: () => void;
  /** A candle closed: re-read now and again once the archive holds the closed bar. */
  refreshAfterClose: () => void;
}

const NO_PANES: IndicatorPaneSpec[] = [];
const NO_MARKERS: readonly MarkerSpec[] = [];
const NO_ROWS: LiquidationItem[] = [];
const NS_PER_S = 1_000_000_000;

function liveItem(row: LiveLiquidation): LiquidationItem | null {
  // A frame without the server's notional (a data_api older than Story 33.5) is not drawn with an
  // invented one: it waits for the next page, which carries it.
  if (row.notional_units === undefined || row.notional_precision === undefined) return null;
  const { instrument_id: _iid, ...rest } = row;
  return { ...rest, price_kind: "bankruptcy", notional_units: row.notional_units, notional_precision: row.notional_precision };
}

/** The live `derivs:` values of bars the routes have not served yet (see `LiveSlots`), pruned as the
 * routes catch up. Ticks outside the forming slot are ignored. */
function useLiveSlots(
  instrumentId: string,
  enabled: boolean,
  liveTime: number | null,
  barSeconds: number,
  routes: { oi: Parameters<typeof pruneLiveSlots>[1]; markIndex: Parameters<typeof pruneLiveSlots>[2]; funding: Parameters<typeof pruneLiveSlots>[3] },
  onReconnect: () => void,
): LiveSlots {
  const [live, setLive] = useState<LiveSlots>(NO_LIVE_SLOTS);
  const liveTimeRef = useRef(liveTime);
  useEffect(() => {
    liveTimeRef.current = liveTime;
  }, [liveTime]);
  useLiveDerivs(enabled ? instrumentId : "", {
    onTick: (tick) => setLive((prev) => addLiveTick(prev, tick, liveTimeRef.current, barSeconds)),
    onReconnect,
  });
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLive((prev) => pruneLiveSlots(prev, routes.oi, routes.markIndex, routes.funding));
  }, [routes.oi, routes.markIndex, routes.funding]);
  return live;
}

/**
 * The Derivatives group of one chart (Story 33.5). Each route is read only while its entry is drawn,
 * the market is known and not spot, and the chart is in Candles mode; the live sockets likewise.
 * Under a replay (`liveTime` null) no live value is applied and everything is cut at `cutoff`. Each
 * series' `data` is memoised on its own inputs, so the funding countdown (once a second) or another
 * series' tick never hands the chart a new array.
 */
export function useChartDerivatives(input: ChartDerivativesInput): ChartDerivatives {
  const { instrumentId, barSeconds, chart, settings, market, candlesMode, bars, liveTime, cutoff, barSpacing, tapeOn } = input;
  const spot = market === "spot";
  const available = market !== null && !spot && candlesMode;
  const firstCandle = bars.length > 0 ? (bars[0].time as number) : null;
  const on = {
    oi: available && settings.oi.on,
    funding: available && settings.funding.on,
    markIndex: available && (settings.basis.on || settings.mark_index.on),
    liquidations: available && settings.liquidations.on,
    markers: available && settings.liquidations.on && settings.liquidations.markers,
    tape: available && tapeOn,
  };

  const oi = useDerivativePages("open-interest", instrumentId, barSeconds, on.oi, chart, firstCandle);
  const funding = useDerivativePages("funding", instrumentId, barSeconds, on.funding, chart, firstCandle);
  const markIndex = useDerivativePages("mark-index", instrumentId, barSeconds, on.markIndex, chart, firstCandle);
  const liqBars = useDerivativePages("liquidation-bars", instrumentId, barSeconds, on.liquidations, chart, firstCandle);
  const markerRows = useLiquidationEvents(instrumentId, "markers", on.markers, firstCandle);
  const replayBeforeNs = cutoff === null ? null : (cutoff + barSeconds) * NS_PER_S;
  const tapeRows = useLiquidationEvents(instrumentId, "tape", on.tape, firstCandle, replayBeforeNs);

  const pages = [oi, funding, markIndex, liqBars];
  const [a0, a1, a2, a3] = pages.map((p) => p.refreshAfterClose);
  const [n0, n1, n2, n3] = pages.map((p) => p.refreshNewest);
  const { refreshNewest: n4 } = markerRows;
  const { refreshNewest: n5 } = tapeRows;
  const refreshNewest = useCallback((): void => {
    for (const refresh of [n0, n1, n2, n3, n4, n5]) refresh();
  }, [n0, n1, n2, n3, n4, n5]);
  const refreshAfterClose = useCallback((): void => {
    for (const refresh of [a0, a1, a2, a3, n4, n5]) refresh();
  }, [a0, a1, a2, a3, n4, n5]);

  const live = useLiveSlots(
    instrumentId,
    on.oi || on.funding || on.markIndex,
    liveTime,
    barSeconds,
    { oi: oi.rows, markIndex: markIndex.rows, funding: funding.rows },
    refreshNewest,
  );
  const { push: pushMarkers } = markerRows;
  const { push: pushTape } = tapeRows;
  useLiveLiquidations(on.markers || on.tape ? instrumentId : "", {
    onLiquidation: (row) => {
      const item = liveItem(row);
      if (item === null) return;
      pushMarkers([item]);
      pushTape([item]);
    },
    onReconnect: refreshNewest,
  });

  // The funding legend's countdown ticks once a second, only while Funding is drawn; it reaches the
  // legend's text alone, never a series' data.
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    if (!on.funding) return;
    const timer = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [on.funding]);

  const slots = useMemo(() => candleSlots(bars, liveTime), [bars, liveTime]);
  const stepMs = barSeconds * 1000;

  const oiRows = useMemo(() => overlayOpenInterest(oi.rows, live.oi, stepMs), [oi.rows, live.oi, stepMs]);
  const oiData = useMemo(() => cutPoints(bucketPoints(oiRows, "oi"), firstCandle, cutoff), [oiRows, firstCandle, cutoff]);

  const markIndexLive = useMemo(() => ({ ...NO_LIVE_SLOTS, mark: live.mark, index: live.index, basis: live.basis }), [live.mark, live.index, live.basis]);
  const markIndexRows = useMemo(() => overlayMarkIndex(markIndex.rows, markIndexLive, stepMs), [markIndex.rows, markIndexLive, stepMs]);
  const basisLines = useMemo(() => basisData(markIndexRows, firstCandle, cutoff), [markIndexRows, firstCandle, cutoff]);
  const markIndexLines = useMemo(() => markIndexData(markIndexRows, firstCandle, cutoff), [markIndexRows, firstCandle, cutoff]);

  // Every loaded event feeds the hold (a bar takes only events before its end, so a replay's bars never
  // see a later rate); the live events the route does not hold yet join them (the route wins by `t`).
  const fundingEvents = useMemo(() => withLiveFunding(funding.rows, live.funding), [funding.rows, live.funding]);
  const latestFunding = useMemo(() => eventsUpTo(fundingEvents, (e) => e.t, cutoff, barSeconds).at(-1), [fundingEvents, cutoff, barSeconds]);
  const fundingBars = useMemo(
    () => fundingPerBar(fundingEvents, slots.times, slots.gaps, barSeconds, liveTime),
    [fundingEvents, slots, barSeconds, liveTime],
  );
  const fundingPoints = useMemo(() => fundingData(fundingBars, firstCandle, cutoff), [fundingBars, firstCandle, cutoff]);
  // Under a replay the wall clock says nothing about a past event's next funding time.
  const countdown = fundingCountdown(latestFunding, cutoff === null ? nowMs : null);

  const measure = settings.liquidations.measure;
  const liqLines = useMemo(() => liquidationData(liqBars.rows, measure, firstCandle, cutoff), [liqBars.rows, measure, firstCandle, cutoff]);

  const markerSource = useMemo(() => eventsUpTo(markerRows.rows, (r) => r.ts_event, cutoff, barSeconds), [markerRows.rows, cutoff, barSeconds]);
  const markersHidden = markersHiddenAt(barSpacing);
  const markers = useMemo(() => {
    if (!on.markers || markerSource.length === 0) return NO_MARKERS;
    const barsByT = new Map(liqBars.rows.map((row) => [row.t, row] as const));
    const colors = { up: chartVar("--chart-up"), down: chartVar("--chart-down") };
    return buildLiquidationMarkers(markerSource, slots.times, barSeconds, barSpacing, colors, barsByT);
  }, [on.markers, markerSource, slots.times, barSeconds, barSpacing, liqBars.rows]);

  const ctx = useCallback(
    (error: string | null): SpecContext => ({ firstCandle, cutoff, error: error === null ? null : "load failed" }),
    [firstCandle, cutoff],
  );
  const markerNotes = useMemo(() => {
    const notes: string[] = [];
    if (on.markers && markersHidden) notes.push("markers hidden: zoom in");
    if (on.markers && markerRows.capped) notes.push(`markers capped at ${MARKER_MAX_ROWS}`);
    if (on.markers && markerRows.error !== null) notes.push("markers load failed");
    return notes;
  }, [on.markers, markersHidden, markerRows.capped, markerRows.error]);

  const panes = useMemo(() => {
    if (!available) return NO_PANES;
    const out: IndicatorPaneSpec[] = [];
    if (on.oi) out.push(openInterestSpec(oiRows, oiData, settings.oi, ctx(oi.error)));
    if (on.funding) out.push(fundingSpec(fundingBars, fundingPoints, countdown, settings.funding, ctx(funding.error)));
    if (on.markIndex && settings.basis.on) out.push(...basisSpecs(basisLines, settings.basis, ctx(markIndex.error)));
    if (on.liquidations) out.push(...liquidationSpecs(liqBars.rows, liqLines, settings.liquidations, ctx(liqBars.error), markerNotes));
    if (on.markIndex && settings.mark_index.on) out.push(...markIndexSpecs(markIndexRows, markIndexLines, settings.mark_index, ctx(markIndex.error)));
    return out;
  }, [
    available, on.oi, on.funding, on.markIndex, on.liquidations, settings, oiRows, oiData, oi.error, fundingBars, fundingPoints,
    countdown, funding.error, basisLines, markIndexRows, markIndexLines, markIndex.error, liqBars.rows, liqLines, liqBars.error,
    markerNotes, ctx,
  ]);

  const tapeShown = useMemo(() => tapeRows.replayRows ?? tapeRows.rows, [tapeRows.replayRows, tapeRows.rows]);
  return {
    panes,
    markers,
    tape: {
      rows: on.tape ? tapeShown : NO_ROWS,
      loaded: tapeRows.loaded && (cutoff === null || tapeRows.replayRows !== null),
      noFeed: tapeRows.loaded && tapeRows.rows.length === 0,
      error: (cutoff === null ? null : tapeRows.replayError) ?? tapeRows.error,
    },
    spot,
    available,
    refreshNewest,
    refreshAfterClose,
  };
}
