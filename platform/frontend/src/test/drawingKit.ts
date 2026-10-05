import { vi } from "vitest";

import type { ISeriesPrimitive, Time } from "lightweight-charts";

/** A recording stand-in for a canvas 2D context: jsdom has none, and what matters is what was drawn. */
export function fakeContext() {
  const texts: { text: string; x: number; y: number; style: unknown }[] = [];
  const rects: { x: number; y: number; w: number; h: number; style: unknown; lineWidth: number }[] = [];
  const fills: { x: number; y: number; w: number; h: number; alpha: number; style: unknown }[] = [];
  const strokes: { from: [number, number]; to: [number, number]; style: unknown }[] = [];
  let path: [number, number][] = [];
  const context = {
    globalAlpha: 1,
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 1,
    font: "",
    textAlign: "",
    textBaseline: "",
    save: vi.fn(),
    restore: vi.fn(),
    beginPath: () => {
      path = [];
    },
    moveTo: (x: number, y: number) => path.push([x, y]),
    lineTo: (x: number, y: number) => path.push([x, y]),
    stroke: () => {
      if (path.length >= 2) strokes.push({ from: path[0], to: path[path.length - 1], style: context.strokeStyle });
    },
    setLineDash: vi.fn(),
    fillRect: (x: number, y: number, w: number, h: number) =>
      fills.push({ x, y, w, h, alpha: context.globalAlpha, style: context.fillStyle }),
    strokeRect: (x: number, y: number, w: number, h: number) =>
      rects.push({ x, y, w, h, style: context.strokeStyle, lineWidth: context.lineWidth }),
    fillText: (text: string, x: number, y: number) => texts.push({ text, x, y, style: context.fillStyle }),
    // A fixed 6 px per character: enough to tell a text that fits its box from one that does not.
    measureText: (text: string) => ({ width: text.length * 6 }),
  };
  return { context, texts, fills, strokes, rects };
}

/** Run the primitive's renderer into a fake pane `width` bitmap pixels wide (1:1 pixel ratio). */
export function drawPrimitive(primitive: ISeriesPrimitive<Time>, width = 800) {
  const recorded = fakeContext();
  const target = {
    useBitmapCoordinateSpace: (draw: (scope: unknown) => void) =>
      draw({
        context: recorded.context,
        bitmapSize: { width, height: 500 },
        horizontalPixelRatio: 1,
        verticalPixelRatio: 1,
      }),
  };
  primitive.paneViews?.()[0].renderer()?.draw(target as never);
  return recorded;
}

/**
 * Attach `primitive` to a fake chart: x of a bar time is the time itself, x of logical slot `i` is
 * `10 * i`, and a price's y is `1000 - price` (higher prices sit higher on screen).
 */
export function attachTo(primitive: ISeriesPrimitive<Time>): void {
  primitive.attached?.({
    chart: {
      timeScale: () => ({
        timeToCoordinate: (t: number) => t,
        logicalToCoordinate: (i: number) => i * 10,
      }),
    },
    series: { priceToCoordinate: (p: number) => 1000 - p },
    requestUpdate: vi.fn(),
  } as never);
  primitive.updateAllViews?.();
}
