import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { IndicatorPaneSpec } from "./LightweightChart";

const addSeriesMock = vi.fn();
const applyOptionsMock = vi.fn();
const removeMock = vi.fn();
const removeSeriesMock = vi.fn();
const setDataMock = vi.fn();
const createChartMock = vi.fn();
const addPaneMock = vi.fn();
const removePaneMock = vi.fn();
const getVisibleLogicalRangeMock = vi.fn();
const setVisibleLogicalRangeMock = vi.fn();

// One shared counter so each chart.addPane() call gets its own, stable, ever-increasing
// index -- mirrors the real library's paneIndex() behaviour closely enough for the
// registry-diffing assertions below (never reused across removals within one test).
// Starts at 1, not 0: the real library always reserves pane 0 for whatever series is
// added first with no explicit paneIndex (this component's own CandlestickSeries) --
// a mock starting at 0 would let an indicator pane collide with that index undetected.
let nextPaneIndex = 1;

function makePaneMock() {
  const paneIndex = nextPaneIndex++;
  const paneSeriesSetDataMock = vi.fn();
  const paneApplyOptionsMock = vi.fn();
  return {
    paneIndex: () => paneIndex,
    addSeries: vi.fn(() => ({ setData: paneSeriesSetDataMock, applyOptions: paneApplyOptionsMock })),
    getSeries: vi.fn(() => []),
    setStretchFactor: vi.fn(),
    getStretchFactor: vi.fn(() => 1),
  };
}

// A real ISeriesApi's `.options().color`/`.applyOptions({color})` round-trip, so the
// component's own read-current-color-before-reapplying check (LightweightChart.tsx) has
// something real to read -- each call site (candlestick or an indicator pane) gets its
// own closed-over color, not one shared across every series in the test.
function makeSeriesMock(initialColor: string | undefined) {
  let color = initialColor;
  const applyOptions = vi.fn((opts: { color?: string }) => {
    if (opts.color !== undefined) color = opts.color;
  });
  return { setData: setDataMock, applyOptions, options: vi.fn(() => ({ color })) };
}

// Shallow mock of the whole module -- jsdom has no real <canvas> 2D context, so a real
// lightweight-charts render is not the right test boundary here; asserting call counts
// against a mock proves the single-instance invariant (AD-F4) without needing the
// `canvas` npm package (dependency-minimization preference, troll/CLAUDE.md).
vi.mock("lightweight-charts", () => ({
  CandlestickSeries: "CandlestickSeries-sentinel",
  LineSeries: "LineSeries-sentinel",
  HistogramSeries: "HistogramSeries-sentinel",
  createChart: (...args: unknown[]) => createChartMock(...args),
}));

const { default: LightweightChart } = await import("./LightweightChart");

function makePaneSpec(id: string, overrides: Partial<IndicatorPaneSpec> = {}): IndicatorPaneSpec {
  return { id, kind: "Line", data: [], color: "#123456", ...overrides };
}

beforeEach(() => {
  nextPaneIndex = 1;
  setDataMock.mockReset();
  addSeriesMock
    .mockReset()
    .mockImplementation((_definition: unknown, options?: { color?: string }) => makeSeriesMock(options?.color));
  applyOptionsMock.mockReset();
  removeMock.mockReset();
  removeSeriesMock.mockReset();
  addPaneMock.mockReset().mockImplementation(() => makePaneMock());
  removePaneMock.mockReset();
  getVisibleLogicalRangeMock.mockReset().mockReturnValue({ from: 10, to: 50 });
  setVisibleLogicalRangeMock.mockReset();
  createChartMock.mockReset().mockImplementation(() => ({
    addSeries: addSeriesMock,
    removeSeries: removeSeriesMock,
    applyOptions: applyOptionsMock,
    remove: removeMock,
    addPane: addPaneMock,
    removePane: removePaneMock,
    timeScale: () => ({
      getVisibleLogicalRange: getVisibleLogicalRangeMock,
      setVisibleLogicalRange: setVisibleLogicalRangeMock,
      subscribeVisibleLogicalRangeChange: vi.fn(),
      unsubscribeVisibleLogicalRangeChange: vi.fn(),
    }),
  }));
});

afterEach(() => {
  cleanup();
});

describe("LightweightChart", () => {
  it("calls createChart exactly once per mount", () => {
    render(<LightweightChart data={[]} onChartApi={() => {}} />);

    expect(createChartMock).toHaveBeenCalledTimes(1);
    expect(addSeriesMock).toHaveBeenCalledTimes(1);
    expect(addSeriesMock).toHaveBeenCalledWith("CandlestickSeries-sentinel");
  });

  it("does not call createChart again on a data-only re-render", () => {
    const { rerender } = render(<LightweightChart data={[]} onChartApi={() => {}} />);

    rerender(<LightweightChart data={[{ time: 1700000000 as never }]} onChartApi={() => {}} />);

    expect(createChartMock).toHaveBeenCalledTimes(1);
    expect(setDataMock).toHaveBeenCalledTimes(2); // initial mount effect + the data-change effect
  });

  it("calls chart.remove() on unmount", () => {
    const { unmount } = render(<LightweightChart data={[]} onChartApi={() => {}} />);

    unmount();

    expect(removeMock).toHaveBeenCalledTimes(1);
  });

  it("notifies onChartApi with the chart on mount and null on unmount", () => {
    const onChartApi = vi.fn();
    const { unmount } = render(<LightweightChart data={[]} onChartApi={onChartApi} />);

    expect(onChartApi).toHaveBeenCalledTimes(1);
    expect(onChartApi.mock.calls[0][0]).not.toBeNull();

    unmount();

    expect(onChartApi).toHaveBeenCalledTimes(2);
    expect(onChartApi.mock.calls[1][0]).toBeNull();
  });

  it("adds a pane by id via chart.addPane() + chart.addSeries(definition, options, paneIndex)", () => {
    const panes = [makePaneSpec("MultiLevelOFI", { kind: "Line", color: "#2962ff" })];

    render(<LightweightChart data={[]} onChartApi={() => {}} panes={panes} />);

    expect(addPaneMock).toHaveBeenCalledTimes(1);
    // Call [0] is the mount effect's CandlestickSeries add (implicit pane 0); [1] is this
    // indicator pane's -- chart.addSeries(..., paneIndex()) is the sanctioned call site,
    // not pane.addSeries(). Asserting paneIndex 1 here (not just "whatever was passed
    // through") proves the indicator pane never collides with the candlestick's pane 0.
    expect(addSeriesMock).toHaveBeenCalledTimes(2);
    expect(addSeriesMock).toHaveBeenNthCalledWith(2, "LineSeries-sentinel", { color: "#2962ff" }, 1);
  });

  it("removes a pane by id when it drops out of the panes prop, and re-adding the same id afterward works cleanly", () => {
    const onePane = [makePaneSpec("MultiLevelOFI")];
    const { rerender } = render(<LightweightChart data={[]} onChartApi={() => {}} panes={onePane} />);
    expect(addPaneMock).toHaveBeenCalledTimes(1);

    rerender(<LightweightChart data={[]} onChartApi={() => {}} panes={[]} />);
    expect(removePaneMock).toHaveBeenCalledTimes(1);
    expect(removePaneMock).toHaveBeenCalledWith(1);

    rerender(<LightweightChart data={[]} onChartApi={() => {}} panes={onePane} />);
    // Re-adding the same id after removal creates exactly one more pane (never a
    // silently-stale second entry, never a no-op skip).
    expect(addPaneMock).toHaveBeenCalledTimes(2);
  });

  it("never touches the chart's visible logical range when adding or removing a pane (AC #4)", () => {
    const onePane = [makePaneSpec("MultiLevelOFI")];
    const { rerender } = render(<LightweightChart data={[]} onChartApi={() => {}} panes={onePane} />);

    rerender(<LightweightChart data={[]} onChartApi={() => {}} panes={[]} />);
    rerender(<LightweightChart data={[]} onChartApi={() => {}} panes={onePane} />);

    expect(setVisibleLogicalRangeMock).not.toHaveBeenCalled();
  });

  it("preserves the exact visible logical range across a pane add/remove cycle", () => {
    // Byte-for-bit-unchanged, expressed as the strongest possible check: nothing in the
    // pane add/remove path ever even reads, let alone rewrites, the chart's visible
    // logical range -- there is no code path here that could change it.
    const onePane = [makePaneSpec("MultiLevelOFI")];
    const { rerender } = render(<LightweightChart data={[]} onChartApi={() => {}} panes={[]} />);
    rerender(<LightweightChart data={[]} onChartApi={() => {}} panes={onePane} />);
    rerender(<LightweightChart data={[]} onChartApi={() => {}} panes={[]} />);
    rerender(<LightweightChart data={[]} onChartApi={() => {}} panes={onePane} />);

    expect(getVisibleLogicalRangeMock).not.toHaveBeenCalled();
    expect(setVisibleLogicalRangeMock).not.toHaveBeenCalled();
  });

  it("only calls setData again for an already-registered pane when its data reference actually changes", () => {
    // A stable `data` reference across every render below, so the candlestick series'
    // own separate `[data]` effect (LightweightChart.tsx) never re-fires and pollutes
    // the setData count this test is isolating to the pane-registry effect alone.
    const stableCandleData: unknown[] = [];
    const dataA: IndicatorPaneSpec["data"] = [];
    const dataB: IndicatorPaneSpec["data"] = [{ time: 1700000000 as never, value: 1 }];
    const specA = [makePaneSpec("MultiLevelOFI", { data: dataA, color: "#111111" })];

    const { rerender } = render(
      <LightweightChart data={stableCandleData as never} onChartApi={() => {}} panes={specA} />,
    );
    expect(addPaneMock).toHaveBeenCalledTimes(1);
    const callsAfterMount = setDataMock.mock.calls.length;

    // Same id, same data reference, same color: no new pane, no extra setData call.
    rerender(<LightweightChart data={stableCandleData as never} onChartApi={() => {}} panes={specA} />);
    expect(addPaneMock).toHaveBeenCalledTimes(1);
    expect(setDataMock.mock.calls.length).toBe(callsAfterMount);

    // Same id, new data reference: setData runs again, still no new pane created.
    const specB = [{ ...specA[0], data: dataB }];
    rerender(<LightweightChart data={stableCandleData as never} onChartApi={() => {}} panes={specB} />);
    expect(addPaneMock).toHaveBeenCalledTimes(1);
    expect(setDataMock.mock.calls.length).toBe(callsAfterMount + 1);
  });

  describe("Candles/Lines mode swap (Story 15.7)", () => {
    it("defaults to candles mode: adds exactly one CandlestickSeries, no line series", () => {
      render(<LightweightChart data={[]} onChartApi={() => {}} />);

      expect(addSeriesMock).toHaveBeenCalledTimes(1);
      expect(addSeriesMock).toHaveBeenCalledWith("CandlestickSeries-sentinel");
    });

    it("switching to lines mode removes the candlestick series and adds 5 line series on the main pane", () => {
      const { rerender } = render(<LightweightChart data={[]} onChartApi={() => {}} mode="candles" />);
      expect(addSeriesMock).toHaveBeenCalledTimes(1); // the candlestick series

      rerender(<LightweightChart data={[]} onChartApi={() => {}} mode="lines" />);

      expect(removeSeriesMock).toHaveBeenCalledTimes(1); // the candlestick series removed
      // 1 candlestick (mount) + 5 line series (bid/ask/mid/micro/price), every line series
      // call explicitly targets pane 0 (the main pane), never a fresh chart.addPane().
      expect(addSeriesMock).toHaveBeenCalledTimes(6);
      expect(addPaneMock).not.toHaveBeenCalled();
      for (let i = 1; i < 6; i++) {
        expect(addSeriesMock).toHaveBeenNthCalledWith(i + 1, "LineSeries-sentinel", expect.objectContaining({}), 0);
      }
    });

    it("switching from lines back to candles removes the 5 line series and re-adds one candlestick series", () => {
      const { rerender } = render(<LightweightChart data={[]} onChartApi={() => {}} mode="lines" />);
      expect(addSeriesMock).toHaveBeenCalledTimes(5); // 5 line series, no candlestick this time

      rerender(<LightweightChart data={[]} onChartApi={() => {}} mode="candles" />);

      expect(removeSeriesMock).toHaveBeenCalledTimes(5); // all 5 line series removed
      expect(addSeriesMock).toHaveBeenCalledTimes(6); // 5 line series + 1 candlestick
      expect(addSeriesMock).toHaveBeenNthCalledWith(6, "CandlestickSeries-sentinel");
    });

    it("never touches the chart's visible logical/time range across a Candles->Lines->Candles toggle (AC #3)", () => {
      const { rerender } = render(<LightweightChart data={[]} onChartApi={() => {}} mode="candles" />);

      rerender(<LightweightChart data={[]} onChartApi={() => {}} mode="lines" />);
      rerender(<LightweightChart data={[]} onChartApi={() => {}} mode="candles" />);

      // The mode-swap effect itself never calls getVisibleLogicalRange()/
      // setVisibleLogicalRange() -- those are only ever read/written by the data-effects'
      // own scroll-back shift-preservation, which never fires here since `data`/`linesData`
      // stay empty across every rerender (no `addedAtFront` growth to compensate for).
      expect(getVisibleLogicalRangeMock).not.toHaveBeenCalled();
      expect(setVisibleLogicalRangeMock).not.toHaveBeenCalled();
    });

    it("does not recreate the candlestick series on a data-only re-render while mode stays candles", () => {
      const { rerender } = render(<LightweightChart data={[]} onChartApi={() => {}} mode="candles" />);
      expect(addSeriesMock).toHaveBeenCalledTimes(1);

      rerender(<LightweightChart data={[{ time: 1700000000 as never }]} onChartApi={() => {}} mode="candles" />);

      expect(addSeriesMock).toHaveBeenCalledTimes(1); // still just the one, no remove/re-add
      expect(removeSeriesMock).not.toHaveBeenCalled();
    });
  });
});
