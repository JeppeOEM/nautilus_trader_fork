import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  fetchFilterPresets,
  fetchRankings,
  fetchTechnicalsColumns,
  fetchTechnicalsValues,
  saveFilterPresets,
  saveTechnicalsColumns,
  setRankingMode,
} from "../api/client";
import type { FilterPresetItem } from "../api/client";
import type { RankingsLiveMessage } from "./RankingsPage";

// api/client's fetchRankings would otherwise hit a real network fetch under jsdom --
// mocked so the seed React Query call resolves instantly and predictably; every test
// here exercises the live-channel-driven path anyway (useLiveChannel below).
vi.mock("../api/client", () => ({
  fetchRankings: vi.fn().mockResolvedValue({
    items: [],
    updated_at: 0,
    mode: "volume",
    stale_instrument_ids: [],
  }),
  // Technicals tab (Story 17.5): the shared IndicatorPicker and the values poll.
  fetchIndicatorCatalog: vi.fn().mockResolvedValue({
    RelativeStrengthIndex: { params: { period: 14 }, panel: "oscillator", category: "native" },
  }),
  fetchTechnicalsColumns: vi.fn().mockResolvedValue([]),
  saveTechnicalsColumns: vi.fn().mockResolvedValue(undefined),
  fetchTechnicalsValues: vi.fn().mockResolvedValue({}),
  // Story 25.1a: the ranking-mode switch.
  setRankingMode: vi.fn().mockResolvedValue({ mode: "volatility" }),
  // Story 33.7: saved filter presets (server-side).
  fetchFilterPresets: vi.fn().mockResolvedValue([]),
  saveFilterPresets: vi.fn().mockImplementation((presets: unknown) => Promise.resolve(presets)),
}));

const useLiveChannelMock = vi.fn();
vi.mock("../hooks/useLiveChannel", () => ({
  useLiveChannel: () => useLiveChannelMock(),
}));

const navigateMock = vi.fn();
vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return { ...actual, useNavigate: () => navigateMock };
});

// Imported after the mocks above so RankingsPage picks up the mocked hooks/client.
const { default: RankingsPage } = await import("./RankingsPage");

// Hand-declared list of the Performance tab's column labels (the exact set
// RankingsPage.tsx's RANKING_COLS renders), deliberately NOT imported from the
// page module -- a dropped/renamed column must fail these tests rather than
// tautologically pass against whatever the module currently declares.
const PERFORMANCE_COL_LABELS = [
  "OFI10z",
  "OBI10",
  "OBI5",
  "OBI3",
  "CVD",
  "Spread",
  "Vol d 60s",
  "Price",
  "1h %",
  "24h %",
  "1w %",
  "1m %",
  "Vol 24h σ (trade closes)",
  "Vol 1h σ (mids)",
  "Vol24h",
  "OI",
  "OI Δ1h %",
  "OI Δ24h %",
  "Funding",
  "Basis (bps)",
  "Liq 1h",
  "Liq L/S",
  "Forced %",
  "Rel vol",
  "24h range",
];

function pageElement(queryClient: QueryClient) {
  return (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <RankingsPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(pageElement(queryClient));
}

function liveMessage(overrides: Partial<RankingsLiveMessage> = {}): RankingsLiveMessage {
  return {
    mode: "volume",
    updated_at: Date.now() * 1_000_000, // fresh, ns
    ranks: [{ instrument_id: "BTC-USD-PERP.DYDX", ofi_10_z: 0.5, price: 100.1234 }],
    stale_instrument_ids: [],
    ...overrides,
  };
}

beforeEach(() => {
  useLiveChannelMock.mockReset();
  navigateMock.mockReset();
  localStorage.clear(); // venue chip selection (Story 22.10) must not leak between tests
});

// The project's shared Vitest setup (src/test/setup.ts) doesn't register RTL's
// automatic cleanup -- DocsPage.test.tsx never noticed because its two tests query
// disjoint text. This file's tests deliberately reuse the same instrument id
// ("BTC-USD-PERP.DYDX") across cases, so a leftover previous render makes getByText
// ambiguous unless each test's DOM is torn down first.
afterEach(() => {
  cleanup();
  localStorage.clear();
});

describe("RankingsPage", () => {
  it("shows a loading state before any message has arrived", () => {
    useLiveChannelMock.mockReturnValue({ latest: null, connected: false });

    renderPage();

    expect(screen.getByText(/Loading rankings/i)).toBeInTheDocument();
  });

  it("marks a row stale when the message's updated_at exceeds the heartbeat threshold", () => {
    const staleUpdatedAtNs = (Date.now() - 20_000) * 1_000_000; // 20s old, ns
    useLiveChannelMock.mockReturnValue({
      latest: liveMessage({ updated_at: staleUpdatedAtNs }),
      connected: true,
    });

    renderPage();

    const row = screen.getByText("BTC-USD-PERP.DYDX").closest("tr");
    expect(row).not.toBeNull();
    expect(row).toHaveAttribute("data-stale", "true");
  });

  it("does not mark a row stale when the message is fresh", () => {
    useLiveChannelMock.mockReturnValue({ latest: liveMessage(), connected: true });

    renderPage();

    const row = screen.getByText("BTC-USD-PERP.DYDX").closest("tr");
    expect(row).toHaveAttribute("data-stale", "false");
  });

  it("navigates to /chart/:iid when a rankings row is clicked", () => {
    useLiveChannelMock.mockReturnValue({ latest: liveMessage(), connected: true });

    renderPage();
    fireEvent.click(screen.getByText("BTC-USD-PERP.DYDX"));

    expect(navigateMock).toHaveBeenCalledWith("/chart/BTC-USD-PERP.DYDX");
  });

  it("navigates to /history/:iid from the history button without also opening the chart", () => {
    useLiveChannelMock.mockReturnValue({ latest: liveMessage(), connected: true });

    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /31-day history for BTC-USD-PERP.DYDX/ }));

    expect(navigateMock).toHaveBeenCalledTimes(1);
    expect(navigateMock).toHaveBeenCalledWith("/history/BTC-USD-PERP.DYDX");
  });

  it("opens the chart on Enter for a focused row, but not for Enter on its history button", () => {
    useLiveChannelMock.mockReturnValue({ latest: liveMessage(), connected: true });

    renderPage();
    const row = screen.getByText("BTC-USD-PERP.DYDX").closest("tr")!;
    fireEvent.keyDown(screen.getByRole("button", { name: /31-day history/ }), { key: "Enter" });
    expect(navigateMock).not.toHaveBeenCalled();

    fireEvent.keyDown(row, { key: "Enter" });
    expect(navigateMock).toHaveBeenCalledWith("/chart/BTC-USD-PERP.DYDX");
  });

  it("renders row order verbatim (message order), keyed by instrument_id", () => {
    useLiveChannelMock.mockReturnValue({
      latest: liveMessage({
        ranks: [
          { instrument_id: "ETH-USD-PERP.DYDX", price: 2000 },
          { instrument_id: "BTC-USD-PERP.DYDX", price: 100 },
        ],
      }),
      connected: true,
    });

    renderPage();

    const cells = screen.getAllByRole("row").slice(1); // skip header row
    expect(cells[0].textContent).toContain("ETH-USD-PERP.DYDX");
    expect(cells[1].textContent).toContain("BTC-USD-PERP.DYDX");
  });

  it("shows Performance as the default tab with every metric column visible", () => {
    useLiveChannelMock.mockReturnValue({ latest: liveMessage(), connected: true });

    renderPage();

    expect(screen.getByText("Performance")).toBeInTheDocument();
    expect(screen.getByText("Technicals")).toBeInTheDocument();
    // No tab clicked yet -- Performance's columns rendering on load IS the
    // "Performance is the default" claim.
    for (const label of PERFORMANCE_COL_LABELS) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("hides Performance's columns and shows the empty state on the Technicals tab", () => {
    useLiveChannelMock.mockReturnValue({ latest: liveMessage(), connected: true });

    renderPage();
    fireEvent.click(screen.getByText("Technicals"));

    expect(screen.getByText("no columns yet — add one below")).toBeInTheDocument();
    for (const label of PERFORMANCE_COL_LABELS) {
      expect(screen.queryByText(label)).not.toBeInTheDocument();
    }
    // The pinned columns stay visible, and lead, regardless of active tab (AC #1; Story 29.1).
    expect(screen.getAllByRole("columnheader").map((th) => th.textContent)).toEqual([
      "Rank",
      "Symbol",
      "Exchange",
      "Instrument",
    ]);
    expect(screen.getByText("no columns yet — add one below")).toHaveAttribute("colspan", "4");
    expect(screen.getByText("BTC-USD-PERP.DYDX")).toBeInTheDocument();
  });

  it("restores Performance's columns when switching back from Technicals", () => {
    useLiveChannelMock.mockReturnValue({ latest: liveMessage(), connected: true });

    renderPage();
    fireEvent.click(screen.getByText("Technicals"));
    fireEvent.click(screen.getByText("Performance"));

    expect(screen.queryByText("no columns yet — add one below")).not.toBeInTheDocument();
    for (const label of PERFORMANCE_COL_LABELS) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("reflects a live update that arrived while Technicals was active, without throwing", () => {
    useLiveChannelMock.mockReturnValue({ latest: liveMessage(), connected: true });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { rerender } = render(pageElement(queryClient));

    fireEvent.click(screen.getByText("Technicals"));

    // A fresh rankings:live tick lands while Technicals is showing -- the
    // re-render must not throw, and the tab keeps its empty state.
    useLiveChannelMock.mockReturnValue({
      latest: liveMessage({ ranks: [{ instrument_id: "BTC-USD-PERP.DYDX", price: 999.1234 }] }),
      connected: true,
    });
    rerender(pageElement(queryClient));
    expect(screen.getByText("no columns yet — add one below")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Performance"));

    // The newer message's price renders -- live data kept flowing while the
    // Performance columns were hidden, with no stale/cached render on return.
    expect(screen.getByText("999.1234")).toBeInTheDocument();
  });

  it("does not refetch rankings when switching tabs", async () => {
    useLiveChannelMock.mockReturnValue({ latest: liveMessage(), connected: true });

    renderPage();
    await waitFor(() => expect(vi.mocked(fetchRankings)).toHaveBeenCalled());
    const callsAfterLoad = vi.mocked(fetchRankings).mock.calls.length;

    fireEvent.click(screen.getByText("Technicals"));
    fireEvent.click(screen.getByText("Performance"));
    fireEvent.click(screen.getByText("Technicals"));

    expect(vi.mocked(fetchRankings).mock.calls.length).toBe(callsAfterLoad);
  });

  describe("Technicals columns", () => {
    const rsi = { name: "RelativeStrengthIndex", params: {}, category: "native" };
    const macd = { name: "MovingAverageConvergenceDivergence", params: {}, category: "native" };

    function configureTechnicals() {
      useLiveChannelMock.mockReturnValue({ latest: liveMessage(), connected: true });
      vi.mocked(fetchTechnicalsColumns).mockResolvedValue([rsi, macd]);
      vi.mocked(fetchTechnicalsValues).mockResolvedValue({
        "BTC-USD-PERP.DYDX": { "0.value": 55.5, "1.value": 1.25, "1.signal": 0.5 },
      });
      vi.mocked(saveTechnicalsColumns).mockClear();
    }

    it("renders configured columns grouped under one header per entry, with each coin's values", async () => {
      configureTechnicals();

      renderPage();
      fireEvent.click(screen.getByText("Technicals"));

      // The picker below the table also lists each entry's name -- pick the table header.
      // The header's span only widens once the first values reveal MACD's two outputs.
      await waitFor(() => {
        const header = screen
          .getAllByText("MovingAverageConvergenceDivergence")
          .find((el) => el.tagName === "TH");
        expect(header).toHaveAttribute("colspan", "2");
      });
      expect(await screen.findByText("55.5000")).toBeInTheDocument();
      expect(screen.getByText("signal")).toBeInTheDocument();
      expect(screen.getByText("0.5000")).toBeInTheDocument();
    });

    it("shows a CandlePattern column's +100 / -100 / 0 per coin (Story 27.7: the screener as a scanner)", async () => {
      const pattern = { name: "CandlePattern", params: { pattern: "ENGULFING" }, category: "native", bar_seconds: 60 };
      useLiveChannelMock.mockReturnValue({
        latest: liveMessage({
          ranks: [
            { instrument_id: "AAA-USD-PERP.DYDX", price: 1 },
            { instrument_id: "BBB-USD-PERP.DYDX", price: 2 },
            { instrument_id: "CCC-USD-PERP.DYDX", price: 3 },
          ],
        }),
        connected: true,
      });
      vi.mocked(fetchTechnicalsColumns).mockResolvedValue([pattern]);
      vi.mocked(fetchTechnicalsValues).mockResolvedValue({
        "AAA-USD-PERP.DYDX": { "0.value": 100 },
        "BBB-USD-PERP.DYDX": { "0.value": -100 },
        "CCC-USD-PERP.DYDX": { "0.value": 0 },
      });

      renderPage();
      fireEvent.click(screen.getByText("Technicals"));

      const cellOf = (iid: string) => within(screen.getByText(iid).closest("tr")!).getAllByRole("cell").at(-1);
      await waitFor(() => expect(cellOf("AAA-USD-PERP.DYDX")).toHaveTextContent("100.0000"));
      expect(cellOf("BBB-USD-PERP.DYDX")).toHaveTextContent("-100.0000");
      expect(cellOf("CCC-USD-PERP.DYDX")).toHaveTextContent("0.0000");
    });

    it("removes a column from its header, persisting the list without it", async () => {
      configureTechnicals();

      renderPage();
      fireEvent.click(screen.getByText("Technicals"));
      fireEvent.click(await screen.findByRole("button", { name: "Remove RelativeStrengthIndex column" }));

      await waitFor(() => expect(saveTechnicalsColumns).toHaveBeenCalledWith([macd]));
    });

    it("shows each column's timeframe (1H when unset) and saves a change made in the header", async () => {
      configureTechnicals();
      vi.mocked(fetchTechnicalsColumns).mockResolvedValue([rsi, { ...macd, bar_seconds: 86400 }]);

      renderPage();
      fireEvent.click(screen.getByText("Technicals"));
      const rsiTimeframe = await screen.findByLabelText<HTMLSelectElement>("RelativeStrengthIndex timeframe");
      expect(rsiTimeframe.value).toBe("3600");
      expect(screen.getByLabelText<HTMLSelectElement>("MovingAverageConvergenceDivergence timeframe").value).toBe("86400");

      fireEvent.change(rsiTimeframe, { target: { value: "14400" } });

      await waitFor(() =>
        expect(saveTechnicalsColumns).toHaveBeenCalledWith([{ ...rsi, bar_seconds: 14400 }, { ...macd, bar_seconds: 86400 }]),
      );
    });

    it("builds a rapid second header removal on the first one instead of undoing it", async () => {
      configureTechnicals();

      renderPage();
      fireEvent.click(screen.getByText("Technicals"));
      fireEvent.click(await screen.findByRole("button", { name: "Remove RelativeStrengthIndex column" }));
      fireEvent.click(screen.getByRole("button", { name: "Remove MovingAverageConvergenceDivergence column" }));

      await waitFor(() => expect(saveTechnicalsColumns).toHaveBeenLastCalledWith([]));
    });
  });

  describe("filter panel", () => {
    const ranks = [
      { instrument_id: "AAA-USD-PERP.DYDX", price: 1, pct_24h: 5, obi_5: 0.7 },
      { instrument_id: "BBB-USD-PERP.DYDX", price: 2, pct_24h: 5, obi_5: 0.2 },
      { instrument_id: "CCC-USD-PERP.DYDX", price: 3, pct_24h: -1, obi_5: 0.9 },
    ];

    function addFilter(fieldLabel: string, op: string, value: string) {
      fireEvent.click(screen.getByRole("button", { name: "Add filter" }));
      fireEvent.change(screen.getByLabelText("Filter field"), {
        target: { value: Array.from(screen.getByLabelText<HTMLSelectElement>("Filter field").options).find((o) => o.text === fieldLabel)!.value },
      });
      fireEvent.change(screen.getByLabelText("Filter operator"), { target: { value: op } });
      fireEvent.change(screen.getByLabelText("Filter value"), { target: { value } });
      fireEvent.click(screen.getByRole("button", { name: "Add" }));
    }

    function shownInstruments(): string[] {
      return screen.queryAllByText(/-USD-PERP\.DYDX/).map((el) => (el.textContent ?? "").replace("⏲", ""));
    }

    beforeEach(() => {
      useLiveChannelMock.mockReturnValue({ latest: liveMessage({ ranks }), connected: true });
    });

    // The Technicals pending/errored tests leave a never-settling / rejecting values mock behind
    // otherwise.
    afterEach(() => {
      vi.mocked(fetchTechnicalsColumns).mockResolvedValue([]);
      vi.mocked(fetchTechnicalsValues).mockReset().mockResolvedValue({});
    });

    it("narrows rows, combines conditions with AND, and restores rows when a condition is removed", () => {
      renderPage();

      addFilter("24h %", ">", "0");
      expect(shownInstruments()).toEqual(["AAA-USD-PERP.DYDX", "BBB-USD-PERP.DYDX"]);

      addFilter("OBI5", ">", "0.5"); // BBB matches only the first condition
      expect(shownInstruments()).toEqual(["AAA-USD-PERP.DYDX"]);

      fireEvent.click(screen.getByRole("button", { name: /Remove filter OBI5/ }));
      expect(shownInstruments()).toEqual(["AAA-USD-PERP.DYDX", "BBB-USD-PERP.DYDX"]);
    });

    it("keeps each row's true rank and the filtered set across a tab switch", () => {
      renderPage();

      addFilter("24h %", "<", "0");
      const row = screen.getByText("CCC-USD-PERP.DYDX").closest("tr")!;
      expect(row.querySelector("td")?.textContent).toBe("3"); // rank in the full message, not 1

      fireEvent.click(screen.getByText("Technicals"));
      expect(screen.getByText("CCC-USD-PERP.DYDX")).toBeInTheDocument();
      expect(screen.queryByText("AAA-USD-PERP.DYDX")).not.toBeInTheDocument();
    });

    it("excludes rows with no value for the filtered field instead of treating it as 0", () => {
      useLiveChannelMock.mockReturnValue({
        latest: liveMessage({ ranks: [...ranks, { instrument_id: "NNN-USD-PERP.DYDX", price: 4 }] }),
        connected: true,
      });
      renderPage();

      addFilter("24h %", "<", "100");

      expect(screen.queryByText("NNN-USD-PERP.DYDX")).not.toBeInTheDocument();
    });

    it("shows an Exchange column and filters multi-venue rows with `venue = X`, one row per instrument_id", () => {
      useLiveChannelMock.mockReturnValue({
        latest: liveMessage({
          ranks: [
            { instrument_id: "BTC-USD-PERP.DYDX", venue: "DYDX", venue_kind: "dex", price: 1 },
            { instrument_id: "BTCUSDT-LINEAR.BYBIT", venue: "BYBIT", venue_kind: "cex", price: 2 },
            { instrument_id: "BTC-USD-PERP.HYPERLIQUID", venue: "HYPERLIQUID", venue_kind: "dex", price: 3 },
          ],
        }),
        connected: true,
      });
      renderPage();

      expect(screen.getByRole("columnheader", { name: "Exchange" })).toBeInTheDocument();
      // The Exchange cell replaced the Performance tab's own Venue/Market columns (Story 29.1).
      expect(screen.queryByRole("columnheader", { name: "Venue" })).not.toBeInTheDocument();
      expect(within(screen.getByRole("table")).getByText("BYBIT")).toBeInTheDocument();
      expect(screen.getAllByRole("row")).toHaveLength(4); // header + 3 distinct venue-qualified rows

      addFilter("Exchange (venue)", "=", "bybit");

      expect(screen.getByText("BTCUSDT-LINEAR.BYBIT")).toBeInTheDocument();
      expect(screen.queryByText("BTC-USD-PERP.DYDX")).not.toBeInTheDocument();
      expect(screen.queryByText("BTC-USD-PERP.HYPERLIQUID")).not.toBeInTheDocument();
    });

    it("keeps spot and linear rows of one symbol distinct and filters with `market = spot`", () => {
      useLiveChannelMock.mockReturnValue({
        latest: liveMessage({
          ranks: [
            { instrument_id: "BTCUSDT-SPOT.BYBIT", venue: "BYBIT", venue_kind: "cex", market: "spot", price: 1 },
            { instrument_id: "BTCUSDT-LINEAR.BYBIT", venue: "BYBIT", venue_kind: "cex", market: "perp", price: 2 },
          ],
        }),
        connected: true,
      });
      renderPage();

      expect(screen.getAllByRole("row")).toHaveLength(3);
      expect(screen.queryByRole("columnheader", { name: "Market" })).not.toBeInTheDocument();
      // The market kind is the Exchange cell's tag instead.
      const table = within(screen.getByRole("table"));
      expect(table.getByText("· spot")).toHaveClass("rankings-market-tag");
      expect(table.getByText("· perp")).toHaveClass("rankings-market-tag");

      addFilter("Market (perp/spot)", "=", "spot");

      expect(screen.getByText("BTCUSDT-SPOT.BYBIT")).toBeInTheDocument();
      expect(screen.queryByText("BTCUSDT-LINEAR.BYBIT")).not.toBeInTheDocument();
    });

    it("filters by CEX/DEX kind", () => {
      useLiveChannelMock.mockReturnValue({
        latest: liveMessage({
          ranks: [
            { instrument_id: "BTC-USD-PERP.DYDX", venue: "DYDX", venue_kind: "dex", price: 1 },
            { instrument_id: "BTCUSDT-LINEAR.BYBIT", venue: "BYBIT", venue_kind: "cex", price: 2 },
          ],
        }),
        connected: true,
      });
      renderPage();

      addFilter("Kind (cex/dex)", "=", "cex");

      expect(screen.getByText("BTCUSDT-LINEAR.BYBIT")).toBeInTheDocument();
      expect(screen.queryByText("BTC-USD-PERP.DYDX")).not.toBeInTheDocument();
    });

    it("drops a Technicals filter when its column is removed, instead of emptying the table", async () => {
      const rsi = { name: "RelativeStrengthIndex", params: {}, category: "native" };
      vi.mocked(fetchTechnicalsColumns).mockResolvedValue([rsi]);
      vi.mocked(fetchTechnicalsValues).mockResolvedValue({ "AAA-USD-PERP.DYDX": { "0.value": 25 } });
      renderPage();
      fireEvent.click(screen.getByRole("button", { name: "Add filter" }));
      await waitFor(() => {
        const options = Array.from(screen.getByLabelText<HTMLSelectElement>("Filter field").options).map((o) => o.text);
        expect(options).toContain("RelativeStrengthIndex.value");
      });
      fireEvent.click(screen.getByRole("button", { name: "Add filter" }));
      addFilter("RelativeStrengthIndex.value", "<", "30");
      expect(shownInstruments()).toEqual(["AAA-USD-PERP.DYDX"]);

      fireEvent.click(screen.getByText("Technicals"));
      vi.mocked(fetchTechnicalsColumns).mockResolvedValue([]);
      fireEvent.click(await screen.findByRole("button", { name: "Remove RelativeStrengthIndex column" }));

      await waitFor(() => expect(shownInstruments()).toHaveLength(3));
      expect(screen.queryByRole("button", { name: /Remove filter/ })).not.toBeInTheDocument();
    });

    it("filters on a Technicals output from the Performance tab", async () => {
      vi.mocked(fetchTechnicalsColumns).mockResolvedValue([
        { name: "RelativeStrengthIndex", params: {}, category: "native" },
      ]);
      vi.mocked(fetchTechnicalsValues).mockResolvedValue({
        "AAA-USD-PERP.DYDX": { "0.value": 25 },
        "BBB-USD-PERP.DYDX": { "0.value": 60 },
        "CCC-USD-PERP.DYDX": { "0.value": 10 },
      });
      renderPage();
      fireEvent.click(screen.getByRole("button", { name: "Add filter" }));
      await waitFor(() => {
        const options = Array.from(screen.getByLabelText<HTMLSelectElement>("Filter field").options).map((o) => o.text);
        expect(options).toContain("RelativeStrengthIndex.value");
      });
      fireEvent.click(screen.getByRole("button", { name: "Add filter" })); // close, then reuse helper
      addFilter("RelativeStrengthIndex.value", "<", "30");

      expect(shownInstruments()).toEqual(["AAA-USD-PERP.DYDX", "CCC-USD-PERP.DYDX"]);
    });

    it("matches `=` against what a ranking cell shows (DW-135)", () => {
      useLiveChannelMock.mockReturnValue({
        latest: liveMessage({ ranks: [{ ...ranks[0], obi_5: 0.49996 }, ranks[1], ranks[2]] }),
        connected: true,
      });
      renderPage();
      const row = screen.getByText("AAA-USD-PERP.DYDX").closest("tr")!;
      expect(within(row).getByText("0.500")).toBeInTheDocument();

      addFilter("OBI5", "=", "0.5");

      expect(shownInstruments()).toEqual(["AAA-USD-PERP.DYDX"]);
    });

    it("matches `=` against what a Technicals cell shows (DW-135)", async () => {
      vi.mocked(fetchTechnicalsColumns).mockResolvedValue([
        { name: "RelativeStrengthIndex", params: {}, category: "native" },
      ]);
      vi.mocked(fetchTechnicalsValues).mockResolvedValue({
        "AAA-USD-PERP.DYDX": { "0.value": 29.99996 }, // shows 30.0000
        "BBB-USD-PERP.DYDX": { "0.value": 30.0001 },
        "CCC-USD-PERP.DYDX": { "0.value": 29.9999 },
      });
      renderPage();
      fireEvent.click(screen.getByRole("button", { name: "Add filter" }));
      await waitFor(() => {
        const options = Array.from(screen.getByLabelText<HTMLSelectElement>("Filter field").options).map((o) => o.text);
        expect(options).toContain("RelativeStrengthIndex.value");
      });
      fireEvent.click(screen.getByRole("button", { name: "Add filter" })); // close, then reuse helper
      addFilter("RelativeStrengthIndex.value", "=", "30");

      expect(shownInstruments()).toEqual(["AAA-USD-PERP.DYDX"]);
      fireEvent.click(screen.getByText("Technicals"));
      expect(await screen.findByText("30.0000")).toBeInTheDocument();
    });

    // A Technicals filter set, then its values query replaced by one that has not resolved or
    // has failed (removing another column changes the query key): rows stay, with the note.
    async function technicalsFilterThenRefetch(values: () => Promise<never>) {
      const rsi = { name: "RelativeStrengthIndex", params: {}, category: "native" };
      const macd = { name: "MovingAverageConvergenceDivergence", params: {}, category: "native" };
      vi.mocked(fetchTechnicalsColumns).mockResolvedValue([rsi, macd]);
      vi.mocked(fetchTechnicalsValues).mockResolvedValue({ "AAA-USD-PERP.DYDX": { "0.value": 25 } });
      renderPage();
      fireEvent.click(screen.getByRole("button", { name: "Add filter" }));
      await waitFor(() => {
        const options = Array.from(screen.getByLabelText<HTMLSelectElement>("Filter field").options).map((o) => o.text);
        expect(options).toContain("RelativeStrengthIndex.value");
      });
      fireEvent.click(screen.getByRole("button", { name: "Add filter" }));
      addFilter("RelativeStrengthIndex.value", "<", "30");
      expect(shownInstruments()).toEqual(["AAA-USD-PERP.DYDX"]);

      vi.mocked(fetchTechnicalsValues).mockImplementation(values);
      vi.mocked(fetchTechnicalsColumns).mockResolvedValue([rsi]);
      fireEvent.click(screen.getByText("Technicals"));
      fireEvent.click(await screen.findByRole("button", { name: "Remove MovingAverageConvergenceDivergence column" }));
    }

    it("keeps every row and notes a Technicals filter while its values are loading (DW-135)", async () => {
      await technicalsFilterThenRefetch(() => new Promise<never>(() => {}));

      await waitFor(() => expect(shownInstruments()).toHaveLength(3));
      expect(screen.getByText(/1 Technicals filter\(s\) not applied yet — waiting for indicator values$/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Remove filter .*RelativeStrengthIndex\.value/ })).toBeInTheDocument();
    });

    it("keeps every row and notes a Technicals filter with the error when its values failed (DW-135)", async () => {
      await technicalsFilterThenRefetch(() => Promise.reject(new Error("values down")));

      expect(await screen.findByText(/Technicals filter\(s\) not applied yet .*\(values down\)/)).toBeInTheDocument();
      expect(shownInstruments()).toHaveLength(3);
    });

    describe("venue chips", () => {
      const multiVenueRanks = [
        { instrument_id: "BTCUSDT-LINEAR.BYBIT", venue: "BYBIT", price: 1 },
        { instrument_id: "BTC-USD-PERP.DYDX", venue: "DYDX", price: 2 },
        { instrument_id: "BTC-USD-PERP.HYPERLIQUID", venue: "HYPERLIQUID", price: 3 },
        { instrument_id: "ETH-USD-PERP.DYDX", venue: "DYDX", price: 4 },
      ];

      function showLive(ranks: RankingsLiveMessage["ranks"]): void {
        useLiveChannelMock.mockReturnValue({ latest: liveMessage({ ranks }), connected: true });
      }

      function chip(venue: string): HTMLElement {
        return within(screen.getByRole("group", { name: "Venues" })).getByRole("button", { name: venue });
      }

      // [rank, instrument_id] per rendered body row -- rank is the first cell.
      function shownRows(): [string, string][] {
        return screen
          .getAllByRole("row")
          .slice(1)
          .map((tr) => {
            const cells = within(tr).getAllByRole("cell");
            return [cells[0].textContent ?? "", (cells[3].textContent ?? "").replace("⏲", "")];
          });
      }

      beforeEach(() => showLive(multiVenueRanks));

      it("shows every venue's rows by default, with one selected chip per venue (sorted)", () => {
        renderPage();

        const chips = within(screen.getByRole("group", { name: "Venues" })).getAllByRole("button");
        expect(chips.map((c) => c.textContent)).toEqual(["BYBIT", "DYDX", "HYPERLIQUID"]);
        chips.forEach((c) => expect(c).toHaveAttribute("aria-pressed", "true"));
        expect(shownRows().map(([, iid]) => iid)).toEqual(multiVenueRanks.map((r) => r.instrument_id));
      });

      it("deselecting BYBIT hides only Bybit rows and keeps the message rank", () => {
        renderPage();

        fireEvent.click(chip("BYBIT"));

        expect(chip("BYBIT")).toHaveAttribute("aria-pressed", "false");
        expect(shownRows()).toEqual([
          ["2", "BTC-USD-PERP.DYDX"],
          ["3", "BTC-USD-PERP.HYPERLIQUID"],
          ["4", "ETH-USD-PERP.DYDX"],
        ]);
      });

      it("shows a venue that appears after others were deselected", () => {
        showLive(multiVenueRanks.filter((r) => r.venue !== "HYPERLIQUID"));
        const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        const { rerender } = render(pageElement(queryClient));
        fireEvent.click(chip("BYBIT"));

        showLive(multiVenueRanks);
        rerender(pageElement(queryClient));

        expect(chip("HYPERLIQUID")).toHaveAttribute("aria-pressed", "true");
        expect(screen.getByText("BTC-USD-PERP.HYPERLIQUID")).toBeInTheDocument();
        expect(screen.queryByText("BTCUSDT-LINEAR.BYBIT")).not.toBeInTheDocument();
      });

      it("keeps a deselected venue's chip while it has no rows, so the hide can be undone", () => {
        localStorage.setItem("rankings-deselected-venues", JSON.stringify(["KRAKEN"]));
        renderPage();

        expect(chip("KRAKEN")).toHaveAttribute("aria-pressed", "false");
        fireEvent.click(chip("KRAKEN"));

        expect(JSON.parse(localStorage.getItem("rankings-deselected-venues") ?? "null")).toEqual([]);
      });

      it("composes with a `venue = DYDX` condition and the tab", () => {
        renderPage();

        addFilter("Exchange (venue)", "=", "dydx");
        expect(shownRows().map(([, iid]) => iid)).toEqual(["BTC-USD-PERP.DYDX", "ETH-USD-PERP.DYDX"]);

        fireEvent.click(chip("DYDX")); // chips AND conditions: nothing left
        expect(screen.queryAllByRole("cell")).toHaveLength(0);
        // Emptied by the condition, not by the chips alone -- no "every venue" note.
        expect(screen.queryByText(/every venue is deselected/)).not.toBeInTheDocument();

        fireEvent.click(chip("DYDX"));
        fireEvent.click(screen.getByText("Technicals"));
        expect(screen.getByText("BTC-USD-PERP.DYDX")).toBeInTheDocument();
        expect(screen.queryByText("BTCUSDT-LINEAR.BYBIT")).not.toBeInTheDocument();
      });

      it("notes it when the chips alone hide every row", () => {
        renderPage();

        ["BYBIT", "DYDX", "HYPERLIQUID"].forEach((venue) => fireEvent.click(chip(venue)));

        expect(screen.queryAllByRole("cell")).toHaveLength(0);
        expect(screen.getByText(/every venue is deselected/)).toHaveClass("rankings-empty");
      });

      it("persists the deselected venues to localStorage and restores them on reload", () => {
        renderPage();
        fireEvent.click(chip("DYDX"));
        fireEvent.click(chip("BYBIT"));
        expect(JSON.parse(localStorage.getItem("rankings-deselected-venues") ?? "null")).toEqual(["BYBIT", "DYDX"]);

        cleanup();
        renderPage();

        expect(chip("BYBIT")).toHaveAttribute("aria-pressed", "false");
        expect(chip("DYDX")).toHaveAttribute("aria-pressed", "false");
        expect(shownRows().map(([, iid]) => iid)).toEqual(["BTC-USD-PERP.HYPERLIQUID"]);
      });

      it("ignores a malformed stored value and shows every venue", () => {
        localStorage.setItem("rankings-deselected-venues", "{not json");

        renderPage();

        expect(shownRows()).toHaveLength(multiVenueRanks.length);
      });

      it("renders every venue and still toggles in memory when storage throws", () => {
        const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
          throw new Error("storage blocked");
        });
        const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
          throw new Error("storage blocked");
        });
        try {
          renderPage();
          expect(shownRows()).toHaveLength(multiVenueRanks.length);

          fireEvent.click(chip("BYBIT"));

          expect(screen.queryByText("BTCUSDT-LINEAR.BYBIT")).not.toBeInTheDocument();
          expect(shownRows()).toHaveLength(multiVenueRanks.length - 1);
        } finally {
          getItem.mockRestore();
          setItem.mockRestore();
        }
      });
    });
  });

  describe("Symbol and Exchange columns (Story 29.1)", () => {
    // The AC's message: rank order is Bybit BTC, dYdX ETH, Hyperliquid BTC.
    const acRanks = [
      { instrument_id: "BTCUSDT-LINEAR.BYBIT", venue: "BYBIT", symbol: "BTC", market: "perp", price: 1 },
      { instrument_id: "ETH-USD-PERP.DYDX", venue: "DYDX", symbol: "ETH", market: "perp", price: 2 },
      { instrument_id: "BTC-USD-PERP.HYPERLIQUID", venue: "HYPERLIQUID", symbol: "BTC", market: "perp", price: 3 },
    ];

    function showLive(ranks: RankingsLiveMessage["ranks"]): void {
      useLiveChannelMock.mockReturnValue({ latest: liveMessage({ ranks }), connected: true });
    }

    // [rank, symbol, exchange venue, instrument_id] per rendered body row.
    function shownRows(): [string, string, string, string][] {
      return screen
        .getAllByRole("row")
        .slice(1)
        .map((tr) => {
          const cells = within(tr).getAllByRole("cell");
          const exchange = cells[2].firstChild?.textContent ?? "";
          return [cells[0].textContent ?? "", cells[1].textContent ?? "", exchange, (cells[3].textContent ?? "").replace("⏲", "")];
        });
    }

    function header(name: "Symbol" | "Exchange"): HTMLElement {
      return screen.getByRole("columnheader", { name });
    }

    function clickHeader(name: "Symbol" | "Exchange"): void {
      fireEvent.click(within(header(name)).getByRole("button"));
    }

    function addFilter(fieldLabel: string, value: string) {
      fireEvent.click(screen.getByRole("button", { name: "Add filter" }));
      fireEvent.change(screen.getByLabelText("Filter field"), {
        target: { value: Array.from(screen.getByLabelText<HTMLSelectElement>("Filter field").options).find((o) => o.text === fieldLabel)!.value },
      });
      fireEvent.change(screen.getByLabelText("Filter operator"), { target: { value: "=" } });
      fireEvent.change(screen.getByLabelText("Filter value"), { target: { value } });
      fireEvent.click(screen.getByRole("button", { name: "Add" }));
    }

    beforeEach(() => showLive(acRanks));

    it("renders Symbol and Exchange between Rank and Instrument, in message order by default", () => {
      renderPage();

      const headers = screen.getAllByRole("columnheader").map((th) => th.textContent);
      expect(headers.slice(0, 5)).toEqual(["Rank", "Symbol", "Exchange", "Instrument", "Kind"]);
      expect(header("Symbol")).toHaveAttribute("aria-sort", "none");
      expect(shownRows()).toEqual([
        ["1", "BTC", "BYBIT", "BTCUSDT-LINEAR.BYBIT"],
        ["2", "ETH", "DYDX", "ETH-USD-PERP.DYDX"],
        ["3", "BTC", "HYPERLIQUID", "BTC-USD-PERP.HYPERLIQUID"],
      ]);
    });

    it("renders the market as a tag on the Exchange cell, and the full id as the instrument's title", () => {
      renderPage();

      const exchangeCell = within(screen.getAllByRole("row")[1]).getAllByRole("cell")[2];
      expect(exchangeCell.textContent).toBe("BYBIT · perp");
      expect(within(exchangeCell).getByText("· perp")).toHaveClass("rankings-market-tag");
      const instrument = screen.getByText("BTCUSDT-LINEAR.BYBIT");
      expect(instrument).toHaveClass("rankings-instrument");
      expect(instrument).toHaveAttribute("title", "BTCUSDT-LINEAR.BYBIT");
    });

    it("sorts same-symbol rows on different exchanges adjacent, ties by rank, keeping each message rank", () => {
      renderPage();

      clickHeader("Symbol");

      expect(header("Symbol")).toHaveAttribute("aria-sort", "ascending");
      expect(shownRows()).toEqual([
        ["1", "BTC", "BYBIT", "BTCUSDT-LINEAR.BYBIT"],
        ["3", "BTC", "HYPERLIQUID", "BTC-USD-PERP.HYPERLIQUID"],
        ["2", "ETH", "DYDX", "ETH-USD-PERP.DYDX"],
      ]);
    });

    it("cycles a header ascending, descending, then back to rank order", () => {
      renderPage();

      clickHeader("Symbol");
      clickHeader("Symbol");
      expect(header("Symbol")).toHaveAttribute("aria-sort", "descending");
      // Descending by symbol, but ties still ascend by rank.
      expect(shownRows().map(([rank]) => rank)).toEqual(["2", "1", "3"]);

      clickHeader("Symbol");
      expect(header("Symbol")).toHaveAttribute("aria-sort", "none");
      expect(shownRows().map(([rank]) => rank)).toEqual(["1", "2", "3"]);
    });

    it("sorts by Exchange, and switching headers starts the new one ascending", () => {
      showLive([...acRanks, { instrument_id: "BTCUSDT-SPOT.BYBIT", venue: "BYBIT", symbol: "BTC", market: "spot", price: 4 }]);
      renderPage();

      clickHeader("Symbol");
      clickHeader("Exchange");

      expect(header("Symbol")).toHaveAttribute("aria-sort", "none");
      expect(header("Exchange")).toHaveAttribute("aria-sort", "ascending");
      expect(shownRows().map(([rank, , venue]) => [rank, venue])).toEqual([
        ["1", "BYBIT"],
        ["4", "BYBIT"],
        ["2", "DYDX"],
        ["3", "HYPERLIQUID"],
      ]);
    });

    it("renders a missing symbol as — and sorts it last in both directions", () => {
      const olderProducer = { instrument_id: "SOL-USD-PERP.DYDX", venue: "DYDX", price: 5 };
      showLive([olderProducer, ...acRanks]);
      renderPage();

      expect(shownRows()[0]).toEqual(["1", "—", "DYDX", "SOL-USD-PERP.DYDX"]);

      clickHeader("Symbol");
      expect(shownRows().map(([rank]) => rank)).toEqual(["2", "4", "3", "1"]);

      clickHeader("Symbol");
      expect(shownRows().map(([rank]) => rank)).toEqual(["3", "2", "4", "1"]);
    });

    it("groups one exchange's markets together under an Exchange sort, ties by rank", () => {
      showLive([
        { instrument_id: "ETHUSDT-SPOT.BYBIT", venue: "BYBIT", symbol: "ETH", market: "spot", price: 1 },
        { instrument_id: "BTCUSDT-LINEAR.BYBIT", venue: "BYBIT", symbol: "BTC", market: "perp", price: 2 },
        { instrument_id: "BTCUSDT-SPOT.BYBIT", venue: "BYBIT", symbol: "BTC", market: "spot", price: 3 },
        { instrument_id: "ETHUSDT-LINEAR.BYBIT", venue: "BYBIT", symbol: "ETH", market: "perp", price: 4 },
      ]);
      renderPage();

      clickHeader("Exchange");

      expect(shownRows().map(([rank]) => rank)).toEqual(["2", "4", "1", "3"]);
    });

    it("sorts symbols case-insensitively, like the `=` filter matches them", () => {
      showLive([
        { instrument_id: "SOL-USD-PERP.HYPERLIQUID", venue: "HYPERLIQUID", symbol: "SOL", price: 1 },
        { instrument_id: "km:US500-USD-PERP.HYPERLIQUID", venue: "HYPERLIQUID", symbol: "km:US500", price: 2 },
        { instrument_id: "BTC-USD-PERP.HYPERLIQUID", venue: "HYPERLIQUID", symbol: "BTC", price: 3 },
      ]);
      renderPage();

      clickHeader("Symbol");

      expect(shownRows().map(([, symbol]) => symbol)).toEqual(["BTC", "km:US500", "SOL"]);
    });

    it("treats an empty symbol as missing and shows no market tag without a venue", () => {
      showLive([
        { instrument_id: "AAA-USD-PERP.DYDX", symbol: "", market: "spot", price: 1 },
        { instrument_id: "BTC-USD-PERP.DYDX", venue: "DYDX", symbol: "BTC", market: "perp", price: 2 },
      ]);
      renderPage();

      expect(shownRows()[0]).toEqual(["1", "—", "—", "AAA-USD-PERP.DYDX"]);
      expect(within(screen.getAllByRole("row")[1]).getAllByRole("cell")[2].textContent).toBe("—");

      clickHeader("Symbol");
      expect(shownRows().map(([rank]) => rank)).toEqual(["2", "1"]);
    });

    it("hides the other venue's rows with an Exchange (venue) filter", () => {
      renderPage();

      addFilter("Exchange (venue)", "hyperliquid");

      expect(shownRows()).toEqual([["3", "BTC", "HYPERLIQUID", "BTC-USD-PERP.HYPERLIQUID"]]);
    });

    it("shows both BTC rows and hides the others with `symbol = BTC`, sorted or not", () => {
      renderPage();

      addFilter("Symbol", "btc");
      expect(shownRows().map(([, , , iid]) => iid)).toEqual(["BTCUSDT-LINEAR.BYBIT", "BTC-USD-PERP.HYPERLIQUID"]);

      clickHeader("Exchange");
      clickHeader("Exchange");
      expect(shownRows().map(([rank, , venue]) => [rank, venue])).toEqual([
        ["3", "HYPERLIQUID"],
        ["1", "BYBIT"],
      ]);
    });
  });

  describe("ranking mode control", () => {
    function modeButton(label: "Volume" | "Volatility"): HTMLElement {
      return within(screen.getByRole("group", { name: "Ranking mode" })).getByRole("button", { name: label });
    }

    beforeEach(() => {
      vi.mocked(setRankingMode).mockReset();
    });

    it("presses the mode rankings:live carries", () => {
      useLiveChannelMock.mockReturnValue({ latest: liveMessage({ mode: "volatility" }), connected: true });

      renderPage();

      expect(modeButton("Volatility")).toHaveAttribute("aria-pressed", "true");
      expect(modeButton("Volume")).toHaveAttribute("aria-pressed", "false");
    });

    it("presses the REST seed's mode until a live message arrives", async () => {
      useLiveChannelMock.mockReturnValue({ latest: null, connected: false });

      renderPage();

      await waitFor(() => expect(modeButton("Volume")).toHaveAttribute("aria-pressed", "true"));
      expect(modeButton("Volatility")).toHaveAttribute("aria-pressed", "false");
    });

    it("on a cold open shows both buttons, neither pressed, both enabled", () => {
      vi.mocked(fetchRankings).mockReturnValueOnce(new Promise(() => {})); // no seed yet
      useLiveChannelMock.mockReturnValue({ latest: null, connected: false });

      renderPage();

      expect(screen.getByText(/Loading rankings/i)).toBeInTheDocument();
      for (const label of ["Volume", "Volatility"] as const) {
        expect(modeButton(label)).toHaveAttribute("aria-pressed", "false");
        expect(modeButton(label)).toBeEnabled();
      }
    });

    it("clicking the other mode sends it and waits for rankings:live instead of flipping", async () => {
      let resolveSend: (value: { mode: string }) => void = () => {};
      vi.mocked(setRankingMode).mockReturnValueOnce(
        new Promise((resolve) => {
          resolveSend = resolve;
        }),
      );
      useLiveChannelMock.mockReturnValue({ latest: liveMessage({ mode: "volume" }), connected: true });

      renderPage();
      fireEvent.click(modeButton("Volatility"));

      expect(setRankingMode).toHaveBeenCalledExactlyOnceWith("volatility");
      expect(modeButton("Volume")).toBeDisabled();
      expect(modeButton("Volatility")).toBeDisabled();
      resolveSend({ mode: "volatility" });
      await waitFor(() => expect(modeButton("Volatility")).toBeEnabled());
      // Accepted is not applied: the pressed button still follows rankings:live.
      expect(modeButton("Volume")).toHaveAttribute("aria-pressed", "true");
      expect(modeButton("Volatility")).toHaveAttribute("aria-pressed", "false");
    });

    it("clicking the already-active mode sends nothing", () => {
      useLiveChannelMock.mockReturnValue({ latest: liveMessage({ mode: "volume" }), connected: true });

      renderPage();
      fireEvent.click(modeButton("Volume"));

      expect(setRankingMode).not.toHaveBeenCalled();
      expect(modeButton("Volume")).toBeEnabled();
    });

    it("shows a failed switch inline and reports it to console.error", async () => {
      const failure = new Error("PUT /api/rankings/mode failed: 503 no ranking_engine subscribed to ranking:control");
      vi.mocked(setRankingMode).mockRejectedValueOnce(failure);
      const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
      useLiveChannelMock.mockReturnValue({ latest: liveMessage({ mode: "volume" }), connected: true });
      try {
        renderPage();
        fireEvent.click(modeButton("Volatility"));

        const alert = await within(screen.getByRole("group", { name: "Ranking mode" })).findByRole("alert");
        expect(alert).toHaveTextContent("no ranking_engine subscribed to ranking:control");
        expect(consoleError).toHaveBeenCalledWith(
          "RankingsPage: failed to switch ranking mode to volatility",
          failure,
        );
        expect(modeButton("Volatility")).toBeEnabled(); // a retry stays possible
      } finally {
        consoleError.mockRestore();
      }
    });

    it("keeps an in-flight switch when the first payload arrives mid-request", () => {
      vi.mocked(fetchRankings).mockReturnValueOnce(new Promise(() => {})); // no seed yet
      vi.mocked(setRankingMode).mockReturnValueOnce(new Promise(() => {})); // never settles
      useLiveChannelMock.mockReturnValue({ latest: null, connected: false });
      const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

      const { rerender } = render(pageElement(queryClient));
      fireEvent.click(modeButton("Volatility"));
      useLiveChannelMock.mockReturnValue({ latest: liveMessage({ mode: "volume" }), connected: true });
      rerender(pageElement(queryClient));

      expect(screen.queryByText(/Loading rankings/i)).not.toBeInTheDocument();
      expect(modeButton("Volume")).toBeDisabled(); // same component instance, still sending
      expect(setRankingMode).toHaveBeenCalledOnce();
    });

    it("clears a failed switch's alert once rankings:live carries a new mode", async () => {
      vi.mocked(setRankingMode).mockRejectedValueOnce(new Error("503"));
      const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
      useLiveChannelMock.mockReturnValue({ latest: liveMessage({ mode: "volume" }), connected: true });
      const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      try {
        const { rerender } = render(pageElement(queryClient));
        fireEvent.click(modeButton("Volatility"));
        const group = screen.getByRole("group", { name: "Ranking mode" });
        await within(group).findByRole("alert");

        useLiveChannelMock.mockReturnValue({ latest: liveMessage({ mode: "volatility" }), connected: true });
        rerender(pageElement(queryClient));

        expect(within(screen.getByRole("group", { name: "Ranking mode" })).queryByRole("alert")).toBeNull();
      } finally {
        consoleError.mockRestore();
      }
    });

    it("keeps a cold-open failure when the first payload only reveals the unchanged mode", async () => {
      vi.mocked(fetchRankings).mockReturnValueOnce(new Promise(() => {})); // no seed yet
      vi.mocked(setRankingMode).mockRejectedValueOnce(new Error("503 no subscriber"));
      const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
      useLiveChannelMock.mockReturnValue({ latest: null, connected: false });
      const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      try {
        const { rerender } = render(pageElement(queryClient));
        fireEvent.click(modeButton("Volatility"));
        await within(screen.getByRole("group", { name: "Ranking mode" })).findByRole("alert");

        useLiveChannelMock.mockReturnValue({ latest: liveMessage({ mode: "volume" }), connected: true });
        rerender(pageElement(queryClient));

        const alert = within(screen.getByRole("group", { name: "Ranking mode" })).getByRole("alert");
        expect(alert).toHaveTextContent("503 no subscriber");
      } finally {
        consoleError.mockRestore();
      }
    });

    it("never brings a superseded failure back when the mode returns to where it was", async () => {
      vi.mocked(setRankingMode).mockRejectedValueOnce(new Error("503"));
      const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
      useLiveChannelMock.mockReturnValue({ latest: liveMessage({ mode: "volume" }), connected: true });
      const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      try {
        const { rerender } = render(pageElement(queryClient));
        fireEvent.click(modeButton("Volatility"));
        await within(screen.getByRole("group", { name: "Ranking mode" })).findByRole("alert");

        for (const mode of ["volatility", "volume"]) {
          useLiveChannelMock.mockReturnValue({ latest: liveMessage({ mode }), connected: true });
          rerender(pageElement(queryClient));
        }

        expect(within(screen.getByRole("group", { name: "Ranking mode" })).queryByRole("alert")).toBeNull();
      } finally {
        consoleError.mockRestore();
      }
    });
  });

  describe("Story 33.7: derivatives, flow and range columns, every-column sort, saved presets", () => {
    const NOW_MS = 1_800_000_000_000;
    const linear = {
      instrument_id: "BTCUSDT-LINEAR.BYBIT",
      venue: "BYBIT",
      symbol: "BTC",
      market: "perp",
      pct_1h: 2,
      open_interest: 1000,
      oi_change_1h_pct: 5,
      oi_change_24h_pct: -1,
      funding_rate: 0.0002,
      funding_annualised: 0.1095,
      next_funding_ns: (NOW_MS + 3_600_000) * 1_000_000,
      basis_mi_bps: 1.5,
      liq_long_1h: 1.5,
      liq_short_1h: 0.5,
      liq_notional_1h: 120_000,
      liq_ratio_1h: 0.75,
      forced_share_1h: 0.0123,
      relative_volume: 1.5,
      high_24h: 110,
      low_24h: 100,
      range_position_24h: 0.25,
    };
    // A spot row carrying stray derivative values: each must still read as the dash.
    const spot = {
      instrument_id: "BTCUSDT-SPOT.BYBIT",
      venue: "BYBIT",
      symbol: "BTC",
      market: "spot",
      pct_1h: null,
      open_interest: 5,
      oi_change_1h_pct: 1,
      oi_change_24h_pct: 1,
      funding_rate: 0.0001,
      funding_annualised: 0.05,
      basis_mi_bps: 2,
      liq_notional_1h: 10,
      liq_ratio_1h: 0.5,
      forced_share_1h: 0.5,
      relative_volume: 0.8,
      high_24h: 111,
      low_24h: 99,
      range_position_24h: 0.5,
    };
    // Hyperliquid: a perp with no liquidation feed (every liq_* null).
    const hyperliquid = {
      instrument_id: "SOL-USD-PERP.HYPERLIQUID",
      venue: "HYPERLIQUID",
      symbol: "SOL",
      market: "perp",
      pct_1h: -1,
      open_interest: 2000,
      oi_change_1h_pct: null,
      oi_change_24h_pct: null,
      funding_rate: -0.00005,
      funding_annualised: -0.05475,
      next_funding_ns: null,
      basis_mi_bps: -0.5,
      liq_long_1h: null,
      liq_short_1h: null,
      liq_notional_1h: null,
      liq_ratio_1h: null,
      forced_share_1h: null,
      relative_volume: 2,
      high_24h: 20,
      low_24h: 20,
      range_position_24h: null,
    };
    const DERIVATIVE_LABELS = ["OI", "OI Δ1h %", "OI Δ24h %", "Funding", "Basis (bps)", "Liq 1h", "Liq L/S", "Forced %"];

    let dateNow: { mockRestore: () => void };

    beforeEach(() => {
      dateNow = vi.spyOn(Date, "now").mockReturnValue(NOW_MS);
      vi.mocked(fetchFilterPresets).mockReset().mockResolvedValue([]);
      vi.mocked(saveFilterPresets)
        .mockReset()
        .mockImplementation((presets) => Promise.resolve(presets));
      showLive([linear, spot, hyperliquid]);
    });

    afterEach(() => dateNow.mockRestore());

    function showLive(ranks: RankingsLiveMessage["ranks"], overrides: Partial<RankingsLiveMessage> = {}): void {
      useLiveChannelMock.mockReturnValue({ latest: liveMessage({ ranks, ...overrides }), connected: true });
    }

    function headerLabels(): string[] {
      return screen.getAllByRole("columnheader").map((th) => (th.textContent ?? "").replace(/ [▲▼]$/, ""));
    }

    function header(label: string): HTMLElement {
      return screen.getAllByRole("columnheader")[headerLabels().indexOf(label)];
    }

    function clickHeader(label: string): void {
      fireEvent.click(within(header(label)).getByRole("button"));
    }

    function cell(instrumentId: string, label: string): HTMLElement {
      const row = screen.getByText(instrumentId).closest("tr")!;
      return within(row).getAllByRole("cell")[headerLabels().indexOf(label)];
    }

    function shownRanks(): string[] {
      return screen
        .getAllByRole("row")
        .slice(1)
        .map((tr) => within(tr).getAllByRole("cell")[0].textContent ?? "");
    }

    function addFilter(fieldLabel: string, op: string, value: string): void {
      fireEvent.click(screen.getByRole("button", { name: "Add filter" }));
      const select = screen.getByLabelText<HTMLSelectElement>("Filter field");
      fireEvent.change(select, { target: { value: Array.from(select.options).find((o) => o.text === fieldLabel)!.value } });
      fireEvent.change(screen.getByLabelText("Filter operator"), { target: { value: op } });
      fireEvent.change(screen.getByLabelText("Filter value"), { target: { value } });
      fireEvent.click(screen.getByRole("button", { name: "Add" }));
    }

    // Instrument rows only (the Technicals tab's empty-state row has one cell).
    function shownInstruments(): string[] {
      return screen
        .getAllByRole("row")
        .slice(1)
        .map((tr) => within(tr).getAllByRole("cell"))
        .filter((cells) => cells.length > 3)
        .map((cells) => (cells[3].textContent ?? "").replace(/[⏲⏱⚠ ]/g, ""));
    }

    describe("columns", () => {
      it("renders the ten new columns after Vol24h, each at its display precision", () => {
        renderPage();

        expect(headerLabels().slice(-11)).toEqual(["Vol24h", ...DERIVATIVE_LABELS, "Rel vol", "24h range"]);
        const iid = linear.instrument_id;
        expect(
          [...DERIVATIVE_LABELS, "Rel vol", "24h range"].map((label) => cell(iid, label).textContent),
        ).toEqual(["1000.00", "+5.00%", "-1.00%", "0.0200%", "+1.50", "120.0K", "75.0%", "1.2%", "1.50×", "25%"]);
      });

      it("dashes every derivatives column of a spot row whatever its value, but shows rel vol and range", () => {
        renderPage();

        for (const label of DERIVATIVE_LABELS) expect(cell(spot.instrument_id, label).textContent).toBe("—");
        expect(cell(spot.instrument_id, "Rel vol").textContent).toBe("0.80×");
        expect(cell(spot.instrument_id, "24h range").textContent).toBe("50%");
      });

      it("dashes a Hyperliquid row's liquidation columns (no feed) and keeps the staleness markers", () => {
        showLive([linear, spot, hyperliquid], { stale_instrument_ids: [hyperliquid.instrument_id] });
        renderPage();

        for (const label of ["Liq 1h", "Liq L/S", "Forced %"]) {
          expect(cell(hyperliquid.instrument_id, label).textContent).toBe("—");
        }
        expect(cell(hyperliquid.instrument_id, "Funding").textContent).toBe("-0.0050%");
        const staleRow = screen.getByText(hyperliquid.instrument_id).closest("tr")!;
        expect(within(staleRow).getByTitle("market data stale")).toBeInTheDocument();
        const freshRow = screen.getByText(linear.instrument_id).closest("tr")!;
        expect(within(freshRow).queryByTitle("market data stale")).toBeNull();
        expect(within(freshRow).queryByTitle("rankings feed stale")).toBeNull();
      });

      it("explains the funding cell with its annualised rate and the countdown to the next payment", () => {
        renderPage();

        expect(cell(linear.instrument_id, "Funding")).toHaveAttribute(
          "title",
          "annualised 10.95% · next payment in 01:00:00",
        );
        expect(cell(hyperliquid.instrument_id, "Funding")).toHaveAttribute(
          "title",
          "annualised -5.48% · next payment unknown",
        );
        expect(cell(spot.instrument_id, "Funding")).not.toHaveAttribute("title");
      });

      it("reads a funding time at or before now as a payment due, never a frozen countdown", () => {
        showLive([{ ...linear, next_funding_ns: NOW_MS * 1_000_000 }]);
        renderPage();

        expect(cell(linear.instrument_id, "Funding")).toHaveAttribute(
          "title",
          "annualised 10.95% · next payment due",
        );
      });

      it("clamps the range marker to the bar while the text keeps the published value", () => {
        showLive([{ ...linear, range_position_24h: 1.2 }]);
        renderPage();

        const range = cell(linear.instrument_id, "24h range");
        expect(range.textContent).toBe("120%");
        expect((within(range).getByTestId("range-bar").firstElementChild as HTMLElement).style.left).toBe("100%");
      });

      it("explains the liquidation cells and the range, and marks the close's place on the range bar", () => {
        renderPage();

        expect(cell(linear.instrument_id, "Liq 1h")).toHaveAttribute(
          "title",
          "liquidated last 1 h (base size): long 1.5 · short 0.5",
        );
        expect(cell(linear.instrument_id, "Liq L/S")).toHaveAttribute("title", "long share of liquidated size");
        const range = cell(linear.instrument_id, "24h range");
        expect(range).toHaveAttribute("title", "low 100 · high 110");
        const marker = within(range).getByTestId("range-bar").firstElementChild as HTMLElement;
        expect(marker.style.left).toBe("25%");
        // A flat range has no position: the dash, no bar.
        expect(cell(hyperliquid.instrument_id, "24h range").textContent).toBe("—");
        expect(within(cell(hyperliquid.instrument_id, "24h range")).queryByTestId("range-bar")).toBeNull();
      });
    });

    describe("sort", () => {
      const pctRanks = [
        { instrument_id: "AAA-USD-PERP.DYDX", pct_1h: 2, pct_24h: 1 },
        { instrument_id: "BBB-USD-PERP.DYDX", pct_1h: null, pct_24h: 1 },
        { instrument_id: "CCC-USD-PERP.DYDX", pct_1h: -1, pct_24h: Number.NaN },
        { instrument_id: "DDD-USD-PERP.DYDX", pct_1h: 5, pct_24h: 0 },
      ];

      it("cycles a metric header ascending, descending, then rank order, missing values last both ways", () => {
        showLive(pctRanks);
        renderPage();

        clickHeader("1h %");
        expect(header("1h %")).toHaveAttribute("aria-sort", "ascending");
        expect(shownRanks()).toEqual(["3", "1", "4", "2"]); // -1, 2, 5, null

        clickHeader("1h %");
        expect(header("1h %")).toHaveAttribute("aria-sort", "descending");
        expect(shownRanks()).toEqual(["4", "1", "3", "2"]); // 5, 2, -1, null

        clickHeader("1h %");
        expect(header("1h %")).toHaveAttribute("aria-sort", "none");
        expect(shownRanks()).toEqual(["1", "2", "3", "4"]);
      });

      it("breaks ties by rank in both directions and sorts NaN as missing", () => {
        showLive(pctRanks);
        renderPage();

        clickHeader("24h %");
        expect(shownRanks()).toEqual(["4", "1", "2", "3"]); // 0, 1, 1, NaN
        clickHeader("24h %");
        expect(shownRanks()).toEqual(["1", "2", "4", "3"]); // 1, 1, 0, NaN
      });

      it("sorts a spot row's stray derivative value last, as missing", () => {
        renderPage();

        clickHeader("Funding");
        expect(shownInstruments()).toEqual([hyperliquid.instrument_id, linear.instrument_id, spot.instrument_id]);
        clickHeader("Funding");
        expect(shownInstruments()).toEqual([linear.instrument_id, hyperliquid.instrument_id, spot.instrument_id]);
      });

      it("keeps the sort across a reload, and clears it once back at rank order", () => {
        renderPage();
        clickHeader("Funding");
        clickHeader("Funding");
        expect(JSON.parse(localStorage.getItem("rankings-sort") ?? "null")).toEqual({
          key: "funding_rate",
          direction: "descending",
        });

        cleanup();
        renderPage();
        expect(header("Funding")).toHaveAttribute("aria-sort", "descending");
        expect(shownInstruments()[0]).toBe(linear.instrument_id);

        clickHeader("Funding");
        expect(localStorage.getItem("rankings-sort")).toBeNull();
      });

      it.each([
        ["a removed column's key", JSON.stringify({ key: "gone_column", direction: "ascending" })],
        ["a bad direction", JSON.stringify({ key: "funding_rate", direction: "up" })],
        ["a bad shape", JSON.stringify(["funding_rate"])],
        ["bad JSON", "{not json"],
      ])("reads %s in storage as rank order", (_case, stored) => {
        localStorage.setItem("rankings-sort", stored);
        renderPage();

        expect(shownRanks()).toEqual(["1", "2", "3"]);
        expect(header("Funding")).toHaveAttribute("aria-sort", "none");
      });

      it("renders in rank order and still sorts in memory when storage throws", () => {
        const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
          throw new Error("storage blocked");
        });
        const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
          throw new Error("storage blocked");
        });
        try {
          renderPage();
          expect(shownRanks()).toEqual(["1", "2", "3"]);

          clickHeader("Rel vol");

          expect(shownInstruments()).toEqual([spot.instrument_id, linear.instrument_id, hyperliquid.instrument_id]);
        } finally {
          getItem.mockRestore();
          setItem.mockRestore();
        }
      });

      it("applies a metric sort on Performance only, keeping it stored, while Symbol sorts both tabs", () => {
        renderPage();
        clickHeader("Rel vol");

        fireEvent.click(screen.getByText("Technicals"));

        expect(within(screen.getByRole("table")).queryByRole("button", { name: "Rel vol" })).toBeNull();
        // No header on this tab shows the Rel vol sort, so the rows keep their rank order here.
        expect(shownInstruments()).toEqual([linear.instrument_id, spot.instrument_id, hyperliquid.instrument_id]);
        expect(JSON.parse(localStorage.getItem("rankings-sort") ?? "null")).toEqual({
          key: "relative_volume",
          direction: "ascending",
        });

        fireEvent.click(screen.getByText("Performance"));
        expect(shownInstruments()).toEqual([spot.instrument_id, linear.instrument_id, hyperliquid.instrument_id]);

        fireEvent.click(screen.getByText("Technicals"));
        clickHeader("Symbol");
        clickHeader("Symbol");
        // Descending: SOL before the two BTC rows (ties by rank).
        expect(shownInstruments()).toEqual([hyperliquid.instrument_id, linear.instrument_id, spot.instrument_id]);
      });
    });

    describe("filters", () => {
      it("types a fraction raw, matches `=` at the shown percent, and never matches a spot derivative", () => {
        renderPage();

        addFilter("Funding (fraction/interval)", ">", "0");
        expect(shownInstruments()).toEqual([linear.instrument_id]); // spot's stray 0.0001 is missing

        fireEvent.click(screen.getByRole("button", { name: /Remove filter/ }));
        addFilter("Funding (fraction/interval)", "=", "0.0002");
        expect(shownInstruments()).toEqual([linear.instrument_id]);
      });

      it("labels each scaled field with the unit it is typed in", () => {
        renderPage();
        fireEvent.click(screen.getByRole("button", { name: "Add filter" }));

        const labels = Array.from(screen.getByLabelText<HTMLSelectElement>("Filter field").options).map((o) => o.text);
        expect(labels).toEqual(
          expect.arrayContaining([
            "OI (venue units)",
            "OI Δ1h %",
            "Funding (fraction/interval)",
            "Basis (bps)",
            "Liq 1h (raw quote)",
            "Liq L/S (fraction)",
            "Forced % (fraction)",
            "Rel vol",
            "24h range (fraction)",
          ]),
        );
      });
    });

    describe("presets", () => {
      function presetBar(): HTMLElement {
        return screen.getByLabelText("Filter preset").parentElement!;
      }

      async function savePreset(name: string): Promise<void> {
        fireEvent.change(screen.getByLabelText("Preset name"), { target: { value: name } });
        const save = within(presetBar()).getByRole("button", { name: /^(Save|Overwrite)$/ });
        await waitFor(() => expect(save).toBeEnabled()); // once the stored list has loaded
        fireEvent.click(save);
      }

      function pickPreset(name: string): void {
        fireEvent.change(screen.getByLabelText("Filter preset"), { target: { value: name } });
      }

      function presetOptions(): string[] {
        return Array.from(screen.getByLabelText<HTMLSelectElement>("Filter preset").options).map((o) => o.text);
      }

      function activeChip(): string | null {
        return within(presetBar()).queryByTitle("active preset")?.textContent ?? null;
      }

      it("saves, recalls, clears the chip on an edit, and deletes a preset", async () => {
        renderPage();
        expect(within(presetBar()).getByRole("button", { name: "Save" })).toBeDisabled(); // no conditions

        addFilter("Funding (fraction/interval)", ">", "0.00003");
        await savePreset("lev");

        await waitFor(() => expect(activeChip()).toBe("lev"));
        expect(saveFilterPresets).toHaveBeenLastCalledWith([
          { name: "lev", conditions: [{ field: "funding_rate", op: ">", value: 0.00003 }] },
        ]);
        expect(presetOptions()).toContain("lev");
        expect(within(presetBar()).getByRole("button", { name: "Overwrite" })).toBeInTheDocument();

        fireEvent.click(screen.getByRole("button", { name: /Remove filter/ })); // an edit
        expect(activeChip()).toBeNull();
        expect(shownInstruments()).toHaveLength(3);

        pickPreset("lev");
        expect(activeChip()).toBe("lev");
        expect(shownInstruments()).toEqual([linear.instrument_id]);
        expect(screen.getByText("Funding (fraction/interval) > 0.00003")).toBeInTheDocument();
        // The select is back on its placeholder: picking the same preset after an edit recalls it.
        expect(screen.getByLabelText<HTMLSelectElement>("Filter preset").value).toBe("");
        fireEvent.click(screen.getByRole("button", { name: /Remove filter/ }));
        pickPreset("lev");
        expect(shownInstruments()).toEqual([linear.instrument_id]);

        fireEvent.click(screen.getByRole("button", { name: "Delete preset" }));
        await waitFor(() => expect(presetOptions()).not.toContain("lev"));
        expect(saveFilterPresets).toHaveBeenLastCalledWith([]);
        expect(activeChip()).toBeNull();
      });

      it("recalls a preset saved in another browser, naming a field it no longer knows", async () => {
        vi.mocked(fetchFilterPresets).mockResolvedValue([
          {
            name: "old",
            conditions: [
              { field: "gone_column", op: ">", value: 1 },
              { field: "pct_1h", op: ">", value: 0 },
            ],
          },
        ]);
        renderPage();
        await waitFor(() => expect(presetOptions()).toContain("old"));

        pickPreset("old");

        expect(within(presetBar()).getByRole("status")).toHaveTextContent(
          "not applied: gone_column (unknown field) — kept on save",
        );
        expect(shownInstruments()).toEqual([linear.instrument_id]); // pct_1h > 0 applied

        await savePreset("old");
        await waitFor(() => expect(saveFilterPresets).toHaveBeenCalled());
        expect(saveFilterPresets).toHaveBeenLastCalledWith([
          {
            name: "old",
            conditions: [
              { field: "pct_1h", op: ">", value: 0 },
              { field: "gone_column", op: ">", value: 1 },
            ],
          },
        ]);
      });

      it("names the actual reason a recalled condition does not fit its field", async () => {
        vi.mocked(fetchFilterPresets).mockResolvedValue([
          {
            name: "odd",
            conditions: [
              { field: "symbol", op: ">", value: "BTC" },
              { field: "pct_1h", op: "=", value: "two" },
              { field: "venue", op: "=", value: 3 },
            ],
          },
        ]);
        renderPage();
        await waitFor(() => expect(presetOptions()).toContain("odd"));

        pickPreset("odd");

        expect(within(presetBar()).getByRole("status")).toHaveTextContent(
          "not applied: symbol (operator does not fit the field), pct_1h (value does not fit the field), " +
            "venue (value does not fit the field) — kept on save",
        );
      });

      it("keeps Save and Delete disabled and shows the error when the stored list failed to load", async () => {
        vi.mocked(fetchFilterPresets).mockRejectedValue(new Error("GET failed: 500"));
        const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
        try {
          renderPage();
          addFilter("Rel vol", ">", "1");
          fireEvent.change(screen.getByLabelText("Preset name"), { target: { value: "lev" } });

          expect(await within(presetBar()).findByRole("alert")).toHaveTextContent("load failed: GET failed: 500");
          expect(within(presetBar()).getByRole("button", { name: "Save" })).toBeDisabled();
          expect(within(presetBar()).getByRole("button", { name: "Delete preset" })).toBeDisabled();
          expect(saveFilterPresets).not.toHaveBeenCalled();
        } finally {
          consoleError.mockRestore();
        }
      });

      it("never lets a GET that resolves after a later PUT overwrite the stored list", async () => {
        let resolveStaleGet: (presets: never[]) => void = () => {};
        vi.mocked(fetchFilterPresets)
          .mockResolvedValueOnce([])
          .mockImplementationOnce(() => new Promise((resolve) => (resolveStaleGet = resolve)));
        vi.mocked(saveFilterPresets).mockRejectedValueOnce(new Error("PUT failed: 503"));
        const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
        try {
          renderPage();
          addFilter("Rel vol", ">", "1");
          await savePreset("first"); // fails: the list reload (a GET) stays pending
          await within(presetBar()).findByRole("alert");
          await savePreset("second"); // succeeds
          await waitFor(() => expect(presetOptions()).toContain("second"));

          resolveStaleGet([]);
          await new Promise((resolve) => setTimeout(resolve, 0));

          expect(presetOptions()).toContain("second");
        } finally {
          consoleError.mockRestore();
        }
      });

      it("shows no active chip when the filters changed while the save was in flight", async () => {
        let resolveSave: (presets: FilterPresetItem[]) => void = () => {};
        vi.mocked(saveFilterPresets).mockImplementationOnce(
          () => new Promise((resolve) => (resolveSave = resolve)),
        );
        renderPage();
        addFilter("Rel vol", ">", "1");
        await savePreset("lev");
        fireEvent.click(screen.getByRole("button", { name: /Remove filter/ }));

        resolveSave([{ name: "lev", conditions: [{ field: "relative_volume", op: ">", value: 1 }] }]);

        await waitFor(() => expect(presetOptions()).toContain("lev"));
        expect(activeChip()).toBeNull();
      });

      it("shows a failed save inline, reports it and reloads the list", async () => {
        vi.mocked(saveFilterPresets).mockRejectedValueOnce(new Error("PUT failed: 500"));
        const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
        try {
          renderPage();
          await waitFor(() => expect(fetchFilterPresets).toHaveBeenCalledTimes(1));
          addFilter("Rel vol", ">", "1");
          await savePreset("busy");

          expect(await within(presetBar()).findByRole("alert")).toHaveTextContent("PUT failed: 500");
          expect(consoleError).toHaveBeenCalledWith("RankingsPage: failed to save filter presets", expect.any(Error));
          await waitFor(() => expect(fetchFilterPresets).toHaveBeenCalledTimes(2));
          expect(activeChip()).toBeNull();
        } finally {
          consoleError.mockRestore();
        }
      });

      it("offers a Retry after a failed load, which enables Save once the list loads", async () => {
        vi.mocked(fetchFilterPresets).mockRejectedValueOnce(new Error("GET failed: 502")).mockResolvedValue([]);
        const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
        try {
          renderPage();
          addFilter("Rel vol", ">", "1");
          fireEvent.change(screen.getByLabelText("Preset name"), { target: { value: "lev" } });
          await within(presetBar()).findByRole("alert");
          expect(within(presetBar()).getByRole("button", { name: "Save" })).toBeDisabled();

          fireEvent.click(within(presetBar()).getByRole("button", { name: "Retry" }));

          await waitFor(() => expect(within(presetBar()).getByRole("button", { name: "Save" })).toBeEnabled());
          expect(within(presetBar()).queryByRole("alert")).toBeNull();
          expect(within(presetBar()).queryByRole("button", { name: "Retry" })).toBeNull();
        } finally {
          consoleError.mockRestore();
        }
      });

      it("deletes the preset just saved under a new name, not the one recalled before", async () => {
        vi.mocked(fetchFilterPresets).mockResolvedValue([
          { name: "lev", conditions: [{ field: "relative_volume", op: ">", value: 1 }] },
        ]);
        renderPage();
        await waitFor(() => expect(presetOptions()).toContain("lev"));
        pickPreset("lev");
        await savePreset("lev2");
        await waitFor(() => expect(presetOptions()).toContain("lev2"));

        const del = within(presetBar()).getByRole("button", { name: "Delete preset" });
        expect(del).toHaveAttribute("title", "delete preset lev2");
        fireEvent.click(del);

        await waitFor(() => expect(presetOptions()).not.toContain("lev2"));
        expect(presetOptions()).toContain("lev");
      });

      it("lets a recalled preset whose every condition was not applied be saved back", async () => {
        vi.mocked(fetchFilterPresets).mockResolvedValue([
          { name: "old", conditions: [{ field: "gone_column", op: ">", value: 1 }] },
        ]);
        renderPage();
        await waitFor(() => expect(presetOptions()).toContain("old"));
        pickPreset("old");

        await savePreset("old"); // no chip is visible, yet Save stores the kept condition

        await waitFor(() =>
          expect(saveFilterPresets).toHaveBeenLastCalledWith([
            { name: "old", conditions: [{ field: "gone_column", op: ">", value: 1 }] },
          ]),
        );
      });

      describe("a Technicals condition", () => {
        const rsi = { name: "RelativeStrengthIndex", params: {}, category: "native" };

        afterEach(() => {
          vi.mocked(fetchTechnicalsColumns).mockReset().mockResolvedValue([]);
          vi.mocked(fetchTechnicalsValues).mockReset().mockResolvedValue({});
        });

        it("is reported as not loaded, not unknown, while the Technicals columns are loading", async () => {
          vi.mocked(fetchTechnicalsColumns).mockImplementation(() => new Promise(() => {}));
          vi.mocked(fetchFilterPresets).mockResolvedValue([
            { name: "rsi", conditions: [{ field: "tech:RelativeStrengthIndex.value", op: "<", value: 30 }] },
          ]);
          renderPage();
          await waitFor(() => expect(presetOptions()).toContain("rsi"));

          pickPreset("rsi");

          expect(within(presetBar()).getByRole("status")).toHaveTextContent(
            "not applied: tech:RelativeStrengthIndex.value (Technicals columns not loaded) — kept on save",
          );
        });

        it("is unknown when its output is not one the loaded values have", async () => {
          vi.mocked(fetchTechnicalsColumns).mockResolvedValue([rsi]);
          vi.mocked(fetchTechnicalsValues).mockResolvedValue({ [linear.instrument_id]: { "0.value": 25 } });
          vi.mocked(fetchFilterPresets).mockResolvedValue([
            {
              name: "rsi",
              conditions: [
                { field: "tech:RelativeStrengthIndex.value", op: "<", value: 30 },
                { field: "tech:RelativeStrengthIndex.valeu", op: "<", value: 30 },
              ],
            },
          ]);
          renderPage();
          fireEvent.click(screen.getByRole("button", { name: "Add filter" })); // loads the values
          await waitFor(() => expect(fetchTechnicalsValues).toHaveBeenCalled());
          await screen.findByRole("option", { name: "RelativeStrengthIndex.value" });

          pickPreset("rsi");

          expect(within(presetBar()).getByRole("status")).toHaveTextContent(
            "not applied: tech:RelativeStrengthIndex.valeu (unknown field) — kept on save",
          );
        });
      });
    });
  });
});
