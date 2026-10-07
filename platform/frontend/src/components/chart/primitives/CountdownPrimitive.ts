import type { ISeriesApi, ISeriesPrimitive, ISeriesPrimitiveAxisView, SeriesAttachedParameter, SeriesType, Time } from "lightweight-charts";

import { chartVar } from "../chartTheme";

/** What the countdown label shows: its text at the last price, in the last-price label's colour. */
export interface CountdownLabel {
  price: number;
  text: string;
  color: string;
}

// The library's price-axis label is about this tall at the chart's 12 px font: the countdown asks to sit
// one label below the last price, and the library's own overlap avoidance keeps the two apart.
const LABEL_OFFSET_PX = 18;

/**
 * Story 33.12: the bar countdown -- a price-axis label right under the main series' last-price label,
 * the time left to the forming bar's close (`lib/time.ts`'s `barCountdown`, by the viewer's clock,
 * audit D-219). It draws only on the axis; the chart hands it a fresh label every second, or null to
 * hide it (Lines mode, a replay, no bars, or the setting off).
 */
export class CountdownPrimitive implements ISeriesPrimitive<Time> {
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private requestUpdate: (() => void) | null = null;
  private label: CountdownLabel | null = null;
  private y: number | null = null;
  private readonly view: ISeriesPrimitiveAxisView = {
    coordinate: (): number => (this.y ?? 0) + LABEL_OFFSET_PX,
    text: (): string => this.label?.text ?? "",
    textColor: (): string => chartVar("--chart-bg"),
    backColor: (): string => this.label?.color ?? chartVar("--chart-text-dim"),
    visible: (): boolean => this.label !== null && this.y !== null,
    tickVisible: (): boolean => false,
  };
  private readonly views: readonly ISeriesPrimitiveAxisView[] = [this.view];

  attached(param: SeriesAttachedParameter<Time>): void {
    this.series = param.series;
    this.requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this.series = null;
    this.requestUpdate = null;
    this.y = null;
  }

  setLabel(label: CountdownLabel | null): void {
    const same =
      label === this.label ||
      (label !== null && this.label !== null && label.price === this.label.price && label.text === this.label.text && label.color === this.label.color);
    if (same) return;
    this.label = label;
    this.requestUpdate?.();
  }

  updateAllViews(): void {
    this.y = this.label && this.series ? this.series.priceToCoordinate(this.label.price) : null;
  }

  priceAxisViews(): readonly ISeriesPrimitiveAxisView[] {
    return this.views;
  }

  /** The label as the axis shows it now (null while hidden), for the tests. */
  shown(): { text: string; y: number } | null {
    return this.view.visible?.() ? { text: this.view.text(), y: this.view.coordinate() } : null;
  }
}
