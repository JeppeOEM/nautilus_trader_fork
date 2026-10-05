import { describe, expect, it } from "vitest";

import {
  DEFAULT_IB_MINUTES,
  TPO_BAR_SECONDS,
  TPO_MAX_BLOCKS_PER_ROW,
  initialBalance,
  tpoLetter,
  tpoRows,
} from "./tpo";
import { type ProfileCandle, buildVolumeProfile } from "./volumeProfile";

const c = (open: number, high: number, low: number, close: number, volume = 1): ProfileCandle => ({
  open,
  high,
  low,
  close,
  volume,
});

describe("TPO rows (Story 32.7)", () => {
  // Rows 0..3 of size 1 over 0..4: A row 0; B, C, D row 1; E rows 2..3 (down); F row 3.
  const slice = [c(0.2, 0.5, 0, 0.3), c(1.2, 1.9, 1.1, 1.5), c(1.3, 1.8, 1.2, 1.6), c(1.4, 1.7, 1.3, 1.5), c(3.5, 3.5, 2.2, 2.4), c(3.6, 4, 3.6, 3.9)];

  it("row r touched by 3 candles has 3 blocks and row s touched by 1 has 1; the POC is r", () => {
    const profile = buildVolumeProfile(slice, 4, 0.7, "time");
    const rows = tpoRows(profile, slice);

    expect(rows.map((r) => r.count)).toEqual([1, 3, 1, 2]);
    expect(rows.map((r) => r.blocks)).toEqual([1, 3, 1, 2]);
    expect(rows.map((r) => r.overflow)).toEqual([0, 0, 0, 0]);
    expect(profile.poc).toBe(1.5); // the centre of row 1, the 3-block row
  });

  it("agrees with the engine on every row's count", () => {
    const profile = buildVolumeProfile(slice, 4, 0.7, "time");

    expect(tpoRows(profile, slice).map((r) => r.count)).toEqual(profile.rows.map((r) => r.upVolume + r.downVolume));
  });

  it("lists each row's touches in time order with the candle's letter and side", () => {
    const rows = tpoRows(buildVolumeProfile(slice, 4, 0.7, "time"), slice);

    expect(rows[1].touches).toEqual([
      { letter: "B", up: true },
      { letter: "C", up: true },
      { letter: "D", up: true },
    ]);
    expect(rows[3].touches).toEqual([
      { letter: "E", up: false },
      { letter: "F", up: true },
    ]);
  });

  it("a row touched 40 times draws 30 blocks plus ONE longer bar standing for the other 10", () => {
    const touching = Array.from({ length: 40 }, () => c(1.2, 1.8, 1.2, 1.6));
    const bounds = [c(0, 0.1, 0, 0.05), c(3.9, 4, 3.9, 3.95)];
    const all = [...bounds, ...touching];
    const rows = tpoRows(buildVolumeProfile(all, 4, 0.7, "time"), all);

    expect(TPO_MAX_BLOCKS_PER_ROW).toBe(30);
    expect(rows[1]).toMatchObject({ count: 40, blocks: 30, overflow: 10 });
    expect(rows[1].touches).toHaveLength(30);
    expect(rows[0]).toMatchObject({ count: 1, blocks: 1, overflow: 0 });
  });

  it("takes the cap as an argument, and exactly the cap is not an overflow", () => {
    const touching = Array.from({ length: 3 }, () => c(1.2, 1.8, 1.2, 1.6));
    const all = [c(0, 0.1, 0, 0.05), c(3.9, 4, 3.9, 3.95), ...touching];
    const profile = buildVolumeProfile(all, 4, 0.7, "time");

    expect(tpoRows(profile, all, 3)[1]).toMatchObject({ blocks: 3, overflow: 0 });
    expect(tpoRows(profile, all, 2)[1]).toMatchObject({ blocks: 2, overflow: 1 });
  });

  it("has no rows for an empty profile, and a skipped candle does not shift the later letters", () => {
    expect(tpoRows(buildVolumeProfile([], 4, 0.7, "time"), [])).toEqual([]);

    const withBad = [c(1, 2, 1, 2), c(Number.NaN, 2, 1, 2), c(1, 2, 1, 2)];
    const rows = tpoRows(buildVolumeProfile(withBad, 1, 0.7, "time"), withBad);
    expect(rows[0].touches.map((t) => t.letter)).toEqual(["A", "C"]);
  });
});

describe("TPO letters by clock (Story 32.7)", () => {
  it("name each candle's 30-minute period from the session start, so a missing bar shifts nothing", () => {
    const at = (period: number) => ({ ...c(1, 2, 1, 2), time: 1000 + period * TPO_BAR_SECONDS });
    const candles = [at(0), at(2), at(3)]; // period 1 (B) is missing
    const rows = tpoRows(buildVolumeProfile(candles, 1, 0.7, "time"), candles, TPO_MAX_BLOCKS_PER_ROW, {
      sessionStart: 1000,
      barSeconds: TPO_BAR_SECONDS,
    });

    expect(rows[0].touches.map((t) => t.letter)).toEqual(["A", "C", "D"]);
  });
});

describe("tpoLetter", () => {
  it("runs A..Z, a..z and then around again", () => {
    expect([0, 1, 25, 26, 51, 52, 53].map(tpoLetter)).toEqual(["A", "B", "Z", "a", "z", "A", "B"]);
  });
});

describe("initialBalance (Story 32.7)", () => {
  const bars = (n: number, step: number) =>
    Array.from({ length: n }, (_, i) => ({ time: 1000 + i * step, high: 100 + i, low: 90 - i }));

  it("at 5m bars the default 60 minutes is the first 12 bars: their high and low", () => {
    const ib = initialBalance(bars(30, 300), 1000, DEFAULT_IB_MINUTES);

    expect(ib).toEqual({ startTime: 1000, endTime: 1000 + 11 * 300, high: 111, low: 79 });
  });

  it("is 2 bars of the 30-minute TPO candle by default", () => {
    const ib = initialBalance(bars(10, TPO_BAR_SECONDS), 1000, DEFAULT_IB_MINUTES);

    expect(ib).toMatchObject({ startTime: 1000, endTime: 1000 + TPO_BAR_SECONDS, high: 101, low: 89 });
  });

  it("is counted by time from the session start: a missing bar pulls no later bar in", () => {
    const withHole = bars(6, TPO_BAR_SECONDS).filter((_, i) => i !== 1); // the 2nd period is missing
    const ib = initialBalance(withHole, 1000, DEFAULT_IB_MINUTES);

    expect(ib).toEqual({ startTime: 1000, endTime: 1000, high: 100, low: 90 });
  });

  it("resolves to whole bars: 45 minutes at 30-minute bars takes the first two", () => {
    expect(initialBalance(bars(4, TPO_BAR_SECONDS), 1000, 45)).toMatchObject({ endTime: 1000 + TPO_BAR_SECONDS });
  });

  it("only the bars the session has", () => {
    expect(initialBalance(bars(3, 300), 1000, 600)).toMatchObject({ endTime: 1600 });
  });

  it("is null for no bars, none inside the window, or a non-positive length", () => {
    expect(initialBalance([], 1000, 60)).toBeNull();
    expect(initialBalance(bars(3, 300), 1000 - 7200, 60)).toBeNull(); // the session's first hour has no bar
    expect(initialBalance(bars(3, 300), 1000, 0)).toBeNull();
  });
});
