import { useCallback, useEffect, useState } from "react";

// The chart's focus view (chart UX rework, 2026-10-08; it replaced Story 33.12's browser fullscreen,
// which took the whole screen and fought a tiling window manager): the site's navigation is hidden
// and the chart, with its top bar, tools and every pane, fits the browser window as it is sized, so
// an i3 tile is the chart. The page still scrolls. View state only: never in the layout or the
// browser's storage, so a reload starts in the normal page.

/** The class on `<body>` while the focus view is on; `index.css` hides the site's chrome with it. */
export const FOCUS_BODY_CLASS = "chart-focus";

export interface FocusView {
  active: boolean;
  toggle: () => void;
  exit: () => void;
}

export function useFocusView(): FocusView {
  const [active, setActive] = useState(false);

  useEffect(() => {
    if (!active) return;
    document.body.classList.add(FOCUS_BODY_CLASS);
    // Leaving the chart page (another route) ends it: the class never outlives the page.
    return () => document.body.classList.remove(FOCUS_BODY_CLASS);
  }, [active]);

  const toggle = useCallback((): void => setActive((on) => !on), []);
  const exit = useCallback((): void => setActive(false), []);
  return { active, toggle, exit };
}
