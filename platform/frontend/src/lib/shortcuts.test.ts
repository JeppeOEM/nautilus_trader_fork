import { afterEach, describe, expect, it } from "vitest";

import { isTypingContext, shortcutFor, timeframeFromBuffer } from "./shortcuts";

const key = (init: KeyboardEventInit): KeyboardEvent => new KeyboardEvent("keydown", init);

afterEach(() => {
  document.body.innerHTML = "";
});

describe("shortcutFor (Story 33.12)", () => {
  it("maps Alt combinations by the physical key, whatever character macOS makes of it", () => {
    expect(shortcutFor(key({ altKey: true, code: "KeyT", key: "†" }))).toEqual({ kind: "tool", tool: "trendline" });
    expect(shortcutFor(key({ altKey: true, code: "KeyH", key: "˙" }))).toEqual({ kind: "tool", tool: "hline" });
    expect(shortcutFor(key({ altKey: true, code: "KeyF", key: "ƒ" }))).toEqual({ kind: "tool", tool: "fib" });
    expect(shortcutFor(key({ altKey: true, code: "KeyV", key: "√" }))).toEqual({ kind: "tool", tool: "vline" });
    expect(shortcutFor(key({ altKey: true, code: "KeyR", key: "®" }))).toEqual({ kind: "replay" });
    expect(shortcutFor(key({ altKey: true, code: "KeyC", key: "ç" }))).toEqual({ kind: "compare" });
    expect(shortcutFor(key({ altKey: true, code: "KeyQ", key: "œ" }))).toBeNull();
  });

  it("maps Shift+L, Shift+F, /, Ctrl+K, Cmd+K and ?", () => {
    expect(shortcutFor(key({ shiftKey: true, code: "KeyL", key: "L" }))).toEqual({ kind: "log_scale" });
    expect(shortcutFor(key({ shiftKey: true, code: "KeyF", key: "F" }))).toEqual({ kind: "focus" });
    expect(shortcutFor(key({ code: "Slash", key: "/" }))).toEqual({ kind: "search" });
    expect(shortcutFor(key({ ctrlKey: true, code: "KeyK", key: "k" }))).toEqual({ kind: "search" });
    expect(shortcutFor(key({ metaKey: true, code: "KeyK", key: "k" }))).toEqual({ kind: "search" });
    expect(shortcutFor(key({ shiftKey: true, code: "Slash", key: "?" }))).toEqual({ kind: "sheet" });
  });

  it("feeds only unmodified [0-9hdw] keys to the timeframe buffer", () => {
    expect(shortcutFor(key({ code: "Digit1", key: "1" }))).toEqual({ kind: "timeframe_char", char: "1" });
    expect(shortcutFor(key({ code: "KeyH", key: "h" }))).toEqual({ kind: "timeframe_char", char: "h" });
    expect(shortcutFor(key({ code: "KeyD", key: "D" }))).toEqual({ kind: "timeframe_char", char: "d" }); // Caps Lock
    expect(shortcutFor(key({ code: "Enter", key: "Enter" }))).toEqual({ kind: "timeframe_enter" });
    expect(shortcutFor(key({ shiftKey: true, code: "KeyH", key: "H" }))).toBeNull();
    expect(shortcutFor(key({ code: "KeyX", key: "x" }))).toBeNull();
  });

  it("matches / and timeframe characters by the key typed, Shift or not (German, AZERTY)", () => {
    // German: "/" is Shift+7.
    expect(shortcutFor(key({ shiftKey: true, code: "Digit7", key: "/" }))).toEqual({ kind: "search" });
    // AZERTY: digits are Shift on the top row ("&" unshifted, "1" shifted).
    expect(shortcutFor(key({ shiftKey: true, code: "Digit1", key: "1" }))).toEqual({ kind: "timeframe_char", char: "1" });
    expect(shortcutFor(key({ shiftKey: true, code: "Digit5", key: "5" }))).toEqual({ kind: "timeframe_char", char: "5" });
    expect(shortcutFor(key({ code: "Digit1", key: "&" }))).toBeNull();
    // German "?" is Shift+ß.
    expect(shortcutFor(key({ shiftKey: true, code: "Minus", key: "?" }))).toEqual({ kind: "sheet" });
    // Shift+L / Shift+F still reach their actions on every layout.
    expect(shortcutFor(key({ shiftKey: true, code: "KeyL", key: "L" }))).toEqual({ kind: "log_scale" });
    expect(shortcutFor(key({ shiftKey: true, code: "KeyF", key: "F" }))).toEqual({ kind: "focus" });
    expect(shortcutFor(key({ shiftKey: true, code: "Enter", key: "Enter" }))).toBeNull();
  });

  it("leaves Esc, undo and redo to their own handlers", () => {
    expect(shortcutFor(key({ key: "Escape", code: "Escape" }))).toBeNull();
    expect(shortcutFor(key({ ctrlKey: true, code: "KeyZ", key: "z" }))).toBeNull();
    expect(shortcutFor(key({ ctrlKey: true, shiftKey: true, code: "KeyZ", key: "Z" }))).toBeNull();
    expect(shortcutFor(key({ ctrlKey: true, code: "KeyY", key: "y" }))).toBeNull();
  });
});

describe("timeframeFromBuffer (Story 33.12)", () => {
  it.each([
    ["1", 60],
    ["5", 300],
    ["15", 900],
    ["1h", 3600],
    ["4h", 14400],
    ["d", 86400],
    ["1d", 86400],
    ["w", 604800],
    ["1w", 604800],
  ])("%s is %i s", (buffer, seconds) => {
    expect(timeframeFromBuffer(buffer)).toBe(seconds);
  });

  it("names nothing for an unknown buffer", () => {
    expect(timeframeFromBuffer("7")).toBeNull();
    expect(timeframeFromBuffer("constructor")).toBeNull();
    expect(timeframeFromBuffer("")).toBeNull();
  });
});

describe("isTypingContext (Story 33.12)", () => {
  function onTarget(element: Element): KeyboardEvent {
    const event = key({ key: "1" });
    Object.defineProperty(event, "target", { value: element });
    return event;
  }

  it("is true in an input, select, textarea or contenteditable element", () => {
    for (const html of ["<input />", "<select></select>", "<textarea></textarea>", "<div contenteditable='true'><span></span></div>"]) {
      document.body.innerHTML = html;
      const target = document.body.querySelector("span") ?? document.body.firstElementChild!;
      expect(isTypingContext(onTarget(target))).toBe(true);
    }
  });

  it("is true anywhere while a dialog is open, and false otherwise", () => {
    document.body.innerHTML = "<button>b</button><dialog></dialog>";
    const button = document.body.querySelector("button")!;
    expect(isTypingContext(onTarget(button))).toBe(false);
    document.body.querySelector("dialog")!.setAttribute("open", "");
    expect(isTypingContext(onTarget(button))).toBe(true);
  });
});
