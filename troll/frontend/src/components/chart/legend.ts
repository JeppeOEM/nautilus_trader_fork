import type { IChartApi, IPaneApi, ISeriesApi, MouseEventParams, Time } from "lightweight-charts";

import type { IndicatorDatum } from "../../hooks/useIndicatorSeries";

// Spec §A4.1's chart legend: a text strip at the top-left of whichever pane an indicator
// lives in -- overlays stack in the price pane's corner, a non-overlay indicator heads its
// own pane. Each output's value is drawn in that output's line color, following the
// crosshair (latest value when the cursor is off the chart).

export interface LegendSeries {
  /** Outputs of one indicator share a group -- one legend row, one pane. */
  group: string;
  groupLabel: string;
  outputLabel: string;
  color: string;
  series: ISeriesApi<"Line", Time> | ISeriesApi<"Histogram", Time>;
  data: IndicatorDatum[];
  /** null = overlay in the price pane. */
  pane: IPaneApi<Time> | null;
}

export function formatLegendValue(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return Math.abs(value) >= 100 ? value.toFixed(2) : Number(value.toPrecision(4)).toString();
}

function latestValue(data: IndicatorDatum[]): number | null {
  for (let i = data.length - 1; i >= 0; i--) {
    const point = data[i];
    if ("value" in point) return point.value;
  }
  return null;
}

function valueAt(item: LegendSeries, param: MouseEventParams<Time> | null): number | null {
  if (!param || param.time === undefined) return latestValue(item.data);
  const point = param.seriesData.get(item.series);
  return point && "value" in point ? point.value : null;
}

function legendContainer(paneEl: HTMLElement): HTMLElement {
  let el = paneEl.querySelector<HTMLElement>(":scope > .chart-legend");
  if (!el) {
    el = document.createElement("div");
    el.className = "chart-legend";
    if (getComputedStyle(paneEl).position === "static") paneEl.style.position = "relative";
    paneEl.appendChild(el);
  }
  return el;
}

function legendRow(groupLabel: string, members: LegendSeries[], param: MouseEventParams<Time> | null): HTMLElement {
  const row = document.createElement("div");
  row.className = "chart-legend-row";
  const title = document.createElement("span");
  title.className = "chart-legend-title";
  title.textContent = groupLabel;
  row.appendChild(title);
  for (const member of members) {
    const value = document.createElement("span");
    value.className = "chart-legend-value";
    value.style.color = member.color;
    value.title = member.outputLabel;
    value.textContent = formatLegendValue(valueAt(member, param));
    row.appendChild(value);
  }
  return row;
}

/**
 * Rebuilds every pane's legend. Cheap (a handful of rows), so no incremental diffing.
 * Returns false if some pane has no DOM element yet -- the library creates a freshly added
 * pane's element lazily, at its next paint -- so the caller can retry on the next frame.
 */
export function renderLegends(chart: IChartApi, items: LegendSeries[], param: MouseEventParams<Time> | null): boolean {
  let allRendered = true;
  chart.panes().forEach((pane, index) => {
    const paneEl = pane.getHTMLElement();
    if (!paneEl) {
      allRendered = false;
      return;
    }
    const container = legendContainer(paneEl);
    container.replaceChildren();
    const groups = new Map<string, LegendSeries[]>();
    for (const item of items) {
      // overlays (pane === null) belong to pane 0
      const owner = item.pane ? item.pane.paneIndex() : 0;
      if (owner !== index) continue;
      groups.set(item.group, [...(groups.get(item.group) ?? []), item]);
    }
    for (const [, members] of groups) container.appendChild(legendRow(members[0].groupLabel, members, param));
  });
  return allRendered;
}
