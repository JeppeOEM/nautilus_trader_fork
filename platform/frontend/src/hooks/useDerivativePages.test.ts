import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { FundingResponse, OpenInterestResponse } from "../api/schema";

const fetchOpenInterestMock = vi.fn<(...args: unknown[]) => Promise<OpenInterestResponse>>();
const fetchFundingMock = vi.fn<(...args: unknown[]) => Promise<FundingResponse>>();

vi.mock("../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/client")>()),
  fetchOpenInterest: (...args: unknown[]) => fetchOpenInterestMock(...args),
  fetchFunding: (...args: unknown[]) => fetchFundingMock(...args),
}));

const { useDerivativePages, DERIVATIVE_PAGE_LIMIT, NEWEST_POLL_MS, ARCHIVE_LAG_MS } = await import("./useDerivativePages");
const { HttpError } = await import("../api/client");

const IID = "BTCUSDT-LINEAR.BYBIT";
const NS = 1_000_000_000;

function oiPage(times: number[], hasMore: boolean): OpenInterestResponse {
  return { items: times.map((t) => ({ t, oi: String(t), oi_change: null })), has_more: hasMore, venue: "BYBIT", market: "perp" };
}

function fundingPage(times: number[], hasMore: boolean): FundingResponse {
  return {
    items: times.map((t) => ({ t, rate: "0.0001", interval: 28_800, next_funding_ns: null, annualised: 0.1095 })),
    has_more: hasMore,
    venue: "BYBIT",
    market: "perp",
  };
}

const flush = () => act(async () => {});

beforeEach(() => {
  fetchOpenInterestMock.mockReset();
  fetchFundingMock.mockReset();
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("useDerivativePages", () => {
  it("issues no request while off, then loads the newest page of the bucketed route", async () => {
    fetchOpenInterestMock.mockResolvedValue(oiPage([600_000, 660_000], false));
    const { result, rerender } = renderHook(({ on }) => useDerivativePages("open-interest", IID, 60, on, null, 600), {
      initialProps: { on: false },
    });
    await flush();
    expect(fetchOpenInterestMock).not.toHaveBeenCalled();

    rerender({ on: true });
    await flush();

    expect(fetchOpenInterestMock).toHaveBeenCalledTimes(1);
    const [iid, , limit, bar] = fetchOpenInterestMock.mock.calls[0];
    expect([iid, limit, bar]).toEqual([IID, DERIVATIVE_PAGE_LIMIT, 60]);
    expect(result.current.rows.map((r) => r.t)).toEqual([600_000, 660_000]);
    expect(result.current.loaded).toBe(true);
  });

  it("pages back while its earliest row is later than the earliest candle, with a gap seam between pages", async () => {
    fetchOpenInterestMock
      .mockResolvedValueOnce(oiPage([900_000, 960_000], true))
      .mockResolvedValueOnce(oiPage([600_000, 660_000], true));
    const { result, rerender } = renderHook(({ candle }) => useDerivativePages("open-interest", IID, 60, true, null, candle), {
      initialProps: { candle: 900 },
    });
    await flush();
    expect(fetchOpenInterestMock).toHaveBeenCalledTimes(1); // its earliest (900) is the candles' own

    rerender({ candle: 600 }); // the candles paged back
    await flush();

    expect(fetchOpenInterestMock).toHaveBeenCalledTimes(2);
    expect(fetchOpenInterestMock.mock.calls[1][1]).toBe(900_000 * 1_000_000); // the cursor: the earliest row, ns
    expect(result.current.rows).toEqual([
      { t: 600_000, oi: "600000", oi_change: null },
      { t: 660_000, oi: "660000", oi_change: null },
      { t: 720_000 },
      { t: 780_000 },
      { t: 840_000 },
      { t: 900_000, oi: "900000", oi_change: null },
      { t: 960_000, oi: "960000", oi_change: null },
    ]);
    await flush();
    expect(fetchOpenInterestMock).toHaveBeenCalledTimes(2); // now level with the candles: no more
  });

  it("re-reads the newest page on request and merges it by t, the route winning", async () => {
    fetchOpenInterestMock.mockResolvedValueOnce(oiPage([600_000, 660_000], false)).mockResolvedValueOnce({
      items: [{ t: 660_000, oi: "7", oi_change: "1" }, { t: 720_000, oi: "8", oi_change: "1" }],
      has_more: true,
      venue: "BYBIT",
      market: "perp",
    });
    const { result } = renderHook(() => useDerivativePages("open-interest", IID, 60, true, null, 600));
    await flush();

    act(() => result.current.refreshNewest());
    await flush();

    expect(result.current.rows.map((r) => [r.t, r.oi])).toEqual([
      [600_000, "600000"],
      [660_000, "7"],
      [720_000, "8"],
    ]);
  });

  it("deduplicates funding events by t across pages (audit D-170)", async () => {
    fetchFundingMock.mockResolvedValueOnce(fundingPage([700 * NS, 800 * NS], true)).mockResolvedValueOnce(fundingPage([600 * NS, 700 * NS], false));
    const { result } = renderHook(() => useDerivativePages("funding", IID, 60, true, null, 600));
    await flush();
    await flush();

    expect(fetchFundingMock.mock.calls[1][1]).toBe(700 * NS);
    expect(result.current.rows.map((r) => r.t)).toEqual([600 * NS, 700 * NS, 800 * NS]);
  });

  it("retries a gateway failure with backoff and logs it once", async () => {
    vi.useFakeTimers();
    fetchOpenInterestMock
      .mockRejectedValueOnce(new HttpError(502, "bad gateway"))
      .mockRejectedValueOnce(new HttpError(503, "unavailable"))
      .mockResolvedValueOnce(oiPage([600_000], false));
    const { result } = renderHook(() => useDerivativePages("open-interest", IID, 60, true, null, 600));
    await act(async () => {});
    expect(result.current.error).toBe("load failed, retrying...");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    expect(fetchOpenInterestMock).toHaveBeenCalledTimes(3);
    expect(result.current.error).toBeNull();
    expect(result.current.rows).toHaveLength(1);
    expect(console.error).toHaveBeenCalledTimes(1);
  });

  it("treats any other status as final: no retry, the legend reads load failed", async () => {
    vi.useFakeTimers();
    fetchOpenInterestMock.mockRejectedValue(new HttpError(500, "archive read failed"));
    const { result } = renderHook(() => useDerivativePages("open-interest", IID, 60, true, null, 600));
    await act(async () => {});
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });

    expect(fetchOpenInterestMock).toHaveBeenCalledTimes(1);
    expect(result.current.error).toBe("load failed");
  });

  it("re-reads the newest page on a poll and once more after a bar close, when the archive holds the bar", async () => {
    vi.useFakeTimers();
    fetchOpenInterestMock.mockResolvedValue(oiPage([600_000], false));
    const { result } = renderHook(() => useDerivativePages("open-interest", IID, 60, true, null, 600));
    await act(async () => {});
    expect(fetchOpenInterestMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(NEWEST_POLL_MS);
    });
    expect(fetchOpenInterestMock).toHaveBeenCalledTimes(2);

    act(() => result.current.refreshAfterClose());
    await act(async () => {});
    expect(fetchOpenInterestMock).toHaveBeenCalledTimes(3); // at once
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ARCHIVE_LAG_MS);
    });
    // In those 75 s: the poll at 120 s and the one-shot at 60 + 75 = 135 s.
    expect(fetchOpenInterestMock).toHaveBeenCalledTimes(5);
  });

  it("keeps each bar close's archive re-read when the next close comes first (1m bars, 60 s < ARCHIVE_LAG_MS)", async () => {
    vi.useFakeTimers();
    fetchOpenInterestMock.mockResolvedValue(oiPage([600_000], false));
    const { result } = renderHook(() => useDerivativePages("open-interest", IID, 60, true, null, 600));
    await act(async () => {});
    const at = (): number => fetchOpenInterestMock.mock.calls.length;

    act(() => result.current.refreshAfterClose()); // close at 0 s: re-read now, again at 75 s
    await act(async () => {});
    await act(async () => {
      await vi.advanceTimersByTimeAsync(NEWEST_POLL_MS - 1); // 59.999 s: no poll yet
    });
    act(() => result.current.refreshAfterClose()); // close at ~60 s: re-read now, again at ~135 s
    await act(async () => {});
    const beforeLag = at();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ARCHIVE_LAG_MS - NEWEST_POLL_MS + 1); // to 75 s
    });
    // The poll at 60 s and the first close's one-shot at 75 s; the second close did not cancel it.
    expect(at() - beforeLag).toBe(2);
  });

  it("runs a newest re-read asked for while a page was in flight once that page settles", async () => {
    let release: (page: OpenInterestResponse) => void = () => {};
    fetchOpenInterestMock
      .mockReturnValueOnce(new Promise((resolve) => (release = resolve)))
      .mockResolvedValue(oiPage([600_000, 660_000], false));
    const { result } = renderHook(() => useDerivativePages("open-interest", IID, 60, true, null, 600));
    await flush();
    release(oiPage([600_000], false));
    await flush();
    fetchOpenInterestMock.mockReturnValueOnce(new Promise((resolve) => (release = resolve)));
    act(() => result.current.refreshNewest()); // in flight
    act(() => result.current.refreshNewest()); // busy: pending
    expect(fetchOpenInterestMock).toHaveBeenCalledTimes(2);
    release(oiPage([600_000], false));
    await flush();
    expect(fetchOpenInterestMock).toHaveBeenCalledTimes(3);
    expect(result.current.rows.map((r) => r.t)).toEqual([600_000, 660_000]);
  });

  it("keeps a loaded pane's rows on a failed re-read, retries it, and says load failed only with nothing held", async () => {
    vi.useFakeTimers();
    fetchOpenInterestMock
      .mockResolvedValueOnce(oiPage([600_000], false))
      .mockRejectedValueOnce(new HttpError(502, "bad gateway"))
      .mockResolvedValueOnce(oiPage([600_000, 660_000], false));
    const { result } = renderHook(() => useDerivativePages("open-interest", IID, 60, true, null, 600));
    await act(async () => {});
    act(() => result.current.refreshNewest());
    await act(async () => {});
    expect([result.current.error, result.current.rows.length]).toEqual([null, 1]);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(result.current.rows.map((r) => r.t)).toEqual([600_000, 660_000]);
  });

  it("re-reads the newest page when re-enabled with rows held", async () => {
    fetchOpenInterestMock.mockResolvedValue(oiPage([600_000], false));
    const { rerender } = renderHook(({ on }) => useDerivativePages("open-interest", IID, 60, on, null, 600), { initialProps: { on: true } });
    await flush();
    rerender({ on: false });
    rerender({ on: true });
    await flush();
    expect(fetchOpenInterestMock).toHaveBeenCalledTimes(2);
  });
});
