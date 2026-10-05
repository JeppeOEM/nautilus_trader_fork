import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  PrimitivePaneViewZOrder,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";

import type { FootprintItem } from "../../../api/schema";
import type { FootprintSettings } from "../../../lib/chartLayout";
import type { InstrumentPrecision } from "../../../lib/drawings";
import { type RowImbalance, cellText, diagonalImbalances, footerText, maxRowTotal, rowTotal } from "../../../lib/footprint";
import { unitsToNumber } from "../../../lib/units";
import { chartVar } from "../chartTheme";
import { GAP_FILL_ALPHA } from "./GapPrimitive";

// Transitive fancy-canvas type, derived rather than imported (not a direct dependency).
type CanvasRenderingTarget2D = Parameters<IPrimitivePaneRenderer["draw"]>[0];

// Story 32.8: below this bar spacing (px) a column cannot hold "sell × buy" text legibly, so every
// number (cells and footer) hides and the cells stay as heat only.
export const FOOTPRINT_TEXT_MIN_BAR_SPACING = 40;
// A row shorter than this (px) holds no text either, whatever the bar spacing.
const TEXT_MIN_ROW_PX = 9;
const TEXT_MAX_FONT_PX = 11;
// A bar's column takes this share of the bar spacing, leaving a gutter between neighbours.
const COLUMN_WIDTH_RATIO = 0.9;
// The veil over the candle beneath (the chart background at this alpha) dims it without hiding it.
export const FOOTPRINT_VEIL_ALPHA = 0.55;
// Heat: a cell's alpha grows with its row's total against the bar's fullest row.
const HEAT_MIN_ALPHA = 0.12;
const HEAT_MAX_ALPHA = 0.7;
const FOOTER_LINE_PX = 12;

/** Everything the footprint draws: the hook's bars, their precisions and the layout's settings. */
export interface FootprintRenderSpec {
  items: readonly FootprintItem[];
  /** The response's precisions; null until the first answer (nothing is drawn without them). */
  precision: InstrumentPrecision | null;
  settings: FootprintSettings;
}

export interface CellLayout {
  /** Top and bottom of the row in pane px (the row's upper price edge is the next row's floor). */
  top: number;
  bottom: number;
  text: string;
  /** Row total / the bar's fullest row, 0..1. */
  heat: number;
  delta: number;
  isPoc: boolean;
  imbalance: RowImbalance;
}

export interface BarLayout {
  x: number;
  width: number;
  /** A bar the archive holds no trade for: drawn as a gap, never as zero cells. */
  noTrades: boolean;
  cells: CellLayout[];
  footer: { delta: string; total: string; positive: boolean } | null;
}

interface Scales {
  timeToX: (time: number) => number | null;
  priceToY: (price: number) => number | null;
  barSpacing: number;
}

/** One bar's column: rows placed by their integer price edges, text through `lib/units.ts`. */
export function layoutBar(item: FootprintItem, spec: FootprintRenderSpec, precision: InstrumentPrecision, scales: Scales): BarLayout | null {
  const x = scales.timeToX(item.t / 1000);
  if (x === null) return null;
  const width = Math.max(1, scales.barSpacing * COLUMN_WIDTH_RATIO);
  if (item.no_trades || item.row_ticks === null) return { x, width, noTrades: true, cells: [], footer: null };
  const ticks = item.row_ticks;
  const max = maxRowTotal(item);
  const imbalances = diagonalImbalances(item.rows, ticks, spec.settings.imbalance_ratio);
  const cells: CellLayout[] = [];
  item.rows.forEach((row, i) => {
    // Integer units -> a coordinate only; every printed number stays `formatUnits`.
    const top = scales.priceToY(unitsToNumber(row.p + ticks, precision.price));
    const bottom = scales.priceToY(unitsToNumber(row.p, precision.price));
    if (top === null || bottom === null) return;
    cells.push({
      top: Math.min(top, bottom),
      bottom: Math.max(top, bottom),
      text: cellText(row, spec.settings.mode, precision.size),
      heat: max === 0 ? 0 : rowTotal(row) / max,
      delta: row.b - row.s,
      isPoc: row.p === item.poc_row,
      imbalance: imbalances[i],
    });
  });
  const footer = footerText(item, precision.size);
  return { x, width, noTrades: false, cells, footer: footer && { ...footer, positive: (item.delta ?? 0) >= 0 } };
}

/**
 * Story 32.8: the volume footprint of the chart's closed bars, one primitive on the candle series
 * (Candles mode only), drawn above the candles: per bar a column of cells, one per traded price row
 * (sell on the left half, buy on the right, shaded on a heat scale of the bar's fullest row), the
 * POC row outlined, diagonal imbalances outlined in `--chart-up` (buy) / `--chart-down` (sell), and
 * a footer with the bar's delta and total under its lowest row. A translucent veil in the chart
 * background dims the candle beneath. A `no_trades` bar is painted in `--chart-gap` (Story 32.1),
 * never as zeros; a bar with no item (the forming bar, or one not loaded yet) draws nothing.
 *
 * Known limit (historical only): only closed, settled bars have a footprint, so the newest ~7
 * minutes never show one. Upgrade path: fold the `trades` Redis stream in the candles context.
 */
export class FootprintPrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null;
  private series: ISeriesApi<"Candlestick"> | null = null;
  private requestUpdate: (() => void) | null = null;
  private spec: FootprintRenderSpec;
  private bars: BarLayout[] = [];
  private barSpacing = 0;
  private readonly view: IPrimitivePaneView = {
    // Above the candles: the cells are the reading, the veil keeps the candle visible under them.
    zOrder: (): PrimitivePaneViewZOrder => "top",
    renderer: (): IPrimitivePaneRenderer | null => this.renderer(),
  };

  constructor(spec: FootprintRenderSpec) {
    this.spec = spec;
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this.chart = param.chart as IChartApi;
    this.series = param.series as ISeriesApi<"Candlestick">;
    this.requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this.chart = null;
    this.series = null;
    this.requestUpdate = null;
    this.bars = [];
  }

  update(spec: FootprintRenderSpec): void {
    if (spec === this.spec) return;
    this.spec = spec;
    this.requestUpdate?.();
  }

  updateAllViews(): void {
    const timeScale = this.chart?.timeScale();
    const { series } = this;
    const precision = this.spec.precision;
    const visible = timeScale?.getVisibleRange() ?? null;
    if (!timeScale || !series || precision === null || visible === null) {
      this.bars = [];
      return;
    }
    this.barSpacing = timeScale.options().barSpacing;
    const scales: Scales = {
      timeToX: (time) => timeScale.timeToCoordinate(time as Time),
      priceToY: (price) => series.priceToCoordinate(price),
      barSpacing: this.barSpacing,
    };
    // Only the visible bars are laid out (this runs on every pan/zoom frame), plus one neighbour on
    // each side: a bar whose start is just off screen still has part of its column on it.
    const items = this.spec.items;
    const from = visible.from as number;
    const to = visible.to as number;
    const first = items.findIndex((item) => item.t / 1000 >= from);
    const last = items.findLastIndex((item) => item.t / 1000 <= to);
    const lo = Math.max(0, (first === -1 ? items.length : first) - 1);
    const hi = Math.min(items.length - 1, last + 1);
    const bars: BarLayout[] = [];
    for (let k = lo; k <= hi; k++) {
      const bar = layoutBar(items[k], this.spec, precision, scales);
      if (bar) bars.push(bar);
    }
    this.bars = bars;
  }

  /** The laid-out visible bars, for tests. */
  layout(): readonly BarLayout[] {
    return this.bars;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  private renderer(): IPrimitivePaneRenderer | null {
    if (this.bars.length === 0) return null;
    const { bars, barSpacing } = this;
    const { settings } = this.spec;
    const showText = settings.text && barSpacing >= FOOTPRINT_TEXT_MIN_BAR_SPACING;
    const colors = {
      buy: settings.buy_color ?? chartVar("--chart-up"),
      sell: settings.sell_color ?? chartVar("--chart-down"),
      up: chartVar("--chart-up"),
      down: chartVar("--chart-down"),
      neutral: chartVar("--chart-text-dim"),
      text: chartVar("--chart-text"),
      bg: chartVar("--chart-bg"),
      poc: chartVar("--chart-poc"),
      gap: chartVar("--chart-gap"),
    };
    return {
      draw: (target: CanvasRenderingTarget2D): void => {
        target.useBitmapCoordinateSpace(({ context, horizontalPixelRatio: hr, verticalPixelRatio: vr, bitmapSize }) => {
          const px = { hr, vr, height: bitmapSize.height };
          for (const bar of bars) {
            if (bar.noTrades) drawGap(context, bar, colors.gap, px);
            else drawBar(context, bar, settings, colors, showText, px);
          }
        });
      },
    };
  }
}

type Colors = Record<"buy" | "sell" | "up" | "down" | "neutral" | "text" | "bg" | "poc" | "gap", string>;
interface Px {
  hr: number;
  vr: number;
  height: number;
}

function drawGap(context: CanvasRenderingContext2D, bar: BarLayout, color: string, px: Px): void {
  context.globalAlpha = GAP_FILL_ALPHA;
  context.fillStyle = color;
  context.fillRect((bar.x - bar.width / 2) * px.hr, 0, bar.width * px.hr, px.height);
  context.globalAlpha = 1;
}

/** A cell's fill colour per display mode: the two sides in bid×ask, the delta's sign otherwise. */
function cellFills(cell: CellLayout, mode: FootprintSettings["mode"], colors: Colors): [string, string] {
  if (mode === "bid_ask") return [colors.sell, colors.buy];
  if (mode === "volume") return [colors.neutral, colors.neutral];
  const fill = cell.delta >= 0 ? colors.buy : colors.sell;
  return [fill, fill];
}

function drawBar(
  context: CanvasRenderingContext2D,
  bar: BarLayout,
  settings: FootprintSettings,
  colors: Colors,
  showText: boolean,
  { hr, vr }: Px,
): void {
  // No row on the price scale (all off the pane): nothing to anchor the veil or the footer to.
  if (bar.cells.length === 0) return;
  const left = bar.x - bar.width / 2;
  const half = bar.width / 2;
  const top = Math.min(...bar.cells.map((c) => c.top));
  const bottom = Math.max(...bar.cells.map((c) => c.bottom));
  context.globalAlpha = FOOTPRINT_VEIL_ALPHA;
  context.fillStyle = colors.bg;
  context.fillRect(left * hr, top * vr, bar.width * hr, (bottom - top) * vr);
  for (const cell of bar.cells) {
    const y = cell.top * vr;
    const h = Math.max(1, cell.bottom - cell.top) * vr;
    const [sellFill, buyFill] = cellFills(cell, settings.mode, colors);
    context.globalAlpha = HEAT_MIN_ALPHA + (HEAT_MAX_ALPHA - HEAT_MIN_ALPHA) * cell.heat;
    context.fillStyle = sellFill;
    context.fillRect(left * hr, y, half * hr, h);
    context.fillStyle = buyFill;
    context.fillRect((left + half) * hr, y, half * hr, h);
    context.globalAlpha = 1;
    // Sell imbalance on the left half, buy imbalance on the right, in every mode.
    context.lineWidth = hr;
    if (cell.imbalance.sell) {
      context.strokeStyle = colors.down;
      context.strokeRect(left * hr, y, half * hr, h);
    }
    if (cell.imbalance.buy) {
      context.strokeStyle = colors.up;
      context.strokeRect((left + half) * hr, y, half * hr, h);
    }
    if (cell.isPoc) {
      context.strokeStyle = colors.poc;
      context.lineWidth = 2 * hr;
      context.strokeRect(left * hr, y, bar.width * hr, h);
    }
    if (showText) drawCellText(context, cell, bar, colors, hr, vr);
  }
  if (showText && bar.footer) drawFooter(context, bar, bottom, colors, hr, vr);
}

function drawCellText(context: CanvasRenderingContext2D, cell: CellLayout, bar: BarLayout, colors: Colors, hr: number, vr: number): void {
  const rowPx = cell.bottom - cell.top;
  if (rowPx < TEXT_MIN_ROW_PX) return;
  context.font = `${Math.min(TEXT_MAX_FONT_PX, rowPx - 2) * vr}px sans-serif`;
  // A number wider than its column is not drawn rather than overprinting the neighbours.
  if (context.measureText(cell.text).width > (bar.width - 2) * hr) return;
  context.fillStyle = cell.imbalance.buy ? colors.up : cell.imbalance.sell ? colors.down : colors.text;
  context.textAlign = "center";
  context.textBaseline = "middle";
  context.fillText(cell.text, bar.x * hr, ((cell.top + cell.bottom) / 2) * vr);
}

function drawFooter(context: CanvasRenderingContext2D, bar: BarLayout, bottom: number, colors: Colors, hr: number, vr: number): void {
  const footer = bar.footer;
  if (!footer) return;
  context.font = `${(FOOTER_LINE_PX - 2) * vr}px sans-serif`;
  context.textAlign = "center";
  context.textBaseline = "top";
  context.fillStyle = footer.positive ? colors.up : colors.down;
  context.fillText(footer.delta, bar.x * hr, (bottom + 2) * vr);
  context.fillStyle = colors.text;
  context.fillText(footer.total, bar.x * hr, (bottom + 2 + FOOTER_LINE_PX) * vr);
}
