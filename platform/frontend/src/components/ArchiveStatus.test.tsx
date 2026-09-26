import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ArchiveRun, ArchiveStatusResponse } from "../api/schema";
import { archiveOutcome } from "../hooks/useArchiveStatus";
import ArchiveStatus from "./ArchiveStatus";

function run(exits: number[], overrides: Partial<ArchiveRun> = {}): ArchiveRun {
  return {
    run_id: "r-1",
    kind: "nightly",
    day: "2026-09-25",
    days: ["2026-09-25"],
    started: "2026-09-26T03:07:00Z",
    finished: "2026-09-26T03:41:12Z",
    steps: exits.map((exit, i) => ({ venue: i === 0 ? "BYBIT" : null, name: `step${i}`, exit, duration_s: 1.5 })),
    ...overrides,
  };
}

function status(overrides: Partial<ArchiveStatusResponse> = {}): ArchiveStatusResponse {
  return {
    next_run: "2026-09-27T03:07:00Z",
    next_intraday: "2026-09-26T16:07:00Z",
    running: null,
    last_run: run([0, 0]),
    last_intraday: null,
    ...overrides,
  };
}

type Handler = (url: string, init?: RequestInit) => Response;

function stubFetch(handler: Handler) {
  const mock = vi.fn(async (url: string, init?: RequestInit) => handler(url, init));
  vi.stubGlobal("fetch", mock);
  return mock;
}

function statusOnly(body: ArchiveStatusResponse): Handler {
  return () => new Response(JSON.stringify(body), { status: 200 });
}

function renderStatus() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <ArchiveStatus />
    </QueryClientProvider>,
  );
}

function statusText(): string {
  return screen.getByLabelText("Maintenance status").textContent ?? "";
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("archiveOutcome", () => {
  it("is ok when every step exited 0", () => {
    expect(archiveOutcome(run([0, 0, 0]))).toBe("ok");
  });

  it("is findings when some step exited 2 and no other step failed", () => {
    expect(archiveOutcome(run([0, 2, 0]))).toBe("findings");
  });

  it("is FAILED when any step exited with anything but 0 or 2", () => {
    expect(archiveOutcome(run([2, 1, 0]))).toBe("FAILED");
  });
});

describe("ArchiveStatus", () => {
  it("shows the last run's day, outcome, finish time and the next run", async () => {
    stubFetch(statusOnly(status()));
    renderStatus();
    await waitFor(() => expect(statusText()).toContain("last 2026-09-25 ok"));
    expect(statusText()).toContain("2026-09-26 03:41Z");
    expect(statusText()).toContain("next 2026-09-27 03:07Z");
  });

  it("names the failing steps of a FAILED run", async () => {
    stubFetch(statusOnly(status({ last_run: run([1, 2]) })));
    renderStatus();
    await waitFor(() => expect(statusText()).toContain("FAILED (BYBIT step0=1, step1=2)"));
  });

  it("shows a running job instead of the last run", async () => {
    stubFetch(statusOnly(status({ running: run([0, 0], { finished: null, kind: "run_now" }) })));
    renderStatus();
    await waitFor(() => expect(statusText()).toContain("running run_now 2026-09-25 (step 3)…"));
    expect(statusText()).not.toContain("last ");
  });

  it("surfaces a failed intraday merge next to the nightly", async () => {
    stubFetch(statusOnly(status({ last_intraday: run([1], { kind: "intraday", day: "2026-09-26" }) })));
    renderStatus();
    await waitFor(() => expect(statusText()).toContain("intraday 2026-09-26 FAILED"));
  });

  it("says unavailable on a 503 rather than inventing a status", async () => {
    stubFetch(() => new Response(JSON.stringify({ detail: "Archive status not yet available" }), { status: 503 }));
    renderStatus();
    await waitFor(() => expect(statusText()).toContain("status unavailable"));
  });

  it("Run now asks for confirmation, then POSTs day null and closes", async () => {
    const fetchMock = stubFetch((_url, init) =>
      init?.method === "POST"
        ? new Response(JSON.stringify({ day: null }), { status: 202 })
        : new Response(JSON.stringify(status()), { status: 200 }),
    );
    renderStatus();
    fireEvent.click(screen.getByRole("button", { name: "Run now" }));
    const dialog = screen.getByRole("dialog", { name: "Run maintenance now" });
    expect(dialog).toHaveAttribute("open");
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Run" }));

    await waitFor(() => expect(dialog).not.toHaveAttribute("open"));
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post?.[0]).toBe("/api/archive/run");
    expect(post?.[1]?.body).toBe(JSON.stringify({ day: null }));
  });

  it("Cancel closes the dialog without posting", async () => {
    const fetchMock = stubFetch(statusOnly(status()));
    renderStatus();
    fireEvent.click(screen.getByRole("button", { name: "Run now" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    const dialog = screen.getByRole("dialog", { hidden: true });
    expect(dialog).not.toHaveAttribute("open");
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("shows a failed POST inline and keeps the dialog open", async () => {
    stubFetch((_url, init) =>
      init?.method === "POST"
        ? new Response(JSON.stringify({ detail: "no archive scheduler subscribed to archive:control" }), {
            status: 503,
          })
        : new Response(JSON.stringify(status()), { status: 200 }),
    );
    renderStatus();
    fireEvent.click(screen.getByRole("button", { name: "Run now" }));
    fireEvent.click(screen.getByRole("button", { name: "Run" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("503 no archive scheduler subscribed to archive:control");
    expect(screen.getByRole("dialog", { name: "Run maintenance now" })).toHaveAttribute("open");
  });
});
