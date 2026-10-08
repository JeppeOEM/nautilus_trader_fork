import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The error log is module state (one per page load, by design: no reset in production code), so
// each test loads a fresh copy of the module and the bar that reads it.
let ErrorBar: typeof import("./ErrorBar").default;
let recordFrontendError: typeof import("../hooks/useErrorLog").recordFrontendError;
// `installErrorCapture` wraps console.error once per module copy: restored after each test.
let originalConsoleError: typeof console.error;

beforeEach(async () => {
  originalConsoleError = console.error;
  vi.resetModules();
  ({ recordFrontendError } = await import("../hooks/useErrorLog"));
  ({ default: ErrorBar } = await import("./ErrorBar"));
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ counts: {}, last: {} }) })));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  console.error = originalConsoleError;
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
    render(<ErrorBar />);
    await act(async () => {});
    const spy = vi.spyOn(console, "error");
    act(() => console.error("CandlesPage: failed to load", new Error("500")));
    expect(screen.getByRole("alert").textContent).toContain("CandlesPage: failed to load Error: 500");
    spy.mockRestore();
  });
});
