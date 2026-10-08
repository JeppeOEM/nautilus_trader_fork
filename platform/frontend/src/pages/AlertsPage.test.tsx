import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AlertResponse } from "../api/schema";
import AlertsPage from "./AlertsPage";

const IID = "BTCUSDT-LINEAR.BYBIT";

function alert(id: string, status: string, overrides: Partial<AlertResponse> = {}): AlertResponse {
  return {
    id, status, instrument_id: IID, level: 65000, frequency: "once_per_bar",
    bar_seconds: 60, template: "t", webhook_url: "https://x.test/h", created_ns: 1,
    condition: { kind: "price_cross", level: 65000 }, condition_text: "close crosses 65000 on 60s bars",
    ...overrides,
  };
}

const CATALOG = {
  RelativeStrengthIndex: {
    params: { period: 14 }, panel: "oscillator", category: "native", choices: {}, source_selectable: true,
    units: {}, outputs: ["value"],
  },
};

interface Call {
  url: string;
  method: string;
  body: Record<string, unknown> | null;
}

/** A fake data_api: GET lists, POST appends, PUT answers `putStatus`, every call recorded. */
function serve(alerts: AlertResponse[], putStatus = 200, catalog: () => Promise<Response> = async () => Response.json(CATALOG)) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? (JSON.parse(init.body as string) as Record<string, unknown>) : null;
    calls.push({ url, method, body });
    if (url === "/api/rankings") return Response.json({ items: [{ instrument_id: IID }], updated_at: 0, mode: "volume", stale_instrument_ids: [] });
    if (url === "/api/indicators/catalog") return catalog();
    if (method === "POST") {
      alerts.push(alert("new", "active", { condition: body!.condition, condition_text: "server text" }));
      return Response.json(alerts.at(-1), { status: 201 });
    }
    if (method === "PUT") {
      if (putStatus !== 200) return Response.json({ detail: "condition.pct must be non-zero and at most 1000 in magnitude" }, { status: putStatus });
      const i = alerts.findIndex((a) => url.endsWith(a.id));
      alerts[i] = { ...alerts[i], status: "active", condition: body!.condition, condition_text: "edited text" };
      return Response.json(alerts[i]);
    }
    return Response.json(alerts);
  });
  vi.stubGlobal("fetch", fetchMock);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AlertsPage />
    </QueryClientProvider>,
  );
  return calls;
}

const posted = (calls: Call[], method: string) => calls.filter((c) => c.method === method).map((c) => c.body);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AlertsPage", () => {
  it("lists each alert's server condition text and status, and delete removes it", async () => {
    let alerts = [alert("a", "active"), alert("b", "triggered"), alert("c", "expired")];
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/rankings") return Response.json({ items: [] });
      if (init?.method === "DELETE") {
        alerts = alerts.filter((a) => a.id !== "b");
        return new Response(null, { status: 204 });
      }
      return Response.json(alerts);
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
    expect(screen.getAllByText(`${IID} close crosses 65000 on 60s bars (once per bar)`, { exact: false })).toHaveLength(3);

    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[1]);
    await waitFor(() => expect(screen.queryByText("triggered")).not.toBeInTheDocument());
    expect(screen.getByText("active")).toBeInTheDocument();
  });

  it("shows an invalid alert's status and reason", async () => {
    serve([alert("a", "invalid", { invalid_reason: "drawing t1 no longer exists" })]);
    expect(await screen.findByText("invalid")).toBeInTheDocument();
    expect(screen.getByText("(drawing t1 no longer exists)")).toBeInTheDocument();
  });

  it("creates a price alert from the form without a chart", async () => {
    const calls = serve([]);
    const form = await screen.findByRole("region", { name: "New alert" });
    await waitFor(() => expect(within(form).getByLabelText("Instrument")).toHaveValue(IID));
    fireEvent.change(within(form).getByLabelText("Timeframe"), { target: { value: "3600" } });
    fireEvent.change(within(form).getByLabelText("Condition kind"), { target: { value: "price_above" } });
    fireEvent.change(within(form).getByLabelText("Price level"), { target: { value: "70000" } });
    fireEvent.change(within(form).getByLabelText("Webhook URL"), { target: { value: "https://hook.test/x" } });
    fireEvent.click(within(form).getByRole("button", { name: "Create" }));
    await waitFor(() => expect(posted(calls, "POST")).toHaveLength(1));
    expect(posted(calls, "POST")[0]).toMatchObject({
      instrument_id: IID, bar_seconds: 3600, condition: { kind: "price_above", level: 70000 },
      frequency: "once_per_bar_close", webhook_url: "https://hook.test/x",
    });
    expect(await screen.findByText(/server text/)).toBeInTheDocument();
  });

  it("creates an indicator alert (RSI > 70 on 1H) with its typed params and output", async () => {
    const calls = serve([]);
    const form = await screen.findByRole("region", { name: "New alert" });
    fireEvent.change(within(form).getByLabelText("Timeframe"), { target: { value: "3600" } });
    fireEvent.change(within(form).getByLabelText("Condition kind"), { target: { value: "indicator" } });
    const picker = within(form).getByLabelText("Indicator");
    await within(form).findByRole("option", { name: "RelativeStrengthIndex" });
    fireEvent.change(picker, { target: { value: "RelativeStrengthIndex" } });
    expect(within(form).getByLabelText("Parameter period")).toHaveValue(14);
    expect(within(form).getByLabelText("Output")).toHaveValue("value");
    fireEvent.change(within(form).getByLabelText("Value"), { target: { value: "70" } });
    fireEvent.click(within(form).getByRole("button", { name: "Create" }));
    await waitFor(() => expect(posted(calls, "POST")).toHaveLength(1));
    expect(posted(calls, "POST")[0]!.condition).toEqual({
      kind: "indicator", name: "RelativeStrengthIndex", params: { period: 14 }, source: "close",
      output: "value", op: ">", value: 70,
    });
  });

  it("edits a triggered alert with re-arm through PUT", async () => {
    const calls = serve([alert("b", "triggered", { frequency: "only_once" })]);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    const dialog = screen.getByRole("dialog", { name: "Edit Alert" });
    expect(within(dialog).getByLabelText("Price level")).toHaveValue(65000);
    expect(within(dialog).getByLabelText("Frequency")).toHaveValue("only_once");
    fireEvent.change(within(dialog).getByLabelText("Condition kind"), { target: { value: "price_cross_down" } });
    fireEvent.change(within(dialog).getByLabelText("Price level"), { target: { value: "60000" } });
    fireEvent.click(within(dialog).getByLabelText("Re-arm"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(posted(calls, "PUT")).toHaveLength(1));
    expect(calls.find((c) => c.method === "PUT")!.url).toBe("/api/alerts/b");
    expect(posted(calls, "PUT")[0]).toEqual({
      condition: { kind: "price_cross_down", level: 60000 }, frequency: "only_once", expires_at_ns: null,
      template: "t", webhook_url: "https://x.test/h", rearm: true,
    });
    expect(await screen.findByText(/edited text/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Edit Alert" })).not.toBeInTheDocument();
  });

  it("offers Re-arm only on a triggered alert", async () => {
    serve([alert("a", "active")]);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    expect(within(screen.getByRole("dialog", { name: "Edit Alert" })).queryByLabelText("Re-arm")).not.toBeInTheDocument();
  });

  it("keeps the dialog open with the server's detail when a PUT fails", async () => {
    serve([alert("a", "active", { condition: { kind: "pct_move", pct: 5, bars: 2 }, level: null })], 422);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    const dialog = screen.getByRole("dialog", { name: "Edit Alert" });
    expect(within(dialog).getByLabelText("Bars")).toHaveValue(2);
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(await within(dialog).findByText("condition.pct must be non-zero and at most 1000 in magnitude")).toBeInTheDocument();
  });

  it("offers Re-arm on an only_once alert that fired and then turned invalid", async () => {
    serve([
      alert("a", "invalid", { frequency: "only_once", last_fired_ns: 5, invalid_reason: "drawing t1 no longer exists" }),
      alert("b", "invalid", { frequency: "only_once", invalid_reason: "drawing t2 no longer exists" }),
    ]);
    const [fired, neverFired] = await screen.findAllByRole("button", { name: "Edit" });
    fireEvent.click(fired);
    expect(within(screen.getByRole("dialog", { name: "Edit Alert" })).getByLabelText("Re-arm")).toBeInTheDocument();
    fireEvent.click(within(screen.getByRole("dialog", { name: "Edit Alert" })).getByRole("button", { name: "Cancel" }));
    fireEvent.click(neverFired);
    expect(within(screen.getByRole("dialog", { name: "Edit Alert" })).queryByLabelText("Re-arm")).not.toBeInTheDocument();
  });

  it("keeps an edited alert's exact expiry unless its date is changed", async () => {
    const expires = new Date(2030, 0, 15, 12, 34, 56).getTime() * 1_000_000; // 12:34:56 local
    const calls = serve([alert("a", "active", { expires_at_ns: expires })]);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    let dialog = screen.getByRole("dialog", { name: "Edit Alert" });
    expect(within(dialog).getByLabelText("Expiration date")).toHaveValue("2030-01-15");
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(posted(calls, "PUT")).toHaveLength(1));
    expect(posted(calls, "PUT")[0]!.expires_at_ns).toBe(expires);

    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    dialog = screen.getByRole("dialog", { name: "Edit Alert" });
    fireEvent.change(within(dialog).getByLabelText("Expiration date"), { target: { value: "2030-01-16" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(posted(calls, "PUT")).toHaveLength(2));
    expect(posted(calls, "PUT")[1]!.expires_at_ns).toBe(new Date(2030, 0, 16, 23, 59, 59).getTime() * 1_000_000);
  });

  it("refuses to save an indicator condition until the catalog has loaded", async () => {
    let answer: (r: Response) => void = () => {};
    const catalog = new Promise<Response>((resolve) => {
      answer = resolve;
    });
    const rsi = { kind: "indicator", name: "RelativeStrengthIndex", params: { period: 14 }, source: "close", output: "value", op: ">", value: 70 };
    const calls = serve([alert("a", "active", { condition: rsi, level: null })], 200, () => catalog);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    const dialog = screen.getByRole("dialog", { name: "Edit Alert" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(await within(dialog).findByText(/indicator catalog has not loaded/)).toBeInTheDocument();
    expect(posted(calls, "PUT")).toHaveLength(0);
    answer(Response.json(CATALOG));
    await within(dialog).findByRole("option", { name: "RelativeStrengthIndex" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(posted(calls, "PUT")).toHaveLength(1));
    expect(posted(calls, "PUT")[0]!.condition).toEqual(rsi);
  });

  it("shows a stored output the catalog entry no longer lists as the selected output", async () => {
    const rsi = { kind: "indicator", name: "RelativeStrengthIndex", params: { period: 14 }, source: "close", output: "signal", op: ">", value: 70 };
    serve([alert("a", "invalid", { condition: rsi, level: null, invalid_reason: "output 'signal' is not one of the replay's ['value']" })]);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    const dialog = screen.getByRole("dialog", { name: "Edit Alert" });
    await within(dialog).findByRole("option", { name: "value" }); // the catalog loaded
    expect(within(dialog).getByLabelText("Output")).toHaveValue("signal");
  });

  it("keeps a stored string param a string (an indicator the catalog does not list)", async () => {
    const vwap = {
      kind: "indicator", name: "AnchoredStoredVWAP", params: { anchor_t: "0" }, source: "close", output: "value", op: ">", value: 1,
    };
    const calls = serve([alert("a", "active", { condition: vwap, level: null })]);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    const dialog = screen.getByRole("dialog", { name: "Edit Alert" });
    await within(dialog).findByRole("option", { name: "RelativeStrengthIndex" }); // the catalog loaded
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(posted(calls, "PUT")).toHaveLength(1));
    expect(posted(calls, "PUT")[0]!.condition).toEqual(vwap);
  });

  it("never keeps another coin's trendline or shows its drawings while a coin's are loading", async () => {
    const ETH = "ETHUSDT-LINEAR.BYBIT";
    const fetchMock = vi.fn(async (url: string) => {
      if (url === "/api/rankings") return Response.json({ items: [{ instrument_id: IID }, { instrument_id: ETH }] });
      if (url === `/api/coin/${encodeURIComponent(IID)}/drawings`) {
        return Response.json({ items: [{ id: "t1", kind: "trendline", anchors: [{ time: 1, price: 1 }, { time: 2, price: 2 }] }] });
      }
      if (url.endsWith("/drawings")) return new Promise<Response>(() => {}); // ETH's never answers
      return Response.json([]);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <QueryClientProvider client={new QueryClient()}>
        <AlertsPage />
      </QueryClientProvider>,
    );
    const form = await screen.findByRole("region", { name: "New alert" });
    await waitFor(() => expect(within(form).getByLabelText("Instrument")).toHaveValue(IID));
    fireEvent.change(within(form).getByLabelText("Condition kind"), { target: { value: "trendline_cross" } });
    await within(form).findByRole("option", { name: /t1/ });
    fireEvent.change(within(form).getByLabelText("Trendline"), { target: { value: "t1" } });
    fireEvent.change(within(form).getByLabelText("Instrument"), { target: { value: ETH } });
    await waitFor(() => expect(within(form).getByLabelText("Trendline")).toHaveValue(""));
    expect(within(form).queryByRole("option", { name: /t1/ })).not.toBeInTheDocument();
  });
});
