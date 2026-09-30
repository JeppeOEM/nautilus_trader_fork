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
});
