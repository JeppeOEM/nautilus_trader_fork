import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MetricHistoryItem } from "../api/schema";
import type { MetricDatum } from "../components/chart/MetricTile";
import { gapRun } from "../lib/gaps";

// jsdom has no canvas: each tile's chart is stubbed down to the label and the data it was handed.
const tileData = new Map<string, MetricDatum[]>();
vi.mock("../components/chart/MetricTile", () => ({
  default: ({ label, data }: { label: string; data: MetricDatum[] }) => {
    tileData.set(label, data);
    return <div data-testid="metric-tile">{label}</div>;
  },
}));

const { default: HistoryPage } = await import("./HistoryPage");

const NS = 1_000_000_000;

function renderHistory(items: MetricHistoryItem[]) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ items }), { status: 200 })));
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={["/history/BTC-USD-PERP.DYDX"]}>
        <Routes>
          <Route path="/history/:iid" element={<HistoryPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  tileData.clear();
  vi.unstubAllGlobals();
});

describe("HistoryPage", () => {
  it("still renders a column's tile across a 720-row run of nulls, the run kept as whitespace (Story 32.1)", async () => {
    const slots = gapRun(60, 86_400, 60);
    renderHistory([
      { ts: 60 * NS, price: 100 },
      ...slots.map((s) => ({ ts: s * NS, price: null })),
      { ts: 86_400 * NS, price: 101 },
    ]);

    expect(await screen.findByText("Price")).toBeInTheDocument();
    const price = tileData.get("Price")!;
    expect(slots).toHaveLength(720);
    expect(price).toHaveLength(722);
    expect(price.filter((d) => "value" in d)).toEqual([
      { time: 60, value: 100 },
      { time: 86_400, value: 101 },
    ]);
    expect(price[1]).toEqual({ time: 120 }); // never 0, never dropped
    expect(screen.getAllByTestId("metric-tile")).toHaveLength(1); // all-null columns skipped
  });

  it("draws the OI, funding and 1h liquidation tiles, skipping an all-null one (Story 33.5)", async () => {
    renderHistory([
      { ts: 60 * NS, price: 100, open_interest: 51_234.5, funding_rate: 0.0001, liq_notional_1h: null },
      { ts: 120 * NS, price: 101, open_interest: null, funding_rate: -0.0002, liq_notional_1h: null },
    ]);

    expect(await screen.findByText("Open interest (venue units)")).toBeInTheDocument();
    expect(screen.getAllByTestId("metric-tile").map((t) => t.textContent)).toEqual(["Price", "Open interest (venue units)", "Funding rate (fraction per interval)"]);
    expect(tileData.get("Open interest (venue units)")).toEqual([{ time: 60, value: 51_234.5 }, { time: 120 }]);
    expect(tileData.get("Funding rate (fraction per interval)")).toEqual([
      { time: 60, value: 0.0001 },
      { time: 120, value: -0.0002 },
    ]);
  });

  it("draws the 1h liquidations tile when the instrument has the feed", async () => {
    renderHistory([{ ts: 60 * NS, liq_notional_1h: 0 }, { ts: 120 * NS, liq_notional_1h: 1500.5 }]);
    expect(await screen.findByText("Liquidations 1h (quote notional)")).toBeInTheDocument();
    expect(tileData.get("Liquidations 1h (quote notional)")).toEqual([
      { time: 60, value: 0 },
      { time: 120, value: 1500.5 },
    ]);
  });
});
