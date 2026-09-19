import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CreatePriceLineOptions } from "lightweight-charts";

import type { ChartMode, IndicatorPaneSpec, PriceLineSpec } from "./LightweightChart";

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
// Story 18.1: the chart's click/crosshair subscriptions and the main series'
// price-line API surface -- module-level shared mocks (same convention as setDataMock
// above) so tests can trigger/inspect them regardless of which series instance the
// component attached them to.
const subscribeClickMock = vi.fn();
const unsubscribeClickMock = vi.fn();
const subscribeCrosshairMoveMock = vi.fn();
const unsubscribeCrosshairMoveMock = vi.fn();
const createPriceLineMock = vi.fn();
const removePriceLineMock = vi.fn();
const priceToCoordinateMock = vi.fn();
const coordinateToPriceMock = vi.fn();

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
  return {
    setData: setDataMock,
    applyOptions,
    options: vi.fn(() => ({ color })),
    createPriceLine: createPriceLineMock,
    removePriceLine: removePriceLineMock,
    priceToCoordinate: priceToCoordinateMock,
    coordinateToPrice: coordinateToPriceMock,
  };
}

// A real IPriceLine's `.options()`/`.applyOptions()` round-trip, same rationale as
// makeSeriesMock above: the component's read-current-options-before-reapplying check
// needs something real to read. An unspecified create title defaults to "", mirroring
// the real library's PriceLineOptions default -- without that, a title-less spec
// would look "changed" on every diff.
function makePriceLineMock(options: CreatePriceLineOptions) {
  const applied = { ...options, title: options.title ?? "" };
  const applyOptions = vi.fn((opts: Partial<CreatePriceLineOptions>) => {
    Object.assign(applied, opts);
  });
  return { applyOptions, options: vi.fn(() => ({ ...applied })) };
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

// Story 15.9: the candlestick series' fallback color literals (LightweightChart.tsx's
// `cssVar(name, fallback)` calls) resolve deterministically under jsdom, since no
// stylesheet is ever loaded here -- `cssVar` always returns `fallback`. Asserting this
// exact object (not `expect.any(Object)`) is what actually catches a regression that
// scrambles or drops the token-derived up/down colors, the entire point of the AC #4
// chart-theming change.
const EXPECTED_CANDLESTICK_OPTIONS = {
  upColor: "#55ff55",
  downColor: "#ff5555",
  borderUpColor: "#55ff55",
  borderDownColor: "#ff5555",
  wickUpColor: "#55ff55",
  wickDownColor: "#ff5555",
  borderColor: "#555555",
  wickColor: "#555555",
};

function makePaneSpec(id: string, overrides: Partial<IndicatorPaneSpec> = {}): IndicatorPaneSpec {
  return { id, kind: "Line", data: [], color: "#123456", ...overrides };
}

// Default price 100 lines up with the identity priceToCoordinate/coordinateToPrice
// mocks above (a spec at price 100 sits at y=100), so drag tests need no coordinate
// overrides.
function makePriceLineSpec(id: string, overrides: Partial<PriceLineSpec> = {}): PriceLineSpec {
  return { id, price: 100, color: "#123456", ...overrides };
}

// The Story 18.1 tests' shared inert base props -- one element factory (same pattern
// as RankingsPage.test's pageElement) keeps each test's JSX down to the props it
// actually varies.
type ChartTestProps = {
  priceLines?: PriceLineSpec[];
  mode?: ChartMode;
  onPriceClick?: (price: number) => void;
  onPriceLineDrag?: (id: string, price: number) => void;
};

function chartElement(props: ChartTestProps) {
  return <LightweightChart data={[]} onChartApi={() => {}} {...props} />;
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
  subscribeClickMock.mockReset();
  unsubscribeClickMock.mockReset();
  subscribeCrosshairMoveMock.mockReset();
  unsubscribeCrosshairMoveMock.mockReset();
  createPriceLineMock.mockReset().mockImplementation((options: CreatePriceLineOptions) => makePriceLineMock(options));
  removePriceLineMock.mockReset();
  // Identity defaults: a spec at price P sits at y=P, and a clicked/dragged y of Y
  // reads back as price Y -- individual tests override these when they need
  // controlled conversions.
  priceToCoordinateMock.mockReset().mockImplementation((price: number) => price);
  coordinateToPriceMock.mockReset().mockImplementation((coordinate: number) => coordinate);
  createChartMock.mockReset().mockImplementation(() => ({
    addSeries: addSeriesMock,
    removeSeries: removeSeriesMock,
    applyOptions: applyOptionsMock,
    remove: removeMock,
    addPane: addPaneMock,
    removePane: removePaneMock,
    subscribeClick: subscribeClickMock,
    unsubscribeClick: unsubscribeClickMock,
    subscribeCrosshairMove: subscribeCrosshairMoveMock,
    unsubscribeCrosshairMove: unsubscribeCrosshairMoveMock,
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
    // Story 15.9: candle series now also carries up/down color options sourced from
    // the terminal theme tokens -- assert the exact options object, not just its shape.
    expect(addSeriesMock).toHaveBeenCalledWith("CandlestickSeries-sentinel", EXPECTED_CANDLESTICK_OPTIONS);
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
      expect(addSeriesMock).toHaveBeenCalledWith("CandlestickSeries-sentinel", EXPECTED_CANDLESTICK_OPTIONS);
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
      expect(addSeriesMock).toHaveBeenNthCalledWith(6, "CandlestickSeries-sentinel", EXPECTED_CANDLESTICK_OPTIONS);
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

  describe("priceLines registry (Story 18.1)", () => {
    it("creates a price line on the main series for a new id (AC #2)", () => {
      render(chartElement({ priceLines: [makePriceLineSpec("hline-1", { price: 61000.5 })] }));

      expect(createPriceLineMock).toHaveBeenCalledTimes(1);
      expect(createPriceLineMock).toHaveBeenCalledWith({
        id: "hline-1",
        price: 61000.5,
        color: "#123456",
        lineWidth: 1,
        axisLabelVisible: true,
        title: undefined,
      });
    });

    it("passes a spec's title through on create", () => {
      const spec = makePriceLineSpec("hline-1", { title: "TP" });
      render(chartElement({ priceLines: [spec] }));

      expect(createPriceLineMock).toHaveBeenCalledWith({
        id: "hline-1",
        price: 100,
        color: "#123456",
        lineWidth: 1,
        axisLabelVisible: true,
        title: "TP",
      });
    });

    it("applies only the changed price on an existing id, never a second createPriceLine", () => {
      const specA = makePriceLineSpec("hline-1", { price: 61000.5 });
      const { rerender } = render(chartElement({ priceLines: [specA] }));
      const line = createPriceLineMock.mock.results[0].value;

      rerender(chartElement({ priceLines: [{ ...specA, price: 62000 }] }));

      expect(createPriceLineMock).toHaveBeenCalledTimes(1);
      // Exactly one applyOptions carrying only the price -- the unchanged color and
      // the title normalization (unspecified === the library's "" default) must not
      // spuriously re-apply.
      expect(line.applyOptions).toHaveBeenCalledTimes(1);
      expect(line.applyOptions).toHaveBeenCalledWith({ price: 62000 });
    });

    it("applies only the changed color on an existing id", () => {
      const specA = makePriceLineSpec("hline-1");
      const { rerender } = render(chartElement({ priceLines: [specA] }));
      const line = createPriceLineMock.mock.results[0].value;

      rerender(chartElement({ priceLines: [{ ...specA, color: "#654321" }] }));

      expect(line.applyOptions).toHaveBeenCalledTimes(1);
      expect(line.applyOptions).toHaveBeenCalledWith({ color: "#654321" });
    });

    it("removes a dropped id via removePriceLine with the exact created instance, and re-adds cleanly", () => {
      const spec = makePriceLineSpec("hline-1");
      const { rerender } = render(chartElement({ priceLines: [spec] }));
      const line = createPriceLineMock.mock.results[0].value;

      rerender(chartElement({ priceLines: [] }));

      expect(removePriceLineMock).toHaveBeenCalledTimes(1);
      expect(removePriceLineMock).toHaveBeenCalledWith(line);

      // Re-adding the same id after removal creates exactly one fresh line, never a
      // silently-stale registry entry -- mirrors the panes registry's own test.
      rerender(chartElement({ priceLines: [spec] }));
      expect(createPriceLineMock).toHaveBeenCalledTimes(2);
    });

    it("re-creates every price line on the fresh series across a Candles->Lines->Candles toggle", () => {
      const spec = makePriceLineSpec("hline-1");
      const { rerender } = render(chartElement({ mode: "candles", priceLines: [spec] }));
      expect(createPriceLineMock).toHaveBeenCalledTimes(1);

      rerender(chartElement({ mode: "lines", priceLines: [spec] }));
      // Lines mode is a no-op for price lines, and the candles->lines series removal
      // kills them with the series -- no removePriceLine() on a series that is gone.
      expect(createPriceLineMock).toHaveBeenCalledTimes(1);
      expect(removePriceLineMock).not.toHaveBeenCalled();

      rerender(chartElement({ mode: "candles", priceLines: [spec] }));
      // The registry was cleared with the old series, so the same id counts as new on
      // the fresh candlestick series.
      expect(createPriceLineMock).toHaveBeenCalledTimes(2);
      expect(createPriceLineMock).toHaveBeenLastCalledWith(
        expect.objectContaining({ id: "hline-1", price: 100 }),
      );
    });

    it("never touches the chart's visible range while price lines are added, updated, or removed (AC #4)", () => {
      const spec = makePriceLineSpec("hline-1");
      const { rerender } = render(chartElement({ priceLines: [spec] }));
      rerender(chartElement({ priceLines: [{ ...spec, price: 200 }] }));
      rerender(chartElement({ priceLines: [] }));

      expect(setVisibleLogicalRangeMock).not.toHaveBeenCalled();
      expect(getVisibleLogicalRangeMock).not.toHaveBeenCalled();
    });
  });

  describe("price-line click/drag reporting (Story 18.1)", () => {
    it("reports a clicked y-coordinate as a price through onPriceClick (AC #2)", () => {
      const onPriceClick = vi.fn();
      coordinateToPriceMock.mockReturnValue(61000.5);
      render(chartElement({ onPriceClick }));

      const clickHandler = subscribeClickMock.mock.calls[0][0];
      clickHandler({ point: { x: 5, y: 100 } });

      // The conversion runs through the main series' coordinateToPrice with the
      // click's own y (AC #4: the series lives inside LightweightChart, so the
      // click->price conversion must too).
      expect(coordinateToPriceMock).toHaveBeenCalledWith(100);
      expect(onPriceClick).toHaveBeenCalledTimes(1);
      expect(onPriceClick).toHaveBeenCalledWith(61000.5);
    });

    it("does not subscribe to chart clicks when no onPriceClick callback is provided", () => {
      render(chartElement({}));

      expect(subscribeClickMock).not.toHaveBeenCalled();
    });

    it("does not fire onPriceClick for a click with no point", () => {
      const onPriceClick = vi.fn();
      render(chartElement({ onPriceClick }));

      subscribeClickMock.mock.calls[0][0]({});

      expect(onPriceClick).not.toHaveBeenCalled();
    });

    it("does not fire onPriceClick when the coordinate-to-price conversion returns null", () => {
      const onPriceClick = vi.fn();
      coordinateToPriceMock.mockReturnValue(null);
      render(chartElement({ onPriceClick }));

      subscribeClickMock.mock.calls[0][0]({ point: { x: 5, y: 100 } });

      expect(onPriceClick).not.toHaveBeenCalled();
    });

    it("does not subscribe to chart clicks in lines mode", () => {
      render(chartElement({ mode: "lines", onPriceClick: () => {} }));

      expect(subscribeClickMock).not.toHaveBeenCalled();
    });

    it("unsubscribes the click handler when the mode flips to lines", () => {
      const { rerender } = render(chartElement({ mode: "candles", onPriceClick: () => {} }));
      const clickHandler = subscribeClickMock.mock.calls[0][0];

      rerender(chartElement({ mode: "lines", onPriceClick: () => {} }));

      expect(unsubscribeClickMock).toHaveBeenCalledWith(clickHandler);
    });

    it("unsubscribes its crosshair handler on unmount", () => {
      const { unmount } = render(chartElement({ onPriceLineDrag: () => {} }));
      const crosshairHandler = subscribeCrosshairMoveMock.mock.calls[0][0];

      unmount();

      expect(unsubscribeCrosshairMoveMock).toHaveBeenCalledWith(crosshairHandler);
    });

    it("starts a drag from a mousedown over a hovered line, reporting prices until mouseup (AC #3)", () => {
      const onPriceLineDrag = vi.fn();
      const { container } = render(
        chartElement({ priceLines: [makePriceLineSpec("hline-1")], onPriceLineDrag }),
      );
      const crosshairHandler = subscribeCrosshairMoveMock.mock.calls[0][0];
      const candleSeries = addSeriesMock.mock.results[0].value;

      // A hover the library reports as "over this series' custom price line", at y=100
      // -- exactly where the identity priceToCoordinate puts a price-100 spec.
      crosshairHandler({
        point: { x: 10, y: 100 },
        paneIndex: 0,
        hoveredInfo: { objectKind: "custom-price-line", series: candleSeries },
      });
      fireEvent.mouseDown(container.firstElementChild!);
      crosshairHandler({ point: { x: 12, y: 150 }, paneIndex: 0 });

      expect(onPriceLineDrag).toHaveBeenCalledTimes(1);
      expect(onPriceLineDrag).toHaveBeenCalledWith("hline-1", 150);

      // The drag ends with the window-level mouseup (the release can happen outside
      // the chart): further crosshair moves report nothing.
      fireEvent.mouseUp(window);
      crosshairHandler({ point: { x: 14, y: 200 }, paneIndex: 0 });

      expect(onPriceLineDrag).toHaveBeenCalledTimes(1);
    });

    it("never starts a drag when the mousedown is not over one of the series' price lines", () => {
      const onPriceLineDrag = vi.fn();
      const { container } = render(
        chartElement({ priceLines: [makePriceLineSpec("hline-1")], onPriceLineDrag }),
      );
      const crosshairHandler = subscribeCrosshairMoveMock.mock.calls[0][0];
      const candleSeries = addSeriesMock.mock.results[0].value;

      // Hovering the series itself (not a custom price line) must not arm a grab.
      crosshairHandler({
        point: { x: 10, y: 100 },
        paneIndex: 0,
        hoveredInfo: { objectKind: "series", series: candleSeries },
      });
      fireEvent.mouseDown(container.firstElementChild!);
      crosshairHandler({ point: { x: 12, y: 150 }, paneIndex: 0 });

      expect(onPriceLineDrag).not.toHaveBeenCalled();
    });

    it("never starts a drag from a mousedown with no preceding crosshair point", () => {
      const onPriceLineDrag = vi.fn();
      const { container } = render(
        chartElement({ priceLines: [makePriceLineSpec("hline-1")], onPriceLineDrag }),
      );
      const crosshairHandler = subscribeCrosshairMoveMock.mock.calls[0][0];

      // Mouse left the chart: the param carries no point, clearing the remembered
      // hover -- a following mousedown has nothing to hit-test against.
      crosshairHandler({});
      fireEvent.mouseDown(container.firstElementChild!);
      crosshairHandler({ point: { x: 12, y: 150 }, paneIndex: 0 });

      expect(onPriceLineDrag).not.toHaveBeenCalled();
    });

    it("suppresses the chart click that immediately follows a line-grab mousedown, one-shot", () => {
      const onPriceClick = vi.fn();
      const { container } = render(
        chartElement({
          priceLines: [makePriceLineSpec("hline-1")],
          onPriceLineDrag: () => {},
          onPriceClick,
        }),
      );
      const crosshairHandler = subscribeCrosshairMoveMock.mock.calls[0][0];
      const clickHandler = subscribeClickMock.mock.calls[0][0];
      const candleSeries = addSeriesMock.mock.results[0].value;

      crosshairHandler({
        point: { x: 10, y: 100 },
        paneIndex: 0,
        hoveredInfo: { objectKind: "custom-price-line", series: candleSeries },
      });
      fireEvent.mouseDown(container.firstElementChild!); // grabs the line
      fireEvent.mouseUp(window);
      // lightweight-charts still fires a click for a press that never moved -- it
      // must NOT place a new line under the cursor.
      clickHandler({ point: { x: 10, y: 100 } });

      expect(onPriceClick).not.toHaveBeenCalled();

      // The suppression is one-shot: the next click reports normally again.
      clickHandler({ point: { x: 10, y: 100 } });

      expect(onPriceClick).toHaveBeenCalledTimes(1);
    });
  });
});
