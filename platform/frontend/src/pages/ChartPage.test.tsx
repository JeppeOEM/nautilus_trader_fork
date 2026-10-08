import { act, cleanup, fireEvent, render as rtlRender, screen, within } from "@testing-library/react";
import { CHART_TOKENS } from "../components/chart/chartTheme";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PriceLineSpec } from "../components/chart/LightweightChart";

// The page test isolates ChartPage's OWN tool state machine: the api client would hit
// real fetches under jsdom, and the data hooks' fetch/websocket machinery plus
// LightweightChart's chart internals are each covered by their own test files --
// here they are all shallow-mocked so the only real code under test is ChartPage.tsx.
const saveConfigMock = vi.hoisted(() => vi.fn().mockResolvedValue({ ok: true }));
// Story 32.5: the drawings resource. `drawings.server` is what GET answers, `saveDrawingsMock` records
// every PUT (the page persists on every change, debounced).
const drawingsApi = vi.hoisted(() => ({
  server: [] as unknown[],
  save: vi.fn().mockResolvedValue(undefined),
}));
// Story 32.6: the layout resource. `server` is what GET answers per instrument (absent = the built-in
// layout); `get` answers through a thenable that settles synchronously, so a page renders ready
// inside `render`'s own act exactly as it did before the layout had to load first. The tests that
// look at the loading state hand `get` a real promise instead.
const layoutApi = vi.hoisted(() => ({
  server: {} as Record<string, unknown>,
  get: vi.fn(),
  save: vi.fn(),
  saveDefault: vi.fn(),
  reset: vi.fn(),
}));
const route = vi.hoisted(() => ({ iid: "BTC-USD-PERP.DYDX" }));
// Story 33.5: the derivatives routes, one page per route (`items`; `has_more` false), every call
// recorded; and the two live channels, whose latest ticks and handlers a test drives.
const derivApi = vi.hoisted(() => ({
  pages: {} as Record<string, unknown[]>,
  calls: [] as { route: string; args: unknown[] }[],
}));
const liveDerivs = vi.hoisted(() => ({
  latest: {} as Record<string, unknown>,
  subscribed: [] as string[],
  liquidationsSubscribed: [] as string[],
  onLiquidation: undefined as ((row: unknown) => void) | undefined,
  onTick: undefined as ((tick: unknown) => void) | undefined,
}));
// Story 33.9: `GET /api/markets` for the Compare field's suggestions.
const marketsApi = vi.hoisted(() => ({ get: vi.fn() }));
// Story 33.12: the chart watchlist resource (`server` is what GET answers; every PUT recorded) and the
// `rankings:live` message the rail prices its rows from.
const watchlistApi = vi.hoisted(() => ({ server: [] as string[], save: vi.fn() }));
const liveRanks = vi.hoisted(() => ({ latest: null as unknown }));
vi.mock("../hooks/useLiveChannel", () => ({
  useLiveChannel: () => ({ latest: liveRanks.latest, connected: liveRanks.latest !== null }),
}));
vi.mock("../api/client", () => ({
  fetchMarkets: (...args: unknown[]) => marketsApi.get(...args),
  fetchWatchlist: () => Promise.resolve({ instruments: watchlistApi.server }),
  saveWatchlist: (...args: unknown[]) => watchlistApi.save(...args),
  fetchCoinLayout: (...args: unknown[]) => layoutApi.get(...args),
  saveCoinLayout: (...args: unknown[]) => layoutApi.save(...args),
  saveLayoutAsDefault: (...args: unknown[]) => layoutApi.saveDefault(...args),
  resetLayoutToDefault: (...args: unknown[]) => layoutApi.reset(...args),
  fetchCoinDrawings: vi.fn(() => Promise.resolve(drawingsApi.server)),
  saveCoinDrawings: (...args: unknown[]) => drawingsApi.save(...args),
  fetchCoinIndicatorConfig: vi.fn().mockResolvedValue([]),
  saveCoinIndicatorConfig: saveConfigMock,
  // Story 32.8: never reached (useFootprint is mocked below); listed so a stray call fails loudly.
  fetchFootprint: vi.fn(() => Promise.reject(new Error("fetchFootprint is not stubbed in this test"))),
  ...Object.fromEntries(
    (
      [
        ["fetchFunding", "funding"],
        ["fetchOpenInterest", "open-interest"],
        ["fetchMarkIndex", "mark-index"],
        ["fetchLiquidations", "liquidations"],
        ["fetchLiquidationBars", "liquidation-bars"],
      ] as const
    ).map(([name, route]) => [
      name,
      (...args: unknown[]) => {
        derivApi.calls.push({ route, args });
        // The liquidations page honours its cursor (the replay tape asks for the rows before a time).
        const rows = (derivApi.pages[route] ?? []) as { ts_event?: number }[];
        const items = route === "liquidations" ? rows.filter((r) => (r.ts_event ?? 0) < (args[1] as number)) : rows;
        return Promise.resolve({ items, has_more: false, venue: "BYBIT", market: "perp" });
      },
    ]),
  ),
  // The real class's constructor, so a test builds an `HttpError` exactly as the client does.
  HttpError: class extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
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
  footprint: [] as { enabled: boolean; rowTicks: number; barSeconds: number }[],
}));
// Stable references (a fresh array per render would churn useReplay's memo) that the
// replay tests swap in.
const mocks = vi.hoisted(() => ({
  candles: [] as unknown[],
  // Story 33.9: a compare symbol's own candles (absent = `candles`), and the ids whose load failed.
  candlesByIid: {} as Record<string, unknown[]>,
  failedIids: [] as string[],
  volume: [] as unknown[],
  venueMarket: null as { venue: string; market: string } | null,
  precision: { price: 2, size: 3 } as { price: number; size: number } | null,
  liveBar: null as unknown,
  session: { candles: [], volume: [], completeFrom: null } as {
    candles: unknown[];
    volume: unknown[];
    completeFrom: number | null;
    loading?: boolean;
  },
  sessionArgs: { enabled: false, sinceSeconds: 0, barSeconds: 0 },
}));

vi.mock("../hooks/useCandles", () => ({
  BAR_SECONDS: 60,
  useCandles: (iid: string, _chart: unknown, _enabled: boolean, bar: number) => {
    hooks.candlesBar.push(bar);
    return {
      candles: mocks.candlesByIid[iid] ?? mocks.candles,
      loadFailed: mocks.failedIids.includes(iid),
      volume: mocks.volume,
      venueMarket: mocks.venueMarket,
      precision: mocks.precision,
      openGapTo: (time: number) => hooks.openGapTo.push(time),
    };
  },
}));

// Story 32.8: the footprint hook records what the page asks of it; `footprintResult` is what it
// answers (a stable object, like the real hook's state).
const footprintResult = vi.hoisted(() => ({
  current: { items: [] as unknown[], precision: null as { price: number; size: number } | null, error: null as string | null },
}));
vi.mock("../hooks/useFootprint", () => ({
  useFootprint: (_iid: string, _chart: unknown, barSeconds: number, enabled: boolean, rowTicks: number) => {
    hooks.footprint.push({ enabled, rowTicks, barSeconds });
    return footprintResult.current;
  },
}));

vi.mock("../hooks/useSessionCandles", () => ({
  useSessionCandles: (_iid: string, enabled: boolean, sinceSeconds: number, barSeconds: number) => {
    mocks.sessionArgs = { enabled, sinceSeconds, barSeconds };
    return mocks.session;
  },
}));

vi.mock("../hooks/useLiveDerivs", () => ({
  useLiveDerivs: (iid: string, handlers: { onTick?: (tick: unknown) => void }) => {
    liveDerivs.subscribed.push(iid);
    liveDerivs.onTick = iid ? handlers.onTick : undefined;
    return iid ? liveDerivs.latest : {};
  },
}));

vi.mock("../hooks/useLiveLiquidations", () => ({
  useLiveLiquidations: (iid: string, handlers: { onLiquidation?: (row: unknown) => void }) => {
    liveDerivs.liquidationsSubscribed.push(iid);
    liveDerivs.onLiquidation = handlers.onLiquidation;
    return null;
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

// Story 33.6: the stored-source Anchored VWAP's own values hook (its entries and paging are tested in
// useStoredAnchoredVwap.test.ts). Records the drawings the page hands it; answers `values`/`errors`
// by drawing id (stable objects, like the real hook's memo).
const storedVwap = vi.hoisted(() => ({
  drawings: [] as unknown[],
  result: { values: {}, errors: {} } as { values: Record<string, unknown[]>; errors: Record<string, string> },
}));
vi.mock("../hooks/useStoredAnchoredVwap", () => ({
  useStoredAnchoredVwap: (_iid: string, _chart: unknown, drawings: unknown[]) => {
    storedVwap.drawings = drawings;
    return storedVwap.result;
  },
}));

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return { ...actual, useParams: () => ({ iid: route.iid }) };
});

// The subset of LightweightChart's props this page test asserts on. The stub records
// the latest props object ChartPage handed it -- every claim below is about what
// ChartPage FEEDS the chart component, never about LightweightChart's internals.
interface ChartStubProps {
  panes?: {
    id: string;
    kind: string;
    data: { time: number; value?: number; color?: string }[];
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
    format?: (value: number, time: number | null) => string;
    text?: string;
    zeroLine?: boolean;
    plot?: string;
    markersOnly?: boolean;
  }[];
  liquidationMarkers?: { id: string; time: number; position: string; price?: number; text?: string }[];
  patternMarkers?: { id: string; time: number; shape: string; position: string; color: string; tooltip: readonly string[] }[];
  onBarSpacing?: (spacing: number) => void;
  onLegendAction?: (action: "hide" | "settings" | "remove", group: string) => void;
  initialPaneHeights?: Record<string, number>;
  onPaneHeights?: (heights: Record<string, number>) => void;
  initialVisibleBars?: number;
  onVisibleBars?: (bars: number) => void;
  priceLines?: PriceLineSpec[];
  onPriceClick?: (price: number) => void;
  onPriceLineDrag?: (id: string, price: number) => void;
  drawings?: ({ id: string; kind: string; anchors: unknown[] } & Record<string, unknown>)[];
  precision?: { price: number; size: number } | null;
  fibActive?: boolean;
  onFibPlace?: (a: { time: number; price: number }, b: { time: number; price: number }) => void;
  onDrawingDrag?: (id: string, handle: string, point: { price: number; time: number | null; barsSince: (t: number) => number | null }) => void;
  onDrawingSettings?: (id: string) => void;
  onDrawingDelete?: (id: string) => void;
  onDrawingColor?: (id: string, color: string) => void;
  onPointClick?: (point: { time: number; price: number; shift?: boolean }) => void;
  // Story 33.10
  placement?: { tool: string; points: { time: number; price: number }[] } | null;
  magnet?: string;
  onDrawingDragStart?: (id: string) => void;
  onDrawingLock?: (id: string, locked: boolean) => void;
  onDrawingHide?: (id: string) => void;
  measureActive?: boolean;
  onMeasureEnd?: () => void;
  data?: { time: number }[];
  timeZone?: string;
  sessionBreaks?: boolean;
  countdown?: { barSeconds: number; enabled: boolean } | null;
  lastPrice?: { line: boolean; label: boolean };
  fitToWindow?: boolean;
  liveBar?: unknown;
  liveVolumeColor?: string;
  markerTime?: number | null;
  followNewest?: boolean;
  anchorMarkerTime?: number | null;
  legendExtras?: { id: string; label: string; color: string; value: number | null; format: (value: number) => string }[];
  footprint?: { items: { t: number }[]; precision: { price: number; size: number } | null; settings: Record<string, unknown> } | null;
  volumeProfiles?: {
    id: string;
    profile: { totalVolume: number; rows: unknown[] };
    xAnchor: unknown;
    width: unknown;
    edges?: unknown;
    respondsToZoom?: boolean;
    widthFraction?: number;
    maxWidthFraction?: number;
    barSeconds?: number;
    upColor?: string;
    showPoc?: boolean;
    tpo?: { rows: { count: number; blocks: number; overflow: number }[]; letters: boolean };
    initialBalance?: { high: number; low: number };
  }[];
  rangeSelectActive?: boolean;
  profileEdgesEditable?: boolean;
  onChartApi?: (chart: unknown) => void;
  crosshairVisible?: boolean;
  viewCommand?: { kind: string; seq: number } | null;
  onRangeSelect?: (start: { time: number; price: number }, end: { time: number; price: number }) => void;
  onProfileEdgeDrag?: (id: string, edge: "start" | "end", time: number) => void;
  onProfileEdgeCommit?: (id: string, edge: "start" | "end", time: number) => void;
  onProfileEdgeCancel?: () => void;
  chartType?: string;
  priceScale?: { mode: string; autoScale: boolean; invert: boolean };
  onPriceScale?: (patch: Record<string, unknown>) => void;
  scaleModesLocked?: string | null;
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
const { fetchCoinIndicatorConfig, fetchIndicatorCatalog } = await import("../api/client");
const { SAVE_DEBOUNCE_MS, SAVE_RETRY_MS } = await import("../hooks/useChartDrawings");
const { BUILT_IN_LAYOUT } = await import("../lib/chartLayout");
type ChartLayout = import("../lib/chartLayout").ChartLayout;

// Chart UX rework (2026-10-08): the chart type menu holds the candle chart types and the 1 s book
// lines (what the Candles / Lines buttons were); the Settings menu the display toggles.
const LINES_ITEM = "Lines (1 s book)";
function pickChartType(item: string): void {
  fireEvent.click(screen.getByRole("button", { name: /^Chart type:/ }));
  fireEvent.click(screen.getByRole("menuitemradio", { name: item }));
}
const switchToLines = (): void => pickChartType(LINES_ITEM);
const switchToCandles = (): void => pickChartType("Candles");
function openSettings(): void {
  if (screen.queryByRole("group", { name: "Chart settings" }) === null) {
    fireEvent.click(screen.getByRole("button", { name: "Chart settings" }));
  }
}
/** The Settings menu's checkbox `name`, opening the menu first when it is closed. */
function setting(name: string): HTMLElement {
  openSettings();
  return screen.getByRole("checkbox", { name });
}
/** The Settings menu's time zone select, opening the menu first when it is closed. */
function timeZoneSelect(): HTMLElement {
  openSettings();
  return screen.getByRole("combobox", { name: "Time zone" });
}

const IID = "BTC-USD-PERP.DYDX";
const layoutOf = (patch: Partial<ChartLayout> = {}): ChartLayout => ({ ...BUILT_IN_LAYOUT, ...patch });
// A promise-like that has already settled: `.then` runs its callback at once, so the layout GET lands
// while the page renders instead of a microtask later.
function settled<T>(value: T): PromiseLike<T> & { catch: () => unknown } {
  const thenable = {
    then: (onOk?: (v: T) => unknown) => {
      onOk?.(value);
      return thenable;
    },
    catch: () => thenable,
  };
  return thenable as unknown as PromiseLike<T> & { catch: () => unknown };
}
/** The layout the page last saved for `iid` (the argument of its latest PUT). */
function lastSaved(iid = IID): ChartLayout {
  const calls = layoutApi.save.mock.calls.filter((c) => c[0] === iid);
  return calls[calls.length - 1][1] as ChartLayout;
}
const flushSave = () =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(SAVE_DEBOUNCE_MS + 1);
  });
// The tools that save a drawing are off until the coin's drawings have loaded (Story 32.5): a test
// that clicks one renders through this, which lets the (mocked) GET settle first.
async function renderReady(ui: ReactElement): Promise<ReturnType<typeof render>> {
  const rendered = render(ui);
  await act(async () => {});
  return rendered;
}
const page = () => (
  <MemoryRouter>
    <ChartPage />
  </MemoryRouter>
);

const { TOOL_GROUPS } = await import("../lib/chartTools");
/** The rail group a tool (by its accessible name) is declared in. */
function groupOf(name: string): (typeof TOOL_GROUPS)[number] {
  const group = TOOL_GROUPS.find((g) => g.tools.some((t) => t.ariaLabel === name));
  if (!group) throw new Error(`no rail group holds ${name}`);
  return group;
}
/** A rail tool's control: its group's button when the group shows it, else its item in the group's
 * flyout menu (opened here if it is not open yet). */
function toolControl(name: string): HTMLElement {
  const button = screen.queryByRole("button", { name });
  if (button) return button;
  const item = screen.queryByRole("menuitemradio", { name });
  if (item) return item;
  fireEvent.click(screen.getByRole("button", { name: `${groupOf(name).label} tools` }));
  return screen.getByRole("menuitemradio", { name });
}
/** Arms a rail tool the way the operator does: its group's button, or the group's flyout menu. */
const armTool = (name: string) => fireEvent.click(toolControl(name));
/** Opens the Volume overlays dialog from the top bar (a no-op while it is open). */
function openOverlays(): HTMLElement {
  const open = screen.queryByRole("dialog", { name: "Volume overlays" });
  if (open) return open;
  fireEvent.click(screen.getByRole("button", { name: "Volume overlays" }));
  return screen.getByRole("dialog", { name: "Volume overlays" });
}
/** Clicks a control of the Volume overlays dialog by its accessible name, opening the dialog first. */
const overlayClick = (name: string) => fireEvent.click(within(openOverlays()).getByRole("button", { name }));

beforeEach(() => {
  lastChartProps.current = null;
  picker.values = {};
  picker.calls = 0;
  saveConfigMock.mockClear();
  hooks.candlesBar = [];
  hooks.liveBar = [];
  hooks.pickerBar = [];
  hooks.openGapTo = [];
  hooks.footprint = [];
  footprintResult.current = { items: [], precision: null, error: null };
  localStorage.clear();
  mocks.candles = [];
  mocks.candlesByIid = {};
  mocks.failedIids = [];
  marketsApi.get.mockReset().mockResolvedValue({
    items: [
      { instrument_id: "BTC-USD-PERP.HYPERLIQUID", symbol: "BTC", venue: "HYPERLIQUID", same_asset: true, market: "perp", volume24h: 2_500_000, collected: true },
    ],
    stale_venues: [],
  });
  mocks.volume = [];
  mocks.venueMarket = null;
  mocks.precision = { price: 2, size: 3 };
  drawingsApi.server = [];
  watchlistApi.server = [];
  watchlistApi.save.mockReset().mockImplementation((instruments: string[]) => Promise.resolve({ instruments }));
  liveRanks.latest = null;
  route.iid = IID;
  layoutApi.server = {};
  layoutApi.get.mockReset().mockImplementation((iid: string) =>
    settled({ layout: layoutApi.server[iid] ?? layoutOf(), seeded: layoutApi.server[iid] === undefined }),
  );
  layoutApi.save.mockReset().mockResolvedValue(undefined);
  layoutApi.saveDefault.mockReset().mockResolvedValue(undefined);
  layoutApi.reset.mockReset().mockResolvedValue(layoutOf());
  drawingsApi.save.mockReset().mockResolvedValue(undefined);
  mocks.liveBar = null;
  mocks.session = { candles: [], volume: [], completeFrom: null, loading: false };
  derivApi.pages = {};
  derivApi.calls = [];
  liveDerivs.latest = {};
  liveDerivs.subscribed = [];
  liveDerivs.liquidationsSubscribed = [];
  liveDerivs.onLiquidation = undefined;
  liveDerivs.onTick = undefined;
  storedVwap.drawings = [];
  storedVwap.result = { values: {}, errors: {} };
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
  it("has cursor active by default and arms the hline tool on click (AC #1)", async () => {
    await renderReady(page());

    expect(screen.getByTestId("chart-stub")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cursor tool" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("button", { name: "Horizontal line tool", pressed: true })).toBeNull();

    armTool("Horizontal line tool");

    expect(screen.getByRole("button", { name: "Horizontal line tool" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Cursor tool" })).toHaveAttribute("aria-pressed", "false");
  });

  it("places exactly one line from an armed hline's chart click, then disarms (AC #2)", async () => {
    await renderReady(page());
    armTool("Horizontal line tool");

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

  it("resets an armed tool to cursor on Escape (AC #5)", async () => {
    await renderReady(page());
    armTool("Horizontal line tool");

    fireEvent.keyDown(window, { key: "Escape" });

    expect(screen.getByRole("button", { name: "Cursor tool" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Horizontal line tool" })).toHaveAttribute("aria-pressed", "false");
  });

  it("disables the hline button in Lines mode and re-enables it back in Candles", async () => {
    await renderReady(page());

    switchToLines();
    expect(toolControl("Horizontal line tool")).toBeDisabled();

    switchToCandles();
    expect(toolControl("Horizontal line tool")).toBeEnabled();
  });

  it("disarms an armed hline when the chart switches to Lines mode", async () => {
    await renderReady(page());
    armTool("Horizontal line tool");

    switchToLines();

    expect(screen.getByRole("button", { name: "Cursor tool" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Horizontal line tool" })).toHaveAttribute("aria-pressed", "false");
  });

  it("updates the dragged line's price in the priceLines prop (AC #3)", async () => {
    await renderReady(page());
    armTool("Horizontal line tool");
    act(() => {
      lastChartProps.current!.onPriceClick!(61000.5);
    });

    act(() => {
      lastChartProps.current!.onPriceLineDrag!("hline-1", 61500.25);
    });

    expect(lastChartProps.current!.priceLines).toEqual([{ id: "hline-1", price: 61500.25, color: CHART_TOKENS["--chart-drawing"] }]);
  });

  it("gives each placed line its own counter id, and a drag updates only its own spec", async () => {
    await renderReady(page());
    armTool("Horizontal line tool");
    act(() => {
      lastChartProps.current!.onPriceClick!(61000.5);
    });
    armTool("Horizontal line tool");
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
    // Story 33.11: the ±100 pane is the Pane display; markers are the default (tested below).
    const entry = {
      name: "CandlePattern",
      params: { pattern: "ENGULFING", trend_bars: 3 },
      category: "native",
      style: { value: { display: "pane" } },
    };
    vi.mocked(fetchCoinIndicatorConfig).mockResolvedValueOnce([entry]);
    picker.values = { "CandlePattern_pattern=ENGULFING,trend_bars=3.value": [] };
    render(page());
    await act(async () => {}); // catalog + saved config

    // The below-chart list is gone (Story 32.3): the legend's gear opens the same editor as a modal.
    const id = "CandlePattern_pattern=ENGULFING,trend_bars=3";
    expect(lastChartProps.current!.panes!.find((p) => p.id === `${id}.value`)).toMatchObject({
      group: id,
      actionable: true,
      kind: "Histogram",
      placement: "pane",
    });
    expect(lastChartProps.current!.patternMarkers).toEqual([]);
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
      // After Apply the values mock still keys the old params, so no entry owns the series any
      // more: it keeps its own instance group, with no buttons (the real hook refetches under the
      // new key). With no entry its display is unknown, so it takes the default (markers): an
      // invisible legend row, never a pane that would flash in and out.
      kind: "Line",
      placement: "overlay",
      markersOnly: true,
      group: id,
      actionable: false,
    });
  });

  it("saves placed and dragged lines through the drawings resource (one PUT per burst) and restores them on a remount", async () => {
    vi.useFakeTimers();
    try {
      const first = await renderReady(page());
      armTool("Horizontal line tool");
      act(() => lastChartProps.current?.onPriceClick?.(123.5));
      // A drag changes the line on every mouse move: still one save once the burst ends.
      act(() => lastChartProps.current?.onPriceLineDrag?.("hline-1", 128));
      act(() => lastChartProps.current?.onPriceLineDrag?.("hline-1", 130));
      expect(drawingsApi.save).not.toHaveBeenCalled();
      await act(async () => {
        vi.advanceTimersByTime(SAVE_DEBOUNCE_MS + 1);
      });
      expect(drawingsApi.save).toHaveBeenCalledTimes(1);
      expect(drawingsApi.save).toHaveBeenCalledWith(
        "BTC-USD-PERP.DYDX",
        [{ kind: "hline", id: "hline-1", price: 130, color: CHART_TOKENS["--chart-drawing"] }],
        false, // an ordinary save: no keepalive (its 64 KiB cap is for an unload only)
      );
      const saved = drawingsApi.save.mock.calls[0][1] as unknown[];
      first.unmount();

      drawingsApi.server = saved;
      await renderReady(page());
      expect(lastChartProps.current?.priceLines).toEqual([expect.objectContaining({ id: "hline-1", price: 130 })]);

      // a second placement continues the counter instead of colliding with the restored id
      armTool("Horizontal line tool");
      act(() => lastChartProps.current?.onPriceClick?.(99));
      expect(lastChartProps.current?.priceLines?.map((l) => l.id)).toEqual(["hline-1", "hline-2"]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("flushes a save still pending when the chart goes away", async () => {
    const view = await renderReady(page());
    armTool("Horizontal line tool");
    act(() => lastChartProps.current?.onPriceClick?.(123.5));
    view.unmount();
    expect(drawingsApi.save).toHaveBeenCalledTimes(1);
    expect(drawingsApi.save.mock.calls[0][2]).toBe(false); // the page lives on: no keepalive needed
  });

  it("an unmount during an in-flight save sends the newer list after it lands, in order", async () => {
    vi.useFakeTimers();
    try {
      let land: () => void = () => {};
      drawingsApi.save.mockImplementationOnce(() => new Promise<void>((resolve) => (land = resolve)));
      const view = await renderReady(page());
      armTool("Horizontal line tool");
      act(() => lastChartProps.current?.onPriceClick?.(123.5));
      await act(async () => {
        vi.advanceTimersByTime(SAVE_DEBOUNCE_MS + 1);
      });
      expect(drawingsApi.save).toHaveBeenCalledTimes(1); // in flight
      act(() => lastChartProps.current?.onPriceLineDrag?.("hline-1", 130));
      view.unmount();
      // Not raced against the one in flight (the older could land last and win).
      expect(drawingsApi.save).toHaveBeenCalledTimes(1);
      await act(async () => land());
      expect(drawingsApi.save).toHaveBeenCalledTimes(2);
      expect(drawingsApi.save.mock.calls[1][1]).toEqual([expect.objectContaining({ id: "hline-1", price: 130 })]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("on pagehide sends the latest list even while an earlier save is still in flight", async () => {
    vi.useFakeTimers();
    try {
      let land: () => void = () => {};
      drawingsApi.save.mockImplementationOnce(() => new Promise<void>((resolve) => (land = resolve)));
      await renderReady(page());
      armTool("Horizontal line tool");
      act(() => lastChartProps.current?.onPriceClick?.(123.5));
      await act(async () => {
        vi.advanceTimersByTime(SAVE_DEBOUNCE_MS + 1);
      });
      expect(drawingsApi.save).toHaveBeenCalledTimes(1); // in flight
      act(() => lastChartProps.current?.onPriceLineDrag?.("hline-1", 130));
      act(() => {
        window.dispatchEvent(new Event("pagehide"));
      });
      expect(drawingsApi.save).toHaveBeenCalledTimes(2);
      expect(drawingsApi.save.mock.calls[1][1]).toEqual([expect.objectContaining({ id: "hline-1", price: 130 })]);
      expect(drawingsApi.save.mock.calls[1][2]).toBe(true);
      await act(async () => land());
    } finally {
      vi.useRealTimers();
    }
  });

  it("imports the browser's old horizontal lines once, and removes the key after the first save that has them", async () => {
    vi.useFakeTimers();
    try {
      localStorage.setItem("chart-hlines:BTC-USD-PERP.DYDX", JSON.stringify([{ id: "hline-1", price: 61000.5, color: "#112233" }]));
      await renderReady(page());
      expect(lastChartProps.current?.priceLines).toEqual([{ id: "hline-1", price: 61000.5, color: "#112233" }]);
      expect(localStorage.getItem("chart-hlines:BTC-USD-PERP.DYDX")).not.toBeNull(); // not before it is saved
      await act(async () => {
        vi.advanceTimersByTime(SAVE_DEBOUNCE_MS + 1);
      });
      expect(drawingsApi.save).toHaveBeenCalledWith(
        "BTC-USD-PERP.DYDX",
        [{ kind: "hline", id: "hline-1", price: 61000.5, color: "#112233" }],
        false,
      );
      expect(localStorage.getItem("chart-hlines:BTC-USD-PERP.DYDX")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("skips an old line at a price the server already holds, so a repeated import cannot duplicate", async () => {
    drawingsApi.server = [{ kind: "hline", id: "hline-1", price: 61000.5 }];
    localStorage.setItem("chart-hlines:BTC-USD-PERP.DYDX", JSON.stringify([{ id: "hline-1", price: 61000.5 }]));
    await renderReady(page());
    expect(lastChartProps.current?.priceLines).toHaveLength(1);
    expect(localStorage.getItem("chart-hlines:BTC-USD-PERP.DYDX")).toBeNull();
    expect(drawingsApi.save).not.toHaveBeenCalled();
  });

  it("keeps the drawing tools off, and saves nothing, when the drawings could not be loaded", async () => {
    const { fetchCoinDrawings } = await import("../api/client");
    vi.mocked(fetchCoinDrawings).mockRejectedValueOnce(new Error("GET failed"));
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const view = await renderReady(page());
    expect(screen.getByRole("alert")).toHaveTextContent("Drawings could not be loaded");
    for (const name of ["Trendline tool", "Horizontal line tool", "Fibonacci retracement tool", "Long position tool"]) {
      expect(toolControl(name)).toBeDisabled();
    }
    view.unmount();
    expect(drawingsApi.save).not.toHaveBeenCalled();
    expect(errors).toHaveBeenCalled();
    errors.mockRestore();
  });

  it("does not retry a load the server refused (4xx): it would fail identically", async () => {
    vi.useFakeTimers();
    const { fetchCoinDrawings } = await import("../api/client");
    vi.mocked(fetchCoinDrawings).mockClear();
    vi.mocked(fetchCoinDrawings).mockRejectedValueOnce(Object.assign(new Error("GET failed: 400"), { status: 400 }));
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      await renderReady(page());
      await act(async () => {
        vi.advanceTimersByTime(SAVE_RETRY_MS * 4);
      });
      expect(fetchCoinDrawings).toHaveBeenCalledTimes(1);
      expect(screen.getByText(/Drawings could not be loaded/)).toBeInTheDocument();
    } finally {
      errors.mockRestore();
      vi.useRealTimers();
    }
  });

  it("retries a failed load every SAVE_RETRY_MS, so a transient error doesn't leave the chart without drawings", async () => {
    vi.useFakeTimers();
    const { fetchCoinDrawings } = await import("../api/client");
    vi.mocked(fetchCoinDrawings).mockRejectedValueOnce(new Error("GET failed: 502"));
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      drawingsApi.server = [{ kind: "hline", id: "hline-1", price: 130 }];
      await renderReady(page());
      expect(toolControl("Horizontal line tool")).toBeDisabled();
      await act(async () => {
        vi.advanceTimersByTime(SAVE_RETRY_MS + 1);
      });
      expect(toolControl("Horizontal line tool")).toBeEnabled();
      expect(lastChartProps.current?.priceLines).toEqual([expect.objectContaining({ id: "hline-1", price: 130 })]);
    } finally {
      errors.mockRestore();
      vi.useRealTimers();
    }
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

  it("remembers the timeframe per coin across a remount, through the server's layout", () => {
    const first = render(page());
    fireEvent.click(screen.getByRole("button", { name: "Timeframe 1D" }));
    first.unmount(); // flushes the pending save
    layoutApi.server[IID] = lastSaved();

    hooks.candlesBar = [];
    render(page());
    expect(screen.getByRole("button", { name: "Timeframe 1D" })).toHaveAttribute("aria-pressed", "true");
    expect(hooks.candlesBar.every((bar) => bar === 86400)).toBe(true); // the first request already used it
  });

  // Story 32.4 (2026-09-30): still no toggle. The chart area alone is light, by operator decision,
  // through the `.chart-workspace` tokens in theme.css; the rest of the app keeps the VGA identity.
  it("orders the top bar [market, timeframe, chart type] [what is drawn] [actions, menus, view], with no theme toggle", () => {
    render(page());

    const names = within(screen.getByRole("toolbar", { name: "Chart controls" }))
      .getAllByRole("button")
      .map((b) => b.getAttribute("aria-label") ?? b.textContent);
    // Chart UX rework (2026-10-08): one line; Invert is gone, the display toggles and the
    // Liquidation tape are in the Settings menu, the scale switches and Fit / Latest on the chart.
    expect(names).toEqual([
      "BTC-USD-PERP.DYDX", // Story 33.12: the symbol opens the symbol search
      ...["1m", "5m", "15m", "1H", "4H", "1D", "1W"].map((l) => `Timeframe ${l}`),
      "Chart type: Candles",
      "Indicators",
      "Compare",
      "Spread",
      "Volume overlays",
      "Alert",
      "Replay",
      "Layout ▾",
      "Chart settings",
      "Focus",
      "Watchlist",
      "Keyboard shortcuts",
    ]);
    expect(screen.getByRole("link", { name: /Rankings/ })).toHaveAttribute("href", "/");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("BTC-USD-PERP.DYDX");
  });

  it("orders the left toolbar cursor, crosshair | the tool groups, each with its first tool shown", async () => {
    await renderReady(page());

    const names = within(screen.getByRole("toolbar", { name: "Chart tools" }))
      .getAllByRole("button")
      .map((b) => b.getAttribute("aria-label"));
    expect(names).toEqual([
      "Cursor tool",
      "Crosshair toggle",
      "Trendline tool",
      "Lines tools",
      "Fibonacci retracement tool",
      "Fibonacci tools",
      "Long position tool",
      "Projection tools",
      "Rectangle tool",
      "Shapes / Annotation tools",
      "Measurement tool",
      "Measure tools",
      "Fixed range volume profile tool",
      "Volume-based tools",
      // Story 33.10: the drawing actions, after a divider.
      "Magnet",
      "Undo",
      "Redo",
      "Hide all drawings",
      "Delete all drawings",
    ]);
  });

  it("groups the tools TradingView-style: each group's flyout lists its tools, Long and Short in one", async () => {
    await renderReady(page());
    const menuOf = (group: string): string[] => {
      fireEvent.click(screen.getByRole("button", { name: `${group} tools` }));
      const items = within(screen.getByRole("menu", { name: group }))
        .getAllByRole("menuitemradio")
        .map((b) => b.getAttribute("aria-label")!);
      fireEvent.click(screen.getByRole("button", { name: `${group} tools` })); // closes it again
      return items;
    };

    expect(menuOf("Lines")).toEqual([
      "Trendline tool",
      "Ray tool",
      "Extended line tool",
      "Horizontal line tool",
      "Vertical line tool",
      "Parallel channel tool",
    ]);
    expect(menuOf("Fibonacci")).toEqual(["Fibonacci retracement tool", "Fibonacci extension tool"]);
    expect(menuOf("Projection")).toEqual(["Long position tool", "Short position tool"]);
    expect(menuOf("Shapes / Annotation")).toEqual(["Rectangle tool", "Text tool", "Arrow tool"]);
    expect(menuOf("Measure")).toEqual(["Measurement tool", "Price range tool", "Date range tool"]);
    expect(menuOf("Volume-based")).toEqual([
      "Fixed range volume profile tool",
      "Anchored volume profile tool",
      "Anchored VWAP tool",
    ]);
    // A one-tool group has no flyout.
    expect(screen.queryByRole("button", { name: "Cursor tools" })).toBeNull();
  });

  it("opens a group's flyout as a menu, arms the picked tool and shows it on the group button from then on", async () => {
    await renderReady(page());
    const expander = screen.getByRole("button", { name: "Projection tools" });
    expect(expander).toHaveAttribute("aria-haspopup", "menu");
    expect(expander).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(expander);
    expect(expander).toHaveAttribute("aria-expanded", "true");
    const menu = screen.getByRole("menu", { name: "Projection" });
    expect(within(menu).getByRole("menuitemradio", { name: "Long position tool" })).toHaveFocus();
    fireEvent.click(within(menu).getByRole("menuitemradio", { name: "Short position tool" }));

    expect(screen.queryByRole("menu")).toBeNull();
    expect(screen.getByRole("button", { name: "Short position tool" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("button", { name: "Long position tool" })).toBeNull();
    act(() => lastChartProps.current!.onPointClick!({ time: 100, price: 100 }));
    expect(lastChartProps.current!.drawings!.at(-1)).toMatchObject({ kind: "position", side: "short" });

    // Placed: the cursor is back, and the group's button still shows (and re-arms) the Short.
    expect(screen.getByRole("button", { name: "Cursor tool" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Short position tool" }));
    expect(screen.getByRole("button", { name: "Short position tool" })).toHaveAttribute("aria-pressed", "true");
  });

  it("remembers each group's last-used tool across a timeframe change", async () => {
    await renderReady(page());
    armTool("Horizontal line tool");
    fireEvent.click(screen.getByRole("button", { name: "Timeframe 5m" }));

    expect(screen.getByRole("button", { name: "Horizontal line tool" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Trendline tool" })).toBeNull();
  });

  it("closes a flyout on Escape (the armed tool stays armed) and on a click outside it", async () => {
    await renderReady(page());
    armTool("Fibonacci retracement tool");
    fireEvent.click(screen.getByRole("button", { name: "Lines tools" }));
    const item = screen.getByRole("menuitemradio", { name: "Trendline tool" });

    fireEvent.keyDown(item, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(screen.getByRole("button", { name: "Lines tools" })).toHaveFocus();
    expect(screen.getByRole("button", { name: "Fibonacci retracement tool" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: "Lines tools" }));
    expect(screen.getByRole("menu", { name: "Lines" })).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByTestId("chart-stub"));
    expect(screen.queryByRole("menu")).toBeNull();

    // Focus leaving the group by keyboard closes it too.
    fireEvent.click(screen.getByRole("button", { name: "Lines tools" }));
    fireEvent.blur(screen.getByRole("menuitemradio", { name: "Trendline tool" }), {
      relatedTarget: screen.getByRole("button", { name: "Crosshair toggle" }),
    });
    expect(screen.queryByRole("menu")).toBeNull();
    // Esc outside a flyout disarms, as before.
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.getByRole("button", { name: "Cursor tool" })).toHaveAttribute("aria-pressed", "true");
  });

  it("closes on Escape a flyout whose every item is disabled (focus sits on the menu itself)", async () => {
    mocks.precision = null;
    await renderReady(page());
    fireEvent.click(screen.getByRole("button", { name: "Projection tools" }));
    const menu = screen.getByRole("menu", { name: "Projection" });
    expect(menu).toHaveFocus();

    fireEvent.keyDown(menu, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("keeps one flyout open at a time, and arrow keys move through its items", async () => {
    await renderReady(page());
    fireEvent.click(screen.getByRole("button", { name: "Lines tools" }));
    fireEvent.click(screen.getByRole("button", { name: "Volume-based tools" }));
    expect(screen.getAllByRole("menu")).toHaveLength(1);
    const menu = screen.getByRole("menu", { name: "Volume-based" });

    fireEvent.keyDown(within(menu).getByRole("menuitemradio", { name: "Fixed range volume profile tool" }), { key: "ArrowDown" });
    expect(within(menu).getByRole("menuitemradio", { name: "Anchored volume profile tool" })).toHaveFocus();
    fireEvent.keyDown(within(menu).getByRole("menuitemradio", { name: "Anchored volume profile tool" }), { key: "ArrowUp" });
    expect(within(menu).getByRole("menuitemradio", { name: "Fixed range volume profile tool" })).toHaveFocus();
  });

  it("disables a candles-only tool in its flyout in Lines mode, with its title, while the group stays usable", async () => {
    await renderReady(page());
    armTool("Horizontal line tool");
    switchToLines();

    // The group shows the HLine (disabled), but its flyout still offers the Trend line, which Lines mode allows.
    expect(screen.getByRole("button", { name: "Horizontal line tool" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Lines tools" }));
    expect(screen.getByRole("menuitemradio", { name: "Horizontal line tool" })).toBeDisabled();
    fireEvent.click(screen.getByRole("menuitemradio", { name: "Trendline tool" }));
    expect(screen.getByRole("button", { name: "Trendline tool" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: "Volume-based tools" }));
    const avp = screen.getByRole("menuitemradio", { name: "Anchored volume profile tool" });
    expect(avp).toBeDisabled();
    expect(avp).toHaveAttribute("title", "Anchored volume profile: click the bar it starts at");
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
    // Chart UX rework (2026-10-08): an added indicator can be added again, as another copy.
    expect(within(dialog).getByRole("button", { name: /^SimpleMovingAverage/ })).toBeEnabled();
    expect(within(dialog).getByText("added · click to add another")).toBeInTheDocument();
  });

  it("adds a second copy with the same defaults as instance 2, its own legend row and series keys", async () => {
    const dialog = await openDialog();

    fireEvent.click(within(dialog).getByRole("button", { name: /^SimpleMovingAverage/ }));
    await act(async () => {});
    fireEvent.click(within(dialog).getByRole("button", { name: /^SimpleMovingAverage/ }));
    await act(async () => {});

    expect(saveConfigMock).toHaveBeenLastCalledWith("BTC-USD-PERP.DYDX", [
      { name: "SimpleMovingAverage", params: {}, category: "native" },
      { name: "SimpleMovingAverage", params: {}, category: "native", instance: 2 },
    ]);
  });
});

describe("ChartPage volume toggle (Story 32.2)", () => {
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

  it("removes the volume pane when switched off and saves volume: false in the coin's layout", async () => {
    const dialog = await openDialog();

    fireEvent.click(within(dialog).getByRole("checkbox", { name: "Volume" }));

    expect(paneIds()).toEqual([]);
    cleanup(); // flushes the pending save
    expect(lastSaved().volume).toBe(false);
  });

  it("restores the off state on reload", () => {
    layoutApi.server[IID] = layoutOf({ volume: false });
    render(page());

    expect(paneIds()).toEqual([]);
  });

  it("puts volume first, before every indicator pane, when switched back on", async () => {
    layoutApi.server[IID] = layoutOf({ volume: false });
    picker.values = { "RelativeStrengthIndex_period=14.value": [] };
    const dialog = await openDialog();
    expect(paneIds()).toEqual(["RelativeStrengthIndex_period=14.value"]);

    fireEvent.click(within(dialog).getByRole("checkbox", { name: "Volume" }));

    expect(paneIds()).toEqual(["volume", "RelativeStrengthIndex_period=14.value"]);
    cleanup();
    expect(lastSaved().volume).toBe(true);
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
    layoutApi.server[IID] = layoutOf({ volume: false });
    render(page());

    fireEvent.click(screen.getByRole("button", { name: "Timeframe 5m" }));

    expect(paneIds()).toEqual([]);
  });

  it("still feeds the fetched volume to the profiles with the pane off", () => {
    const bars = [1, 2, 3, 4, 5].map((t) => ({ time: t, open: t, high: t + 1, low: t, close: t + 1 }));
    mocks.candles = bars;
    mocks.volume = bars.map((b) => ({ time: b.time, value: 10 }));
    const totalWith = (): number => {
      armTool("Fixed range volume profile tool");
      act(() => {
        lastChartProps.current!.onRangeSelect!({ time: 2, price: 1 }, { time: 4, price: 2 });
      });
      return lastChartProps.current!.volumeProfiles![0].profile.totalVolume;
    };
    const on = render(page());
    const withPane = totalWith();
    on.unmount();

    layoutApi.server[IID] = layoutOf({ volume: false });
    render(page());
    expect(paneIds()).toEqual([]);

    expect(totalWith()).toBe(withPane);
  });

  it("still renders, with volume on and nothing imported, when localStorage is blocked", () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    try {
      render(page());

      expect(paneIds()).toEqual(["volume"]);
      expect(errors).not.toHaveBeenCalled();
    } finally {
      getItem.mockRestore();
      errors.mockRestore();
    }
  });
});

describe("ChartPage trendline tool (Story 18.2)", () => {
  const arm = () => armTool("Trendline tool");
  const click = (time: number, price: number) =>
    act(() => {
      lastChartProps.current!.onPointClick!({ time, price });
    });

  it("creates a trendline from two clicks, then disarms (AC #1/#2)", async () => {
    await renderReady(<ChartPage />);
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

  it("cancels an in-progress line on Escape without creating a drawing (AC #5)", async () => {
    await renderReady(<ChartPage />);
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

  it("discards a pending first point when another tool is selected", async () => {
    await renderReady(<ChartPage />);
    arm();
    click(100, 10);

    armTool("Cursor tool");
    arm();
    click(200, 20);
    click(300, 30);

    expect(lastChartProps.current!.drawings![0].anchors[0]).toEqual({ time: 200, price: 20 });
  });

  it("works in Lines mode too", async () => {
    await renderReady(<ChartPage />);
    switchToLines();

    expect(toolControl("Trendline tool")).toBeEnabled();
  });
});

describe("ChartPage measurement tool (Story 18.3)", () => {
  it("arms the measurement tool and disarms it when the drag ends (AC #1/#5)", () => {
    render(<ChartPage />);
    armTool("Measurement tool");
    expect(lastChartProps.current!.measureActive).toBe(true);

    act(() => {
      lastChartProps.current!.onMeasureEnd!();
    });

    expect(lastChartProps.current!.measureActive).toBe(false);
    expect(screen.getByRole("button", { name: "Cursor tool" })).toHaveAttribute("aria-pressed", "true");
  });

  it("cancels an armed measurement on Escape", () => {
    render(<ChartPage />);
    armTool("Measurement tool");

    fireEvent.keyDown(window, { key: "Escape" });

    expect(lastChartProps.current!.measureActive).toBe(false);
  });

  it("is disabled in Lines mode", () => {
    render(<ChartPage />);
    switchToLines();

    expect(toolControl("Measurement tool")).toBeDisabled();
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

    expect(screen.getByRole("button", { name: "Step back" })).toBeDisabled(); // at the marker
    fireEvent.click(screen.getByRole("button", { name: "Step forward" }));
    expect(lastChartProps.current!.data).toHaveLength(3);
    expect(screen.getByRole("button", { name: "Step back" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Step back" }));
    expect(lastChartProps.current!.data).toHaveLength(2);
    // DW-146: clamped at the start marker, never walking past it.
    expect(screen.getByRole("button", { name: "Step back" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Step back" }));
    expect(lastChartProps.current!.data).toHaveLength(2);
    expect(lastChartProps.current!.markerTime).toBe(120);
  });

  it("a pick on a data gap stays picking and says so; the next real pick clears it (DW-146)", () => {
    render(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Replay" }));
    expect(screen.getByRole("status")).toHaveTextContent("Click a candle to start the replay");

    pickBar(150); // no bar at this time

    expect(screen.getByRole("status")).toHaveTextContent("No bar at that time -- click a candle");
    expect(lastChartProps.current!.data).toHaveLength(5);
    expect(lastChartProps.current!.markerTime).toBeNull();
    pickBar(120);
    expect(lastChartProps.current!.markerTime).toBe(120);
    fireEvent.click(screen.getByRole("button", { name: "Go to..." }));
    expect(screen.getByRole("status")).toHaveTextContent("Click a candle to start the replay");
  });

  it("disables Play and Step forward at the newest loaded bar, until a later bar arrives (DW-146)", () => {
    const { rerender } = render(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Replay" }));
    pickBar(240);
    expect(screen.getByRole("button", { name: "Play" })).toBeEnabled();
    expect(screen.queryByText("End of loaded data")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Step forward" }));

    expect(lastChartProps.current!.data).toHaveLength(5);
    expect(screen.getByRole("button", { name: "Play" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Step forward" })).toBeDisabled();
    expect(screen.getByText("End of loaded data")).toBeInTheDocument();

    mocks.candles = [...bars, { time: 360, open: 1, high: 2, low: 1, close: 1 }];
    rerender(<ChartPage />);
    expect(screen.getByRole("button", { name: "Play" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Step forward" })).toBeEnabled();
    expect(screen.queryByText("End of loaded data")).toBeNull();
    expect(lastChartProps.current!.data).toHaveLength(5); // nothing revealed by itself
  });

  it("asks the chart to follow the replay head only while a replay is active (DW-145)", () => {
    render(<ChartPage />);
    expect(lastChartProps.current!.followNewest).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Replay" }));
    expect(lastChartProps.current!.followNewest).toBe(false);
    pickBar(120);
    expect(lastChartProps.current!.followNewest).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Exit" }));
    expect(lastChartProps.current!.followNewest).toBe(false);
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

  it("does not place a trendline point from the click that picks the start bar", async () => {
    await renderReady(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Replay" }));

    pickBar(120);

    armTool("Trendline tool");
    pickBar(60);
    pickBar(120);
    expect(lastChartProps.current!.drawings).toHaveLength(1);
  });

  it("is disabled in Lines mode, saying why (Story 33.12: Replay is a candle-chart feature)", () => {
    render(<ChartPage />);
    expect(screen.getByRole("button", { name: "Replay" })).toHaveAttribute("title", "Bar Replay (Alt+R)");
    switchToLines();

    expect(screen.getByRole("button", { name: "Replay" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Replay" })).toHaveAttribute("title", "Replay is available on candle charts");
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
    armTool("Fixed range volume profile tool");
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
    armTool("Fixed range volume profile tool");
    select(2, 4);
    const placed = lastChartProps.current!.volumeProfiles![0].profile;

    mocks.candles = [...bars, { time: 6, open: 6, high: 7, low: 6, close: 7 }];
    mocks.volume = mocks.candles.map((b) => ({ time: (b as { time: number }).time, value: 99 }));
    rerender(<ChartPage />);

    expect(lastChartProps.current!.volumeProfiles![0].profile).toBe(placed);
  });

  it("moves a ghost edge without recomputing, then recomputes once on commit (AC #3)", () => {
    render(<ChartPage />);
    armTool("Fixed range volume profile tool");
    select(2, 4);
    const placed = lastChartProps.current!.volumeProfiles![0].profile;

    act(() => lastChartProps.current!.onProfileEdgeDrag!("frvp-1", "end", 5));
    expect(lastChartProps.current!.volumeProfiles![0].width).toEqual({ toTime: 5 });
    expect(lastChartProps.current!.volumeProfiles![0].profile).toBe(placed);

    act(() => lastChartProps.current!.onProfileEdgeCommit!("frvp-1", "end", 5));
    expect(lastChartProps.current!.volumeProfiles).toHaveLength(1);
    expect(lastChartProps.current!.volumeProfiles![0].profile.totalVolume).toBe(40);
  });

  it("clears the ghost edge on a cancelled edge drag, leaving the profile as placed (DW-150)", () => {
    render(<ChartPage />);
    armTool("Fixed range volume profile tool");
    select(2, 4);
    const placed = lastChartProps.current!.volumeProfiles![0];

    act(() => lastChartProps.current!.onProfileEdgeDrag!("frvp-1", "end", 5));
    act(() => lastChartProps.current!.onProfileEdgeCancel!());

    expect(lastChartProps.current!.volumeProfiles![0].width).toEqual({ toTime: 4 });
    expect(lastChartProps.current!.volumeProfiles![0].profile).toBe(placed.profile);
  });

  it("shows the FRVP settings in the Volume overlays dialog as soon as the tool is armed, with no remove button yet (DW-150)", () => {
    render(<ChartPage />);
    expect(within(openOverlays()).queryByRole("group", { name: "Fixed range volume profile settings" })).toBeNull();
    fireEvent.click(within(openOverlays()).getByRole("button", { name: "Close volume overlays" }));

    armTool("Fixed range volume profile tool");

    const dialog = openOverlays();
    expect(within(dialog).getByRole("group", { name: "Fixed range volume profile settings" })).toBeInTheDocument();
    expect(within(dialog).getByText("Drag a range on the chart to place one.")).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: /Remove volume profile/ })).toBeNull();
  });

  it("draws an FRVP from the Volume overlays dialog: it closes and arms the rail's tool", () => {
    render(<ChartPage />);
    overlayClick("Draw fixed range volume profile");

    expect(screen.queryByRole("dialog", { name: "Volume overlays" })).toBeNull();
    expect(screen.getByRole("button", { name: "Fixed range volume profile tool" })).toHaveAttribute("aria-pressed", "true");
    expect(lastChartProps.current!.rangeSelectActive).toBe(true);
  });

  it("lists each placed range by its UTC span in the dialog, with one shared settings set", () => {
    render(<ChartPage />);
    for (const [a, b] of [[1, 2], [3, 5]]) {
      armTool("Fixed range volume profile tool");
      select(a, b);
    }
    const dialog = openOverlays();
    const entry = within(dialog).getByRole("region", { name: "Fixed Range Volume Profile" });
    expect(within(entry).getByText("1970-01-01 00:00:01 to 1970-01-01 00:00:02 UTC")).toBeInTheDocument();
    expect(within(entry).getByText("1970-01-01 00:00:03 to 1970-01-01 00:00:05 UTC")).toBeInTheDocument();
    expect(within(entry).getAllByRole("button", { name: /Remove volume profile/ })).toHaveLength(2);
    expect(within(entry).getAllByRole("group", { name: "Fixed range volume profile settings" })).toHaveLength(1);
  });

  it("supports several profiles, removable independently (AC #4)", () => {
    render(<ChartPage />);
    for (const [a, b] of [[1, 2], [3, 5]]) {
      armTool("Fixed range volume profile tool");
      select(a, b);
    }
    expect(lastChartProps.current!.volumeProfiles!.map((p) => p.id)).toEqual(["frvp-1", "frvp-2"]);

    overlayClick("Remove volume profile frvp-1");

    expect(lastChartProps.current!.volumeProfiles!.map((p) => p.id)).toEqual(["frvp-2"]);
  });

  it("ignores a range with no candles in it and cancels the armed tool on Escape", () => {
    render(<ChartPage />);
    armTool("Fixed range volume profile tool");

    select(50, 60);
    expect(lastChartProps.current!.volumeProfiles).toEqual([]);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(lastChartProps.current!.rangeSelectActive).toBe(false);
  });

  it("rebuilds placed profiles when the shared settings change (row count)", () => {
    render(<ChartPage />);
    armTool("Fixed range volume profile tool");
    select(1, 5);
    expect(lastChartProps.current!.volumeProfiles![0].profile.rows).toHaveLength(24);

    fireEvent.change(within(openOverlays()).getByLabelText("Row count"), { target: { value: "6" } });

    expect(lastChartProps.current!.volumeProfiles![0].profile.rows).toHaveLength(6);
  });

  it("shows only revealed bars during a replay and the full stored profile after it ends", () => {
    render(<ChartPage />);
    armTool("Fixed range volume profile tool");
    select(1, 5);
    expect(lastChartProps.current!.volumeProfiles![0].profile.totalVolume).toBeCloseTo(50);

    fireEvent.click(screen.getByRole("button", { name: "Replay" }));
    act(() => lastChartProps.current!.onPointClick!({ time: 3, price: 1 }));
    expect(lastChartProps.current!.volumeProfiles![0].profile.totalVolume).toBeCloseTo(30);

    fireEvent.click(screen.getByRole("button", { name: "Exit" }));
    expect(lastChartProps.current!.volumeProfiles![0].profile.totalVolume).toBeCloseTo(50);
  });

  it("only lets edges be grabbed while the cursor tool is active", async () => {
    await renderReady(<ChartPage />);
    expect(lastChartProps.current!.profileEdgesEditable).toBe(true);

    armTool("Horizontal line tool");

    expect(lastChartProps.current!.profileEdgesEditable).toBe(false);
  });

  it("is disabled in Lines mode", () => {
    render(<ChartPage />);
    switchToLines();

    expect(toolControl("Fixed range volume profile tool")).toBeDisabled();
  });
});

describe("ChartPage visible range volume profile (Story 18.7)", () => {
  const bars = [1, 2, 3, 4, 5, 6].map((n) => ({ time: n, open: n, high: n + 1, low: n, close: n + 1 }));
  let handlers: (() => void)[];
  let visible: { from: number; to: number };
  let logical: { from: number; to: number };
  let frames: FrameRequestCallback[];

  const attachChart = () =>
    act(() => {
      lastChartProps.current!.onChartApi!({
        applyOptions: vi.fn(),
        timeScale: () => ({
          getVisibleRange: () => visible,
          getVisibleLogicalRange: () => logical,
          subscribeVisibleLogicalRangeChange: (h: () => void) => handlers.push(h),
          unsubscribeVisibleLogicalRangeChange: vi.fn(),
          subscribeVisibleTimeRangeChange: vi.fn(),
          unsubscribeVisibleTimeRangeChange: vi.fn(),
          // The chart's corner controls (scale chips, Latest) read these.
          scrollPosition: () => 0,
          height: () => 28,
          subscribeSizeChange: vi.fn(),
          unsubscribeSizeChange: vi.fn(),
        }),
        priceScale: () => ({ width: () => 60 }),
        chartElement: () => document.createElement("div"),
      });
    });
  const vrvp = () => lastChartProps.current!.volumeProfiles!.filter((p) => p.id === "vrvp");
  /** The chart reports a new view; the page reads it on the next animation frame. */
  const pan = (range: { from: number; to: number }, logicalRange = logical) => {
    visible = range;
    logical = logicalRange;
    handlers.forEach((h) => h());
  };
  const nextFrame = () => {
    const due = frames;
    frames = [];
    act(() => due.forEach((cb) => cb(0)));
  };

  beforeEach(() => {
    handlers = [];
    frames = [];
    visible = { from: 2, to: 4 };
    logical = { from: 1, to: 3 };
    mocks.candles = bars;
    mocks.volume = bars.map((b) => ({ time: b.time, value: 10 }));
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => frames.push(cb));
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
  });
  afterEach(() => vi.unstubAllGlobals());

  it("adds a right-anchored profile of the visible bars, only after Add (AC #1/#2)", () => {
    render(<ChartPage />);
    attachChart();
    expect(vrvp()).toHaveLength(0);

    overlayClick("Add visible range volume profile");

    expect(vrvp()).toHaveLength(1);
    expect(vrvp()[0].xAnchor).toBe("right");
    expect(vrvp()[0].profile.totalVolume).toBeCloseTo(30);
  });

  it("caps its width at a share of the pane, so a narrow pane keeps its candles (DW-151)", () => {
    render(<ChartPage />);
    attachChart();
    overlayClick("Add visible range volume profile");

    expect(vrvp()[0]).toMatchObject({ width: 150, maxWidthFraction: 0.3 });
  });

  it("recomputes once per animation frame and updates the same entry, never adding one (AC #3, DW-151)", () => {
    render(<ChartPage />);
    attachChart();
    overlayClick("Add visible range volume profile");
    const first = vrvp()[0].profile;

    pan({ from: 2, to: 4 }); // unchanged range
    nextFrame();
    expect(vrvp()[0].profile).toBe(first);

    for (let to = 5; to <= 15; to++) pan({ from: 1, to: Math.min(to, 6) }); // a pan burst in one frame
    expect(vrvp()[0].profile).toBe(first); // nothing before the frame
    expect(frames).toHaveLength(1);
    nextFrame();

    expect(vrvp()).toHaveLength(1);
    expect(vrvp()[0].profile).not.toBe(first);
    expect(vrvp()[0].profile.totalVolume).toBeCloseTo(60);
  });

  it("says it covers the loaded bars only while the view reaches past the oldest bar (DW-151)", () => {
    render(<ChartPage />);
    attachChart();
    overlayClick("Add visible range volume profile");
    expect(screen.queryByText(/Covers loaded bars only/)).toBeNull();

    pan({ from: 1, to: 4 }, { from: -12, to: 3 });
    nextFrame();
    expect(screen.getByText(/Covers loaded bars only/)).toBeInTheDocument();
    // Under the chart too, where it is read with the dialog closed.
    expect(screen.getByText("Visible Range Volume Profile: covers the loaded bars only")).toBeInTheDocument();

    pan({ from: 1, to: 4 }, { from: 0, to: 3 });
    nextFrame();
    expect(screen.queryByText(/Covers loaded bars only/)).toBeNull();
    expect(screen.queryByText("Visible Range Volume Profile: covers the loaded bars only")).toBeNull();
  });

  it("subscribes to the visible range only while active, and shows revealed bars only during a replay", () => {
    render(<ChartPage />);
    attachChart();
    // The chart's corner controls (Latest) hold their own subscription throughout.
    const base = handlers.length;

    overlayClick("Add visible range volume profile");
    expect(handlers).toHaveLength(base + 1);
    expect(vrvp()[0].profile.totalVolume).toBeCloseTo(30); // bars 2..4

    fireEvent.click(screen.getByRole("button", { name: "Replay" }));
    act(() => lastChartProps.current!.onPointClick!({ time: 3, price: 1 }));
    expect(vrvp()[0].profile.totalVolume).toBeCloseTo(20); // bars 2..3, bar 4 hidden
  });

  it("disables Add while active or outside Candles mode", () => {
    render(<ChartPage />);
    attachChart();
    const add = () => within(openOverlays()).getByRole("button", { name: "Add visible range volume profile" });
    expect(add()).toBeEnabled();

    fireEvent.click(add());
    expect(add()).toBeDisabled();
    expect(add()).toHaveTextContent("on the chart");

    switchToLines();
    expect(screen.getByText("Shown in Candles mode only")).toBeInTheDocument();
    const svp = within(openOverlays()).getByRole("button", { name: "Add Session Volume Profile" });
    expect(svp).toBeDisabled();
    expect(svp).toHaveTextContent("Candles mode only");
  });

  it("is a single instance: adding again does not stack, Remove clears it (AC #4)", () => {
    render(<ChartPage />);
    attachChart();
    const add = () => overlayClick("Add visible range volume profile");
    add();
    add();
    expect(vrvp()).toHaveLength(1);

    overlayClick("Remove visible range volume profile");

    expect(vrvp()).toHaveLength(0);
  });

  it("coexists with a placed FRVP and is hidden in Lines mode", () => {
    render(<ChartPage />);
    attachChart();
    overlayClick("Add visible range volume profile");
    armTool("Fixed range volume profile tool");
    act(() => lastChartProps.current!.onRangeSelect!({ time: 1, price: 1 }, { time: 3, price: 2 }));
    expect(lastChartProps.current!.volumeProfiles!.map((p) => p.id)).toEqual(["frvp-1", "vrvp"]);

    switchToLines();

    expect(vrvp()).toHaveLength(0);
  });
});

describe("ChartPage Volume overlays dialog", () => {
  const D1 = Date.UTC(2024, 0, 1) / 1000;
  const times = [D1, D1 + 60, D1 + 86_400, D1 + 86_460];
  const bar = (t: number, p: number) => ({ time: t, open: p, high: p + 1, low: p, close: p + 1 });

  beforeEach(() => {
    const candles = times.map((t, i) => bar(t, 10 + i * 10));
    mocks.candles = candles;
    mocks.volume = times.map((t) => ({ time: t, value: 5 }));
    mocks.session = { candles, volume: mocks.volume, completeFrom: null };
  });

  it("opens from the top bar's Volume overlays button, in the Indicators cluster, with the overlays to add and nothing on", () => {
    render(page());
    const topbar = screen.getByRole("toolbar", { name: "Chart controls" });
    const labels = within(topbar).getAllByRole("button").map((b) => b.textContent);
    // Chart UX rework (2026-10-08): Indicators, Compare, Spread, Volume overlays -- what the chart draws.
    expect(labels.indexOf("Volume overlays")).toBe(labels.indexOf("Indicators") + 3);
    expect(screen.queryByRole("dialog", { name: "Volume overlays" })).toBeNull();

    fireEvent.click(within(topbar).getByRole("button", { name: "Volume overlays" }));

    const dialog = screen.getByRole("dialog", { name: "Volume overlays" });
    const adds = within(within(dialog).getByRole("region", { name: "Add a volume overlay" }))
      .getAllByRole("button")
      .map((b) => b.getAttribute("aria-label"));
    expect(adds).toEqual([
      "Add visible range volume profile",
      "Draw fixed range volume profile",
      "Add Session Volume Profile",
      "Add Session Volume Profile HD",
      "Add Periodic Volume Profile",
      "Add Time Price Opportunity (TPO)",
      "Add Auto Anchored Volume Profile",
    ]);
    expect(within(dialog).getByText("Nothing added yet")).toBeInTheDocument();
  });

  it("adds an overlay into the list below with its settings, applies a setting at once, and removes it", () => {
    render(page());
    overlayClick("Add Periodic Volume Profile");

    const dialog = screen.getByRole("dialog", { name: "Volume overlays" });
    const entry = within(dialog).getByRole("region", { name: "Periodic Volume Profile" });
    expect(within(dialog).queryByText("Nothing added yet")).toBeNull();
    expect(within(dialog).getByRole("button", { name: "Add Periodic Volume Profile" })).toBeDisabled();
    fireEvent.change(within(entry).getByLabelText("Profile period"), { target: { value: "daily" } });
    expect(lastChartProps.current!.volumeProfiles!.filter((p) => p.id.startsWith("session-")).length).toBeGreaterThan(0);
    expect(mocks.sessionArgs).toMatchObject({ enabled: true, barSeconds: 60 }); // the daily period's fetch

    fireEvent.click(within(entry).getByRole("button", { name: "Remove session volume profile" }));
    expect(within(dialog).queryByRole("region", { name: "Periodic Volume Profile" })).toBeNull();
    expect(lastChartProps.current!.volumeProfiles!.some((p) => p.id.startsWith("session-"))).toBe(false);
    expect(within(dialog).getByRole("button", { name: "Add Periodic Volume Profile" })).toBeEnabled();
  });

  it("says a session-type preset replaces the one on, then switches the one slot", () => {
    render(page());
    overlayClick("Add Session Volume Profile");
    const dialog = screen.getByRole("dialog", { name: "Volume overlays" });
    const tpo = within(dialog).getByRole("button", { name: "Add Time Price Opportunity (TPO)" });
    expect(tpo).toHaveTextContent("replaces Session Volume Profile");
    expect(within(dialog).getByRole("button", { name: "Add visible range volume profile" })).not.toHaveTextContent("replaces");

    fireEvent.click(tpo);

    expect(within(dialog).queryByRole("region", { name: "Session Volume Profile" })).toBeNull();
    expect(within(dialog).getByRole("region", { name: "Time Price Opportunity (TPO)" })).toBeInTheDocument();
    expect(within(dialog).getAllByRole("group", { name: "Session volume profile settings" })).toHaveLength(1);
    expect(within(dialog).getByRole("button", { name: "Add Session Volume Profile" })).toHaveTextContent(
      "replaces Time Price Opportunity (TPO)",
    );
  });

  it("keeps VRVP, a session profile and placed FRVPs on together, each with its own entry", () => {
    render(page());
    overlayClick("Add visible range volume profile");
    overlayClick("Add Session Volume Profile HD");
    armTool("Fixed range volume profile tool");
    act(() => lastChartProps.current!.onRangeSelect!({ time: times[0] as never, price: 0 }, { time: times[1] as never, price: 0 }));

    const dialog = openOverlays();
    for (const name of ["Visible Range Volume Profile", "Session Volume Profile HD", "Fixed Range Volume Profile"]) {
      expect(within(dialog).getByRole("region", { name })).toBeInTheDocument();
    }
    fireEvent.click(within(dialog).getByRole("button", { name: "Remove volume profile frvp-1" }));
    expect(within(dialog).queryByRole("region", { name: "Fixed Range Volume Profile" })).toBeNull();
    expect(lastChartProps.current!.volumeProfiles!.some((p) => p.id === "frvp-1")).toBe(false);
  });

  it("disables every Add and the FRVP draw in Lines mode, saying why", () => {
    render(page());
    switchToLines();
    const dialog = openOverlays();

    expect(within(dialog).getByText(/Volume overlays draw in Candles mode only/)).toBeInTheDocument();
    for (const button of within(within(dialog).getByRole("region", { name: "Add a volume overlay" })).getAllByRole("button")) {
      expect(button).toBeDisabled();
      expect(button).toHaveTextContent("Candles mode only");
    }
  });

  it("closes from its close button and from Esc (the dialog's own close event), keeping what was added", () => {
    render(page());
    overlayClick("Add Session Volume Profile");
    fireEvent.click(within(openOverlays()).getByRole("button", { name: "Close volume overlays" }));
    expect(screen.queryByRole("dialog", { name: "Volume overlays" })).toBeNull();
    expect(lastChartProps.current!.volumeProfiles!.some((p) => p.id.startsWith("session-"))).toBe(true);

    // jsdom has no showModal: Esc's effect on a native modal dialog is its `close` event.
    fireEvent(openOverlays(), new Event("close"));
    expect(screen.queryByRole("dialog", { name: "Volume overlays" })).toBeNull();
  });

  it("keeps the partial-data notices under the chart while the dialog is closed (DW-151/153)", () => {
    mocks.session = { ...mocks.session, loading: true };
    render(page());
    expect(screen.queryByRole("list", { name: "Volume overlay notices" })).toBeNull();
    overlayClick("Add Session Volume Profile");
    fireEvent.click(within(openOverlays()).getByRole("button", { name: "Close volume overlays" }));

    const notices = screen.getByRole("list", { name: "Volume overlay notices" });
    expect(within(notices).getByText("Session Volume Profile: loading session history")).toBeInTheDocument();
    expect(within(notices).getByText("Session Volume Profile: 2 of 5 sessions drawn")).toBeInTheDocument();

    switchToLines();
    expect(screen.queryByRole("list", { name: "Volume overlay notices" })).toBeNull();
  });

  it("an Esc inside it does not disarm an FRVP tool armed before it opened", () => {
    render(page());
    armTool("Fixed range volume profile tool");
    const dialog = openOverlays();

    fireEvent.keyDown(within(dialog).getByRole("button", { name: "Close volume overlays" }), { key: "Escape" });

    expect(screen.getByRole("button", { name: "Fixed range volume profile tool" })).toHaveAttribute("aria-pressed", "true");
  });

  it("shows the session status messages in the overlay's entry", () => {
    mocks.session = { ...mocks.session, loading: true };
    render(page());
    overlayClick("Add Session Volume Profile");
    const entry = within(openOverlays()).getByRole("region", { name: "Session Volume Profile" });

    expect(within(entry).getByRole("status")).toHaveTextContent("Loading session history…");
    expect(within(entry).getByText(/Showing 2 of 5/)).toBeInTheDocument();
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

    overlayClick("Add Session Volume Profile");

    expect(sessions().map((s) => s.id)).toEqual([`session-${D1}`, `session-${D2}`, `session-${D3}`]);
    expect(sessions()[1].xAnchor).toEqual({ time: D2, offsetSeconds: 0 });
    // The last bar (D2 + 60) plus one 1-minute session bar: the elapsed part of the period.
    expect(sessions()[1].width).toEqual({ toTime: D2 + 60, offsetSeconds: 60 });
    expect(sessions()[1].barSeconds).toBe(60);
    expect(sessions()[1].widthFraction).toBe(0.7);
    expect(sessions()[1].profile.totalVolume).toBeCloseTo(10);
    expect(sessions()[0].profile).not.toBe(sessions()[1].profile);
  });

  it("SVP HD is the same component with a higher row count and respondsToZoom (AC #3)", () => {
    render(<ChartPage />);

    overlayClick("Add Session Volume Profile");
    expect(sessions()[0].profile.rows).toHaveLength(24);
    expect(sessions()[0].respondsToZoom).toBe(false);

    overlayClick("Add Session Volume Profile HD");
    expect(sessions()).toHaveLength(3); // switched preset, not stacked
    expect(sessions()[0].profile.rows).toHaveLength(120);
    expect(sessions()[0].respondsToZoom).toBe(true);
  });

  it("keeps the session count and colors when switching presets", () => {
    render(<ChartPage />);
    overlayClick("Add Session Volume Profile");
    fireEvent.change(screen.getByLabelText("Sessions to render"), { target: { value: "2" } });

    overlayClick("Add Session Volume Profile HD");

    expect(sessions()).toHaveLength(2);
    expect((screen.getByLabelText("Sessions to render") as HTMLInputElement).value).toBe("2");
  });

  it("says so when fewer sessions than requested can be drawn", () => {
    render(<ChartPage />);
    overlayClick("Add Session Volume Profile");
    expect(screen.getByText(/Showing 3 of 5/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Sessions to render"), { target: { value: "3" } });

    expect(screen.queryByText(/Showing/)).toBeNull();
  });

  it("limits the rendered sessions with the sessions setting (AC #4)", () => {
    render(<ChartPage />);
    overlayClick("Add Session Volume Profile");

    fireEvent.change(screen.getByLabelText("Sessions to render"), { target: { value: "2" } });

    expect(sessions().map((s) => s.id)).toEqual([`session-${D2}`, `session-${D3}`]);
  });

  it("can be removed, and is hidden in Lines mode", () => {
    render(<ChartPage />);
    overlayClick("Add Session Volume Profile");
    switchToLines();
    expect(sessions()).toHaveLength(0);
    expect(screen.getByText("Shown in Candles mode only")).toBeInTheDocument();

    switchToCandles();
    expect(sessions()).toHaveLength(3);
    overlayClick("Remove session volume profile");
    expect(sessions()).toHaveLength(0);
  });

  it("draws a session over the part of it the chart has loaded, and skips one the chart holds none of", () => {
    mocks.candles = [bar(D3, 50), bar(D3 + 60, 60)]; // chart only loaded the last day
    render(<ChartPage />);

    overlayClick("Add Session Volume Profile");

    expect(sessions().map((s) => s.id)).toEqual([`session-${D3}`]);
  });

  it("anchors a partly loaded session at its period start, before the first chart bar (DW-152)", () => {
    // The session's own history reaches the period start; the chart's oldest bar is 10 min into D3.
    const sessionTimes = [D3, D3 + 300, D3 + 600, D3 + 660];
    mocks.session = {
      candles: sessionTimes.map((t) => bar(t, 50)),
      volume: sessionTimes.map((t) => ({ time: t, value: 5 })),
      completeFrom: null,
    };
    mocks.candles = [bar(D3 + 600, 50), bar(D3 + 660, 60)];
    render(<ChartPage />);

    overlayClick("Add Session Volume Profile");

    const [session] = sessions();
    expect(session.xAnchor).toEqual({ time: D3 + 600, offsetSeconds: -600 }); // 10 bars left of it
    expect(session.width).toEqual({ toTime: D3 + 660, offsetSeconds: 60 });
  });

  it("gives a session held in a single chart bar its elapsed period as width (DW-152)", () => {
    mocks.candles = [bar(D1, 10), bar(D2, 30), bar(D3, 50)]; // a daily chart: one bar per session
    layoutApi.server[IID] = layoutOf({ bar_seconds: 86_400 });
    render(page());

    overlayClick("Add Session Volume Profile");

    // D2's newest 1-minute bar is D2 + 60: the session has elapsed to D2 + 120 (the past one ends
    // where its newest bar does; it never stretches to a period end the data does not reach).
    expect(sessions()[1].xAnchor).toEqual({ time: D2, offsetSeconds: 0 });
    expect(sessions()[1].width).toEqual({ toTime: D2, offsetSeconds: 120 });
    expect(sessions()[1].barSeconds).toBe(86_400);
  });

  it("shows a loading status while older session history is paged in (DW-153)", () => {
    mocks.session = { ...mocks.session, loading: true };
    render(<ChartPage />);
    overlayClick("Add Session Volume Profile");

    expect(screen.getByRole("status")).toHaveTextContent("Loading session history…");
  });

  it("asks for history back from the session count's start, re-armed at the UTC period rollover (DW-152)", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    try {
      vi.setSystemTime(Date.UTC(2024, 0, 10, 23, 59, 30));
      render(<ChartPage />);
      overlayClick("Add Session Volume Profile");
      expect(mocks.sessionArgs.sinceSeconds).toBe(Date.UTC(2024, 0, 6) / 1000); // 5 days: Jan 6..10

      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000); // midnight
      });

      expect(mocks.sessionArgs.sinceSeconds).toBe(Date.UTC(2024, 0, 7) / 1000);
    } finally {
      vi.useRealTimers();
    }
  });

  it("re-arms a monthly rollover in capped steps: a month's delay would overflow setTimeout (DW-152)", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    const timeouts = vi.spyOn(globalThis, "setTimeout");
    try {
      vi.setSystemTime(Date.UTC(2024, 0, 2));
      render(<ChartPage />);
      overlayClick("Add Periodic Volume Profile");
      fireEvent.change(screen.getByLabelText("Profile period"), { target: { value: "monthly" } });
      expect(mocks.sessionArgs.sinceSeconds).toBe(Date.UTC(2023, 8, 1) / 1000); // Sep..Jan
      // A browser fires a delay past 2^31 - 1 ms at once; the fake clock would not, so check them.
      expect(timeouts.mock.calls.every(([, ms]) => (ms ?? 0) <= 2 ** 31 - 1)).toBe(true);

      // One act per day: React re-renders (and the effect re-arms) when each act settles.
      const day = () =>
        act(async () => {
          await vi.advanceTimersByTimeAsync(86_400_000);
        });
      for (let i = 0; i < 29; i++) await day(); // Jan 31: re-armed every 24 h, unchanged
      expect(mocks.sessionArgs.sinceSeconds).toBe(Date.UTC(2023, 8, 1) / 1000);

      await day(); // Feb 1
      expect(mocks.sessionArgs.sinceSeconds).toBe(Date.UTC(2023, 9, 1) / 1000);
    } finally {
      timeouts.mockRestore();
      vi.useRealTimers();
    }
  });

  it("anchors the session history to the replay cutoff while replaying (DW-153)", () => {
    render(<ChartPage />);
    overlayClick("Add Session Volume Profile");
    fireEvent.click(screen.getByRole("button", { name: "Replay" }));

    act(() => lastChartProps.current!.onPointClick!({ time: D2 + 60, price: 1 }));

    expect(mocks.sessionArgs.sinceSeconds).toBe(D2 - 4 * 86_400); // 5 sessions ending on the cutoff's day
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

    overlayClick("Add Periodic Volume Profile");

    expect(periods().map((p) => p.id)).toEqual([`session-${MON1}`, `session-${MON2}`]);
    expect(periods().map((p) => p.profile.totalVolume)).toEqual([10, 10]);
    expect((screen.getByLabelText("Profile period") as HTMLSelectElement).value).toBe("weekly");
  });

  it("regroups when the period dropdown changes, offering only the fixed set", () => {
    render(<ChartPage />);
    overlayClick("Add Periodic Volume Profile");
    const select = screen.getByLabelText("Profile period") as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).toEqual(["4h", "daily", "weekly", "monthly"]);
    expect([...select.options].map((o) => o.textContent)).toEqual(["4 hours", "Daily", "Weekly", "Monthly"]);

    fireEvent.change(select, { target: { value: "daily" } });

    expect(periods()).toHaveLength(4); // four distinct UTC days
  });

  it("switching the period refetches at that period's bar size and a deeper wanted start", () => {
    render(<ChartPage />);
    overlayClick("Add Periodic Volume Profile");
    const weekly = { ...mocks.sessionArgs };
    expect(weekly).toMatchObject({ enabled: true, barSeconds: 300 });

    fireEvent.change(screen.getByLabelText("Profile period"), { target: { value: "monthly" } });

    expect(mocks.sessionArgs.barSeconds).toBe(900);
    expect(mocks.sessionArgs.sinceSeconds).toBeLessThan(weekly.sinceSeconds); // 5 months back vs 5 weeks back
  });

  it("switching PVP -> SVP -> PVP restarts on the preset's default period", () => {
    render(<ChartPage />);
    overlayClick("Add Periodic Volume Profile");
    fireEvent.change(screen.getByLabelText("Profile period"), { target: { value: "monthly" } });

    overlayClick("Add Session Volume Profile");
    expect(mocks.sessionArgs.barSeconds).toBe(60);
    overlayClick("Add Periodic Volume Profile");

    expect((screen.getByLabelText("Profile period") as HTMLSelectElement).value).toBe("weekly");
  });

  it("reuses the shared sessions-to-render setting (AC #3), and only PVP shows a period dropdown", () => {
    render(<ChartPage />);
    overlayClick("Add Periodic Volume Profile");
    fireEvent.change(screen.getByLabelText("Sessions to render"), { target: { value: "1" } });
    expect(periods()).toHaveLength(1);

    overlayClick("Add Session Volume Profile");
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

  it("places a trendline and a horizontal line while a replay is active, and they survive stepping", async () => {
    await renderReady(<ChartPage />);
    fireEvent.click(screen.getByRole("button", { name: "Replay" }));
    point(3, 1);
    expect(lastChartProps.current!.data).toHaveLength(3);

    armTool("Trendline tool");
    point(1, 1);
    point(3, 2);
    armTool("Horizontal line tool");
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
    armTool("Trendline tool");
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

  it("the Cursor button says what it does, and is active again after a Trend tool completes", async () => {
    await renderReady(page());
    const cursor = screen.getByRole("button", { name: "Cursor tool" });
    expect(cursor).toHaveAttribute("title", "Select / edit drawings (Esc)");

    armTool("Trendline tool");
    expect(cursor).toHaveAttribute("aria-pressed", "false");
    act(() => lastChartProps.current!.onPointClick!({ time: 1, price: 1 }));
    act(() => lastChartProps.current!.onPointClick!({ time: 2, price: 2 }));

    expect(lastChartProps.current!.drawings).toHaveLength(1);
    expect(cursor).toHaveAttribute("aria-pressed", "true");
    expect(lastChartProps.current).toMatchObject({ drawEditable: true });
  });

  it("has no indicator list, select or Add, and no volume-overlay controls, below the chart", async () => {
    await mountWith([SMA], { [`${SMA_ID}.value`]: [] });

    expect(screen.queryByRole("button", { name: "Add" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Indicators" })).toBeNull();
    // The volume overlays are added and edited in their own dialog (nothing links to the old anchor).
    expect(screen.queryByRole("button", { name: /^Add / })).toBeNull();
    expect(screen.queryByRole("group", { name: /volume profile settings/ })).toBeNull();
    expect(document.querySelector("div#indicators")).toBeNull();
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

  it("gives Volume an eye (hide, no save), a gear (its colour mode, Story 33.6) and an x (the Indicators toggle)", () => {
    render(page());
    expect(paneOf("volume").configurable).not.toBe(false);
    expect(paneOf("volume").hidden).toBe(false);

    legend("hide", "volume");
    expect(paneOf("volume").hidden).toBe(true);
    legend("hide", "volume");
    expect(paneOf("volume").hidden).toBe(false);

    legend("settings", "volume");
    const dialog = screen.getByRole("dialog", { name: "Volume settings" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog", { name: "Volume settings" })).toBeNull();

    legend("remove", "volume");
    expect(lastChartProps.current!.panes!.map((p) => p.id)).toEqual([]);
    cleanup();
    expect(lastSaved().volume).toBe(false);
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

describe("ChartPage Fibonacci and position tools (Story 32.5)", () => {
  const drag = (price: number, time: number | null = null, barsSince = (): number | null => null) => ({ price, time, barsSince });
  const drawings = (): ChartStubProps["drawings"] => lastChartProps.current!.drawings;

  it("arms the Fibonacci tool for a drag, and Esc cancels it with nothing placed", async () => {
    await renderReady(page());
    armTool("Fibonacci retracement tool");
    expect(lastChartProps.current!.fibActive).toBe(true);

    fireEvent.keyDown(window, { key: "Escape" });

    expect(lastChartProps.current!.fibActive).toBe(false);
    expect(drawings()).toEqual([]);
  });

  it("places a Fibonacci from a drag (A at the press, B at the release) with the default levels, then selects Cursor", async () => {
    await renderReady(page());
    armTool("Fibonacci retracement tool");

    act(() => lastChartProps.current!.onFibPlace!({ time: 100, price: 100.004 }, { time: 200, price: 90 }));

    const [fib] = drawings()!;
    expect(fib).toMatchObject({
      id: "fib-1",
      kind: "fib",
      anchors: [
        { time: 100, price: 100 },
        { time: 200, price: 90 },
      ],
      extend_right: true,
      label_side: "left",
      line_width: 1,
    });
    const levels = fib.levels as { ratio: number; enabled: boolean }[];
    expect(levels.filter((l) => l.enabled).map((l) => l.ratio)).toEqual([0, 0.236, 0.382, 0.5, 0.618, 0.786, 1]);
    expect(levels.filter((l) => !l.enabled).map((l) => l.ratio)).toEqual([1.272, 1.618, 2.618, 4.236]);
    expect(screen.getByRole("button", { name: "Cursor tool" })).toHaveAttribute("aria-pressed", "true");
  });

  it("ignores a Fibonacci drag that ends where it began and keeps the tool armed", async () => {
    await renderReady(page());
    armTool("Fibonacci retracement tool");
    act(() => lastChartProps.current!.onFibPlace!({ time: 100, price: 100 }, { time: 100, price: 100 }));
    expect(drawings()).toEqual([]);
    expect(lastChartProps.current!.fibActive).toBe(true);
  });

  it("ignores a flat Fibonacci drag (one price on the grid) whatever its times", async () => {
    await renderReady(page());
    armTool("Fibonacci retracement tool");
    act(() => lastChartProps.current!.onFibPlace!({ time: 100, price: 100 }, { time: 200, price: 100.004 }));
    expect(drawings()).toEqual([]);
    expect(lastChartProps.current!.fibActive).toBe(true);
  });

  it("places no horizontal line at or below zero (the resource would refuse every save)", async () => {
    await renderReady(page());
    armTool("Horizontal line tool");
    act(() => lastChartProps.current?.onPriceClick?.(0.004));
    act(() => lastChartProps.current?.onPriceClick?.(-3));
    expect(drawings()).toEqual([]);
  });

  it("places a Long by one click: stop 1 % below, target 2 x the stop distance above", async () => {
    await renderReady(page());
    armTool("Long position tool");

    act(() => lastChartProps.current!.onPointClick!({ time: 100, price: 100 }));

    expect(drawings()![0]).toMatchObject({ id: "position-1", kind: "position", side: "long", time: 100, entry: 100, stop: 99, target: 102, width_bars: 40 });
    expect(screen.getByRole("button", { name: "Cursor tool" })).toHaveAttribute("aria-pressed", "true");
  });

  it("places a Short as the mirror: stop above, target below", async () => {
    await renderReady(page());
    armTool("Short position tool");
    act(() => lastChartProps.current!.onPointClick!({ time: 100, price: 100 }));
    expect(drawings()![0]).toMatchObject({ side: "short", entry: 100, stop: 101, target: 98 });
  });

  it("snaps a placed position's prices to the precision the candles carried (6 decimals here)", async () => {
    mocks.precision = { price: 6, size: 0 };
    await renderReady(page());
    armTool("Long position tool");
    act(() => lastChartProps.current!.onPointClick!({ time: 100, price: 0.1234561234 }));
    expect(drawings()![0]).toMatchObject({ entry: 0.123456, stop: 0.122221, target: 0.125926 });
  });

  it("keeps the Fibonacci and position tools off until the instrument's precision is known", async () => {
    mocks.precision = null;
    await renderReady(page());
    for (const name of ["Fibonacci retracement tool", "Long position tool", "Short position tool"]) {
      expect(toolControl(name)).toBeDisabled();
    }
    expect(toolControl("Trendline tool")).toBeEnabled();
    expect(lastChartProps.current!.precision).toBeNull();
  });

  it("hands the chart the precision it labels with", async () => {
    await renderReady(page());
    expect(lastChartProps.current!.precision).toEqual({ price: 2, size: 3 });
  });

  async function withLong() {
    await renderReady(page());
    armTool("Long position tool");
    act(() => lastChartProps.current!.onPointClick!({ time: 100, price: 100 }));
  }

  it("moves a dragged target and refuses one dragged past the entry (it stops one tick above)", async () => {
    await withLong();
    act(() => lastChartProps.current!.onDrawingDrag!("position-1", "target", drag(103)));
    expect(drawings()![0]).toMatchObject({ target: 103 });
    act(() => lastChartProps.current!.onDrawingDrag!("position-1", "target", drag(95)));
    expect(drawings()![0]).toMatchObject({ target: 100.01 });
  });

  it("moves the whole box with the entry handle and sets the width from the right handle", async () => {
    await withLong();
    act(() => lastChartProps.current!.onDrawingDrag!("position-1", "entry", drag(110, 500)));
    expect(drawings()![0]).toMatchObject({ time: 500, entry: 110, stop: 109, target: 112 });
    act(() => lastChartProps.current!.onDrawingDrag!("position-1", "right", drag(110, 900, () => 25)));
    expect(drawings()![0]).toMatchObject({ width_bars: 25 });
  });

  it("moves a Fibonacci anchor with its handle", async () => {
    await renderReady(page());
    armTool("Fibonacci retracement tool");
    act(() => lastChartProps.current!.onFibPlace!({ time: 100, price: 100 }, { time: 200, price: 90 }));
    act(() => lastChartProps.current!.onDrawingDrag!("fib-1", "b", drag(88.5, 300)));
    expect(drawings()![0].anchors).toEqual([
      { time: 100, price: 100 },
      { time: 300, price: 88.5 },
    ]);
  });

  it("opens the position settings modal and saves the entry, stop, target, width and the optional size", async () => {
    await withLong();
    act(() => lastChartProps.current!.onDrawingSettings!("position-1"));
    const dialog = screen.getByRole("dialog", { name: "Position settings" });
    expect(within(dialog).getByLabelText("Entry")).toHaveValue("100.00"); // at the instrument precision

    fireEvent.change(within(dialog).getByLabelText("Target"), { target: { value: "104" } });
    fireEvent.change(within(dialog).getByLabelText("Account size"), { target: { value: "10000" } });
    fireEvent.change(within(dialog).getByLabelText("Risk %"), { target: { value: "1" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Apply" }));

    expect(drawings()![0]).toMatchObject({ entry: 100, stop: 99, target: 104, account: 10000, risk_pct: 1 });
    expect(screen.queryByRole("dialog", { name: "Position settings" })).toBeNull();
  });

  it("refuses a position whose target sits on the wrong side, or a lone account size, and keeps the modal open", async () => {
    await withLong();
    act(() => lastChartProps.current!.onDrawingSettings!("position-1"));
    const dialog = screen.getByRole("dialog", { name: "Position settings" });

    fireEvent.change(within(dialog).getByLabelText("Target"), { target: { value: "98" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Apply" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("A long needs stop < entry < target");

    fireEvent.change(within(dialog).getByLabelText("Target"), { target: { value: "102" } });
    fireEvent.change(within(dialog).getByLabelText("Account size"), { target: { value: "500" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Apply" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Risk %");
    expect(drawings()![0]).toMatchObject({ target: 102 });
    expect(drawings()![0]).not.toHaveProperty("account");
  });

  it("opens the Fibonacci settings: a level off, a colour, extend right, label side and width", async () => {
    await renderReady(page());
    armTool("Fibonacci retracement tool");
    act(() => lastChartProps.current!.onFibPlace!({ time: 100, price: 100 }, { time: 200, price: 90 }));
    act(() => lastChartProps.current!.onDrawingSettings!("fib-1"));
    const dialog = screen.getByRole("dialog", { name: "Fibonacci settings" });

    fireEvent.click(within(dialog).getByLabelText("0.5 on"));
    fireEvent.click(within(dialog).getByLabelText("1.618 on"));
    fireEvent.change(within(dialog).getByLabelText("0.618 colour"), { target: { value: "#123456" } });
    fireEvent.click(within(dialog).getByLabelText("Extend levels to the right"));
    fireEvent.change(within(dialog).getByLabelText("Labels:"), { target: { value: "right" } });
    fireEvent.change(within(dialog).getByLabelText("Width:"), { target: { value: "3" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Apply" }));

    const fib = drawings()![0];
    const levels = fib.levels as { ratio: number; enabled: boolean; color: string }[];
    expect(levels.find((l) => l.ratio === 0.5)?.enabled).toBe(false);
    expect(levels.find((l) => l.ratio === 1.618)?.enabled).toBe(true);
    expect(levels.find((l) => l.ratio === 0.618)?.color).toBe("#123456");
    expect(fib).toMatchObject({ extend_right: false, label_side: "right", line_width: 3 });
  });

  it("closes a settings modal on Cancel without a change, and removes the drawing on Remove", async () => {
    await withLong();
    act(() => lastChartProps.current!.onDrawingSettings!("position-1"));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(drawings()).toHaveLength(1);

    act(() => lastChartProps.current!.onDrawingSettings!("position-1"));
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(drawings()).toEqual([]);
  });

  it("recolours every level of a Fibonacci from the context menu's one colour", async () => {
    await renderReady(page());
    armTool("Fibonacci retracement tool");
    act(() => lastChartProps.current!.onFibPlace!({ time: 100, price: 100 }, { time: 200, price: 90 }));
    act(() => lastChartProps.current!.onDrawingColor!("fib-1", "#abcdef"));
    const levels = drawings()![0].levels as { color: string }[];
    expect(new Set(levels.map((l) => l.color))).toEqual(new Set(["#abcdef"]));
  });

  it("keeps every drawing in place across a timeframe change, without loading them again", async () => {
    const { fetchCoinDrawings } = await import("../api/client");
    vi.mocked(fetchCoinDrawings).mockClear();
    await withLong();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Timeframe 1H" }));
    });
    expect(drawings()).toEqual([expect.objectContaining({ id: "position-1", entry: 100 })]);
    expect(fetchCoinDrawings).toHaveBeenCalledTimes(1);
  });

  it("restores a Fibonacci and a position from the server, as another browser would see them", async () => {
    drawingsApi.server = [
      {
        kind: "fib",
        id: "fib-1",
        anchors: [
          { time: 100, price: 100 },
          { time: 200, price: 90 },
        ],
        levels: [{ ratio: 0.5, enabled: true, color: "#123456" }],
        extend_right: true,
        label_side: "left",
        line_width: 1,
      },
      { kind: "position", id: "position-2", side: "long", time: 100, entry: 100, stop: 99, target: 102, width_bars: 40 },
    ];
    await renderReady(page());
    expect(drawings()!.map((d) => d.id)).toEqual(["fib-1", "position-2"]);
  });
});

describe("drawing settings and failed saves (Story 32.5 review)", () => {
  const placeLong = (): void => {
    armTool("Long position tool");
    act(() => lastChartProps.current!.onPointClick!({ time: 100, price: 100 }));
  };

  it("closes the settings dialog when its drawing is deleted, and an id reused later does not reopen it", async () => {
    await renderReady(page());
    placeLong();
    act(() => lastChartProps.current!.onDrawingSettings!("position-1"));
    expect(screen.getByRole("dialog", { name: "Position settings" })).toBeInTheDocument();
    act(() => lastChartProps.current!.onDrawingDelete!("position-1"));
    expect(screen.queryByRole("dialog", { name: "Position settings" })).toBeNull();
    placeLong(); // the counter reuses position-1
    expect(screen.queryByRole("dialog", { name: "Position settings" })).toBeNull();
  });

  it("drops a settings request made before the instrument's precision is known, instead of parking it", async () => {
    drawingsApi.server = [
      { kind: "position", id: "position-1", side: "long", time: 100, entry: 100, stop: 99, target: 102, width_bars: 40 },
    ];
    mocks.precision = null;
    const view = await renderReady(page());
    act(() => lastChartProps.current!.onDrawingSettings!("position-1"));
    mocks.precision = { price: 2, size: 3 };
    view.rerender(page());
    expect(screen.queryByRole("dialog", { name: "Position settings" })).toBeNull();
  });

  it("a refused save (422) is shown, not retried, and is tried again by the next edit", async () => {
    vi.useFakeTimers();
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      drawingsApi.save.mockRejectedValue(Object.assign(new Error("PUT failed: 422"), { status: 422 }));
      await renderReady(page());
      armTool("Horizontal line tool");
      act(() => lastChartProps.current?.onPriceClick?.(123.5));
      await act(async () => {
        vi.advanceTimersByTime(SAVE_DEBOUNCE_MS + 1);
      });
      expect(screen.getByRole("alert")).toHaveTextContent("refused by the server (HTTP 422)");
      await act(async () => {
        vi.advanceTimersByTime(SAVE_RETRY_MS * 4);
      });
      expect(drawingsApi.save).toHaveBeenCalledTimes(1);

      drawingsApi.save.mockResolvedValue(undefined);
      act(() => lastChartProps.current?.onPriceLineDrag?.("hline-1", 130));
      await act(async () => {
        vi.advanceTimersByTime(SAVE_DEBOUNCE_MS + 1);
      });
      expect(drawingsApi.save).toHaveBeenCalledTimes(2);
      expect(screen.queryByRole("alert")).toBeNull();
    } finally {
      errors.mockRestore();
      vi.useRealTimers();
    }
  });

  it("a failed save (5xx) is shown and retried every SAVE_RETRY_MS until it lands", async () => {
    vi.useFakeTimers();
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      drawingsApi.save.mockRejectedValueOnce(Object.assign(new Error("PUT failed: 500"), { status: 500 }));
      await renderReady(page());
      armTool("Horizontal line tool");
      act(() => lastChartProps.current?.onPriceClick?.(123.5));
      await act(async () => {
        vi.advanceTimersByTime(SAVE_DEBOUNCE_MS + 1);
      });
      expect(screen.getByRole("alert")).toHaveTextContent("could not be saved");
      expect(drawingsApi.save).toHaveBeenCalledTimes(1);
      await act(async () => {
        vi.advanceTimersByTime(SAVE_RETRY_MS + 1);
      });
      expect(drawingsApi.save).toHaveBeenCalledTimes(2);
      expect(screen.queryByRole("alert")).toBeNull();
    } finally {
      errors.mockRestore();
      vi.useRealTimers();
    }
  });

  it("a save that fails after unmount is not re-flushed (no tight PUT loop)", async () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    drawingsApi.save.mockRejectedValue(new Error("network down"));
    const view = await renderReady(page());
    armTool("Horizontal line tool");
    act(() => lastChartProps.current?.onPriceClick?.(123.5));
    view.unmount(); // flushes once
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(drawingsApi.save).toHaveBeenCalledTimes(1);
    errors.mockRestore();
  });

  it("imports two old lines at the same price once", async () => {
    localStorage.setItem(
      "chart-hlines:BTC-USD-PERP.DYDX",
      JSON.stringify([{ id: "a", price: 5 }, { id: "b", price: 5 }, { id: "c", price: 6 }]),
    );
    await renderReady(page());
    expect(lastChartProps.current?.priceLines?.map((l) => l.price)).toEqual([5, 6]);
  });
});

describe("ChartPage layout that comes back as it was left (Story 32.6)", () => {
  const SAVED = layoutOf({
    bar_seconds: 3600,
    mode: "lines",
    volume: false,
    crosshair: false,
    pane_heights: { "RelativeStrengthIndex_period=14": 220 }, // keyed by instance id, as the chart reports
    visible_bars: 80,
  });
  const openLayoutMenu = () => fireEvent.click(screen.getByRole("button", { name: "Layout" }));
  const httpError = (status: number) => Object.assign(new Error(`HTTP ${status}`), { status });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("restores every saved field before the first candle request (return to coin)", () => {
    layoutApi.server[IID] = SAVED;
    render(page());

    expect(hooks.candlesBar.length).toBeGreaterThan(0);
    expect(hooks.candlesBar.every((bar) => bar === 3600)).toBe(true);
    expect(screen.getByRole("button", { name: "Timeframe 1H" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /^Chart type:/ })).toHaveAccessibleName(`Chart type: ${LINES_ITEM}`);
    expect(screen.getByRole("button", { name: "Crosshair toggle" })).toHaveAttribute("aria-pressed", "false");
    expect(lastChartProps.current!.panes!.map((p) => p.id)).toEqual([]); // volume off
    expect(lastChartProps.current!.initialPaneHeights).toEqual({ "RelativeStrengthIndex_period=14": 220 });
    expect(lastChartProps.current!.initialVisibleBars).toBe(80);
  });

  it("draws nothing and requests no candles until the layout has loaded", async () => {
    let answer: (value: unknown) => void = () => {};
    layoutApi.get.mockReturnValueOnce(new Promise((resolve) => (answer = resolve)));
    render(page());

    expect(screen.getByText(/Loading the layout/)).toBeInTheDocument();
    expect(screen.queryByTestId("chart-stub")).toBeNull();
    expect(hooks.candlesBar).toEqual([]);

    await act(async () => answer({ layout: SAVED, seeded: false }));
    expect(screen.getByTestId("chart-stub")).toBeInTheDocument();
    expect(hooks.candlesBar.every((bar) => bar === 3600)).toBe(true);
  });

  it("says so, and draws no chart, when the layout cannot be loaded", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    layoutApi.get.mockRejectedValueOnce(httpError(500));
    render(page());
    await act(async () => {});

    expect(screen.getByRole("alert")).toHaveTextContent(/layout of BTC-USD-PERP.DYDX could not be loaded/);
    expect(screen.queryByTestId("chart-stub")).toBeNull();
  });

  it("falls back to 1m with ONE console.error for a stale timeframe, and saves the corrected layout", async () => {
    vi.useFakeTimers();
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    layoutApi.server[IID] = { ...layoutOf(), bar_seconds: 30 };
    render(page());

    expect(screen.getByRole("button", { name: "Timeframe 1m" })).toHaveAttribute("aria-pressed", "true");
    expect(errors.mock.calls.filter((c) => String(c[0]).startsWith("chart layout"))).toHaveLength(1);
    await flushSave();
    expect(lastSaved().bar_seconds).toBe(60);
  });

  it("sends one PUT for a burst of five zoom changes", async () => {
    vi.useFakeTimers();
    render(page());
    for (const bars of [90, 91, 92, 93, 94]) {
      act(() => lastChartProps.current!.onVisibleBars!(bars));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(60);
      });
    }
    await flushSave();

    expect(layoutApi.save).toHaveBeenCalledTimes(1);
    expect(lastSaved().visible_bars).toBe(94);
  });

  it("saves the timeframe, mode, crosshair, volume and dragged pane heights as they change", async () => {
    vi.useFakeTimers();
    render(page());
    fireEvent.click(screen.getByRole("button", { name: "Timeframe 15m" }));
    switchToLines();
    fireEvent.click(screen.getByRole("button", { name: "Crosshair toggle" }));
    act(() => lastChartProps.current!.onPaneHeights!({ price: 480, volume: 150 }));
    await flushSave();

    expect(lastSaved()).toMatchObject({
      bar_seconds: 900,
      mode: "lines",
      crosshair: false,
      pane_heights: { price: 480, volume: 150 },
    });
    act(() => lastChartProps.current!.onPaneHeights!({ volume: 170 }));
    await flushSave();
    expect(lastSaved().pane_heights).toEqual({ price: 480, volume: 170 });
  });

  it("saves a placed fixed-range profile (kind, rows, value area, anchors) and restores it", async () => {
    vi.useFakeTimers();
    const bars = [1, 2, 3, 4, 5].map((t) => ({ time: t, open: t, high: t + 1, low: t, close: t + 1 }));
    mocks.candles = bars;
    mocks.volume = bars.map((b) => ({ time: b.time, value: 10 }));
    const first = render(page());
    armTool("Fixed range volume profile tool");
    act(() => lastChartProps.current!.onRangeSelect!({ time: 2, price: 1 }, { time: 4, price: 2 }));
    await flushSave();

    expect(lastSaved().volume_profile).toMatchObject({ kind: "fixed", rows: 24, value_area_pct: 70, start: 2, end: 4 });
    first.unmount();
    layoutApi.server[IID] = lastSaved();

    render(page());
    expect(lastChartProps.current!.volumeProfiles).toHaveLength(1);
    expect(lastChartProps.current!.volumeProfiles![0].profile.totalVolume).toBeCloseTo(30);
    expect(lastChartProps.current!.volumeProfiles![0].edges).toEqual({ startTime: 2, endTime: 4 });
  });

  it("drops a saved height whose indicator is gone with the next drag, keeping volume's", async () => {
    vi.useFakeTimers();
    layoutApi.server[IID] = layoutOf({ pane_heights: { volume: 150, "Gone_period=3": 90 } });
    render(page());
    act(() => lastChartProps.current!.onPaneHeights!({ price: 480 }));
    await flushSave();

    expect(lastSaved().pane_heights).toEqual({ price: 480, volume: 150 });
  });

  it("profiles a restored fixed range once a later candle load reaches it", () => {
    const bar = (t: number) => ({ time: t, open: t, high: t + 1, low: t, close: t + 1 });
    layoutApi.server[IID] = layoutOf({
      volume_profile: { ...BUILT_IN_LAYOUT.volume_profile, kind: "fixed", start: 2, end: 4 },
    });
    const load = (bars: ReturnType<typeof bar>[]): void => {
      mocks.candles = bars;
      mocks.volume = bars.map((b) => ({ time: b.time, value: 10 }));
    };
    load([bar(8), bar(9)]); // the first window does not reach the range
    const { rerender } = render(page());
    expect(lastChartProps.current!.volumeProfiles![0].profile.totalVolume).toBe(0);

    load([2, 3, 4, 8, 9].map(bar)); // scrolled back
    rerender(page());
    expect(lastChartProps.current!.volumeProfiles![0].profile.totalVolume).toBeCloseTo(30);
  });

  it("restores a saved visible-range profile with its row count and value area", () => {
    layoutApi.server[IID] = layoutOf({
      volume_profile: { ...BUILT_IN_LAYOUT.volume_profile, kind: "visible", rows: 48, value_area_pct: 60 },
    });
    render(page());

    expect(within(openOverlays()).getByRole("button", { name: "Remove visible range volume profile" })).toBeInTheDocument();
    expect((screen.getByLabelText("Row count") as HTMLInputElement).value).toBe("48");
    expect((screen.getByLabelText("Value area percent") as HTMLInputElement).value).toBe("60");
  });

  it("restores a saved session profile and saves its period", async () => {
    vi.useFakeTimers();
    layoutApi.server[IID] = layoutOf({
      volume_profile: { ...BUILT_IN_LAYOUT.volume_profile, kind: "session", session: "weekly", rows: 24 },
    });
    render(page());

    expect(mocks.sessionArgs).toMatchObject({ enabled: true, barSeconds: 300 }); // the weekly period's fetch
    await flushSave();
    expect(layoutApi.save).not.toHaveBeenCalled(); // what was restored is not saved back
  });

  it("restores the saved kind's session count, colours and toggles (DW-151/153)", () => {
    const D3 = Date.UTC(2024, 0, 3) / 1000;
    const bars = [D3 - 86_400, D3].map((t) => ({ time: t, open: 1, high: 2, low: 1, close: 2 }));
    mocks.candles = bars;
    mocks.session = { candles: bars, volume: bars.map((b) => ({ time: b.time, value: 5 })), completeFrom: null };
    layoutApi.server[IID] = layoutOf({
      volume_profile: {
        ...BUILT_IN_LAYOUT.volume_profile,
        kind: "session",
        sessions: 1,
        up_color: "#112233",
        down_color: "#445566",
        show_poc: false,
        show_value_area: false,
      },
    });
    render(page());
    openOverlays();

    expect((screen.getByLabelText("Sessions to render") as HTMLInputElement).value).toBe("1");
    expect((screen.getByLabelText("Up volume color") as HTMLInputElement).value).toBe("#112233");
    expect(screen.getByLabelText("Show POC")).not.toBeChecked();
    const drawn = lastChartProps.current!.volumeProfiles!.filter((p) => p.id.startsWith("session-"));
    expect(drawn).toHaveLength(1);
    expect(drawn[0]).toMatchObject({ upColor: "#112233", showPoc: false });
  });

  it("restores a visible-range profile's colours and toggles too", () => {
    layoutApi.server[IID] = layoutOf({
      volume_profile: { ...BUILT_IN_LAYOUT.volume_profile, kind: "visible", up_color: "#abcdef", show_value_area: false },
    });
    render(page());
    openOverlays();

    expect((screen.getByLabelText("Up volume color") as HTMLInputElement).value).toBe("#abcdef");
    expect(screen.getByLabelText("Show value area")).not.toBeChecked();
  });

  it("saves the session count, colours and toggles of the saved kind", async () => {
    vi.useFakeTimers();
    render(page());
    overlayClick("Add Session Volume Profile");
    fireEvent.change(screen.getByLabelText("Sessions to render"), { target: { value: "8" } });
    fireEvent.change(screen.getByLabelText("Down volume color"), { target: { value: "#010203" } });
    fireEvent.click(screen.getByLabelText("Show POC"));
    await flushSave();

    expect(lastSaved().volume_profile).toMatchObject({
      kind: "session",
      session: "daily",
      sessions: 8,
      up_color: BUILT_IN_LAYOUT.volume_profile.up_color,
      down_color: "#010203",
      show_poc: false,
      show_value_area: true,
    });
  });

  it("imports the old chart-timeframe / chart-volume keys once, then removes them", async () => {
    vi.useFakeTimers();
    localStorage.setItem(`chart-timeframe:${IID}`, "300");
    localStorage.setItem(`chart-volume:${IID}`, "off");
    render(page());

    expect(screen.getByRole("button", { name: "Timeframe 5m" })).toHaveAttribute("aria-pressed", "true");
    expect(lastChartProps.current!.panes!.map((p) => p.id)).toEqual([]);
    expect(localStorage.getItem(`chart-timeframe:${IID}`)).not.toBeNull(); // not before the save landed
    await flushSave();

    expect(lastSaved()).toMatchObject({ bar_seconds: 300, volume: false });
    expect(localStorage.getItem(`chart-timeframe:${IID}`)).toBeNull();
    expect(localStorage.getItem(`chart-volume:${IID}`)).toBeNull();
    cleanup();
    layoutApi.server[IID] = lastSaved();
    layoutApi.save.mockClear();
    render(page()); // a second open imports nothing and saves nothing
    await flushSave();
    expect(layoutApi.save).not.toHaveBeenCalled();
  });

  it("keeps a second coin's layout apart from the first coin's edits", () => {
    const first = render(page());
    fireEvent.click(screen.getByRole("button", { name: "Timeframe 4H" }));
    first.unmount();
    expect(lastSaved(IID).bar_seconds).toBe(14400);

    route.iid = "ETH-USD-PERP.DYDX";
    layoutApi.server["ETH-USD-PERP.DYDX"] = layoutOf({ bar_seconds: 300 });
    layoutApi.save.mockClear();
    const second = render(page());
    expect(screen.getByRole("button", { name: "Timeframe 5m" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Crosshair toggle" }));
    second.unmount();

    expect(layoutApi.save.mock.calls.map((c) => c[0])).toEqual(["ETH-USD-PERP.DYDX"]);
    expect(layoutApi.get.mock.calls.map((c) => c[0])).toEqual([IID, "ETH-USD-PERP.DYDX"]);
  });

  it("shows a refused save as an alert", async () => {
    vi.useFakeTimers();
    vi.spyOn(console, "error").mockImplementation(() => {});
    layoutApi.save.mockRejectedValue(httpError(422));
    render(page());
    fireEvent.click(screen.getByRole("button", { name: "Crosshair toggle" }));
    await flushSave();

    expect(screen.getByRole("alert")).toHaveTextContent(/layout was refused by the server \(HTTP 422\)/);
  });

  describe("the Layout menu", () => {
    it("Save as default asks first, naming what it overwrites, then saves a pending edit before the template", async () => {
      vi.useFakeTimers();
      const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
      render(page());
      fireEvent.click(screen.getByRole("button", { name: "Crosshair toggle" })); // a pending edit
      openLayoutMenu();
      fireEvent.click(screen.getByRole("menuitem", { name: "Save as default" }));
      await act(async () => {});

      expect(confirm.mock.calls[0][0]).toMatch(/overwrites the current default/);
      expect(layoutApi.saveDefault).toHaveBeenCalledWith(IID);
      expect(layoutApi.save.mock.invocationCallOrder[0]).toBeLessThan(layoutApi.saveDefault.mock.invocationCallOrder[0]);
      expect(screen.queryByRole("menuitem")).toBeNull(); // the menu closed
    });

    it("does nothing when the confirm is declined", async () => {
      vi.spyOn(window, "confirm").mockReturnValue(false);
      render(page());
      openLayoutMenu();
      fireEvent.click(screen.getByRole("menuitem", { name: "Save as default" }));
      openLayoutMenu();
      fireEvent.click(screen.getByRole("menuitem", { name: "Reset to default" }));
      await act(async () => {});

      expect(layoutApi.saveDefault).not.toHaveBeenCalled();
      expect(layoutApi.reset).not.toHaveBeenCalled();
    });

    it("Reset to default replaces the layout and indicators, reloads the picker and keeps the drawings", async () => {
      const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
      layoutApi.server[IID] = SAVED;
      layoutApi.reset.mockResolvedValue(layoutOf({ bar_seconds: 900, volume: true, crosshair: true }));
      drawingsApi.server = [{ kind: "hline", id: "hline-1", price: 5 }];
      await renderReady(page());
      const pickerLoads = vi.mocked(fetchCoinIndicatorConfig).mock.calls.length;
      drawingsApi.save.mockClear();

      openLayoutMenu();
      fireEvent.click(screen.getByRole("menuitem", { name: "Reset to default" }));
      await act(async () => {});

      expect(confirm.mock.calls[0][0]).toMatch(/Its drawings are kept/);
      expect(screen.getByRole("button", { name: "Timeframe 15m" })).toHaveAttribute("aria-pressed", "true");
      expect(lastChartProps.current!.panes!.map((p) => p.id)).toEqual(["volume"]);
      expect(lastChartProps.current!.priceLines).toHaveLength(1); // drawings untouched
      expect(vi.mocked(fetchCoinIndicatorConfig).mock.calls.length).toBeGreaterThan(pickerLoads);
      expect(drawingsApi.save).not.toHaveBeenCalled();
      expect(layoutApi.save).not.toHaveBeenCalled(); // the reset layout is the server's, not saved back
    });

    it("shows a failed Save as default or Reset as an alert", async () => {
      vi.spyOn(console, "error").mockImplementation(() => {});
      vi.spyOn(window, "confirm").mockReturnValue(true);
      layoutApi.saveDefault.mockRejectedValue(httpError(404));
      layoutApi.reset.mockRejectedValue(httpError(500));
      render(page());

      openLayoutMenu();
      fireEvent.click(screen.getByRole("menuitem", { name: "Save as default" }));
      await act(async () => {});
      expect(screen.getByRole("alert")).toHaveTextContent(/could not be saved as the default/);

      openLayoutMenu();
      fireEvent.click(screen.getByRole("menuitem", { name: "Reset to default" }));
      await act(async () => {});
      expect(screen.getByRole("alert")).toHaveTextContent(/could not be reset to the default/);
    });
  });
});

describe("ChartPage Anchored VP and Anchored VWAP drawings (Story 32.7)", () => {
  // Four 1-minute bars; the volume of each is its time / 100.
  const bar = (t: number, low: number, high: number) => ({ time: t, open: low, high, low, close: high });
  const bars = [bar(100, 10, 12), bar(200, 11, 13), bar(300, 12, 14), bar(400, 13, 15)];
  const volumes = (list = bars) => list.map((b) => ({ time: b.time, value: b.time / 100 }));
  const avpProfiles = () => lastChartProps.current!.volumeProfiles!.filter((p) => p.id.startsWith("avp-"));
  const place = (toolName: string, time: number) => {
    armTool(toolName);
    act(() => lastChartProps.current!.onPointClick!({ time, price: 12 }));
  };

  beforeEach(() => {
    mocks.candles = bars;
    mocks.volume = volumes();
  });

  it("one click places an Anchored VP: the engine's profile from that bar to the latest, and the tool disarms", async () => {
    await renderReady(page());
    place("Anchored volume profile tool", 200);

    expect(screen.getByRole("button", { name: "Cursor tool" })).toHaveAttribute("aria-pressed", "true");
    const spec = lastChartProps.current!.drawings!.find((d) => d.kind === "anchored_vp")!;
    expect(spec).toMatchObject({ id: "anchored_vp-1", time: 200, rows: 24, value_area_pct: 70 });
    expect(spec.up_color).toBe(CHART_TOKENS["--chart-up"]);
    expect(spec.anchorPrice).toBe(15); // the profile's top
    expect(avpProfiles()).toHaveLength(1);
    expect(avpProfiles()[0]).toMatchObject({ id: "avp-anchored_vp-1", xAnchor: { time: 200 }, width: { toTime: 400 }, throughEndBar: true });
    // volume of bars 200, 300, 400 = 2 + 3 + 4
    expect(avpProfiles()[0].profile.totalVolume).toBeCloseTo(9);
  });

  it("follows new bars: the profile grows to the latest bar", async () => {
    const { rerender } = await renderReady(page());
    place("Anchored volume profile tool", 200);

    const more = [...bars, bar(500, 14, 16)];
    mocks.candles = more;
    mocks.volume = volumes(more);
    rerender(page());

    expect(avpProfiles()[0]).toMatchObject({ xAnchor: { time: 200 }, width: { toTime: 500 } });
    expect(avpProfiles()[0].profile.totalVolume).toBeCloseTo(14);
  });

  it("a click on the forming bar draws at once: both include the live bar, and it closes into history unchanged", async () => {
    mocks.liveBar = { time: 500, open: 14, high: 16, low: 14, close: 16, volume: 5 };
    const { rerender } = await renderReady(page());
    place("Anchored volume profile tool", 500);
    place("Anchored VWAP tool", 400);

    expect(avpProfiles()).toHaveLength(1);
    expect(avpProfiles()[0]).toMatchObject({ xAnchor: { time: 500 }, width: { toTime: 500 } });
    expect(avpProfiles()[0].profile.totalVolume).toBeCloseTo(5);
    const vwap = () =>
      lastChartProps.current!.drawings!.find((d) => d.kind === "anchored_vwap")! as unknown as { points: { time: number }[] };
    expect(vwap().points.map((p) => p.time)).toEqual([400, 500]);

    // The live bar closes: history holds it now, and the drawings read the same bars.
    const closed = [...bars, bar(500, 14, 16)];
    mocks.candles = closed;
    mocks.volume = [...volumes(bars), { time: 500, value: 5 }];
    mocks.liveBar = null;
    rerender(page());
    expect(avpProfiles()[0]).toMatchObject({ xAnchor: { time: 500 }, width: { toTime: 500 } });
    expect(avpProfiles()[0].profile.totalVolume).toBeCloseTo(5);
    expect(vwap().points.map((p) => p.time)).toEqual([400, 500]);
  });

  it("an anchor in a gap slot is drawn on the real bar the profile starts at, not on the slot", async () => {
    mocks.candles = [bar(100, 10, 12), bar(200, 11, 13), { time: 250 }, bar(300, 12, 14), bar(400, 13, 15)];
    drawingsApi.server = [
      { kind: "anchored_vp", id: "anchored_vp-1", time: 250, rows: 24, value_area_pct: 70, up_color: "#25a399", down_color: "#ef5350" },
    ];
    await renderReady(page());

    expect(lastChartProps.current!.drawings!.find((d) => d.kind === "anchored_vp")).toMatchObject({ time: 200 });
    expect(avpProfiles()[0].xAnchor).toEqual({ time: 200 });
  });

  it("opens the Anchored VP's and VWAP's settings before the instrument's precision is known (their dialogs print no price)", async () => {
    drawingsApi.server = [
      { kind: "anchored_vp", id: "anchored_vp-1", time: 200, rows: 24, value_area_pct: 70, up_color: "#25a399", down_color: "#ef5350" },
      { kind: "anchored_vwap", id: "anchored_vwap-1", time: 100, source: "hlc3", bands: false, band_color: "#b26a00" },
    ];
    mocks.precision = null;
    await renderReady(page());

    act(() => lastChartProps.current!.onDrawingSettings!("anchored_vwap-1"));
    const vwapDialog = screen.getByRole("dialog", { name: "Anchored VWAP settings" });
    fireEvent.click(within(vwapDialog).getByRole("button", { name: "Cancel" }));
    act(() => lastChartProps.current!.onDrawingSettings!("anchored_vp-1"));
    expect(screen.getByRole("dialog", { name: "Anchored volume profile settings" })).toBeInTheDocument();
  });

  it("the VWAP's line colour picker shows the colour the line is drawn in when none is stored", async () => {
    drawingsApi.server = [{ kind: "anchored_vwap", id: "anchored_vwap-1", time: 100, source: "hlc3", bands: false, band_color: "#b26a00" }];
    await renderReady(page());

    act(() => lastChartProps.current!.onDrawingSettings!("anchored_vwap-1"));
    const dialog = screen.getByRole("dialog", { name: "Anchored VWAP settings" });
    expect(within(dialog).getByLabelText("Line colour")).toHaveValue(CHART_TOKENS["--chart-drawing"].toLowerCase());
    fireEvent.click(within(dialog).getByRole("button", { name: "Apply" }));
    expect(lastChartProps.current!.drawings!.find((d) => d.id === "anchored_vwap-1")).not.toHaveProperty("color");
  });

  it("saves only what the wire holds (no computed rows) and restores both kinds after a reload", async () => {
    vi.useFakeTimers();
    try {
      const first = await renderReady(page());
      place("Anchored volume profile tool", 200);
      place("Anchored VWAP tool", 300);
      await act(async () => {
        vi.advanceTimersByTime(SAVE_DEBOUNCE_MS + 1);
      });

      const saved = drawingsApi.save.mock.calls.at(-1)![1] as Record<string, unknown>[];
      expect(saved).toEqual([
        {
          kind: "anchored_vp",
          id: "anchored_vp-1",
          time: 200,
          rows: 24,
          value_area_pct: 70,
          up_color: CHART_TOKENS["--chart-up"],
          down_color: CHART_TOKENS["--chart-down"],
        },
        {
          kind: "anchored_vwap",
          id: "anchored_vwap-1",
          time: 300,
          source: "hlc3",
          bands: false,
          color: CHART_TOKENS["--chart-drawing"],
          band_color: CHART_TOKENS["--chart-pane-4"],
        },
      ]);
      first.unmount();

      drawingsApi.server = saved;
      await renderReady(page());
      expect(avpProfiles()).toHaveLength(1);
      expect(lastChartProps.current!.drawings!.map((d) => d.kind)).toEqual(["anchored_vp", "anchored_vwap"]);
      expect(lastChartProps.current!.legendExtras).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("an Anchored VWAP carries the line from its anchor and the current value in the legend at the instrument precision", async () => {
    await renderReady(page());
    place("Anchored VWAP tool", 100);

    const spec = lastChartProps.current!.drawings!.find((d) => d.kind === "anchored_vwap")! as unknown as {
      points: { time: number; vwap: number }[];
    };
    expect(spec.points.map((p) => p.time)).toEqual([100, 200, 300, 400]);
    // hlc3 = (high + low + close) / 3 per bar; volumes 1..4
    const hlc3 = bars.map((b) => (b.high + b.low + b.close) / 3);
    const expected = hlc3.reduce((sum, v, i) => sum + v * (i + 1), 0) / 10;
    const legend = lastChartProps.current!.legendExtras![0];
    expect(legend.label).toBe("AVWAP (hlc3)");
    expect(legend.value).toBeCloseTo(expected, 10);
    expect(legend.format(legend.value!)).toBe(expected.toFixed(2)); // precision 2
  });

  it("leaves no point and no legend value for a VWAP over zero-volume bars", async () => {
    mocks.volume = bars.map((b) => ({ time: b.time, value: 0 }));
    await renderReady(page());
    place("Anchored VWAP tool", 100);

    const spec = lastChartProps.current!.drawings!.find((d) => d.kind === "anchored_vwap")! as unknown as { points: unknown[] };
    expect(spec.points).toEqual([]);
    expect(lastChartProps.current!.legendExtras![0].value).toBeNull();
  });

  it("draws nothing for an anchor older than the loaded bars, until a scroll-back pages them in", async () => {
    drawingsApi.server = [
      { kind: "anchored_vwap", id: "anchored_vwap-1", time: 50, source: "hlc3", bands: false, band_color: "#b26a00" },
      { kind: "anchored_vp", id: "anchored_vp-1", time: 50, rows: 24, value_area_pct: 70, up_color: "#25a399", down_color: "#ef5350" },
    ];
    const { rerender } = await renderReady(page());
    const specs = lastChartProps.current!.drawings! as unknown as { kind: string; points?: unknown[]; anchorPrice?: number | null }[];
    expect(specs.find((d) => d.kind === "anchored_vwap")!.points).toEqual([]);
    expect(specs.find((d) => d.kind === "anchored_vp")!.anchorPrice).toBeNull();
    expect(avpProfiles()).toHaveLength(0);

    const older = [bar(40, 9, 11), ...bars];
    mocks.candles = older;
    mocks.volume = volumes(older);
    rerender(page());
    expect(avpProfiles()).toHaveLength(1);
    expect(avpProfiles()[0].xAnchor).toEqual({ time: 40 }); // drawn on the bar at or before the anchor... which is 40
  });

  it("dragging the anchor handle moves the anchor to the pointer's bar and the profile with it", async () => {
    await renderReady(page());
    place("Anchored volume profile tool", 200);

    act(() => lastChartProps.current!.onDrawingDrag!("anchored_vp-1", "anchor", { price: 11, time: 300, barsSince: () => null }));

    expect(avpProfiles()[0].xAnchor).toEqual({ time: 300 });
    expect(avpProfiles()[0].profile.totalVolume).toBeCloseTo(7); // bars 300 and 400
  });

  it("shows neither in Lines mode (their bars are candles)", async () => {
    await renderReady(page());
    place("Anchored volume profile tool", 200);
    place("Anchored VWAP tool", 100);

    switchToLines();

    expect(lastChartProps.current!.drawings).toEqual([]);
    expect(avpProfiles()).toHaveLength(0);
    expect(lastChartProps.current!.legendExtras).toEqual([]);
    expect(toolControl("Anchored volume profile tool")).toBeDisabled();
  });

  it("the settings modal edits the VWAP's source and bands, and the VP's rows and value area", async () => {
    await renderReady(page());
    place("Anchored VWAP tool", 100);
    place("Anchored volume profile tool", 200);

    act(() => lastChartProps.current!.onDrawingSettings!("anchored_vwap-1"));
    const vwapDialog = screen.getByRole("dialog", { name: "Anchored VWAP settings" });
    fireEvent.change(within(vwapDialog).getByLabelText("Source"), { target: { value: "close" } });
    fireEvent.click(within(vwapDialog).getByLabelText("Bands on"));
    fireEvent.click(within(vwapDialog).getByRole("button", { name: "Apply" }));
    expect(lastChartProps.current!.drawings!.find((d) => d.id === "anchored_vwap-1")).toMatchObject({ source: "close", bands: true });
    expect(lastChartProps.current!.legendExtras![0].label).toBe("AVWAP (close)");

    act(() => lastChartProps.current!.onDrawingSettings!("anchored_vp-1"));
    const vpDialog = screen.getByRole("dialog", { name: "Anchored volume profile settings" });
    fireEvent.change(within(vpDialog).getByLabelText("Rows"), { target: { value: "8" } });
    fireEvent.change(within(vpDialog).getByLabelText("Value area %"), { target: { value: "60" } });
    fireEvent.click(within(vpDialog).getByRole("button", { name: "Apply" }));
    expect(lastChartProps.current!.drawings!.find((d) => d.id === "anchored_vp-1")).toMatchObject({ rows: 8, value_area_pct: 60 });
    expect(avpProfiles()[0].profile.rows).toHaveLength(8);
  });

  it("a refused VP setting keeps the dialog open with the reason and changes nothing", async () => {
    await renderReady(page());
    place("Anchored volume profile tool", 200);
    act(() => lastChartProps.current!.onDrawingSettings!("anchored_vp-1"));
    const dialog = screen.getByRole("dialog", { name: "Anchored volume profile settings" });

    fireEvent.change(within(dialog).getByLabelText("Rows"), { target: { value: "1" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Apply" }));

    expect(within(dialog).getByRole("alert")).toHaveTextContent(/Rows must be/);
    expect(lastChartProps.current!.drawings!.find((d) => d.id === "anchored_vp-1")).toMatchObject({ rows: 24 });
  });

  it("the context menu's colour is the VWAP's colour and the VP's up colour; Delete removes the drawing and its profile", async () => {
    await renderReady(page());
    place("Anchored VWAP tool", 100);
    place("Anchored volume profile tool", 200);

    act(() => lastChartProps.current!.onDrawingColor!("anchored_vwap-1", "#112233"));
    act(() => lastChartProps.current!.onDrawingColor!("anchored_vp-1", "#445566"));
    expect(lastChartProps.current!.drawings!.find((d) => d.id === "anchored_vwap-1")).toMatchObject({ color: "#112233" });
    expect(lastChartProps.current!.drawings!.find((d) => d.id === "anchored_vp-1")).toMatchObject({ up_color: "#445566" });
    expect(avpProfiles()[0]).toMatchObject({ upColor: "#445566" });

    act(() => lastChartProps.current!.onDrawingDelete!("anchored_vp-1"));
    expect(avpProfiles()).toHaveLength(0);
    expect(lastChartProps.current!.drawings!.map((d) => d.id)).toEqual(["anchored_vwap-1"]);
  });
});

describe("ChartPage Auto Anchored profile and TPO (Story 32.7)", () => {
  const D1 = Date.UTC(2024, 0, 3) / 1000; // a Wednesday; its week starts Mon 2024-01-01
  const D2 = D1 + 86_400;
  const bar = (t: number, low: number, high: number) => ({ time: t, open: low, high, low, close: high });
  // Chart bars over two sessions; the highest high is on D1 + 120 and the lowest low on D2 + 60.
  const chartBars = [bar(D1, 10, 12), bar(D1 + 120, 20, 30), bar(D2, 15, 18), bar(D2 + 60, 5, 17)];
  const autoSpec = () => lastChartProps.current!.volumeProfiles!.find((p) => p.id === "auto-anchored");
  const add = (name: string) => overlayClick(name);

  beforeEach(() => {
    mocks.candles = chartBars;
    mocks.volume = chartBars.map((b) => ({ time: b.time, value: 2 }));
    mocks.session = {
      candles: chartBars.filter((b) => b.time >= D2),
      volume: chartBars.filter((b) => b.time >= D2).map((b) => ({ time: b.time, value: 2 })),
      completeFrom: null,
    };
  });

  it("auto at 1m is the current session: fetched from the day's start, the profile from the anchor to the latest bar, with its marker", () => {
    render(page());
    add("Add Auto Anchored Volume Profile");

    expect(mocks.sessionArgs).toMatchObject({ enabled: true, sinceSeconds: D2, barSeconds: 60 });
    expect(autoSpec()).toMatchObject({ xAnchor: { time: D2 }, width: { toTime: D2 + 60 } });
    expect(autoSpec()!.profile.totalVolume).toBeCloseTo(4); // two bars of volume 2
    expect(lastChartProps.current!.anchorMarkerTime).toBe(D2);
  });

  it("re-anchors when a live bar crosses a session boundary: the profile, the fetch and the marker move", () => {
    const { rerender } = render(page());
    add("Add Auto Anchored Volume Profile");
    expect(lastChartProps.current!.anchorMarkerTime).toBe(D2);

    const D3 = D2 + 86_400;
    const more = [...chartBars, bar(D3, 16, 19)];
    mocks.candles = more;
    mocks.volume = more.map((b) => ({ time: b.time, value: 2 }));
    mocks.session = { candles: more.filter((b) => b.time >= D2), volume: more.filter((b) => b.time >= D2).map((b) => ({ time: b.time, value: 2 })), completeFrom: null };
    rerender(page());

    expect(mocks.sessionArgs.sinceSeconds).toBe(D3);
    expect(lastChartProps.current!.anchorMarkerTime).toBe(D3);
    expect(autoSpec()).toMatchObject({ xAnchor: { time: D3 } });
  });

  it("re-resolves by the new bar size's rule on a timeframe change: 1D is a month (fetched at 15m bars)", () => {
    layoutApi.server[IID] = layoutOf({ bar_seconds: 86_400 });
    render(page());
    add("Add Auto Anchored Volume Profile");

    expect(mocks.sessionArgs).toMatchObject({ enabled: true, sinceSeconds: Date.UTC(2024, 0, 1) / 1000, barSeconds: 900 });
  });

  it("at 1W starts on the bar holding the month's start (not the first bar opening after it), marker there, one bar wide at least", () => {
    const W1 = Date.UTC(2024, 0, 29) / 1000; // Monday; its week holds Thu 2024-02-01
    const W2 = Date.UTC(2024, 1, 5) / 1000;
    const weekly = [bar(W1 - 7 * 86_400, 10, 12), bar(W1, 11, 13), bar(W2, 12, 14)];
    mocks.candles = weekly;
    mocks.volume = weekly.map((b) => ({ time: b.time, value: 2 }));
    const feb = Date.UTC(2024, 1, 1) / 1000;
    mocks.session = { candles: [bar(feb, 11, 13), bar(W2, 12, 14)], volume: [{ time: feb, value: 2 }, { time: W2, value: 2 }], completeFrom: null };
    layoutApi.server[IID] = layoutOf({ bar_seconds: 604_800 });
    render(page());
    add("Add Auto Anchored Volume Profile");

    expect(mocks.sessionArgs.sinceSeconds).toBe(feb);
    expect(autoSpec()).toMatchObject({ xAnchor: { time: W1 }, width: { toTime: W2 }, throughEndBar: true });
    expect(lastChartProps.current!.anchorMarkerTime).toBe(W1);
  });

  it("highest high anchors at the chart's highest-high bar and reads the chart's own bars (no session fetch)", () => {
    render(page());
    add("Add Auto Anchored Volume Profile");
    fireEvent.change(screen.getByLabelText("Anchor"), { target: { value: "highest_high" } });

    expect(mocks.sessionArgs.enabled).toBe(false);
    expect(autoSpec()).toMatchObject({ xAnchor: { time: D1 + 120 }, width: { toTime: D2 + 60 } });
    expect(lastChartProps.current!.anchorMarkerTime).toBe(D1 + 120);
    // three bars from the anchor on, volume 2 each
    expect(autoSpec()!.profile.totalVolume).toBeCloseTo(6);

    fireEvent.change(screen.getByLabelText("Anchor"), { target: { value: "lowest_low" } });
    expect(autoSpec()).toMatchObject({ xAnchor: { time: D2 + 60 } });
  });

  it("is one session-type profile: adding the TPO replaces it, and Remove clears the marker", () => {
    render(page());
    add("Add Auto Anchored Volume Profile");
    add("Add Time Price Opportunity (TPO)");

    expect(autoSpec()).toBeUndefined();
    expect(lastChartProps.current!.anchorMarkerTime).toBeNull();
    expect(screen.getAllByRole("group", { name: "Session volume profile settings" })).toHaveLength(1);
  });

  it("does not draw a calendar anchor whose period the fetch did not fully cover", () => {
    mocks.session = { ...mocks.session, completeFrom: D2 + 86_400 };
    render(page());
    add("Add Auto Anchored Volume Profile");

    expect(autoSpec()).toBeUndefined();
    expect(screen.getByText(/Not drawn yet/)).toBeInTheDocument();
  });

  it("saves the Auto Anchored preset to the layout and restores it", async () => {
    vi.useFakeTimers();
    const first = render(page());
    add("Add Auto Anchored Volume Profile");
    fireEvent.change(screen.getByLabelText("Anchor"), { target: { value: "week" } });
    await flushSave();

    expect(lastSaved().volume_profile).toMatchObject({ kind: "auto", anchor: "week", rows: 24, value_area_pct: 70 });
    first.unmount();
    layoutApi.server[IID] = lastSaved();
    layoutApi.save.mockClear();

    render(page());
    openOverlays();
    expect((screen.getByLabelText("Anchor") as HTMLSelectElement).value).toBe("week");
    expect(mocks.sessionArgs).toMatchObject({ enabled: true, sinceSeconds: Date.UTC(2024, 0, 1) / 1000, barSeconds: 300 });
    await flushSave();
    expect(layoutApi.save).not.toHaveBeenCalled(); // what was restored is not saved back
  });

  describe("TPO", () => {
    // Half-hour bars of one session, 12 of them.
    const tpoBars = Array.from({ length: 12 }, (_, i) => {
      const low = 100 + (i % 4) * 2;
      return bar(D2 + i * 1800, low, low + 5);
    });
    beforeEach(() => {
      mocks.session = {
        candles: tpoBars,
        volume: tpoBars.map((b) => ({ time: b.time, value: 0 })), // volume plays no part in a TPO
        completeFrom: null,
      };
      mocks.candles = [...chartBars.slice(0, 2), ...tpoBars];
    });
    const sessions = () => lastChartProps.current!.volumeProfiles!.filter((p) => p.id.startsWith("session-"));

    it("counts 30-minute candle touches per day through the one engine and the one primitive", () => {
      render(page());
      add("Add Time Price Opportunity (TPO)");

      expect(mocks.sessionArgs).toMatchObject({ enabled: true, barSeconds: 1800 });
      expect(sessions()).toHaveLength(1);
      const [tpo] = sessions();
      expect(tpo.tpo!.letters).toBe(false);
      expect(tpo.tpo!.rows.reduce((n, r) => n + r.count, 0)).toBe(tpo.profile.totalVolume);
      expect(tpo.profile.totalVolume).toBeGreaterThan(12); // zero-volume bars still count, each in every row it touches
    });

    it("outlines the initial balance: the first hour, 2 x 30m, high to low", () => {
      render(page());
      add("Add Time Price Opportunity (TPO)");

      // the first two bars: lows 100 and 102, highs 105 and 107
      expect(sessions()[0].initialBalance).toEqual({ high: 107, low: 100 });

      fireEvent.change(screen.getByLabelText("Initial balance minutes"), { target: { value: "30" } });
      expect(sessions()[0].initialBalance).toEqual({ high: 105, low: 100 }); // one bar
    });

    it("shows letters when asked, off by default", () => {
      render(page());
      add("Add Time Price Opportunity (TPO)");
      expect(sessions()[0].tpo!.letters).toBe(false);

      fireEvent.click(screen.getByLabelText("TPO letters"));
      expect(sessions()[0].tpo!.letters).toBe(true);
    });

    it("takes a day (the default) or another period", () => {
      render(page());
      add("Add Time Price Opportunity (TPO)");

      fireEvent.change(screen.getByLabelText("Profile period"), { target: { value: "weekly" } });
      expect(sessions()).toHaveLength(1);
    });

    it("saves kind, period, initial balance and letters, and restores them", async () => {
      vi.useFakeTimers();
      const first = render(page());
      add("Add Time Price Opportunity (TPO)");
      fireEvent.change(screen.getByLabelText("Initial balance minutes"), { target: { value: "90" } });
      fireEvent.click(screen.getByLabelText("TPO letters"));
      await flushSave();

      expect(lastSaved().volume_profile).toMatchObject({ kind: "tpo", session: "daily", ib_minutes: 90, letters: true, rows: 48 });
      first.unmount();
      layoutApi.server[IID] = lastSaved();
      layoutApi.save.mockClear();

      render(page());
      expect(sessions()[0].tpo!.letters).toBe(true);
      openOverlays();
      expect((screen.getByLabelText("Initial balance minutes") as HTMLInputElement).value).toBe("90");
      expect(mocks.sessionArgs.barSeconds).toBe(1800);
      await flushSave();
      expect(layoutApi.save).not.toHaveBeenCalled();
    });

    it("ignores an initial balance the server would refuse", () => {
      render(page());
      add("Add Time Price Opportunity (TPO)");

      fireEvent.change(screen.getByLabelText("Initial balance minutes"), { target: { value: "5000" } });
      fireEvent.change(screen.getByLabelText("Initial balance minutes"), { target: { value: "0" } });
      fireEvent.blur(screen.getByLabelText("Initial balance minutes"));

      expect((screen.getByLabelText("Initial balance minutes") as HTMLInputElement).value).toBe("60");
    });

    it("an old layout without the new keys restores an SVP exactly as before", () => {
      const { anchor: _a, ib_minutes: _i, letters: _l, ...old } = BUILT_IN_LAYOUT.volume_profile;
      layoutApi.server[IID] = { ...layoutOf(), volume_profile: { ...old, kind: "session", session: "daily" } };
      render(page());

      expect(within(openOverlays()).getByRole("region", { name: "Session Volume Profile" })).toBeInTheDocument();
      expect(screen.queryByLabelText("Anchor")).toBeNull();
    });
  });

  it("lets the initial balance field be cleared and retyped without snapping back", () => {
    render(page());
    add("Add Time Price Opportunity (TPO)");
    const input = screen.getByLabelText("Initial balance minutes") as HTMLInputElement;

    fireEvent.change(input, { target: { value: "" } });
    expect(input.value).toBe(""); // the draft is kept while empty
    fireEvent.change(input, { target: { value: "45" } });
    expect(input.value).toBe("45");
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.blur(input);
    expect(input.value).toBe("45"); // blur restores the last committed value
  });

  it("a TPO counts a candle that has no volume datum", () => {
    mocks.session = {
      candles: [bar(D2, 100, 105), bar(D2 + 1800, 100, 105)],
      volume: [{ time: D2, value: 3 }], // the second candle has no volume row
      completeFrom: null,
    };
    mocks.candles = mocks.session.candles;
    render(page());
    add("Add Time Price Opportunity (TPO)");

    const tpo = lastChartProps.current!.volumeProfiles!.find((p) => p.id.startsWith("session-"))!;
    expect(tpo.profile.totalVolume).toBe(2 * tpo.profile.rows.length); // both candles touch every row
  });
});

describe("ChartPage anchored drawing past the newest bar (Story 32.7 review)", () => {
  it("omits an anchored drawing whose anchor is after the newest displayed bar", async () => {
    mocks.candles = [100, 200].map((t) => ({ time: t, open: 1, high: 2, low: 1, close: 2 }));
    mocks.volume = [100, 200].map((t) => ({ time: t, value: 1 }));
    drawingsApi.server = [
      { kind: "anchored_vwap", id: "anchored_vwap-1", time: 900, source: "hlc3", bands: false, band_color: "#b26a00" },
      { kind: "anchored_vp", id: "anchored_vp-1", time: 900, rows: 24, value_area_pct: 70, up_color: "#25a399", down_color: "#ef5350" },
    ];
    await renderReady(page());

    expect(lastChartProps.current!.drawings).toEqual([]);
    expect(lastChartProps.current!.volumeProfiles).toEqual([]);
    expect(lastChartProps.current!.legendExtras).toEqual([]);
  });

  it("snaps the anchor on the chart's own bars even when the bar has no volume datum", async () => {
    mocks.candles = [100, 200, 300].map((t) => ({ time: t, open: 1, high: 2, low: 1, close: 2 }));
    mocks.volume = [100, 300].map((t) => ({ time: t, value: 1 })); // 200 has none
    drawingsApi.server = [
      { kind: "anchored_vp", id: "anchored_vp-1", time: 200, rows: 4, value_area_pct: 70, up_color: "#25a399", down_color: "#ef5350" },
    ];
    await renderReady(page());

    expect(lastChartProps.current!.volumeProfiles![0].xAnchor).toEqual({ time: 200 });
  });
});

describe("ChartPage volume footprint (Story 32.8)", () => {
  const ITEMS = [{ t: 60_000 }, { t: 120_000 }];
  const lastFootprintCall = () => hooks.footprint.at(-1)!;
  async function openIndicators(): Promise<HTMLElement> {
    render(page());
    await act(async () => {}); // catalog load
    fireEvent.click(within(screen.getByRole("toolbar", { name: "Chart controls" })).getByRole("button", { name: "Indicators" }));
    return screen.getByRole("dialog", { name: "Indicators" });
  }
  const legend = (action: "hide" | "settings" | "remove") => act(() => lastChartProps.current!.onLegendAction!(action, "footprint"));

  it("is off by default: the hook is disabled (no request) and the chart gets no footprint", async () => {
    const dialog = await openIndicators();

    expect(within(dialog).getByRole("checkbox", { name: /Footprint/ })).not.toBeChecked();
    expect(hooks.footprint.every((c) => !c.enabled)).toBe(true);
    expect(lastChartProps.current!.footprint).toBeNull();
  });

  it("is pinned next to Volume, and switching it on fetches, draws and saves it in the coin's layout", async () => {
    footprintResult.current = { items: ITEMS, precision: { price: 2, size: 6 }, error: null };
    const dialog = await openIndicators();
    const pinned = within(dialog).getByRole("checkbox", { name: "Volume" }).closest(".indicator-dialog-pinned")!;

    fireEvent.click(within(pinned as HTMLElement).getByRole("checkbox", { name: /Footprint/ }));

    expect(lastFootprintCall()).toEqual({ enabled: true, rowTicks: 0, barSeconds: 60 });
    expect(lastChartProps.current!.footprint).toMatchObject({ items: ITEMS, precision: { price: 2, size: 6 }, settings: { on: true } });
    cleanup(); // flushes the pending save
    expect(lastSaved().footprint).toMatchObject({ on: true, row_ticks: 0, mode: "bid_ask" });
  });

  it("restores the on state from the layout, and the legend x turns it off with no further request", () => {
    layoutApi.server[IID] = layoutOf({ footprint: { ...BUILT_IN_LAYOUT.footprint, on: true } });
    render(page());
    expect(lastFootprintCall().enabled).toBe(true);

    legend("remove");

    expect(lastFootprintCall().enabled).toBe(false);
    expect(lastChartProps.current!.footprint).toBeNull();
    cleanup();
    expect(lastSaved().footprint.on).toBe(false);
  });

  it("is not fetched or drawn in Lines mode, and says so in the Indicators dialog", async () => {
    layoutApi.server[IID] = layoutOf({ footprint: { ...BUILT_IN_LAYOUT.footprint, on: true } });
    const dialog = await openIndicators();

    switchToLines();

    expect(lastFootprintCall().enabled).toBe(false);
    expect(lastChartProps.current!.footprint).toBeNull();
    const footprintRow = within(dialog).getByRole("checkbox", { name: /Footprint/ }).closest("label") as HTMLElement;
    expect(within(footprintRow).getByText("Candles mode only")).toBeInTheDocument();
  });

  it("opens its settings from the legend gear; a style-only Apply keeps the row size (no refetch)", () => {
    layoutApi.server[IID] = layoutOf({ footprint: { ...BUILT_IN_LAYOUT.footprint, on: true } });
    render(page());
    legend("settings");
    const dialog = screen.getByRole("dialog", { name: "Footprint settings" });

    fireEvent.change(within(dialog).getByLabelText("Display mode"), { target: { value: "delta" } });
    fireEvent.click(within(dialog).getByLabelText("Show numbers"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Apply" }));

    expect(screen.queryByRole("dialog", { name: "Footprint settings" })).toBeNull();
    expect(new Set(hooks.footprint.filter((c) => c.enabled).map((c) => c.rowTicks))).toEqual(new Set([0]));
    expect(lastChartProps.current!.footprint!.settings).toMatchObject({ mode: "delta", text: false, row_ticks: 0 });
    cleanup();
    expect(lastSaved().footprint).toMatchObject({ on: true, mode: "delta", text: false, row_ticks: 0 });
  });

  it("refetches with the new row size when Apply changes it, and refuses a row size it cannot use", () => {
    layoutApi.server[IID] = layoutOf({ footprint: { ...BUILT_IN_LAYOUT.footprint, on: true } });
    render(page());
    legend("settings");
    const dialog = screen.getByRole("dialog", { name: "Footprint settings" });

    fireEvent.change(within(dialog).getByLabelText("Row size"), { target: { value: "fixed" } });
    fireEvent.change(within(dialog).getByLabelText("Ticks per row"), { target: { value: "0" } });
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Row size must be a whole number of ticks");
    expect(within(dialog).getByRole("button", { name: "Apply" })).toBeDisabled();

    fireEvent.change(within(dialog).getByLabelText("Ticks per row"), { target: { value: "5" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Apply" }));

    expect(lastFootprintCall()).toMatchObject({ enabled: true, rowTicks: 5 });
    cleanup();
    expect(lastSaved().footprint.row_ticks).toBe(5);
  });

  it("shows the hook's error under the chart", () => {
    layoutApi.server[IID] = layoutOf({ footprint: { ...BUILT_IN_LAYOUT.footprint, on: true } });
    footprintResult.current = { items: [], precision: null, error: "Data API answered 500 -- footprint not loaded (see error bar)" };
    render(page());

    expect(screen.getByText(/Footprint: Data API answered 500/)).toBeInTheDocument();
  });
});

describe("ChartPage derivatives panes (Story 33.5)", () => {
  const NS = 1_000_000_000;
  const PERP = "BTCUSDT-LINEAR.BYBIT";
  const ALL_ON: ChartLayout["derivatives"] = {
    oi: { on: true },
    funding: { on: true },
    basis: { on: true },
    mark_index: { on: true },
    liquidations: { on: true, measure: "size", markers: true },
  };
  const bars = [600, 660, 720].map((time) => ({ time, open: 1, high: 2, low: 1, close: 1 }));
  const pane = (id: string) => lastChartProps.current!.panes!.find((p) => p.id === id)!;
  const derivativePanes = () => lastChartProps.current!.panes!.filter((p) => p.id.startsWith("deriv_"));
  const flush = () => act(async () => {});

  async function renderPerp(derivatives: ChartLayout["derivatives"] = ALL_ON): Promise<void> {
    route.iid = PERP;
    layoutApi.server[PERP] = layoutOf({ derivatives: { ...BUILT_IN_LAYOUT.derivatives, ...derivatives } });
    render(page());
    for (let i = 0; i < 4; i++) await flush(); // pages land (and the older-page check settles)
  }

  beforeEach(() => {
    mocks.candles = bars;
    mocks.venueMarket = { venue: "BYBIT", market: "perp" };
    derivApi.pages = {
      "open-interest": [{ t: 600_000, oi: "120", oi_change: null }, { t: 660_000 }, { t: 720_000, oi: "90", oi_change: "-30" }],
      funding: [
        { t: 620 * NS, rate: "0.0001", interval: 28_800, next_funding_ns: null, annualised: 0.1095 },
        { t: 700 * NS, rate: "-0.0002", interval: 28_800, next_funding_ns: null, annualised: -0.219 },
      ],
      "mark-index": [{ t: 600_000, mark: "100.5", index: "100.0", basis_mi_bps: 50, basis_ml_bps: null }],
      "liquidation-bars": [
        {
          t: 600_000,
          long_v: 1500,
          short_v: 200,
          n: 3,
          size_precision: 3,
          notional_units: 170_000_000,
          notional_precision: 5,
          long_notional_units: 150_000_000,
          short_notional_units: 20_000_000,
        },
      ],
      liquidations: [],
    };
  });

  it("mounts each of the five entries with its pane, kind and placement", async () => {
    await renderPerp();

    expect(derivativePanes().map((p) => [p.id, p.kind, p.placement ?? "pane", p.group])).toEqual([
      ["deriv_oi.oi", "Line", "pane", "deriv_oi"],
      ["deriv_funding.rate", "Histogram", "pane", "deriv_funding"],
      ["deriv_basis.mark_index", "Line", "pane", "deriv_basis"],
      ["deriv_basis.mark_last", "Line", "pane", "deriv_basis"],
      ["deriv_liquidations.long", "Histogram", "pane", "deriv_liquidations"],
      ["deriv_liquidations.short", "Histogram", "pane", "deriv_liquidations"],
      ["deriv_mark_index.mark", "Line", "overlay", "deriv_mark_index"],
      ["deriv_mark_index.index", "Line", "overlay", "deriv_mark_index"],
    ]);
    expect(pane("deriv_basis.mark_index").zeroLine).toBe(true);
    expect(new Set(derivApi.calls.map((c) => c.route))).toEqual(
      new Set(["open-interest", "funding", "mark-index", "liquidation-bars", "liquidations"]),
    );
  });

  it("draws open interest with whitespace for a null bucket, the legend printing the exact text", async () => {
    await renderPerp();

    expect(pane("deriv_oi.oi").data).toEqual([{ time: 600, value: 120 }, { time: 660 }, { time: 720, value: 90 }]);
    expect(pane("deriv_oi.oi").format!(90, 720)).toBe("90 · Δ -30");
    expect(pane("deriv_oi.oi").format!(120, 600)).toBe("120 · Δ —");
  });

  it("holds funding per bar, up/down coloured, with rate %, annualised and the countdown", async () => {
    await renderPerp();

    const funding = pane("deriv_funding.rate");
    // No forming bar here: the hold stops at the newest event's bar (10:01), so 10:02 is whitespace.
    expect(funding.data).toEqual([{ time: 600, value: 0.0001 }, { time: 660, value: -0.0002 }, { time: 720 }]);
    expect([funding.upColor, funding.downColor]).toEqual([CHART_TOKENS["--chart-up"], CHART_TOKENS["--chart-down"]]);
    expect(funding.format!(0.0001, 600)).toBe("rate 0.0100% · ann. 10.95% · next —");
  });

  it("ticks the funding countdown every second against the latest event's next funding time", async () => {
    vi.useFakeTimers({ now: 1_800_000_000_000, toFake: ["setInterval", "clearInterval", "Date"] });
    try {
      const next = (1_800_000_000_000 + (3 * 3600 + 5) * 1000) * 1_000_000;
      derivApi.pages.funding = [{ t: 620 * NS, rate: "0.0001", interval: 28_800, next_funding_ns: next, annualised: 0.1095 }];
      await renderPerp();
      expect(pane("deriv_funding.rate").format!(0.0001, 600)).toContain("next 03:00:05");

      act(() => vi.advanceTimersByTime(1000));
      expect(pane("deriv_funding.rate").format!(0.0001, 600)).toContain("next 03:00:04");
      act(() => vi.advanceTimersByTime(4 * 3600 * 1000));
      expect(pane("deriv_funding.rate").format!(0.0001, 600)).toContain("next 00:00:00");
    } finally {
      vi.useRealTimers();
    }
  });

  it("draws basis against a zero line and the mark/index overlays with their exact text", async () => {
    await renderPerp();

    expect(pane("deriv_basis.mark_index").data).toEqual([{ time: 600, value: 50 }]);
    expect(pane("deriv_basis.mark_last").data).toEqual([{ time: 600 }]);
    expect(pane("deriv_basis.mark_index").format!(50, 600)).toBe("50.00 bps");
    expect(pane("deriv_mark_index.index").format!(100, 600)).toBe("100.0");
  });

  it("mirrors the liquidation bars by size or notional, the legend showing the notional and n", async () => {
    await renderPerp();
    expect([pane("deriv_liquidations.long").data, pane("deriv_liquidations.short").data]).toEqual([
      [{ time: 600, value: -1.5 }],
      [{ time: 600, value: 0.2 }],
    ]);
    expect(pane("deriv_liquidations.short").format!(0.2, 600)).toBe("short 0.200 · notional 1700.00000 · n 3");

    cleanup();
    await renderPerp({ ...ALL_ON, liquidations: { on: true, measure: "notional", markers: true } });
    expect(pane("deriv_liquidations.long").data).toEqual([{ time: 600, value: -1500 }]);
  });

  it("draws the liquidation markers from the rows, and hides them at a narrow bar spacing", async () => {
    derivApi.pages.liquidations = [
      {
        side: "long",
        size_units: 4,
        price_units: 1_000_000,
        price_precision: 1,
        size_precision: 3,
        venue_event_id: "a",
        ts_event: 610 * NS,
        ts_init: 610 * NS,
        price_kind: "bankruptcy",
        notional_units: 4_000_000,
        notional_precision: 4,
      },
    ];
    await renderPerp();
    act(() => lastChartProps.current!.onBarSpacing!(12));
    expect(lastChartProps.current!.liquidationMarkers!.map((m) => [m.id, m.time, m.position, m.price])).toEqual([
      ["liq:a", 600, "atPriceBottom", 100000],
    ]);

    act(() => lastChartProps.current!.onBarSpacing!(4));
    expect(lastChartProps.current!.liquidationMarkers).toEqual([]);
    expect(pane("deriv_liquidations.long").groupLabel).toContain("markers hidden: zoom in");
  });

  it("on spot: the group is disabled, nothing is fetched or subscribed and nothing is drawn", async () => {
    mocks.venueMarket = { venue: "BYBIT", market: "spot" };
    await renderPerp();

    expect(derivApi.calls).toEqual([]);
    expect(liveDerivs.subscribed.every((iid) => iid === "")).toBe(true);
    expect(liveDerivs.liquidationsSubscribed.every((iid) => iid === "")).toBe(true);
    expect(derivativePanes()).toEqual([]);
    expect(lastChartProps.current!.liquidationMarkers).toEqual([]);
    expect(setting("Liquidation tape")).toBeDisabled();
    fireEvent.click(within(screen.getByRole("toolbar", { name: "Chart controls" })).getByRole("button", { name: "Indicators" }));
    const group = within(screen.getByRole("dialog", { name: "Indicators" })).getByRole("group", { name: "Derivatives" });
    expect(within(group).getByText("spot: no derivatives")).toBeInTheDocument();
    expect(within(group).getAllByRole("checkbox").every((box) => (box as HTMLInputElement).disabled)).toBe(true);
    expect(within(group).getByRole("checkbox", { name: "Open Interest" })).toBeChecked(); // the saved state is kept
  });

  it("fetches nothing until the candles reported the market", async () => {
    mocks.venueMarket = null;
    await renderPerp();
    expect(derivApi.calls).toEqual([]);
    expect(liveDerivs.subscribed.every((iid) => iid === "")).toBe(true);
  });

  it("cuts every pane, the markers and the tape at the replay time", async () => {
    mocks.liveBar = { time: 780, open: 1, high: 1, low: 1, close: 1 };
    derivApi.pages.liquidations = [610, 700].map((t) => ({
      side: "short",
      size_units: 1,
      price_units: 10,
      price_precision: 1,
      size_precision: 0,
      venue_event_id: `e${t}`,
      ts_event: t * NS,
      ts_init: t * NS,
      price_kind: "bankruptcy",
      notional_units: 10,
      notional_precision: 1,
    }));
    await renderPerp();
    act(() => lastChartProps.current!.onBarSpacing!(12));
    fireEvent.click(setting("Liquidation tape"));
    await flush();

    fireEvent.click(screen.getByRole("button", { name: "Replay" }));
    act(() => lastChartProps.current!.onPointClick!({ time: 600, price: 1 }));
    await flush(); // the replay tape's own page

    expect(derivApi.calls.filter((c) => c.route === "liquidations").at(-1)!.args.slice(1)).toEqual([660 * NS, 50]);
    for (const p of derivativePanes()) expect(p.data.every((d) => d.time <= 600)).toBe(true);
    expect(pane("deriv_oi.oi").data).toEqual([{ time: 600, value: 120 }]);
    expect(lastChartProps.current!.liquidationMarkers!.map((m) => m.id)).toEqual(["liq:e610"]);
    const tape = screen.getByRole("complementary", { name: "Liquidation tape" });
    expect(within(tape).getAllByRole("row")).toHaveLength(2); // the header and e610
  });

  it("sets the forming slot from a live tick in it, ignores an older tick, and keeps it until the route serves the bar", async () => {
    mocks.liveBar = { time: 780, open: 1, high: 1, low: 1, close: 1 };
    await renderPerp();
    act(() => liveDerivs.onTick!({ kind: "oi", t: 779 * NS, ts_init: 779 * NS, value: "77" })); // before the slot
    expect(pane("deriv_oi.oi").data.at(-1)).toEqual({ time: 720, value: 90 });

    act(() => liveDerivs.onTick!({ kind: "oi", t: 810 * NS, ts_init: 810 * NS, value: "95" }));
    expect(pane("deriv_oi.oi").data.at(-1)).toEqual({ time: 780, value: 95 });
    expect(pane("deriv_oi.oi").format!(95, 780)).toBe("95 · Δ —");

    // The bar closed (the next one forms) but the archive has not served 780 yet: the live value stays.
    mocks.liveBar = { time: 840, open: 1, high: 1, low: 1, close: 1 };
    act(() => lastChartProps.current!.onPaneHeights!({ price: 500 })); // any re-render
    expect(pane("deriv_oi.oi").data.find((d) => d.time === 780)).toEqual({ time: 780, value: 95 });
  });

  it("holds a live funding event after its slot closed, until the route has it", async () => {
    mocks.liveBar = { time: 780, open: 1, high: 1, low: 1, close: 1 };
    await renderPerp();
    act(() =>
      liveDerivs.onTick!({ kind: "funding", t: 790 * NS, ts_init: 790 * NS, value: "0.0003", interval: 28_800, next_funding_ns: null, annualised: 0.3285 }),
    );
    expect(pane("deriv_funding.rate").data.at(-1)).toEqual({ time: 780, value: 0.0003 });
    expect(pane("deriv_funding.rate").format!(0.0003, 780)).toBe("rate 0.0300% · ann. 32.85% · next —");
  });

  it("does not hand the chart new series data on a countdown tick", async () => {
    vi.useFakeTimers({ now: 1_800_000_000_000, toFake: ["setInterval", "clearInterval", "Date"] });
    try {
      await renderPerp();
      const before = derivativePanes().map((p) => p.data);
      act(() => vi.advanceTimersByTime(3000));
      const after = derivativePanes().map((p) => p.data);
      expect(after.every((data, i) => data === before[i])).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it("in Lines mode: the group is disabled with Candles mode only, nothing drawn, the tape button disabled", async () => {
    await renderPerp();
    switchToLines();
    expect(derivativePanes()).toEqual([]);
    expect(setting("Liquidation tape")).toBeDisabled();
    fireEvent.click(within(screen.getByRole("toolbar", { name: "Chart controls" })).getByRole("button", { name: "Indicators" }));
    const group = within(screen.getByRole("dialog", { name: "Indicators" })).getByRole("group", { name: "Derivatives" });
    expect(within(group).getByText("Candles mode only")).toBeInTheDocument();
    expect(within(group).getAllByRole("checkbox").every((box) => (box as HTMLInputElement).disabled)).toBe(true);
  });

  it("disables the tape until the instrument's market is known", async () => {
    mocks.venueMarket = null;
    await renderPerp();
    expect(setting("Liquidation tape")).toBeDisabled();
  });

  it("persists the on/off state, the style and the pane height in the coin's layout", async () => {
    await renderPerp({ ...BUILT_IN_LAYOUT.derivatives });
    expect(derivativePanes()).toEqual([]);
    fireEvent.click(within(screen.getByRole("toolbar", { name: "Chart controls" })).getByRole("button", { name: "Indicators" }));
    const group = within(screen.getByRole("dialog", { name: "Indicators" })).getByRole("group", { name: "Derivatives" });
    fireEvent.click(within(group).getByRole("checkbox", { name: "Open Interest" }));
    await flush();
    expect(pane("deriv_oi.oi")).toBeDefined();

    act(() => lastChartProps.current!.onLegendAction!("settings", "deriv_oi"));
    const settings = screen.getByRole("dialog", { name: "Open Interest" });
    fireEvent.change(within(settings).getByLabelText("oi colour"), { target: { value: "#123456" } });
    fireEvent.click(within(settings).getByRole("button", { name: "Apply" }));
    await flush();
    expect(pane("deriv_oi.oi").color).toBe("#123456");
    act(() => lastChartProps.current!.onPaneHeights!({ price: 400, deriv_oi: 140 }));

    cleanup(); // flushes the pending save
    const saved = lastSaved(PERP);
    expect(saved.derivatives.oi).toEqual({ on: true, style: { oi: { color: "#123456" } } });
    expect(saved.pane_heights).toEqual({ price: 400, deriv_oi: 140 });

    layoutApi.server[PERP] = saved;
    render(page());
    for (let i = 0; i < 4; i++) await flush();
    expect(pane("deriv_oi.oi").color).toBe("#123456");
    expect(lastChartProps.current!.initialPaneHeights).toEqual({ price: 400, deriv_oi: 140 });
  });

  it("turns an entry off from the legend's x, and changes the liquidation measure in its settings", async () => {
    await renderPerp();
    act(() => lastChartProps.current!.onLegendAction!("remove", "deriv_oi"));
    expect(derivativePanes().some((p) => p.group === "deriv_oi")).toBe(false);

    act(() => lastChartProps.current!.onLegendAction!("settings", "deriv_liquidations"));
    const settings = screen.getByRole("dialog", { name: "Liquidations" });
    fireEvent.change(within(settings).getByDisplayValue("size"), { target: { value: "notional" } });
    fireEvent.click(within(settings).getByRole("button", { name: "Apply" }));
    await flush();
    expect(pane("deriv_liquidations.long").data).toEqual([{ time: 600, value: -1500 }]);
    cleanup();
    expect(lastSaved(PERP).derivatives.liquidations).toMatchObject({ on: true, measure: "notional", markers: true });
    expect(lastSaved(PERP).derivatives.oi.on).toBe(false);
  });

  it("toggles the Liquidation tape, which says when the instrument has no feed", async () => {
    await renderPerp();
    const toggle = setting("Liquidation tape");
    expect(toggle).not.toBeChecked();
    expect(screen.queryByRole("complementary", { name: "Liquidation tape" })).toBeNull();

    fireEvent.click(toggle);
    await flush();

    expect(toggle).toBeChecked();
    expect(screen.getByRole("complementary", { name: "Liquidation tape" })).toHaveTextContent(
      "no liquidation feed for this instrument",
    );
  });

  it("shows a live liquidation on the tape", async () => {
    await renderPerp();
    fireEvent.click(setting("Liquidation tape"));
    await flush();
    act(() =>
      liveDerivs.onLiquidation!({
        instrument_id: PERP,
        side: "long",
        size_units: 41,
        price_units: 8_513_850,
        price_precision: 2,
        size_precision: 3,
        venue_event_id: "live-1",
        ts_event: 650 * NS,
        ts_init: 650 * NS,
        notional_units: 349_067_850,
        notional_precision: 5,
      }),
    );
    const tape = screen.getByRole("complementary", { name: "Liquidation tape" });
    expect(within(tape).getByText("85138.50")).toBeInTheDocument();
    expect(within(tape).getByText("3490.67850")).toBeInTheDocument();
  });
});

describe("ChartPage order-flow indicators (Story 33.6)", () => {
  const paneOf = (id: string) => lastChartProps.current!.panes!.find((p) => p.id === id)!;
  const legendAction = (action: "hide" | "settings" | "remove", group: string) =>
    act(() => lastChartProps.current!.onLegendAction!(action, group));
  let baseCatalog: Awaited<ReturnType<typeof fetchIndicatorCatalog>>;

  beforeEach(async () => {
    baseCatalog = await fetchIndicatorCatalog();
    vi.mocked(fetchIndicatorCatalog).mockResolvedValue({
      ...baseCatalog,
      VolumeDelta: { params: {}, panel: "histogram", category: "custom", units: { value: "size" }, outputs: ["value"] },
      TradeCount: { params: { split: false }, panel: "histogram", category: "custom", units: { value: "count", buys: "count", sells: "count" }, outputs: ["value", "buys", "sells"] },
      StoredVWAP: { params: { mode: "session" }, panel: "overlay", category: "custom", units: { value: "price" }, outputs: ["value"] },
      ForcedShare: { params: {}, panel: "histogram", category: "custom", units: { value: "ratio" }, outputs: ["value"] },
    });
  });
  afterEach(() => {
    vi.mocked(fetchIndicatorCatalog).mockResolvedValue(baseCatalog);
  });

  async function mountWith(entries: object[], values: Record<string, never[]>): Promise<void> {
    vi.mocked(fetchCoinIndicatorConfig).mockResolvedValueOnce(entries as never);
    picker.values = values;
    render(page());
    await act(async () => {}); // catalog + saved config
  }

  describe("unit-formatted legends", () => {
    it("prints each output in its catalog unit at the instrument's precision", async () => {
      await mountWith(
        [
          { name: "VolumeDelta", params: {}, category: "custom" },
          { name: "TradeCount", params: { split: true }, category: "custom" },
          { name: "StoredVWAP", params: { mode: "bar" }, category: "custom" },
          { name: "ForcedShare", params: {}, category: "custom" },
        ],
        {
          "VolumeDelta.value": [],
          "TradeCount_split=True.buys": [],
          "TradeCount_split=True.sells": [],
          "StoredVWAP_mode=bar.value": [],
          "ForcedShare.value": [],
        },
      );

      // precision { price: 2, size: 3 }
      expect(paneOf("VolumeDelta.value").format!(4, null)).toBe("4.000");
      expect(paneOf("TradeCount_split=True.buys").format!(7, null)).toBe("7");
      expect(paneOf("TradeCount_split=True.sells").format!(-3, null)).toBe("-3");
      expect(paneOf("StoredVWAP_mode=bar.value").format!(1000.05, null)).toBe("1000.05");
      expect(paneOf("ForcedShare.value").format!(1.25, null)).toBe("125.00%");
    });

    it("leaves a native entry, and every entry while the precision is unknown, on the default readout", async () => {
      mocks.precision = null;
      await mountWith(
        [
          { name: "VolumeDelta", params: {}, category: "custom" },
          { name: "RelativeStrengthIndex", params: { period: 14 }, category: "native" },
        ],
        { "VolumeDelta.value": [], "RelativeStrengthIndex_period=14.value": [] },
      );

      expect(paneOf("VolumeDelta.value").format).toBeUndefined();
      expect(paneOf("RelativeStrengthIndex_period=14.value").format).toBeUndefined();
    });
  });

  describe("the stored Anchored VWAP", () => {
    const bar = (t: number) => ({ time: t, open: 10, high: 12, low: 9, close: 11 });
    const STORED = { kind: "anchored_vwap", id: "anchored_vwap-1", time: 200, source: "stored", bands: true, band_color: "#b26a00" };
    const vwapSpec = () =>
      lastChartProps.current!.drawings!.find((d) => d.kind === "anchored_vwap")! as unknown as {
        bands: boolean;
        points: { time: number; vwap: number; breakBefore?: boolean }[];
      };

    beforeEach(() => {
      mocks.candles = [bar(100), bar(200), bar(300), bar(400)];
      mocks.volume = [100, 200, 300, 400].map((time) => ({ time, value: 1 }));
    });

    it("hands its own values hook the drawings, and draws the server's values with no bands", async () => {
      drawingsApi.server = [STORED];
      storedVwap.result = {
        values: { "anchored_vwap-1": [{ time: 100 }, { time: 200, value: 12.5 }, { time: 300 }, { time: 400, value: 13.25 }] },
        errors: {},
      };
      await renderReady(page());

      expect(storedVwap.drawings).toEqual([STORED]);
      // Never a picker pane: the picker's values hook saw no AnchoredStoredVWAP entry.
      expect(lastChartProps.current!.panes!.some((p) => p.id.startsWith("AnchoredStoredVWAP"))).toBe(false);
      expect(vwapSpec().bands).toBe(false);
      expect(vwapSpec().points.map((p) => [p.time, p.vwap, p.breakBefore === true])).toEqual([
        [200, 12.5, false],
        [400, 13.25, true],
      ]);
      const legend = lastChartProps.current!.legendExtras![0];
      expect(legend.label).toBe("AVWAP (stored)");
      expect(legend.format(legend.value!)).toBe("13.25");
    });

    it("shows the entry's replay error in the drawing's legend row", async () => {
      drawingsApi.server = [STORED];
      // Values from a page that did load are not drawn beside the error: a partial line.
      storedVwap.result = { values: { "anchored_vwap-1": [{ time: 200, value: 12.5 }] }, errors: { "anchored_vwap-1": "no exact stored sum from the anchor 1970-01-01T00:03:20Z: the candle store's first 60 s bar is 1970-01-01T01:00:00Z" } };
      await renderReady(page());

      const legend = lastChartProps.current!.legendExtras![0] as { text?: string; value: number | null };
      expect(legend.text).toBe("failed: no exact stored sum from the anchor 1970-01-01T00:03:20Z: the candle store's first 60 s bar is 1970-01-01T01:00:00Z");
      expect(legend.value).toBeNull();
      expect(vwapSpec().points).toEqual([]);
    });

    it("is offered in the drawing's settings, which turn its bands off with a note, and is saved", async () => {
      vi.useFakeTimers();
      try {
        drawingsApi.server = [{ ...STORED, source: "hlc3", bands: true }];
        await renderReady(page());
        act(() => lastChartProps.current!.onDrawingSettings!("anchored_vwap-1"));
        const dialog = screen.getByRole("dialog", { name: "Anchored VWAP settings" });
        const source = within(dialog).getByLabelText<HTMLSelectElement>("Source");
        expect(Array.from(source.options).map((o) => o.value)).toEqual(["hlc3", "close", "ohlc4", "stored"]);

        fireEvent.change(source, { target: { value: "stored" } });
        expect(within(dialog).getByLabelText("Bands on")).toBeDisabled();
        expect(within(dialog).getByText(/bands need per-trade prices/)).toBeInTheDocument();
        fireEvent.click(within(dialog).getByRole("button", { name: "Apply" }));
        await act(async () => {
          vi.advanceTimersByTime(SAVE_DEBOUNCE_MS + 1);
        });

        const saved = drawingsApi.save.mock.calls.at(-1)![1] as Record<string, unknown>[];
        expect(saved[0]).toMatchObject({ id: "anchored_vwap-1", source: "stored" });
        expect(storedVwap.drawings).toEqual([expect.objectContaining({ id: "anchored_vwap-1", source: "stored" })]);
      } finally {
        vi.useRealTimers();
      }
    });
  });

  describe("the Volume colour", () => {
    const volumePoints = [
      { time: 100, value: 10, o: 1, c: 2, buy_v: 3, sell_v: 7 },
      { time: 200, value: 4, o: 2, c: 1, buy_v: null, sell_v: null },
    ];
    const colours = () => paneOf("volume").data.map((d) => d.color);

    beforeEach(() => {
      mocks.candles = [
        { time: 100, open: 1, high: 2, low: 1, close: 2 },
        { time: 200, open: 2, high: 2, low: 1, close: 1 },
      ];
      mocks.volume = volumePoints;
    });

    it("colours by direction by default, the forming bar included", () => {
      mocks.liveBar = { time: 300, open: 1, high: 2, low: 1, close: 0.5, volume: 5, buy_v: 4, sell_v: 1 };
      render(page());

      expect(colours()).toEqual([CHART_TOKENS["--chart-up"], CHART_TOKENS["--chart-down"]]);
      expect(paneOf("volume").data[0]).toEqual({ time: 100, value: 10, color: CHART_TOKENS["--chart-up"] });
      expect(lastChartProps.current!.liveVolumeColor).toBe(CHART_TOKENS["--chart-down"]);
    });

    it("colours by delta from the saved layout: the sign, shaded by one-sidedness; unknown flow neutral", () => {
      layoutApi.server[IID] = layoutOf({ volume_color_by: "delta" });
      mocks.liveBar = { time: 300, open: 1, high: 2, low: 1, close: 0.5, volume: 5, buy_v: 4, sell_v: 1 };
      render(page());

      // 3 vs 7: down at 0.4; null flow: the pane's own colour.
      expect(colours()).toEqual(["rgba(239, 83, 80, 0.4)", paneOf("volume").color]);
      // The forming bar: 4 vs 1, up at 0.6, though its candle closed down.
      expect(lastChartProps.current!.liveVolumeColor).toBe("rgba(37, 163, 153, 0.6)");
    });

    it("the Volume gear sets the mode, repaints and saves it in the layout", () => {
      render(page());
      legendAction("settings", "volume");
      const dialog = screen.getByRole("dialog", { name: "Volume settings" });
      const select = within(dialog).getByLabelText<HTMLSelectElement>("Colour by");
      expect(select.value).toBe("direction");

      fireEvent.change(select, { target: { value: "delta" } });
      fireEvent.click(within(dialog).getByRole("button", { name: "Apply" }));

      expect(screen.queryByRole("dialog", { name: "Volume settings" })).toBeNull();
      expect(colours()[0]).toBe("rgba(239, 83, 80, 0.4)");
      cleanup(); // flushes the pending save
      expect(lastSaved().volume_color_by).toBe("delta");
    });
  });
});

// Story 33.9: the chart type, the price scale and compare symbols, as the page feeds them to the chart
// and saves them in the coin's layout.
describe("chart type, price scale and compare (Story 33.9)", () => {
  const OTHER = "BTC-USD-PERP.HYPERLIQUID";
  const bar = (time: number, close: number) => ({ time, open: close, high: close, low: close, close });
  const comparePane = (iid = OTHER) => lastChartProps.current!.panes!.find((p) => p.id === `compare:${iid}`);
  const spreadPane = () => lastChartProps.current!.panes!.find((p) => p.id === "compare-spread");
  // Story 33.12: the Compare button opens the symbol search in compare mode; Enter adds the highlighted
  // market, or the typed id when none matches. A refused id keeps the search open with the reason.
  const compareField = () => screen.queryByRole("searchbox", { name: "Search a market to compare" });
  async function addCompare(text: string): Promise<void> {
    if (!compareField()) fireEvent.click(screen.getByRole("button", { name: "Compare" }));
    await act(async () => {});
    fireEvent.change(compareField()!, { target: { value: text } });
    fireEvent.keyDown(compareField()!, { key: "Enter" });
  }

  it("feeds the chart type and the scale settings to the chart and saves them per coin", async () => {
    vi.useFakeTimers();
    render(page());
    expect(lastChartProps.current!.chartType).toBe("candles");
    expect(lastChartProps.current!.priceScale).toEqual({ mode: "normal", autoScale: true });

    // Chart UX rework (2026-10-08): the type from the chart type menu, the scale from the chart's
    // corner chips; Invert is gone.
    pickChartType("Heikin Ashi");
    fireEvent.click(screen.getByRole("button", { name: "Log scale" }));
    fireEvent.click(screen.getByRole("button", { name: "Auto scale" }));
    await flushSave();

    expect(lastChartProps.current!.chartType).toBe("heikin_ashi");
    expect(lastChartProps.current!.priceScale).toEqual({ mode: "log", autoScale: false });
    expect(screen.getByRole("button", { name: "Auto scale" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "Log scale" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("button", { name: /Invert/ })).toBeNull();
    expect(lastSaved()).toMatchObject({
      chart_type: "heikin_ashi",
      price_scale: { mode: "log", auto_scale: false },
    });
  });

  it("persists a change the chart reports (the scale menu, a double-click restoring auto)", async () => {
    vi.useFakeTimers();
    layoutApi.server[IID] = layoutOf({ price_scale: { mode: "normal", auto_scale: false } });
    render(page());

    act(() => lastChartProps.current!.onPriceScale!({ auto_scale: true }));
    act(() => lastChartProps.current!.onPriceScale!({ mode: "indexed" }));
    await flushSave();

    expect(lastSaved().price_scale).toEqual({ mode: "indexed", auto_scale: true });
  });

  it("restores the saved type and scale", () => {
    layoutApi.server[IID] = layoutOf({ chart_type: "area", price_scale: { mode: "percent", auto_scale: true } });
    render(page());

    expect(screen.getByRole("button", { name: /^Chart type:/ })).toHaveAccessibleName("Chart type: Area");
    expect(lastChartProps.current!.priceScale).toEqual({ mode: "percent", autoScale: true });
  });

  it("adds a compare as a coloured overlay aligned on the main bars, with gaps, and saves it", async () => {
    vi.useFakeTimers();
    mocks.candles = [bar(60, 100), { time: 120 }, bar(180, 102)];
    mocks.candlesByIid[OTHER] = [bar(60, 50), bar(120, 51), { time: 180 }, bar(240, 53)];
    render(page());
    fireEvent.click(screen.getByRole("button", { name: "Compare" }));
    await act(async () => {});
    expect(marketsApi.get).toHaveBeenCalledWith(IID);
    expect(within(screen.getByRole("listbox", { name: "Markets" })).getByRole("option")).toHaveTextContent(
      `BTC ${OTHER}HYPERLIQUID · perp · 24h 2.50M`,
    );

    await addCompare(`  ${OTHER} `);
    await flushSave();

    const pane = comparePane()!;
    expect(pane).toMatchObject({ kind: "Line", placement: "overlay", group: `compare:${OTHER}`, groupLabel: OTHER });
    expect(pane.color).toBe(CHART_TOKENS["--chart-compare-1"]);
    expect(pane.data).toEqual([{ time: 60, value: 50 }, { time: 120 }, { time: 180 }]);
    expect(pane.format!(50.5, 60)).toBe("50.50");
    expect(lastSaved().compare).toEqual({ symbols: [OTHER], spread: false });
  });

  it("forces the percent scale while a compare is drawn, keeping the stored mode for when it goes", async () => {
    vi.useFakeTimers();
    layoutApi.server[IID] = layoutOf({ price_scale: { mode: "log", auto_scale: true } });
    render(page());

    await addCompare(OTHER);
    expect(lastChartProps.current!.priceScale!.mode).toBe("percent");
    expect(lastChartProps.current!.scaleModesLocked).toMatch(/percent scale/);
    expect(screen.getByRole("button", { name: "Log scale" })).toBeDisabled();
    await flushSave();
    expect(lastSaved().price_scale.mode).toBe("log");

    act(() => lastChartProps.current!.onLegendAction!("remove", `compare:${OTHER}`));
    await flushSave();
    expect(comparePane()).toBeUndefined();
    expect(lastChartProps.current!.priceScale!.mode).toBe("log");
    expect(lastChartProps.current!.scaleModesLocked).toBeNull();
    expect(lastSaved().compare.symbols).toEqual([]);
  });

  it("keeps Indexed to 100 as the compare scale when it is the stored mode", async () => {
    layoutApi.server[IID] = layoutOf({ price_scale: { mode: "indexed", auto_scale: true }, compare: { symbols: [OTHER], spread: false } });
    render(page());

    expect(lastChartProps.current!.priceScale!.mode).toBe("indexed");
  });

  it("refuses the chart's own id, a duplicate, a fourth symbol and text without a venue, saving nothing", async () => {
    vi.useFakeTimers();
    layoutApi.server[IID] = layoutOf({ compare: { symbols: ["A.BYBIT", "B.BYBIT"], spread: false } });
    render(page());

    for (const [text, reason] of [
      [IID, "own instrument"],
      ["A.BYBIT", "already compared"],
      ["NOVENUE", ".VENUE suffix"],
    ] as const) {
      await addCompare(text);
      expect(screen.getByRole("alert")).toHaveTextContent(reason);
    }
    await addCompare("C.BYBIT");
    await addCompare("D.BYBIT");
    expect(screen.getByRole("alert")).toHaveTextContent("At most 3");
    await flushSave();
    expect(lastSaved().compare.symbols).toEqual(["A.BYBIT", "B.BYBIT", "C.BYBIT"]);
  });

  it("still accepts free text when the market list fails, saying so inline", async () => {
    marketsApi.get.mockRejectedValue(new Error("down"));
    render(page());

    fireEvent.click(screen.getByRole("button", { name: "Compare" }));
    await act(async () => {});
    expect(screen.getByRole("status")).toHaveTextContent("could not be loaded; type an instrument id.");

    await addCompare(OTHER);
    expect(comparePane()).toBeDefined();
  });

  it("keeps a compare whose load failed as a removable row reading no data", () => {
    mocks.failedIids = [OTHER];
    layoutApi.server[IID] = layoutOf({ compare: { symbols: [OTHER], spread: false } });
    render(page());

    expect(comparePane()).toMatchObject({ text: "no data", data: [] });
  });

  it("offers Spread for exactly one compare: a bps pane with a zero line, gaps where either side has none", async () => {
    vi.useFakeTimers();
    mocks.candles = [bar(60, 101), bar(120, 102), { time: 180 }];
    mocks.candlesByIid[OTHER] = [bar(60, 100), { time: 120 }, bar(180, 100)];
    render(page());
    // With no compare yet, Spread opens the compare search and draws once a symbol is picked.
    const spread = () => screen.getByRole("button", { name: "Spread" });
    expect(spread()).toBeEnabled();
    expect(spread()).toHaveAttribute("title", expect.stringMatching(/opens the search/));
    fireEvent.click(spread());
    await act(async () => {});
    expect(compareField()).not.toBeNull();

    await addCompare(OTHER);
    await flushSave();
    expect(spread()).toHaveAttribute("aria-pressed", "true");

    const pane = spreadPane()!;
    expect(pane).toMatchObject({ placement: "pane", zeroLine: true });
    expect(pane.data[0].value).toBeCloseTo(100, 9);
    expect(pane.data.slice(1)).toEqual([{ time: 120 }, { time: 180 }]);
    expect(lastSaved().compare.spread).toBe(true);

    await addCompare("ETHUSDT-LINEAR.BYBIT");
    expect(spread()).toBeDisabled();
    expect(spread()).toHaveAttribute("title", expect.stringMatching(/exactly one compare/));
    expect(spreadPane()).toBeUndefined();
    await flushSave();
    expect(lastSaved().compare.spread).toBe(true); // kept as stored

    act(() => lastChartProps.current!.onLegendAction!("remove", "compare:ETHUSDT-LINEAR.BYBIT"));
    act(() => lastChartProps.current!.onLegendAction!("remove", "compare-spread"));
    await flushSave();
    expect(spreadPane()).toBeUndefined();
    expect(lastSaved().compare).toEqual({ symbols: [OTHER], spread: false });
  });

  it("adds a compare once for a double Enter queued before a re-render", async () => {
    vi.useFakeTimers();
    render(page());
    fireEvent.click(screen.getByRole("button", { name: "Compare" }));
    await act(async () => {});
    const field = compareField()!;
    fireEvent.change(field, { target: { value: OTHER } });

    act(() => {
      field.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      field.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); // the search still holds the render that had no compare
    });
    await flushSave();

    expect(lastSaved().compare.symbols).toEqual([OTHER]);
  });

  it("forgets a removed compare's hidden flag, so re-adding it draws it shown", async () => {
    layoutApi.server[IID] = layoutOf({ compare: { symbols: [OTHER], spread: false } });
    render(page());
    act(() => lastChartProps.current!.onLegendAction!("hide", `compare:${OTHER}`));
    expect(comparePane()!.hidden).toBe(true);

    act(() => lastChartProps.current!.onLegendAction!("remove", `compare:${OTHER}`));
    expect(comparePane()).toBeUndefined();
    await addCompare(OTHER);

    expect(comparePane()!.hidden).toBe(false);
  });

  it("hides a compare line in place from its legend eye, without changing the layout", () => {
    layoutApi.server[IID] = layoutOf({ compare: { symbols: [OTHER], spread: false } });
    render(page());

    act(() => lastChartProps.current!.onLegendAction!("hide", `compare:${OTHER}`));

    expect(comparePane()!.hidden).toBe(true);
  });

  it("disables Compare and Spread in Lines mode and draws no compare, keeping the type and compares stored", async () => {
    vi.useFakeTimers();
    layoutApi.server[IID] = layoutOf({ chart_type: "bars", compare: { symbols: [OTHER], spread: false } });
    render(page());

    switchToLines();
    await flushSave();

    // The chart type menu stays usable in Lines mode: it is the way back to a candle chart type.
    expect(screen.getByRole("button", { name: /^Chart type:/ })).toHaveAccessibleName(`Chart type: ${LINES_ITEM}`);
    expect(screen.getByRole("button", { name: "Compare" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Spread" })).toBeDisabled();
    expect(comparePane()).toBeUndefined();
    expect(lastChartProps.current!.priceScale!.mode).toBe("normal");
    expect(screen.getByRole("button", { name: "Log scale" })).toBeEnabled();
    expect(lastSaved()).toMatchObject({ chart_type: "bars", compare: { symbols: [OTHER] } });
  });
});

describe("ChartPage drawing tools II (Story 33.10)", () => {
  const click = (time: number, price: number) =>
    act(() => {
      lastChartProps.current!.onPointClick!({ time, price });
    });
  const drawings = () => lastChartProps.current!.drawings!;
  const undoKey = (shift = false) => fireEvent.keyDown(window, { key: "z", ctrlKey: true, shiftKey: shift });

  it("places a ray in two clicks, showing the first point as a placement, then disarms", async () => {
    await renderReady(<ChartPage />);
    armTool("Ray tool");
    click(100, 10);
    expect(lastChartProps.current!.placement).toEqual({ tool: "ray", points: [{ time: 100, price: 10 }] });
    click(100, 10); // on the first point: ignored, still armed
    expect(drawings()).toEqual([]);
    click(200, 20.004);
    expect(drawings()).toEqual([
      {
        kind: "ray",
        id: "ray-1",
        anchors: [
          { time: 100, price: 10 },
          { time: 200, price: 20 },
        ],
        color: CHART_TOKENS["--chart-drawing"],
      },
    ]);
    expect(lastChartProps.current!.placement).toBeNull();
    expect(screen.getByRole("button", { name: "Cursor tool" })).toHaveAttribute("aria-pressed", "true");
  });

  it("places a parallel channel in three clicks, its offset from the third", async () => {
    await renderReady(<ChartPage />);
    armTool("Parallel channel tool");
    click(100, 100);
    click(200, 110);
    expect(drawings()).toEqual([]);
    click(150, 99);
    expect(drawings()).toEqual([expect.objectContaining({ kind: "channel", id: "channel-1", offset: -6 })]);
  });

  it("opens the text dialog on placing a text note", async () => {
    await renderReady(<ChartPage />);
    armTool("Text tool");
    click(100, 100);
    expect(drawings()).toEqual([expect.objectContaining({ kind: "text", text: "Text" })]);
    expect(screen.getByRole("dialog", { name: "Text settings" })).toBeInTheDocument();
  });

  it("discards the placement's points on Escape", async () => {
    await renderReady(<ChartPage />);
    armTool("Fibonacci extension tool");
    click(100, 100);
    click(200, 110);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(lastChartProps.current!.placement).toBeNull();
    armTool("Fibonacci extension tool");
    click(300, 105);
    expect(lastChartProps.current!.placement?.points).toEqual([{ time: 300, price: 105 }]);
  });

  it("undoes and redoes by keyboard and from the rail, but not while typing in a field", async () => {
    await renderReady(<ChartPage />);
    armTool("Vertical line tool");
    click(100, 1);
    armTool("Vertical line tool");
    click(200, 1);
    expect(drawings().map((d) => d.id)).toEqual(["vline-1", "vline-2"]);

    undoKey();
    expect(drawings().map((d) => d.id)).toEqual(["vline-1"]);
    undoKey();
    expect(drawings()).toEqual([]);
    undoKey(); // nothing left: a no-op
    expect(drawings()).toEqual([]);
    undoKey(true);
    expect(drawings().map((d) => d.id)).toEqual(["vline-1"]);
    fireEvent.click(screen.getByRole("button", { name: "Redo" }));
    expect(drawings().map((d) => d.id)).toEqual(["vline-1", "vline-2"]);
    expect(screen.getByRole("button", { name: "Redo" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(drawings().map((d) => d.id)).toEqual(["vline-1"]);

    const field = timeZoneSelect();
    fireEvent.keyDown(field, { key: "z", ctrlKey: true });
    expect(drawings().map((d) => d.id)).toEqual(["vline-1"]);
  });

  it("makes one drag gesture one undo step", async () => {
    await renderReady(<ChartPage />);
    armTool("Horizontal line tool");
    act(() => lastChartProps.current!.onPriceClick!(100));
    act(() => lastChartProps.current!.onDrawingDragStart!("hline-1"));
    for (const price of [101, 102, 103]) act(() => lastChartProps.current!.onPriceLineDrag!("hline-1", price));
    expect(lastChartProps.current!.priceLines![0].price).toBe(103);
    undoKey();
    expect(lastChartProps.current!.priceLines![0].price).toBe(100);
  });

  it("deletes every drawing only once the confirm naming the count is accepted, undoably", async () => {
    await renderReady(<ChartPage />);
    armTool("Vertical line tool");
    click(100, 1);
    armTool("Horizontal line tool");
    act(() => lastChartProps.current!.onPriceClick!(100));
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    try {
      fireEvent.click(screen.getByRole("button", { name: "Delete all drawings" }));
      expect(confirm).toHaveBeenCalledWith(expect.stringContaining("Delete all 2 drawings of BTC-USD-PERP.DYDX?"));
      expect(drawings()).toHaveLength(1);
      expect(lastChartProps.current!.priceLines).toHaveLength(1);

      confirm.mockReturnValue(true);
      fireEvent.click(screen.getByRole("button", { name: "Delete all drawings" }));
      expect(drawings()).toEqual([]);
      expect(lastChartProps.current!.priceLines).toEqual([]);
      expect(screen.getByRole("button", { name: "Delete all drawings" })).toBeDisabled();
      undoKey();
      expect(drawings()).toHaveLength(1);
    } finally {
      confirm.mockRestore();
    }
  });

  it("hides every drawing and turns the drawing tools off, persisted in the layout", async () => {
    await renderReady(<ChartPage />);
    armTool("Vertical line tool");
    click(100, 1);
    armTool("Ray tool");
    fireEvent.click(screen.getByRole("button", { name: "Hide all drawings" }));
    expect(screen.getByRole("button", { name: "Hide all drawings" })).toHaveAttribute("aria-pressed", "true");
    expect(drawings()).toEqual([]);
    expect(screen.getByRole("button", { name: "Cursor tool" })).toHaveAttribute("aria-pressed", "true"); // disarmed
    expect(toolControl("Ray tool")).toBeDisabled();
    cleanup(); // flushes the pending layout save
    expect(lastSaved().drawings_hidden).toBe(true);

    layoutApi.server[IID] = layoutOf({ drawings_hidden: true });
    drawingsApi.server = [{ kind: "vline", id: "vline-1", time: 100 }];
    await renderReady(<ChartPage />);
    expect(drawings()).toEqual([]);
    expect(toolControl("Trendline tool")).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Hide all drawings" }));
    expect(drawings().map((d) => d.id)).toEqual(["vline-1"]);
    expect(toolControl("Trendline tool")).toBeEnabled();
  });

  it("locks and hides a drawing from its menu, and Show hidden brings every hidden one back", async () => {
    await renderReady(<ChartPage />);
    armTool("Vertical line tool");
    click(100, 1);
    act(() => lastChartProps.current!.onDrawingLock!("vline-1", true));
    expect(drawings()[0]).toMatchObject({ id: "vline-1", locked: true });
    act(() => lastChartProps.current!.onDrawingLock!("vline-1", false));
    expect(drawings()[0]).toEqual({ kind: "vline", id: "vline-1", time: 100, color: CHART_TOKENS["--chart-drawing"] });

    act(() => lastChartProps.current!.onDrawingHide!("vline-1"));
    expect(drawings()).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Show hidden (1)" }));
    expect(drawings().map((d) => d.id)).toEqual(["vline-1"]);
    expect(screen.queryByRole("button", { name: /Show hidden/ })).toBeNull();
  });

  it("redoes with Ctrl+Y, and ignores undo/redo keys mid-placement or under Hide all", async () => {
    await renderReady(<ChartPage />);
    armTool("Vertical line tool");
    click(100, 1);
    undoKey();
    expect(drawings()).toEqual([]);
    fireEvent.keyDown(window, { key: "y", ctrlKey: true });
    expect(drawings().map((d) => d.id)).toEqual(["vline-1"]);

    armTool("Ray tool");
    click(100, 10); // a placement in progress
    undoKey();
    expect(drawings().map((d) => d.id)).toEqual(["vline-1"]);
    expect(screen.getByRole("button", { name: "Undo" })).toBeDisabled();
    fireEvent.keyDown(window, { key: "Escape" });

    fireEvent.click(screen.getByRole("button", { name: "Hide all drawings" }));
    undoKey();
    fireEvent.click(screen.getByRole("button", { name: "Hide all drawings" }));
    expect(drawings().map((d) => d.id)).toEqual(["vline-1"]);
  });

  it("turns Show hidden and Delete all off under Hide all, saying why", async () => {
    await renderReady(<ChartPage />);
    armTool("Vertical line tool");
    click(100, 1);
    armTool("Vertical line tool");
    click(200, 1);
    act(() => lastChartProps.current!.onDrawingHide!("vline-1"));
    fireEvent.click(screen.getByRole("button", { name: "Hide all drawings" }));
    for (const name of ["Show hidden (1)", "Delete all drawings"]) {
      expect(screen.getByRole("button", { name })).toBeDisabled();
      expect(screen.getByRole("button", { name })).toHaveAttribute("title", "Drawings are hidden (Hide all): show them first");
    }
  });

  it("asks the stored-VWAP hook only for the drawings on screen", async () => {
    drawingsApi.server = [
      { kind: "anchored_vwap", id: "anchored_vwap-1", time: 100, source: "stored", bands: false, band_color: "#000000" },
      { kind: "anchored_vwap", id: "anchored_vwap-2", time: 100, source: "stored", bands: false, band_color: "#000000", hidden: true },
    ];
    await renderReady(<ChartPage />);
    expect((storedVwap.drawings as { id: string }[]).map((d) => d.id)).toEqual(["anchored_vwap-1"]);
    fireEvent.click(screen.getByRole("button", { name: "Hide all drawings" }));
    expect(storedVwap.drawings).toEqual([]);
  });

  it("ignores a channel's B on A's bar (a vertical A-B), the tool staying armed", async () => {
    await renderReady(<ChartPage />);
    armTool("Parallel channel tool");
    click(100, 100);
    click(100, 120);
    expect(lastChartProps.current!.placement?.points).toEqual([{ time: 100, price: 100 }]);
    click(200, 110);
    click(150, 105.001); // no width on the grid: ignored too
    expect(drawings()).toEqual([]);
    click(150, 99);
    expect(drawings()).toEqual([expect.objectContaining({ kind: "channel", offset: -6 })]);
  });

  it("cycles the magnet off, weak, strong and hands it to the chart", async () => {
    await renderReady(<ChartPage />);
    const magnet = screen.getByRole("button", { name: "Magnet" });
    expect(lastChartProps.current!.magnet).toBe("off");
    fireEvent.click(magnet);
    expect(lastChartProps.current!.magnet).toBe("weak");
    expect(magnet).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(magnet);
    fireEvent.click(magnet);
    expect(lastChartProps.current!.magnet).toBe("off");
  });
});

describe("ChartPage candle-pattern markers and catalog plot hints (Story 33.11)", () => {
  const paneOf = (id: string) => lastChartProps.current!.panes!.find((p) => p.id === id)!;
  const legendAction = (action: "hide" | "settings" | "remove", group: string) =>
    act(() => lastChartProps.current!.onLegendAction!(action, group));
  const ENGULFING = { name: "CandlePattern", params: { pattern: "ENGULFING", trend_bars: 3 }, category: "native" };
  const ENGULFING_ID = "CandlePattern_pattern=ENGULFING,trend_bars=3";
  // A bullish hit at 60, a bearish one at 120, a gap at 180, no pattern at 240, bullish again at 300.
  const HITS = [
    { time: 60, value: 100 },
    { time: 120, value: -100 },
    { time: 180 },
    { time: 240, value: 0 },
    { time: 300, value: 100 },
  ];
  const placed = () => (lastChartProps.current!.patternMarkers ?? []).map((m) => [m.time, m.shape, m.position]);
  let baseCatalog: Awaited<ReturnType<typeof fetchIndicatorCatalog>>;

  beforeEach(async () => {
    baseCatalog = await fetchIndicatorCatalog();
    vi.mocked(fetchIndicatorCatalog).mockResolvedValue({
      ...baseCatalog,
      ZigZag: {
        params: { deviation_pct: 5 },
        panel: "overlay",
        category: "custom",
        plot: { value: "swing" },
        note: "repaints last leg",
        outputs: ["value"],
      },
    });
  });
  afterEach(() => {
    vi.mocked(fetchIndicatorCatalog).mockResolvedValue(baseCatalog);
  });

  async function mountWith(entries: object[], values: Record<string, unknown[]>): Promise<void> {
    vi.mocked(fetchCoinIndicatorConfig).mockResolvedValueOnce(entries as never);
    picker.values = values as Record<string, never[]>;
    render(page());
    await act(async () => {}); // catalog + saved config
  }

  it("draws bullish hits as arrows below the bar and bearish ones above, by default", async () => {
    await mountWith([ENGULFING], { [`${ENGULFING_ID}.value`]: HITS });

    expect(placed()).toEqual([
      [60, "arrowUp", "belowBar"],
      [120, "arrowDown", "aboveBar"],
      [300, "arrowUp", "belowBar"],
    ]);
    const markers = lastChartProps.current!.patternMarkers!;
    expect(markers[0]).toMatchObject({ id: `pat:${ENGULFING_ID}:60`, tooltip: ["Engulfing", "bullish"], color: CHART_TOKENS["--chart-up"] });
    expect(markers[1]).toMatchObject({ tooltip: ["Engulfing", "bearish"], color: CHART_TOKENS["--chart-down"] });
    // The legend row stays, on an invisible overlay that never scales the price axis.
    const spec = paneOf(`${ENGULFING_ID}.value`);
    expect(spec).toMatchObject({ kind: "Line", placement: "overlay", markersOnly: true, actionable: true });
    expect(spec.format!(100, 60)).toBe("Engulfing");
    expect(spec.format!(0, 240)).toBe("—");
  });

  it("draws a non-directional pattern's hit as a neutral circle above the bar", async () => {
    const doji = { name: "CandlePattern", params: { pattern: "DOJI", trend_bars: 3 }, category: "native" };
    await mountWith([doji], { "CandlePattern_pattern=DOJI,trend_bars=3.value": [{ time: 60, value: 100 }] });

    expect(placed()).toEqual([[60, "circle", "aboveBar"]]);
    expect(lastChartProps.current!.patternMarkers![0].tooltip).toEqual(["Doji", "neutral"]);
  });

  it("names a stale series' hits by the pattern its id carries, not the catalog default", async () => {
    // No saved entry owns these HAMMER values any more; the catalog's default pattern is ENGULFING.
    await mountWith([], { "CandlePattern_pattern=HAMMER,trend_bars=3.value": HITS });

    const spec = paneOf("CandlePattern_pattern=HAMMER,trend_bars=3.value");
    expect(spec).toMatchObject({ markersOnly: true, actionable: false });
    expect(spec.format!(100, 60)).toBe("Hammer");
  });

  it("removes the markers while the eye hides the entry, keeping its legend row", async () => {
    await mountWith([ENGULFING], { [`${ENGULFING_ID}.value`]: HITS });

    legendAction("hide", ENGULFING_ID);
    await act(async () => {});

    expect(lastChartProps.current!.patternMarkers).toEqual([]);
    expect(paneOf(`${ENGULFING_ID}.value`)).toMatchObject({ hidden: true, markersOnly: true });
  });

  it("switches to the ±100 pane from the settings' Display select, a style key and never a param", async () => {
    await mountWith([ENGULFING], { [`${ENGULFING_ID}.value`]: HITS });

    legendAction("settings", ENGULFING_ID);
    const dialog = screen.getByRole("dialog", { name: "CandlePattern (ENGULFING, 3)" });
    const display = within(dialog).getByLabelText<HTMLSelectElement>(/Display/);
    expect(display.value).toBe("markers");
    fireEvent.change(display, { target: { value: "pane" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Apply" }));
    await act(async () => {});

    expect(saveConfigMock).toHaveBeenLastCalledWith("BTC-USD-PERP.DYDX", [
      expect.objectContaining({ ...ENGULFING, style: { value: { display: "pane" } } }),
    ]);
    expect(paneOf(`${ENGULFING_ID}.value`)).toMatchObject({ kind: "Histogram", placement: "pane" });
    expect(paneOf(`${ENGULFING_ID}.value`).markersOnly).toBeUndefined();
    expect(lastChartProps.current!.patternMarkers).toEqual([]);
  });

  it("draws no marker after the Bar Replay cursor", async () => {
    mocks.candles = [60, 120, 180, 240, 300].map((time) => ({ time, open: 1, high: 2, low: 1, close: 1 }));
    await mountWith([ENGULFING], { [`${ENGULFING_ID}.value`]: HITS });
    expect(placed()).toHaveLength(3);

    fireEvent.click(screen.getByRole("button", { name: "Replay" }));
    act(() => lastChartProps.current!.onPointClick!({ time: 120, price: 1 }));

    expect(placed().map(([time]) => time)).toEqual([60, 120]);
  });

  it("carries a ZigZag's swing plot and its catalog note onto the legend title", async () => {
    const zigzag = { name: "ZigZag", params: { deviation_pct: 5 }, category: "custom" };
    await mountWith([zigzag], { "ZigZag_deviation_pct=5.value": [{ time: 60, value: 110 }, { time: 120 }] });

    expect(paneOf("ZigZag_deviation_pct=5.value")).toMatchObject({
      plot: "swing",
      placement: "overlay",
      groupLabel: "ZigZag (5) · repaints last leg",
    });
  });
});

describe("symbol search, watchlist, shortcuts and chart settings (Story 33.12)", () => {
  const ETH = "ETHUSDT-LINEAR.BYBIT";
  const HL = "BTC-USD-PERP.HYPERLIQUID";
  const SPOT = "BTCUSDT-SPOT.BYBIT";
  const markets = [
    { instrument_id: IID, symbol: "BTC", venue: "DYDX", same_asset: false, market: "perp", volume24h: 1_000_000, collected: true },
    { instrument_id: HL, symbol: "BTC", venue: "HYPERLIQUID", same_asset: true, market: "perp", volume24h: 2_500_000, collected: true },
    { instrument_id: ETH, symbol: "ETH", venue: "BYBIT", same_asset: false, market: "perp", volume24h: null, collected: true },
    { instrument_id: SPOT, symbol: "BTC", venue: "BYBIT", same_asset: false, market: "spot", volume24h: null, collected: true },
  ];
  const press = (init: KeyboardEventInit) => fireEvent.keyDown(window, init);
  const searchField = () => screen.getByRole("searchbox", { name: "Search markets" });
  const rows = () =>
    within(screen.getByRole("listbox", { name: "Markets" }))
      .queryAllByRole("option")
      .map((o) => o.textContent ?? "");

  function LocationProbe() {
    return <output aria-label="location">{useLocation().pathname}</output>;
  }
  const routed = () => (
    <MemoryRouter initialEntries={[`/chart/${IID}`]}>
      <Routes>
        <Route
          path="/chart/:iid"
          element={
            <>
              <ChartPage />
              <LocationProbe />
            </>
          }
        />
      </Routes>
    </MemoryRouter>
  );

  it("opens the search from the symbol, / and Ctrl+K, listing every market with venue, market and 24 h volume", async () => {
    marketsApi.get.mockResolvedValue({ items: markets, stale_venues: [] });
    render(page());

    fireEvent.click(screen.getByRole("button", { name: IID }));
    await act(async () => {});
    expect(marketsApi.get).toHaveBeenLastCalledWith(undefined);
    expect(rows()).toHaveLength(4);
    expect(rows()[1]).toContain("HYPERLIQUID · perp · 24h 2.50M");
    expect(rows()[2]).toContain("24h —"); // no volume is never 0
    fireEvent.click(within(screen.getByRole("dialog", { name: "Symbol search" })).getByRole("button", { name: "Close" }));

    press({ key: "/", code: "Slash" });
    expect(screen.getByRole("dialog", { name: "Symbol search" })).toBeInTheDocument();
    fireEvent.click(within(screen.getByRole("dialog", { name: "Symbol search" })).getByRole("button", { name: "Close" }));
    press({ key: "k", code: "KeyK", ctrlKey: true });
    expect(screen.getByRole("dialog", { name: "Symbol search" })).toBeInTheDocument();
  });

  it("filters by every token over symbol, venue and id in the server's order, and Enter opens the highlighted chart", async () => {
    marketsApi.get.mockResolvedValue({ items: markets, stale_venues: [] });
    render(routed());
    press({ key: "/", code: "Slash" });
    await act(async () => {});

    fireEvent.change(searchField(), { target: { value: "btc by" } });
    expect(rows()).toHaveLength(1);
    expect(rows()[0]).toContain(SPOT);
    fireEvent.change(searchField(), { target: { value: "BTC" } });
    expect(rows().map((r) => r.includes(HL))).toEqual([false, true, false]);
    fireEvent.keyDown(searchField(), { key: "ArrowDown" });
    fireEvent.keyDown(searchField(), { key: "Enter" });

    expect(screen.getByLabelText("location")).toHaveTextContent(`/chart/${HL}`);
  });

  it("says so when nothing matches, and Enter then does nothing", async () => {
    marketsApi.get.mockResolvedValue({ items: markets, stale_venues: [] });
    render(routed());
    press({ key: "/", code: "Slash" });
    await act(async () => {});

    fireEvent.change(searchField(), { target: { value: "doge" } });
    fireEvent.keyDown(searchField(), { key: "Enter" });

    expect(screen.getByText("No market matches")).toBeInTheDocument();
    expect(screen.getByLabelText("location")).toHaveTextContent(`/chart/${IID}`);
  });

  it("reports a market list that is down inline, without a crash", async () => {
    const { HttpError } = await import("../api/client");
    const unavailable = new HttpError(503, "no venue market list is live");
    marketsApi.get.mockRejectedValue(unavailable);
    render(page());
    press({ key: "/", code: "Slash" });
    await act(async () => {});

    expect(within(screen.getByRole("dialog", { name: "Symbol search" })).getByRole("status")).toHaveTextContent(
      "No venue's market list is live.",
    );
  });

  it("opens the compare search with Alt+C in Candles mode only, by the physical key", async () => {
    render(page());
    press({ key: "ç", code: "KeyC", altKey: true });
    expect(screen.getByRole("dialog", { name: "Compare symbol" })).toBeInTheDocument();
    fireEvent.click(within(screen.getByRole("dialog", { name: "Compare symbol" })).getByRole("button", { name: "Close" }));

    switchToLines();
    press({ key: "ç", code: "KeyC", altKey: true });
    expect(screen.queryByRole("dialog", { name: "Compare symbol" })).toBeNull();
  });

  it("arms the drawing tools from Alt+T / Alt+F / Alt+V / Alt+H, never a candles-only tool in Lines mode", async () => {
    await renderReady(page());
    press({ key: "†", code: "KeyT", altKey: true });
    expect(screen.getByRole("button", { name: "Trendline tool" })).toHaveAttribute("aria-pressed", "true");
    press({ key: "ƒ", code: "KeyF", altKey: true });
    expect(lastChartProps.current!.fibActive).toBe(true);
    press({ key: "√", code: "KeyV", altKey: true });
    expect(screen.getByRole("button", { name: "Vertical line tool" })).toHaveAttribute("aria-pressed", "true");

    switchToLines();
    press({ key: "˙", code: "KeyH", altKey: true });
    expect(screen.queryByRole("button", { name: "Horizontal line tool", pressed: true })).toBeNull();
    switchToCandles();
    press({ key: "˙", code: "KeyH", altKey: true });
    expect(screen.getByRole("button", { name: "Horizontal line tool" })).toHaveAttribute("aria-pressed", "true");
  });

  it("starts and exits a replay with Alt+R", () => {
    mocks.candles = [1, 2].map((t) => ({ time: t, open: 1, high: 1, low: 1, close: 1 }));
    render(page());
    press({ key: "®", code: "KeyR", altKey: true });
    expect(screen.getByRole("status")).toHaveTextContent("Click a candle to start the replay");
    press({ key: "®", code: "KeyR", altKey: true });
    expect(screen.queryByRole("group", { name: "Replay controls" })).toBeNull();
  });

  it("ignores Alt+R in Lines mode, where Replay is disabled", () => {
    mocks.candles = [1, 2].map((t) => ({ time: t, open: 1, high: 1, low: 1, close: 1 }));
    render(page());
    switchToLines();
    press({ key: "®", code: "KeyR", altKey: true });
    expect(screen.queryByRole("group", { name: "Replay controls" })).toBeNull();
    expect(lastChartProps.current!.markerTime ?? null).toBeNull();
    expect(screen.getByRole("button", { name: "Replay" })).toBeDisabled();
  });

  it("toggles the log scale with Shift+L, but not while a compare forces the percent scale", async () => {
    vi.useFakeTimers();
    render(page());
    press({ key: "L", code: "KeyL", shiftKey: true });
    expect(lastChartProps.current!.priceScale!.mode).toBe("log");
    await flushSave();
    expect(lastSaved().price_scale.mode).toBe("log");
    press({ key: "L", code: "KeyL", shiftKey: true });
    expect(lastChartProps.current!.priceScale!.mode).toBe("normal");

    cleanup();
    layoutApi.server[IID] = layoutOf({ compare: { symbols: [HL], spread: false } });
    render(page());
    press({ key: "L", code: "KeyL", shiftKey: true });
    expect(lastChartProps.current!.priceScale!.mode).toBe("percent");
    expect(screen.getByRole("button", { name: "Percent scale" })).toHaveAttribute("aria-pressed", "true");
  });

  it("switches timeframe from typed keys and Enter, showing the buffer; an unknown one changes nothing", () => {
    vi.useFakeTimers();
    render(page());
    press({ key: "4", code: "Digit4" });
    press({ key: "h", code: "KeyH" });
    expect(screen.getByRole("status", { name: "Typed timeframe" })).toHaveTextContent("4h");
    press({ key: "Enter", code: "Enter" });
    expect(hooks.candlesBar.at(-1)).toBe(14400);
    expect(screen.queryByRole("status", { name: "Typed timeframe" })).toBeNull();

    for (const key of ["d", "Enter"]) press({ key, code: key === "d" ? "KeyD" : "Enter" });
    expect(hooks.candlesBar.at(-1)).toBe(86400);
    for (const key of ["7", "Enter"]) press({ key, code: key === "7" ? "Digit7" : "Enter" });
    expect(hooks.candlesBar.at(-1)).toBe(86400);
  });

  it("drops a typed timeframe after 3 s idle and on Esc, and ignores digits in Lines mode", () => {
    vi.useFakeTimers();
    render(page());
    press({ key: "1", code: "Digit1" });
    act(() => vi.advanceTimersByTime(3001));
    expect(screen.queryByRole("status", { name: "Typed timeframe" })).toBeNull();
    press({ key: "1", code: "Digit1" });
    press({ key: "Escape", code: "Escape" });
    expect(screen.queryByRole("status", { name: "Typed timeframe" })).toBeNull();

    switchToLines();
    press({ key: "5", code: "Digit5" });
    expect(screen.queryByRole("status", { name: "Typed timeframe" })).toBeNull();
  });

  it("clears a typed timeframe on the switch to Lines, and Enter there changes nothing", () => {
    render(page());
    const before = hooks.candlesBar.at(-1);
    press({ key: "4", code: "Digit4" });
    press({ key: "h", code: "KeyH" });
    switchToLines();
    expect(screen.queryByRole("status", { name: "Typed timeframe" })).toBeNull();
    press({ key: "Enter", code: "Enter" });
    switchToCandles();
    press({ key: "Enter", code: "Enter" });
    expect(hooks.candlesBar.at(-1)).toBe(before);
  });

  it("does nothing while the operator types in a field or a dialog is open", () => {
    render(page());
    const zone = timeZoneSelect();
    fireEvent.keyDown(zone, { key: "/", code: "Slash" });
    fireEvent.keyDown(zone, { key: "L", code: "KeyL", shiftKey: true });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(lastChartProps.current!.priceScale!.mode).toBe("normal");

    press({ key: "?", code: "Slash", shiftKey: true });
    expect(screen.getByRole("dialog", { name: "Keyboard shortcuts" })).toBeInTheDocument();
    press({ key: "L", code: "KeyL", shiftKey: true });
    press({ key: "1", code: "Digit1" });
    expect(lastChartProps.current!.priceScale!.mode).toBe("normal");
    expect(screen.queryByRole("status", { name: "Typed timeframe" })).toBeNull();
  });

  it("lists exactly the shortcut table on the ? sheet", async () => {
    const { SHORTCUTS } = await import("../lib/shortcuts");
    render(page());
    press({ key: "?", code: "Slash", shiftKey: true });

    const sheet = screen.getByRole("dialog", { name: "Keyboard shortcuts" });
    const listed = within(sheet)
      .getAllByRole("row")
      .map((r) => [within(r).getByRole("rowheader").textContent, within(r).getByRole("cell").textContent]);
    expect(listed).toEqual(SHORTCUTS.map((row) => [row.keys, row.does]));
  });

  it("saves every new setting in the coin's layout and restores it on reload", async () => {
    vi.useFakeTimers();
    render(page());
    expect(lastChartProps.current!).toMatchObject({
      timeZone: "utc",
      sessionBreaks: false,
      countdown: { barSeconds: 60, enabled: true },
      lastPrice: { line: true, label: true },
      fitToWindow: false,
    });

    // Chart UX rework (2026-10-08): all five in the Settings menu.
    fireEvent.change(timeZoneSelect(), { target: { value: "local" } });
    for (const name of ["Session breaks", "Countdown", "Last price line"]) fireEvent.click(setting(name));
    await flushSave();
    const saved = lastSaved();
    expect(saved).toMatchObject({ time_zone: "local", session_breaks: true, bar_countdown: false, last_price: { line: false, label: true } });
    expect(JSON.stringify(saved)).not.toMatch(/focus|fullscreen|fit_to_window/i);

    cleanup();
    layoutApi.server[IID] = saved;
    render(page());
    expect(lastChartProps.current!).toMatchObject({
      timeZone: "local",
      sessionBreaks: true,
      countdown: { enabled: false },
      lastPrice: { line: false, label: true },
    });
    expect(timeZoneSelect()).toHaveValue("local");
    expect(setting("Session breaks")).toBeChecked();
    expect(setting("Countdown")).not.toBeChecked();
  });

  it("hands the chart identical bar times in every time zone (formatting only)", () => {
    mocks.candles = [60, 120].map((t) => ({ time: t, open: 1, high: 1, low: 1, close: 1 }));
    render(page());
    const utc = lastChartProps.current!.data;

    fireEvent.change(timeZoneSelect(), { target: { value: "local" } });

    expect(lastChartProps.current!.timeZone).toBe("local");
    expect(lastChartProps.current!.data).toBe(utc);
    expect(lastChartProps.current!.data!.map((d) => d.time)).toEqual([60, 120]);
  });

  it("hides the countdown in Lines mode and during a replay", () => {
    mocks.candles = [1, 2].map((t) => ({ time: t, open: 1, high: 1, low: 1, close: 1 }));
    render(page());
    fireEvent.click(screen.getByRole("button", { name: "Replay" }));
    act(() => lastChartProps.current!.onPointClick!({ time: 1, price: 1 }));
    expect(lastChartProps.current!.countdown!.enabled).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Exit" }));
    expect(lastChartProps.current!.countdown!.enabled).toBe(true);
    switchToLines();
    expect(lastChartProps.current!.countdown!.enabled).toBe(false);
  });

  it("pins and unpins the chart on the server-side watchlist rail, priced from rankings:live, — where unranked", async () => {
    watchlistApi.server = [HL, SPOT];
    liveRanks.latest = {
      mode: "volume",
      updated_at: Date.now() * 1_000_000,
      stale_instrument_ids: [],
      ranks: [{ instrument_id: HL, symbol: "BTC", venue: "HYPERLIQUID", price: 61234.5, pct_24h: -1.234 }],
    };
    render(page());
    fireEvent.click(screen.getByRole("button", { name: "Watchlist" }));
    await act(async () => {});

    const rail = screen.getByRole("complementary", { name: "Watchlist" });
    const links = within(rail).getAllByRole("link");
    expect(links.map((l) => l.textContent)).toEqual([`BTCHYPERLIQUID61234.5000-1.23%`, `${SPOT}———`]);
    expect(links[0]).toHaveAttribute("href", `/chart/${HL}`);

    fireEvent.click(within(rail).getByRole("button", { name: "Pin this chart" }));
    await act(async () => {});
    expect(watchlistApi.save).toHaveBeenLastCalledWith([HL, SPOT, IID]);
    fireEvent.click(within(rail).getByRole("button", { name: `Unpin ${HL}` }));
    await act(async () => {});
    expect(watchlistApi.save).toHaveBeenLastCalledWith([SPOT, IID]);
    expect(localStorage.length).toBe(0);
  });

  it("shows a refused watchlist save inline and the server's list again", async () => {
    watchlistApi.server = [HL];
    watchlistApi.save.mockRejectedValue(new Error("PUT /api/watchlist failed: 422"));
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    render(page());
    fireEvent.click(screen.getByRole("button", { name: "Watchlist" }));
    await act(async () => {});

    fireEvent.click(screen.getByRole("button", { name: "Pin this chart" }));
    await act(async () => {});

    const rail = screen.getByRole("complementary", { name: "Watchlist" });
    expect(within(rail).getByRole("alert")).toHaveTextContent("Watchlist could not be saved: PUT /api/watchlist failed: 422");
    expect(within(rail).getAllByRole("link")).toHaveLength(1);
    expect(errors).toHaveBeenCalled();
    errors.mockRestore();
  });
});
