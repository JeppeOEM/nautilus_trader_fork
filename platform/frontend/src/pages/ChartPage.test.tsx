import { act, cleanup, fireEvent, render as rtlRender, screen, within } from "@testing-library/react";
import { CHART_TOKENS } from "../components/chart/chartTheme";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PriceLineSpec } from "../components/chart/LightweightChart";

// The page test isolates ChartPage's OWN tool state machine: the api client would hit
// real fetches under jsdom, and the data hooks' fetch/websocket machinery plus
// LightweightChart's chart internals are each covered by their own test files --
// here they are all shallow-mocked so the only real code under test is ChartPage.tsx.
const saveConfigMock = vi.hoisted(() => vi.fn().mockResolvedValue({ ok: true }));
vi.mock("../api/client", () => ({
  fetchCoinIndicatorConfig: vi.fn().mockResolvedValue([]),
  saveCoinIndicatorConfig: saveConfigMock,
  // IndicatorPicker (rendered by ChartPage) fetches the catalog on mount.
  fetchIndicatorCatalog: vi.fn().mockResolvedValue({
    SimpleMovingAverage: { params: {}, panel: "overlay", category: "native", source_selectable: true },
    RelativeStrengthIndex: { params: {}, panel: "oscillator", category: "native" },
    CancelPressure: { params: {}, panel: "histogram", category: "custom" },
    CandlePattern: {
      params: { pattern: "ENGULFING", trend_bars: 3 },
      panel: "histogram",
      category: "native",
      choices: { pattern: ["DOJI", "HAMMER", "ENGULFING"] },
    },
  }),
}));

const hooks = vi.hoisted(() => ({
  candlesBar: [] as number[],
  liveBar: [] as number[],
  pickerBar: [] as number[],
  openGapTo: [] as number[],
}));
// Stable references (a fresh array per render would churn useReplay's memo) that the
// replay tests swap in.
const mocks = vi.hoisted(() => ({
  candles: [] as unknown[],
  volume: [] as unknown[],
  venueMarket: null as { venue: string; market: string } | null,
  liveBar: null as unknown,
  session: { candles: [] as unknown[], volume: [] as unknown[], completeFrom: null as number | null },
  sessionArgs: { enabled: false, sinceSeconds: 0, barSeconds: 0 },
}));

vi.mock("../hooks/useCandles", () => ({
  BAR_SECONDS: 60,
  useCandles: (_iid: string, _chart: unknown, _enabled: boolean, bar: number) => {
    hooks.candlesBar.push(bar);
    return {
      candles: mocks.candles,
      volume: mocks.volume,
      venueMarket: mocks.venueMarket,
      openGapTo: (time: number) => hooks.openGapTo.push(time),
    };
  },
}));

vi.mock("../hooks/useSessionCandles", () => ({
  useSessionCandles: (_iid: string, enabled: boolean, sinceSeconds: number, barSeconds: number) => {
    mocks.sessionArgs = { enabled, sinceSeconds, barSeconds };
    return mocks.session;
  },
}));

vi.mock("../hooks/useSnapshotSeries", () => ({
  useSnapshotSeries: () => ({ bid: [], ask: [], mid: [], micro: [], price: [] }),
}));

vi.mock("../hooks/useLiveCandle", () => ({
  useLiveCandle: (_iid: string, bar: number) => {
    hooks.liveBar.push(bar);
    return mocks.liveBar;
  },
}));

// Module-level constant: ChartPage's adjust-state-during-render pattern bails out on
// identical pickerValues identity, which mirrors the real hook's contract of returning
// its state object (stable until a real data change) -- a fresh {} per render would
// loop the page into React's too-many-re-renders guard.
// The first render sees the real hook's initial empty state; later renders see `values`,
// so ChartPage's identity-change detection fires exactly like a real data arrival.
const picker = vi.hoisted(() => ({
  values: {} as Record<string, never[]>,
  calls: 0,
  // The page's `onErrors` callback, so a test can report a per-entry replay failure.
  onErrors: undefined as ((errors: Record<string, string>) => void) | undefined,
}));
vi.mock("../hooks/usePickerIndicatorValues", () => {
  const initial = {};
  return { usePickerIndicatorValues: (
    _i: string,
    _c: unknown,
    _e: unknown,
    bar: number,
    onErrors?: (errors: Record<string, string>) => void,
  ) => {
    hooks.pickerBar.push(bar);
    picker.onErrors = onErrors;
    return picker.calls++ === 0 ? initial : picker.values;
  } };
});

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return { ...actual, useParams: () => ({ iid: "BTC-USD-PERP.DYDX" }) };
});

// The subset of LightweightChart's props this page test asserts on. The stub records
// the latest props object ChartPage handed it -- every claim below is about what
// ChartPage FEEDS the chart component, never about LightweightChart's internals.
interface ChartStubProps {
  panes?: {
    id: string;
    kind: string;
    data: { time: number }[];
    placement?: string;
    group?: string;
    groupLabel?: string;
    outputLabel?: string;
    hidden?: boolean;
    color?: string;
    lineWidth?: number;
    lineStyle?: string;
    upColor?: string;
    downColor?: string;
    configurable?: boolean;
  }[];
  onLegendAction?: (action: "hide" | "settings" | "remove", group: string) => void;
  priceLines?: PriceLineSpec[];
  onPriceClick?: (price: number) => void;
  onPriceLineDrag?: (id: string, price: number) => void;
  drawings?: { id: string; kind: string; anchors: unknown[] }[];
  onPointClick?: (point: { time: number; price: number }) => void;
  measureActive?: boolean;
  onMeasureEnd?: () => void;
  data?: { time: number }[];
  liveBar?: unknown;
  markerTime?: number | null;
  volumeProfiles?: { id: string; profile: { totalVolume: number; rows: unknown[] }; xAnchor: unknown; width: unknown; edges?: unknown; respondsToZoom?: boolean; widthFraction?: number }[];
  rangeSelectActive?: boolean;
  profileEdgesEditable?: boolean;
  onChartApi?: (chart: unknown) => void;
  crosshairVisible?: boolean;
  viewCommand?: { kind: string; seq: number } | null;
  onRangeSelect?: (start: { time: number; price: number }, end: { time: number; price: number }) => void;
  onProfileEdgeDrag?: (id: string, edge: "start" | "end", time: number) => void;
  onProfileEdgeCommit?: (id: string, edge: "start" | "end", time: number) => void;
}

const lastChartProps: { current: ChartStubProps | null } = { current: null };

vi.mock("../components/chart/LightweightChart", () => ({
  default: (props: ChartStubProps) => {
    lastChartProps.current = props;
    return <div data-testid="chart-stub" />;
  },
}));

// The top bar's back-to-Rankings <Link> needs a router; `wrapper` (unlike wrapping the
// element) survives `rerender`.
// Some tests pass an element that already carries its own router (per-coin persistence,
// toolbars); the rest render a bare <ChartPage /> and need the wrapper for its <Link>.
const render = (ui: ReactElement) =>
  ui.type === MemoryRouter ? rtlRender(ui) : rtlRender(ui, { wrapper: MemoryRouter });
// Imported after the mocks above so ChartPage picks up the mocked client/hooks/chart.
const { default: ChartPage } = await import("./ChartPage");
const { fetchCoinIndicatorConfig } = await import("../api/client");
const page = () => (
  <MemoryRouter>
    <ChartPage />
  </MemoryRouter>
);

beforeEach(() => {
  lastChartProps.current = null;
  picker.values = {};
  picker.calls = 0;
  saveConfigMock.mockClear();
  hooks.candlesBar = [];
  hooks.liveBar = [];
  hooks.pickerBar = [];
  hooks.openGapTo = [];
  localStorage.clear();
  mocks.candles = [];
  mocks.volume = [];
  mocks.venueMarket = null;
  mocks.liveBar = null;
  mocks.session = { candles: [], volume: [], completeFrom: null };
});

afterEach(() => {
  cleanup();
});

describe("ChartPage venue badge (Story 22.4)", () => {
  it("shows `VENUE · market` from the candles response, and nothing before it loads", () => {
    const { unmount } = render(page());
    expect(screen.queryByLabelText("Venue and market")).not.toBeInTheDocument();
    unmount();

    mocks.venueMarket = { venue: "BYBIT", market: "spot" };
    render(page());
    expect(screen.getByLabelText("Venue and market")).toHaveTextContent("BYBIT · spot");
  });
});

describe("ChartPage drawing tools (Story 18.1)", () => {
  it("has cursor active by default and arms the hline tool on click (AC #1)", () => {
    render(page());

    expect(screen.getByTestId("chart-stub")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cursor tool" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Horizontal line tool" })).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(screen.getByRole("button", { name: "Horizontal line tool" }));

    expect(screen.getByRole("button", { name: "Horizontal line tool" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Cursor tool" })).toHaveAttribute("aria-pressed", "false");
  });

  it("places exactly one line from an armed hline's chart click, then disarms (AC #2)", () => {
    render(page());
    fireEvent.click(screen.getByRole("button", { name: "Horizontal line tool" }));

    // act(): the handler's state updates must flush before the assertions read the
    // props the re-render hands the stub.
    act(() => {
      lastChartProps.current!.onPriceClick!(61000.5);
    });

    expect(lastChartProps.current!.priceLines).toEqual([{ id: "hline-1", price: 61000.5, color: CHART_TOKENS["--chart-drawing"] }]);
    expect(screen.getByRole("button", { name: "Horizontal line tool" })).toHaveAttribute("aria-pressed", "false");

    // Single-click-and-done: the tool disarmed itself, so a further click adds nothing.
    act(() => {
      lastChartProps.current!.onPriceClick!(62000);
    });

    expect(lastChartProps.current!.priceLines).toHaveLength(1);
  });

  it("resets an armed tool to cursor on Escape (AC #5)", () => {
    render(page());
    fireEvent.click(screen.getByRole("button", { name: "Horizontal line tool" }));

    fireEvent.keyDown(window, { key: "Escape" });

    expect(screen.getByRole("button", { name: "Cursor tool" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Horizontal line tool" })).toHaveAttribute("aria-pressed", "false");
  });

  it("disables the hline button in Lines mode and re-enables it back in Candles", () => {
    render(page());

    fireEvent.click(screen.getByRole("button", { name: "Lines" }));
    expect(screen.getByRole("button", { name: "Horizontal line tool" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Candles" }));
    expect(screen.getByRole("button", { name: "Horizontal line tool" })).toBeEnabled();
  });

  it("disarms an armed hline when the chart switches to Lines mode", () => {
    render(page());
    fireEvent.click(screen.getByRole("button", { name: "Horizontal line tool" }));

    fireEvent.click(screen.getByRole("button", { name: "Lines" }));

    expect(screen.getByRole("button", { name: "Cursor tool" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Horizontal line tool" })).toHaveAttribute("aria-pressed", "false");
  });

  it("updates the dragged line's price in the priceLines prop (AC #3)", () => {
    render(page());
    fireEvent.click(screen.getByRole("button", { name: "Horizontal line tool" }));
    act(() => {
      lastChartProps.current!.onPriceClick!(61000.5);
    });

    act(() => {
      lastChartProps.current!.onPriceLineDrag!("hline-1", 61500.25);
    });

    expect(lastChartProps.current!.priceLines).toEqual([{ id: "hline-1", price: 61500.25, color: CHART_TOKENS["--chart-drawing"] }]);
  });

  it("gives each placed line its own counter id, and a drag updates only its own spec", () => {
    render(page());
    fireEvent.click(screen.getByRole("button", { name: "Horizontal line tool" }));
    act(() => {
      lastChartProps.current!.onPriceClick!(61000.5);
    });
    fireEvent.click(screen.getByRole("button", { name: "Horizontal line tool" }));
    act(() => {
      lastChartProps.current!.onPriceClick!(62000);
    });

    expect(lastChartProps.current!.priceLines).toHaveLength(2);
    expect(lastChartProps.current!.priceLines!.map((line) => line.id)).toEqual(["hline-1", "hline-2"]);

    act(() => {
      lastChartProps.current!.onPriceLineDrag!("hline-2", 63000);
    });

    expect(lastChartProps.current!.priceLines).toEqual([
      { id: "hline-1", price: 61000.5, color: CHART_TOKENS["--chart-drawing"] },
      { id: "hline-2", price: 63000, color: CHART_TOKENS["--chart-drawing"] },
    ]);
  });
});

describe("ChartPage default layout and per-coin persistence", () => {
  it("shows only candles + a volume pane by default -- no OFI/OBI/microprice/spread panes", () => {
    render(page());

    const panes = lastChartProps.current?.panes ?? [];
    expect(panes.map((p) => p.id)).toEqual(["volume"]);
    expect(panes[0]).toMatchObject({ kind: "Histogram" });
    expect(panes[0].placement).not.toBe("overlay"); // own pane, spec A1 -- not drawn on the price pane
  });

  it("places picker indicators by catalog panel: overlay on price, oscillator/histogram in own panes", async () => {
    picker.values = { "SimpleMovingAverage_period=20.value": [], "RelativeStrengthIndex_period=14.value": [], "CancelPressure_window=200.value": [] };
    render(page());
    await act(async () => {}); // let the catalog fetch resolve

    const byId = Object.fromEntries((lastChartProps.current?.panes ?? []).map((p) => [p.id, p]));
    expect(byId["SimpleMovingAverage_period=20.value"]).toMatchObject({ kind: "Line", placement: "overlay" });
    expect(byId["RelativeStrengthIndex_period=14.value"]).toMatchObject({ kind: "Line", placement: "pane" });
    expect(byId["CancelPressure_window=200.value"]).toMatchObject({ kind: "Histogram", placement: "pane" });
    // legend info: grouped by instance, titled with its name, tooltip = output attr. No saved entry
    // owns these series, so their rows are a plain readout (no buttons acting on nothing).
    expect(byId["RelativeStrengthIndex_period=14.value"]).toMatchObject({
      group: "RelativeStrengthIndex_period=14",
      groupLabel: "RelativeStrengthIndex",
      outputLabel: "value",
      actionable: false,
    });
    expect(lastChartProps.current?.panes?.find((p) => p.id === "volume")).toMatchObject({ groupLabel: "Volume" });
  });

  it("offers a CandlePattern's pattern as a dropdown of the catalog's choices and draws it as a histogram", async () => {
    const entry = { name: "CandlePattern", params: { pattern: "ENGULFING", trend_bars: 3 }, category: "native" };
    vi.mocked(fetchCoinIndicatorConfig).mockResolvedValueOnce([entry]);
    picker.values = { "CandlePattern_pattern=ENGULFING,trend_bars=3.value": [] };
    render(page());
    await act(async () => {}); // catalog + saved config

    // The below-chart list is gone (Story 32.3): the legend's gear opens the same editor as a modal.
    const id = "CandlePattern_pattern=ENGULFING,trend_bars=3";
    expect(lastChartProps.current!.panes!.find((p) => p.id === `${id}.value`)).toMatchObject({
      group: id,
      actionable: true,
    });
    act(() => lastChartProps.current!.onLegendAction!("settings", id));
    const dialog = screen.getByRole("dialog", { name: "CandlePattern (ENGULFING, 3)" });
    const select = within(dialog).getByLabelText<HTMLSelectElement>(/pattern/);
    expect(select.tagName).toBe("SELECT");
    expect(Array.from(select.options).map((o) => o.value)).toEqual(["DOJI", "HAMMER", "ENGULFING"]);
    expect(select.value).toBe("ENGULFING");
    expect(within(dialog).getByLabelText(/trend_bars/).tagName).toBe("INPUT"); // no choices: a text field

    fireEvent.change(select, { target: { value: "HAMMER" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Apply" }));
    await act(async () => {});

    expect(saveConfigMock).toHaveBeenCalledWith("BTC-USD-PERP.DYDX", [
      expect.objectContaining({ ...entry, params: { pattern: "HAMMER", trend_bars: 3 } }),
    ]);
    const byId = Object.fromEntries((lastChartProps.current?.panes ?? []).map((p) => [p.id, p]));
    expect(byId["CandlePattern_pattern=ENGULFING,trend_bars=3.value"]).toMatchObject({
      kind: "Histogram",
      placement: "pane",
      // After Apply the values mock still keys the old params, so no entry owns the series any
      // more: it keeps its own instance group, with no buttons (the real hook refetches under the
      // new key).
      group: id,
      actionable: false,
    });
  });

  it("persists placed horizontal lines per coin across a remount", () => {
    const first = render(page());
    fireEvent.click(screen.getByRole("button", { name: "Horizontal line tool" }));
    act(() => lastChartProps.current?.onPriceClick?.(123.5));
    act(() => lastChartProps.current?.onPriceLineDrag?.("hline-1", 130));
    first.unmount();

    render(page());
    expect(lastChartProps.current?.priceLines).toEqual([expect.objectContaining({ id: "hline-1", price: 130 })]);

    // a second placement continues the counter instead of colliding with the restored id
    fireEvent.click(screen.getByRole("button", { name: "Horizontal line tool" }));
    act(() => lastChartProps.current?.onPriceClick?.(99));
    expect(lastChartProps.current?.priceLines?.map((l) => l.id)).toEqual(["hline-1", "hline-2"]);
  });
});

const last = (xs: number[]): number | undefined => xs[xs.length - 1];

describe("ChartPage toolbars and timeframe (spec A8.1)", () => {
  it("offers 1m/5m/15m/1H/4H/1D/1W, defaults to 1m, and feeds the chosen bar size to every data hook", () => {
    render(page());

    const labels = ["1m", "5m", "15m", "1H", "4H", "1D", "1W"];
    for (const l of labels) expect(screen.getByRole("button", { name: `Timeframe ${l}` })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Timeframe 1m" })).toHaveAttribute("aria-pressed", "true");
    expect(last(hooks.candlesBar)).toBe(60);

    expect(screen.queryByRole("button", { name: "Timeframe 1s" })).toBeNull(); // 1s removed on purpose

    fireEvent.click(screen.getByRole("button", { name: "Timeframe 4H" }));

    expect(screen.getByRole("button", { name: "Timeframe 4H" })).toHaveAttribute("aria-pressed", "true");
    expect([last(hooks.candlesBar), last(hooks.liveBar), last(hooks.pickerBar)]).toEqual([14400, 14400, 14400]);
  });

  it("remembers the timeframe per coin across a remount", () => {
    const first = render(page());
    fireEvent.click(screen.getByRole("button", { name: "Timeframe 1D" }));
    first.unmount();

    render(page());
    expect(screen.getByRole("button", { name: "Timeframe 1D" })).toHaveAttribute("aria-pressed", "true");
    expect(last(hooks.candlesBar)).toBe(86400);
  });

  // Story 32.4 (2026-09-30): still no toggle. The chart area alone is light, by operator decision,
  // through the `.chart-workspace` tokens in theme.css; the rest of the app keeps the VGA identity.
  it("orders the top toolbar [symbol+timeframe] [chart type] [indicators+fit+latest], with no theme toggle", () => {
    render(page());

    const names = within(screen.getByRole("toolbar", { name: "Chart controls" }))
      .getAllByRole("button")
      .map((b) => b.getAttribute("aria-label") ?? b.textContent);
    expect(names).toEqual([
      ...["1m", "5m", "15m", "1H", "4H", "1D", "1W"].map((l) => `Timeframe ${l}`),
      "Candles",
      "Lines",
      "Indicators",
      "Alert",
      "Fit",
      "Latest",
      "Replay",
    ]);
    expect(screen.getByRole("link", { name: /Rankings/ })).toHaveAttribute("href", "/");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("BTC-USD-PERP.DYDX");
  });

  it("orders the left toolbar cursor, crosshair | horizontal line", () => {
    render(page());

    const names = within(screen.getByRole("toolbar", { name: "Chart tools" }))
      .getAllByRole("button")
      .map((b) => b.getAttribute("aria-label"));
    expect(names).toEqual([
      "Cursor tool",
      "Crosshair toggle",
      "Trendline tool",
      "Horizontal line tool",
      "Measurement tool",
      "Fixed range volume profile tool",
    ]);
  });
});

describe("ChartPage indicators dialog (spec A4.1)", () => {
  async function openDialog(): Promise<HTMLElement> {
    render(page());
    await act(async () => {}); // catalog load
    fireEvent.click(within(screen.getByRole("toolbar", { name: "Chart controls" })).getByRole("button", { name: "Indicators" }));
    return screen.getByRole("dialog", { name: "Indicators" });
  }

  it("opens a searchable dialog from the Indicators button", async () => {
    const dialog = await openDialog();

    expect(within(dialog).getAllByRole("listitem")).toHaveLength(4);
    fireEvent.change(within(dialog).getByLabelText("Search indicators"), { target: { value: "rela" } });
    expect(within(dialog).getAllByRole("listitem")).toHaveLength(1);
    expect(within(dialog).getByText("RelativeStrengthIndex")).toBeInTheDocument();
  });

  it("filters by the Overlays / Oscillators categories (histogram counts as oscillator)", async () => {
    const dialog = await openDialog();

    fireEvent.click(within(dialog).getByRole("button", { name: "Overlays" }));
    expect(within(dialog).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["SimpleMovingAverageoverlay"]);

    fireEvent.click(within(dialog).getByRole("button", { name: "Oscillators" }));
    expect(within(dialog).getAllByRole("listitem")).toHaveLength(3);
  });

  it("adds a result immediately with default params -- no confirm step -- and marks it added", async () => {
    const dialog = await openDialog();

    fireEvent.click(within(dialog).getByRole("button", { name: /^SimpleMovingAverage/ }));
    await act(async () => {});

    expect(saveConfigMock).toHaveBeenCalledWith("BTC-USD-PERP.DYDX", [
      { name: "SimpleMovingAverage", params: {}, category: "native" },
    ]);
    expect(within(dialog).getByRole("button", { name: /^SimpleMovingAverage/ })).toBeDisabled();
    expect(within(dialog).getByText("added")).toBeInTheDocument();
  });
});

describe("ChartPage volume toggle (Story 32.2)", () => {
  const VOLUME_KEY = "chart-volume:BTC-USD-PERP.DYDX";
  const paneIds = () => (lastChartProps.current?.panes ?? []).map((p) => p.id);
  async function openDialog(): Promise<HTMLElement> {
    render(page());
    await act(async () => {}); // catalog load
    fireEvent.click(within(screen.getByRole("toolbar", { name: "Chart controls" })).getByRole("button", { name: "Indicators" }));
    return screen.getByRole("dialog", { name: "Indicators" });
  }

  it("pins Volume above the categories, on by default, without a params editor", async () => {
    const dialog = await openDialog();

    const toggle = within(dialog).getByRole("checkbox", { name: "Volume" });
    expect(toggle).toBeChecked();
    expect(paneIds()).toEqual(["volume"]);
    const categories = within(dialog).getByRole("group", { name: "Category" });
    expect(toggle.compareDocumentPosition(categories) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("removes the volume pane when switched off and stores 'off' under chart-volume:{iid}", async () => {
    const dialog = await openDialog();

    fireEvent.click(within(dialog).getByRole("checkbox", { name: "Volume" }));

    expect(paneIds()).toEqual([]);
    expect(localStorage.getItem(VOLUME_KEY)).toBe("off");
  });

  it("restores the off state on reload", () => {
    localStorage.setItem(VOLUME_KEY, "off");
    render(page());

    expect(paneIds()).toEqual([]);
  });

  it("puts volume first, before every indicator pane, when switched back on", async () => {
    localStorage.setItem(VOLUME_KEY, "off");
    picker.values = { "RelativeStrengthIndex_period=14.value": [] };
    const dialog = await openDialog();
    expect(paneIds()).toEqual(["RelativeStrengthIndex_period=14.value"]);

    fireEvent.click(within(dialog).getByRole("checkbox", { name: "Volume" }));

    expect(paneIds()).toEqual(["volume", "RelativeStrengthIndex_period=14.value"]);
    expect(localStorage.getItem(VOLUME_KEY)).toBe("on");
  });

  it("shows volume again after hide (eye) then off then on in the Indicators dialog", async () => {
    const dialog = await openDialog();
    act(() => lastChartProps.current!.onLegendAction!("hide", "volume"));
    expect(lastChartProps.current!.panes!.find((p) => p.id === "volume")!.hidden).toBe(true);

    fireEvent.click(within(dialog).getByRole("checkbox", { name: "Volume" })); // off
    fireEvent.click(within(dialog).getByRole("checkbox", { name: "Volume" })); // on

    expect(lastChartProps.current!.panes!.find((p) => p.id === "volume")!.hidden).toBeFalsy();
  });

  it("keeps the choice across a timeframe change", async () => {
    localStorage.setItem(VOLUME_KEY, "off");
    render(page());

    fireEvent.click(screen.getByRole("button", { name: "Timeframe 5m" }));

    expect(paneIds()).toEqual([]);
  });

  it("still feeds the fetched volume to the profiles with the pane off", () => {
    const bars = [1, 2, 3, 4, 5].map((t) => ({ time: t, open: t, high: t + 1, low: t, close: t + 1 }));
    mocks.candles = bars;
    mocks.volume = bars.map((b) => ({ time: b.time, value: 10 }));
    const totalWith = (): number => {
      fireEvent.click(screen.getByRole("button", { name: "Fixed range volume profile tool" }));
      act(() => {
        lastChartProps.current!.onRangeSelect!({ time: 2, price: 1 }, { time: 4, price: 2 });
      });
      return lastChartProps.current!.volumeProfiles![0].profile.totalVolume;
    };
    const on = render(page());
    const withPane = totalWith();
    on.unmount();

    localStorage.setItem(VOLUME_KEY, "off");
    render(page());
    expect(paneIds()).toEqual([]);

    expect(totalWith()).toBe(withPane);
  });

  it("defaults to on and logs one console.error when localStorage throws", () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    try {
      render(page());

      expect(paneIds()).toEqual(["volume"]);
      expect(errors.mock.calls.filter((c) => String(c[0]).startsWith("chart-volume"))).toHaveLength(1);
    } finally {
      getItem.mockRestore();
      errors.mockRestore();
    }
  });
});

describe("ChartPage trendline tool (Story 18.2)", () => {
  const arm = () => fireEvent.click(screen.getByRole("button", { name: "Trendline tool" }));
  const click = (time: number, price: number) =>
    act(() => {
      lastChartProps.current!.onPointClick!({ time, price });
    });

  it("creates a trendline from two clicks, then disarms (AC #1/#2)", () => {
    render(<ChartPage />);
    arm();

    click(100, 10);
    expect(lastChartProps.current!.drawings).toEqual([]);
    click(200, 20);

    expect(lastChartProps.current!.drawings).toEqual([
      {
        id: "trendline-1",
        kind: "trendline",
        anchors: [
          { time: 100, price: 10 },
          { time: 200, price: 20 },
        ],
        color: CHART_TOKENS["--chart-drawing"],
      },
    ]);
    expect(screen.getByRole("button", { name: "Cursor tool" })).toHaveAttribute("aria-pressed", "true");
  });

  it("ignores point clicks while the trendline tool is not armed", () => {
    render(<ChartPage />);

    click(100, 10);
    click(200, 20);

    expect(lastChartProps.current!.drawings).toEqual([]);
  });

  it("cancels an in-progress line on Escape without creating a drawing (AC #5)", () => {
    render(<ChartPage />);
    arm();
    click(100, 10);

    fireEvent.keyDown(window, { key: "Escape" });
    arm();
    click(200, 20);
    click(300, 30);

    // The Esc'd first point is gone: the new line starts at 200, not 100.
    expect(lastChartProps.current!.drawings).toHaveLength(1);
    expect(lastChartProps.current!.drawings![0].anchors[0]).toEqual({ time: 200, price: 20 });
  });

  it("discards a pending first point when another tool is selected", () => {
    render(<ChartPage />);
    arm();
    click(100, 10);

    fireEvent.click(screen.getByRole("button", { name: "Cursor tool" }));
    arm();
    click(200, 20);
    click(300, 30);

    expect(lastChartProps.current!.drawings![0].anchors[0]).toEqual({ time: 200, price: 20 });
  });

  it("works in Lines mode too", () => {
    render(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Lines" }));

    expect(screen.getByRole("button", { name: "Trendline tool" })).toBeEnabled();
  });
});

describe("ChartPage measurement tool (Story 18.3)", () => {
  it("arms the measurement tool and disarms it when the drag ends (AC #1/#5)", () => {
    render(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Measurement tool" }));
    expect(lastChartProps.current!.measureActive).toBe(true);

    act(() => {
      lastChartProps.current!.onMeasureEnd!();
    });

    expect(lastChartProps.current!.measureActive).toBe(false);
    expect(screen.getByRole("button", { name: "Cursor tool" })).toHaveAttribute("aria-pressed", "true");
  });

  it("cancels an armed measurement on Escape", () => {
    render(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Measurement tool" }));

    fireEvent.keyDown(window, { key: "Escape" });

    expect(lastChartProps.current!.measureActive).toBe(false);
  });

  it("is disabled in Lines mode", () => {
    render(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Lines" }));

    expect(screen.getByRole("button", { name: "Measurement tool" })).toBeDisabled();
  });
});

describe("ChartPage bar replay (Story 18.4)", () => {
  const bars = [60, 120, 180, 240, 300].map((time) => ({ time, open: 1, high: 2, low: 1, close: 1 }));
  const pickBar = (time: number) =>
    act(() => {
      lastChartProps.current!.onPointClick!({ time, price: 1 });
    });

  beforeEach(() => {
    mocks.candles = bars;
    mocks.liveBar = { time: 360, open: 1, high: 1, low: 1, close: 1 };
  });

  it("opens the gap run up to the forming live bar (Story 32.1)", () => {
    render(<ChartPage />);
    expect(hooks.openGapTo).toContain(360);
  });

  it("opens the gap run once history lands when the live bar arrived first (Story 32.1)", () => {
    mocks.candles = [];
    const { rerender } = render(<ChartPage />);
    expect(hooks.openGapTo).toEqual([]);
    mocks.candles = bars;
    rerender(<ChartPage />);
    expect(hooks.openGapTo).toContain(360);
  });

  it("picks a start bar, hides later bars, marks it and suppresses the live bar (AC #1/#2)", () => {
    render(<ChartPage />);
    expect(lastChartProps.current!.data).toHaveLength(5);
    expect(lastChartProps.current!.liveBar).toBe(mocks.liveBar);

    fireEvent.click(screen.getByRole("button", { name: "Replay" }));
    expect(screen.getByText("Click a candle to start the replay")).toBeInTheDocument();
    pickBar(120);

    expect(lastChartProps.current!.data!.map((c) => c.time)).toEqual([60, 120]);
    expect(lastChartProps.current!.markerTime).toBe(120);
    expect(lastChartProps.current!.liveBar).toBeNull();
    expect(screen.getByRole("group", { name: "Replay controls" })).toBeInTheDocument();
  });

  it("trims volume and indicator panes to the replay position too (no future leak)", () => {
    mocks.volume = bars.map((b) => ({ time: b.time, value: 1 }));
    render(<ChartPage />);
    expect(lastChartProps.current!.panes!.find((p) => p.id === "volume")!.data).toHaveLength(5);

    fireEvent.click(screen.getByRole("button", { name: "Replay" }));
    pickBar(120);

    expect(lastChartProps.current!.panes!.find((p) => p.id === "volume")!.data.map((d) => d.time)).toEqual([60, 120]);
  });

  it("steps forward and back through the control bar (AC #3/#4)", () => {
    render(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Replay" }));
    pickBar(120);

    fireEvent.click(screen.getByRole("button", { name: "Step forward" }));
    expect(lastChartProps.current!.data).toHaveLength(3);
    fireEvent.click(screen.getByRole("button", { name: "Step back" }));
    fireEvent.click(screen.getByRole("button", { name: "Step back" }));
    expect(lastChartProps.current!.data).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Step back" }));
    expect(lastChartProps.current!.data).toHaveLength(1);
  });

  it("restores the full dataset and removes the controls and marker on Exit (AC #6)", () => {
    render(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Replay" }));
    pickBar(120);

    fireEvent.click(screen.getByRole("button", { name: "Exit" }));

    expect(lastChartProps.current!.data).toHaveLength(5);
    expect(lastChartProps.current!.markerTime).toBeNull();
    expect(lastChartProps.current!.liveBar).toBe(mocks.liveBar);
    expect(screen.queryByRole("group", { name: "Replay controls" })).toBeNull();
  });

  it("Go to... re-picks without leaving replay; Esc while picking returns to the replay", () => {
    render(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Replay" }));
    pickBar(120);

    fireEvent.click(screen.getByRole("button", { name: "Go to..." }));
    expect(lastChartProps.current!.data).toHaveLength(5);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(lastChartProps.current!.data).toHaveLength(2);

    fireEvent.click(screen.getByRole("button", { name: "Go to..." }));
    pickBar(240);
    expect(lastChartProps.current!.data).toHaveLength(4);
    expect(lastChartProps.current!.markerTime).toBe(240);
  });

  it("does not place a trendline point from the click that picks the start bar", () => {
    render(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Replay" }));

    pickBar(120);

    fireEvent.click(screen.getByRole("button", { name: "Trendline tool" }));
    pickBar(60);
    pickBar(120);
    expect(lastChartProps.current!.drawings).toHaveLength(1);
  });

  it("is disabled in Lines mode", () => {
    render(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Lines" }));

    expect(screen.getByRole("button", { name: "Replay" })).toBeDisabled();
  });
});

describe("ChartPage fixed range volume profile (Story 18.6)", () => {
  const bars = [1, 2, 3, 4, 5].map((n) => ({ time: n, open: n, high: n + 1, low: n, close: n + 1 }));
  const select = (a: number, b: number) =>
    act(() => {
      lastChartProps.current!.onRangeSelect!({ time: a, price: 1 }, { time: b, price: 2 });
    });

  beforeEach(() => {
    mocks.candles = bars;
    mocks.volume = bars.map((b) => ({ time: b.time, value: 10 }));
  });

  it("arms the FRVP tool, computes the profile for the dragged range once, then disarms (AC #1/#2)", () => {
    render(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Fixed range volume profile tool" }));
    expect(lastChartProps.current!.rangeSelectActive).toBe(true);

    select(2, 4);

    const [spec] = lastChartProps.current!.volumeProfiles!;
    expect(spec.id).toBe("frvp-1");
    expect(spec.profile.totalVolume).toBe(30);
    expect(spec.xAnchor).toEqual({ time: 2 });
    expect(spec.width).toEqual({ toTime: 4 });
    expect(lastChartProps.current!.rangeSelectActive).toBe(false);
  });

  it("stays static: new candles/pan never recompute a placed profile (AC #3)", () => {
    const { rerender } = render(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Fixed range volume profile tool" }));
    select(2, 4);
    const placed = lastChartProps.current!.volumeProfiles![0].profile;

    mocks.candles = [...bars, { time: 6, open: 6, high: 7, low: 6, close: 7 }];
    mocks.volume = mocks.candles.map((b) => ({ time: (b as { time: number }).time, value: 99 }));
    rerender(<ChartPage />);

    expect(lastChartProps.current!.volumeProfiles![0].profile).toBe(placed);
  });

  it("moves a ghost edge without recomputing, then recomputes once on commit (AC #3)", () => {
    render(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Fixed range volume profile tool" }));
    select(2, 4);
    const placed = lastChartProps.current!.volumeProfiles![0].profile;

    act(() => lastChartProps.current!.onProfileEdgeDrag!("frvp-1", "end", 5));
    expect(lastChartProps.current!.volumeProfiles![0].width).toEqual({ toTime: 5 });
    expect(lastChartProps.current!.volumeProfiles![0].profile).toBe(placed);

    act(() => lastChartProps.current!.onProfileEdgeCommit!("frvp-1", "end", 5));
    expect(lastChartProps.current!.volumeProfiles).toHaveLength(1);
    expect(lastChartProps.current!.volumeProfiles![0].profile.totalVolume).toBe(40);
  });

  it("supports several profiles, removable independently (AC #4)", () => {
    render(<ChartPage />);
    for (const [a, b] of [[1, 2], [3, 5]]) {
      fireEvent.click(screen.getByRole("button", { name: "Fixed range volume profile tool" }));
      select(a, b);
    }
    expect(lastChartProps.current!.volumeProfiles!.map((p) => p.id)).toEqual(["frvp-1", "frvp-2"]);

    fireEvent.click(screen.getByRole("button", { name: "Remove volume profile frvp-1" }));

    expect(lastChartProps.current!.volumeProfiles!.map((p) => p.id)).toEqual(["frvp-2"]);
  });

  it("ignores a range with no candles in it and cancels the armed tool on Escape", () => {
    render(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Fixed range volume profile tool" }));

    select(50, 60);
    expect(lastChartProps.current!.volumeProfiles).toEqual([]);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(lastChartProps.current!.rangeSelectActive).toBe(false);
  });

  it("rebuilds placed profiles when the shared settings change (row count)", () => {
    render(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Fixed range volume profile tool" }));
    select(1, 5);
    expect(lastChartProps.current!.volumeProfiles![0].profile.rows).toHaveLength(24);

    fireEvent.change(screen.getByLabelText("Row count"), { target: { value: "6" } });

    expect(lastChartProps.current!.volumeProfiles![0].profile.rows).toHaveLength(6);
  });

  it("shows only revealed bars during a replay and the full stored profile after it ends", () => {
    render(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Fixed range volume profile tool" }));
    select(1, 5);
    expect(lastChartProps.current!.volumeProfiles![0].profile.totalVolume).toBeCloseTo(50);

    fireEvent.click(screen.getByRole("button", { name: "Replay" }));
    act(() => lastChartProps.current!.onPointClick!({ time: 3, price: 1 }));
    expect(lastChartProps.current!.volumeProfiles![0].profile.totalVolume).toBeCloseTo(30);

    fireEvent.click(screen.getByRole("button", { name: "Exit" }));
    expect(lastChartProps.current!.volumeProfiles![0].profile.totalVolume).toBeCloseTo(50);
  });

  it("only lets edges be grabbed while the cursor tool is active", () => {
    render(<ChartPage />);
    expect(lastChartProps.current!.profileEdgesEditable).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Horizontal line tool" }));

    expect(lastChartProps.current!.profileEdgesEditable).toBe(false);
  });

  it("is disabled in Lines mode", () => {
    render(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Lines" }));

    expect(screen.getByRole("button", { name: "Fixed range volume profile tool" })).toBeDisabled();
  });
});

describe("ChartPage visible range volume profile (Story 18.7)", () => {
  const bars = [1, 2, 3, 4, 5, 6].map((n) => ({ time: n, open: n, high: n + 1, low: n, close: n + 1 }));
  type RangeHandler = (range: { from: number; to: number } | null) => void;
  let handlers: RangeHandler[];
  let visible: { from: number; to: number };

  const attachChart = () =>
    act(() => {
      lastChartProps.current!.onChartApi!({
        applyOptions: vi.fn(),
        timeScale: () => ({
          getVisibleRange: () => visible,
          subscribeVisibleTimeRangeChange: (h: RangeHandler) => handlers.push(h),
          unsubscribeVisibleTimeRangeChange: vi.fn(),
        }),
      });
    });
  const vrvp = () => lastChartProps.current!.volumeProfiles!.filter((p) => p.id === "vrvp");

  beforeEach(() => {
    handlers = [];
    visible = { from: 2, to: 4 };
    mocks.candles = bars;
    mocks.volume = bars.map((b) => ({ time: b.time, value: 10 }));
  });

  it("adds a right-anchored profile of the visible bars, only after Add (AC #1/#2)", () => {
    render(<ChartPage />);
    attachChart();
    expect(vrvp()).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Add visible range volume profile" }));

    expect(vrvp()).toHaveLength(1);
    expect(vrvp()[0].xAnchor).toBe("right");
    expect(vrvp()[0].profile.totalVolume).toBeCloseTo(30);
  });

  it("recomputes once per visible-range change and updates the same entry, never adding one (AC #3)", () => {
    render(<ChartPage />);
    attachChart();
    fireEvent.click(screen.getByRole("button", { name: "Add visible range volume profile" }));
    const first = vrvp()[0].profile;

    act(() => handlers.forEach((h) => h({ from: 2, to: 4 }))); // unchanged range
    expect(vrvp()[0].profile).toBe(first);

    act(() => handlers.forEach((h) => h({ from: 1, to: 6 })));
    expect(vrvp()).toHaveLength(1);
    expect(vrvp()[0].profile).not.toBe(first);
    expect(vrvp()[0].profile.totalVolume).toBeCloseTo(60);
  });

  it("subscribes to the visible range only while active, and shows revealed bars only during a replay", () => {
    render(<ChartPage />);
    attachChart();
    expect(handlers).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Add visible range volume profile" }));
    expect(handlers).toHaveLength(1);
    expect(vrvp()[0].profile.totalVolume).toBeCloseTo(30); // bars 2..4

    fireEvent.click(screen.getByRole("button", { name: "Replay" }));
    act(() => lastChartProps.current!.onPointClick!({ time: 3, price: 1 }));
    expect(vrvp()[0].profile.totalVolume).toBeCloseTo(20); // bars 2..3, bar 4 hidden
  });

  it("disables Add while active or outside Candles mode", () => {
    render(<ChartPage />);
    attachChart();
    const add = () => screen.getByRole("button", { name: "Add visible range volume profile" });
    expect(add()).toBeEnabled();

    fireEvent.click(add());
    expect(add()).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Lines" }));
    expect(screen.getByText("Shown in Candles mode only")).toBeInTheDocument();
  });

  it("is a single instance: adding again does not stack, Remove clears it (AC #4)", () => {
    render(<ChartPage />);
    attachChart();
    const add = () => fireEvent.click(screen.getByRole("button", { name: "Add visible range volume profile" }));
    add();
    add();
    expect(vrvp()).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "Remove visible range volume profile" }));

    expect(vrvp()).toHaveLength(0);
  });

  it("coexists with a placed FRVP and is hidden in Lines mode", () => {
    render(<ChartPage />);
    attachChart();
    fireEvent.click(screen.getByRole("button", { name: "Add visible range volume profile" }));
    fireEvent.click(screen.getByRole("button", { name: "Fixed range volume profile tool" }));
    act(() => lastChartProps.current!.onRangeSelect!({ time: 1, price: 1 }, { time: 3, price: 2 }));
    expect(lastChartProps.current!.volumeProfiles!.map((p) => p.id)).toEqual(["frvp-1", "vrvp"]);

    fireEvent.click(screen.getByRole("button", { name: "Lines" }));

    expect(vrvp()).toHaveLength(0);
  });
});

describe("ChartPage session volume profiles (Story 18.8)", () => {
  const D1 = Date.UTC(2024, 0, 1) / 1000;
  const D2 = D1 + 86_400;
  const D3 = D2 + 86_400;
  const times = [D1, D1 + 60, D2, D2 + 60, D3, D3 + 60];
  const bar = (t: number, p: number) => ({ time: t, open: p, high: p + 1, low: p, close: p + 1 });
  const sessions = () =>
    lastChartProps.current!.volumeProfiles!.filter((p) => p.id.startsWith("session-"));

  beforeEach(() => {
    const candles = times.map((t, i) => bar(t, 10 + i * 10));
    mocks.candles = candles;
    mocks.session = { candles, volume: times.map((t) => ({ time: t, value: 5 })), completeFrom: null };
  });

  it("adds one independent profile per UTC day, anchored to that session's bars (AC #1/#4)", () => {
    render(<ChartPage />);
    expect(sessions()).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Add Session Volume Profile" }));

    expect(sessions().map((s) => s.id)).toEqual([`session-${D1}`, `session-${D2}`, `session-${D3}`]);
    expect(sessions()[1].xAnchor).toEqual({ time: D2 });
    expect(sessions()[1].width).toEqual({ toTime: D2 + 60 });
    expect(sessions()[1].profile.totalVolume).toBeCloseTo(10);
    expect(sessions()[0].profile).not.toBe(sessions()[1].profile);
  });

  it("SVP HD is the same component with a higher row count and respondsToZoom (AC #3)", () => {
    render(<ChartPage />);

    fireEvent.click(screen.getByRole("button", { name: "Add Session Volume Profile" }));
    expect(sessions()[0].profile.rows).toHaveLength(24);
    expect(sessions()[0].respondsToZoom).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Add Session Volume Profile HD" }));
    expect(sessions()).toHaveLength(3); // switched preset, not stacked
    expect(sessions()[0].profile.rows).toHaveLength(120);
    expect(sessions()[0].respondsToZoom).toBe(true);
  });

  it("keeps the session count and colors when switching presets", () => {
    render(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Add Session Volume Profile" }));
    fireEvent.change(screen.getByLabelText("Sessions to render"), { target: { value: "2" } });

    fireEvent.click(screen.getByRole("button", { name: "Add Session Volume Profile HD" }));

    expect(sessions()).toHaveLength(2);
    expect((screen.getByLabelText("Sessions to render") as HTMLInputElement).value).toBe("2");
  });

  it("says so when fewer sessions than requested can be drawn", () => {
    render(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Add Session Volume Profile" }));
    expect(screen.getByText(/Showing 3 of 5/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Sessions to render"), { target: { value: "3" } });

    expect(screen.queryByText(/Showing/)).toBeNull();
  });

  it("limits the rendered sessions with the sessions setting (AC #4)", () => {
    render(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Add Session Volume Profile" }));

    fireEvent.change(screen.getByLabelText("Sessions to render"), { target: { value: "2" } });

    expect(sessions().map((s) => s.id)).toEqual([`session-${D2}`, `session-${D3}`]);
  });

  it("can be removed, and is hidden in Lines mode", () => {
    render(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Add Session Volume Profile" }));
    fireEvent.click(screen.getByRole("button", { name: "Lines" }));
    expect(sessions()).toHaveLength(0);
    expect(screen.getByText("Shown in Candles mode only")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Candles" }));
    expect(sessions()).toHaveLength(3);
    fireEvent.click(screen.getByRole("button", { name: "Remove session volume profile" }));
    expect(sessions()).toHaveLength(0);
  });

  it("draws a session over the part of it the chart has loaded, and skips one the chart holds none of", () => {
    mocks.candles = [bar(D3, 50), bar(D3 + 60, 60)]; // chart only loaded the last day
    render(<ChartPage />);

    fireEvent.click(screen.getByRole("button", { name: "Add Session Volume Profile" }));

    expect(sessions().map((s) => s.id)).toEqual([`session-${D3}`]);
  });
});

describe("ChartPage periodic volume profile (Story 18.9)", () => {
  const MON1 = Date.UTC(2024, 0, 1) / 1000; // a Monday
  const MON2 = MON1 + 7 * 86_400;
  const times = [MON1, MON1 + 3 * 86_400, MON2, MON2 + 86_400];
  const bar = (t: number, p: number) => ({ time: t, open: p, high: p + 1, low: p, close: p + 1 });
  const periods = () => lastChartProps.current!.volumeProfiles!.filter((p) => p.id.startsWith("session-"));

  beforeEach(() => {
    const candles = times.map((t, i) => bar(t, 10 + i * 10));
    mocks.candles = candles;
    mocks.session = { candles, volume: times.map((t) => ({ time: t, value: 5 })), completeFrom: null };
  });

  it("adds a periodic profile (weekly by default) with one profile per period (AC #1/#2)", () => {
    render(<ChartPage />);

    fireEvent.click(screen.getByRole("button", { name: "Add Periodic Volume Profile" }));

    expect(periods().map((p) => p.id)).toEqual([`session-${MON1}`, `session-${MON2}`]);
    expect(periods().map((p) => p.profile.totalVolume)).toEqual([10, 10]);
    expect((screen.getByLabelText("Profile period") as HTMLSelectElement).value).toBe("weekly");
  });

  it("regroups when the period dropdown changes, offering only the fixed set", () => {
    render(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Add Periodic Volume Profile" }));
    const select = screen.getByLabelText("Profile period") as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).toEqual(["4h", "daily", "weekly", "monthly"]);

    fireEvent.change(select, { target: { value: "daily" } });

    expect(periods()).toHaveLength(4); // four distinct UTC days
  });

  it("switching the period refetches at that period's bar size and a deeper wanted start", () => {
    render(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Add Periodic Volume Profile" }));
    const weekly = { ...mocks.sessionArgs };
    expect(weekly).toMatchObject({ enabled: true, barSeconds: 300 });

    fireEvent.change(screen.getByLabelText("Profile period"), { target: { value: "monthly" } });

    expect(mocks.sessionArgs.barSeconds).toBe(900);
    expect(mocks.sessionArgs.sinceSeconds).toBeLessThan(weekly.sinceSeconds); // 5 months back vs 5 weeks back
  });

  it("switching PVP -> SVP -> PVP restarts on the preset's default period", () => {
    render(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Add Periodic Volume Profile" }));
    fireEvent.change(screen.getByLabelText("Profile period"), { target: { value: "monthly" } });

    fireEvent.click(screen.getByRole("button", { name: "Add Session Volume Profile" }));
    expect(mocks.sessionArgs.barSeconds).toBe(60);
    fireEvent.click(screen.getByRole("button", { name: "Add Periodic Volume Profile" }));

    expect((screen.getByLabelText("Profile period") as HTMLSelectElement).value).toBe("weekly");
  });

  it("reuses the shared sessions-to-render setting (AC #3), and only PVP shows a period dropdown", () => {
    render(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Add Periodic Volume Profile" }));
    fireEvent.change(screen.getByLabelText("Sessions to render"), { target: { value: "1" } });
    expect(periods()).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "Add Session Volume Profile" }));
    expect(screen.queryByLabelText("Profile period")).toBeNull();
    expect(screen.getAllByLabelText("Sessions to render")).toHaveLength(1);
  });
});

describe("cross-story: drawing tools during replay (Story 18.4 AC #5, verified end-to-end at page level in 18.10)", () => {
  const bars = [1, 2, 3, 4, 5].map((n) => ({ time: n, open: n, high: n + 1, low: n, close: n + 1 }));
  const point = (time: number, price: number) =>
    act(() => {
      lastChartProps.current!.onPointClick!({ time, price });
    });

  beforeEach(() => {
    mocks.candles = bars;
    mocks.volume = bars.map((b) => ({ time: b.time, value: 10 }));
  });

  it("places a trendline and a horizontal line while a replay is active, and they survive stepping", () => {
    render(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Replay" }));
    point(3, 1);
    expect(lastChartProps.current!.data).toHaveLength(3);

    fireEvent.click(screen.getByRole("button", { name: "Trendline tool" }));
    point(1, 1);
    point(3, 2);
    fireEvent.click(screen.getByRole("button", { name: "Horizontal line tool" }));
    act(() => lastChartProps.current!.onPriceClick!(1.5));
    const drawings = lastChartProps.current!.drawings;
    expect(drawings).toHaveLength(1);
    expect(lastChartProps.current!.priceLines).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "Step forward" }));
    expect(lastChartProps.current!.data).toHaveLength(4);
    expect(lastChartProps.current!.drawings).toBe(drawings);
    expect(lastChartProps.current!.priceLines).toHaveLength(1);
  });

  it("Esc cancels an in-progress tool during replay without ending the replay", () => {
    render(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Replay" }));
    point(3, 1);
    fireEvent.click(screen.getByRole("button", { name: "Trendline tool" }));
    point(1, 1);

    fireEvent.keyDown(window, { key: "Escape" });

    expect(screen.getByRole("group", { name: "Replay controls" })).toBeInTheDocument();
    expect(lastChartProps.current!.drawings).toHaveLength(0);
  });
});

// Story 32.3: the legend is the indicator's control surface. LightweightChart is a stub here, so
// the legend's eye / gear / x arrive as `onLegendAction` calls, exactly as the real one reports.
describe("ChartPage legend controls (Story 32.3)", () => {
  const SMA_ID = "SimpleMovingAverage_period=20";
  const SMA = { name: "SimpleMovingAverage", params: { period: 20 }, category: "native" };
  const RSI = { name: "RelativeStrengthIndex", params: { period: 14 }, category: "native" };
  const paneOf = (id: string) => lastChartProps.current!.panes!.find((p) => p.id === id)!;
  const legend = (action: "hide" | "settings" | "remove", group: string) =>
    act(() => lastChartProps.current!.onLegendAction!(action, group));

  async function mountWith(entries: object[], values: Record<string, never[]>): Promise<void> {
    vi.mocked(fetchCoinIndicatorConfig).mockResolvedValueOnce(entries as never);
    picker.values = values;
    render(page());
    await act(async () => {}); // catalog + saved config
  }

  it("the Cursor button says what it does, and is active again after a Trend tool completes", () => {
    render(page());
    const cursor = screen.getByRole("button", { name: "Cursor tool" });
    expect(cursor).toHaveAttribute("title", "Select / edit drawings (Esc)");

    fireEvent.click(screen.getByRole("button", { name: "Trendline tool" }));
    expect(cursor).toHaveAttribute("aria-pressed", "false");
    act(() => lastChartProps.current!.onPointClick!({ time: 1, price: 1 }));
    act(() => lastChartProps.current!.onPointClick!({ time: 2, price: 2 }));

    expect(lastChartProps.current!.drawings).toHaveLength(1);
    expect(cursor).toHaveAttribute("aria-pressed", "true");
    expect(lastChartProps.current).toMatchObject({ drawEditable: true });
  });

  it("has no indicator list, select or Add below the chart, and keeps the div#indicators anchor", async () => {
    await mountWith([SMA], { [`${SMA_ID}.value`]: [] });

    expect(screen.queryByRole("button", { name: "Add" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Indicators" })).toBeNull();
    expect(document.querySelector("div#indicators")).not.toBeNull();
    expect(document.querySelector("div#indicators")?.contains(screen.getByRole("heading", { name: "Chart overlays" }))).toBe(true);
  });

  it("titles the legend row per instance: RSI(14) and RSI(21) are two rows, each with its own params", async () => {
    const rsi21 = { ...RSI, params: { period: 21 } };
    await mountWith([RSI, rsi21], {
      "RelativeStrengthIndex_period=14.value": [],
      "RelativeStrengthIndex_period=21.value": [],
    });

    const rows = lastChartProps.current!.panes!.filter((p) => p.id !== "volume").map((p) => [p.group, p.groupLabel]);
    expect(rows).toEqual([
      ["RelativeStrengthIndex_period=14", "RelativeStrengthIndex (14)"],
      ["RelativeStrengthIndex_period=21", "RelativeStrengthIndex (21)"],
    ]);
  });

  it("hides an overlay in place: persists hidden: true, flags the spec, and shows it again with hidden: false", async () => {
    await mountWith([SMA], { [`${SMA_ID}.value`]: [] });
    expect(paneOf(`${SMA_ID}.value`).hidden).toBe(false);

    legend("hide", SMA_ID);
    await act(async () => {});
    expect(saveConfigMock).toHaveBeenLastCalledWith("BTC-USD-PERP.DYDX", [{ ...SMA, hidden: true }]);
    expect(paneOf(`${SMA_ID}.value`)).toMatchObject({ hidden: true, placement: "overlay" });

    legend("hide", SMA_ID);
    await act(async () => {});
    expect(saveConfigMock).toHaveBeenLastCalledWith("BTC-USD-PERP.DYDX", [{ ...SMA, hidden: false }]);
    expect(paneOf(`${SMA_ID}.value`).hidden).toBe(false);
  });

  it("hides a pane indicator by flagging its spec (the chart collapses the pane), data kept in the spec", async () => {
    await mountWith([RSI], { "RelativeStrengthIndex_period=14.value": [] });

    legend("hide", "RelativeStrengthIndex_period=14");
    await act(async () => {});

    expect(paneOf("RelativeStrengthIndex_period=14.value")).toMatchObject({ hidden: true, placement: "pane" });
  });

  it("two legend toggles in the same tick both land: the second builds on the first", async () => {
    await mountWith([SMA, RSI], { [`${SMA_ID}.value`]: [] });

    act(() => {
      lastChartProps.current!.onLegendAction!("hide", SMA_ID);
      lastChartProps.current!.onLegendAction!("hide", "RelativeStrengthIndex_period=14");
    });
    await act(async () => {});

    expect(saveConfigMock).toHaveBeenLastCalledWith("BTC-USD-PERP.DYDX", [
      { ...SMA, hidden: true },
      { ...RSI, hidden: true },
    ]);
  });

  it("closes the settings modal on a backdrop click only when the press began on the backdrop", async () => {
    await mountWith([SMA], { [`${SMA_ID}.value`]: [] });
    legend("settings", SMA_ID);
    const dialog = screen.getByRole("dialog", { name: "SimpleMovingAverage (20)" });

    // A text-selection drag: pressed inside the content, released on the backdrop.
    fireEvent.mouseDown(within(dialog).getByRole("heading", { name: "SimpleMovingAverage (20)" }));
    fireEvent.click(dialog);
    expect(screen.queryByRole("dialog", { name: "SimpleMovingAverage (20)" })).not.toBeNull();

    fireEvent.mouseDown(dialog);
    fireEvent.click(dialog);
    expect(screen.queryByRole("dialog", { name: "SimpleMovingAverage (20)" })).toBeNull();
  });

  it("removes an indicator from its legend x through the same persist path as an add", async () => {
    await mountWith([SMA, RSI], { [`${SMA_ID}.value`]: [] });

    legend("remove", SMA_ID);
    await act(async () => {});

    expect(saveConfigMock).toHaveBeenLastCalledWith("BTC-USD-PERP.DYDX", [RSI]);
  });

  it("gives Volume an eye (hide, no save) and an x (the Indicators toggle) but no settings", () => {
    render(page());
    expect(paneOf("volume")).toMatchObject({ configurable: false, hidden: false });

    legend("hide", "volume");
    expect(paneOf("volume").hidden).toBe(true);
    legend("hide", "volume");
    expect(paneOf("volume").hidden).toBe(false);

    legend("settings", "volume"); // there is no gear: nothing opens
    expect(screen.queryByRole("dialog", { name: /Volume/ })).toBeNull();

    legend("remove", "volume");
    expect(lastChartProps.current!.panes!.map((p) => p.id)).toEqual([]);
    expect(localStorage.getItem("chart-volume:BTC-USD-PERP.DYDX")).toBe("off");
    expect(saveConfigMock).not.toHaveBeenCalled();
  });

  it("opens the settings modal titled with the legend title: period input, Source select (close), one style output", async () => {
    await mountWith([SMA], { [`${SMA_ID}.value`]: [] });

    legend("settings", SMA_ID);

    const dialog = screen.getByRole("dialog", { name: "SimpleMovingAverage (20)" });
    expect(within(dialog).getByLabelText(/period/)).toHaveValue("20");
    const source = within(dialog).getByLabelText<HTMLSelectElement>("Source:");
    expect(source.value).toBe("close");
    expect(Array.from(source.options).map((o) => o.value)).toEqual(["close", "open", "high", "low", "hl2", "hlc3", "ohlc4"]);
    expect(within(dialog).getAllByRole("group")).toHaveLength(1); // one output: "value"
    expect(within(dialog).getByRole("group", { name: "value" })).toBeInTheDocument();
    // The library's default width, which is what the chart draws while the entry stores none.
    expect(within(dialog).getByLabelText("value width")).toHaveValue("3");
    expect(within(dialog).getByLabelText("value line style")).toHaveValue("solid");
  });

  it("offers no Source for an indicator the catalog does not mark source_selectable", async () => {
    const pressure = { name: "CancelPressure", params: {}, category: "custom" };
    await mountWith([pressure], { "CancelPressure.value": [] });

    legend("settings", "CancelPressure");

    const dialog = screen.getByRole("dialog", { name: "CancelPressure" });
    expect(within(dialog).queryByLabelText("Source:")).toBeNull();
    expect(within(dialog).getByRole("group", { name: "value" })).toBeInTheDocument();
  });

  it("Source -> hl2 persists a new instance id (...:hl2) next to SMA(20) on close: two rows", async () => {
    await mountWith([SMA], { [`${SMA_ID}.value`]: [] });
    legend("settings", SMA_ID);
    const dialog = screen.getByRole("dialog", { name: "SimpleMovingAverage (20)" });

    fireEvent.change(within(dialog).getByLabelText("Source:"), { target: { value: "hl2" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Apply" }));
    await act(async () => {});

    expect(saveConfigMock).toHaveBeenLastCalledWith("BTC-USD-PERP.DYDX", [
      expect.objectContaining({ ...SMA, source: "hl2" }),
    ]);
    // The same params on close can now be added again: it is a different instance.
    picker.values = { [`${SMA_ID}:hl2.value`]: [], [`${SMA_ID}.value`]: [] };
    legend("settings", `${SMA_ID}:hl2`);
    expect(screen.getByRole("dialog", { name: "SimpleMovingAverage (20, hl2)" })).toBeInTheDocument();
  });

  it("refuses a duplicate (same name, params and source), shows the message and keeps the modal open", async () => {
    const sma50 = { ...SMA, params: { period: 50 } };
    await mountWith([SMA, sma50], { [`${SMA_ID}.value`]: [] });
    legend("settings", "SimpleMovingAverage_period=50");
    const dialog = screen.getByRole("dialog", { name: "SimpleMovingAverage (50)" });
    saveConfigMock.mockClear();

    fireEvent.change(within(dialog).getByLabelText(/period/), { target: { value: "20" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Apply" }));
    await act(async () => {});

    expect(saveConfigMock).not.toHaveBeenCalled();
    expect(within(dialog).getByRole("alert")).toHaveTextContent("SimpleMovingAverage with those params and source is already added");
  });

  it("keeps the modal open with the save's error when the PUT fails, and closes it once a save lands", async () => {
    await mountWith([SMA], { [`${SMA_ID}.value`]: [] });
    legend("settings", SMA_ID);
    const dialog = screen.getByRole("dialog", { name: "SimpleMovingAverage (20)" });
    saveConfigMock.mockRejectedValueOnce(new Error("invalid source: nope"));

    fireEvent.change(within(dialog).getByLabelText(/period/), { target: { value: "30" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Apply" }));
    await act(async () => {});

    expect(screen.getByRole("dialog", { name: "SimpleMovingAverage (20)" })).toBeInTheDocument();
    expect(within(dialog).getByRole("alert")).toHaveTextContent("invalid source: nope");
    expect(within(dialog).getByLabelText(/period/)).toHaveValue("30"); // the draft survives

    fireEvent.click(within(dialog).getByRole("button", { name: "Apply" }));
    await act(async () => {});
    expect(screen.queryByRole("dialog", { name: "SimpleMovingAverage (20)" })).toBeNull();
  });

  it("keeps the modal on its own entry when a failed remove's rollback shifts the positions", async () => {
    const sma50 = { ...SMA, params: { period: 50 } };
    await mountWith([SMA, sma50], { [`${SMA_ID}.value`]: [], "SimpleMovingAverage_period=50.value": [] });
    let failRemove: (err: Error) => void = () => {};
    saveConfigMock.mockImplementationOnce(() => new Promise((_, reject) => (failRemove = reject)));

    legend("remove", SMA_ID); // optimistic: SMA(50) moves to position 0
    legend("settings", "SimpleMovingAverage_period=50");
    await act(async () => failRemove(new Error("disk full"))); // SMA(20) is put back at position 0

    const dialog = screen.getByRole("dialog", { name: "SimpleMovingAverage (50)" });
    expect(within(dialog).getByLabelText(/period/)).toHaveValue("50");
    fireEvent.change(within(dialog).getByLabelText(/period/), { target: { value: "60" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Apply" }));
    await act(async () => {});
    expect(saveConfigMock).toHaveBeenLastCalledWith("BTC-USD-PERP.DYDX", [
      SMA,
      expect.objectContaining({ ...sma50, params: { period: 60 } }),
    ]);
  });

  it("disables Remove while an Apply is in flight", async () => {
    await mountWith([SMA], { [`${SMA_ID}.value`]: [] });
    legend("settings", SMA_ID);
    const dialog = screen.getByRole("dialog", { name: "SimpleMovingAverage (20)" });
    let landSave: (value: unknown) => void = () => {};
    saveConfigMock.mockImplementationOnce(() => new Promise((resolve) => (landSave = resolve)));

    fireEvent.change(within(dialog).getByLabelText(/period/), { target: { value: "30" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Apply" }));
    expect(within(dialog).getByRole("button", { name: "Remove" })).toBeDisabled();

    await act(async () => landSave({ ok: true }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("refuses a duplicate whose params differ only in key order: they would share one id", async () => {
    const first = { ...SMA, params: { k: 2, period: 20 } };
    const second = { ...SMA, params: { period: 30, k: 2 } };
    await mountWith([first, second], { "SimpleMovingAverage_k=2,period=30.value": [] });
    legend("settings", "SimpleMovingAverage_k=2,period=30");
    const dialog = screen.getByRole("dialog", { name: "SimpleMovingAverage (30, 2)" });
    saveConfigMock.mockClear();

    fireEvent.change(within(dialog).getByLabelText(/period/), { target: { value: "20" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Apply" }));
    await act(async () => {});

    expect(saveConfigMock).not.toHaveBeenCalled();
    expect(within(dialog).getByRole("alert")).toHaveTextContent("already added");
  });

  it("seeds a histogram's up and down colours with the palette colour it is drawn in", async () => {
    const pressure = { name: "CancelPressure", params: { window: 200 }, category: "custom" };
    const id = "CancelPressure_window=200";
    await mountWith([pressure], { [`${id}.value`]: [] });

    legend("settings", id);

    const dialog = screen.getByRole("dialog", { name: "CancelPressure (200)" });
    const drawn = (paneOf(`${id}.value`).color ?? "").toLowerCase();
    expect(within(dialog).getByLabelText<HTMLInputElement>("value up colour").value).toBe(drawn);
    expect(within(dialog).getByLabelText<HTMLInputElement>("value down colour").value).toBe(drawn);
  });

  it("offers settings and remove on a failed instance's alert: it draws no series, so it has no legend row", async () => {
    await mountWith([SMA], {});
    act(() => picker.onErrors?.({ [SMA_ID]: "period must be positive" }));
    saveConfigMock.mockClear();

    fireEvent.click(screen.getByRole("button", { name: `Settings for ${SMA_ID}` }));
    expect(screen.getByRole("dialog", { name: "SimpleMovingAverage (20)" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    fireEvent.click(screen.getByRole("button", { name: `Remove ${SMA_ID}` }));
    await act(async () => {});
    expect(saveConfigMock).toHaveBeenLastCalledWith("BTC-USD-PERP.DYDX", []);
  });

  it("disables Apply and flags the field for invalid parameter text", async () => {
    await mountWith([SMA], { [`${SMA_ID}.value`]: [] });
    legend("settings", SMA_ID);
    const dialog = screen.getByRole("dialog", { name: "SimpleMovingAverage (20)" });

    fireEvent.change(within(dialog).getByLabelText(/period/), { target: { value: "abc" } });

    expect(within(dialog).getByRole("button", { name: "Apply" })).toBeDisabled();
    expect(within(dialog).getByLabelText(/period/)).toHaveAttribute("aria-invalid", "true");
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Invalid value for period");
  });

  it("a style-only Apply persists style, changes no value-request field, and restyles the spec", async () => {
    await mountWith([SMA], { [`${SMA_ID}.value`]: [] });
    legend("settings", SMA_ID);
    const dialog = screen.getByRole("dialog", { name: "SimpleMovingAverage (20)" });

    fireEvent.change(within(dialog).getByLabelText("value width"), { target: { value: "3" } });
    fireEvent.change(within(dialog).getByLabelText("value line style"), { target: { value: "dashed" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Apply" }));
    await act(async () => {});

    expect(saveConfigMock).toHaveBeenLastCalledWith("BTC-USD-PERP.DYDX", [
      expect.objectContaining({ ...SMA, source: "close", style: { value: { line_width: 3, line_style: "dashed" } } }),
    ]);
    expect(paneOf(`${SMA_ID}.value`)).toMatchObject({ lineWidth: 3, lineStyle: "dashed" });
    expect(screen.queryByRole("dialog", { name: "SimpleMovingAverage (20)" })).toBeNull();
  });

  it("a stored colour wins over the palette slot, and a histogram output gets up/down colours", async () => {
    const pattern = { name: "CandlePattern", params: { pattern: "DOJI", trend_bars: 3 }, category: "native", style: { value: { up_color: "#00ff00", down_color: "#ff0000" } } };
    const sma = { ...SMA, style: { value: { color: "#123456" } } };
    await mountWith([sma, pattern], { [`${SMA_ID}.value`]: [], "CandlePattern_pattern=DOJI,trend_bars=3.value": [] });

    expect(paneOf(`${SMA_ID}.value`).color).toBe("#123456");
    expect(paneOf("CandlePattern_pattern=DOJI,trend_bars=3.value")).toMatchObject({ upColor: "#00ff00", downColor: "#ff0000" });
    legend("settings", "CandlePattern_pattern=DOJI,trend_bars=3");
    const dialog = screen.getByRole("dialog", { name: /CandlePattern/ });
    expect(within(dialog).getByLabelText("value up colour")).toHaveValue("#00ff00");
    expect(within(dialog).getByLabelText("value down colour")).toHaveValue("#ff0000");
  });

  it("Cancel, Esc and a backdrop click close the modal with no save", async () => {
    await mountWith([SMA], { [`${SMA_ID}.value`]: [] });
    const open = () => {
      legend("settings", SMA_ID);
      return screen.getByRole("dialog", { name: "SimpleMovingAverage (20)" });
    };
    saveConfigMock.mockClear();

    fireEvent.click(within(open()).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog", { name: "SimpleMovingAverage (20)" })).toBeNull();

    fireEvent(open(), new Event("close")); // Esc: the native dialog's close event
    expect(screen.queryByRole("dialog", { name: "SimpleMovingAverage (20)" })).toBeNull();

    const backdrop = open();
    fireEvent.mouseDown(backdrop); // press and release both on the <dialog> itself: the backdrop
    fireEvent.click(backdrop);
    expect(screen.queryByRole("dialog", { name: "SimpleMovingAverage (20)" })).toBeNull();

    expect(saveConfigMock).not.toHaveBeenCalled();
  });

  it("Remove in the modal removes through the same persist path", async () => {
    await mountWith([SMA, RSI], { [`${SMA_ID}.value`]: [] });
    legend("settings", SMA_ID);

    fireEvent.click(within(screen.getByRole("dialog", { name: "SimpleMovingAverage (20)" })).getByRole("button", { name: "Remove" }));
    await act(async () => {});

    expect(saveConfigMock).toHaveBeenLastCalledWith("BTC-USD-PERP.DYDX", [RSI]);
    expect(screen.queryByRole("dialog", { name: "SimpleMovingAverage (20)" })).toBeNull();
  });

  it("loads a pre-story entry (no source/hidden/style) with the defaults", async () => {
    await mountWith([SMA], { [`${SMA_ID}.value`]: [] });

    expect(paneOf(`${SMA_ID}.value`)).toMatchObject({ hidden: false, lineWidth: undefined, lineStyle: undefined, group: SMA_ID });
  });
});
