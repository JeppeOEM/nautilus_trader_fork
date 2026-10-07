import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useWatchlist, WATCHLIST_RETRY_MS } from "./useWatchlist";

const api = vi.hoisted(() => ({ fetch: vi.fn(), save: vi.fn() }));
vi.mock("../api/client", () => ({
  fetchWatchlist: (...args: unknown[]) => api.fetch(...args),
  saveWatchlist: (...args: unknown[]) => api.save(...args),
}));

/** A promise the test settles by hand. */
function deferred<T>(): { promise: Promise<T>; resolve: (v: T) => void; reject: (e: unknown) => void } {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const flush = () => act(async () => {});

let errors: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  api.fetch.mockReset();
  api.save.mockReset();
  errors = vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  errors.mockRestore();
  vi.useRealTimers();
});

describe("useWatchlist (Story 33.12)", () => {
  it("sends each PUT only after the previous one answered, so the server ends on the last edit", async () => {
    api.fetch.mockResolvedValue({ instruments: [] });
    const first = deferred<{ instruments: string[] }>();
    const second = deferred<{ instruments: string[] }>();
    api.save.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const { result } = renderHook(() => useWatchlist());
    await flush();

    act(() => result.current.pin("A.BYBIT"));
    act(() => result.current.pin("B.BYBIT"));
    await flush();
    expect(api.save).toHaveBeenCalledTimes(1); // the second waits for the first
    expect(api.save).toHaveBeenLastCalledWith(["A.BYBIT"]);

    await act(async () => first.resolve({ instruments: ["A.BYBIT"] }));
    expect(api.save).toHaveBeenCalledTimes(2);
    expect(api.save).toHaveBeenLastCalledWith(["A.BYBIT", "B.BYBIT"]);
    expect(result.current.instruments).toEqual(["A.BYBIT", "B.BYBIT"]); // the stale answer is not shown

    await act(async () => second.resolve({ instruments: ["A.BYBIT", "B.BYBIT"] }));
    expect(result.current.instruments).toEqual(["A.BYBIT", "B.BYBIT"]);
  });

  it("does not reload over a queued later save when an earlier PUT fails", async () => {
    api.fetch.mockResolvedValue({ instruments: [] });
    const first = deferred<{ instruments: string[] }>();
    api.save.mockReturnValueOnce(first.promise).mockResolvedValueOnce({ instruments: ["A.BYBIT", "B.BYBIT"] });
    const { result } = renderHook(() => useWatchlist());
    await flush();
    expect(api.fetch).toHaveBeenCalledTimes(1);

    act(() => result.current.pin("A.BYBIT"));
    act(() => result.current.pin("B.BYBIT"));
    await act(async () => first.reject(new Error("PUT /api/watchlist failed: 503")));

    expect(api.fetch).toHaveBeenCalledTimes(1); // no reload
    expect(result.current.instruments).toEqual(["A.BYBIT", "B.BYBIT"]);
    expect(result.current.error).toBeNull();
  });

  it("reloads the server's list and says so when the newest PUT fails", async () => {
    api.fetch.mockResolvedValue({ instruments: [] });
    api.save.mockRejectedValue(new Error("PUT /api/watchlist failed: 422"));
    const { result } = renderHook(() => useWatchlist());
    await flush();

    act(() => result.current.pin("A.BYBIT"));
    await flush();

    expect(api.fetch).toHaveBeenCalledTimes(2);
    expect(result.current.instruments).toEqual([]);
    expect(result.current.error).toBe("Watchlist could not be saved: PUT /api/watchlist failed: 422");
  });

  it("retries a failed first GET with a growing, capped backoff and shows the error meanwhile", async () => {
    vi.useFakeTimers();
    api.fetch.mockRejectedValue(new Error("GET /api/watchlist failed: 500"));
    const { result, unmount } = renderHook(() => useWatchlist());
    await flush();
    expect(result.current.loaded).toBe(false);
    expect(result.current.loadFailed).toBe(true);
    expect(result.current.error).toBe("Watchlist could not be loaded: GET /api/watchlist failed: 500 (retrying in 2 s)");

    for (const [i, wait] of WATCHLIST_RETRY_MS.entries()) {
      await act(async () => vi.advanceTimersByTime(wait - 1));
      expect(api.fetch).toHaveBeenCalledTimes(i + 1);
      await act(async () => vi.advanceTimersByTime(1));
      expect(api.fetch).toHaveBeenCalledTimes(i + 2);
    }
    const capped = WATCHLIST_RETRY_MS[WATCHLIST_RETRY_MS.length - 1];
    await act(async () => vi.advanceTimersByTime(capped)); // the cap repeats
    expect(api.fetch).toHaveBeenCalledTimes(WATCHLIST_RETRY_MS.length + 2);

    api.fetch.mockResolvedValue({ instruments: ["A.BYBIT"] });
    await act(async () => vi.advanceTimersByTime(capped));
    expect(result.current.loaded).toBe(true);
    expect(result.current.loadFailed).toBe(false);
    expect(result.current.error).toBeNull();
    expect(result.current.instruments).toEqual(["A.BYBIT"]);

    unmount();
  });

  it("stops retrying once unmounted", async () => {
    vi.useFakeTimers();
    api.fetch.mockRejectedValue(new Error("down"));
    const { unmount } = renderHook(() => useWatchlist());
    await flush();
    unmount();
    await act(async () => vi.advanceTimersByTime(60_000));
    expect(api.fetch).toHaveBeenCalledTimes(1);
  });
  it("shows the stored list at once when a PUT fails, so an edit before the reload lands never stores the failed pin", async () => {
    api.fetch.mockResolvedValueOnce({ instruments: ["S.BYBIT"] });
    const reloading = deferred<{ instruments: string[] }>();
    api.fetch.mockReturnValueOnce(reloading.promise);
    api.save.mockRejectedValueOnce(new Error("PUT /api/watchlist failed: 503")).mockResolvedValueOnce({ instruments: ["S.BYBIT", "B.BYBIT"] });
    const { result } = renderHook(() => useWatchlist());
    await flush();

    act(() => result.current.pin("A.BYBIT"));
    await flush();
    expect(result.current.instruments).toEqual(["S.BYBIT"]); // the failed pin is gone before the reload answers

    act(() => result.current.pin("B.BYBIT"));
    await flush();
    expect(api.save).toHaveBeenLastCalledWith(["S.BYBIT", "B.BYBIT"]);
    await act(async () => reloading.resolve({ instruments: ["S.BYBIT"] })); // superseded by the edit
    expect(result.current.instruments).toEqual(["S.BYBIT", "B.BYBIT"]);
  });

  it("cancels a pending GET retry on an edit, so the retry never paints an older list over the pin", async () => {
    vi.useFakeTimers();
    api.fetch.mockResolvedValueOnce({ instruments: [] }).mockRejectedValueOnce(new Error("GET /api/watchlist failed: 500"));
    api.save.mockRejectedValueOnce(new Error("PUT /api/watchlist failed: 503")).mockResolvedValueOnce({ instruments: ["X.BYBIT"] });
    const { result, unmount } = renderHook(() => useWatchlist());
    await flush();

    act(() => result.current.pin("A.BYBIT")); // fails, and its reload fails too: a retry is scheduled
    await flush();
    expect(api.fetch).toHaveBeenCalledTimes(2);
    expect(result.current.loadFailed).toBe(true);

    act(() => result.current.pin("X.BYBIT"));
    await flush();
    expect(result.current.instruments).toEqual(["X.BYBIT"]);
    expect(result.current.error).toBeNull(); // the PUT's answer is the server's list

    await act(async () => vi.advanceTimersByTime(60_000));
    expect(api.fetch).toHaveBeenCalledTimes(2); // the retry was cancelled
    expect(result.current.instruments).toEqual(["X.BYBIT"]);
    unmount();
  });
});
