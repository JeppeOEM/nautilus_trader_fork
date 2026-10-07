import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  barCountdown,
  formatChartTime,
  formatClock,
  formatCountdown,
  formatDateTime,
  sessionBreakTimes,
  tickMarkFormatter,
  timeZoneLabel,
} from "./time";

// 2026-10-07 13:45:00 UTC.
const T = Date.UTC(2026, 9, 7, 13, 45, 0) / 1000;
describe("time formatting (Story 33.12)", () => {
  it("prints UTC, and exchange time as UTC (every collected venue runs on UTC)", () => {
    expect(formatChartTime(T, "utc")).toBe("2026-10-07 13:45");
    expect(formatChartTime(T, "exchange")).toBe("2026-10-07 13:45");
    expect(formatDateTime(T + 7, "utc")).toBe("2026-10-07 13:45:07");
  });

  it("shows seconds only where the time has them", () => {
    expect(formatChartTime(T + 9, "utc")).toBe("2026-10-07 13:45:09");
  });

  it("prints a nanosecond event time as a clock", () => {
    expect(formatClock(T * 1e9 + 5e8, "utc")).toBe("13:45:00");
  });

  it("names the zone", () => {
    expect(timeZoneLabel("utc")).toBe("UTC");
    expect(timeZoneLabel("exchange")).toBe("UTC");
    expect(timeZoneLabel("local")).toBe("local");
  });

  it("prints a daily or weekly bar as its UTC date alone (the bucket is a UTC day)", () => {
    const day = Date.UTC(2026, 9, 7) / 1000;
    expect(formatChartTime(day, "utc", 86_400)).toBe("2026-10-07");
    expect(formatChartTime(day, "utc", 604_800)).toBe("2026-10-07");
    expect(formatChartTime(T, "utc", 3600)).toBe("2026-10-07 13:45");
  });

  it("prints a time Date cannot hold as a placeholder, never NaN", () => {
    expect(formatChartTime(1e20, "utc")).toBe("invalid time");
  });

  it("labels tick marks in the zone, by the library's tick kinds", () => {
    const utc = tickMarkFormatter("utc");
    expect(utc(T, 0)).toBe("2026");
    expect(utc(T, 1)).toBe("Oct");
    expect(utc(T, 2)).toBe("7");
    expect(utc(T, 3)).toBe("13:45");
    expect(utc(T + 3, 4)).toBe("13:45:03");
    expect(utc("2026-10-07", 2)).toBeNull();
  });
});

// A non-UTC viewer, pinned: under the runner's own zone (often UTC) a local-zone test could not fail.
// Node re-reads `TZ` when it is assigned, so the dates below are formatted in New York time
// (EDT, UTC-4, on 2026-10-07; EST, UTC-5, on 1 January).
describe("local time (Story 33.12, viewer in America/New_York)", () => {
  let saved: string | undefined;
  beforeAll(() => {
    saved = process.env.TZ;
    process.env.TZ = "America/New_York";
  });
  afterAll(() => {
    if (saved === undefined) delete process.env.TZ;
    else process.env.TZ = saved;
  });

  it("really runs in New York time", () => {
    expect(new Date(T * 1000).getHours()).toBe(9);
  });

  it("prints local time by the viewer's zone", () => {
    expect(formatChartTime(T, "local")).toBe("2026-10-07 09:45");
    expect(formatDateTime(T + 7, "local")).toBe("2026-10-07 09:45:07");
    expect(formatClock(T * 1e9, "local")).toBe("09:45:00");
  });

  it("prints a daily bar's UTC date in the local zone too", () => {
    expect(formatChartTime(Date.UTC(2026, 9, 7) / 1000, "local", 86_400)).toBe("2026-10-07");
  });

  it("labels a UTC-boundary tick with the local date and time it really sits on", () => {
    const local = tickMarkFormatter("local");
    expect(local(Date.UTC(2026, 9, 7) / 1000, 2)).toBe("6 20:00");
    expect(local(Date.UTC(2026, 9, 1) / 1000, 1)).toBe("Sep 30 20:00");
    expect(local(Date.UTC(2026, 0, 1) / 1000, 0)).toBe("2025-12-31 19:00");
    expect(local(T, 3)).toBe("09:45");
  });

  it("keeps the plain calendar label on a tick at local midnight", () => {
    const local = tickMarkFormatter("local");
    const localMidnight = Date.UTC(2026, 9, 7, 4) / 1000; // 2026-10-07 00:00 EDT
    expect(local(localMidnight, 2)).toBe("7");
    expect(local(localMidnight, 1)).toBe("Oct");
    expect(local(localMidnight, 0)).toBe("2026");
  });

  it("keeps UTC labels in UTC whatever the viewer's zone", () => {
    expect(tickMarkFormatter("utc")(Date.UTC(2026, 9, 7) / 1000, 2)).toBe("7");
    expect(formatChartTime(T, "utc")).toBe("2026-10-07 13:45");
  });
});

describe("sessionBreakTimes (Story 33.12)", () => {
  const MIDNIGHT = Date.UTC(2026, 9, 8) / 1000;

  it("marks the first bar of each UTC day, not the first loaded bar", () => {
    const times = [MIDNIGHT - 120, MIDNIGHT - 60, MIDNIGHT, MIDNIGHT + 60];
    expect(sessionBreakTimes(times, 60)).toEqual([MIDNIGHT]);
  });

  it("marks the first bar after a gap that spans midnight", () => {
    expect(sessionBreakTimes([MIDNIGHT - 60, MIDNIGHT + 3600], 60)).toEqual([MIDNIGHT + 3600]);
  });

  it("draws none at a bar of a day or longer", () => {
    expect(sessionBreakTimes([MIDNIGHT - 86_400, MIDNIGHT], 86_400)).toEqual([]);
    expect(sessionBreakTimes([MIDNIGHT - 604_800, MIDNIGHT], 604_800)).toEqual([]);
  });
});

describe("barCountdown and formatCountdown (Story 33.12)", () => {
  it("counts to the last bar's close", () => {
    expect(barCountdown(600, 60, 615)).toBe(45);
  });

  it("counts to the next whole-bar close once the last bar's close has passed", () => {
    expect(barCountdown(600, 60, 700)).toBe(20); // the bar forming at 660 closes at 720
  });

  it("counts a whole extra bar when now sits exactly on a boundary", () => {
    expect(barCountdown(600, 60, 660)).toBe(60);
  });

  it("never counts more than one bar when the viewer's clock is behind the bar's open", () => {
    expect(barCountdown(600, 60, 590)).toBe(60);
    expect(barCountdown(600, 60, 0)).toBe(60);
  });

  it("works for any bucket alignment (a weekly bar opening on a Monday)", () => {
    const monday = Date.UTC(2026, 9, 5) / 1000;
    expect(barCountdown(monday, 604_800, monday + 86_400)).toBe(6 * 86_400);
  });

  it("formats mm:ss, h:mm:ss from an hour and Nd hh:mm from a day", () => {
    expect(formatCountdown(45)).toBe("00:45");
    expect(formatCountdown(59.2)).toBe("01:00");
    expect(formatCountdown(3_725)).toBe("1:02:05");
    expect(formatCountdown(6 * 86_400 + 3_660)).toBe("6d 01:01");
  });
});
