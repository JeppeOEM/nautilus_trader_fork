import type { IChartApi } from "lightweight-charts";
import { useEffect, useRef, useState } from "react";

import type { PriceScaleModeName } from "../../lib/chartTypes";
import type { PriceScalePatch } from "./LightweightChart";

// Chart UX rework (2026-10-08): the price scale's quick switches and the view's two jumps, on the chart
// itself (TradingView's corner) instead of in the top bar. Bottom right, beside the right price scale
// and above the time axis; "Latest" shows only while the newest bar is scrolled out of view.

/** Bars scrolled away from the newest one before "Latest" is offered (a nudge is not "away"). */
const AWAY_BARS = 3;

interface Props {
  chart: IChartApi | null;
  /** The operator's scale mode as drawn (Percent while a compare is drawn). */
  mode: PriceScaleModeName;
  autoScale: boolean;
  /** Why Normal and Log are locked (a compare draws on the percent scale), else null. */
  lockReason: string | null;
  onScale: (patch: PriceScalePatch) => void;
}

export default function ChartCornerControls({ chart, mode, autoScale, lockReason, onScale }: Props) {
  const [away, setAway] = useState(false);
  const [inset, setInset] = useState({ right: 60, bottom: 30 });
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!chart) return;
    const timeScale = chart.timeScale();
    const sync = (): void => {
      setAway(timeScale.scrollPosition() < -AWAY_BARS);
      // Measured from the box this group is positioned in to the chart's own element: the box's
      // padding and anything beside the chart are cleared whatever the page's font size.
      const box = rootRef.current?.offsetParent?.getBoundingClientRect();
      const plot = chart.chartElement().getBoundingClientRect();
      const right = box ? box.right - plot.right : 0;
      const bottom = box ? box.bottom - plot.bottom : 0;
      setInset({ right: right + chart.priceScale("right").width() + 6, bottom: bottom + timeScale.height() + 6 });
    };
    sync();
    timeScale.subscribeVisibleLogicalRangeChange(sync);
    timeScale.subscribeSizeChange(sync);
    return () => {
      timeScale.unsubscribeVisibleLogicalRangeChange(sync);
      timeScale.unsubscribeSizeChange(sync);
    };
  }, [chart]);

  const toggleMode = (target: PriceScaleModeName): void => onScale({ mode: mode === target ? "normal" : target });
  return (
    <div ref={rootRef} className="chart-corner" role="group" aria-label="Price scale and view" style={{ right: inset.right, bottom: inset.bottom }}>
      {away && (
        <button type="button" className="chart-chip" title="Scroll to the newest bar" onClick={() => chart?.timeScale().scrollToRealTime()}>
          » Latest
        </button>
      )}
      <button type="button" className="chart-chip" title="Fit every loaded bar on screen" onClick={() => chart?.timeScale().fitContent()}>
        Fit
      </button>
      <button
        type="button"
        className={mode === "percent" ? "chart-chip active" : "chart-chip"}
        aria-pressed={mode === "percent"}
        aria-label="Percent scale"
        disabled={lockReason !== null}
        title={lockReason ?? "Percent scale: each value against the first visible one"}
        onClick={() => toggleMode("percent")}
      >
        %
      </button>
      <button
        type="button"
        className={mode === "log" ? "chart-chip active" : "chart-chip"}
        aria-pressed={mode === "log"}
        aria-label="Log scale"
        disabled={lockReason !== null}
        title={lockReason ?? "Logarithmic scale (Shift+L)"}
        onClick={() => toggleMode("log")}
      >
        log
      </button>
      <button
        type="button"
        className={autoScale ? "chart-chip active" : "chart-chip"}
        aria-pressed={autoScale}
        aria-label="Auto scale"
        title="Fit the price axis to the visible bars (a drag of the scale turns it off)"
        onClick={() => onScale({ auto_scale: !autoScale })}
      >
        auto
      </button>
    </div>
  );
}
