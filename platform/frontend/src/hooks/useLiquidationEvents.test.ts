import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { LiquidationItem, LiquidationsResponse } from "../api/schema";

const fetchLiquidationsMock = vi.fn<(...args: unknown[]) => Promise<LiquidationsResponse>>();

vi.mock("../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/client")>()),
  fetchLiquidations: (...args: unknown[]) => fetchLiquidationsMock(...args),
}));

const { useLiquidationEvents, mergeLiquidations, MARKER_MAX_ROWS, TAPE_ROWS } = await import("./useLiquidationEvents");

const IID = "BTCUSDT-LINEAR.BYBIT";
const NS = 1_000_000_000;

function row(id: string, tSec: number): LiquidationItem {
  return {
    side: "long",
    size_units: 4,
    price_units: 1_000_000,
    price_precision: 1,
    size_precision: 3,
    venue_event_id: id,
    ts_event: tSec * NS,
    ts_init: tSec * NS,
    price_kind: "bankruptcy",
    notional_units: 4_000_000,
    notional_precision: 4,
  };
}

function page(rows: LiquidationItem[], hasMore: boolean): LiquidationsResponse {
  return { items: rows, has_more: hasMore, venue: "BYBIT", market: "perp", price_precision: 1, size_precision: 3 };
}

const flush = () => act(async () => {});

beforeEach(() => {
  fetchLiquidationsMock.mockReset();
});

describe("mergeLiquidations", () => {
  it("keeps each venue event once, oldest first", () => {
    const merged = mergeLiquidations([row("b", 2), row("a", 1)], [row("b", 2), row("c", 3)], 10);
    expect(merged).toEqual({ rows: [row("a", 1), row("b", 2), row("c", 3)], capped: false });
  });

  it("appends a newer live row, and still dedupes one already held", () => {
    const held = [row("a", 1), row("b", 2)];
    expect(mergeLiquidations(held, [row("c", 3)], 50).rows.map((r) => r.venue_event_id)).toEqual(["a", "b", "c"]);
    expect(mergeLiquidations(held, [row("b", 2)], 50).rows.map((r) => r.venue_event_id)).toEqual(["a", "b"]);
    expect(mergeLiquidations(held, [row("c", 3)], 2)).toEqual({ rows: [row("b", 2), row("c", 3)], capped: true });
  });

  it("keeps the newest rows past the cap and says it was capped", () => {
    const merged = mergeLiquidations([row("a", 1), row("b", 2)], [row("c", 3)], 2);
    expect(merged.rows.map((r) => r.venue_event_id)).toEqual(["b", "c"]);
    expect(merged.capped).toBe(true);
  });
});

describe("useLiquidationEvents", () => {
  it("issues no request and takes no live row while off", async () => {
    const { result } = renderHook(() => useLiquidationEvents(IID, "tape", false, 0));
    await flush();
    act(() => result.current.push([row("a", 1)]));
    expect(fetchLiquidationsMock).not.toHaveBeenCalled();
    expect(result.current.rows).toEqual([]);
  });

  it("tape: holds exactly the newest 50, a live row evicting the oldest, and dedupes a re-served one", async () => {
    const first = Array.from({ length: TAPE_ROWS }, (_, i) => row(`r${i}`, i));
    fetchLiquidationsMock.mockResolvedValue(page(first, true));
    const { result } = renderHook(() => useLiquidationEvents(IID, "tape", true, 0));
    await flush();
    expect(fetchLiquidationsMock.mock.calls[0][2]).toBe(TAPE_ROWS);
    expect(result.current.rows).toHaveLength(TAPE_ROWS);

    act(() => result.current.push([row("live", 100), row("r49", 49)]));

    expect(result.current.rows).toHaveLength(TAPE_ROWS);
    expect(result.current.rows[0].venue_event_id).toBe("r1");
    expect(result.current.rows.at(-1)!.venue_event_id).toBe("live");
    expect(fetchLiquidationsMock).toHaveBeenCalledTimes(1); // the tape never pages back
  });

  it("markers: pages back to the earliest candle, then stops", async () => {
    fetchLiquidationsMock.mockResolvedValueOnce(page([row("c", 900)], true)).mockResolvedValueOnce(page([row("b", 500)], true));
    const { result } = renderHook(() => useLiquidationEvents(IID, "markers", true, 600));
    await flush();
    await flush();

    expect(fetchLiquidationsMock).toHaveBeenCalledTimes(2);
    expect(fetchLiquidationsMock.mock.calls[1][1]).toBe(900 * NS);
    expect(result.current.rows.map((r) => r.venue_event_id)).toEqual(["b", "c"]);
  });

  it(`markers: stops at ${MARKER_MAX_ROWS} rows and says so`, async () => {
    // Each page is 500 rows older than the last, every one later than the earliest candle (0).
    let calls = 0;
    fetchLiquidationsMock.mockImplementation(() => {
      const offset = calls++ * 500;
      const rows = Array.from({ length: 500 }, (_, i) => row(`p${offset + i}`, 100_000 - offset - 500 + i));
      return Promise.resolve(page(rows, true));
    });
    const { result } = renderHook(() => useLiquidationEvents(IID, "markers", true, 0));
    for (let i = 0; i < 15; i++) await flush();

    expect(result.current.rows).toHaveLength(MARKER_MAX_ROWS);
    expect(result.current.capped).toBe(true);
    expect(fetchLiquidationsMock).toHaveBeenCalledTimes(MARKER_MAX_ROWS / 500 + 1);
  });

  it("re-reads the newest page when re-enabled: live rows were refused while off", async () => {
    fetchLiquidationsMock.mockResolvedValueOnce(page([row("a", 1)], false)).mockResolvedValueOnce(page([row("a", 1), row("b", 2)], false));
    const { result, rerender } = renderHook(({ on }) => useLiquidationEvents(IID, "tape", on, 0), { initialProps: { on: true } });
    await flush();
    rerender({ on: false });
    act(() => result.current.push([row("b", 2)])); // refused while off
    rerender({ on: true });
    await flush();
    expect(fetchLiquidationsMock).toHaveBeenCalledTimes(2);
    expect(result.current.rows.map((r) => r.venue_event_id)).toEqual(["a", "b"]);
  });

  it("runs a newest re-read asked for while a page was in flight once that page settles", async () => {
    let release: (p: LiquidationsResponse) => void = () => {};
    fetchLiquidationsMock.mockResolvedValueOnce(page([row("c", 900)], true));
    fetchLiquidationsMock.mockReturnValueOnce(new Promise((resolve) => (release = resolve))); // the older page
    fetchLiquidationsMock.mockResolvedValueOnce(page([row("c", 900), row("d", 950)], true));
    const { result } = renderHook(() => useLiquidationEvents(IID, "markers", true, 600));
    await flush(); // the first page landed; the older page is in flight
    act(() => result.current.refreshNewest()); // a reconnect: busy, so pending
    expect(fetchLiquidationsMock).toHaveBeenCalledTimes(2);
    release(page([row("b", 500)], false));
    await flush();
    expect(fetchLiquidationsMock).toHaveBeenCalledTimes(3);
    expect(result.current.rows.map((r) => r.venue_event_id)).toEqual(["b", "c", "d"]);
  });

  it("reports a failed replay page as a failure, never as an empty tape", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    fetchLiquidationsMock.mockResolvedValueOnce(page([row("a", 1)], false)).mockRejectedValueOnce(new Error("network"));
    const { result } = renderHook(() => useLiquidationEvents(IID, "tape", true, 0, 2 * NS));
    await flush();
    await flush();
    expect([result.current.replayRows, result.current.replayError]).toEqual([null, "load failed"]);
    vi.restoreAllMocks();
  });

  it("reports an empty first page as loaded with no rows (no feed)", async () => {
    fetchLiquidationsMock.mockResolvedValue(page([], false));
    const { result } = renderHook(() => useLiquidationEvents(IID, "tape", true, 0));
    await flush();
    expect([result.current.loaded, result.current.rows]).toEqual([true, []]);
  });
});
