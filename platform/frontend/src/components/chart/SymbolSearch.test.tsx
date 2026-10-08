import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import SymbolSearch from "./SymbolSearch";

const markets = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("../../api/client", () => ({
  HttpError: class HttpError extends Error {
    status = 500;
  },
  fetchMarkets: (...args: unknown[]) => markets.get(...args),
}));

const item = (symbol: string) => ({
  instrument_id: `${symbol}USDT-LINEAR.BYBIT`,
  symbol,
  venue: "BYBIT",
  market: "perp",
  volume24h: null,
});

afterEach(() => {
  delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
});

describe("SymbolSearch keyboard highlight (Story 33.12)", () => {
  it("points aria-activedescendant at the highlighted option and scrolls it into view", async () => {
    markets.get.mockResolvedValue({ items: ["BTC", "ETH", "SOL"].map(item), stale_venues: [] });
    const scrolled: string[] = [];
    Element.prototype.scrollIntoView = vi.fn(function (this: Element, arg?: boolean | ScrollIntoViewOptions) {
      expect(arg).toEqual({ block: "nearest" });
      scrolled.push(this.textContent ?? "");
    });
    render(<SymbolSearch mode="navigate" instrumentId="BTCUSDT-LINEAR.BYBIT" onPick={() => {}} onClose={() => {}} />);
    await act(async () => {});
    const input = screen.getByRole("searchbox", { name: "Search markets" });
    const options = screen.getAllByRole("option");

    expect(input).toHaveAttribute("aria-activedescendant", options[0].id);
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "ArrowDown" });

    expect(input).toHaveAttribute("aria-activedescendant", options[2].id);
    expect(options[2]).toHaveAttribute("aria-selected", "true");
    expect(scrolled.at(-1)).toContain("SOL");
    expect(new Set(options.map((o) => o.id)).size).toBe(3);
  });

  it("drops aria-activedescendant when nothing matches, and runs where scrollIntoView is missing (jsdom)", async () => {
    markets.get.mockResolvedValue({ items: [item("BTC")], stale_venues: [] });
    render(<SymbolSearch mode="navigate" instrumentId="BTCUSDT-LINEAR.BYBIT" onPick={() => {}} onClose={() => {}} />);
    await act(async () => {});
    const input = screen.getByRole("searchbox", { name: "Search markets" });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.change(input, { target: { value: "zzz" } });
    expect(input).not.toHaveAttribute("aria-activedescendant");
  });
});

describe("SymbolSearch market list (Story 33.12)", () => {
  it("clears an earlier load failure once a later fetch for a new instrument succeeds", async () => {
    markets.get.mockRejectedValueOnce(new Error("down")).mockResolvedValueOnce({ items: [item("ETH")], stale_venues: ["HYPERLIQUID"] });
    const props = { mode: "compare" as const, onPick: () => {}, onClose: () => {} };
    const { rerender } = render(<SymbolSearch {...props} instrumentId="BTCUSDT-LINEAR.BYBIT" />);
    await act(async () => {});
    expect(screen.getByText(/could not be loaded/)).toBeInTheDocument();

    rerender(<SymbolSearch {...props} instrumentId="SOLUSDT-LINEAR.BYBIT" />);
    await act(async () => {});
    expect(screen.queryByText(/could not be loaded/)).not.toBeInTheDocument();
    expect(screen.getByText(/Market list stale for HYPERLIQUID/)).toBeInTheDocument();
    expect(screen.getAllByRole("option")).toHaveLength(1);
  });
});
