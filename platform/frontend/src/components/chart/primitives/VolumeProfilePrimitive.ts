import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";

import type { TpoRow } from "../../../lib/tpo";
import type { VolumeProfile } from "../../../lib/volumeProfile";
import { chartVar } from "../chartTheme";

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
  /** Scales a `{toTime}` width (e.g. 0.7 = the longest bar spans 70% of the range). */
  widthFraction?: number;
  /** Story 32.7 (Anchored VP, Auto Anchored): a `{toTime}` range includes the end bar's own slot
   * (one bar spacing past its x), so a range of one bar (an anchor on the newest bar, a period
   * whose first bar just closed) is drawn one bar wide instead of zero px wide. */
  throughEndBar?: boolean;
  /** Story 18.8 (SVP HD): at draw time, drop the inter-row gap once rows get too short to
   * afford one, so a dense profile stays legible while zooming out. A redraw-only
   * adaptation -- the profile is never recomputed for it. */
  respondsToZoom?: boolean;
  /** Draws grab-able range edges (thin vertical lines) at these times. */
  edges?: { startTime: Time; endTime: Time };
  /** Story 32.7 (TPO): the rows' touch counts drawn as blocks (one per candle-touch, the overflow
   * of a row as one longer bar), or as the touching candles' letters, instead of the histogram.
   * `tpo.rows[i]` is `profile.rows[i]`; the profile's POC and value area are marked as ever. */
  tpo?: { rows: readonly TpoRow[]; letters: boolean };
  /** The TPO's initial balance: a dashed outline across the profile between these prices. */
  initialBalance?: { high: number; low: number };
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

export interface TpoBlockRect {
  x: number;
  w: number;
  letter: string;
  up: boolean;
}

export interface TpoRowRect {
  y: number;
  h: number;
  blocks: TpoBlockRect[];
  /** The one longer bar standing for the touches beyond the cap, or null. */
  overflow: { x: number; w: number } | null;
  isPoc: boolean;
}

/**
 * Pure geometry of a TPO (media-pixel coordinates): every row's blocks are one `width / fullest
 * count` px wide, side by side from the anchor, so the fullest row spans `width` and a row's length
 * stays proportional to its touches; a row over the block cap ends in one bar as long as its
 * overflow. `ys[i]` is row i's screen span, or null when the row is off the price scale.
 */
export function layoutTpo(
  spec: ResolvedProfileSpec & { tpo: { rows: readonly TpoRow[]; letters: boolean } },
  ys: readonly (RowY | null)[],
  paneWidth: number,
): TpoRowRect[] {
  const { profile, xAnchor, width, tpo } = spec;
  const maxCount = Math.max(0, ...tpo.rows.map((r) => r.count));
  if (maxCount === 0) return [];
  const unit = width / maxCount;
  const left = xAnchor === "right" ? paneWidth - width : xAnchor;
  const pocIndex = profile.rows.findIndex((r) => profile.poc >= r.priceLow && profile.poc <= r.priceHigh);
  const out: TpoRowRect[] = [];
  tpo.rows.forEach((row, i) => {
    const span = ys[i];
    if (!span || row.count === 0) return;
    out.push({
      y: Math.min(span.y1, span.y2),
      h: Math.abs(span.y2 - span.y1),
      blocks: row.touches.map((t, n) => ({ x: left + n * unit, w: unit, letter: t.letter, up: t.up })),
      overflow: row.overflow > 0 ? { x: left + row.blocks * unit, w: row.overflow * unit } : null,
      isPoc: i === pocIndex,
    });
  });
  return out;
}

// Rows shorter than this (px) lose their 1px gap when `respondsToZoom` is set.
const ZOOM_GAP_MIN_ROW_PX = 3;

// Story 32.4: the point-of-control line reads the chart token at draw time.
const pocColor = (): string => chartVar("--chart-poc");
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
  private ibY: RowY | null = null;
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
    this.ibY = null;
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
    const endSlot = this.spec.throughEndBar ? timeScale.options().barSpacing : 0;
    const rangeWidth =
      toX === null || anchorX === "right" || anchorX === null ? null : Math.abs(toX - anchorX) + endSlot;
    let widthPx: number | null;
    if (typeof width === "object") {
      widthPx = rangeWidth === null ? null : rangeWidth * (this.spec.widthFraction ?? 1);
    } else {
      widthPx = width;
    }
    this.resolved = anchorX === null || widthPx === null ? null : { xAnchor: anchorX, width: widthPx };

    const start = edges ? timeScale.timeToCoordinate(edges.startTime) : null;
    const end = edges ? timeScale.timeToCoordinate(edges.endTime) : null;
    this.edgeXs = start === null || end === null ? null : { start, end };
    const top = series.priceToCoordinate(profile.rows.at(-1)?.priceHigh ?? 0);
    const bottom = series.priceToCoordinate(profile.rows[0]?.priceLow ?? 0);
    this.edgeY = top === null || bottom === null ? null : { y1: top, y2: bottom };
    const ib = this.spec.initialBalance;
    const ibTop = ib ? series.priceToCoordinate(ib.high) : null;
    const ibBottom = ib ? series.priceToCoordinate(ib.low) : null;
    this.ibY = ibTop === null || ibBottom === null ? null : { y1: ibTop, y2: ibBottom };
  }

  /** The last resolved pixel anchor/width, or null when a time had no coordinate. */
  resolvedAnchor(): { xAnchor: number | "right"; width: number } | null {
    return this.resolved;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  private renderer(): IPrimitivePaneRenderer | null {
    const { spec, ys, resolved, edgeXs, edgeY, ibY } = this;
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
          if (spec.tpo) {
            drawTpo(context, { ...spec, ...resolved, tpo: spec.tpo }, ys, bitmapSize.width / hr, hr, vr);
          }
          if (spec.tpo && spec.initialBalance && ibY) {
            const left = resolved.xAnchor === "right" ? bitmapSize.width / hr - resolved.width : resolved.xAnchor;
            context.strokeStyle = chartVar("--chart-drawing");
            context.lineWidth = hr;
            context.setLineDash([4 * hr, 3 * hr]);
            context.strokeRect(
              left * hr,
              Math.min(ibY.y1, ibY.y2) * vr,
              resolved.width * hr,
              Math.abs(ibY.y2 - ibY.y1) * vr,
            );
            context.setLineDash([]);
          }
          for (const r of spec.tpo ? [] : rows) {
            // 1px gap between rows keeps neighbouring bars legible.
            const gap = spec.respondsToZoom && r.h < ZOOM_GAP_MIN_ROW_PX ? 0 : 1;
            const h = Math.max(1, r.h - gap) * vr;
            context.fillStyle = spec.upColor;
            context.fillRect(r.upX * hr, r.y * vr, r.upW * hr, h);
            context.fillStyle = spec.downColor;
            context.fillRect(r.downX * hr, r.y * vr, r.downW * hr, h);
            if (spec.showPoc && r.isPoc) {
              context.strokeStyle = pocColor();
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

const TPO_LETTER_MIN_BLOCK_PX = 7;
const TPO_LETTER_FONT_PX = 11;

/** The TPO's blocks (or letters) and its point-of-control outline. */
function drawTpo(
  context: CanvasRenderingContext2D,
  spec: ResolvedProfileSpec & { tpo: { rows: readonly TpoRow[]; letters: boolean } },
  ys: readonly (RowY | null)[],
  paneWidth: number,
  hr: number,
  vr: number,
): void {
  const rows = layoutTpo(spec, ys, paneWidth);
  for (const r of rows) {
    const h = Math.max(1, r.h - 1) * vr;
    for (const b of r.blocks) {
      context.fillStyle = b.up ? spec.upColor : spec.downColor;
      context.fillRect(b.x * hr, r.y * vr, Math.max(1, b.w - 1) * hr, h);
      if (spec.tpo.letters && b.w >= TPO_LETTER_MIN_BLOCK_PX && r.h >= TPO_LETTER_FONT_PX - 2) {
        context.fillStyle = chartVar("--chart-bg");
        context.font = `${Math.min(TPO_LETTER_FONT_PX, r.h - 1) * vr}px sans-serif`;
        context.textAlign = "center";
        context.textBaseline = "middle";
        context.fillText(b.letter, (b.x + b.w / 2) * hr, (r.y + r.h / 2) * vr);
      }
    }
    if (r.overflow) {
      // One longer bar: the touches past the cap, in the colour of the row's last block.
      const last = r.blocks.at(-1);
      context.fillStyle = last?.up === false ? spec.downColor : spec.upColor;
      context.fillRect(r.overflow.x * hr, r.y * vr, r.overflow.w * hr, h);
    }
    if (spec.showPoc && r.isPoc) {
      const end = r.overflow ? r.overflow.x + r.overflow.w : (r.blocks.at(-1)?.x ?? 0) + (r.blocks.at(-1)?.w ?? 0);
      const start = r.blocks[0]?.x ?? 0;
      context.strokeStyle = pocColor();
      context.lineWidth = 2 * hr;
      context.strokeRect(start * hr, r.y * vr, (end - start) * hr, h);
    }
  }
}
