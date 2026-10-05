import type { Time } from "lightweight-charts";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CHART_TOKENS } from "../chartTheme";
import { VerticalMarkerPrimitive } from "./VerticalMarkerPrimitive";

function attach(primitive: VerticalMarkerPrimitive, toX: (t: number) => number | null = (t) => t) {
  const requestUpdate = vi.fn();
  primitive.attached({
    chart: { timeScale: () => ({ timeToCoordinate: toX }) },
    series: {},
    requestUpdate,
  } as never);
  return requestUpdate;
}

interface Stroke {
  color: string;
  from: [number, number];
  to: [number, number];
}

function draw(primitive: VerticalMarkerPrimitive, pixelRatio = 1): Stroke[] {
  const strokes: Stroke[] = [];
  let path: [number, number][] = [];
  const context = {
    strokeStyle: "",
    lineWidth: 0,
    setLineDash: () => undefined,
    beginPath: () => {
      path = [];
    },
    moveTo: (x: number, y: number) => path.push([x, y]),
    lineTo: (x: number, y: number) => path.push([x, y]),
    stroke() {
      strokes.push({ color: this.strokeStyle, from: path[0], to: path[1] });
    },
  };
  primitive.updateAllViews();
  const renderer = primitive.paneViews()[0].renderer();
  renderer?.draw({
    useBitmapCoordinateSpace: (cb: (scope: unknown) => void) =>
      cb({ context, bitmapSize: { width: 800, height: 300 }, horizontalPixelRatio: pixelRatio, verticalPixelRatio: pixelRatio }),
  } as never);
  return strokes;
}

describe("VerticalMarkerPrimitive (Story 18.4)", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("draws one full-height line at the marker time, scaled to the bitmap", () => {
    const primitive = new VerticalMarkerPrimitive(120 as Time);
    attach(primitive);

    expect(draw(primitive, 2)).toEqual([{ color: CHART_TOKENS["--chart-marker"], from: [240, 0], to: [240, 300] }]);
  });

  it("draws nothing while the time has no coordinate", () => {
    const primitive = new VerticalMarkerPrimitive(120 as Time);
    attach(primitive, () => null);

    expect(draw(primitive)).toEqual([]);
  });

  it("reads --chart-marker at draw time, so a token change reaches an existing marker (DW-146)", () => {
    const primitive = new VerticalMarkerPrimitive(120 as Time);
    attach(primitive);
    expect(draw(primitive)[0].color).toBe(CHART_TOKENS["--chart-marker"]); // jsdom fallback

    const workspace = document.createElement("div");
    workspace.className = "chart-workspace";
    workspace.style.setProperty("--chart-marker", "#abcdef");
    document.body.appendChild(workspace);

    expect(draw(primitive)[0].color).toBe("#abcdef");
  });

  it("moves to a new time with a redraw request, and ignores the same time", () => {
    const primitive = new VerticalMarkerPrimitive(120 as Time);
    const requestUpdate = attach(primitive);

    primitive.setTime(120 as Time);
    expect(requestUpdate).not.toHaveBeenCalled();
    primitive.setTime(180 as Time);
    expect(requestUpdate).toHaveBeenCalledOnce();
    expect(draw(primitive)[0].from).toEqual([180, 0]);
  });
});
