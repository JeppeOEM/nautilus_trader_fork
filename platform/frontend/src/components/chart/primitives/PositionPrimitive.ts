import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  Logical,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";

import { type InstrumentPrecision, type PositionDrawing, positionLabels } from "../../../lib/drawings";
import { chartVar } from "../chartTheme";
import {
  BODY_TOLERANCE_PX,
  BarGrid,
  type DrawingHit,
  type DrawingPrimitive,
  HANDLE_SIZE_PX,
  nearestHandle,
} from "./drawingPrimitive";

// Transitive fancy-canvas type, derived rather than imported (not a direct dependency).
type CanvasRenderingTarget2D = Parameters<IPrimitivePaneRenderer["draw"]>[0];

const FONT_PX = 12;
const ZONE_ALPHA = 0.2;
const LABEL_OFFSET_PX = 10;

interface Geometry {
  left: number;
  right: number;
  entryY: number;
  targetY: number;
  stopY: number;
}

/**
 * A Long or Short position (Story 32.5): the profit zone entry -> target in `--chart-up` and the
 * loss zone entry -> stop in `--chart-down` (about 20 % alpha), from the placed bar to
 * `width_bars` bars later, with Target / Entry / Stop / Risk-Reward (and Size when the account and
 * risk percent are set) labels at the instrument's precision. The left edge sits on the placed bar's
 * logical slot and the right edge `width_bars` slots further on (it may lie past the newest bar).
 * With no precision known the zones draw but no label does.
 */
export class PositionPrimitive implements DrawingPrimitive {
  private chart: IChartApi | null = null;
  private series: ISeriesApi<"Candlestick" | "Line"> | null = null;
  private requestUpdate: (() => void) | null = null;
  private geometry: Geometry | null = null;
  private handlesVisible = false;
  private readonly view: IPrimitivePaneView = {
    renderer: (): IPrimitivePaneRenderer | null => this.renderer(),
  };

  private position: PositionDrawing;
  private precision: InstrumentPrecision | null;
  private readonly grid: BarGrid;

  constructor(position: PositionDrawing, precision: InstrumentPrecision | null, grid: BarGrid = new BarGrid()) {
    this.position = position;
    this.precision = precision;
    this.grid = grid;
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this.chart = param.chart as IChartApi;
    this.series = param.series as ISeriesApi<"Candlestick" | "Line">;
    this.requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this.chart = null;
    this.series = null;
    this.requestUpdate = null;
    this.geometry = null;
  }

  update(position: PositionDrawing, precision: InstrumentPrecision | null): void {
    if (position === this.position && precision === this.precision) return;
    this.position = position;
    this.precision = precision;
    this.requestUpdate?.();
  }

  setHandlesVisible(visible: boolean): void {
    if (visible === this.handlesVisible) return;
    this.handlesVisible = visible;
    this.requestUpdate?.();
  }

  refresh(): void {
    this.requestUpdate?.();
  }

  updateAllViews(): void {
    const { chart, series } = this;
    if (!chart || !series) return;
    const p = this.position;
    const index = this.grid.indexOf(p.time);
    const timeScale = chart.timeScale();
    const left = index === null ? null : timeScale.logicalToCoordinate(index as Logical);
    const right = index === null ? null : timeScale.logicalToCoordinate((index + p.width_bars) as Logical);
    const entryY = series.priceToCoordinate(p.entry);
    const targetY = series.priceToCoordinate(p.target);
    const stopY = series.priceToCoordinate(p.stop);
    this.geometry =
      left === null || right === null || entryY === null || targetY === null || stopY === null
        ? null
        : { left, right, entryY, targetY, stopY };
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  private handles(g: Geometry): { id: string; x: number; y: number }[] {
    return [
      { id: "entry", x: g.left, y: g.entryY },
      { id: "target", x: g.right, y: g.targetY },
      { id: "stop", x: g.right, y: g.stopY },
      { id: "right", x: g.right, y: g.entryY },
    ];
  }

  /** A handle (entry, target, stop, right edge) within the grab radius, else the box itself. */
  hit(x: number, y: number): DrawingHit | null {
    const g = this.geometry;
    if (!g) return null;
    const handle = nearestHandle(this.handles(g), x, y);
    if (handle) return handle;
    const top = Math.min(g.targetY, g.stopY, g.entryY);
    const bottom = Math.max(g.targetY, g.stopY, g.entryY);
    const inside = x >= g.left && x <= g.right && y >= top && y <= bottom;
    return inside ? { handle: null, distance: BODY_TOLERANCE_PX } : null;
  }

  /** The current screen geometry, or `null` when not drawable -- exposed for tests. */
  screen(): Geometry | null {
    return this.geometry;
  }

  private renderer(): IPrimitivePaneRenderer | null {
    const g = this.geometry;
    if (!g) return null;
    const labels = this.precision === null ? null : positionLabels(this.position, this.precision);
    const handles = this.handlesVisible && !this.position.locked ? this.handles(g) : [];
    const up = chartVar("--chart-up");
    const down = chartVar("--chart-down");
    // A drawing's own colour (the context menu's) tints the entry line and its labels.
    const text = this.position.color ?? chartVar("--chart-text");
    const bg = chartVar("--chart-bg");
    return {
      draw: (target: CanvasRenderingTarget2D): void => {
        target.useBitmapCoordinateSpace(({ context, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
          const left = g.left * hr;
          const width = (g.right - g.left) * hr;
          const zone = (fromY: number, toY: number, color: string): void => {
            context.save();
            context.globalAlpha = ZONE_ALPHA;
            context.fillStyle = color;
            context.fillRect(left, Math.min(fromY, toY) * vr, width, Math.abs(toY - fromY) * vr);
            context.restore();
          };
          zone(g.entryY, g.targetY, up);
          zone(g.entryY, g.stopY, down);
          const line = (y: number, color: string): void => {
            context.strokeStyle = color;
            context.lineWidth = hr;
            context.beginPath();
            context.moveTo(left, y * vr);
            context.lineTo(left + width, y * vr);
            context.stroke();
          };
          line(g.targetY, up);
          line(g.stopY, down);
          line(g.entryY, text);
          if (labels) {
            context.font = `${FONT_PX * vr}px sans-serif`;
            context.textAlign = "left";
            context.textBaseline = "middle";
            const put = (value: string, y: number, toward: number, color: string): void => {
              context.fillStyle = color;
              // Inside the zone: on the entry's side of its line.
              const dir = Math.sign(toward - y) || 1;
              context.fillText(value, left + 6 * hr, (y + dir * LABEL_OFFSET_PX) * vr);
            };
            put(labels.target, g.targetY, g.entryY, up);
            put(labels.stop, g.stopY, g.entryY, down);
            put(labels.entry, g.entryY, g.stopY, text);
            put(
              labels.size === null ? labels.rewardRisk : `${labels.rewardRisk}   ${labels.size}`,
              g.entryY,
              g.targetY,
              text,
            );
          }
          const half = (HANDLE_SIZE_PX / 2) * hr;
          context.fillStyle = bg;
          context.strokeStyle = text;
          context.lineWidth = hr;
          for (const h of handles) {
            context.fillRect(h.x * hr - half, h.y * vr - half, half * 2, half * 2);
            context.strokeRect(h.x * hr - half, h.y * vr - half, half * 2, half * 2);
          }
        });
      },
    };
  }
}
