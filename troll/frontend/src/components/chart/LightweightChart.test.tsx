import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const addSeriesMock = vi.fn();
const applyOptionsMock = vi.fn();
const removeMock = vi.fn();
const setDataMock = vi.fn();
const createChartMock = vi.fn();

// Shallow mock of the whole module -- jsdom has no real <canvas> 2D context, so a real
// lightweight-charts render is not the right test boundary here; asserting call counts
// against a mock proves the single-instance invariant (AD-F4) without needing the
// `canvas` npm package (dependency-minimization preference, troll/CLAUDE.md).
vi.mock("lightweight-charts", () => ({
  CandlestickSeries: "CandlestickSeries-sentinel",
  createChart: (...args: unknown[]) => createChartMock(...args),
}));

const { default: LightweightChart } = await import("./LightweightChart");

beforeEach(() => {
  setDataMock.mockReset();
  addSeriesMock.mockReset().mockReturnValue({ setData: setDataMock });
  applyOptionsMock.mockReset();
  removeMock.mockReset();
  createChartMock.mockReset().mockImplementation(() => ({
    addSeries: addSeriesMock,
    applyOptions: applyOptionsMock,
    remove: removeMock,
  }));
});

afterEach(() => {
  cleanup();
});

describe("LightweightChart", () => {
  it("calls createChart exactly once per mount", () => {
    render(<LightweightChart data={[]} onChartApi={() => {}} />);

    expect(createChartMock).toHaveBeenCalledTimes(1);
    expect(addSeriesMock).toHaveBeenCalledTimes(1);
    expect(addSeriesMock).toHaveBeenCalledWith("CandlestickSeries-sentinel");
  });

  it("does not call createChart again on a data-only re-render", () => {
    const { rerender } = render(<LightweightChart data={[]} onChartApi={() => {}} />);

    rerender(<LightweightChart data={[{ time: 1700000000 as never }]} onChartApi={() => {}} />);

    expect(createChartMock).toHaveBeenCalledTimes(1);
    expect(setDataMock).toHaveBeenCalledTimes(2); // initial mount effect + the data-change effect
  });

  it("calls chart.remove() on unmount", () => {
    const { unmount } = render(<LightweightChart data={[]} onChartApi={() => {}} />);

    unmount();

    expect(removeMock).toHaveBeenCalledTimes(1);
  });

  it("notifies onChartApi with the chart on mount and null on unmount", () => {
    const onChartApi = vi.fn();
    const { unmount } = render(<LightweightChart data={[]} onChartApi={onChartApi} />);

    expect(onChartApi).toHaveBeenCalledTimes(1);
    expect(onChartApi.mock.calls[0][0]).not.toBeNull();

    unmount();

    expect(onChartApi).toHaveBeenCalledTimes(2);
    expect(onChartApi.mock.calls[1][0]).toBeNull();
  });
});
