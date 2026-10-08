import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { crashReport } from "../lib/crashReport";
import CrashBoundary from "./CrashBoundary";

function Bomb({ armed }: { armed: boolean }) {
  if (armed) throw new Error("Value is null");
  return <p>chart drawn</p>;
}

// React reports every caught render error through console.error: silenced here, asserted below.
let consoleError: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("CrashBoundary", () => {
  it("renders its children while nothing throws", () => {
    render(
      <CrashBoundary area="page">
        <Bomb armed={false} />
      </CrashBoundary>,
    );
    expect(screen.getByText("chart drawn")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows the error message, its stack and the component stack instead of a blank page", () => {
    render(
      <div>
        <p>nav stays</p>
        <CrashBoundary area="page">
          <Bomb armed />
        </CrashBoundary>
      </div>,
    );
    const panel = screen.getByRole("alert", { name: "The page crashed" });
    const text = panel.textContent ?? "";
    expect(text).toContain("Error: Value is null");
    expect(text).toContain("Stack:");
    expect(text).toMatch(/Component stack:\s+at Bomb/);
    expect(text).toContain(`URL: ${window.location.href}`);
    expect(screen.getByText("nav stays")).toBeTruthy();
    expect(consoleError).toHaveBeenCalled();
  });

  it("copies the same report it shows", async () => {
    const writeText = vi.fn(async (_text: string) => {});
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    render(
      <CrashBoundary area="page">
        <Bomb armed />
      </CrashBoundary>,
    );
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Copy error" })));
    expect(writeText).toHaveBeenCalledOnce();
    expect(screen.getByRole("alert").textContent).toContain(writeText.mock.calls[0][0]);
    vi.unstubAllGlobals();
  });

  it("renders the children again on Try again once the cause is gone", () => {
    function Harness() {
      const [armed, setArmed] = useState(true);
      return (
        <>
          <button type="button" onClick={() => setArmed(false)}>
            disarm
          </button>
          <CrashBoundary area="page">
            <Bomb armed={armed} />
          </CrashBoundary>
        </>
      );
    }
    render(<Harness />);
    expect(screen.getByRole("alert")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "disarm" }));
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(screen.getByText("chart drawn")).toBeTruthy();
  });
});

describe("crashReport", () => {
  it("names a thrown non-Error value instead of losing it", () => {
    const report = crashReport("plain string", "", "2026-10-08T16:30:00.000Z", "http://x/chart/A");
    expect(report.split("\n")[0]).toBe("Thrown value: plain string");
    expect(report).toContain("(no stack)");
    expect(report).toContain("Component stack:\n(none)");
  });
});
