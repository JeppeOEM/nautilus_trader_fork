import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CandleItem } from "../api/schema";

const fetchCandlesMock = vi.hoisted(() => vi.fn());
vi.mock("../api/client", () => ({ fetchCandles: fetchCandlesMock }));

const { useSessionCandles, SESSION_REQUEST_DEBOUNCE_MS } = await import("./useSessionCandles");

const NOW = Date.parse("2024-01-03T12:00:00Z") / 1000;
const DAY = 86_400;
const item = (tSec: number, v = 1): CandleItem => ({ t: tSec * 1000, o: 1, h: 2, l: 1, c: 2, v });
// Lets every pending promise chain (a whole paging run of mocked fetches) settle.
const flush = async (rounds = 5) => {
  for (let i = 0; i < rounds; i++) await act(async () => { await Promise.resolve(); await Promise.resolve(); });
};
const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
/** The `before` cursor (seconds) of every fetch so far. */
const cursors = (): number[] => fetchCandlesMock.mock.calls.map((c) => c[1] / 1e9);

/** A fake `/api/candles`: one bar every `bar` seconds from `oldest` up to the (fake) clock's now,
 * served newest page first; `has_more` is false once a page reaches `oldest`, like the server. */
function serveHistory(bar: number, oldest: number): void {
  fetchCandlesMock.mockImplementation(async (_iid: string, beforeNs: number, limit: number) => {
    const before = beforeNs / 1e9;
    const newest = Math.min(Math.floor(Date.now() / 1000 / bar) * bar, Math.ceil(before / bar) * bar - bar);
    const first = Math.max(oldest, newest - (limit - 1) * bar);
    const items: CandleItem[] = [];
    for (let t = first; t <= newest; t += bar) items.push(item(t));
    return { items, has_more: first > oldest };
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW * 1000);
  fetchCandlesMock.mockReset();
});
afterEach(() => vi.useRealTimers());

describe("useSessionCandles (Story 18.8)", () => {
  it("is idle while disabled", async () => {
    renderHook(() => useSessionCandles("BTC", false, 0, 60));
    await flush();

    expect(fetchCandlesMock).not.toHaveBeenCalled();
  });

  it("pages back at once until the wanted start is covered, then stops (fully covered)", async () => {
    const since = 1_000_000;
    fetchCandlesMock
      .mockResolvedValueOnce({ items: [item(1_000_600), item(1_000_660)], has_more: true })
      .mockResolvedValueOnce({ items: [item(999_900), item(1_000_000)], has_more: true });
    const { result } = renderHook(() => useSessionCandles("BTC", true, since, 60));
    await flush();

    expect(fetchCandlesMock).toHaveBeenCalledTimes(2);
    expect(fetchCandlesMock.mock.calls[1][1]).toBe(1_000_600 * 1000 * 1_000_000); // cursor = earliest bar so far
    // The bar before the wanted start was fetched with the page but is not kept.
    expect(result.current.candles.map((c) => c.time)).toEqual([1_000_000, 1_000_600, 1_000_660]);
    expect(result.current.completeFrom).toBeNull();
  });

  it("flags the history as only partially covered when it stops short of the wanted start", async () => {
    fetchCandlesMock.mockResolvedValue({ items: [item(5_000)], has_more: true });
    const { result } = renderHook(() => useSessionCandles("BTC", true, 10, 60));
    await flush(100);

    // The second page reaches no older than its cursor: the run stops instead of re-requesting it.
    expect(fetchCandlesMock).toHaveBeenCalledTimes(2);
    expect(result.current.completeFrom).toBe(5_000);
  });

  it("sizes the page budget to the wanted span, so 10 weekly periods at 5m bars are reachable", async () => {
    // One bar per page, each a page further back: never reaches `since` within the budget.
    fetchCandlesMock.mockImplementation(async (_iid: string, beforeNs: number) => ({
      items: [item(beforeNs / 1e9 - 500 * 300)],
      has_more: true,
    }));
    renderHook(() => useSessionCandles("BTC", true, NOW - 10 * 7 * DAY, 300));
    await flush(120);

    // ~20160 bars / 500 per page = 41 pages, +2 slack -- more than the old fixed 40.
    expect(fetchCandlesMock.mock.calls.length).toBeGreaterThan(40);
    expect(fetchCandlesMock.mock.calls.length).toBeLessThanOrEqual(80);
  });

  it("refreshes at most once a minute even for coarse bars", async () => {
    fetchCandlesMock.mockResolvedValue({ items: [item(NOW)], has_more: false });
    renderHook(() => useSessionCandles("BTC", true, NOW, 900));
    await flush();
    fetchCandlesMock.mockClear();

    await advance(60_000);

    expect(fetchCandlesMock).toHaveBeenCalledTimes(1);
  });

  it("refreshes only the newest bars each interval, replacing the forming bar in place", async () => {
    fetchCandlesMock.mockResolvedValueOnce({ items: [item(NOW - 60, 1)], has_more: false });
    const { result } = renderHook(() => useSessionCandles("BTC", true, NOW - 60, 60));
    await flush();

    fetchCandlesMock.mockResolvedValueOnce({ items: [item(NOW - 60, 9), item(NOW, 2)], has_more: false });
    await advance(60_000);
    await flush();

    expect(fetchCandlesMock.mock.calls[1][2]).toBe(5);
    expect(result.current.volume.map((v) => "value" in v && v.value)).toEqual([9, 2]);
  });

  it("widens the refresh to bridge a long starved interval", async () => {
    fetchCandlesMock.mockResolvedValueOnce({ items: [item(NOW - 60)], has_more: false });
    renderHook(() => useSessionCandles("BTC", true, NOW - 3600, 60));
    await flush();

    vi.setSystemTime((NOW + 3600) * 1000); // the tab was throttled: no refresh ran for an hour
    fetchCandlesMock.mockResolvedValueOnce({ items: [], has_more: false });
    await advance(60_000);
    await flush();

    expect(fetchCandlesMock.mock.calls[1][2]).toBeGreaterThanOrEqual(60);
  });

  it("keeps refreshing the tail of a quiet market without re-paging, however old its newest bar", async () => {
    fetchCandlesMock.mockResolvedValueOnce({ items: [item(NOW - 10 * 3600)], has_more: false }); // no trade for 10 h
    const { result } = renderHook(() => useSessionCandles("BTC", true, NOW - DAY, 60));
    await flush();

    fetchCandlesMock.mockResolvedValue({ items: [], has_more: false });
    await advance(60_000);
    await flush();
    await advance(60_000);
    await flush();

    // Two on-time refreshes of the small tail, never a reset back to a paging run from now.
    expect(fetchCandlesMock.mock.calls.map((c) => c[2])).toEqual([500, 5, 5]);
    expect(result.current.candles).toHaveLength(1);
  });

  it("aborts an in-flight paging run before the starved refresh clears the bars", async () => {
    serveHistory(60, NOW - 3 * 3600);
    const { rerender } = renderHook(({ since }) => useSessionCandles("BTC", true, since, 60), {
      initialProps: { since: NOW - 3600 },
    });
    await flush();
    let signal: AbortSignal | undefined;
    fetchCandlesMock.mockImplementationOnce((_i: string, _b: number, _l: number, _s: number, s: AbortSignal) => {
      signal = s;
      return new Promise(() => {}); // never lands
    });
    rerender({ since: NOW - 2 * 3600 }); // more sessions: a debounced page back
    await advance(SESSION_REQUEST_DEBOUNCE_MS);
    expect(signal?.aborted).toBe(false);

    vi.setSystemTime((NOW + 10 * 3600) * 1000);
    await advance(60_000 - SESSION_REQUEST_DEBOUNCE_MS);

    expect(signal?.aborted).toBe(true);
  });

  it("pages again from now when the refresh was starved past one page (a suspended tab)", async () => {
    serveHistory(60, NOW - 3600);
    const { result } = renderHook(() => useSessionCandles("BTC", true, NOW - 3600, 60));
    await flush();
    expect(result.current.candles).toHaveLength(60); // `before` is exclusive: NOW - 3600 .. NOW - 60

    vi.setSystemTime((NOW + 10 * 3600) * 1000); // ten hours of 1m bars missed: more than one page
    fetchCandlesMock.mockClear();
    await advance(60_000);
    await flush(20);

    // A paging run from now back to the wanted start (two pages), not one capped refresh that
    // would have left a hole between the old bars and the newest page.
    expect(fetchCandlesMock.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(cursors()[1]).toBeLessThan(cursors()[0]);
    expect(result.current.candles).toHaveLength(661); // NOW - 3600 .. NOW + 10 h (the clock moved on a minute), no hole
  });

  it("drops a refresh that lands after a starvation reset: the tail alone is not covered history", async () => {
    serveHistory(60, NOW - 3600);
    const { result } = renderHook(() => useSessionCandles("BTC", true, NOW - 3600, 60));
    await flush();
    let land: (v: { items: CandleItem[]; has_more: boolean }) => void = () => {};
    fetchCandlesMock.mockImplementationOnce(() => new Promise((resolve) => (land = resolve)));
    await advance(60_000); // a refresh, left in flight

    vi.setSystemTime((NOW + 10 * 3600) * 1000);
    fetchCandlesMock.mockImplementation(() => new Promise(() => {})); // the new paging run never lands
    await advance(60_000); // starved past one page: reset
    await act(async () => land({ items: [item(NOW + 60)], has_more: true }));

    expect(result.current.candles).toEqual([]);
  });

  it("derives coverage from the current wanted start, so asking for more sessions is not covered until fetched", async () => {
    fetchCandlesMock.mockResolvedValue({ items: [item(1_000_000)], has_more: true });
    const { result, rerender } = renderHook(({ since }) => useSessionCandles("BTC", true, since, 60), {
      initialProps: { since: 1_000_000 },
    });
    await flush();
    expect(result.current.completeFrom).toBeNull();

    rerender({ since: 900_000 });

    expect(result.current.completeFrom).toBe(1_000_000);
  });

  it("retries a failed load after the refresh interval, and discards items of another bar size", async () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    fetchCandlesMock.mockRejectedValueOnce(new Error("boom"));
    const { result, rerender } = renderHook(({ bar }) => useSessionCandles("BTC", true, 1_000_000, bar), {
      initialProps: { bar: 60 },
    });
    await flush();
    expect(errors).toHaveBeenCalledTimes(1);
    expect(result.current.loading).toBe(false);

    fetchCandlesMock.mockResolvedValue({ items: [item(1_000_000, 3)], has_more: false });
    await advance(60_000);
    await flush();
    expect(result.current.candles).toHaveLength(1);

    fetchCandlesMock.mockResolvedValue({ items: [item(1_000_300, 4)], has_more: false });
    rerender({ bar: 300 });
    await flush();

    expect(result.current.candles.map((c) => c.time)).toEqual([1_000_300]);
    errors.mockRestore();
  });

  it("stops fetching after unmount", async () => {
    fetchCandlesMock.mockResolvedValue({ items: [item(1_000_000)], has_more: false });
    const { unmount } = renderHook(() => useSessionCandles("BTC", true, 1_000_000, 60));
    await flush();
    unmount();
    fetchCandlesMock.mockClear();

    await advance(300_000);

    expect(fetchCandlesMock).not.toHaveBeenCalled();
  });
});

describe("useSessionCandles incremental paging (DW-152/153)", () => {
  const render = (since: number, bar = 60) =>
    renderHook(({ s, b }) => useSessionCandles("BTC", true, s, b), { initialProps: { s: since, b: bar } });

  it("count increase: after the debounce, pages only from the oldest loaded bar back", async () => {
    serveHistory(60, NOW - 30 * DAY);
    const { result, rerender } = render(NOW - 2 * DAY);
    await flush(20);
    const oldest = result.current.candles[0].time as number;
    expect(oldest).toBe(NOW - 2 * DAY);
    fetchCandlesMock.mockClear();

    rerender({ s: NOW - 3 * DAY, b: 60 });
    await flush();
    expect(fetchCandlesMock).not.toHaveBeenCalled(); // still inside the debounce

    await advance(SESSION_REQUEST_DEBOUNCE_MS);
    await flush(20);

    expect(cursors()[0]).toBe(oldest);
    expect(Math.min(...cursors())).toBeGreaterThan(NOW - 3 * DAY);
    expect(result.current.candles[0].time).toBe(NOW - 3 * DAY);
    expect(result.current.completeFrom).toBeNull();
  });

  it("a burst of edits inside the debounce is one request", async () => {
    serveHistory(60, NOW - 30 * DAY);
    const { rerender } = render(NOW - DAY);
    await flush(20);
    fetchCandlesMock.mockClear();

    for (const days of [2, 3, 4]) {
      rerender({ s: NOW - days * DAY, b: 60 });
      await advance(SESSION_REQUEST_DEBOUNCE_MS / 3);
    }
    await advance(SESSION_REQUEST_DEBOUNCE_MS);
    await flush(30);

    // Paged once, straight to the last wanted start: every cursor is one run's.
    const run = cursors();
    expect(run.every((c, i) => i === 0 || c < run[i - 1])).toBe(true);
    expect(Math.min(...run)).toBeGreaterThan(NOW - 4 * DAY);
  });

  it("aborts the in-flight page when another edit arrives", async () => {
    serveHistory(60, NOW - 30 * DAY);
    const { rerender } = render(NOW - DAY);
    await flush(20);
    let release: () => void = () => {};
    fetchCandlesMock.mockImplementationOnce(
      (_i: string, _b: number, _l: number, _s: number, signal: AbortSignal) =>
        new Promise((resolve) => {
          release = () => resolve({ items: [], has_more: false });
          signal.addEventListener("abort", () => resolve({ items: [], has_more: false }));
        }),
    );
    rerender({ s: NOW - 5 * DAY, b: 60 });
    await advance(SESSION_REQUEST_DEBOUNCE_MS);
    const signal = fetchCandlesMock.mock.calls.at(-1)![4] as AbortSignal;
    expect(signal.aborted).toBe(false);

    rerender({ s: NOW - 6 * DAY, b: 60 });

    expect(signal.aborted).toBe(true);
    release();
  });

  it("count decrease: no fetch, the older items are pruned", async () => {
    serveHistory(60, NOW - 30 * DAY);
    const { result, rerender } = render(NOW - 3 * DAY);
    await flush(30);
    fetchCandlesMock.mockClear();

    rerender({ s: NOW - DAY, b: 60 });
    await advance(SESSION_REQUEST_DEBOUNCE_MS);
    await flush();

    expect(fetchCandlesMock).not.toHaveBeenCalled();
    expect(result.current.candles[0].time).toBe(NOW - DAY);
    expect(result.current.completeFrom).toBeNull();
  });

  it("rollover: a later wanted start prunes without re-paging, and the refresh keeps going", async () => {
    serveHistory(60, NOW - 30 * DAY);
    const { result, rerender } = render(NOW - 2 * DAY);
    await flush(20);
    fetchCandlesMock.mockClear();

    rerender({ s: NOW - DAY, b: 60 }); // the period rolled over: the wanted start moves a day later
    await advance(SESSION_REQUEST_DEBOUNCE_MS);
    await flush();
    expect(fetchCandlesMock).not.toHaveBeenCalled();
    expect(result.current.candles[0].time).toBe(NOW - DAY);

    await advance(60_000);
    expect(fetchCandlesMock).toHaveBeenCalledTimes(1); // the refresh, not a re-page
    expect(fetchCandlesMock.mock.calls[0][2]).toBeLessThan(500);
  });

  it("reports loading while a run pages, and not once it is done", async () => {
    let release: (v: unknown) => void = () => {};
    fetchCandlesMock.mockImplementationOnce(() => new Promise((resolve) => (release = resolve)));
    const { result } = render(NOW - DAY);
    await flush();
    expect(result.current.loading).toBe(true);

    await act(async () => release({ items: [item(NOW - DAY)], has_more: false }));
    await flush();

    expect(result.current.loading).toBe(false);
  });

  it("holds at most HARD_MAX_PAGES x PAGE_LIMIT bars, flagging the rest as not covered (a far replay)", async () => {
    serveHistory(60, NOW - 60 * DAY);
    const { result } = render(NOW - 40 * DAY); // 57 600 1m bars wanted
    await flush(400);

    expect(result.current.candles.length).toBeLessThanOrEqual(40_000);
    expect(result.current.completeFrom).not.toBeNull();
    expect(result.current.completeFrom!).toBeGreaterThan(NOW - 40 * DAY);
  });
});
