import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import CompareControl from "./CompareControl";

const marketsApi = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("../../api/client", () => ({
  HttpError: class HttpError extends Error {
    status = 0;
  },
  fetchMarkets: (...args: unknown[]) => marketsApi.get(...args),
}));

const MAIN = "BTCUSDT-LINEAR.BYBIT";

beforeEach(() => {
  marketsApi.get.mockResolvedValue({ items: [], stale_venues: [] });
});
afterEach(cleanup);

const field = (): HTMLInputElement => screen.getByRole("combobox", { name: "Compare instrument id" }) as HTMLInputElement;

function refuseOne(): void {
  fireEvent.click(screen.getByRole("button", { name: "Compare" }));
  fireEvent.change(field(), { target: { value: "BTCUSDT" } });
  fireEvent.keyDown(field(), { key: "Enter" });
  expect(screen.getByRole("alert")).toHaveTextContent("suffix");
}

describe("CompareControl (Story 33.9)", () => {
  it("forgets the text and the refusal when Escape closes it", () => {
    render(<CompareControl instrumentId={MAIN} symbols={[]} disabled={false} onAdd={vi.fn()} />);
    refuseOne();

    fireEvent.keyDown(field(), { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "Compare" }));

    expect(field().value).toBe("");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("forgets the text and the refusal when the button closes it", () => {
    render(<CompareControl instrumentId={MAIN} symbols={[]} disabled={false} onAdd={vi.fn()} />);
    refuseOne();

    fireEvent.click(screen.getByRole("button", { name: "Compare" }));
    fireEvent.click(screen.getByRole("button", { name: "Compare" }));

    expect(field().value).toBe("");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("closes when disabled (Lines mode) and stays closed when enabled again", () => {
    const { rerender } = render(<CompareControl instrumentId={MAIN} symbols={[]} disabled={false} onAdd={vi.fn()} />);
    refuseOne();

    rerender(<CompareControl instrumentId={MAIN} symbols={[]} disabled onAdd={vi.fn()} />);
    rerender(<CompareControl instrumentId={MAIN} symbols={[]} disabled={false} onAdd={vi.fn()} />);

    expect(screen.queryByRole("combobox", { name: "Compare instrument id" })).toBeNull();
    expect(screen.getByRole("button", { name: "Compare" })).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(screen.getByRole("button", { name: "Compare" }));
    expect(field().value).toBe("");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  const HL = "BTC-USD-PERP.HYPERLIQUID";
  const ETH = "ETHUSDT-LINEAR.BYBIT";
  const listed = (container: HTMLElement): string[] =>
    Array.from(container.querySelectorAll("datalist option")).map((o) => (o as HTMLOptionElement).value);
  const market = (iid: string) => ({ instrument_id: iid, symbol: iid.split("-")[0], venue: iid.split(".")[1], same_asset: false });

  it("does not suggest a market already compared", async () => {
    marketsApi.get.mockResolvedValue({ items: [market(HL), market(ETH)], stale_venues: [] });
    const { container } = render(<CompareControl instrumentId={MAIN} symbols={[HL]} disabled={false} onAdd={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Compare" }));

    await waitFor(() => expect(listed(container)).toEqual([ETH]));
  });

  it("drops an earlier list when a later fetch fails", async () => {
    marketsApi.get.mockResolvedValueOnce({ items: [market(HL)], stale_venues: ["BYBIT"] });
    const { container } = render(<CompareControl instrumentId={MAIN} symbols={[]} disabled={false} onAdd={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Compare" }));
    await waitFor(() => expect(listed(container)).toEqual([HL]));
    fireEvent.click(screen.getByRole("button", { name: "Compare" }));

    marketsApi.get.mockRejectedValueOnce(new Error("down"));
    fireEvent.click(screen.getByRole("button", { name: "Compare" }));

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("could not be loaded"));
    expect(listed(container)).toEqual([]);
    expect(screen.queryByText(/Market list stale/)).toBeNull();
  });
});
