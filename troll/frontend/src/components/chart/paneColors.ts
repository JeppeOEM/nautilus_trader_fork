// Story 15.9: final pane-color-slot assignment (Story 15.4 AC #7). Values come
// from the 16-color VGA/ANSI token set declared in theme.css -- read at call time
// via `cssVar` (not at module load) so it always reflects whatever the stylesheet
// currently has applied, and works whether or not the stylesheet has loaded yet
// (falls back to the same literal value baked into theme.css).

/** Reads a CSS custom property off :root, falling back to `fallback` when unset
 * (e.g. under jsdom in tests, where no stylesheet is ever loaded). Shared with
 * LightweightChart.tsx so there is exactly one place that knows how to resolve a
 * design token into a literal color string a canvas API can use. */
export function cssVar(name: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

// Eight bright/high-contrast VGA tones, chosen for legibility against the
// terminal's black chart background -- deliberately skips black/dark-gray/the
// non-"light" red/green/cyan/magenta/blue (too low-contrast on black to read as a
// chart line). Order matters: it's also the color-slot order.
function palette(): string[] {
  return [
    cssVar("--vga-light-cyan", "#55ffff"),
    cssVar("--vga-light-green", "#55ff55"),
    cssVar("--vga-light-red", "#ff5555"),
    cssVar("--vga-yellow", "#ffff55"),
    cssVar("--vga-light-magenta", "#ff55ff"),
    cssVar("--vga-light-blue", "#5555ff"),
    cssVar("--vga-brown", "#aa5500"),
    cssVar("--vga-white", "#ffffff"),
  ];
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
