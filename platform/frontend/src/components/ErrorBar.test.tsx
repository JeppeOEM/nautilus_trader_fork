import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { recordFrontendError, resetErrorLog } from "../hooks/useErrorLog";
import ErrorBar from "./ErrorBar";

beforeEach(() => {
  resetErrorLog();
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ counts: {}, last: {} }) })));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ErrorBar", () => {
  it("renders nothing while no error has been recorded", async () => {
    render(<ErrorBar />);
    await act(async () => {});
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows a frontend error and a backend ledger entry, and has no dismiss control", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true,
      json: async () => ({ counts: { "candles.invalid_candle": 2 }, last: { "candles.invalid_candle": "h<l" } }),
    })));
    render(<ErrorBar />);
    await act(async () => {});
    act(() => recordFrontendError("boom"));
    const bar = screen.getByRole("alert");
    expect(bar.textContent).toContain("browser×1: boom");
    expect(bar.textContent).toContain("candles.invalid_candle×2: h<l");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("captures console.error calls from anywhere in the app", async () => {
    const original = console.error;
    render(<ErrorBar />);
    await act(async () => {});
    const spy = vi.spyOn(console, "error");
    act(() => console.error("CandlesPage: failed to load", new Error("500")));
    expect(screen.getByRole("alert").textContent).toContain("CandlesPage: failed to load Error: 500");
    spy.mockRestore();
    console.error = original;
  });
});
