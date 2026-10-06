import type {
  IChartApi,
  IPaneApi,
  ISeriesApi,
  LineData,
  MouseEventParams,
  Time,
  WhitespaceData,
} from "lightweight-charts";

import { type GapRun, gapLabel } from "../../lib/gaps";

// Spec §A4.1's chart legend: a text strip at the top-left of whichever pane an indicator
// lives in -- overlays stack in the price pane's corner, a non-overlay indicator heads its
// own pane. Each output's value is drawn in that output's line color, following the
// crosshair (latest value when the cursor is off the chart). Over a gap slot (Story 32.1) every
// value reads "no data · <duration>" instead of "—": the legend is the chart's crosshair readout.
// Story 32.3: the row is also the indicator's control surface -- an eye (hide/show), a gear
// (settings modal; not for Volume) and an x (remove), inline SVG, wired by one delegated click
// handler per legend. A hidden indicator keeps its row, dimmed and crossed, reading its latest
// value (it does not follow the crosshair).

/** One point of an indicator line: a value, or whitespace (a gap or a not-yet-defined value),
 * passed straight through to lightweight-charts, never filtered or reshaped. */
export type IndicatorDatum = LineData<Time> | WhitespaceData<Time>;

/** Slot time (chart seconds) -> the gap run it belongs to, from the price series. */
export type GapLookup = ReadonlyMap<number, GapRun>;

export type LegendAction = "hide" | "settings" | "remove";

export interface LegendSeries {
  /** Outputs of one indicator share a group -- one legend row, one pane. For a picker
   * indicator this is its instance id, the id the row's buttons act on. */
  group: string;
  groupLabel: string;
  outputLabel: string;
  color: string;
  /** null while the indicator's pane is collapsed (hidden): the row has no series to read. */
  series: ISeriesApi<"Line", Time> | ISeriesApi<"Histogram", Time> | null;
  data: IndicatorDatum[];
  /** null = overlay in the price pane (or a collapsed pane, whose row lives there). */
  pane: IPaneApi<Time> | null;
  hidden?: boolean;
  /** False for Volume: eye and x only, no settings. Default true. */
  configurable?: boolean;
  /** How the row's number is printed, where the plain readout would lose the instrument's precision
   * (Story 32.7's Anchored VWAP prints through `lib/units.ts`). */
  format?: (value: number) => string;
  /** False when no configured entry owns the row: no buttons at all. Default true. */
  actionable?: boolean;
  /** False for a row with nothing to hide in place (Story 32.8's Footprint: gear and x only). Default true. */
  hideable?: boolean;
  /** A fixed readout shown instead of a value (the Footprint's mode and row size). */
  text?: string;
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
  if (item.hidden || !item.series || !param || param.time === undefined) return latestValue(item.data);
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

function gapAt(param: MouseEventParams<Time> | null, gaps: GapLookup | undefined): GapRun | undefined {
  return param?.time === undefined ? undefined : gaps?.get(param.time as number);
}

const SVG_NS = "http://www.w3.org/2000/svg";

// Inline SVG, no icon library. Stroke icons in currentColor, so the one CSS rule colours them.
const ICON_PATHS: Record<LegendAction | "hidden", string[]> = {
  hide: ["M1 8s2.5-5 7-5 7 5 7 5-2.5 5-7 5-7-5-7-5z", "M8 5.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z"],
  // The crossed eye: the same eye plus the slash.
  hidden: ["M1 8s2.5-5 7-5 7 5 7 5-2.5 5-7 5-7-5-7-5z", "M8 5.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z", "M2 14L14 2"],
  settings: [
    "M8 5.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z",
    "M8 1v2M8 13v2M1 8h2M13 8h2M3 3l1.4 1.4M11.6 11.6L13 13M3 13l1.4-1.4M11.6 4.4L13 3",
  ],
  remove: ["M3 3l10 10M13 3L3 13"],
};

function iconButton(action: LegendAction, label: string, crossed = false): HTMLButtonElement {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "chart-legend-btn";
  button.dataset.action = action;
  button.setAttribute("aria-label", label);
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("width", "14");
  svg.setAttribute("height", "14");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.5");
  for (const d of ICON_PATHS[action === "hide" && crossed ? "hidden" : action]) {
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", d);
    svg.appendChild(path);
  }
  button.appendChild(svg);
  return button;
}

interface RowModel {
  group: string;
  title: string;
  members: LegendSeries[];
  hidden: boolean;
  configurable: boolean;
  actionable: boolean;
  hideable: boolean;
}

/** Everything about a row except its values: when it is unchanged the row is updated in place, so
 * a button the pointer is on is never rebuilt between mousedown and click. */
function rowSignature(row: RowModel, actions: boolean): string {
  return JSON.stringify([
    row.group,
    row.title,
    row.hidden,
    row.configurable,
    row.actionable,
    row.hideable,
    actions,
    row.members.map((m) => [m.outputLabel, m.color]),
  ]);
}

function valueText(member: LegendSeries, param: MouseEventParams<Time> | null, gap: GapRun | undefined): string {
  if (member.text !== undefined) return member.text;
  if (gap && !member.hidden) return gapLabel(gap);
  const value = valueAt(member, param);
  return value !== null && member.format ? member.format(value) : formatLegendValue(value);
}

function legendRow(row: RowModel, param: MouseEventParams<Time> | null, gap: GapRun | undefined, actions: boolean): HTMLElement {
  const el = document.createElement("div");
  el.className = row.hidden ? "chart-legend-row chart-legend-row--hidden" : "chart-legend-row";
  el.dataset.group = row.group;
  el.dataset.sig = rowSignature(row, actions);
  const title = document.createElement("span");
  title.className = "chart-legend-title";
  title.textContent = row.title;
  el.appendChild(title);
  for (const member of row.members) {
    const value = document.createElement("span");
    value.className = "chart-legend-value";
    value.style.color = member.color;
    value.title = member.outputLabel;
    value.textContent = valueText(member, param, gap);
    el.appendChild(value);
  }
  if (actions && row.actionable) {
    const bar = document.createElement("span");
    bar.className = "chart-legend-actions";
    if (row.hideable) bar.appendChild(iconButton("hide", `${row.hidden ? "Show" : "Hide"} ${row.title}`, row.hidden));
    if (row.configurable) bar.appendChild(iconButton("settings", `Settings for ${row.title}`));
    bar.appendChild(iconButton("remove", `Remove ${row.title}`));
    el.appendChild(bar);
  }
  return el;
}

function updateValues(el: Element, row: RowModel, param: MouseEventParams<Time> | null, gap: GapRun | undefined): void {
  const values = el.querySelectorAll<HTMLElement>(".chart-legend-value");
  row.members.forEach((member, i) => {
    const text = valueText(member, param, gap);
    if (values[i] && values[i].textContent !== text) values[i].textContent = text;
  });
}

/**
 * Renders every pane's legend. Cheap (a handful of rows). A pane whose rows are structurally
 * unchanged only has its value text updated; otherwise its rows are rebuilt.
 * `onAction` (the chart's eye/gear/x, by row group) turns the buttons on; without it the legend
 * is the plain readout. Returns false if some pane has no DOM element yet -- the library creates
 * a freshly added pane's element lazily, at its next paint -- so the caller can retry on the
 * next frame.
 */
export function renderLegends(
  chart: IChartApi,
  items: LegendSeries[],
  param: MouseEventParams<Time> | null,
  gaps?: GapLookup,
  onAction?: (action: LegendAction, group: string) => void,
): boolean {
  let allRendered = true;
  const gap = gapAt(param, gaps);
  chart.panes().forEach((pane, index) => {
    const paneEl = pane.getHTMLElement();
    if (!paneEl) {
      allRendered = false;
      return;
    }
    const container = legendContainer(paneEl);
    // One delegated handler (re-assigned, never stacked): the buttons themselves are rebuilt freely.
    container.onclick = onAction
      ? (event) => {
          const button = (event.target as Element).closest<HTMLElement>("button[data-action]");
          const group = button?.closest<HTMLElement>(".chart-legend-row")?.dataset.group;
          if (button && group !== undefined) onAction(button.dataset.action as LegendAction, group);
        }
      : null;
    const groups = new Map<string, LegendSeries[]>();
    for (const item of items) {
      // overlays (pane === null) belong to pane 0
      const owner = item.pane ? item.pane.paneIndex() : 0;
      if (owner !== index) continue;
      groups.set(item.group, [...(groups.get(item.group) ?? []), item]);
    }
    const models: RowModel[] = [...groups].map(([group, members]) => ({
      group,
      title: members[0].groupLabel,
      members,
      hidden: members[0].hidden === true,
      configurable: members[0].configurable !== false,
      actionable: members[0].actionable !== false,
      hideable: members[0].hideable !== false,
    }));
    const existing = [...container.children] as HTMLElement[];
    const same =
      existing.length === models.length &&
      models.every((m, i) => existing[i].dataset.sig === rowSignature(m, onAction !== undefined));
    if (same) models.forEach((m, i) => updateValues(existing[i], m, param, gap));
    else container.replaceChildren(...models.map((m) => legendRow(m, param, gap, onAction !== undefined)));
  });
  return allRendered;
}
