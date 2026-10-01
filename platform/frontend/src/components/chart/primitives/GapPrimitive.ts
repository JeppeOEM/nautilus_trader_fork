import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesPrimitive,
  PrimitivePaneViewZOrder,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";

import { type GapRun, gapLabel } from "../../../lib/gaps";

// Transitive fancy-canvas type, derived rather than imported (not a direct dependency).
type CanvasRenderingTarget2D = Parameters<IPrimitivePaneRenderer["draw"]>[0];

// Story 32.1: a gap slot's fill is translucent so the grid under it stays visible.
export const GAP_FILL_ALPHA = 0.28;
// One placeholder bar is as wide as a candle body at the current zoom (lightweight-charts
// draws a candle body at roughly 80 % of the bar spacing), never narrower than one pixel.
const GAP_BAR_WIDTH_RATIO = 0.8;
const LABEL_FONT_SIZE_PX = 11;
const LABEL_TOP_PX = 4;
const LABEL_LEFT_PADDING_PX = 3;

export interface GapPrimitiveOptions {
  /** Draw "no data · <duration>" at each run's first slot (the price pane only). */
  label: boolean;
  fontFamily?: string;
}

interface SlotLayout {
  x: number;
  /** Only a run's first slot carries its label. */
  label: string | null;
}

/**
 * Story 32.1: paints every gap slot of its pane -- the whitespace points a hole takes, one per
 * missing interval -- as a full-height translucent placeholder bar in the dedicated gap colour,
 * so a hole reads as its real length instead of one missing candle. The slots stay native
 * whitespace data (AD-F6); this only draws on top. Same family as `VerticalMarkerPrimitive`.
 * One instance per pane (the chart pushes the same runs to each), so fills never stack.
 */
export class GapPrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null;
  private requestUpdate: (() => void) | null = null;
  private runs: readonly GapRun[] = [];
  private slots: SlotLayout[] = [];
  private barSpacing = 0;
  private readonly color: string;
  private readonly options: GapPrimitiveOptions;
  private readonly view: IPrimitivePaneView = {
    zOrder: (): PrimitivePaneViewZOrder => "bottom",
    renderer: (): IPrimitivePaneRenderer | null => this.renderer(),
  };

  constructor(color: string, options: GapPrimitiveOptions) {
    this.color = color;
    this.options = options;
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this.chart = param.chart as IChartApi;
    this.requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this.chart = null;
    this.requestUpdate = null;
    this.slots = [];
  }

  setRuns(runs: readonly GapRun[]): void {
    if (runs === this.runs) return;
    this.runs = runs;
    this.requestUpdate?.();
  }

  updateAllViews(): void {
    const timeScale = this.chart?.timeScale();
    if (!timeScale) {
      this.slots = [];
      return;
    }
    this.barSpacing = timeScale.options().barSpacing;
    // Only the visible slots are laid out: a scrolled-back history can hold tens of thousands
    // of gap slots, and this runs on every pan/zoom frame in every pane.
    const visible = timeScale.getVisibleRange();
    const slots: SlotLayout[] = [];
    if (visible !== null) {
      for (const run of this.runs) this.layoutRun(run, visible.from as number, visible.to as number, slots);
    }
    this.slots = slots;
  }

  private layoutRun(run: GapRun, from: number, to: number, slots: SlotLayout[]): void {
    const timeScale = this.chart?.timeScale();
    const first = run.times[0];
    const last = run.times[run.times.length - 1];
    if (!timeScale || last < from || first > to) return;
    // The label rides the run's first *visible* slot, so a run whose start is scrolled off
    // the left edge still says what it is (and whether it is compressed).
    let label: string | null = this.options.label ? gapLabel(run) : null;
    for (const time of run.times) {
      if (time < from || time > to) continue;
      const x = timeScale.timeToCoordinate(time as Time);
      if (x === null) continue;
      slots.push({ x, label });
      label = null;
    }
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  /** The laid-out slots, for tests. */
  layout(): readonly SlotLayout[] {
    return this.slots;
  }

  private renderer(): IPrimitivePaneRenderer | null {
    if (this.slots.length === 0) return null;
    const { slots, color, barSpacing } = this;
    const fontFamily = this.options.fontFamily ?? "monospace";
    return {
      draw: (target: CanvasRenderingTarget2D): void => {
        target.useBitmapCoordinateSpace(({ context, horizontalPixelRatio: hpr, verticalPixelRatio: vpr, bitmapSize }) => {
          const width = Math.max(1, Math.round(barSpacing * GAP_BAR_WIDTH_RATIO * hpr));
          context.fillStyle = color;
          context.globalAlpha = GAP_FILL_ALPHA;
          for (const slot of slots) {
            context.fillRect(Math.round(slot.x * hpr - width / 2), 0, width, bitmapSize.height);
          }
          context.globalAlpha = 1;
          context.font = `${Math.round(LABEL_FONT_SIZE_PX * vpr)}px ${fontFamily}`;
          context.textBaseline = "top";
          // Runs closer together than a label is wide (many short holes at a small bar
          // spacing) keep the first label and skip the ones it would overprint; every run's
          // fill is still drawn.
          let labelRight = -Infinity;
          for (const slot of slots) {
            if (slot.label === null) continue;
            // Clamped into the pane at both edges: a run whose first visible slot sits at the
            // right edge still shows its whole label rather than a clipped duration.
            const textWidth = context.measureText(slot.label).width;
            const padding = LABEL_LEFT_PADDING_PX * hpr;
            const wanted = Math.max(0, slot.x * hpr - width / 2) + padding;
            const left = Math.max(0, Math.min(wanted, bitmapSize.width - textWidth - padding));
            if (left < labelRight) continue;
            context.fillText(slot.label, Math.round(left), Math.round(LABEL_TOP_PX * vpr));
            labelRight = left + textWidth + padding;
          }
        });
      },
    };
  }
}
