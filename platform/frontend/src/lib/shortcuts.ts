// Story 33.12: the chart page's keyboard shortcuts, one table. The page's one keydown handler maps an
// event through `shortcutFor`, the `?` sheet (`ShortcutSheet`) lists `SHORTCUTS`, and the Docs page
// lists the same rows (`pages/docs/kbData.ts`), so the three can never disagree.

export type ShortcutTool = "trendline" | "hline" | "fib" | "vline";

export type ShortcutAction =
  | { kind: "tool"; tool: ShortcutTool }
  | { kind: "replay" }
  | { kind: "log_scale" }
  | { kind: "fullscreen" }
  | { kind: "compare" }
  | { kind: "search" }
  | { kind: "sheet" }
  /** One character of a typed timeframe (`1`, `h`, ...), shown in the buffer chip until Enter. */
  | { kind: "timeframe_char"; char: string }
  | { kind: "timeframe_enter" };

export interface ShortcutRow {
  keys: string;
  does: string;
}

/** Every shortcut of the chart page, as the sheet and the Docs page print them. */
export const SHORTCUTS: readonly ShortcutRow[] = [
  { keys: "1 5 15 1h 4h d w, then Enter", does: "Switch timeframe (1m, 5m, 15m, 1H, 4H, 1D, 1W; 1d and 1w work too). Candles mode only" },
  { keys: "Alt+T", does: "Trendline tool" },
  { keys: "Alt+H", does: "Horizontal line tool (Candles mode only)" },
  { keys: "Alt+F", does: "Fibonacci retracement tool" },
  { keys: "Alt+V", does: "Vertical line tool" },
  { keys: "Alt+R", does: "Bar Replay: pick a start bar, or exit a running replay (candle charts only; nothing in Lines mode)" },
  { keys: "Alt+C", does: "Compare: open the symbol search to add a compare symbol (Candles mode only)" },
  { keys: "Shift+L", does: "Log scale on / off (not while a compare forces the percent scale)" },
  { keys: "Shift+F", does: "Fullscreen on / off (the chart with every pane, the tools and the top bar)" },
  { keys: "/ or Ctrl+K (Cmd+K)", does: "Symbol search: open another market" },
  { keys: "?", does: "This list" },
  { keys: "Esc", does: "Disarm the tool, cancel a replay pick, clear a typed timeframe" },
  { keys: "Ctrl+Z (Cmd+Z)", does: "Undo a drawing edit" },
  { keys: "Ctrl+Shift+Z or Ctrl+Y", does: "Redo a drawing edit" },
  { keys: "Shift while drawing", does: "Snap a line to 0 / 45 / 90 degrees" },
];

/** How long a typed timeframe waits for Enter before it is dropped. */
export const TIMEFRAME_BUFFER_IDLE_MS = 3000;
/** Longer than any timeframe word: an over-long buffer can never match, so it is not kept growing. */
export const TIMEFRAME_BUFFER_MAX = 3;

const TIMEFRAME_WORDS: Record<string, number> = {
  "1": 60,
  "5": 300,
  "15": 900,
  "1h": 3600,
  "4h": 14400,
  d: 86400,
  "1d": 86400,
  w: 604800,
  "1w": 604800,
};

/** The bar size a typed buffer names, or null for an unknown one. */
export function timeframeFromBuffer(buffer: string): number | null {
  return Object.hasOwn(TIMEFRAME_WORDS, buffer) ? TIMEFRAME_WORDS[buffer] : null;
}

/**
 * Whether a key belongs to something else: the operator types in a field (which keeps its own keys,
 * undo included) or a dialog is open (its own Esc, Enter and focus trap). Every chart shortcut, the
 * drawing undo / redo included, stands down then.
 */
export function isTypingContext(event: KeyboardEvent): boolean {
  const target = event.target instanceof Element ? event.target : null;
  if (target?.closest("input, select, textarea, [contenteditable='true']")) return true;
  return document.querySelector("dialog[open]") !== null;
}

// By `event.code`, not `event.key`: on macOS Alt turns the letter into another character (Alt+T is
// "†"), while the physical key stays `KeyT`.
const ALT_ACTIONS: Record<string, ShortcutAction> = {
  KeyT: { kind: "tool", tool: "trendline" },
  KeyH: { kind: "tool", tool: "hline" },
  KeyF: { kind: "tool", tool: "fib" },
  KeyV: { kind: "tool", tool: "vline" },
  KeyR: { kind: "replay" },
  KeyC: { kind: "compare" },
};

const TIMEFRAME_CHAR = /^[0-9hdw]$/;

/**
 * The timeframe character a key types, or null. By `event.key`, so a layout that needs Shift for a
 * digit (AZERTY) still types it. Without Shift a letter is lowercased (Caps Lock); with Shift it is
 * taken as typed, so `Shift+H` ("H") stays out of the buffer.
 */
function timeframeChar(event: KeyboardEvent): string | null {
  const char = event.shiftKey ? event.key : event.key.toLowerCase();
  return TIMEFRAME_CHAR.test(char) ? char : null;
}

/**
 * The action a keydown asks for, or null. Alt and Ctrl/Cmd combinations are checked first. Then the
 * characters matched by `event.key` whatever Shift does (`?`, `/` and a timeframe character), since a
 * layout may need Shift to type them (German `/` is Shift+7, AZERTY digits are shifted). Only a
 * Shift key that types none of them is `Shift+L` / `Shift+F`, matched by the physical key. Esc, undo
 * and redo keep their own handlers and are not mapped here.
 */
export function shortcutFor(event: KeyboardEvent): ShortcutAction | null {
  const command = event.ctrlKey || event.metaKey;
  if (event.altKey) return command ? null : (ALT_ACTIONS[event.code] ?? null);
  if (command) return !event.shiftKey && event.key.toLowerCase() === "k" ? { kind: "search" } : null;
  if (event.key === "?") return { kind: "sheet" };
  if (event.key === "/") return { kind: "search" };
  const char = timeframeChar(event);
  if (char !== null) return { kind: "timeframe_char", char };
  if (event.shiftKey) {
    if (event.code === "KeyL") return { kind: "log_scale" };
    if (event.code === "KeyF") return { kind: "fullscreen" };
    return null;
  }
  return event.key === "Enter" ? { kind: "timeframe_enter" } : null;
}
