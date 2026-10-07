import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import WatchlistRail from "./WatchlistRail";

const HL = "BTC.HYPERLIQUID";
const BY = "ETHUSDT-LINEAR.BYBIT";
const state = vi.hoisted(() => ({
  watchlist: { instruments: [] as string[], loaded: true, loadFailed: false, error: null as string | null },
  live: { latest: null as unknown, connected: true },
}));
vi.mock("../../hooks/useWatchlist", () => ({
  useWatchlist: () => ({ ...state.watchlist, pin: vi.fn(), unpin: vi.fn() }),
}));
vi.mock("../../hooks/useLiveChannel", () => ({ useLiveChannel: () => state.live }));

const message = (stale: string[], updatedAtNs = Date.now() * 1_000_000) => ({
  mode: "volume",
  updated_at: updatedAtNs,
  stale_instrument_ids: stale,
  ranks: [
    { instrument_id: HL, symbol: "BTC", venue: "HYPERLIQUID", price: 61234.5, pct_24h: -1.234 },
    { instrument_id: BY, symbol: "ETH", venue: "BYBIT", price: 2500, pct_24h: 2 },
  ],
});

function rows(): string[] {
  render(
    <MemoryRouter>
      <WatchlistRail instrumentId={HL} />
    </MemoryRouter>,
  );
  const rail = screen.getByRole("complementary", { name: "Watchlist" });
  return within(rail)
    .getAllByRole("link")
    .map((l) => l.textContent ?? "");
}

beforeEach(() => {
  state.watchlist = { instruments: [HL, BY], loaded: true, loadFailed: false, error: null };
  state.live = { latest: message([]), connected: true };
});

describe("WatchlistRail (Story 33.12)", () => {
  it("prices every row live while connected and fresh", () => {
    expect(rows()).toEqual(["BTCHYPERLIQUID61234.5000-1.23%", "ETHBYBIT2500.0000+2.00%"]);
  });

  it("shows — for an instrument the ranking marks stale, with the reason as its title", () => {
    state.live = { latest: message([BY]), connected: true };
    expect(rows()).toEqual(["BTCHYPERLIQUID61234.5000-1.23%", "ETHBYBIT——"]);
    expect(screen.getAllByTitle(/marks it stale/)).toHaveLength(1);
  });

  it("shows — for every row while the connected feed has sent nothing for over 15 s (the ranking engine down)", () => {
    state.live = { latest: message([], (Date.now() - 16_000) * 1_000_000), connected: true };
    expect(rows()).toEqual(["BTCHYPERLIQUID——", "ETHBYBIT——"]);
    expect(screen.getByText("Live prices paused: rankings stale.")).toBeInTheDocument();
    expect(screen.getAllByTitle(/Live rankings stale/)).toHaveLength(2);
  });

  it("shows — for every row while the rankings socket is disconnected, never the frozen last price", () => {
    state.live = { latest: message([]), connected: false };
    expect(rows()).toEqual(["BTCHYPERLIQUID——", "ETHBYBIT——"]);
    expect(screen.getByText("Live prices paused: rankings disconnected.")).toBeInTheDocument();
  });

  it("says the watchlist could not be loaded on the disabled pin toggle while the GET is failing", () => {
    state.watchlist = { instruments: [], loaded: false, loadFailed: true, error: "Watchlist could not be loaded: down (retrying in 2 s)" };
    render(
      <MemoryRouter>
        <WatchlistRail instrumentId={HL} />
      </MemoryRouter>,
    );
    const toggle = screen.getByRole("button", { name: "Pin this chart" });
    expect(toggle).toBeDisabled();
    expect(toggle).toHaveAttribute("title", "The saved watchlist could not be loaded (retrying)");
    expect(screen.getByRole("alert")).toHaveTextContent("Watchlist could not be loaded: down (retrying in 2 s)");
  });
});
