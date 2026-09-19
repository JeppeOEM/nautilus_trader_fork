import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PriceLineSpec } from "../components/chart/LightweightChart";

// The page test isolates ChartPage's OWN tool state machine: the api client would hit
// real fetches under jsdom, and the data hooks' fetch/websocket machinery plus
// LightweightChart's chart internals are each covered by their own test files --
// here they are all shallow-mocked so the only real code under test is ChartPage.tsx.
vi.mock("../api/client", () => ({
  fetchCoinIndicatorConfig: vi.fn().mockResolvedValue([]),
  saveCoinIndicatorConfig: vi.fn().mockResolvedValue({ ok: true }),
  // IndicatorPicker (rendered by ChartPage) fetches the catalog on mount.
  fetchIndicatorCatalog: vi.fn().mockResolvedValue({}),
}));

// Stable references (a fresh array per render would churn useReplay's memo) that the
// replay tests swap in.
const mocks = vi.hoisted(() => ({ candles: [] as unknown[], volume: [] as unknown[], liveBar: null as unknown }));

vi.mock("../hooks/useCandles", () => ({
  BAR_SECONDS: 60,
  useCandles: () => ({ candles: mocks.candles, volume: mocks.volume }),
}));

vi.mock("../hooks/useSnapshotSeries", () => ({
  useSnapshotSeries: () => ({ bid: [], ask: [], mid: [], micro: [], price: [] }),
}));

vi.mock("../hooks/useIndicatorSeries", () => ({
  useIndicatorSeries: () => ({ ofi: [], obi: [], microprice: [], spread: [] }),
}));

vi.mock("../hooks/useLiveCandle", () => ({
  useLiveCandle: () => mocks.liveBar,
}));

// Module-level constant: ChartPage's adjust-state-during-render pattern bails out on
// identical pickerValues identity, which mirrors the real hook's contract of returning
// its state object (stable until a real data change) -- a fresh {} per render would
// loop the page into React's too-many-re-renders guard.
const pickerValues = {};
vi.mock("../hooks/usePickerIndicatorValues", () => ({
  usePickerIndicatorValues: () => pickerValues,
}));

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return { ...actual, useParams: () => ({ iid: "BTC-USD-PERP.DYDX" }) };
});

// The subset of LightweightChart's props this page test asserts on. The stub records
// the latest props object ChartPage handed it -- every claim below is about what
// ChartPage FEEDS the chart component, never about LightweightChart's internals.
interface ChartStubProps {
  priceLines?: PriceLineSpec[];
  onPriceClick?: (price: number) => void;
  onPriceLineDrag?: (id: string, price: number) => void;
  drawings?: { id: string; kind: string; anchors: unknown[] }[];
  onPointClick?: (point: { time: number; price: number }) => void;
  measureActive?: boolean;
  onMeasureEnd?: () => void;
  data?: { time: number }[];
  panes?: { id: string; data: { time: number }[] }[];
  liveBar?: unknown;
  markerTime?: number | null;
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

beforeEach(() => {
  lastChartProps.current = null;
  mocks.candles = [];
  mocks.volume = [];
  mocks.liveBar = null;
});

afterEach(() => {
  cleanup();
});

describe("ChartPage drawing tools (Story 18.1)", () => {
  it("has cursor active by default and arms the hline tool on click (AC #1)", () => {
    render(<ChartPage />);

    expect(screen.getByTestId("chart-stub")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cursor tool" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Horizontal line tool" })).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(screen.getByRole("button", { name: "Horizontal line tool" }));

    expect(screen.getByRole("button", { name: "Horizontal line tool" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Cursor tool" })).toHaveAttribute("aria-pressed", "false");
  });

  it("places exactly one line from an armed hline's chart click, then disarms (AC #2)", () => {
    render(<ChartPage />);
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
    render(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Horizontal line tool" }));

    fireEvent.keyDown(window, { key: "Escape" });

    expect(screen.getByRole("button", { name: "Cursor tool" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Horizontal line tool" })).toHaveAttribute("aria-pressed", "false");
  });

  it("disables the hline button in Lines mode and re-enables it back in Candles", () => {
    render(<ChartPage />);

    fireEvent.click(screen.getByRole("button", { name: "Lines" }));
    expect(screen.getByRole("button", { name: "Horizontal line tool" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Candles" }));
    expect(screen.getByRole("button", { name: "Horizontal line tool" })).toBeEnabled();
  });

  it("disarms an armed hline when the chart switches to Lines mode", () => {
    render(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Horizontal line tool" }));

    fireEvent.click(screen.getByRole("button", { name: "Lines" }));

    expect(screen.getByRole("button", { name: "Cursor tool" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Horizontal line tool" })).toHaveAttribute("aria-pressed", "false");
  });

  it("updates the dragged line's price in the priceLines prop (AC #3)", () => {
    render(<ChartPage />);
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
    render(<ChartPage />);
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
        color: "#55ffff",
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
