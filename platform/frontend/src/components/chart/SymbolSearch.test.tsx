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
  collected: true as boolean | null,
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


describe("SymbolSearch collected markets (chart UX rework, 2026-10-08)", () => {
  const listed = () => screen.queryAllByRole("option").map((o) => o.textContent);

  it("lists only the collected markets, and every market with All markets, an uncollected one tagged", async () => {
    markets.get.mockResolvedValue({ items: [item("BTC"), { ...item("ETH"), collected: false }, { ...item("SOL"), collected: null }], stale_venues: [] });
    render(<SymbolSearch mode="navigate" instrumentId="XRPUSDT-LINEAR.BYBIT" onPick={() => {}} onClose={() => {}} />);
    await act(async () => {});

    expect(listed()).toHaveLength(1);
    expect(listed()[0]).toContain("BTCUSDT-LINEAR.BYBIT");

    fireEvent.click(screen.getByRole("checkbox", { name: /All markets \(3\)/ }));
    expect(listed()).toHaveLength(3);
    expect(listed()[1]).toContain("not collected");
    expect(listed()[2]).not.toContain("not collected");
  });

  it("lists every market, saying why, when no collector has reported what it collects", async () => {
    markets.get.mockResolvedValue({ items: [{ ...item("BTC"), collected: null }, { ...item("ETH"), collected: null }], stale_venues: [] });
    render(<SymbolSearch mode="navigate" instrumentId="XRPUSDT-LINEAR.BYBIT" onPick={() => {}} onClose={() => {}} />);
    await act(async () => {});

    expect(listed()).toHaveLength(2);
    expect(screen.getByRole("status")).toHaveTextContent("No collector has reported what it collects");
    expect(screen.queryByRole("checkbox", { name: /All markets/ })).toBeNull();
  });

  it("offers only collected markets to compare, with no All markets switch", async () => {
    markets.get.mockResolvedValue({ items: [item("BTC"), { ...item("ETH"), collected: false }], stale_venues: [] });
    render(<SymbolSearch mode="compare" instrumentId="XRPUSDT-LINEAR.BYBIT" onPick={() => {}} onClose={() => {}} />);
    await act(async () => {});

    expect(listed()).toHaveLength(1);
    expect(screen.queryByRole("checkbox", { name: /All markets/ })).toBeNull();
  });
});
