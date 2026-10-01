import { chartPalette } from "./chartTheme";

// Story 15.9: final pane-color-slot assignment (Story 15.4 AC #7). Values come
// from the chart tokens declared in theme.css (Story 32.4) -- read at call time (not at
// module load) so it always reflects whatever the stylesheet currently has applied, and
// works whether or not the stylesheet has loaded yet (falls back to the same literal value
// baked into theme.css).

/** Reads a CSS custom property off :root, falling back to `fallback` when unset
 * (e.g. under jsdom in tests, where no stylesheet is ever loaded). For the page's
 * semantic colour tokens (MetricTile on the history page); chart drawing code reads
 * its own `--chart-*` tokens through chartTheme.ts's `chartVar` instead (Story 32.4). */
export function cssVar(name: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

// Story 32.4: the eight pane colours are the chart's own `--chart-pane-*` tokens (TradingView's
// palette, legible on the white chart), resolved through chartTheme.ts. Order matters: it is also
// the colour-slot order.
function palette(): string[] {
  return chartPalette();
}

/**
 * Deterministic, first-available-slot color for `indicatorId` given the current set of
 * visible indicator ids (`visibleIds`, in mount/registration order). Two calls with the
 * same `visibleIds` always agree, and an id's color never changes while it stays in that
 * list -- it only depends on its own position among `visibleIds`, not on any other id's
 * add/remove history.
 */
export function assignPaneColor(indicatorId: string, visibleIds: string[]): string {
  const index = visibleIds.indexOf(indicatorId);
  const slot = index === -1 ? visibleIds.length : index;
  const pal = palette();
  return pal[slot % pal.length];
}
