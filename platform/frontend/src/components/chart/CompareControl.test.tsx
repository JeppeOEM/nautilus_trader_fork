import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import CompareControl from "./CompareControl";

const marketsApi = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("../../api/client", () => ({
  HttpError: class HttpError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  fetchMarkets: (...args: unknown[]) => marketsApi.get(...args),
}));

const { HttpError } = await import("../../api/client");

const MAIN = "BTCUSDT-LINEAR.BYBIT";
const HL = "BTC-USD-PERP.HYPERLIQUID";
const ETH = "ETHUSDT-LINEAR.BYBIT";
const market = (iid: string, volume24h: number | null = 12_345_678) => ({
  instrument_id: iid,
  symbol: iid.split("-")[0],
  venue: iid.split(".")[1],
  same_asset: false,
  market: "perp",
  volume24h,
  collected: true,
});

/** The page's side of the control: it holds `open`, as `Alt+C` opens the same search. */
function Harness({ symbols = [], disabled = false, onAdd }: { symbols?: string[]; disabled?: boolean; onAdd: (iid: string) => void }) {
  const [open, setOpen] = useState(false);
  return <CompareControl instrumentId={MAIN} symbols={symbols} disabled={disabled} open={open} onOpenChange={setOpen} onAdd={onAdd} />;
}

const dialog = (): HTMLElement => screen.getByRole("dialog", { name: "Compare symbol" });
const field = (): HTMLInputElement => within(dialog()).getByRole("searchbox") as HTMLInputElement;
const listed = (): string[] =>
  within(dialog())
    .queryAllByRole("option")
    .map((o) => o.textContent ?? "");

beforeEach(() => {
  marketsApi.get.mockReset().mockResolvedValue({ items: [], stale_venues: [] });
});
afterEach(cleanup);

describe("CompareControl (Story 33.9, the symbol search since 33.12)", () => {
  it("opens the symbol search in compare mode, asking the server with the chart's id", async () => {
    marketsApi.get.mockResolvedValue({ items: [market(HL), market(ETH)], stale_venues: [] });
    render(<Harness onAdd={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Compare" }));

    await waitFor(() => expect(listed()).toHaveLength(2));
    expect(marketsApi.get).toHaveBeenCalledWith(MAIN);
    expect(screen.queryByRole("combobox")).toBeNull(); // the 33.9 datalist field is gone
  });

  it("does not offer a market already compared", async () => {
    marketsApi.get.mockResolvedValue({ items: [market(HL), market(ETH)], stale_venues: [] });
    render(<Harness symbols={[HL]} onAdd={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Compare" }));

    await waitFor(() => expect(listed()).toHaveLength(1));
    expect(listed()[0]).toContain(ETH);
  });

  it("adds the highlighted market on Enter and closes", async () => {
    marketsApi.get.mockResolvedValue({ items: [market(HL), market(ETH)], stale_venues: [] });
    const onAdd = vi.fn();
    render(<Harness onAdd={onAdd} />);
    fireEvent.click(screen.getByRole("button", { name: "Compare" }));
    await waitFor(() => expect(listed()).toHaveLength(2));

    fireEvent.keyDown(field(), { key: "ArrowDown" });
    fireEvent.keyDown(field(), { key: "Enter" });

    expect(onAdd).toHaveBeenCalledWith(ETH);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("still accepts a typed id while the market list is down, saying so", async () => {
    marketsApi.get.mockRejectedValue(new HttpError(503, "down"));
    const onAdd = vi.fn();
    render(<Harness onAdd={onAdd} />);
    fireEvent.click(screen.getByRole("button", { name: "Compare" }));
    await waitFor(() => expect(within(dialog()).getByRole("status")).toHaveTextContent("No venue's market list is live"));

    fireEvent.change(field(), { target: { value: HL } });
    fireEvent.keyDown(field(), { key: "Enter" });

    expect(onAdd).toHaveBeenCalledWith(HL);
  });

  it("refuses an id the chart cannot compare, inline, and saves nothing", async () => {
    marketsApi.get.mockRejectedValue(new Error("down"));
    const onAdd = vi.fn();
    render(<Harness onAdd={onAdd} />);
    fireEvent.click(screen.getByRole("button", { name: "Compare" }));
    await waitFor(() => expect(within(dialog()).getByRole("status")).toHaveTextContent("could not be loaded"));

    fireEvent.change(field(), { target: { value: "BTCUSDT" } });
    fireEvent.keyDown(field(), { key: "Enter" });

    expect(within(dialog()).getByRole("alert")).toHaveTextContent("suffix");
    expect(onAdd).not.toHaveBeenCalled();
  });

  it("refuses a fourth compare symbol picked from the list", async () => {
    marketsApi.get.mockResolvedValue({ items: [market(ETH)], stale_venues: [] });
    const onAdd = vi.fn();
    render(<Harness symbols={["A.X", "B.X", "C.X"]} onAdd={onAdd} />);
    fireEvent.click(screen.getByRole("button", { name: "Compare" }));
    await waitFor(() => expect(listed()).toHaveLength(1));

    fireEvent.keyDown(field(), { key: "Enter" });

    expect(within(dialog()).getByRole("alert")).toHaveTextContent("At most 3");
    expect(onAdd).not.toHaveBeenCalled();
  });

  it("shows no search while disabled (Lines mode)", () => {
    render(<Harness disabled onAdd={vi.fn()} />);

    expect(screen.getByRole("button", { name: "Compare" })).toBeDisabled();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
