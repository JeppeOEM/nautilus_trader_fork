import { type RefObject, useCallback, useEffect, useState } from "react";

// Story 33.12: the chart's fullscreen, the browser Fullscreen API on one element (`.chart-stage`).
// View state only: never in the layout, never in the browser's storage, so a reload starts windowed.

const FULLSCREEN_REFUSED = "Fullscreen was refused by the browser";

export interface Fullscreen {
  /** The element is `document.fullscreenElement` now (after the browser's `fullscreenchange`). */
  active: boolean;
  /** Why the last request did not happen (no API, or the browser rejected it), else null. */
  error: string | null;
  enter: () => void;
  exit: () => void;
  toggle: () => void;
}

export function useFullscreen(ref: RefObject<HTMLElement | null>): Fullscreen {
  const [active, setActive] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // The browser's own event is the truth: Esc, another element going fullscreen or the element
    // leaving the DOM all end it without a call from here.
    const sync = (): void => setActive(ref.current !== null && document.fullscreenElement === ref.current);
    sync();
    document.addEventListener("fullscreenchange", sync);
    return () => document.removeEventListener("fullscreenchange", sync);
  }, [ref]);

  const enter = useCallback((): void => {
    const element = ref.current;
    setError(null);
    if (!element || typeof element.requestFullscreen !== "function") {
      setError(FULLSCREEN_REFUSED);
      return;
    }
    const refused = (err: unknown): void => {
      console.warn("ChartPage: fullscreen request refused", err);
      setError(FULLSCREEN_REFUSED);
    };
    // Older engines throw synchronously instead of rejecting.
    try {
      element.requestFullscreen().catch(refused);
    } catch (err) {
      refused(err);
    }
  }, [ref]);

  const exit = useCallback((): void => {
    if (document.fullscreenElement === null || typeof document.exitFullscreen !== "function") return;
    document.exitFullscreen().catch((err: unknown) => console.error("ChartPage: leaving fullscreen failed", err));
  }, []);

  const toggle = useCallback((): void => {
    if (ref.current !== null && document.fullscreenElement === ref.current) exit();
    else enter();
  }, [ref, enter, exit]);

  return { active, error, enter, exit, toggle };
}
