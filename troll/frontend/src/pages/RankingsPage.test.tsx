import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <RankingsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
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
});
