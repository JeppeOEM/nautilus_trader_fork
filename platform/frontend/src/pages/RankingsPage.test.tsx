import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  fetchRankings,
  fetchTechnicalsColumns,
  fetchTechnicalsValues,
  saveTechnicalsColumns,
  setRankingMode,
} from "../api/client";
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

  it("shows Performance as the default tab with all 13 metric columns visible", () => {
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
});
