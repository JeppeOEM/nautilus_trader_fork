import type { Time } from "lightweight-charts";
import { useCallback, useEffect, useMemo, useState } from "react";

import type { ChartDatum } from "./useCandles";

export const REPLAY_SPEEDS = [0.5, 1, 2, 5] as const;
export const REPLAY_BASE_MS = 700;

export type ReplayMode = "off" | "picking" | "active";

const isBar = (c: ChartDatum): boolean => "open" in c;

/** The nearest real (non-gap) bar time strictly after/before `time`, or null at the end.
 * By time, not index: `time` may be absent after a reload/trim and must still step on. */
export function stepBarTime(candles: readonly ChartDatum[], time: number, dir: 1 | -1): number | null {
  const bars = candles.filter((c) => isBar(c) && ((c.time as number) - time) * dir > 0);
  if (bars.length === 0) return null;
  return (dir === 1 ? bars[0] : bars[bars.length - 1]).time as number;
}

interface ReplayState {
  mode: ReplayMode;
  /** Time of the picked start bar (the marker); kept while re-picking via "Go to...". */
  startTime: number | null;
  /** Time of the newest revealed bar. Times, not indices: a scroll-back refill prepends
   * older bars and shifts every array index, but never changes a bar's time. */
  lastTime: number | null;
  playing: boolean;
  speedIdx: number;
}

const OFF: ReplayState = { mode: "off", startTime: null, lastTime: null, playing: false, speedIdx: 1 };

// Story 18.4: replay state machine over the already-loaded candle array. Purely
// downstream of `useCandles` (AC #7): it only trims the newest end for display and never
// touches the hook's older-history pagination.
export function useReplay(candles: ChartDatum[]) {
  const [state, setState] = useState<ReplayState>(OFF);
  const { mode, startTime, lastTime, speedIdx } = state;

  const atEnd = lastTime !== null && stepBarTime(candles, lastTime, 1) === null;
  // Playback stops (never wraps) at the newest loaded bar -- derived, not effect-synced.
  const isPlaying = state.playing && !atEnd;
  const speed = REPLAY_SPEEDS[speedIdx];

  const displayed = useMemo(() => {
    if (mode !== "active" || lastTime === null) return candles;
    // By time, not by looking up the last bar: a missing lastTime must never fall back
    // to showing everything (lookahead leak).
    return candles.filter((c) => (c.time as number) <= lastTime);
  }, [candles, mode, lastTime]);

  useEffect(() => {
    if (!isPlaying) return;
    const id = setInterval(() => {
      setState((s) => {
        const next = s.lastTime === null ? null : stepBarTime(candles, s.lastTime, 1);
        if (next === null) return s;
        // Stop *in state* on the newest bar: `isPlaying` is masked by `atEnd`, so without
        // this a later-arriving bar would silently restart playback.
        return { ...s, lastTime: next, playing: s.playing && stepBarTime(candles, next, 1) !== null };
      });
    }, REPLAY_BASE_MS / speed);
    return () => clearInterval(id);
  }, [isPlaying, speed, candles]);

  // Stable identities for the state-only actions: ChartPage's Esc effect holds them.
  const startPicking = useCallback((): void => setState((s) => ({ ...s, mode: "picking", playing: false })), []);
  const cancelPick = useCallback(
    (): void => setState((s) => (s.mode !== "picking" ? s : s.startTime === null ? OFF : { ...s, mode: "active" })),
    [],
  );
  const exit = useCallback((): void => setState(OFF), []);
  const cycleSpeed = useCallback(
    (): void => setState((s) => ({ ...s, speedIdx: (s.speedIdx + 1) % REPLAY_SPEEDS.length })),
    [],
  );
  const togglePlay = (): void => setState((s) => ({ ...s, playing: !isPlaying }));
  const step = (dir: 1 | -1): void =>
    setState((s) => {
      const next = s.lastTime === null ? null : stepBarTime(candles, s.lastTime, dir);
      return { ...s, playing: false, lastTime: next ?? s.lastTime };
    });

  return {
    mode,
    displayed,
    /** Newest revealed bar's time while a replay is active -- callers trim every other
     * series (volume, indicator panes) to `time <= cutoffTime` so none show the future. */
    cutoffTime: mode === "active" ? lastTime : null,
    // Kept while re-picking via "Go to..." so the old start stays visible.
    markerTime: mode === "off" ? null : (startTime as Time | null),
    isPlaying,
    speed,
    startPicking,
    cancelPick,
    /** Returns false (staying in picking mode) when `time` is not a real loaded bar. */
    pick: (time: number): boolean => {
      const ok = candles.some((c) => c.time === time && isBar(c));
      if (ok) setState((s) => ({ ...s, mode: "active", startTime: time, lastTime: time, playing: false }));
      return ok;
    },
    exit,
    togglePlay,
    step,
    cycleSpeed,
  };
}
