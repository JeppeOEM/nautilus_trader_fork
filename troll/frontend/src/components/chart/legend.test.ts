import type { IChartApi, MouseEventParams, Time } from "lightweight-charts";
import { describe, expect, it } from "vitest";

import { formatLegendValue, type LegendSeries, renderLegends } from "./legend";

// Real DOM elements stand in for the library's pane cells; the chart/series objects are the
// minimum shape renderLegends touches (pane elements + seriesData lookup by series identity).
function makeChart(count: number): { chart: IChartApi; els: HTMLElement[] } {
  const els = Array.from({ length: count }, () => document.createElement("div"));
  const panes = els.map((el, i) => ({ paneIndex: () => i, getHTMLElement: () => el }));
  return { chart: { panes: () => panes } as unknown as IChartApi, els };
}

function makeItem(over: Partial<LegendSeries> & { pane: LegendSeries["pane"] }): LegendSeries {
  return {
    group: "g",
    groupLabel: "G",
    outputLabel: "value",
    color: "rgb(1, 2, 3)",
    series: {} as LegendSeries["series"],
    data: [],
    ...over,
  };
}

const paneRef = (chart: IChartApi, i: number) => chart.panes()[i];
const rows = (el: HTMLElement) => [...el.querySelectorAll(".chart-legend-row")];
const text = (row: Element) => [...row.children].map((c) => c.textContent);

function hover(entries: [LegendSeries["series"], number | null][]): MouseEventParams<Time> {
  return {
    time: 1 as Time,
    seriesData: new Map(entries.map(([s, v]) => [s, v === null ? { time: 1 as Time } : { time: 1 as Time, value: v }])),
  } as unknown as MouseEventParams<Time>;
}

describe("formatLegendValue", () => {
  it("shows an em dash for missing values, 2 decimals for large ones, 4 significant digits for small", () => {
    expect(formatLegendValue(null)).toBe("—");
    expect(formatLegendValue(undefined)).toBe("—");
    expect(formatLegendValue(2651.7)).toBe("2651.70");
    expect(formatLegendValue(0.556789)).toBe("0.5568");
  });
});

describe("renderLegends", () => {
  it("puts an indicator's outputs in one row, in its own pane, colored per line, with the title", () => {
    const { chart, els } = makeChart(2);
    const rsi = makeItem({ pane: paneRef(chart, 1) as never, group: "RSI", groupLabel: "RSI (14)", color: "blue", data: [{ time: 1 as Time, value: 55.5 }] });
    const signal = makeItem({ pane: paneRef(chart, 1) as never, group: "RSI", groupLabel: "RSI (14)", color: "yellow", data: [{ time: 1 as Time, value: 48.25 }] });

    renderLegends(chart, [rsi, signal], null);

    expect(rows(els[0])).toHaveLength(0);
    const [row] = rows(els[1]);
    expect(text(row)).toEqual(["RSI (14)", "55.5", "48.25"]);
    expect([...row.querySelectorAll<HTMLElement>(".chart-legend-value")].map((v) => v.style.color)).toEqual(["blue", "yellow"]);
  });

  it("stacks overlay indicators as separate rows in the main (first) pane", () => {
    const { chart, els } = makeChart(2);
    const ema1 = makeItem({ pane: null, group: "EMA_a", groupLabel: "EMA (8)", data: [{ time: 1 as Time, value: 100 }] });
    const ema2 = makeItem({ pane: null, group: "EMA_b", groupLabel: "EMA (21)", data: [{ time: 1 as Time, value: 99 }] });

    renderLegends(chart, [ema1, ema2], null);

    expect(rows(els[0]).map((r) => r.firstElementChild?.textContent)).toEqual(["EMA (8)", "EMA (21)"]);
    expect(rows(els[1])).toHaveLength(0);
  });

  it("shows the value under the crosshair when hovering, the latest value when not", () => {
    const { chart, els } = makeChart(2);
    const series = { id: "s" } as unknown as LegendSeries["series"];
    const item = makeItem({ pane: paneRef(chart, 1) as never, series, data: [{ time: 1 as Time, value: 10 }, { time: 2 as Time, value: 20 }] });

    renderLegends(chart, [item], null);
    expect(text(rows(els[1])[0])).toEqual(["G", "20"]);

    renderLegends(chart, [item], hover([[series, 12.5]]));
    expect(text(rows(els[1])[0])).toEqual(["G", "12.5"]);

    renderLegends(chart, [item], hover([[series, null]])); // whitespace/gap under the cursor
    expect(text(rows(els[1])[0])).toEqual(["G", "—"]);
  });

  it("clears a pane's legend when its indicators are gone and never duplicates the container", () => {
    const { chart, els } = makeChart(2);
    const item = makeItem({ pane: paneRef(chart, 1) as never, data: [{ time: 1 as Time, value: 1 }] });

    renderLegends(chart, [item], null);
    renderLegends(chart, [item], null);
    expect(els[1].querySelectorAll(".chart-legend")).toHaveLength(1);
    expect(rows(els[1])).toHaveLength(1);

    renderLegends(chart, [], null);
    expect(rows(els[1])).toHaveLength(0);
  });
});
