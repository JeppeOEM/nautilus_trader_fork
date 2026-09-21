import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchRankings, fetchTechnicalsColumns, fetchTechnicalsValues, saveTechnicalsColumns } from "../api/client";
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
  "Vol(catalog)",
  "Vol Score",
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
    // The pinned columns stay visible regardless of active tab (AC #1).
    expect(screen.getByText("Rank")).toBeInTheDocument();
    expect(screen.getByText("Instrument")).toBeInTheDocument();
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

    it("shows a Venue column and filters multi-venue rows with `venue = X`, one row per instrument_id", () => {
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

      expect(screen.getByRole("columnheader", { name: "Venue" })).toBeInTheDocument();
      expect(within(screen.getByRole("table")).getByText("BYBIT")).toBeInTheDocument();
      expect(screen.getAllByRole("row")).toHaveLength(4); // header + 3 distinct venue-qualified rows

      addFilter("Venue", "=", "bybit");

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
      expect(screen.getByRole("columnheader", { name: "Market" })).toBeInTheDocument();

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
            return [cells[0].textContent ?? "", (cells[1].textContent ?? "").replace("⏲", "")];
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

        addFilter("Venue", "=", "dydx");
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
});
