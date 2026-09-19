import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AlertResponse } from "../api/schema";
import AlertsPage from "./AlertsPage";

function alert(id: string, status: string): AlertResponse {
  return {
    id, status, instrument_id: "BTC-USD-PERP.DYDX", level: 65000, frequency: "once_per_bar",
    bar_seconds: 60, template: "t", webhook_url: "https://x.test/h", created_ns: 1,
  };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AlertsPage", () => {
  it("renders each alert's status and delete removes it from the list", async () => {
    let alerts = [alert("a", "active"), alert("b", "triggered"), alert("c", "expired")];
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "DELETE") {
        alerts = alerts.filter((a) => a.id !== "b");
        return new Response(null, { status: 204 });
      }
      return new Response(JSON.stringify(alerts), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <QueryClientProvider client={new QueryClient()}>
        <AlertsPage />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("active")).toBeInTheDocument();
    expect(screen.getByText("triggered")).toBeInTheDocument();
    expect(screen.getByText("expired")).toBeInTheDocument();
    expect(screen.getAllByText(/BTC-USD-PERP.DYDX price crosses 65000/)).toHaveLength(3);

    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[1]);
    await waitFor(() => expect(screen.queryByText("triggered")).not.toBeInTheDocument());
    expect(screen.getByText("active")).toBeInTheDocument();
  });
});
