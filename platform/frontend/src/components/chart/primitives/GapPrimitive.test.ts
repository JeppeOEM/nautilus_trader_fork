import { describe, expect, it, vi } from "vitest";

import type { GapRun } from "../../../lib/gaps";
import { GAP_FILL_ALPHA, GapPrimitive } from "./GapPrimitive";

const run = (times: number[], durationSeconds: number, compressed = false): GapRun => ({ times, durationSeconds, compressed });

function attach(
  primitive: GapPrimitive,
  {
    barSpacing = 10,
    toX = (t: number): number | null => t,
    visible = { from: 0, to: 1e12 },
  }: { barSpacing?: number; toX?: (t: number) => number | null; visible?: { from: number; to: number } | null } = {},
) {
  const requestUpdate = vi.fn();
  primitive.attached({
    chart: {
      timeScale: () => ({ timeToCoordinate: toX, options: () => ({ barSpacing }), getVisibleRange: () => visible }),
    },
    series: {},
    requestUpdate,
  } as never);
  return requestUpdate;
}

interface Drawn {
  rects: { x: number; y: number; w: number; h: number; alpha: number; fill: string }[];
  texts: { text: string; x: number; y: number; alpha: number; fill: string }[];
}

function draw(primitive: GapPrimitive, pixelRatio = 1): Drawn {
  const drawn: Drawn = { rects: [], texts: [] };
  const context = {
    globalAlpha: 1,
    fillStyle: "",
    font: "",
    textBaseline: "",
    fillRect(x: number, y: number, w: number, h: number) {
      drawn.rects.push({ x, y, w, h, alpha: this.globalAlpha, fill: this.fillStyle });
    },
    fillText(text: string, x: number, y: number) {
      drawn.texts.push({ text, x, y, alpha: this.globalAlpha, fill: this.fillStyle });
    },
    measureText: (text: string) => ({ width: text.length * 6 }),
  };
  primitive.updateAllViews();
  const renderer = primitive.paneViews()[0].renderer();
  renderer?.draw({
    useBitmapCoordinateSpace: (cb: (scope: unknown) => void) =>
      cb({ context, bitmapSize: { width: 800, height: 300 }, horizontalPixelRatio: pixelRatio, verticalPixelRatio: pixelRatio }),
  } as never);
  return drawn;
}

describe("GapPrimitive (Story 32.1)", () => {
  it("draws below the series", () => {
    const primitive = new GapPrimitive("#ff9100", { label: false });
    expect(primitive.paneViews()[0].zOrder?.()).toBe("bottom");
  });

  it("fills every slot as a translucent, full-height bar one candle wide, centred on its time", () => {
    const primitive = new GapPrimitive("#ff9100", { label: false });
    attach(primitive, { barSpacing: 10 });
    primitive.setRuns([run([100, 110, 120], 180)]);

    const { rects, texts } = draw(primitive);

    expect(rects).toEqual([100, 110, 120].map((x) => ({ x: x - 4, y: 0, w: 8, h: 300, alpha: GAP_FILL_ALPHA, fill: "#ff9100" })));
    expect(GAP_FILL_ALPHA).toBeLessThan(1);
    expect(texts).toEqual([]); // unlabelled: a volume/indicator pane
  });

  it("scales to the bitmap and never draws a bar narrower than 1px", () => {
    const primitive = new GapPrimitive("#ff9100", { label: false });
    attach(primitive, { barSpacing: 0.5 });
    primitive.setRuns([run([100], 60)]);

    expect(draw(primitive, 2).rects[0]).toMatchObject({ w: 1, h: 300 });

    const wide = new GapPrimitive("#ff9100", { label: false });
    attach(wide, { barSpacing: 10 });
    wide.setRuns([run([100], 60)]);
    expect(draw(wide, 2).rects[0]).toMatchObject({ x: 192, w: 16 });
  });

  it("labels each run once, at its first slot, in the solid colour", () => {
    const primitive = new GapPrimitive("#ff9100", { label: true, fontFamily: "Mono" });
    attach(primitive);
    primitive.setRuns([run([100, 110, 120, 130, 140], 300), run([300, 310], 1e6, true)]);

    const { texts } = draw(primitive);

    expect(texts.map((t) => t.text)).toEqual(["no data · 5m", "no data · 11d 13h (compressed)"]);
    expect(texts[0]).toMatchObject({ alpha: 1, fill: "#ff9100", y: 4 });
    expect(texts.map((t) => t.x)).toEqual([99, 299]); // first slot's left edge + 3px padding
  });

  it("lays out only the visible slots, and labels a run at its first visible slot", () => {
    const primitive = new GapPrimitive("#ff9100", { label: true });
    attach(primitive, { visible: { from: 120, to: 200 } });
    primitive.setRuns([run([100, 110, 120, 130], 240), run([500, 510], 120)]);

    const { rects, texts } = draw(primitive);

    expect(rects.map((r) => r.x)).toEqual([116, 126]);
    expect(texts).toMatchObject([{ text: "no data · 4m", x: 119 }]);
  });

  it("keeps a label whose first visible slot is at the right edge wholly inside the pane", () => {
    const primitive = new GapPrimitive("#ff9100", { label: true });
    attach(primitive);
    primitive.setRuns([run([795], 300)]);

    const { texts } = draw(primitive);

    const width = "no data · 5m".length * 6;
    expect(texts).toMatchObject([{ text: "no data · 5m", x: 800 - width - 3 }]);
  });

  it("lays out nothing while the chart has no visible range", () => {
    const primitive = new GapPrimitive("#ff9100", { label: true });
    attach(primitive, { visible: null });
    primitive.setRuns([run([100], 60)]);
    expect(draw(primitive)).toEqual({ rects: [], texts: [] });
  });

  it("skips a label that would overprint the previous one, but still fills its run", () => {
    const primitive = new GapPrimitive("#ff9100", { label: true });
    attach(primitive);
    primitive.setRuns([run([100], 60), run([120], 60), run([400], 60)]);

    const { rects, texts } = draw(primitive);

    expect(rects).toHaveLength(3);
    expect(texts.map((t) => t.x)).toEqual([99, 399]);
  });

  it("skips slots with no coordinate and draws nothing without runs", () => {
    const primitive = new GapPrimitive("#ff9100", { label: true });
    attach(primitive, { toX: (t) => (t === 110 ? null : t) });
    primitive.setRuns([run([100, 110], 120)]);
    expect(draw(primitive).rects.map((r) => r.x)).toEqual([96]);

    primitive.setRuns([]);
    expect(draw(primitive)).toEqual({ rects: [], texts: [] });
  });

  it("asks for a redraw when its runs change, and lays out nothing once detached", () => {
    const primitive = new GapPrimitive("#ff9100", { label: false });
    const requestUpdate = attach(primitive);
    const runs = [run([100], 60)];

    primitive.setRuns(runs);
    primitive.setRuns(runs);
    expect(requestUpdate).toHaveBeenCalledTimes(1);

    primitive.detached();
    primitive.updateAllViews();
    expect(primitive.layout()).toEqual([]);
  });

  it("follows pan/zoom: slots are re-resolved from their times on every redraw", () => {
    let offset = 0;
    const primitive = new GapPrimitive("#ff9100", { label: false });
    attach(primitive, { toX: (t) => t + offset });
    primitive.setRuns([run([100], 60)]);

    primitive.updateAllViews();
    expect(primitive.layout()[0].x).toBe(100);
    offset = 25;
    primitive.updateAllViews();
    expect(primitive.layout()[0].x).toBe(125);
  });
});
