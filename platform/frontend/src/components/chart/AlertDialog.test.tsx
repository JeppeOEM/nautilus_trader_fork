import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AlertCondition } from "../../lib/alertConditions";
import AlertDialog from "./AlertDialog";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const TRENDLINE = { id: "trendline-1", kind: "trendline", anchors: [{ time: 1000, price: 100 }, { time: 2000, price: 200 }] };

function setup(
  priceLines = [{ id: "hline-1", price: 65000.5, color: "#fff" }],
  initialCondition?: AlertCondition,
  saveDrawings?: () => Promise<void>,
) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "POST") return new Response(JSON.stringify({ id: "x" }), { status: 201 });
    if (url.endsWith("/drawings")) return new Response(JSON.stringify({ items: [TRENDLINE] }), { status: 200 });
    return new Response(JSON.stringify({}), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  const onClose = vi.fn();
  render(
    <AlertDialog
      open
      onClose={onClose}
      instrumentId="BTC-USD-PERP.DYDX"
      barSeconds={60}
      priceLines={priceLines}
      initialCondition={initialCondition}
      saveDrawings={saveDrawings}
    />,
  );
  return { fetchMock, onClose };
}

function postedBody(fetchMock: ReturnType<typeof vi.fn>): Record<string, unknown> {
  const call = fetchMock.mock.calls.find((c) => (c[1] as RequestInit | undefined)?.method === "POST");
  return JSON.parse((call![1] as RequestInit).body as string) as Record<string, unknown>;
}

describe("AlertDialog", () => {
  it("posts a static-value price cross with the chosen frequency and webhook", async () => {
    const { fetchMock, onClose } = setup([]);
    fireEvent.change(screen.getByLabelText("Price level"), { target: { value: "65000" } });
    fireEvent.change(screen.getByLabelText("Frequency"), { target: { value: "only_once" } });
    fireEvent.change(screen.getByLabelText("Webhook URL"), { target: { value: "https://hook.test/x" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    const body = postedBody(fetchMock);
    expect(body).toMatchObject({
      instrument_id: "BTC-USD-PERP.DYDX", condition: { kind: "price_cross", level: 65000 }, frequency: "only_once",
      bar_seconds: 60, expires_at_ns: null, webhook_url: "https://hook.test/x",
    });
    expect(body).not.toHaveProperty("level");
  });

  it("resolves a horizontal-line target to that line's price", async () => {
    const { fetchMock } = setup();
    fireEvent.change(screen.getByLabelText("Condition target"), { target: { value: "hline-1" } });
    fireEvent.change(screen.getByLabelText("Webhook URL"), { target: { value: "https://hook.test/x" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(postedBody(fetchMock).condition).toEqual({ kind: "price_cross", level: 65000.5 });
  });

  it("refuses to save without a price level", async () => {
    const { fetchMock } = setup([]);
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText("Price level is required.")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("opens prefilled from initialCondition (a horizontal line's Add alert…)", async () => {
    const { fetchMock } = setup([], { kind: "price_cross", level: 64000.25 });
    expect(screen.getByLabelText("Condition kind")).toHaveValue("price_cross");
    expect(screen.getByLabelText("Price level")).toHaveValue(64000.25);
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(postedBody(fetchMock).condition).toEqual({ kind: "price_cross", level: 64000.25 });
  });

  it("posts a trendline cross naming the prefilled trendline, without a level target", async () => {
    const { fetchMock } = setup([], { kind: "trendline_cross", drawing_id: "trendline-1" });
    expect(screen.queryByLabelText("Condition target")).not.toBeInTheDocument();
    expect(await screen.findByRole("option", { name: /trendline-1 \(100 → 200\)/ })).toBeInTheDocument();
    expect(screen.getByLabelText("Trendline")).toHaveValue("trendline-1");
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(postedBody(fetchMock).condition).toEqual({ kind: "trendline_cross", drawing_id: "trendline-1" }));
  });

  it("shows the server's 422 detail inline", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ detail: "condition.upper must be greater than lower" }), { status: 422 }));
    vi.stubGlobal("fetch", fetchMock);
    render(
      <AlertDialog
        open
        onClose={() => {}}
        instrumentId="BTC-USD-PERP.DYDX"
        barSeconds={60}
        priceLines={[]}
        initialCondition={{ kind: "channel_exit", upper: 90, lower: 110 }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText("condition.upper must be greater than lower")).toBeInTheDocument();
  });

  const posts = (fetchMock: ReturnType<typeof vi.fn>) => fetchMock.mock.calls.filter((c) => (c[1] as RequestInit | undefined)?.method === "POST");

  it("saves the chart's drawings before creating a trendline alert (a just-drawn line)", async () => {
    let release: () => void = () => {};
    const saveDrawings = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          release = resolve;
        }),
    );
    const { fetchMock, onClose } = setup([], { kind: "trendline_cross", drawing_id: "trendline-1" }, saveDrawings);
    fireEvent.change(screen.getByLabelText("Webhook URL"), { target: { value: "https://hook.test/x" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(saveDrawings).toHaveBeenCalledTimes(1));
    expect(posts(fetchMock)).toHaveLength(0); // not before the drawings file holds the line
    release();
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(postedBody(fetchMock).condition).toEqual({ kind: "trendline_cross", drawing_id: "trendline-1" });
  });

  it("creates no trendline alert when the drawings cannot be saved", async () => {
    const saveDrawings = vi.fn(async () => {
      throw new Error("HTTP 503");
    });
    const { fetchMock } = setup([], { kind: "trendline_cross", drawing_id: "trendline-1" }, saveDrawings);
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText(/The trendline is not saved yet.*HTTP 503/)).toBeInTheDocument();
    expect(posts(fetchMock)).toHaveLength(0);
  });

  it("does not save the drawings for a price alert", async () => {
    const saveDrawings = vi.fn(async () => {});
    const { onClose } = setup([], { kind: "price_cross", level: 1 }, saveDrawings);
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(saveDrawings).not.toHaveBeenCalled();
  });

  it("stores the target line's price at save time, after it was dragged", async () => {
    const fetchMock = vi.fn(async () => Response.json({ id: "x" }, { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);
    const props = { open: true, onClose: () => {}, instrumentId: "BTC-USD-PERP.DYDX", barSeconds: 60 };
    const { rerender } = render(<AlertDialog {...props} priceLines={[{ id: "hline-1", price: 65000.5, color: "#fff" }]} />);
    fireEvent.change(screen.getByLabelText("Condition target"), { target: { value: "hline-1" } });
    expect(screen.getByLabelText("Price level")).toHaveValue(65000.5);
    rerender(<AlertDialog {...props} priceLines={[{ id: "hline-1", price: 65100, color: "#fff" }]} />); // dragged
    expect(screen.getByLabelText("Price level")).toHaveValue(65100);
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(posts(fetchMock)).toHaveLength(1));
    expect(postedBody(fetchMock).condition).toEqual({ kind: "price_cross", level: 65100 });
  });

  it("detaches the level from the line once a level is typed", async () => {
    const { fetchMock } = setup();
    fireEvent.change(screen.getByLabelText("Condition target"), { target: { value: "hline-1" } });
    fireEvent.change(screen.getByLabelText("Price level"), { target: { value: "64000" } });
    expect(screen.getByLabelText("Condition target")).toHaveValue("static");
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(posts(fetchMock)).toHaveLength(1));
    expect(postedBody(fetchMock).condition).toEqual({ kind: "price_cross", level: 64000 });
  });

  it("starts every opening with a fresh delivery (frequency, template, webhook)", () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({})));
    const props = { onClose: () => {}, instrumentId: "BTC-USD-PERP.DYDX", barSeconds: 60, priceLines: [] };
    const { rerender } = render(<AlertDialog {...props} open />);
    fireEvent.change(screen.getByLabelText("Frequency"), { target: { value: "only_once" } });
    fireEvent.change(screen.getByLabelText("Message template"), { target: { value: "custom" } });
    fireEvent.change(screen.getByLabelText("Webhook URL"), { target: { value: "https://hook.test/x" } });
    rerender(<AlertDialog {...props} open={false} />);
    rerender(<AlertDialog {...props} open />);
    expect(screen.getByLabelText("Frequency")).toHaveValue("once_per_bar_close");
    expect(screen.getByLabelText("Message template")).toHaveValue("{{ticker}} {{condition}}: {{value}} ({{time}})");
    expect(screen.getByLabelText("Webhook URL")).toHaveValue("");
  });
});
