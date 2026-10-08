import type { Time } from "lightweight-charts";
import { describe, expect, it } from "vitest";

import type { FundingItem, LiquidationBarItem, OpenInterestItem } from "../api/schema";
import {
  NO_LIVE_SLOTS,
  addLiveTick,
  bucketPoints,
  overlayMarkIndex,
  overlayOpenInterest,
  pruneLiveSlots,
  withLiveFunding,
  eventSlot,
  eventsUpTo,
  fromTime,
  fundingPerBar,
  fundingPoints,
  mergeNewest,
  mirroredLiquidations,
  prependPage,
} from "./derivativeSeries";

const NS = 1_000_000_000;
// 10:00 UTC on an arbitrary day, in chart seconds.
const T10 = 1_800_000_000 - (1_800_000_000 % 86_400) + 10 * 3600;

function funding(tSec: number, rate: string): FundingItem {
  return { t: tSec * NS, rate, interval: 28_800, next_funding_ns: null, annualised: null };
}

describe("bucketPoints", () => {
  it("plots each row's value at its bucket, a missing value as whitespace", () => {
    const rows: OpenInterestItem[] = [{ t: 600_000, oi: "120", oi_change: null }, { t: 660_000 }, { t: 720_000, oi: "90", oi_change: "-30" }];
    expect(bucketPoints(rows, "oi")).toEqual([{ time: 600, value: 120 }, { time: 660 }, { time: 720, value: 90 }]);
    expect(bucketPoints([{ t: 60_000, basis_mi_bps: 50, basis_ml_bps: null }], "basis_ml_bps")).toEqual([{ time: 60 }]);
  });
});

describe("fromTime", () => {
  it("keeps the points from the first candle on, and none before the candles are known", () => {
    const points = [{ time: 1 as Time }, { time: 2 as Time }, { time: 3 as Time }];
    expect(fromTime(points, 2)).toEqual([{ time: 2 }, { time: 3 }]);
    expect(fromTime(points, 1)).toBe(points);
    expect(fromTime(points, 9)).toEqual([]);
    expect(fromTime(points, null)).toEqual([]);
  });
});

describe("eventSlot", () => {
  it("finds the slot an event falls in, by binary search", () => {
    const slots = [600, 660, 720];
    expect(eventSlot(600 * NS, slots)).toBe(600);
    expect(eventSlot(659.9 * NS, slots)).toBe(600);
    expect(eventSlot(725 * NS, slots)).toBe(720);
    expect(eventSlot(599 * NS, slots)).toBeNull();
    expect(eventSlot(790 * NS, slots, 60)).toBeNull();
    expect(eventSlot(779 * NS, slots, 60)).toBe(720);
  });

  it("puts an event in a hole between two slots (wider than the gap cap) in neither", () => {
    const slots = [600, 660, 6000];
    expect(eventSlot(719 * NS, slots, 60)).toBe(660);
    expect(eventSlot(720 * NS, slots, 60)).toBeNull();
    expect(eventSlot(5999 * NS, slots, 60)).toBeNull();
    expect(eventSlot(5999 * NS, slots)).toBe(660); // without `barSeconds`: the latest slot at or before it
  });
});

describe("fundingPerBar", () => {
  it("holds the last event per bar and never across a gap slot (the spec's example)", () => {
    const events = [funding(T10 + 20, "0.0001"), funding(T10 + 190, "-0.0002")];
    const candles = [T10, T10 + 60, T10 + 120, T10 + 180, T10 + 240];
    const bars = fundingPerBar(events, candles, new Set([T10 + 120]), 60, T10 + 240);
    expect(fundingPoints(bars)).toEqual([
      { time: T10, value: 0.0001 },
      { time: T10 + 60, value: 0.0001 },
      { time: T10 + 120 },
      { time: T10 + 180, value: -0.0002 },
      { time: T10 + 240, value: -0.0002 },
    ]);
  });

  it("does not hold across a gap slot when no event follows it", () => {
    const bars = fundingPerBar([funding(T10, "0.0001")], [T10, T10 + 60, T10 + 120], new Set([T10 + 60]), 60, T10 + 120);
    expect(bars.map((b) => b.event?.rate ?? null)).toEqual(["0.0001", null, null]);
  });

  it("starts the next real bar from an event inside a gap slot", () => {
    const events = [funding(T10 + 20, "0.0001"), funding(T10 + 150, "0.0005")];
    const candles = [T10, T10 + 60, T10 + 120, T10 + 180];
    const bars = fundingPerBar(events, candles, new Set([T10 + 120]), 60, T10 + 180);
    expect(bars.map((b) => b.event?.rate ?? null)).toEqual(["0.0001", "0.0001", null, "0.0005"]);
  });

  it("starts the next real bar from an event in the first of several gap slots", () => {
    const events = [funding(T10 + 20, "0.0001"), funding(T10 + 70, "0.0005")];
    const candles = [T10, T10 + 60, T10 + 120, T10 + 180];
    const bars = fundingPerBar(events, candles, new Set([T10 + 60, T10 + 120]), 60, T10 + 180);
    expect(bars.map((b) => b.event?.rate ?? null)).toEqual(["0.0001", null, null, "0.0005"]);
  });

  it("starts no earlier than the first loaded event", () => {
    const bars = fundingPerBar([funding(T10 + 70, "0.0001")], [T10, T10 + 60, T10 + 120], new Set(), 60, T10 + 120);
    expect(bars.map((b) => b.event?.rate ?? null)).toEqual([null, "0.0001", "0.0001"]);
  });

  it("runs no further than the newest event's bar, or the forming bar when later", () => {
    const candles = [T10, T10 + 60, T10 + 120, T10 + 180];
    const events = [funding(T10 + 5, "0.0001")];
    expect(fundingPerBar(events, candles, new Set(), 60).map((b) => b.event?.rate ?? null)).toEqual(["0.0001", null, null, null]);
    expect(fundingPerBar(events, candles, new Set(), 60, T10 + 120).map((b) => b.event?.rate ?? null)).toEqual([
      "0.0001",
      "0.0001",
      "0.0001",
      null,
    ]);
  });
});

describe("mirroredLiquidations", () => {
  const bar: LiquidationBarItem = {
    t: 600_000,
    long_v: 1500,
    short_v: 200,
    n: 3,
    size_precision: 3,
    notional_units: 170_000_000,
    notional_precision: 5,
    long_notional_units: 150_000_000,
    short_notional_units: null,
  };

  it("mirrors the sizes: longs below 0, shorts above", () => {
    expect(mirroredLiquidations([bar, { t: 660_000 }], "size")).toEqual({
      long: [{ time: 600, value: -1.5 }, { time: 660 }],
      short: [{ time: 600, value: 0.2 }, { time: 660 }],
    });
  });

  it("mirrors the server's per-side notionals, a null side as whitespace", () => {
    expect(mirroredLiquidations([bar], "notional")).toEqual({ long: [{ time: 600, value: -1500 }], short: [{ time: 600 }] });
  });
});

describe("page merges", () => {
  it("upserts a newest page by t, the page winning", () => {
    const held = [{ t: 0, oi: "1" }, { t: 60_000, oi: "2" }];
    expect(mergeNewest(held, [{ t: 60_000, oi: "3" }, { t: 120_000, oi: "4" }], 60_000)).toEqual([
      { t: 0, oi: "1" },
      { t: 60_000, oi: "3" },
      { t: 120_000, oi: "4" },
    ]);
  });

  it("puts gap slots between a newest page and an older held row", () => {
    expect(mergeNewest([{ t: 0 }], [{ t: 240_000 }], 60_000).map((r) => r.t)).toEqual([0, 60_000, 120_000, 180_000, 240_000]);
    // Event pages (no step) get no seam.
    expect(mergeNewest([{ t: 0 }], [{ t: 240_000 }]).map((r) => r.t)).toEqual([0, 240_000]);
  });

  it("puts gap slots between an older page and the held rows", () => {
    expect(prependPage([{ t: 300_000 }], [{ t: 60_000 }, { t: 120_000 }], 60_000).map((r) => r.t)).toEqual([
      60_000, 120_000, 180_000, 240_000, 300_000,
    ]);
  });
});

describe("eventsUpTo", () => {
  it("keeps the events before the cutoff bar's end", () => {
    const rows = [{ ts: 600 * NS }, { ts: 659 * NS }, { ts: 660 * NS }];
    expect(eventsUpTo(rows, (r) => r.ts, 600, 60)).toEqual([{ ts: 600 * NS }, { ts: 659 * NS }]);
    expect(eventsUpTo(rows, (r) => r.ts, null, 60)).toBe(rows);
  });
});

describe("live slots", () => {
  const tick = (kind: "oi" | "mark" | "index" | "funding", tSec: number, value: string, extra = {}) => ({ kind, t: tSec * NS, value, ...extra });

  it("takes a tick in the forming slot only", () => {
    const live = addLiveTick(NO_LIVE_SLOTS, tick("oi", 790, "95"), 780, 60);
    expect([...live.oi]).toEqual([[780_000, "95"]]);
    expect(addLiveTick(live, tick("oi", 779, "1"), 780, 60)).toBe(live);
    expect(addLiveTick(live, tick("oi", 790, "1"), null, 60)).toBe(live); // a replay: no forming bar
  });

  it("overlays an unserved bucket's live value, the route winning once it has one", () => {
    const live = addLiveTick(NO_LIVE_SLOTS, tick("oi", 790, "95"), 780, 60).oi;
    const route: OpenInterestItem[] = [{ t: 720_000, oi: "90", oi_change: "-30" }];
    expect(overlayOpenInterest(route, live, 60_000)).toEqual([...route, { t: 780_000, oi: "95", oi_change: null }]);
    const served = [...route, { t: 780_000, oi: "96", oi_change: "6" }];
    expect(overlayOpenInterest(served, live, 60_000)).toBe(served);
  });

  it("overlays mark/index per field with the server's live basis", () => {
    let live = addLiveTick(NO_LIVE_SLOTS, tick("mark", 790, "100.5", { basis_mi_bps: null }), 780, 60);
    live = addLiveTick(live, tick("index", 791, "100.0", { basis_mi_bps: 50 }), 780, 60);
    expect(overlayMarkIndex([], live, 60_000)).toEqual([{ t: 780_000, mark: "100.5", index: "100.0", basis_mi_bps: 50, basis_ml_bps: null }]);
  });

  it("keeps live funding events as events, the route winning by t, pruned once the route is past them", () => {
    const live = addLiveTick(NO_LIVE_SLOTS, tick("funding", 790, "0.0003", { annualised: 0.3 }), 780, 60);
    const route = [funding(700, "0.0001")];
    expect(withLiveFunding(route, live.funding).map((e) => e.rate)).toEqual(["0.0001", "0.0003"]);
    expect(pruneLiveSlots(live, [], [], route)).toBe(live);
    expect(pruneLiveSlots(live, [], [], [funding(790, "0.0003")]).funding).toEqual([]);
  });

  it("prunes a bucket the route now serves, and returns the same object when nothing changes", () => {
    const live = addLiveTick(NO_LIVE_SLOTS, tick("oi", 790, "95"), 780, 60);
    expect(pruneLiveSlots(live, [{ t: 780_000 }], [], [])).toBe(live);
    expect(pruneLiveSlots(live, [{ t: 780_000, oi: "96" }], [], []).oi.size).toBe(0);
  });
});
