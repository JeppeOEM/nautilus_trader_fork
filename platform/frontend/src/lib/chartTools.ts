// Story 18.1 / quick-dev 2026-10-05: the chart's left-rail tools and their TradingView-style groups,
// as data. `components/chart/ToolRail.tsx` renders them; nothing there changes for a new tool.

// Story 18.1 (AC #1): the chart's drawing-tool state -- "cursor" is the inert default.
// Later stories extend this union with their tools, never a second state variable.
export type ChartTool =
  | "cursor"
  | "hline"
  | "trendline"
  | "fib"
  | "long"
  | "short"
  | "measure"
  | "frvp"
  | "avp"
  | "avwap"
  // Story 33.10
  | "ray"
  | "extended"
  | "vline"
  | "channel"
  | "fib_extension"
  | "rect"
  | "text"
  | "arrow"
  | "price_range"
  | "date_range";

export interface ChartToolDef {
  id: ChartTool;
  label: string;
  ariaLabel: string;
  /** Hover tooltip, where the label alone does not say what the button does. */
  title?: string;
  /** No meaning in Lines mode (no single main series to attach to -- spec Task 2's
   * MVP scope decision): disables the tool there and disarms an armed one (see the
   * mode-guard effect in ChartPage). */
  candlesOnly: boolean;
  /** Places a drawing that is saved with the coin: off until the coin's drawings have loaded (a
   * placement before then would be overwritten by the load, or overwrite the server's list). */
  placesDrawing?: boolean;
  /** Labels its prices at the instrument's precision: off until the first candles response has
   * carried it, never at a guessed one. */
  needsPrecision?: boolean;
}

export interface ToolGroupDef {
  id: string;
  /** The group's name: its flyout menu is labelled by it. */
  label: string;
  /** The group's tools in menu order; the first is the one shown before any is used. */
  tools: readonly ChartToolDef[];
}

const CURSOR: ChartToolDef = {
  id: "cursor",
  label: "Cursor",
  ariaLabel: "Cursor tool",
  // The disarm, and the one mode in which drawings can be edited (Story 32.3).
  title: "Select / edit drawings (Esc)",
  candlesOnly: false,
};

// TradingView-style grouped rail: every group is one rail button showing its last-used tool, with a
// flyout menu of the rest. The rail markup never changes shape for a new tool: Story 33.10 appended
// its ray, extended line, rectangle, text, Fib extension and the like to these groups' `tools`.
// The cursor group comes first; the crosshair toggle (a view option, not an exclusive tool) is drawn
// right after it, then the drawing groups.
export const CURSOR_GROUP: ToolGroupDef = { id: "cursor", label: "Cursor", tools: [CURSOR] };

export const DRAWING_GROUPS: readonly ToolGroupDef[] = [
  {
    id: "lines",
    label: "Lines",
    tools: [
      { id: "trendline", label: "Trend", ariaLabel: "Trendline tool", candlesOnly: false, placesDrawing: true },
      // Story 33.10: two clicks each (a ray runs on past B, an extended line past both anchors).
      { id: "ray", label: "Ray", ariaLabel: "Ray tool", title: "Ray: click A, then a point it runs through", candlesOnly: false, placesDrawing: true },
      {
        id: "extended",
        label: "Ext",
        ariaLabel: "Extended line tool",
        title: "Extended line: click two points it runs through",
        candlesOnly: false,
        placesDrawing: true,
      },
      { id: "hline", label: "HLine", ariaLabel: "Horizontal line tool", candlesOnly: true, placesDrawing: true },
      { id: "vline", label: "VLine", ariaLabel: "Vertical line tool", title: "Vertical line: click a bar", candlesOnly: false, placesDrawing: true },
      {
        id: "channel",
        label: "Chan",
        ariaLabel: "Parallel channel tool",
        title: "Parallel channel: click A and B, then the parallel's offset",
        candlesOnly: false,
        placesDrawing: true,
      },
    ],
  },
  {
    id: "fibonacci",
    label: "Fibonacci",
    tools: [
      {
        id: "fib",
        label: "Fib",
        ariaLabel: "Fibonacci retracement tool",
        title: "Fibonacci retracement: drag from anchor A to anchor B",
        candlesOnly: false,
        placesDrawing: true,
        needsPrecision: true,
      },
      {
        id: "fib_extension",
        label: "FibExt",
        ariaLabel: "Fibonacci extension tool",
        title: "Trend-based Fibonacci extension: click A, B, then C",
        candlesOnly: false,
        placesDrawing: true,
        needsPrecision: true,
      },
    ],
  },
  {
    id: "projection",
    label: "Projection",
    tools: [
      {
        id: "long",
        label: "Long",
        ariaLabel: "Long position tool",
        title: "Long position: click the entry price",
        candlesOnly: false,
        placesDrawing: true,
        needsPrecision: true,
      },
      {
        id: "short",
        label: "Short",
        ariaLabel: "Short position tool",
        title: "Short position: click the entry price",
        candlesOnly: false,
        placesDrawing: true,
        needsPrecision: true,
      },
    ],
  },
  {
    // Story 33.10: annotations, placed by clicks and saved with the coin.
    id: "shapes",
    label: "Shapes / Annotation",
    tools: [
      { id: "rect", label: "Rect", ariaLabel: "Rectangle tool", title: "Rectangle: click two opposite corners", candlesOnly: false, placesDrawing: true },
      {
        id: "text",
        label: "Text",
        ariaLabel: "Text tool",
        title: "Text: click where it goes, then type it",
        candlesOnly: false,
        placesDrawing: true,
      },
      { id: "arrow", label: "Arrow", ariaLabel: "Arrow tool", title: "Arrow: click its tail, then its head", candlesOnly: false, placesDrawing: true },
    ],
  },
  {
    id: "measure",
    label: "Measure",
    tools: [
      { id: "measure", label: "Measure", ariaLabel: "Measurement tool", candlesOnly: true },
      // Story 33.10: saved measurements, read from the candle bars like the Measure tool.
      {
        id: "price_range",
        label: "PRange",
        ariaLabel: "Price range tool",
        title: "Price range: click two points",
        candlesOnly: true,
        placesDrawing: true,
        needsPrecision: true,
      },
      {
        id: "date_range",
        label: "DRange",
        ariaLabel: "Date range tool",
        title: "Date range: click two bars",
        candlesOnly: true,
        placesDrawing: true,
        needsPrecision: true,
      },
    ],
  },
  {
    id: "volume",
    label: "Volume-based",
    tools: [
      { id: "frvp", label: "FRVP", ariaLabel: "Fixed range volume profile tool", candlesOnly: true },
      // Story 32.7: single-click drawings, saved with the coin's drawings; both read the candle bars.
      {
        id: "avp",
        label: "AVP",
        ariaLabel: "Anchored volume profile tool",
        title: "Anchored volume profile: click the bar it starts at",
        candlesOnly: true,
        placesDrawing: true,
      },
      {
        id: "avwap",
        label: "AVWAP",
        ariaLabel: "Anchored VWAP tool",
        title: "Anchored VWAP: click the bar it starts at",
        candlesOnly: true,
        placesDrawing: true,
        needsPrecision: true,
      },
    ],
  },
];

export const TOOL_GROUPS: readonly ToolGroupDef[] = [CURSOR_GROUP, ...DRAWING_GROUPS];

/** The group a tool belongs to (every tool is in exactly one). */
export function groupOfTool(tool: ChartTool): ToolGroupDef | undefined {
  return TOOL_GROUPS.find((g) => g.tools.some((t) => t.id === tool));
}

/** A tool's definition by id (every `ChartTool` is declared in one group). */
export function toolDef(tool: ChartTool): ChartToolDef {
  const def = TOOL_GROUPS.flatMap((g) => g.tools).find((t) => t.id === tool);
  if (!def) throw new Error(`ToolRail: no rail group declares the tool ${tool}`);
  return def;
}

/** The tool a group's rail button shows: the remembered one when it is still in the group, else the first. */
export function shownTool(group: ToolGroupDef, lastUsed: Partial<Record<string, ChartTool>>): ChartToolDef {
  return group.tools.find((t) => t.id === lastUsed[group.id]) ?? group.tools[0];
}
