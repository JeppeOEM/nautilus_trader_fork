import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";

import type { VolumeProfile } from "../../../lib/volumeProfile";

// Transitive fancy-canvas type, derived rather than imported (not a direct dependency).
type CanvasRenderingTarget2D = Parameters<IPrimitivePaneRenderer["draw"]>[0];

/** Everything one profile needs to be drawn -- the single input all five variants
 * (Stories 18.6-18.9) hand to the same primitive. */
export interface VolumeProfileRenderSpec {
  profile: VolumeProfile;
  /** Where the bars grow from: a left x in px (pane coordinates) growing rightward;
   * `"right"` to grow leftward from the pane's right edge (the price axis side); or a
   * `{time}` anchor, re-resolved to x on every redraw so a range-pinned profile (FRVP)
   * follows pan/zoom instead of being stranded at a fixed pixel. */
  xAnchor: number | "right" | { time: Time };
  /** Longest bar's length in px, or `{toTime}`: the px distance from the anchor to that
   * time (the longest bar spans the whole range). */
  width: number | { toTime: Time };
  upColor: string;
  downColor: string;
  showPoc: boolean;
  showValueArea: boolean;
  /** Draws grab-able range edges (thin vertical lines) at these times. */
  edges?: { startTime: Time; endTime: Time };
}

/** A spec after time anchors were resolved to pixels -- what `layoutProfile` consumes. */
export type ResolvedProfileSpec = Omit<VolumeProfileRenderSpec, "xAnchor" | "width" | "edges"> & {
  xAnchor: number | "right";
  width: number;
};

interface RowY {
  y1: number;
  y2: number;
}

export interface RowRect {
  y: number;
  h: number;
  upX: number;
  upW: number;
  downX: number;
  downW: number;
  isPoc: boolean;
}

export interface ProfileLayout {
  rows: RowRect[];
  /** The Value Area band across the profile's full extent, or null when not drawable. */
  band: { x: number; w: number; y: number; h: number } | null;
}

/**
 * Pure geometry (media-pixel coordinates): bar lengths are proportional to the fullest
 * row's total volume; up volume sits against the anchor edge, down volume beyond it.
 * `ys[i]` is row i's screen span, or null when the row is off the price scale.
 */
export function layoutProfile(
  spec: ResolvedProfileSpec,
  ys: readonly (RowY | null)[],
  paneWidth: number,
): ProfileLayout {
  const { profile, xAnchor, width } = spec;
  const maxTotal = Math.max(0, ...profile.rows.map((r) => r.upVolume + r.downVolume));
  const rightward = xAnchor !== "right";
  const edge = rightward ? xAnchor : paneWidth;
  const pocIndex = profile.rows.findIndex((r) => profile.poc >= r.priceLow && profile.poc <= r.priceHigh);

  const rows: RowRect[] = [];
  let bandTop = Infinity;
  let bandBottom = -Infinity;
  profile.rows.forEach((row, i) => {
    const span = ys[i];
    if (!span || maxTotal === 0) return;
    const y = Math.min(span.y1, span.y2);
    const h = Math.abs(span.y2 - span.y1);
    const upW = (row.upVolume / maxTotal) * width;
    const downW = (row.downVolume / maxTotal) * width;
    rows.push({
      y,
      h,
      upX: rightward ? edge : edge - upW,
      upW,
      downX: rightward ? edge + upW : edge - upW - downW,
      downW,
      isPoc: i === pocIndex,
    });
    if (row.priceHigh <= profile.vah && row.priceLow >= profile.val) {
      bandTop = Math.min(bandTop, y);
      bandBottom = Math.max(bandBottom, y + h);
    }
  });
  const band =
    bandTop === Infinity ? null : { x: rightward ? edge : edge - width, w: width, y: bandTop, h: bandBottom - bandTop };
  return { rows, band };
}

const POC_COLOR = "#ffff55";
const VA_ALPHA = 0.12;

// Story 18.5 (AC #2): one primitive for every Volume Profile variant. Row y-spans are
// recomputed from prices in `updateAllViews` (so pan/zoom on the price axis can't drift
// the histogram); pane width is only known at draw time, so the x layout happens there.
export class VolumeProfilePrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null;
  private series: ISeriesApi<"Candlestick" | "Line"> | null = null;
  private requestUpdate: (() => void) | null = null;
  private ys: (RowY | null)[] = [];
  private resolved: { xAnchor: number | "right"; width: number } | null = null;
  private edgeXs: { start: number; end: number } | null = null;
  private edgeY: RowY | null = null;
  private spec: VolumeProfileRenderSpec;
  private readonly view: IPrimitivePaneView = {
    // Behind the candles: a profile must never cover the price action it describes.
    zOrder: () => "bottom",
    renderer: (): IPrimitivePaneRenderer | null => this.renderer(),
  };

  constructor(spec: VolumeProfileRenderSpec) {
    this.spec = spec;
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
    this.ys = [];
    this.resolved = null;
    this.edgeXs = null;
    this.edgeY = null;
  }

  update(spec: VolumeProfileRenderSpec): void {
    if (spec === this.spec) return;
    this.spec = spec;
    this.requestUpdate?.();
  }

  updateAllViews(): void {
    const { series, chart } = this;
    if (!series || !chart) return;
    const { profile, xAnchor, width, edges } = this.spec;
    this.ys = profile.rows.map((row) => {
      const y1 = series.priceToCoordinate(row.priceHigh);
      const y2 = series.priceToCoordinate(row.priceLow);
      return y1 === null || y2 === null ? null : { y1, y2 };
    });

    const timeScale = chart.timeScale();
    const anchorX = typeof xAnchor === "object" ? timeScale.timeToCoordinate(xAnchor.time) : xAnchor;
    const toX = typeof width === "object" ? timeScale.timeToCoordinate(width.toTime) : null;
    // An unresolvable time (no coordinate) means nothing drawable, never a guessed position.
    const rangeWidth =
      toX === null || anchorX === "right" || anchorX === null ? null : Math.abs(toX - anchorX);
    const widthPx = typeof width === "object" ? rangeWidth : width;
    this.resolved = anchorX === null || widthPx === null ? null : { xAnchor: anchorX, width: widthPx };

    const start = edges ? timeScale.timeToCoordinate(edges.startTime) : null;
    const end = edges ? timeScale.timeToCoordinate(edges.endTime) : null;
    this.edgeXs = start === null || end === null ? null : { start, end };
    const top = series.priceToCoordinate(profile.rows.at(-1)?.priceHigh ?? 0);
    const bottom = series.priceToCoordinate(profile.rows[0]?.priceLow ?? 0);
    this.edgeY = top === null || bottom === null ? null : { y1: top, y2: bottom };
  }

  /** The last resolved pixel anchor/width, or null when a time had no coordinate. */
  resolvedAnchor(): { xAnchor: number | "right"; width: number } | null {
    return this.resolved;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  private renderer(): IPrimitivePaneRenderer | null {
    const { spec, ys, resolved, edgeXs, edgeY } = this;
    if (spec.profile.rows.length === 0 || !resolved) return null;
    return {
      draw: (target: CanvasRenderingTarget2D): void => {
        target.useBitmapCoordinateSpace(({ context, bitmapSize, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
          const { rows, band } = layoutProfile({ ...spec, ...resolved }, ys, bitmapSize.width / hr);
          if (spec.showValueArea && band) {
            context.globalAlpha = VA_ALPHA;
            context.fillStyle = spec.upColor;
            context.fillRect(band.x * hr, band.y * vr, band.w * hr, band.h * vr);
            context.globalAlpha = 1;
          }
          for (const r of rows) {
            // 1px gap between rows keeps neighbouring bars legible.
            const h = Math.max(1, r.h - 1) * vr;
            context.fillStyle = spec.upColor;
            context.fillRect(r.upX * hr, r.y * vr, r.upW * hr, h);
            context.fillStyle = spec.downColor;
            context.fillRect(r.downX * hr, r.y * vr, r.downW * hr, h);
            if (spec.showPoc && r.isPoc) {
              context.strokeStyle = POC_COLOR;
              context.lineWidth = 2 * hr;
              context.strokeRect(Math.min(r.upX, r.downX) * hr, r.y * vr, (r.upW + r.downW) * hr, h);
            }
          }
          if (edgeXs && edgeY) {
            context.strokeStyle = spec.upColor;
            context.lineWidth = hr;
            context.setLineDash([3 * hr, 3 * hr]);
            for (const x of [edgeXs.start, edgeXs.end]) {
              context.beginPath();
              context.moveTo(x * hr, Math.min(edgeY.y1, edgeY.y2) * vr);
              context.lineTo(x * hr, Math.max(edgeY.y1, edgeY.y2) * vr);
              context.stroke();
            }
            context.setLineDash([]);
          }
        });
      },
    };
  }
}
