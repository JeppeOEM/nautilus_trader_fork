import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { IndicatorConfigEntry } from "../api/schema";
import type { Drawing } from "../lib/drawings";

// The values hook is the one under it (its paging is tested in its own file): this records the
// entries each render asks it for and answers with `values`, reporting `errors` when told to.
const valuesHook = vi.hoisted(() => ({
  entries: [] as IndicatorConfigEntry[][],
  values: {} as Record<string, unknown[]>,
  onErrors: undefined as ((errors: Record<string, string>) => void) | undefined,
}));
vi.mock("./usePickerIndicatorValues", () => ({
  usePickerIndicatorValues: (
    _iid: string,
    _chart: unknown,
    entries: IndicatorConfigEntry[],
    _bar: number,
    onErrors?: (errors: Record<string, string>) => void,
  ) => {
    valuesHook.entries.push(entries);
    valuesHook.onErrors = onErrors;
    return valuesHook.values;
  },
}));

const { useStoredAnchoredVwap, storedVwapEntry } = await import("./useStoredAnchoredVwap");

const IID = "BTCUSDT-LINEAR.BYBIT";
const avwap = (id: string, time: number, source: "hlc3" | "stored"): Drawing => ({
  kind: "anchored_vwap",
  id,
  time,
  source,
  bands: false,
  band_color: "#b26a00",
});

beforeEach(() => {
  valuesHook.entries = [];
  valuesHook.values = {};
  valuesHook.onErrors = undefined;
});

describe("useStoredAnchoredVwap (Story 33.6)", () => {
  it("asks for one AnchoredStoredVWAP entry per stored-source anchor, in epoch ms, and nothing else", () => {
    const drawings = [avwap("a", 1_700_000_000, "stored"), avwap("b", 1_700_000_000, "stored"), avwap("c", 1_600_000_000, "hlc3")];
    renderHook(() => useStoredAnchoredVwap(IID, null, drawings, 60));

    expect(valuesHook.entries.at(-1)).toEqual([{ name: "AnchoredStoredVWAP", category: "custom", params: { anchor_t: "1700000000000" } }]);
  });

  it("asks for nothing while no drawing uses the stored source", () => {
    renderHook(() => useStoredAnchoredVwap(IID, null, [avwap("c", 1_600_000_000, "hlc3")], 60));

    expect(valuesHook.entries.at(-1)).toEqual([]);
  });

  it("hands each drawing its entry's values and its entry's error", () => {
    valuesHook.values = { "AnchoredStoredVWAP_anchor_t=1700000000000.value": [{ time: 1_700_000_000, value: 5 }] };
    const drawings = [avwap("a", 1_700_000_000, "stored"), avwap("b", 1_500_000_000, "stored")];
    const { result } = renderHook(() => useStoredAnchoredVwap(IID, null, drawings, 60));

    act(() => valuesHook.onErrors?.({ "AnchoredStoredVWAP_anchor_t=1500000000000": "anchor before the store's first bar" }));

    expect(result.current.values).toEqual({ a: [{ time: 1_700_000_000, value: 5 }] });
    expect(result.current.errors).toEqual({ b: "anchor before the store's first bar" });
  });

  it("keeps a page's error when a later page succeeds, and drops it when the anchors change", () => {
    const key = "AnchoredStoredVWAP_anchor_t=1500000000000";
    const { result, rerender } = renderHook(({ ds }) => useStoredAnchoredVwap(IID, null, ds, 60), {
      initialProps: { ds: [avwap("b", 1_500_000_000, "stored")] },
    });

    act(() => valuesHook.onErrors?.({ [key]: "no exact stored sum from the anchor" }));
    act(() => valuesHook.onErrors?.({}));
    expect(result.current.errors).toEqual({ b: "no exact stored sum from the anchor" });

    rerender({ ds: [avwap("b", 1_600_000_000, "stored")] });
    expect(result.current.errors).toEqual({});
  });

  it("asks for nothing while disabled (Lines mode)", () => {
    renderHook(() => useStoredAnchoredVwap(IID, null, [avwap("a", 1_700_000_000, "stored")], 60, false));

    expect(valuesHook.entries.at(-1)).toEqual([]);
  });

  it("builds the entry the backend's values route replays", () => {
    expect(storedVwapEntry(60)).toEqual({ name: "AnchoredStoredVWAP", category: "custom", params: { anchor_t: "60000" } });
  });
});
