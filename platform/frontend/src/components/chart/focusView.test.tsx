import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The chart page's focus view (chart UX rework, 2026-10-08; it replaced Story 33.12's browser
// fullscreen), end to end at page level: the site's chrome hidden by a class on <body>, the chart told
// to fit the window, left by its button, Shift+F or Esc. The data hooks and the chart are shallow-mocked
// (ChartPage.test.tsx covers them).

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

const chartProps = vi.hoisted(() => ({ fit: [] as boolean[] }));
vi.mock("./LightweightChart", () => ({
  default: (props: { fitToWindow?: boolean }) => {
    chartProps.fit.push(props.fitToWindow === true);
    return <div data-testid="chart-stub" />;
  },
}));

const { default: ChartPage } = await import("../../pages/ChartPage");
const { SAVE_DEBOUNCE_MS } = await import("../../hooks/useChartDrawings");
const { FOCUS_BODY_CLASS } = await import("../../hooks/useFocusView");

beforeEach(() => {
  chartProps.fit = [];
  layoutApi.save.mockReset().mockResolvedValue(undefined);
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

async function renderPage(): Promise<void> {
  render(
    <MemoryRouter>
      <ChartPage />
    </MemoryRouter>,
  );
  await act(async () => {});
}

const focusButton = () => screen.getByRole("button", { name: /^(Focus|Exit focus)$/ });
const focused = () => document.body.classList.contains(FOCUS_BODY_CLASS);

describe("the chart's focus view", () => {
  it("hides the site's chrome, fits the chart to the window and offers Exit focus, without the browser fullscreen", async () => {
    const requestFullscreen = vi.fn();
    Object.defineProperty(Element.prototype, "requestFullscreen", { configurable: true, value: requestFullscreen });
    await renderPage();
    expect(chartProps.fit.at(-1)).toBe(false);

    await act(async () => fireEvent.click(focusButton()));

    expect(focused()).toBe(true);
    expect(chartProps.fit.at(-1)).toBe(true);
    expect(focusButton()).toHaveTextContent("Exit focus");
    expect(focusButton()).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("link", { name: /Rankings/ })).toBeNull();
    expect(requestFullscreen).not.toHaveBeenCalled();
    delete (Element.prototype as Partial<Element>).requestFullscreen;

    await act(async () => fireEvent.click(focusButton()));
    expect(focused()).toBe(false);
    expect(chartProps.fit.at(-1)).toBe(false);
  });

  it("toggles with Shift+F, a held key's auto-repeat as one press", async () => {
    await renderPage();
    await act(async () => fireEvent.keyDown(window, { key: "F", code: "KeyF", shiftKey: true }));
    expect(focused()).toBe(true);
    for (let i = 0; i < 3; i++) {
      await act(async () => fireEvent.keyDown(window, { key: "F", code: "KeyF", shiftKey: true, repeat: true }));
    }
    expect(focused()).toBe(true);
    await act(async () => fireEvent.keyDown(window, { key: "F", code: "KeyF", shiftKey: true }));
    expect(focused()).toBe(false);
  });

  it("leaves on Esc only when Esc has nothing nearer to undo (an armed tool, an open menu)", async () => {
    await renderPage();
    await act(async () => fireEvent.click(focusButton()));

    fireEvent.click(screen.getByRole("button", { name: "Trendline tool" }));
    await act(async () => fireEvent.keyDown(window, { key: "Escape" }));
    expect(focused()).toBe(true); // that Esc disarmed the tool
    expect(screen.getByRole("button", { name: "Cursor tool" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: "Chart settings" }));
    await act(async () => fireEvent.keyDown(window, { key: "Escape" }));
    expect(focused()).toBe(true); // that Esc closed the menu
    expect(screen.queryByRole("group", { name: "Chart settings" })).toBeNull();

    await act(async () => fireEvent.keyDown(window, { key: "Escape" }));
    expect(focused()).toBe(false);
  });

  it("keeps the focus view across a typed timeframe change", async () => {
    await renderPage();
    await act(async () => fireEvent.click(focusButton()));

    for (const [key, code] of [["5", "Digit5"], ["Enter", "Enter"]]) fireEvent.keyDown(window, { key, code });
    await act(async () => {});

    expect(screen.getByRole("button", { name: "Timeframe 5m" })).toHaveAttribute("aria-pressed", "true");
    expect(focused()).toBe(true);
    expect(chartProps.fit.at(-1)).toBe(true);
  });

  it("ends with the page: leaving the chart never leaves the site's chrome hidden", async () => {
    await renderPage();
    await act(async () => fireEvent.click(focusButton()));
    cleanup();
    expect(focused()).toBe(false);
  });

  it("never saves the focus view: not in the layout, not in the browser", async () => {
    vi.useFakeTimers();
    await renderPage();
    await act(async () => fireEvent.click(focusButton()));
    fireEvent.click(screen.getByRole("button", { name: "Chart settings" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Session breaks" })); // a real edit, so a save is sent
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SAVE_DEBOUNCE_MS + 1);
    });

    expect(layoutApi.save).toHaveBeenCalled();
    for (const [, layout] of layoutApi.save.mock.calls) {
      expect(JSON.stringify(layout)).not.toMatch(/focus|fullscreen|fit/i);
    }
    expect(localStorage.length).toBe(0);
  });
});
