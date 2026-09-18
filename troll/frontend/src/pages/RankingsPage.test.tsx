import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchRankings } from "../api/client";
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

// Hand-declared list of the Performance tab's 13 column labels (the exact set
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
});

// The project's shared Vitest setup (src/test/setup.ts) doesn't register RTL's
// automatic cleanup -- DocsPage.test.tsx never noticed because its two tests query
// disjoint text. This file's tests deliberately reuse the same instrument id
// ("BTC-USD-PERP.DYDX") across cases, so a leftover previous render makes getByText
// ambiguous unless each test's DOM is torn down first.
afterEach(() => {
  cleanup();
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

    expect(screen.getByText("no columns yet — click + to add one")).toBeInTheDocument();
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

    expect(screen.queryByText("no columns yet — click + to add one")).not.toBeInTheDocument();
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
    expect(screen.getByText("no columns yet — click + to add one")).toBeInTheDocument();

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
});
