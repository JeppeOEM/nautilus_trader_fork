// Deterministic placeholder pane-color-slot assignment (Story 15.4 AC #7). Exact hex
// values are Story 15.9's job -- this is only enough to make up-to-5 simultaneous panes
// visually distinguishable in the interim, written generically enough that Story 15.6
// (user-added/removed panes) and 15.9 (final palette swap) can call the same function.

const PLACEHOLDER_PALETTE = ["#2962ff", "#e91e63", "#ff9800", "#4caf50", "#9c27b0"];

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
  return PLACEHOLDER_PALETTE[slot % PLACEHOLDER_PALETTE.length];
}
