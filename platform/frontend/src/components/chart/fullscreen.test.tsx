import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Story 33.12: the chart page's fullscreen, end to end at page level. The browser Fullscreen API is
// faked on `Element.prototype` and `document` (jsdom has none): a request makes the element
// `document.fullscreenElement` and fires `fullscreenchange`, as a browser does. The data hooks and the
// chart are shallow-mocked (ChartPage.test.tsx covers them); the chart stub renders its legend inside
// itself, as LightweightChart's legends live inside its container.

const IID = "BTCUSDT-LINEAR.BYBIT";
const layoutApi = vi.hoisted(() => ({ save: vi.fn() }));
vi.mock("../../api/client", async () => ({
  HttpError: class extends Error {
    status = 0;
  },
  fetchCoinLayout: async () => ({ layout: (await import("../../lib/chartLayout")).BUILT_IN_LAYOUT, seeded: false }),
  saveCoinLayout: (...args: unknown[]) => layoutApi.save(...args),
  saveLayoutAsDefault: () => Promise.resolve(undefined),
  resetLayoutToDefault: () => Promise.resolve(undefined),
  fetchCoinDrawings: () => Promise.resolve([]),
  saveCoinDrawings: () => Promise.resolve(undefined),
  fetchCoinIndicatorConfig: () => Promise.resolve([]),
  saveCoinIndicatorConfig: () => Promise.resolve({ ok: true }),
  fetchIndicatorCatalog: () => Promise.resolve({}),
  fetchMarkets: () => Promise.resolve({ items: [], stale_venues: [] }),
  fetchWatchlist: () => Promise.resolve({ instruments: [] }),
  saveWatchlist: (instruments: string[]) => Promise.resolve({ instruments }),
  // The Derivatives group is off here: listed so a stray call fails loudly.
  ...Object.fromEntries(
    ["fetchFunding", "fetchOpenInterest", "fetchMarkIndex", "fetchLiquidations", "fetchLiquidationBars", "fetchFootprint"].map((name) => [
      name,
      () => Promise.reject(new Error(`${name} is not stubbed in this test`)),
    ]),
  ),
}));

const candleHook = vi.hoisted(() => ({
  candles: [{ time: 60, open: 1, high: 2, low: 1, close: 2 }],
  volume: [],
  venueMarket: null,
  precision: { price: 2, size: 3 },
  loadFailed: false,
  openGapTo: () => {},
}));
vi.mock("../../hooks/useCandles", () => ({ useCandles: () => candleHook }));
const noLines = vi.hoisted(() => ({ bid: [], ask: [], mid: [], micro: [], price: [] }));
vi.mock("../../hooks/useSnapshotSeries", () => ({ useSnapshotSeries: () => noLines }));
vi.mock("../../hooks/useLiveCandle", () => ({ useLiveCandle: () => null }));
const noValues = vi.hoisted(() => ({}));
vi.mock("../../hooks/usePickerIndicatorValues", () => ({ usePickerIndicatorValues: () => noValues }));
const noFootprint = vi.hoisted(() => ({ items: [], precision: null, error: null }));
vi.mock("../../hooks/useFootprint", () => ({ useFootprint: () => noFootprint }));
const noSession = vi.hoisted(() => ({ candles: [], volume: [], completeFrom: null, loading: false }));
vi.mock("../../hooks/useSessionCandles", () => ({ useSessionCandles: () => noSession }));
const noVwap = vi.hoisted(() => ({ values: {}, errors: {} }));
vi.mock("../../hooks/useStoredAnchoredVwap", () => ({ useStoredAnchoredVwap: () => noVwap }));
// Stable objects, as the real hooks return: a fresh `{}` per render re-fires the page's effects forever.
const noDerivs = vi.hoisted(() => ({}));
vi.mock("../../hooks/useLiveDerivs", () => ({ useLiveDerivs: () => noDerivs }));
vi.mock("../../hooks/useLiveLiquidations", () => ({ useLiveLiquidations: () => null }));
const noRanks = vi.hoisted(() => ({ latest: null, connected: false }));
vi.mock("../../hooks/useLiveChannel", () => ({ useLiveChannel: () => noRanks }));
vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return { ...actual, useParams: () => ({ iid: IID }) };
});

const chartProps = vi.hoisted(() => ({ fullscreen: [] as boolean[] }));
vi.mock("./LightweightChart", () => ({
  default: (props: { fullscreen?: boolean }) => {
    chartProps.fullscreen.push(props.fullscreen === true);
    return (
      <div data-testid="chart-stub">
        <div className="chart-legend" data-testid="legend" />
      </div>
    );
  },
}));

const { default: ChartPage } = await import("../../pages/ChartPage");
const { SAVE_DEBOUNCE_MS } = await import("../../hooks/useChartDrawings");

// The faked Fullscreen API: `refuse` makes the next request reject, as a browser refusing one does.
let fullscreenElement: Element | null = null;
let refuse = false;
// A method on the prototype: the element it was called on is the call's `this`, which the mock
// records in `mock.contexts` (read there rather than aliasing `this`, oxlint's no-this-alias).
const requestFullscreen = vi.fn((): Promise<void> => {
  if (refuse) return Promise.reject(new TypeError("Permissions check failed"));
  fullscreenElement = requestFullscreen.mock.contexts.at(-1) as Element;
  document.dispatchEvent(new Event("fullscreenchange"));
  return Promise.resolve();
});
const exitFullscreen = vi.fn((): Promise<void> => {
  fullscreenElement = null;
  document.dispatchEvent(new Event("fullscreenchange"));
  return Promise.resolve();
});

beforeEach(() => {
  fullscreenElement = null;
  refuse = false;
  chartProps.fullscreen = [];
  layoutApi.save.mockReset().mockResolvedValue(undefined);
  requestFullscreen.mockClear();
  exitFullscreen.mockClear();
  Object.defineProperty(document, "fullscreenElement", { configurable: true, get: () => fullscreenElement });
  Object.defineProperty(document, "exitFullscreen", { configurable: true, value: exitFullscreen });
  Object.defineProperty(Element.prototype, "requestFullscreen", { configurable: true, value: requestFullscreen });
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  delete (Element.prototype as Partial<Element>).requestFullscreen;
});

async function renderPage(): Promise<void> {
  render(
    <MemoryRouter>
      <ChartPage />
    </MemoryRouter>,
  );
  await act(async () => {});
}

const button = () => screen.getByRole("button", { name: "Fullscreen" });

describe("the chart's fullscreen (Story 33.12)", () => {
  it("puts the stage holding the top bar, the tool rail and the chart with its legends into fullscreen, the rail outside", async () => {
    await renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Watchlist" }));
    await act(async () => fireEvent.click(button()));

    const stage = document.fullscreenElement as HTMLElement;
    expect(stage).toHaveClass("chart-stage");
    for (const pane of [
      screen.getByRole("toolbar", { name: "Chart controls" }),
      screen.getByRole("toolbar", { name: "Chart tools" }),
      screen.getByTestId("chart-stub"),
      screen.getByTestId("legend"),
    ]) {
      expect(stage.contains(pane)).toBe(true);
    }
    expect(stage.contains(screen.getByRole("complementary", { name: "Watchlist" }))).toBe(false);
    expect(button()).toHaveAttribute("aria-pressed", "true");
  });

  it("holds the replay controls inside the stage too", async () => {
    await renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Replay" }));
    await act(async () => fireEvent.click(button()));

    expect((document.fullscreenElement as HTMLElement).contains(screen.getByRole("group", { name: "Replay controls" }))).toBe(true);
  });

  it("flips the chart's fullscreen prop on entry and on exit (button, Shift+F, the browser's Esc)", async () => {
    await renderPage();
    expect(chartProps.fullscreen.at(-1)).toBe(false);

    await act(async () => fireEvent.click(button()));
    expect(chartProps.fullscreen.at(-1)).toBe(true);
    await act(async () => fireEvent.keyDown(window, { key: "F", code: "KeyF", shiftKey: true }));
    expect(exitFullscreen).toHaveBeenCalledTimes(1);
    expect(chartProps.fullscreen.at(-1)).toBe(false);

    await act(async () => fireEvent.keyDown(window, { key: "F", code: "KeyF", shiftKey: true }));
    expect(chartProps.fullscreen.at(-1)).toBe(true);
    // The browser's own Esc leaves without a call from the page; its `fullscreenchange` is the truth.
    act(() => {
      fullscreenElement = null;
      document.dispatchEvent(new Event("fullscreenchange"));
    });
    expect(chartProps.fullscreen.at(-1)).toBe(false);
  });

  it("takes a held Shift+F's auto-repeat as one press, never flipping fullscreen back and forth", async () => {
    await renderPage();
    await act(async () => fireEvent.keyDown(window, { key: "F", code: "KeyF", shiftKey: true }));
    expect(chartProps.fullscreen.at(-1)).toBe(true);
    for (let i = 0; i < 3; i++) {
      await act(async () => fireEvent.keyDown(window, { key: "F", code: "KeyF", shiftKey: true, repeat: true }));
    }
    expect(exitFullscreen).not.toHaveBeenCalled();
    expect(chartProps.fullscreen.at(-1)).toBe(true);
  });

  it("keeps fullscreen across a typed timeframe change (the stage is above the chart's remount)", async () => {
    await renderPage();
    await act(async () => fireEvent.click(button()));
    const stage = document.fullscreenElement;

    for (const [key, code] of [["5", "Digit5"], ["Enter", "Enter"]]) fireEvent.keyDown(window, { key, code });
    await act(async () => {});

    expect(screen.getByRole("button", { name: "Timeframe 5m" })).toHaveAttribute("aria-pressed", "true");
    expect(stage?.isConnected).toBe(true);
    expect(document.fullscreenElement).toBe(stage);
    expect(chartProps.fullscreen.at(-1)).toBe(true);
  });

  it("says inline when the browser refuses, and when it has no Fullscreen API", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    refuse = true;
    await renderPage();
    await act(async () => fireEvent.click(button()));
    const bar = screen.getByRole("toolbar", { name: "Chart controls" });
    expect(within(bar).getByRole("alert")).toHaveTextContent("Fullscreen was refused by the browser");
    expect(chartProps.fullscreen.at(-1)).toBe(false);

    delete (Element.prototype as Partial<Element>).requestFullscreen;
    await act(async () => fireEvent.keyDown(window, { key: "F", code: "KeyF", shiftKey: true }));
    expect(within(bar).getByRole("alert")).toHaveTextContent("Fullscreen was refused by the browser");
    warn.mockRestore();
  });

  it("never saves fullscreen: not in the layout, not in the browser", async () => {
    vi.useFakeTimers();
    await renderPage();
    await act(async () => fireEvent.click(button()));
    fireEvent.click(screen.getByRole("button", { name: "Session breaks" })); // a real edit, so a save is sent
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SAVE_DEBOUNCE_MS + 1);
    });

    expect(layoutApi.save).toHaveBeenCalled();
    for (const [, layout] of layoutApi.save.mock.calls) {
      expect(JSON.stringify(layout)).not.toMatch(/fullscreen/i);
    }
    expect(localStorage.length).toBe(0);
  });
});
