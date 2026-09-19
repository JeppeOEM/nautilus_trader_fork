import type { Time } from "lightweight-charts";
import { describe, expect, it } from "vitest";

import type { ChartDatum, VolumeDatum } from "../hooks/useCandles";
import {
  buildSessionProfiles,
  periodStart,
  periodStartBack,
  sessionBarSeconds,
  type SessionProfileCache,
} from "./sessionProfile";

const utc = (y: number, mo: number, d: number, h = 0, mi = 0): number => Date.UTC(y, mo - 1, d, h, mi) / 1000;
const settings = { rowCount: 4, valueAreaPercent: 70 };

describe("periodStart (Story 18.8/18.9)", () => {
  it("aligns 4h and daily periods to UTC boundaries", () => {
    expect(periodStart(utc(2024, 1, 3, 13, 59), "4h")).toBe(utc(2024, 1, 3, 12));
    expect(periodStart(utc(2024, 1, 3, 15, 0), "4h")).toBe(utc(2024, 1, 3, 12));
    expect(periodStart(utc(2024, 1, 3, 23, 59), "daily")).toBe(utc(2024, 1, 3));
    expect(periodStart(utc(2024, 1, 4, 0, 0), "daily")).toBe(utc(2024, 1, 4));
  });

  it("starts weeks on Monday 00:00 UTC", () => {
    const monday = utc(2024, 1, 1); // a Monday
    expect(periodStart(utc(2024, 1, 3, 12), "weekly")).toBe(monday);
    expect(periodStart(utc(2024, 1, 7, 23, 59), "weekly")).toBe(monday);
    expect(periodStart(utc(2024, 1, 8), "weekly")).toBe(utc(2024, 1, 8));
    expect(periodStart(utc(2024, 1, 1), "weekly")).toBe(monday);
  });

  it("starts months on the 1st, across variable lengths and a leap February", () => {
    expect(periodStart(utc(2024, 2, 29, 23, 59), "monthly")).toBe(utc(2024, 2, 1));
    expect(periodStart(utc(2024, 3, 1), "monthly")).toBe(utc(2024, 3, 1));
    expect(periodStart(utc(2023, 12, 31, 23, 59), "monthly")).toBe(utc(2023, 12, 1));
  });

  it("steps back whole periods, including across a year boundary", () => {
    expect(periodStartBack(utc(2024, 1, 3, 5), "daily", 2)).toBe(utc(2024, 1, 1));
    expect(periodStartBack(utc(2024, 1, 10), "weekly", 1)).toBe(utc(2024, 1, 1));
    expect(periodStartBack(utc(2024, 1, 20), "monthly", 2)).toBe(utc(2023, 11, 1));
    expect(periodStartBack(utc(2024, 1, 3, 13), "4h", 1)).toBe(utc(2024, 1, 3, 8));
  });

  it("profiles long periods from coarser bars", () => {
    expect(sessionBarSeconds("daily")).toBe(60);
    expect(sessionBarSeconds("weekly")).toBeGreaterThan(60);
    expect(sessionBarSeconds("monthly")).toBeGreaterThan(sessionBarSeconds("weekly"));
  });
});

describe("buildSessionProfiles (Story 18.8)", () => {
  // Three UTC days, two 1-minute bars each; day 1 is cheap, day 2 mid, day 3 (in progress) high.
  const bar = (time: number, price: number): ChartDatum => ({
    time: time as Time,
    open: price,
    high: price + 1,
    low: price,
    close: price + 1,
  });
  const vol = (time: number, value: number): VolumeDatum => ({ time: time as Time, value });
  const D1 = utc(2024, 1, 1);
  const D2 = utc(2024, 1, 2);
  const D3 = utc(2024, 1, 3);
  const candles = [bar(D1, 10), bar(D1 + 60, 11), bar(D2, 20), bar(D2 + 60, 21), bar(D3, 30), bar(D3 + 60, 31)];
  const volume = [D1, D1 + 60, D2, D2 + 60, D3, D3 + 60].map((t) => vol(t, 5));
  const build = (over: { candles?: ChartDatum[]; volume?: VolumeDatum[]; count?: number; from?: number | null } = {}, cache: SessionProfileCache = new Map()) =>
    buildSessionProfiles(over.candles ?? candles, over.volume ?? volume, "daily", over.count ?? 5, settings, cache, over.from ?? null);

  it("produces one independent profile per calendar day, oldest first (AC #1/#4)", () => {
    const entries = build();

    expect(entries.map((e) => e.periodStart)).toEqual([D1, D2, D3]);
    expect(entries.map((e) => e.startTime)).toEqual([D1, D2, D3]);
    expect(entries.map((e) => e.endTime)).toEqual([D1 + 60, D2 + 60, D3 + 60]);
    // Each session's price range is its own -- never merged across days.
    expect(entries[0].profile.rows[0].priceLow).toBe(10);
    expect(entries[1].profile.rows[0].priceLow).toBe(20);
    expect(entries[2].profile.rows[0].priceLow).toBe(30);
    expect(entries.map((e) => e.profile.totalVolume)).toEqual([10, 10, 10]);
  });

  it("assigns a bar to its own UTC day near midnight", () => {
    const entries = build({
      candles: [bar(D2 - 60, 10), bar(D2, 20)],
      volume: [vol(D2 - 60, 1), vol(D2, 1)],
    });

    expect(entries.map((e) => e.periodStart)).toEqual([D1, D2]);
  });

  it("renders only the last N sessions (AC #4)", () => {
    expect(build({ count: 2 }).map((e) => e.periodStart)).toEqual([D2, D3]);
    expect(build({ count: 0 })).toEqual([]);
  });

  it("only the in-progress session's profile changes when a bar arrives (AC #2)", () => {
    const cache: SessionProfileCache = new Map();
    const before = build({}, cache);

    const after = build(
      { candles: [...candles, bar(D3 + 120, 40)], volume: [...volume, vol(D3 + 120, 5)] },
      cache,
    );

    expect(after[0].profile).toBe(before[0].profile);
    expect(after[1].profile).toBe(before[1].profile);
    expect(after[2].profile).not.toBe(before[2].profile);
    expect(after[2].profile.totalVolume).toBe(15);
    expect(after[2].endTime).toBe(D3 + 120);
  });

  it("rebuilds the in-progress session when its forming bar is updated in place (same count and times)", () => {
    const cache: SessionProfileCache = new Map();
    const before = build({}, cache);

    const updatedVolume = volume.map((v, i) => (i === volume.length - 1 ? vol(D3 + 60, 50) : v));
    const after = build({ volume: updatedVolume }, cache);

    expect(after[0].profile).toBe(before[0].profile);
    expect(after[1].profile).toBe(before[1].profile);
    expect(after[2].profile).not.toBe(before[2].profile);
    expect(after[2].profile.totalVolume).toBe(55);
  });

  it("rebuilds every session when the settings change, and prunes sessions that left view", () => {
    const cache: SessionProfileCache = new Map();
    const before = build({}, cache);

    const rebuilt = buildSessionProfiles(candles, volume, "daily", 5, { rowCount: 6, valueAreaPercent: 70 }, cache);
    expect(rebuilt[0].profile).not.toBe(before[0].profile);
    expect(rebuilt[0].profile.rows).toHaveLength(6);

    build({ count: 1 }, cache);
    expect(cache.size).toBe(1);
  });

  it("omits periods that start before the fully-covered history instead of showing a truncated profile", () => {
    const entries = build({ from: D2 });

    expect(entries.map((e) => e.periodStart)).toEqual([D2, D3]);
  });

  it("skips gap entries and bars without volume", () => {
    const entries = build({
      candles: [...candles, { time: (D3 + 120) as Time }, bar(D3 + 180, 50)],
      volume,
    });

    expect(entries[2].endTime).toBe(D3 + 60); // the volume-less bar at +180 is not part of the session
  });
});
