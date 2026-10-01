import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { CHART_TOKENS } from "./chartTheme";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CreatePriceLineOptions, Time } from "lightweight-charts";

import type { ChartMode, DrawingSpec, IndicatorPaneSpec, PriceLineSpec, TrendlineSpec, VolumeProfileSpec } from "./LightweightChart";
import { TrendlinePrimitive } from "./primitives/TrendlinePrimitive";
import { FibPrimitive } from "./primitives/FibPrimitive";
import { PositionPrimitive } from "./primitives/PositionPrimitive";
import { type DragPoint, type FibDrawing, type PositionDrawing, defaultFibLevels, newPosition } from "../../lib/drawings";
import { GapPrimitive } from "./primitives/GapPrimitive";

const addSeriesMock = vi.fn();
const seriesUpdateMock = vi.fn();
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
// Story 18.2: the host series' primitive API and the time scale's x<->time conversions.
const attachPrimitiveMock = vi.fn();
const detachPrimitiveMock = vi.fn();
// Story 32.1: every price host and every non-overlay pane carries a GapPrimitive for its whole
// life, so its attach/detach calls are recorded apart (with the host series) -- the drawing,
// marker, measurement and profile assertions above keep counting only their own primitives.
const gapAttachMock = vi.fn();
const gapDetachMock = vi.fn();
const coordinateToTimeMock = vi.fn();
const timeToCoordinateMock = vi.fn();
const fitContentMock = vi.fn();
const scrollToRealTimeMock = vi.fn();
// Story 32.6: the visible-range subscription, shared so a test can fire the handler.
const subscribeRangeMock = vi.fn();
const unsubscribeRangeMock = vi.fn();

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
    getHTMLElement: () => document.createElement("div"),
    addSeries: vi.fn(() => ({ setData: paneSeriesSetDataMock, applyOptions: paneApplyOptionsMock })),
    getSeries: vi.fn(() => []),
    setStretchFactor: vi.fn(),
    getStretchFactor: vi.fn(() => 1),
    // Story 32.2: 0 = "not laid out yet"; a test sets the px the library would report.
    getHeight: vi.fn(() => 0),
    moveTo: vi.fn(),
  };
}

// Story 32.2: the price pane (index 0) and the time axis the library reserves inside the chart.
const pricePaneMock = {
  paneIndex: () => 0,
  getHTMLElement: () => document.createElement("div"),
  setStretchFactor: vi.fn(),
  getHeight: vi.fn(() => 0),
};
const TIME_AXIS_PX = 28;
const timeScaleHeightMock = vi.fn(() => TIME_AXIS_PX);
type PaneMock = ReturnType<typeof makePaneMock>;
function addedPane(n: number): PaneMock {
  return addPaneMock.mock.results[n].value as PaneMock;
}
function lastChartHeight(): number | undefined {
  const calls = applyOptionsMock.mock.calls.filter((c) => (c[0] as { height?: number }).height !== undefined);
  return (calls[calls.length - 1]?.[0] as { height: number } | undefined)?.height;
}

// A real ISeriesApi's `.options().color`/`.applyOptions({color})` round-trip, so the
// component's own read-current-color-before-reapplying check (LightweightChart.tsx) has
// something real to read -- each call site (candlestick or an indicator pane) gets its
// own closed-over color, not one shared across every series in the test.
function makeSeriesMock(initial: Record<string, unknown> | undefined) {
  // Every option the component creates a series with or applies later round-trips, so its
  // read-current-options-before-reapplying checks (colour, visible, lineWidth, lineStyle) see them.
  const state: Record<string, unknown> = { ...initial };
  const applyOptions = vi.fn((opts: Record<string, unknown>) => {
    Object.assign(state, opts);
  });
  const series = {
    setData: setDataMock,
    update: seriesUpdateMock,
    applyOptions,
    options: vi.fn(() => ({ ...state })),
    createPriceLine: createPriceLineMock,
    removePriceLine: removePriceLineMock,
    priceToCoordinate: priceToCoordinateMock,
    coordinateToPrice: coordinateToPriceMock,
    attachPrimitive: (p: unknown) => (p instanceof GapPrimitive ? gapAttachMock(p, series) : attachPrimitiveMock(p)),
    detachPrimitive: (p: unknown) => (p instanceof GapPrimitive ? gapDetachMock(p, series) : detachPrimitiveMock(p)),
  };
  return series;
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
// `canvas` npm package (dependency-minimization preference, platform/CLAUDE.md).
vi.mock("lightweight-charts", () => ({
  CandlestickSeries: "CandlestickSeries-sentinel",
  LineSeries: "LineSeries-sentinel",
  HistogramSeries: "HistogramSeries-sentinel",
  LineStyle: { Solid: 0, Dotted: 1, Dashed: 2 },
  createChart: (...args: unknown[]) => createChartMock(...args),
}));

const { default: LightweightChart, INDICATOR_PANE_PX, PRICE_PANE_PX, VOLUME_PANE_PX, VISIBLE_BARS_DEBOUNCE_MS } = await import("./LightweightChart");

// Story 15.9: the candlestick series' fallback color literals (LightweightChart.tsx's
// `cssVar(name, fallback)` calls) resolve deterministically under jsdom, since no
// stylesheet is ever loaded here -- `cssVar` always returns `fallback`. Asserting this
// exact object (not `expect.any(Object)`) is what actually catches a regression that
// scrambles or drops the token-derived up/down colors, the entire point of the AC #4
// chart-theming change.
const EXPECTED_CANDLESTICK_OPTIONS = {
  // Story 32.4: the chart's own light-palette tokens (jsdom has no stylesheet, so the fallbacks).
  upColor: CHART_TOKENS["--chart-up"],
  downColor: CHART_TOKENS["--chart-down"],
  borderUpColor: CHART_TOKENS["--chart-up"],
  borderDownColor: CHART_TOKENS["--chart-down"],
  wickUpColor: CHART_TOKENS["--chart-up"],
  wickDownColor: CHART_TOKENS["--chart-down"],
  borderColor: CHART_TOKENS["--chart-text-dim"],
  wickColor: CHART_TOKENS["--chart-text-dim"],
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
  drawings?: DrawingSpec[];
  drawEditable?: boolean;
  onDrawingColor?: (id: string, color: string) => void;
  onDrawingDelete?: (id: string) => void;
  measureActive?: boolean;
  onMeasureEnd?: () => void;
  data?: { time: Time; open: number; high: number; low: number; close: number }[];
  markerTime?: Time | null;
  volumeProfiles?: VolumeProfileSpec[];
  crosshairVisible?: boolean;
  viewCommand?: { kind: "fit" | "latest"; seq: number } | null;
  rangeSelectActive?: boolean;
  onRangeSelect?: (start: { time: Time; price: number }, end: { time: Time; price: number }) => void;
  profileEdgesEditable?: boolean;
  onProfileEdgeDrag?: (id: string, edge: "start" | "end", time: Time) => void;
  onProfileEdgeCommit?: (id: string, edge: "start" | "end", time: Time) => void;
  onPointClick?: (point: { time: Time; price: number }) => void;
  precision?: { price: number; size: number } | null;
  onDrawingDrag?: (id: string, handle: string, point: DragPoint) => void;
  onDrawingSettings?: (id: string) => void;
  fibActive?: boolean;
  onFibPlace?: (a: { time: Time; price: number }, b: { time: Time; price: number }) => void;
  panes?: IndicatorPaneSpec[];
  initialPaneHeights?: Record<string, number>;
  onPaneHeights?: (heights: Record<string, number>) => void;
  initialVisibleBars?: number;
  onVisibleBars?: (bars: number) => void;
};

function chartElement(props: ChartTestProps) {
  return <LightweightChart data={[]} onChartApi={() => {}} {...props} />;
}

beforeEach(() => {
  nextPaneIndex = 1;
  pricePaneMock.setStretchFactor.mockReset();
  pricePaneMock.getHeight.mockReset().mockReturnValue(0);
  timeScaleHeightMock.mockReset().mockReturnValue(TIME_AXIS_PX);
  setDataMock.mockReset();
  seriesUpdateMock.mockReset();
  addSeriesMock
    .mockReset()
    .mockImplementation((_definition: unknown, options?: Record<string, unknown>) => makeSeriesMock(options));
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
  attachPrimitiveMock.mockReset();
  detachPrimitiveMock.mockReset();
  gapAttachMock.mockReset();
  gapDetachMock.mockReset();
  coordinateToTimeMock.mockReset().mockReturnValue(null);
  fitContentMock.mockReset();
  scrollToRealTimeMock.mockReset();
  subscribeRangeMock.mockReset();
  unsubscribeRangeMock.mockReset();
  timeToCoordinateMock.mockReset().mockImplementation((t: number) => t);
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
    panes: () => [pricePaneMock],
    subscribeClick: subscribeClickMock,
    unsubscribeClick: unsubscribeClickMock,
    subscribeCrosshairMove: subscribeCrosshairMoveMock,
    unsubscribeCrosshairMove: unsubscribeCrosshairMoveMock,
    timeScale: () => ({
      height: timeScaleHeightMock,
      getVisibleLogicalRange: getVisibleLogicalRangeMock,
      setVisibleLogicalRange: setVisibleLogicalRangeMock,
      coordinateToTime: coordinateToTimeMock,
      timeToCoordinate: timeToCoordinateMock,
      logicalToCoordinate: (i: number) => i * 10,
      coordinateToLogical: (x: number) => x / 10,
      fitContent: fitContentMock,
      scrollToRealTime: scrollToRealTimeMock,
      subscribeVisibleLogicalRangeChange: subscribeRangeMock,
      unsubscribeVisibleLogicalRangeChange: unsubscribeRangeMock,
    }),
  }));
});

afterEach(() => {
  cleanup();
});

describe("LightweightChart", () => {
  it("paints the live bar as-is on the candle series and its volume on the volume pane (no client merge)", () => {
    const historyBar = { time: 60 as never, open: 5, high: 50, low: 1, close: 6 };
    const element = (liveBar: Parameters<typeof LightweightChart>[0]["liveBar"]) => (
      <LightweightChart
        data={[historyBar]}
        onChartApi={() => {}}
        panes={[makePaneSpec("volume", { kind: "Histogram" })]}
        liveBar={liveBar}
      />
    );
    const { rerender } = render(element(null));
    // A tick arriving after mount (the volume pane is registered by then).
    rerender(element({ time: 60 as never, open: 7, high: 8, low: 6, close: 7.5, volume: 42 }));

    expect(seriesUpdateMock).toHaveBeenCalledWith({ time: 60, open: 7, high: 8, low: 6, close: 7.5 });
    expect(seriesUpdateMock).toHaveBeenCalledWith({ time: 60, value: 42 });
  });

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

  describe("pane heights grow the page (Story 32.2)", () => {
    const volumeSpec = () => makePaneSpec("volume", { kind: "Histogram" });
    const rsiSpec = () => makePaneSpec("RSI");
    const element = (panes: IndicatorPaneSpec[]) => (
      <LightweightChart data={[]} onChartApi={() => {}} panes={panes} />
    );
    // Σ pane px + one 1 px separator per extra pane + the time axis the library reserves.
    const total = (extras: number[]) =>
      PRICE_PANE_PX + extras.reduce((a, b) => a + b, 0) + extras.length + TIME_AXIS_PX;

    it("sizes the default chart to the price pane plus the volume pane", () => {
      render(element([volumeSpec()]));

      expect(lastChartHeight()).toBe(total([VOLUME_PANE_PX]));
      expect(addedPane(0).setStretchFactor).toHaveBeenLastCalledWith(VOLUME_PANE_PX);
      expect(pricePaneMock.setStretchFactor).toHaveBeenLastCalledWith(PRICE_PANE_PX);
    });

    it("adds a pane's default px to the height and leaves price and volume at their px, then removes it again", () => {
      const { rerender } = render(element([volumeSpec()]));
      // The library reports what the first layout produced.
      pricePaneMock.getHeight.mockReturnValue(PRICE_PANE_PX);
      addedPane(0).getHeight.mockReturnValue(VOLUME_PANE_PX);

      rerender(element([volumeSpec(), rsiSpec()]));
      expect(lastChartHeight()).toBe(total([VOLUME_PANE_PX, INDICATOR_PANE_PX]));
      expect(addedPane(1).setStretchFactor).toHaveBeenLastCalledWith(INDICATOR_PANE_PX);
      expect(addedPane(0).setStretchFactor).toHaveBeenLastCalledWith(VOLUME_PANE_PX);
      expect(pricePaneMock.setStretchFactor).toHaveBeenLastCalledWith(PRICE_PANE_PX);

      rerender(element([volumeSpec()]));
      expect(lastChartHeight()).toBe(total([VOLUME_PANE_PX]));
    });

    it("keeps a divider the operator dragged when another pane is added", () => {
      const { rerender } = render(element([volumeSpec()]));
      pricePaneMock.getHeight.mockReturnValue(PRICE_PANE_PX);
      addedPane(0).getHeight.mockReturnValue(200); // dragged from 120

      rerender(element([volumeSpec(), makePaneSpec("MACD")]));

      expect(addedPane(0).setStretchFactor).toHaveBeenLastCalledWith(200);
      expect(lastChartHeight()).toBe(total([200, INDICATOR_PANE_PX]));
    });

    it("draws no volume pane when it is off, and the chart is the price pane alone", () => {
      render(element([]));

      expect(addPaneMock).not.toHaveBeenCalled();
      expect(lastChartHeight()).toBe(total([]));
    });

    it("returns volume as the first pane under price when switched back on", () => {
      const { rerender } = render(element([rsiSpec()]));
      rerender(element([volumeSpec(), rsiSpec()]));

      // The volume pane is the second one created, so it must be moved up to index 1.
      expect(addedPane(1).moveTo).toHaveBeenCalledWith(1);
    });

    it("paints a live bar with the volume pane off: the candle updates, the volume update is a no-op", () => {
      const bar = { time: 60 as never, open: 5, high: 50, low: 1, close: 6 };
      const live = (volume: number) => (
        <LightweightChart
          data={[bar]}
          onChartApi={() => {}}
          panes={[]}
          liveBar={{ ...bar, close: 7, volume }}
        />
      );
      const { rerender } = render(live(1));
      rerender(live(42));

      expect(seriesUpdateMock).toHaveBeenCalledWith({ time: 60, open: 5, high: 50, low: 1, close: 7 });
      expect(seriesUpdateMock).not.toHaveBeenCalledWith({ time: 60, value: 42 });
    });

    it("trusts the panes' own heights only once the time axis was measured", () => {
      timeScaleHeightMock.mockReturnValue(0);
      const { rerender } = render(element([volumeSpec()]));
      // Pre-paint: the library's price pane is shorter than the 500 px budget (axis taken out).
      pricePaneMock.getHeight.mockReturnValue(PRICE_PANE_PX - TIME_AXIS_PX);

      rerender(element([volumeSpec(), rsiSpec()]));

      expect(pricePaneMock.setStretchFactor).toHaveBeenLastCalledWith(PRICE_PANE_PX);
    });

    it("gives every extra pane its default, not its axis-less pre-paint height, until the axis is measured", () => {
      timeScaleHeightMock.mockReturnValue(0);
      const { rerender } = render(element([volumeSpec()]));
      // Pre-paint: the library squeezed the volume pane by its share of the unmeasured axis.
      addedPane(0).getHeight.mockReturnValue(VOLUME_PANE_PX - 5);

      rerender(element([volumeSpec(), rsiSpec()]));

      expect(addedPane(0).setStretchFactor).toHaveBeenLastCalledWith(VOLUME_PANE_PX);
    });

    it("leaves the heights alone on a data-only refresh, so a divider being dragged is not re-pinned", () => {
      const { rerender } = render(element([volumeSpec()]));
      const heightCalls = () =>
        applyOptionsMock.mock.calls.filter((c) => (c[0] as { height?: number }).height !== undefined).length;
      const before = heightCalls();
      addedPane(0).setStretchFactor.mockClear();

      rerender(element([makePaneSpec("volume", { kind: "Histogram", data: [{ time: 60 as never, value: 1 }] })]));

      expect(heightCalls()).toBe(before);
      expect(addedPane(0).setStretchFactor).not.toHaveBeenCalled();
    });
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
    // A line series always gets the library's default width and style when the spec states none.
    expect(addSeriesMock).toHaveBeenNthCalledWith(
      2,
      "LineSeries-sentinel",
      { color: "#2962ff", lineWidth: 3, lineStyle: 0 },
      1,
    );
  });

  it("draws an overlay inside the price pane (index 0) with no new pane, and removes just its series", () => {
    const overlay = [makePaneSpec("SimpleMovingAverage", { placement: "overlay", color: "#2962ff" })];
    const { rerender } = render(<LightweightChart data={[]} onChartApi={() => {}} panes={overlay} />);

    expect(addPaneMock).not.toHaveBeenCalled();
    expect(addSeriesMock).toHaveBeenNthCalledWith(
      2,
      "LineSeries-sentinel",
      { color: "#2962ff", lineWidth: 3, lineStyle: 0 },
      0,
    );

    rerender(<LightweightChart data={[]} onChartApi={() => {}} panes={[]} />);
    expect(removeSeriesMock).toHaveBeenCalledTimes(1);
    expect(removePaneMock).not.toHaveBeenCalled();
  });

  it("puts outputs of one group in a single pane, and removes that pane only with its last output", () => {
    const both = [
      makePaneSpec("MACD.macd", { group: "MACD" }),
      makePaneSpec("MACD.signal", { group: "MACD" }),
    ];
    const { rerender } = render(<LightweightChart data={[]} onChartApi={() => {}} panes={both} />);

    expect(addPaneMock).toHaveBeenCalledTimes(1);
    expect(addSeriesMock.mock.calls.slice(1).map((c) => c[2])).toEqual([1, 1]); // same pane index

    rerender(<LightweightChart data={[]} onChartApi={() => {}} panes={[both[0]]} />);
    expect(removePaneMock).not.toHaveBeenCalled();
    expect(removeSeriesMock).toHaveBeenCalledTimes(1);

    rerender(<LightweightChart data={[]} onChartApi={() => {}} panes={[]} />);
    expect(removePaneMock).toHaveBeenCalledTimes(1);
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
        lineStyle: 0,
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
        lineStyle: 0,
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

    it("never starts a drag from a right or middle press over a hovered line (Story 32.5)", () => {
      const onPriceLineDrag = vi.fn();
      const { container } = render(
        chartElement({ priceLines: [makePriceLineSpec("hline-1")], onPriceLineDrag }),
      );
      const crosshairHandler = subscribeCrosshairMoveMock.mock.calls[0][0];
      const candleSeries = addSeriesMock.mock.results[0].value;
      const hover = { objectKind: "custom-price-line", series: candleSeries };

      for (const button of [2, 1]) {
        crosshairHandler({ point: { x: 10, y: 100 }, paneIndex: 0, hoveredInfo: hover });
        fireEvent.mouseDown(container.firstElementChild!, { button });
        crosshairHandler({ point: { x: 12, y: 150 }, paneIndex: 0 });
      }

      expect(onPriceLineDrag).not.toHaveBeenCalled();
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

    it("opens an edit menu on a hline click and reports color / delete", () => {
      const onDrawingColor = vi.fn();
      const onDrawingDelete = vi.fn();
      render(
        chartElement({
          priceLines: [makePriceLineSpec("hline-1", { color: "#123456" })],
          drawEditable: true,
          onDrawingColor,
          onDrawingDelete,
        }),
      );
      const clickHandler = subscribeClickMock.mock.calls.at(-1)![0];
      act(() => clickHandler({ point: { x: 10, y: 102 }, sourceEvent: { clientX: 5, clientY: 5 } }));

      fireEvent.change(screen.getByLabelText("Line color"), { target: { value: "#ff0000" } });
      expect(onDrawingColor).toHaveBeenCalledWith("hline-1", "#ff0000");
      fireEvent.click(screen.getByText("Delete"));
      expect(onDrawingDelete).toHaveBeenCalledWith("hline-1");
    });
  });
});

function makeTrendlineSpec(id: string, overrides: Partial<TrendlineSpec> = {}): TrendlineSpec {
  return {
    id,
    kind: "trendline",
    anchors: [
      { time: 100, price: 10 },
      { time: 200, price: 20 },
    ],
    color: "#123456",
    ...overrides,
  };
}

describe("drawings registry (Story 18.2)", () => {
  it("attaches one primitive per new id, and only once across re-renders (AC #4)", () => {
    const drawings = [makeTrendlineSpec("trendline-1")];
    const { rerender } = render(chartElement({ drawings }));
    rerender(chartElement({ drawings }));

    expect(attachPrimitiveMock).toHaveBeenCalledTimes(1);
    expect(attachPrimitiveMock.mock.calls[0][0]).toBeInstanceOf(TrendlinePrimitive);
  });

  it("detaches the primitive of a removed id and leaves the others", () => {
    const { rerender } = render(
      chartElement({ drawings: [makeTrendlineSpec("trendline-1"), makeTrendlineSpec("trendline-2")] }),
    );
    const first = attachPrimitiveMock.mock.calls[0][0];

    rerender(chartElement({ drawings: [makeTrendlineSpec("trendline-2")] }));

    expect(detachPrimitiveMock).toHaveBeenCalledTimes(1);
    expect(detachPrimitiveMock).toHaveBeenCalledWith(first);
  });

  it("updates an existing primitive in place when its anchors change, without re-attaching", () => {
    const { rerender } = render(chartElement({ drawings: [makeTrendlineSpec("trendline-1")] }));
    const primitive = attachPrimitiveMock.mock.calls[0][0] as TrendlinePrimitive;
    const updateSpy = vi.spyOn(primitive, "update");
    const moved = makeTrendlineSpec("trendline-1", {
      anchors: [
        { time: 100, price: 11 },
        { time: 200, price: 21 },
      ],
    });

    rerender(chartElement({ drawings: [moved] }));

    expect(updateSpy).toHaveBeenCalledWith(moved.anchors, "#123456");
    expect(attachPrimitiveMock).toHaveBeenCalledTimes(1);
  });

  it("re-attaches every drawing on the new host series after a mode flip", () => {
    const drawings = [makeTrendlineSpec("trendline-1")];
    const { rerender } = render(chartElement({ drawings }));
    rerender(chartElement({ drawings, mode: "lines" }));

    expect(attachPrimitiveMock).toHaveBeenCalledTimes(2);
  });

  it("reports a click as a {time, price} point, falling back to coordinateToTime (AC #2)", () => {
    const onPointClick = vi.fn();
    coordinateToPriceMock.mockReturnValue(61000.5);
    render(chartElement({ onPointClick }));
    const clickHandler = subscribeClickMock.mock.calls[0][0];

    clickHandler({ point: { x: 5, y: 100 }, time: 1234 });
    coordinateToTimeMock.mockReturnValue(5678);
    clickHandler({ point: { x: 9, y: 100 } });
    coordinateToTimeMock.mockReturnValue(null);
    clickHandler({ point: { x: 9, y: 100 } });

    expect(onPointClick.mock.calls).toEqual([
      [{ time: 1234, price: 61000.5 }],
      [{ time: 5678, price: 61000.5 }],
    ]);
  });
});

describe("TrendlinePrimitive (Story 18.2)", () => {
  it("recomputes screen coordinates from the same anchors after a pan/zoom (AC #3)", () => {
    let offset = 0;
    const primitive = new TrendlinePrimitive(
      [
        { time: 100 as Time, price: 10 },
        { time: 200 as Time, price: 20 },
      ],
      "#fff",
    );
    const requestUpdate = vi.fn();
    primitive.attached({
      chart: { timeScale: () => ({ timeToCoordinate: (t: number) => t + offset }) },
      series: { priceToCoordinate: (p: number) => p * 2 },
      requestUpdate,
    } as never);

    primitive.updateAllViews();
    expect(primitive.screenPoints()).toEqual([
      { x: 100, y: 20 },
      { x: 200, y: 40 },
    ]);

    offset = -50; // the view scrolled; anchors are untouched
    primitive.updateAllViews();
    expect(primitive.screenPoints()).toEqual([
      { x: 50, y: 20 },
      { x: 150, y: 40 },
    ]);
  });

  it("has no drawable points while an anchor is outside the coordinate space", () => {
    const primitive = new TrendlinePrimitive(
      [
        { time: 100 as Time, price: 10 },
        { time: 200 as Time, price: 20 },
      ],
      "#fff",
    );
    primitive.attached({
      chart: { timeScale: () => ({ timeToCoordinate: (t: number) => (t === 100 ? null : t) }) },
      series: { priceToCoordinate: (p: number) => p },
      requestUpdate: vi.fn(),
    } as never);

    primitive.updateAllViews();

    expect(primitive.screenPoints()).toBeNull();
  });
});

describe("measurement drag (Story 18.3)", () => {
  beforeEach(() => {
    coordinateToTimeMock.mockImplementation((x: number) => x);
  });

  it("attaches a transient primitive on drag and removes it on release, reporting the end", () => {
    const onMeasureEnd = vi.fn();
    const { container } = render(chartElement({ measureActive: true, onMeasureEnd }));
    const target = container.firstElementChild!;

    fireEvent.mouseDown(target, { clientX: 10, clientY: 100, button: 0 });
    expect(attachPrimitiveMock).not.toHaveBeenCalled();
    fireEvent.mouseMove(window, { buttons: 1, clientX: 50, clientY: 150 });
    expect(attachPrimitiveMock).toHaveBeenCalledTimes(1);
    expect(detachPrimitiveMock).not.toHaveBeenCalled();
    fireEvent.mouseUp(window);

    expect(detachPrimitiveMock).toHaveBeenCalledTimes(1);
    expect(detachPrimitiveMock.mock.calls[0][0]).toBe(attachPrimitiveMock.mock.calls[0][0]);
    expect(onMeasureEnd).toHaveBeenCalledTimes(1);
  });

  it("cancels mid-drag with no residue when measureActive goes false (Esc)", () => {
    const { container, rerender } = render(chartElement({ measureActive: true }));
    fireEvent.mouseDown(container.firstElementChild!, { clientX: 10, clientY: 100, button: 0 });
    fireEvent.mouseMove(window, { buttons: 1, clientX: 50, clientY: 150 });

    rerender(chartElement({ measureActive: false }));

    expect(detachPrimitiveMock).toHaveBeenCalledTimes(1);
  });

  it("keeps the tool armed after a click without a drag", () => {
    const onMeasureEnd = vi.fn();
    const { container } = render(chartElement({ measureActive: true, onMeasureEnd }));

    fireEvent.mouseDown(container.firstElementChild!, { clientX: 10, clientY: 100, button: 0 });
    fireEvent.mouseUp(window);

    expect(attachPrimitiveMock).not.toHaveBeenCalled();
    expect(onMeasureEnd).not.toHaveBeenCalled();
  });

  it("does nothing while measureActive is false", () => {
    const { container } = render(chartElement({}));

    fireEvent.mouseDown(container.firstElementChild!, { clientX: 10, clientY: 100, button: 0 });

    expect(attachPrimitiveMock).not.toHaveBeenCalled();
  });
});

describe("replay support (Story 18.4)", () => {
  const b = (n: number) => ({ time: n as Time, open: 1, high: 2, low: 1, close: 1 });

  it("does not shift the visible range when bars are added or removed at the newest end", () => {
    const { rerender } = render(chartElement({ data: [b(60), b(120), b(180)] }));

    rerender(chartElement({ data: [b(60)] }));
    rerender(chartElement({ data: [b(60), b(120)] }));

    expect(setVisibleLogicalRangeMock).not.toHaveBeenCalled();
  });

  it("still compensates the visible range for older bars prepended at the front", () => {
    const { rerender } = render(chartElement({ data: [b(60), b(120)] }));

    rerender(chartElement({ data: [b(0), b(30), b(60), b(120)] }));

    expect(setVisibleLogicalRangeMock).toHaveBeenCalledWith({ from: 12, to: 52 });
  });

  it("attaches, moves and detaches the start marker", () => {
    const { rerender } = render(chartElement({ markerTime: 60 as Time }));
    expect(attachPrimitiveMock).toHaveBeenCalledTimes(1);
    const marker = attachPrimitiveMock.mock.calls[0][0];

    rerender(chartElement({ markerTime: 120 as Time }));
    expect(attachPrimitiveMock).toHaveBeenCalledTimes(1);

    rerender(chartElement({ markerTime: null }));
    expect(detachPrimitiveMock).toHaveBeenCalledWith(marker);
  });
});

describe("volumeProfiles registry (Story 18.5)", () => {
  const profileSpec = (id: string, overrides: Partial<VolumeProfileSpec> = {}): VolumeProfileSpec => ({
    id,
    profile: { rows: [], poc: 0, vah: 0, val: 0, totalVolume: 0 },
    xAnchor: "right",
    width: 100,
    upColor: "#0f0",
    downColor: "#f00",
    showPoc: true,
    showValueArea: true,
    ...overrides,
  });

  it("attaches once per id, updates in place, and detaches a removed id", () => {
    const first = profileSpec("p1");
    const { rerender } = render(chartElement({ volumeProfiles: [first, profileSpec("p2")] }));
    expect(attachPrimitiveMock).toHaveBeenCalledTimes(2);
    const primitive = attachPrimitiveMock.mock.calls[0][0];

    rerender(chartElement({ volumeProfiles: [profileSpec("p1", { width: 50 }), profileSpec("p2")] }));
    expect(attachPrimitiveMock).toHaveBeenCalledTimes(2);

    rerender(chartElement({ volumeProfiles: [profileSpec("p2")] }));
    expect(detachPrimitiveMock).toHaveBeenCalledTimes(1);
    expect(detachPrimitiveMock).toHaveBeenCalledWith(primitive);
  });
});

describe("FRVP range select and edge drag (Story 18.6)", () => {
  beforeEach(() => {
    coordinateToTimeMock.mockImplementation((x: number) => x);
  });

  it("previews a drag without calculating, then reports exactly one range on release (AC #2)", () => {
    const onRangeSelect = vi.fn();
    const { container } = render(chartElement({ rangeSelectActive: true, onRangeSelect }));
    const target = container.firstElementChild!;

    fireEvent.mouseDown(target, { clientX: 10, clientY: 100, button: 0 });
    fireEvent.mouseMove(window, { buttons: 1, clientX: 20, clientY: 110 });
    fireEvent.mouseMove(window, { buttons: 1, clientX: 60, clientY: 130 });
    expect(onRangeSelect).not.toHaveBeenCalled();
    expect(attachPrimitiveMock).toHaveBeenCalledTimes(1);
    fireEvent.mouseUp(window);

    expect(onRangeSelect).toHaveBeenCalledTimes(1);
    expect(onRangeSelect).toHaveBeenCalledWith({ time: 10, price: 100 }, { time: 60, price: 130 });
    expect(detachPrimitiveMock).toHaveBeenCalledTimes(1);
  });

  it("reports nothing for a click without a drag, and cancels cleanly on disarm", () => {
    const onRangeSelect = vi.fn();
    const { container, rerender } = render(chartElement({ rangeSelectActive: true, onRangeSelect }));
    const target = container.firstElementChild!;

    fireEvent.mouseDown(target, { clientX: 10, clientY: 100, button: 0 });
    fireEvent.mouseUp(window);
    expect(onRangeSelect).not.toHaveBeenCalled();

    fireEvent.mouseDown(target, { clientX: 10, clientY: 100, button: 0 });
    fireEvent.mouseMove(window, { buttons: 1, clientX: 60, clientY: 130 });
    rerender(chartElement({ rangeSelectActive: false, onRangeSelect }));
    expect(detachPrimitiveMock).toHaveBeenCalledTimes(1);
    expect(onRangeSelect).not.toHaveBeenCalled();
  });

  it("finishes a drag whose mouseup was lost (move with no button held)", () => {
    const onRangeSelect = vi.fn();
    const { container } = render(chartElement({ rangeSelectActive: true, onRangeSelect }));

    fireEvent.mouseDown(container.firstElementChild!, { clientX: 10, clientY: 100, button: 0 });
    fireEvent.mouseMove(window, { buttons: 1, clientX: 60, clientY: 130 });
    fireEvent.mouseMove(window, { buttons: 0, clientX: 90, clientY: 130 });

    expect(onRangeSelect).toHaveBeenCalledTimes(1);
    expect(onRangeSelect).toHaveBeenCalledWith({ time: 10, price: 100 }, { time: 60, price: 130 });
  });

  describe("edge drag", () => {
    const spec: VolumeProfileSpec = {
      id: "frvp-1",
      profile: {
        rows: [
          { priceLow: 10, priceHigh: 15, upVolume: 1, downVolume: 1 },
          { priceLow: 15, priceHigh: 20, upVolume: 1, downVolume: 1 },
        ],
        poc: 12,
        vah: 20,
        val: 10,
        totalVolume: 4,
      },
      xAnchor: { time: 100 as Time },
      width: { toTime: 300 as Time },
      upColor: "#0f0",
      downColor: "#f00",
      showPoc: true,
      showValueArea: true,
      edges: { startTime: 100 as Time, endTime: 300 as Time },
    };

    it("reports a ghost on every move but exactly one commit on release (AC #3)", () => {
      const onProfileEdgeDrag = vi.fn();
      const onProfileEdgeCommit = vi.fn();
      const { container } = render(chartElement({ volumeProfiles: [spec], onProfileEdgeDrag, onProfileEdgeCommit }));

      fireEvent.mouseDown(container.firstElementChild!, { clientX: 102, clientY: 15, button: 0 });
      fireEvent.mouseMove(window, { buttons: 1, clientX: 130, clientY: 15 });
      fireEvent.mouseMove(window, { buttons: 1, clientX: 150, clientY: 15 });
      expect(onProfileEdgeCommit).not.toHaveBeenCalled();
      fireEvent.mouseUp(window);

      expect(onProfileEdgeDrag.mock.calls).toEqual([
        ["frvp-1", "start", 130],
        ["frvp-1", "start", 150],
      ]);
      expect(onProfileEdgeCommit).toHaveBeenCalledTimes(1);
      expect(onProfileEdgeCommit).toHaveBeenCalledWith("frvp-1", "start", 150);
    });

    it("grabs the end edge, and ignores presses away from an edge or outside the profile's height", () => {
      const onProfileEdgeDrag = vi.fn();
      const onProfileEdgeCommit = vi.fn();
      const { container } = render(chartElement({ volumeProfiles: [spec], onProfileEdgeDrag, onProfileEdgeCommit }));
      const target = container.firstElementChild!;

      fireEvent.mouseDown(target, { clientX: 200, clientY: 15, button: 0 }); // between edges
      fireEvent.mouseMove(window, { buttons: 1, clientX: 210, clientY: 15 });
      fireEvent.mouseUp(window);
      fireEvent.mouseDown(target, { clientX: 100, clientY: 500, button: 0 }); // far below
      fireEvent.mouseMove(window, { buttons: 1, clientX: 110, clientY: 500 });
      fireEvent.mouseUp(window);
      expect(onProfileEdgeDrag).not.toHaveBeenCalled();

      fireEvent.mouseDown(target, { clientX: 298, clientY: 12, button: 0 });
      fireEvent.mouseMove(window, { buttons: 1, clientX: 340, clientY: 12 });
      fireEvent.mouseUp(window);
      expect(onProfileEdgeCommit).toHaveBeenCalledWith("frvp-1", "end", 340);
    });

    it("grabs the nearer edge of a very narrow range, and ignores edges when not editable", () => {
      const narrow: VolumeProfileSpec = { ...spec, edges: { startTime: 100 as Time, endTime: 104 as Time } };
      const onProfileEdgeCommit = vi.fn();
      const { container, rerender } = render(chartElement({ volumeProfiles: [narrow], onProfileEdgeCommit }));
      const target = container.firstElementChild!;

      fireEvent.mouseDown(target, { clientX: 103, clientY: 15, button: 0 }); // nearer the end edge
      fireEvent.mouseMove(window, { buttons: 1, clientX: 150, clientY: 15 });
      fireEvent.mouseUp(window);
      expect(onProfileEdgeCommit).toHaveBeenCalledWith("frvp-1", "end", 150);

      onProfileEdgeCommit.mockClear();
      rerender(chartElement({ volumeProfiles: [narrow], onProfileEdgeCommit, profileEdgesEditable: false }));
      fireEvent.mouseDown(target, { clientX: 103, clientY: 15, button: 0 });
      fireEvent.mouseMove(window, { buttons: 1, clientX: 150, clientY: 15 });
      fireEvent.mouseUp(window);
      expect(onProfileEdgeCommit).not.toHaveBeenCalled();
    });

    it("does not lose an in-flight drag when the parent re-renders with fresh callbacks", () => {
      const first = vi.fn();
      const { container, rerender } = render(chartElement({ volumeProfiles: [spec], onProfileEdgeCommit: vi.fn() }));
      fireEvent.mouseDown(container.firstElementChild!, { clientX: 102, clientY: 15, button: 0 });
      fireEvent.mouseMove(window, { buttons: 1, clientX: 150, clientY: 15 });

      rerender(chartElement({ volumeProfiles: [{ ...spec }], onProfileEdgeCommit: first }));
      fireEvent.mouseMove(window, { buttons: 1, clientX: 160, clientY: 15 });
      fireEvent.mouseUp(window);

      expect(first).toHaveBeenCalledWith("frvp-1", "start", 160);
    });
  });
});

describe("view commands and crosshair toggle (Story 18.10)", () => {
  it("runs fit and jump-to-latest once per new command, never on mount or an unrelated re-render", () => {
    const { rerender } = render(chartElement({}));
    expect(fitContentMock).not.toHaveBeenCalled();
    expect(scrollToRealTimeMock).not.toHaveBeenCalled();

    const fit = { kind: "fit" as const, seq: 1 };
    rerender(chartElement({ viewCommand: fit }));
    rerender(chartElement({ viewCommand: fit }));
    expect(fitContentMock).toHaveBeenCalledTimes(1);

    rerender(chartElement({ viewCommand: { kind: "fit", seq: 2 } })); // a repeated click is a new command
    expect(fitContentMock).toHaveBeenCalledTimes(2);

    rerender(chartElement({ viewCommand: { kind: "latest", seq: 3 } }));
    expect(scrollToRealTimeMock).toHaveBeenCalledTimes(1);
    expect(setVisibleLogicalRangeMock).not.toHaveBeenCalled();
  });

  it("hides/shows the crosshair lines only on a real change, leaving the crosshair mode alone", () => {
    const { rerender } = render(chartElement({}));
    const crosshairCalls = () => applyOptionsMock.mock.calls.filter((c) => c[0]?.crosshair).map((c) => c[0].crosshair);
    expect(crosshairCalls()).toHaveLength(0);

    rerender(chartElement({ crosshairVisible: false }));
    expect(crosshairCalls()).toEqual([
      { vertLine: { visible: false, labelVisible: false }, horzLine: { visible: false, labelVisible: false } },
    ]);

    rerender(chartElement({ crosshairVisible: true }));
    expect(crosshairCalls()).toHaveLength(2);
    expect(crosshairCalls()[1].vertLine.visible).toBe(true);
    expect(crosshairCalls().every((c) => !("mode" in c))).toBe(true);
  });
});

describe("gap painting (Story 32.1)", () => {
  const bar = (n: number) => ({ time: n as Time, open: 1, high: 2, low: 1, close: 1 });
  // One real bar, a 3-bar hole, one real bar: slots at 120, 180, 240.
  const holed = [bar(60), { time: 120 as Time }, { time: 180 as Time }, { time: 240 as Time }, bar(300)];
  const gapsAttached = () => gapAttachMock.mock.calls.map(([primitive, host]) => ({ primitive: primitive as GapPrimitive, host }));
  const candleSeries = () => addSeriesMock.mock.results[0].value;

  it("attaches one labelled gap painter to the candle series and pushes the price series' runs to it", () => {
    const setRuns = vi.spyOn(GapPrimitive.prototype, "setRuns");
    const { rerender } = render(<LightweightChart data={[bar(60), bar(120)]} onChartApi={() => {}} />);

    expect(gapsAttached()).toHaveLength(1);
    expect(gapsAttached()[0].host).toBe(candleSeries());
    expect(setRuns).toHaveBeenLastCalledWith([]);

    rerender(<LightweightChart data={holed as never} onChartApi={() => {}} />);

    expect(gapsAttached()).toHaveLength(1); // same painter, fresh runs
    expect(setRuns).toHaveBeenLastCalledWith([{ times: [120, 180, 240], durationSeconds: 180, compressed: false }]);
    setRuns.mockRestore();
  });

  it("closes a trailing run at the forming live bar, so a capped hole reads its real length", () => {
    const setRuns = vi.spyOn(GapPrimitive.prototype, "setRuns");
    const slots = Array.from({ length: 720 }, (_, k) => ({ time: (60 + (k + 1) * 60) as Time }));
    const liveAt = 60 + 3 * 86_400;
    render(
      <LightweightChart
        data={[bar(60), ...slots] as never}
        liveBar={{ time: liveAt as Time, open: 1, high: 2, low: 1, close: 1, volume: 1 } as never}
        onChartApi={() => {}}
      />,
    );

    const [run] = setRuns.mock.lastCall![0];
    expect(run).toMatchObject({ durationSeconds: 3 * 86_400 - 60, compressed: true });
    setRuns.mockRestore();
  });

  it("gives every non-overlay pane exactly one painter, on its first series, with the price runs; overlays none", () => {
    const setRuns = vi.spyOn(GapPrimitive.prototype, "setRuns");
    const indicatorData = [{ time: 60 as Time }, { time: 120 as Time, value: 3 }]; // warm-up whitespace, not a gap
    render(
      <LightweightChart
        data={holed as never}
        onChartApi={() => {}}
        panes={[
          makePaneSpec("volume", { kind: "Histogram" }),
          makePaneSpec("macd.line", { group: "macd", data: indicatorData }),
          makePaneSpec("macd.signal", { group: "macd", data: indicatorData }),
          makePaneSpec("sma", { placement: "overlay", data: indicatorData }),
        ]}
      />,
    );

    const hosts = gapsAttached().map((g) => g.host);
    const [candles, volume, macdLine] = addSeriesMock.mock.results.map((r) => r.value);
    expect(hosts).toEqual([volume, macdLine, candles]);
    for (const { primitive } of gapsAttached()) {
      expect(setRuns.mock.calls.filter((_, i) => setRuns.mock.contexts[i] === primitive).at(-1)).toEqual([
        [{ times: [120, 180, 240], durationSeconds: 180, compressed: false }],
      ]);
    }
    setRuns.mockRestore();
  });

  it("moves a pane's painter to a sibling when its host series goes, and detaches it with the pane", () => {
    const line = makePaneSpec("macd.line", { group: "macd" });
    const signal = makePaneSpec("macd.signal", { group: "macd" });
    const { rerender } = render(<LightweightChart data={holed as never} onChartApi={() => {}} panes={[line, signal]} />);
    const [, lineSeries, signalSeries] = addSeriesMock.mock.results.map((r) => r.value);
    const first = gapsAttached().find((g) => g.host === lineSeries)!.primitive;

    rerender(<LightweightChart data={holed as never} onChartApi={() => {}} panes={[signal]} />);
    expect(gapDetachMock).toHaveBeenCalledWith(first, lineSeries);
    expect(gapsAttached().filter((g) => g.host === signalSeries)).toHaveLength(1);

    const second = gapsAttached().find((g) => g.host === signalSeries)!.primitive;
    rerender(<LightweightChart data={holed as never} onChartApi={() => {}} panes={[]} />);
    expect(gapDetachMock).toHaveBeenCalledWith(second, signalSeries);
    expect(removePaneMock).toHaveBeenCalledTimes(1);
  });

  it("moves the labelled painter to the first Lines series on a mode flip, detaching it from the candles", () => {
    const lines = { bid: [], ask: [], mid: [], micro: [], price: [] };
    const { rerender } = render(<LightweightChart data={holed as never} linesData={lines} onChartApi={() => {}} />);
    const [{ primitive: candlePainter }] = gapsAttached();

    rerender(<LightweightChart mode="lines" data={holed as never} linesData={lines} onChartApi={() => {}} />);

    expect(gapDetachMock).toHaveBeenCalledWith(candlePainter, candleSeries());
    const bidSeries = addSeriesMock.mock.results[1].value; // LINE_SERIES_IDS order: bid first
    expect(gapsAttached().at(-1)!.host).toBe(bidSeries);
  });

  it("reads Lines-mode runs from the Lines series, one slot per missing second", () => {
    const setRuns = vi.spyOn(GapPrimitive.prototype, "setRuns");
    const v = (t: number) => ({ time: t as Time, value: 1 });
    const bid = [v(0), { time: 1 as Time }, { time: 2 as Time }, { time: 3 as Time }, { time: 4 as Time }, v(5)];
    const lines = { bid, ask: bid, mid: bid, micro: bid, price: bid };

    render(<LightweightChart mode="lines" data={[]} linesData={lines} onChartApi={() => {}} />);

    expect(setRuns).toHaveBeenLastCalledWith([{ times: [1, 2, 3, 4], durationSeconds: 4, compressed: false }]);
    setRuns.mockRestore();
  });

  it("shows 'no data · <duration>' in the legend when the crosshair is over a gap slot", () => {
    const paneEl = document.createElement("div");
    const base = createChartMock.getMockImplementation()!;
    createChartMock.mockImplementation((...args: unknown[]) => ({
      ...base(...args),
      panes: () => [{ ...pricePaneMock, getHTMLElement: () => paneEl }],
    }));
    render(
      <LightweightChart
        data={holed as never}
        onChartApi={() => {}}
        panes={[makePaneSpec("sma", { placement: "overlay", groupLabel: "SMA (5)", data: [{ time: 60 as Time, value: 1 }] })]}
      />,
    );
    const legendHandler = subscribeCrosshairMoveMock.mock.calls.at(-1)![0];

    act(() => legendHandler({ time: 180, seriesData: new Map() }));

    expect(paneEl.querySelector(".chart-legend-row")?.textContent).toBe("SMA (5)no data · 3m");
  });
});

// Story 32.3: what the legend's eye and the settings modal's Style do to the chart.
describe("LightweightChart indicator visibility and style (Story 32.3)", () => {
  const NO_DATA: never[] = []; // one reference: an inline [] would re-run the candles' own setData each render
  const element = (panes: IndicatorPaneSpec[]) => <LightweightChart data={NO_DATA} onChartApi={() => {}} panes={panes} />;
  // The price pane's DOM element, attached so the legend rendered into it can be queried.
  const attachedPricePane = (): HTMLElement => {
    const paneEl = document.createElement("div");
    document.body.appendChild(paneEl);
    const base = createChartMock.getMockImplementation()!;
    createChartMock.mockImplementation((...args: unknown[]) => ({
      ...base(...args),
      panes: () => [{ ...pricePaneMock, getHTMLElement: () => paneEl }],
    }));
    return paneEl;
  };
  const overlay = (over: Partial<IndicatorPaneSpec> = {}) =>
    makePaneSpec("SMA.value", { placement: "overlay", group: "SMA", ...over });
  const rsi = (over: Partial<IndicatorPaneSpec> = {}) => makePaneSpec("RSI.value", { group: "RSI", ...over });
  const volume = () => makePaneSpec("volume", { kind: "Histogram", group: "volume" });
  const total = (extras: number[]) => PRICE_PANE_PX + extras.reduce((a, b) => a + b, 0) + extras.length + TIME_AXIS_PX;
  const seriesOf = (n: number) => addSeriesMock.mock.results[n].value as ReturnType<typeof makeSeriesMock>;

  it("hides an overlay in place: every series goes visible: false, no pane is touched, the data stays", () => {
    const data = [{ time: 1 as never, value: 5 }];
    const { rerender } = render(element([overlay({ data })]));
    const setDataCalls = setDataMock.mock.calls.length;

    rerender(element([overlay({ data, hidden: true })]));

    expect(seriesOf(1).applyOptions).toHaveBeenCalledWith({ visible: false });
    expect(removePaneMock).not.toHaveBeenCalled();
    expect(addPaneMock).not.toHaveBeenCalled();
    expect(removeSeriesMock).not.toHaveBeenCalled();
    expect(setDataMock.mock.calls.length).toBe(setDataCalls); // no refetch, no repaint of data

    rerender(element([overlay({ data })]));
    expect(seriesOf(1).applyOptions).toHaveBeenLastCalledWith({ visible: true });
  });

  it("creates an overlay that is already hidden with visible: false", () => {
    render(element([overlay({ hidden: true })]));

    expect(addSeriesMock.mock.calls[1][1]).toMatchObject({ visible: false });
  });

  it("collapses a hidden pane indicator: the pane is removed and the page shrinks by its height", () => {
    const { rerender } = render(element([volume(), rsi()]));
    pricePaneMock.getHeight.mockReturnValue(PRICE_PANE_PX);
    addedPane(0).getHeight.mockReturnValue(VOLUME_PANE_PX);
    addedPane(1).getHeight.mockReturnValue(INDICATOR_PANE_PX);
    expect(lastChartHeight()).toBe(total([VOLUME_PANE_PX, INDICATOR_PANE_PX]));

    rerender(element([volume(), rsi({ hidden: true })]));

    expect(removePaneMock).toHaveBeenCalledTimes(1);
    expect(removePaneMock).toHaveBeenCalledWith(2);
    expect(lastChartHeight()).toBe(total([VOLUME_PANE_PX])); // down by 160 + its separator
    expect(total([VOLUME_PANE_PX, INDICATOR_PANE_PX]) - total([VOLUME_PANE_PX])).toBe(INDICATOR_PANE_PX + 1);
  });

  it("re-adds a shown pane at its former index and height, with the same data and no extra fetch", () => {
    const data = [{ time: 1 as never, value: 5 }];
    const { rerender } = render(element([volume(), rsi({ data }), makePaneSpec("MACD.value", { group: "MACD" })]));
    pricePaneMock.getHeight.mockReturnValue(PRICE_PANE_PX);
    addedPane(0).getHeight.mockReturnValue(VOLUME_PANE_PX);
    addedPane(1).getHeight.mockReturnValue(240); // the operator dragged RSI to 240
    addedPane(2).getHeight.mockReturnValue(INDICATOR_PANE_PX);
    rerender(element([volume(), rsi({ data, hidden: true }), makePaneSpec("MACD.value", { group: "MACD" })]));
    // Re-snapshot as the library would after the collapse: MACD is now the pane at index 2.
    addedPane(2).getHeight.mockReturnValue(INDICATOR_PANE_PX);
    addPaneMock.mockClear();
    setDataMock.mockClear();

    rerender(element([volume(), rsi({ data }), makePaneSpec("MACD.value", { group: "MACD" })]));

    expect(addPaneMock).toHaveBeenCalledTimes(1);
    const restored = addedPane(0);
    expect(restored.moveTo).toHaveBeenCalledWith(2); // volume 1, RSI 2, MACD after it
    expect(restored.setStretchFactor).toHaveBeenLastCalledWith(240);
    expect(setDataMock).toHaveBeenCalledWith(data); // the data in state is painted again, none refetched
  });

  it("keeps a hidden pane indicator's data in the legend row (series null, hidden) on the price pane", () => {
    const paneEl = attachedPricePane();
    render(element([rsi({ hidden: true, groupLabel: "RSI (14)", data: [{ time: 1 as never, value: 55 }] })]));

    // No pane or series exists for it...
    expect(addPaneMock).not.toHaveBeenCalled();
    expect(addSeriesMock).toHaveBeenCalledTimes(1); // only the candles
    // ...but its row is on the price pane's legend.
    expect(paneEl.querySelector(".chart-legend-row--hidden")?.textContent).toContain("RSI (14)");
  });

  it("applies a style-only change with applyOptions: no new series, no pane change, no setData", () => {
    const data = [{ time: 1 as never, value: 5 }]; // one reference, as the page keeps it
    const { rerender } = render(element([overlay({ data })]));
    setDataMock.mockClear();

    rerender(element([overlay({ data, lineWidth: 2, lineStyle: "dashed", color: "#abcdef" })]));

    expect(seriesOf(1).applyOptions).toHaveBeenCalledWith({ color: "#abcdef", lineWidth: 2, lineStyle: 2 });
    expect(addSeriesMock).toHaveBeenCalledTimes(2);
    expect(removeSeriesMock).not.toHaveBeenCalled();
    expect(setDataMock).not.toHaveBeenCalled();

    rerender(element([overlay({ data, lineWidth: 2, lineStyle: "dotted", color: "#abcdef" })]));
    expect(seriesOf(1).applyOptions).toHaveBeenLastCalledWith({ lineStyle: 1 });

    // A width and style the entry no longer stores (cleared, or a failed save rolled back) go back
    // to the library default rather than sticking.
    rerender(element([overlay({ data, color: "#abcdef" })]));
    expect(seriesOf(1).applyOptions).toHaveBeenLastCalledWith({ lineWidth: 3, lineStyle: 0 });
  });

  it("creates a series with its stored width and style", () => {
    render(element([overlay({ lineWidth: 2, lineStyle: "dotted" })]));

    expect(addSeriesMock.mock.calls[1][1]).toMatchObject({ lineWidth: 2, lineStyle: 1 });
  });

  it("paints a histogram's bars by sign in its up and down colours, without touching the data in state", () => {
    const data = [{ time: 1 as never, value: 5 }, { time: 2 as never, value: -3 }, { time: 3 as never }];
    render(element([makePaneSpec("H.value", { kind: "Histogram", data, upColor: "#00ff00", downColor: "#ff0000" })]));

    expect(setDataMock).toHaveBeenLastCalledWith([
      { time: 1, value: 5, color: "#00ff00" },
      { time: 2, value: -3, color: "#ff0000" },
      { time: 3 },
    ]);
    expect(data[0]).toEqual({ time: 1, value: 5 });
  });

  it("repaints the histogram when only its up/down colours change", () => {
    const data = [{ time: 1 as never, value: 5 }];
    const spec = (up: string) => makePaneSpec("H.value", { kind: "Histogram", data, upColor: up, downColor: "#ff0000" });
    const { rerender } = render(element([spec("#00ff00")]));

    rerender(element([spec("#0000ff")]));

    expect(setDataMock).toHaveBeenLastCalledWith([{ time: 1, value: 5, color: "#0000ff" }]);
  });

  it("clamps a stored line width to 1..4 and draws an unknown line style as the default solid", () => {
    render(element([overlay({ lineWidth: 9, lineStyle: "wavy" as never })]));

    const options = addSeriesMock.mock.calls[1][1] as Record<string, unknown>;
    expect(options.lineWidth).toBe(4);
    expect(options.lineStyle).toBe(0);
  });

  it("gives a histogram bar with a null or NaN value no colour", () => {
    const data = [{ time: 1 as never, value: null as never }, { time: 2 as never, value: Number.NaN }];
    render(element([makePaneSpec("H.value", { kind: "Histogram", data, upColor: "#00ff00", downColor: "#ff0000" })]));

    const painted = setDataMock.mock.calls.at(-1)![0] as Record<string, unknown>[];
    expect(painted.every((d) => !("color" in d))).toBe(true);
  });

  it("forgets a collapsed height once its indicator is removed, so a re-add gets the default", () => {
    const { rerender } = render(element([volume(), rsi()]));
    pricePaneMock.getHeight.mockReturnValue(PRICE_PANE_PX);
    addedPane(0).getHeight.mockReturnValue(VOLUME_PANE_PX);
    addedPane(1).getHeight.mockReturnValue(240);
    rerender(element([volume(), rsi({ hidden: true })]));
    rerender(element([volume()])); // removed outright while collapsed
    addPaneMock.mockClear();

    rerender(element([volume(), rsi()]));

    expect(addedPane(0).setStretchFactor).toHaveBeenLastCalledWith(INDICATOR_PANE_PX);
  });

  it("reports a legend button press as (action, group)", () => {
    const onLegendAction = vi.fn();
    const paneEl = attachedPricePane();
    render(
      <LightweightChart
        data={[]}
        onChartApi={() => {}}
        panes={[overlay({ groupLabel: "SMA (20)" })]}
        onLegendAction={onLegendAction}
      />,
    );

    (paneEl.querySelector('button[aria-label="Remove SMA (20)"]') as HTMLElement).click();
    (paneEl.querySelector('button[aria-label="Settings for SMA (20)"]') as HTMLElement).click();

    expect(onLegendAction.mock.calls).toEqual([["remove", "SMA"], ["settings", "SMA"]]);
  });
});

// Story 32.5: Fibonacci and position drawings in the one registry, and the one grab for every handle.
describe("Fibonacci and position drawings (Story 32.5)", () => {
  const bar = (n: number) => ({ time: n as Time, open: 1, high: 2, low: 1, close: 1 });
  const bars = [bar(100), bar(200), bar(300), bar(400)];
  const fibSpec = (id = "fib-1", overrides: Partial<FibDrawing> = {}): FibDrawing => ({
    kind: "fib",
    id,
    anchors: [
      { time: 100, price: 100 },
      { time: 300, price: 90 },
    ],
    levels: defaultFibLevels(() => "#123456"),
    extend_right: true,
    label_side: "left",
    line_width: 1,
    ...overrides,
  });
  const positionSpec = (id = "position-1"): PositionDrawing => newPosition(id, "long", 200, 100, 2);

  /** Give an attached primitive the geometry the real library would (the mock never calls attached()). */
  function attachGeometry(primitive: FibPrimitive | PositionPrimitive | TrendlinePrimitive): void {
    primitive.attached({
      chart: { timeScale: () => ({ timeToCoordinate: (t: number) => t, logicalToCoordinate: (i: number) => i * 10 }) },
      series: { priceToCoordinate: (p: number) => p },
      requestUpdate: vi.fn(),
    } as never);
    primitive.updateAllViews();
  }

  it("attaches one primitive of the right class per drawing, and updates a changed one in place", () => {
    const fib = fibSpec();
    const { rerender } = render(chartElement({ drawings: [fib, positionSpec()], precision: { price: 2, size: 3 }, data: bars }));

    expect(attachPrimitiveMock.mock.calls.map((c) => c[0].constructor)).toEqual([FibPrimitive, PositionPrimitive]);
    const primitive = attachPrimitiveMock.mock.calls[0][0] as FibPrimitive;
    const updateSpy = vi.spyOn(primitive, "update");

    const edited = { ...fib, line_width: 3 };
    rerender(chartElement({ drawings: [edited, positionSpec()], precision: { price: 2, size: 3 }, data: bars }));

    expect(updateSpy).toHaveBeenCalledWith(edited, 2);
    expect(attachPrimitiveMock).toHaveBeenCalledTimes(2);
  });

  it("detaches a removed drawing, and re-attaches all of them on the new host after a mode flip", () => {
    const { rerender } = render(chartElement({ drawings: [fibSpec("fib-1"), fibSpec("fib-2")], data: bars }));
    const first = attachPrimitiveMock.mock.calls[0][0];
    rerender(chartElement({ drawings: [fibSpec("fib-2")], data: bars }));
    expect(detachPrimitiveMock).toHaveBeenCalledWith(first);
    attachPrimitiveMock.mockClear();
    rerender(chartElement({ drawings: [fibSpec("fib-2")], data: bars, mode: "lines" }));
    expect(attachPrimitiveMock).toHaveBeenCalledTimes(1);
  });

  it("shows handles only while drawings are editable (the Cursor tool)", () => {
    const { rerender } = render(chartElement({ drawings: [fibSpec()], data: bars, drawEditable: true }));
    const primitive = attachPrimitiveMock.mock.calls[0][0] as FibPrimitive;
    const spy = vi.spyOn(primitive, "setHandlesVisible");
    rerender(chartElement({ drawings: [fibSpec()], data: bars, drawEditable: false }));
    expect(spy).toHaveBeenLastCalledWith(false);
  });

  it("grabs an anchor handle in Cursor mode and reports the pointer's bar time and price until mouseup", () => {
    const onDrawingDrag = vi.fn();
    const spec = fibSpec();
    const { container } = render(chartElement({ drawings: [spec], data: bars, drawEditable: true, onDrawingDrag }));
    attachGeometry(attachPrimitiveMock.mock.calls[0][0]);
    const crosshair = subscribeCrosshairMoveMock.mock.calls[0][0];

    crosshair({ point: { x: 102, y: 101 }, paneIndex: 0 }); // on anchor A at (100, 100)
    fireEvent.mouseDown(container.firstElementChild!);
    crosshair({ point: { x: 260, y: 95 }, paneIndex: 0, logical: 1.4 }); // pointer between bars 2 and 3

    expect(onDrawingDrag).toHaveBeenCalledTimes(1);
    const [id, handle, point] = onDrawingDrag.mock.calls[0] as [string, string, DragPoint];
    expect([id, handle, point.price, point.time]).toEqual(["fib-1", "a", 95, 200]);
    expect(point.barsSince(100)).toBe(1); // logical 1.4 rounds to bar 1; bar 100 is index 0

    fireEvent.mouseUp(window);
    crosshair({ point: { x: 270, y: 96 }, paneIndex: 0, logical: 1.6 });
    expect(onDrawingDrag).toHaveBeenCalledTimes(1);
  });

  it("grabs a position's target handle with the same mechanism", () => {
    const onDrawingDrag = vi.fn();
    const { container } = render(chartElement({ drawings: [positionSpec()], data: bars, drawEditable: true, onDrawingDrag }));
    attachGeometry(attachPrimitiveMock.mock.calls[0][0]);
    const crosshair = subscribeCrosshairMoveMock.mock.calls[0][0];

    crosshair({ point: { x: 408, y: 102 }, paneIndex: 0 }); // the target handle: right edge x 410, price 102
    fireEvent.mouseDown(container.firstElementChild!);
    crosshair({ point: { x: 408, y: 104 }, paneIndex: 0, logical: 40 });

    expect(onDrawingDrag.mock.calls[0].slice(0, 2)).toEqual(["position-1", "target"]);
  });

  it("grabs a trendline's anchor too", () => {
    const onDrawingDrag = vi.fn();
    const { container } = render(
      chartElement({ drawings: [makeTrendlineSpec("trendline-1")], data: bars, drawEditable: true, onDrawingDrag }),
    );
    attachGeometry(attachPrimitiveMock.mock.calls[0][0]);
    const crosshair = subscribeCrosshairMoveMock.mock.calls[0][0];

    crosshair({ point: { x: 200, y: 21 }, paneIndex: 0 }); // anchor B at (200, 20)
    fireEvent.mouseDown(container.firstElementChild!);
    crosshair({ point: { x: 250, y: 30 }, paneIndex: 0, logical: 1 });

    expect(onDrawingDrag.mock.calls[0].slice(0, 2)).toEqual(["trendline-1", "b"]);
  });

  it("does not grab a handle outside Cursor mode (a placement tool owns the mouse), and never in another pane", () => {
    const onDrawingDrag = vi.fn();
    const { container, rerender } = render(chartElement({ drawings: [fibSpec()], data: bars, drawEditable: false, onDrawingDrag }));
    attachGeometry(attachPrimitiveMock.mock.calls[0][0]);
    const crosshair = subscribeCrosshairMoveMock.mock.calls[0][0];
    crosshair({ point: { x: 102, y: 101 }, paneIndex: 0 });
    fireEvent.mouseDown(container.firstElementChild!);
    crosshair({ point: { x: 150, y: 95 }, paneIndex: 0, logical: 1 });
    expect(onDrawingDrag).not.toHaveBeenCalled();

    rerender(chartElement({ drawings: [fibSpec()], data: bars, drawEditable: true, onDrawingDrag }));
    const again = subscribeCrosshairMoveMock.mock.calls.at(-1)![0];
    again({ point: { x: 102, y: 101 }, paneIndex: 1 }); // the same pixels, but in an indicator pane
    fireEvent.mouseDown(container.firstElementChild!);
    again({ point: { x: 150, y: 95 }, paneIndex: 1, logical: 1 });
    expect(onDrawingDrag).not.toHaveBeenCalled();
  });

  it("swallows the chart click that ends a handle drag, so it places nothing", () => {
    const onDrawingDrag = vi.fn();
    const onPointClick = vi.fn();
    const { container } = render(chartElement({ drawings: [fibSpec()], data: bars, drawEditable: true, onDrawingDrag, onPointClick }));
    attachGeometry(attachPrimitiveMock.mock.calls[0][0]);
    const crosshair = subscribeCrosshairMoveMock.mock.calls[0][0];
    const click = subscribeClickMock.mock.calls.at(-1)![0];

    crosshair({ point: { x: 102, y: 101 }, paneIndex: 0 });
    fireEvent.mouseDown(container.firstElementChild!);
    crosshair({ point: { x: 150, y: 95 }, paneIndex: 0, logical: 1 });
    fireEvent.mouseUp(window);
    click({ point: { x: 150, y: 95 } });
    expect(onPointClick).not.toHaveBeenCalled(); // the click that ends a drag places nothing
  });

  it("offers Settings... in the menu for a Fibonacci and a position, not for a trendline or a horizontal line", () => {
    const onDrawingSettings = vi.fn();
    const hit = (x: number, y: number) => {
      const click = subscribeClickMock.mock.calls.at(-1)![0];
      act(() => click({ point: { x, y }, sourceEvent: { clientX: 5, clientY: 5 } }));
    };
    render(
      chartElement({
        drawings: [fibSpec(), makeTrendlineSpec("trendline-1", { anchors: [{ time: 100, price: 500 }, { time: 300, price: 520 }] })],
        priceLines: [makePriceLineSpec("hline-1", { price: 700 })],
        data: bars,
        drawEditable: true,
        onDrawingSettings,
        onDrawingColor: () => {},
      }),
    );
    for (const call of attachPrimitiveMock.mock.calls) attachGeometry(call[0]);

    hit(200, 95); // the fib's 0.5 level (y 95) in the gap between its anchors
    fireEvent.click(screen.getByText("Settings…"));
    expect(onDrawingSettings).toHaveBeenCalledWith("fib-1");

    hit(200, 510); // the trendline
    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(screen.queryByText("Settings…")).toBeNull();

    hit(500, 700); // the horizontal line
    expect(screen.queryByText("Settings…")).toBeNull();
  });

  it("snaps a drawing placed after the newest loaded bar back to that bar", () => {
    render(chartElement({ drawings: [{ ...positionSpec(), time: 400 }], data: bars.slice(0, 3) }));
    const primitive = attachPrimitiveMock.mock.calls[0][0] as PositionPrimitive;
    attachGeometry(primitive);
    expect(primitive.screen()?.left).toBe(20); // index 2, the newest loaded bar
  });

  it("includes the forming live bar among the bars drawings snap to", () => {
    render(
      <LightweightChart
        data={bars.slice(0, 3)}
        onChartApi={() => {}}
        drawings={[{ ...positionSpec(), time: 400 }]}
        liveBar={{ time: 400 as Time, open: 1, high: 2, low: 1, close: 1, volume: 1 } as never}
      />,
    );
    const primitive = attachPrimitiveMock.mock.calls[0][0] as PositionPrimitive;
    attachGeometry(primitive);
    expect(primitive.screen()?.left).toBe(30); // index 3, the forming bar
  });
});

describe("Fibonacci placement drag (Story 32.5)", () => {
  beforeEach(() => {
    coordinateToTimeMock.mockImplementation((x: number) => x);
  });

  it("previews the retracement while dragging and reports exactly one (A, B) on release", () => {
    const onFibPlace = vi.fn();
    const { container } = render(chartElement({ fibActive: true, onFibPlace, precision: { price: 2, size: 3 } }));
    const target = container.firstElementChild!;

    fireEvent.mouseDown(target, { clientX: 10, clientY: 100, button: 0 });
    expect(attachPrimitiveMock).not.toHaveBeenCalled();
    fireEvent.mouseMove(window, { buttons: 1, clientX: 50, clientY: 90 });
    fireEvent.mouseMove(window, { buttons: 1, clientX: 60, clientY: 80 });
    expect(attachPrimitiveMock).toHaveBeenCalledTimes(1);
    expect(attachPrimitiveMock.mock.calls[0][0]).toBeInstanceOf(FibPrimitive);
    expect(onFibPlace).not.toHaveBeenCalled();
    fireEvent.mouseUp(window);

    expect(onFibPlace).toHaveBeenCalledTimes(1);
    expect(onFibPlace).toHaveBeenCalledWith({ time: 10, price: 100 }, { time: 60, price: 80 });
    expect(detachPrimitiveMock).toHaveBeenCalledTimes(1);
  });

  it("reports nothing for a click without a drag, and cancels with no residue on Esc (fibActive off)", () => {
    const onFibPlace = vi.fn();
    const { container, rerender } = render(chartElement({ fibActive: true, onFibPlace }));
    const target = container.firstElementChild!;

    fireEvent.mouseDown(target, { clientX: 10, clientY: 100, button: 0 });
    fireEvent.mouseUp(window);
    expect(onFibPlace).not.toHaveBeenCalled();

    fireEvent.mouseDown(target, { clientX: 10, clientY: 100, button: 0 });
    fireEvent.mouseMove(window, { buttons: 1, clientX: 60, clientY: 130 });
    rerender(chartElement({ fibActive: false, onFibPlace }));
    expect(detachPrimitiveMock).toHaveBeenCalledTimes(1);
    expect(onFibPlace).not.toHaveBeenCalled();
  });

  it("does nothing while the Fibonacci tool is not armed", () => {
    const { container } = render(chartElement({}));
    fireEvent.mouseDown(container.firstElementChild!, { clientX: 10, clientY: 100, button: 0 });
    fireEvent.mouseMove(window, { buttons: 1, clientX: 60, clientY: 130 });
    expect(attachPrimitiveMock).not.toHaveBeenCalled();
  });
});

describe("LightweightChart layout restore and reports (Story 32.6)", () => {
  const bars = (n: number) => Array.from({ length: n }, (_, i) => ({ time: (1000 + i) as never, open: 1, high: 2, low: 0.5, close: 1.5 }));
  const volumeSpec = () => makePaneSpec("volume", { kind: "Histogram" });
  const rsiSpec = () => makePaneSpec("RSI");
  const fireRange = (from: number, to: number) =>
    act(() => {
      (subscribeRangeMock.mock.calls[0][0] as (r: { from: number; to: number } | null) => void)({ from, to });
    });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("pins the saved heights (price included) instead of the defaults when the axis is not measured yet", () => {
    timeScaleHeightMock.mockReturnValue(0);
    render(chartElement({ panes: [volumeSpec(), rsiSpec()], initialPaneHeights: { price: 420, volume: 90, RSI: 220 } }));

    expect(pricePaneMock.setStretchFactor).toHaveBeenLastCalledWith(420);
    expect(addedPane(0).setStretchFactor).toHaveBeenLastCalledWith(90);
    expect(addedPane(1).setStretchFactor).toHaveBeenLastCalledWith(220);
  });

  it("uses a pane's own measured height over the saved one once the axis is known", () => {
    const { rerender } = render(chartElement({ panes: [volumeSpec()], initialPaneHeights: { volume: 90 } }));
    pricePaneMock.getHeight.mockReturnValue(400);
    addedPane(0).getHeight.mockReturnValue(200); // dragged

    rerender(chartElement({ panes: [volumeSpec(), rsiSpec()], initialPaneHeights: { volume: 90 } }));

    expect(addedPane(0).setStretchFactor).toHaveBeenLastCalledWith(200);
  });

  it("reports the heights after a press that moved a divider, and nothing for a press that moved none", () => {
    const onPaneHeights = vi.fn();
    const { container } = render(chartElement({ panes: [volumeSpec()], onPaneHeights }));
    const chartBox = container.firstElementChild as HTMLElement; // the element the chart is created in
    pricePaneMock.getHeight.mockReturnValue(500);
    addedPane(0).getHeight.mockReturnValue(120);

    fireEvent.pointerDown(chartBox);
    fireEvent.pointerUp(window);
    expect(onPaneHeights).not.toHaveBeenCalled();

    fireEvent.pointerDown(chartBox);
    pricePaneMock.getHeight.mockReturnValue(460);
    addedPane(0).getHeight.mockReturnValue(160); // the divider was dragged up
    fireEvent.pointerUp(window);
    expect(onPaneHeights).toHaveBeenCalledTimes(1);
    expect(onPaneHeights).toHaveBeenCalledWith({ price: 460, volume: 160 });
  });

  it("never reports a height change it made itself (a pane added with no press)", () => {
    const onPaneHeights = vi.fn();
    const { rerender } = render(chartElement({ panes: [volumeSpec()], onPaneHeights }));
    pricePaneMock.getHeight.mockReturnValue(500);
    addedPane(0).getHeight.mockReturnValue(120);

    rerender(chartElement({ panes: [volumeSpec(), rsiSpec()], onPaneHeights }));
    fireEvent.pointerUp(window);

    expect(onPaneHeights).not.toHaveBeenCalled();
  });

  it("shows the saved number of bars with the latest at the right, once, when the first candles arrive", () => {
    const { rerender } = render(chartElement({ data: [], initialVisibleBars: 80 }));
    expect(setVisibleLogicalRangeMock).not.toHaveBeenCalled(); // nothing to zoom on yet

    rerender(chartElement({ data: bars(300), initialVisibleBars: 80 }));
    expect(setVisibleLogicalRangeMock).toHaveBeenCalledTimes(1);
    expect(setVisibleLogicalRangeMock).toHaveBeenCalledWith({ from: 299 - 80 + 0.5, to: 299.5 });

    rerender(chartElement({ data: bars(301), initialVisibleBars: 80 }));
    expect(setVisibleLogicalRangeMock).toHaveBeenCalledTimes(1); // not again on later data
  });

  it("reports a zoom once per burst, after the quiet period, and never the restore itself", () => {
    vi.useFakeTimers();
    const onVisibleBars = vi.fn();
    render(chartElement({ data: bars(300), initialVisibleBars: 80, onVisibleBars }));

    fireRange(219.5, 299.5); // the library echoing our own restore: 80 bars
    act(() => void vi.advanceTimersByTime(VISIBLE_BARS_DEBOUNCE_MS * 2));
    expect(onVisibleBars).not.toHaveBeenCalled();

    for (const width of [90, 100, 110, 120, 130]) {
      fireRange(299.5 - width, 299.5);
      act(() => void vi.advanceTimersByTime(50));
    }
    expect(onVisibleBars).not.toHaveBeenCalled();
    act(() => void vi.advanceTimersByTime(VISIBLE_BARS_DEBOUNCE_MS));
    expect(onVisibleBars).toHaveBeenCalledTimes(1);
    expect(onVisibleBars).toHaveBeenCalledWith(130);
  });

  it("stays silent before the saved zoom was applied, so the library's own first fit cannot overwrite it", () => {
    vi.useFakeTimers();
    const onVisibleBars = vi.fn();
    render(chartElement({ data: [], initialVisibleBars: 80, onVisibleBars }));

    fireRange(0, 200);
    act(() => void vi.advanceTimersByTime(VISIBLE_BARS_DEBOUNCE_MS * 2));

    expect(onVisibleBars).not.toHaveBeenCalled();
  });

  it("reports a zoom still pending when the chart goes away (a timeframe change)", () => {
    vi.useFakeTimers();
    const onVisibleBars = vi.fn();
    const { unmount } = render(chartElement({ data: bars(300), initialVisibleBars: 80, onVisibleBars }));
    fireRange(179.5, 299.5);

    unmount();

    expect(onVisibleBars).toHaveBeenCalledWith(120);
  });

  it("reports a zoom still pending when the page is left (pagehide)", () => {
    vi.useFakeTimers();
    const onVisibleBars = vi.fn();
    render(chartElement({ data: bars(300), initialVisibleBars: 80, onVisibleBars }));
    fireRange(179.5, 299.5);

    act(() => {
      window.dispatchEvent(new Event("pagehide"));
    });

    expect(onVisibleBars).toHaveBeenCalledTimes(1);
    expect(onVisibleBars).toHaveBeenCalledWith(120);
    act(() => void vi.advanceTimersByTime(VISIBLE_BARS_DEBOUNCE_MS * 2));
    expect(onVisibleBars).toHaveBeenCalledTimes(1); // the debounce timer was cleared
  });

  it("does not report in Lines mode (its axis is not bars)", () => {
    vi.useFakeTimers();
    const onVisibleBars = vi.fn();
    render(chartElement({ mode: "lines", data: bars(300), initialVisibleBars: 80, onVisibleBars }));

    fireRange(0, 200);
    act(() => void vi.advanceTimersByTime(VISIBLE_BARS_DEBOUNCE_MS * 2));

    expect(onVisibleBars).not.toHaveBeenCalled();
    expect(setVisibleLogicalRangeMock).not.toHaveBeenCalled();
  });
});
