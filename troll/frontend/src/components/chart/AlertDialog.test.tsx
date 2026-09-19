import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import AlertDialog from "./AlertDialog";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function setup(priceLines = [{ id: "hline-1", price: 65000.5, color: "#fff" }]) {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify({ id: "x" }), { status: 201 }));
  vi.stubGlobal("fetch", fetchMock);
  const onClose = vi.fn();
  render(
    <AlertDialog open onClose={onClose} instrumentId="BTC-USD-PERP.DYDX" barSeconds={60} priceLines={priceLines} />,
  );
  return { fetchMock, onClose };
}

function postedBody(fetchMock: ReturnType<typeof vi.fn>): Record<string, unknown> {
  const init = fetchMock.mock.calls[0][1] as RequestInit;
  return JSON.parse(init.body as string) as Record<string, unknown>;
}

describe("AlertDialog", () => {
  it("posts a static-value alert with the chosen frequency and webhook", async () => {
    const { fetchMock, onClose } = setup([]);
    fireEvent.change(screen.getByLabelText("Price level"), { target: { value: "65000" } });
    fireEvent.change(screen.getByLabelText("Frequency"), { target: { value: "only_once" } });
    fireEvent.change(screen.getByLabelText("Webhook URL"), { target: { value: "https://hook.test/x" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(postedBody(fetchMock)).toMatchObject({
      instrument_id: "BTC-USD-PERP.DYDX", level: 65000, frequency: "only_once", bar_seconds: 60,
      expires_at_ns: null, webhook_url: "https://hook.test/x",
    });
  });

  it("resolves a horizontal-line target to that line's price", async () => {
    const { fetchMock } = setup();
    fireEvent.change(screen.getByLabelText("Condition target"), { target: { value: "hline-1" } });
    fireEvent.change(screen.getByLabelText("Webhook URL"), { target: { value: "https://hook.test/x" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(postedBody(fetchMock).level).toBe(65000.5);
  });

  it("refuses to save without a price level", async () => {
    const { fetchMock } = setup([]);
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText("Enter a price level.")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
