/// <reference types="node" />
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { IChartApi, MouseEventParams, Time } from "lightweight-charts";
import { describe, expect, it, vi } from "vitest";

import { findGapRuns, gapRun, gapRunsBySlot } from "../../lib/gaps";
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

  it("reads 'no data · <duration>' on every row when the crosshair is over a gap slot (Story 32.1)", () => {
    const { chart, els } = makeChart(2);
    const series = { id: "s" } as unknown as LegendSeries["series"];
    const overlay = makeItem({ pane: null, group: "EMA", groupLabel: "EMA (8)", data: [{ time: 0 as Time, value: 1 }] });
    const item = makeItem({ pane: paneRef(chart, 1) as never, series, data: [{ time: 0 as Time, value: 10 }] });
    const price = [{ time: 0, open: 1 }, ...gapRun(0, 360, 60).map((time) => ({ time })), { time: 360, open: 1 }];
    const gaps = gapRunsBySlot(findGapRuns(price, (d) => "open" in d));

    renderLegends(chart, [overlay, item], { time: 180 as Time, seriesData: new Map() } as unknown as MouseEventParams<Time>, gaps);
    expect(text(rows(els[0])[0])).toEqual(["EMA (8)", "no data · 5m"]);
    expect(text(rows(els[1])[0])).toEqual(["G", "no data · 5m"]);

    renderLegends(chart, [item], hover([[series, 12.5]]), gaps); // a real slot still reads its value
    expect(text(rows(els[1])[0])).toEqual(["G", "12.5"]);
  });

  it("the latest value skips a long trailing gap run (720 whitespace slots)", () => {
    const { chart, els } = makeChart(2);
    const run = gapRun(60, 1e9, 60).map((time) => ({ time: time as Time }));
    const base = [{ time: 0 as Time, value: 7 }, { time: 60 as Time, value: 8 }];
    const item = makeItem({ pane: paneRef(chart, 1) as never, data: [...base, ...run] });

    renderLegends(chart, [item], null);
    const withRun = text(rows(els[1])[0]);
    renderLegends(chart, [makeItem({ pane: paneRef(chart, 1) as never, data: base })], null);

    expect(run).toHaveLength(720);
    expect(withRun).toEqual(text(rows(els[1])[0]));
    expect(withRun).toEqual(["G", "8"]);
  });
});

// Story 32.3: the row is the control surface. Each button is inline SVG with an aria-label, and
// one delegated click handler per legend reports (action, group).
describe("legend controls (Story 32.3)", () => {
  const buttons = (row: Element) => [...row.querySelectorAll("button")].map((b) => b.getAttribute("aria-label"));

  it("gives every row Hide, Settings and Remove buttons labelled with its title, as inline SVG", () => {
    const { chart, els } = makeChart(1);
    renderLegends(chart, [makeItem({ pane: null, groupLabel: "SMA (20)" })], null, undefined, () => {});

    const [row] = rows(els[0]);
    expect(buttons(row)).toEqual(["Hide SMA (20)", "Settings for SMA (20)", "Remove SMA (20)"]);
    expect([...row.querySelectorAll("button")].every((b) => b.querySelector("svg path") !== null)).toBe(true);
    expect(row.querySelector("button")?.getAttribute("type")).toBe("button"); // keyboard reachable, never a submit
  });

  it("gives the Volume row (configurable: false) the eye and the x only", () => {
    const { chart, els } = makeChart(1);
    renderLegends(chart, [makeItem({ pane: null, groupLabel: "Volume", configurable: false })], null, undefined, () => {});

    expect(buttons(rows(els[0])[0])).toEqual(["Hide Volume", "Remove Volume"]);
  });

  it("draws no buttons on a row no configured entry owns (actionable: false)", () => {
    const { chart, els } = makeChart(1);
    renderLegends(chart, [makeItem({ pane: null, actionable: false })], null, undefined, () => {});

    expect(rows(els[0])).toHaveLength(1);
    expect(els[0].querySelectorAll("button")).toHaveLength(0);
  });

  it("draws no buttons without an action handler, so the plain readout is unchanged", () => {
    const { chart, els } = makeChart(1);
    renderLegends(chart, [makeItem({ pane: null })], null);

    expect(els[0].querySelectorAll("button")).toHaveLength(0);
  });

  it("reports the action and the row's group on a click", () => {
    const { chart, els } = makeChart(1);
    const onAction = vi.fn();
    renderLegends(chart, [makeItem({ pane: null, group: "RSI_period=14", groupLabel: "RSI (14)" })], null, undefined, onAction);

    els[0].querySelector<HTMLElement>('button[aria-label="Settings for RSI (14)"]')!.click();
    els[0].querySelector<HTMLElement>('button[aria-label="Hide RSI (14)"] svg')!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    els[0].querySelector<HTMLElement>('button[aria-label="Remove RSI (14)"]')!.click();

    expect(onAction.mock.calls).toEqual([
      ["settings", "RSI_period=14"],
      ["hide", "RSI_period=14"],
      ["remove", "RSI_period=14"],
    ]);
  });

  it("keeps one handler per legend across re-renders (no stacked clicks)", () => {
    const { chart, els } = makeChart(1);
    const onAction = vi.fn();
    const item = makeItem({ pane: null, groupLabel: "G" });
    for (let i = 0; i < 3; i++) renderLegends(chart, [item], null, undefined, onAction);

    els[0].querySelector<HTMLElement>('button[aria-label="Remove G"]')!.click();

    expect(onAction).toHaveBeenCalledTimes(1);
  });

  it("a hidden row is dimmed, offers 'Show', and reads its latest value instead of following the crosshair", () => {
    const { chart, els } = makeChart(1);
    const series = { id: "s" } as unknown as LegendSeries["series"];
    const item = makeItem({ pane: null, series, hidden: true, groupLabel: "G", data: [{ time: 1 as Time, value: 10 }, { time: 2 as Time, value: 20 }] });

    renderLegends(chart, [item], hover([[series, 12.5]]), undefined, () => {});

    const [row] = rows(els[0]);
    expect(row.classList.contains("chart-legend-row--hidden")).toBe(true);
    expect(buttons(row)[0]).toBe("Show G");
    expect(text(row).slice(0, 2)).toEqual(["G", "20"]);
  });

  it("keeps a collapsed pane's row (series null) on the price pane's legend, crossed", () => {
    const { chart, els } = makeChart(2);
    const item = makeItem({ pane: null, series: null, hidden: true, groupLabel: "RSI (14)", data: [{ time: 1 as Time, value: 55 }] });

    renderLegends(chart, [item], null, undefined, () => {});

    expect(rows(els[0])).toHaveLength(1);
    expect(rows(els[1])).toHaveLength(0);
    expect(text(rows(els[0])[0]).slice(0, 2)).toEqual(["RSI (14)", "55"]);
  });

  it("updates values in place while the row is unchanged, so a button under the pointer survives", () => {
    const { chart, els } = makeChart(1);
    const series = { id: "s" } as unknown as LegendSeries["series"];
    const item = makeItem({ pane: null, series, groupLabel: "G", data: [{ time: 1 as Time, value: 1 }] });
    renderLegends(chart, [item], hover([[series, 5]]), undefined, () => {});
    const button = els[0].querySelector("button")!;

    renderLegends(chart, [item], hover([[series, 6]]), undefined, () => {});

    expect(els[0].querySelector("button")).toBe(button);
    expect(text(rows(els[0])[0]).slice(0, 2)).toEqual(["G", "6"]);

    renderLegends(chart, [{ ...item, hidden: true }], null, undefined, () => {}); // structure changed: rebuilt
    expect(els[0].querySelector("button")).not.toBe(button);
  });
});

// The legend strip itself stays click-through; only the row takes pointer events, so a drag or a
// wheel anywhere else on the pane still pans and zooms (Story 32.3). A CSS rule, so the test reads
// the stylesheet's text (vitest hands `.css?raw` back empty; Node's fs is the honest reader).
describe("legend CSS (Story 32.3)", () => {
  const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "..", "index.css"), "utf8");
  const theme = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "..", "theme.css"), "utf8");
  const rule = (selector: string): string => {
    const found = new RegExp(`(?:^|\\n)${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{([^}]*)\\}`).exec(css);
    if (!found) throw new Error(`index.css has no ${selector} rule`);
    return found[1];
  };

  it("keeps the legend strip click-through and gives only the row pointer events", () => {
    expect(rule(".chart-legend")).toMatch(/pointer-events:\s*none/);
    expect(rule(".chart-legend-row")).toMatch(/pointer-events:\s*auto/);
  });

  it("sizes each row to its own content, so a short row leaves no dead strip beside it", () => {
    expect(rule(".chart-legend")).toMatch(/align-items:\s*flex-start/);
  });

  it("puts the settings modal's padding on its body, so a click there is not a backdrop click", () => {
    expect(rule(".indicator-settings")).toMatch(/padding:\s*0/);
    expect(rule(".indicator-settings-body")).toMatch(/padding:\s*12px/);
  });

  it("sizes the legend from the one --legend-font-size token (14px)", () => {
    expect(theme).toMatch(/--legend-font-size:\s*14px/);
    expect(rule(".chart-legend")).toMatch(/font-size:\s*var\(--legend-font-size/);
  });

  it("shows the buttons on hover or focus without removing them from the tab order", () => {
    expect(rule(".chart-legend-actions")).toMatch(/opacity:\s*0/);
    expect(rule(".chart-legend-actions")).not.toMatch(/display:\s*none/);
    expect(css).toMatch(/\.chart-legend-row:focus-within \.chart-legend-actions/);
  });

  it("always shows the buttons on a screen without hover, so a tap never hits an unseen x", () => {
    expect(css).toMatch(/@media \(hover: none\)\s*\{\s*\.chart-legend-actions\s*\{\s*opacity:\s*1;/);
  });
});

describe("a legend row with its own number format (Story 32.7)", () => {
  it("prints the value through the row's format, not the plain readout", () => {
    const { chart, els } = makeChart(1);
    const vwap = makeItem({
      pane: null,
      series: null,
      group: "avwap-1",
      groupLabel: "AVWAP (hlc3)",
      data: [{ time: 0 as Time, value: 14.6 }],
      format: (value) => value.toFixed(4), // the instrument's precision
      actionable: false,
    });

    renderLegends(chart, [vwap], null);

    const [row] = rows(els[0]);
    expect(text(row)).toEqual(["AVWAP (hlc3)", "14.6000"]);
    expect(row.querySelector(".chart-legend-actions")).toBeNull();
  });

  it("falls back to the dash while the row has no value", () => {
    const { chart, els } = makeChart(1);
    const vwap = makeItem({ pane: null, series: null, data: [], format: () => "never" });

    renderLegends(chart, [vwap], null);

    expect(text(rows(els[0])[0])).toEqual(["G", "—"]);
  });
});

describe("the Footprint legend row (Story 32.8)", () => {
  it("has the gear and the x but no eye, and shows its fixed readout over a gap too", () => {
    const { chart, els } = makeChart(1);
    const footprint = makeItem({
      pane: null,
      series: null,
      group: "footprint",
      groupLabel: "Footprint",
      text: "bid×ask · auto rows",
      hideable: false,
    });
    const onAction = vi.fn();
    // Slot 120 of a 60 s series is a gap.
    const price = [{ time: 60, open: 1 }, { time: 120 }, { time: 180, open: 1 }];
    const gaps = gapRunsBySlot(findGapRuns(price, (d) => "open" in d));

    renderLegends(chart, [footprint], { time: 120 as Time, seriesData: new Map() } as unknown as MouseEventParams<Time>, gaps, onAction);

    const [row] = rows(els[0]);
    expect([...row.querySelectorAll("button")].map((b) => b.getAttribute("aria-label"))).toEqual([
      "Settings for Footprint",
      "Remove Footprint",
    ]);
    expect(text(row).slice(0, 2)).toEqual(["Footprint", "bid×ask · auto rows"]);
    row.querySelector<HTMLElement>('button[aria-label="Remove Footprint"]')!.click();
    expect(onAction).toHaveBeenCalledWith("remove", "footprint");
  });
});
