import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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
    SimpleMovingAverage: { params: {}, panel: "overlay", category: "native" },
    RelativeStrengthIndex: { params: {}, panel: "oscillator", category: "native" },
    CancelPressure: { params: {}, panel: "histogram", category: "custom" },
  }),
}));

const hooks = vi.hoisted(() => ({
  candlesBar: [] as number[],
  liveBar: [] as number[],
  pickerBar: [] as number[],
}));
const EMPTY_CANDLES = { candles: [], volume: [] };
vi.mock("../hooks/useCandles", () => ({
  BAR_SECONDS: 60,
  useCandles: (_iid: string, _chart: unknown, _enabled: boolean, bar: number) => {
    hooks.candlesBar.push(bar);
    return EMPTY_CANDLES;
  },
}));

vi.mock("../hooks/useSnapshotSeries", () => ({
  useSnapshotSeries: () => ({ bid: [], ask: [], mid: [], micro: [], price: [] }),
}));

vi.mock("../hooks/useLiveCandle", () => ({
  useLiveCandle: (_iid: string, bar: number) => {
    hooks.liveBar.push(bar);
    return null;
  },
}));

// Module-level constant: ChartPage's adjust-state-during-render pattern bails out on
// identical pickerValues identity, which mirrors the real hook's contract of returning
// its state object (stable until a real data change) -- a fresh {} per render would
// loop the page into React's too-many-re-renders guard.
// The first render sees the real hook's initial empty state; later renders see `values`,
// so ChartPage's identity-change detection fires exactly like a real data arrival.
const picker = vi.hoisted(() => ({ values: {} as Record<string, never[]>, calls: 0 }));
vi.mock("../hooks/usePickerIndicatorValues", () => {
  const initial = {};
  return { usePickerIndicatorValues: (_i: string, _c: unknown, _e: unknown, bar: number) => {
    hooks.pickerBar.push(bar);
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
  panes?: { id: string; kind: string; placement?: string; group?: string; groupLabel?: string; outputLabel?: string }[];
  priceLines?: PriceLineSpec[];
  onPriceClick?: (price: number) => void;
  onPriceLineDrag?: (id: string, price: number) => void;
}

const lastChartProps: { current: ChartStubProps | null } = { current: null };

vi.mock("../components/chart/LightweightChart", () => ({
  default: (props: ChartStubProps) => {
    lastChartProps.current = props;
    return <div data-testid="chart-stub" />;
  },
}));

// Imported after the mocks above so ChartPage picks up the mocked client/hooks/chart.
const { default: ChartPage } = await import("./ChartPage");
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
  localStorage.clear();
});

afterEach(() => {
  cleanup();
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

    expect(lastChartProps.current!.priceLines).toEqual([{ id: "hline-1", price: 61000.5, color: "#55ffff" }]);
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

    expect(lastChartProps.current!.priceLines).toEqual([{ id: "hline-1", price: 61500.25, color: "#55ffff" }]);
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
      { id: "hline-1", price: 61000.5, color: "#55ffff" },
      { id: "hline-2", price: 63000, color: "#55ffff" },
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
    // legend info: grouped by indicator, titled with its name, tooltip = output attr
    expect(byId["RelativeStrengthIndex_period=14.value"]).toMatchObject({
      group: "RelativeStrengthIndex",
      groupLabel: "RelativeStrengthIndex",
      outputLabel: "value",
    });
    expect(lastChartProps.current?.panes?.find((p) => p.id === "volume")).toMatchObject({ groupLabel: "Volume" });
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
  it("offers 1s/1m/5m/15m/1H/4H/1D/1W, defaults to 1m, and feeds the chosen bar size to every data hook", () => {
    render(page());

    const labels = ["1s", "1m", "5m", "15m", "1H", "4H", "1D", "1W"];
    for (const l of labels) expect(screen.getByRole("button", { name: `Timeframe ${l}` })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Timeframe 1m" })).toHaveAttribute("aria-pressed", "true");
    expect(last(hooks.candlesBar)).toBe(60);

    fireEvent.click(screen.getByRole("button", { name: "Timeframe 1s" }));
    expect(last(hooks.candlesBar)).toBe(1);

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

  it("orders the top toolbar [symbol+timeframe] [chart type] [indicators+fit+latest], with no theme toggle", () => {
    render(page());

    const names = within(screen.getByRole("toolbar", { name: "Chart controls" }))
      .getAllByRole("button")
      .map((b) => b.getAttribute("aria-label") ?? b.textContent);
    expect(names).toEqual([
      ...["1s", "1m", "5m", "15m", "1H", "4H", "1D", "1W"].map((l) => `Timeframe ${l}`),
      "Candles",
      "Lines",
      "Indicators",
      "Alert",
      "Fit",
      "Latest",
    ]);
    expect(screen.getByRole("link", { name: /Rankings/ })).toHaveAttribute("href", "/");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("BTC-USD-PERP.DYDX");
  });

  it("orders the left toolbar cursor, crosshair | horizontal line", () => {
    render(page());

    const names = within(screen.getByRole("toolbar", { name: "Chart tools" }))
      .getAllByRole("button")
      .map((b) => b.getAttribute("aria-label"));
    expect(names).toEqual(["Cursor tool", "Crosshair toggle", "Horizontal line tool"]);
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

    expect(within(dialog).getAllByRole("listitem")).toHaveLength(3);
    fireEvent.change(within(dialog).getByLabelText("Search indicators"), { target: { value: "rela" } });
    expect(within(dialog).getAllByRole("listitem")).toHaveLength(1);
    expect(within(dialog).getByText("RelativeStrengthIndex")).toBeInTheDocument();
  });

  it("filters by the Overlays / Oscillators categories (histogram counts as oscillator)", async () => {
    const dialog = await openDialog();

    fireEvent.click(within(dialog).getByRole("button", { name: "Overlays" }));
    expect(within(dialog).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["SimpleMovingAverageoverlay"]);

    fireEvent.click(within(dialog).getByRole("button", { name: "Oscillators" }));
    expect(within(dialog).getAllByRole("listitem")).toHaveLength(2);
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
