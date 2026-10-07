/**
 * Story 33.10: the pure editing mechanics of the drawings -- magnet snapping, the Shift angle
 * constraint and the bounded undo/redo history. No DOM and no chart library: the chart hands these
 * pixels and bars, the store hands them its list, and each is tested on its own.
 */

import type { Drawing, Px } from "./drawings";

// -- magnet ---------------------------------------------------------------------------------------

/** How close (CSS px) a weak magnet must be to a bar's O/H/L/C to snap to it. */
export const MAGNET_RADIUS_PX = 12;

/** Off; weak (snap within `MAGNET_RADIUS_PX`); strong (always the nearest of O/H/L/C). */
export type MagnetMode = "off" | "weak" | "strong";

const MAGNET_ORDER: readonly MagnetMode[] = ["off", "weak", "strong"];

/** The rail's toggle: off -> weak -> strong -> off. */
export function nextMagnetMode(mode: MagnetMode): MagnetMode {
  return MAGNET_ORDER[(MAGNET_ORDER.indexOf(mode) + 1) % MAGNET_ORDER.length];
}

/** The real OHLC of the bar under the pointer (never a Heikin Ashi row, AD-F6). */
export interface MagnetBar {
  open: number;
  high: number;
  low: number;
  close: number;
}

/**
 * The price a point at pointer height `y` lands on: the nearest (in pixels) of `bar`'s open, high,
 * low and close -- always for a strong magnet, within `MAGNET_RADIUS_PX` for a weak one -- else the
 * raw `price`. No bar (a whitespace slot, past the data, Lines mode) or a price the scale cannot place
 * leaves the price raw.
 */
export function magnetPrice(
  price: number,
  y: number,
  bar: MagnetBar | null,
  priceToY: (price: number) => number | null,
  mode: MagnetMode,
): number {
  if (mode === "off" || bar === null) return price;
  let best: { price: number; distance: number } | null = null;
  for (const candidate of [bar.open, bar.high, bar.low, bar.close]) {
    const cy = priceToY(candidate);
    if (cy === null || !Number.isFinite(cy)) continue;
    const distance = Math.abs(cy - y);
    if (best === null || distance < best.distance) best = { price: candidate, distance };
  }
  if (best === null) return price;
  return mode === "strong" || best.distance <= MAGNET_RADIUS_PX ? best.price : price;
}

// -- Shift ----------------------------------------------------------------------------------------

const STEP = Math.PI / 4;

/**
 * `to` constrained to the nearest multiple of 45 degrees from `from`, in pixel space (horizontal,
 * diagonal, vertical: what the operator sees, whatever the price scale): `to` projected onto that
 * direction. The caller converts the result back to time and price.
 */
export function constrainAngle(from: Px, to: Px): Px {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  if (dx === 0 && dy === 0) return to;
  const angle = Math.round(Math.atan2(dy, dx) / STEP) * STEP;
  const ux = Math.cos(angle);
  const uy = Math.sin(angle);
  // cos/sin of a multiple of 45 degrees carry ~1e-16 noise where they are 0: an exact 0 keeps a
  // horizontal line's y (a vertical line's x) exactly on the first point's.
  const cx = Math.abs(ux) < 1e-12 ? 0 : ux;
  const cy = Math.abs(uy) < 1e-12 ? 0 : uy;
  const length = dx * cx + dy * cy;
  return { x: from.x + length * cx, y: from.y + length * cy };
}

/**
 * The two-point line kinds (and placing tools of the same name) whose second point Shift constrains
 * relative to the first: the trendline, ray, extended line, arrow and a channel's A-B line.
 */
const ANGLE_KINDS: ReadonlySet<string> = new Set(["trendline", "ray", "extended", "arrow", "channel"]);

export function constrainsAngle(kindOrTool: string): boolean {
  return ANGLE_KINDS.has(kindOrTool);
}

// -- undo / redo ----------------------------------------------------------------------------------

/** The most undo steps kept; an edit past it drops the oldest. */
export const HISTORY_LIMIT = 100;

/**
 * A coin's drawings with their undo history. `past` holds the lists before each edit (oldest first),
 * `future` the lists an undo stepped back from (next redo first). `gesture` names the gesture the
 * last edit belonged to (a handle drag, `drag:<n>`): every edit of one gesture is one undo step.
 */
export interface DrawingsHistory {
  drawings: Drawing[];
  past: Drawing[][];
  future: Drawing[][];
  gesture: string | null;
}

export type DrawingsAction =
  /** The server's list (a GET): replaces everything, history included; never undoable. */
  | { type: "load"; drawings: Drawing[] }
  /** An edit; `gesture` coalesces it with the previous edit of the same gesture. */
  | { type: "edit"; update: (all: Drawing[]) => Drawing[]; gesture?: string }
  | { type: "undo" }
  | { type: "redo" };

export const EMPTY_HISTORY: DrawingsHistory = { drawings: [], past: [], future: [], gesture: null };

/**
 * The history after `action`. Pure (React may run it twice under StrictMode): an edit whose update
 * returns the list it was given (by reference) is no edit and records nothing; any real edit clears
 * the redo stack; an undo or redo with nothing to step to is a no-op.
 */
export function drawingsReducer(state: DrawingsHistory, action: DrawingsAction): DrawingsHistory {
  switch (action.type) {
    case "load":
      return { drawings: action.drawings, past: [], future: [], gesture: null };
    case "edit": {
      const next = action.update(state.drawings);
      if (next === state.drawings) return state;
      const coalesce = action.gesture !== undefined && action.gesture === state.gesture;
      return {
        drawings: next,
        past: coalesce ? state.past : [...state.past, state.drawings].slice(-HISTORY_LIMIT),
        future: [],
        gesture: action.gesture ?? null,
      };
    }
    case "undo": {
      if (state.past.length === 0) return state;
      return {
        drawings: state.past[state.past.length - 1],
        past: state.past.slice(0, -1),
        future: [state.drawings, ...state.future],
        gesture: null,
      };
    }
    case "redo": {
      if (state.future.length === 0) return state;
      return {
        drawings: state.future[0],
        past: [...state.past, state.drawings].slice(-HISTORY_LIMIT),
        future: state.future.slice(1),
        gesture: null,
      };
    }
  }
}

/** The keys a JSON save writes: an `undefined` field is absent from it. */
const savedKeys = (o: Record<string, unknown>): string[] => Object.keys(o).filter((k) => o[k] !== undefined);

/** Two JSON values that save alike (key order ignored): a stored drawing is plain JSON. */
function sameJson(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const aRecord = a as Record<string, unknown>;
  const bRecord = b as Record<string, unknown>;
  const aKeys = savedKeys(aRecord);
  if (aKeys.length !== savedKeys(bRecord).length) return false;
  return aKeys.every((k) => sameJson(aRecord[k], bRecord[k]));
}

/**
 * The list with drawing `id` replaced by `change(drawing)`, or the list itself (by reference) when no
 * drawing has that id or the change stores exactly what it had: no edit, so `drawingsReducer` records
 * no undo step and the store sends no save (a dialog's Apply with nothing changed, a colour picked
 * again, a lock of a locked drawing).
 */
export function replaceDrawing(all: Drawing[], id: string, change: (d: Drawing) => Drawing): Drawing[] {
  const index = all.findIndex((d) => d.id === id);
  if (index === -1) return all;
  const next = change(all[index]);
  return sameJson(next, all[index]) ? all : all.map((d, i) => (i === index ? next : d));
}

let dragGestures = 0;

/**
 * A fresh handle-drag gesture (`drag:<n>`), unique for the page's life. Module-wide, not a ref: the
 * chart component remounts on a timeframe change while the history above it lives on, and a count
 * restarting at 1 would coalesce the next drag into the last one recorded before the switch.
 */
export function nextDragGesture(): string {
  dragGestures += 1;
  return `drag:${dragGestures}`;
}
