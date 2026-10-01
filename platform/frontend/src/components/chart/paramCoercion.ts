/** True when `raw` is a well-formed replacement for a `previous`-typed param -- what
 * `coerceParamValue` would otherwise silently swallow by keeping the prior value. With the
 * catalog's `choices` for this param (an enum's member names, Story 27.7) only one of them is
 * valid: the backend resolves the name to its enum and would reject any other. */
export function isValidParamText(previous: unknown, raw: string, choices?: readonly string[]): boolean {
  if (choices !== undefined) return choices.includes(raw);
  if (typeof previous === "boolean") return ["true", "false"].includes(raw.trim().toLowerCase());
  if (typeof previous === "number") return raw.trim() !== "" && !Number.isNaN(Number(raw));
  return true;
}

/** Coerces a picker param-input's raw text back into the type of its `previous` value.
 * Extracted from `IndicatorPicker.tsx` (rather than exported from that component file)
 * so the file stays component-only-exports for fast refresh, same reasoning as
 * `paneColors.ts` living alongside it. */
export function coerceParamValue(previous: unknown, raw: string): unknown {
  if (typeof previous === "boolean") {
    const normalized = raw.trim().toLowerCase();
    if (normalized === "true") return true;
    if (normalized === "false") return false;
    return previous; // unrecognized text -- keep prior value rather than silently going false
  }
  if (typeof previous === "number") {
    if (raw.trim() === "") return previous; // Number("") is 0, not NaN -- don't coerce a cleared field to 0
    const parsed = Number(raw);
    return Number.isNaN(parsed) ? previous : parsed;
  }
  if (typeof previous === "string") return raw;
  return previous; // structured (object/array/null) default -- no safe string->value coercion
}

/** The text a param input starts from: each saved param as its own string (the one
 * `String(v)` rule every param editor shares, `ParamInputs.tsx` and the Technicals row alike). */
export function rawFromParams(params: Record<string, unknown>): Record<string, string> {
  return Object.fromEntries(Object.entries(params).map(([k, v]) => [k, String(v)]));
}

/** The param keys whose current text `isValidParamText` refuses -- shown, never silently reverted. */
export function invalidParamKeys(
  params: Record<string, unknown>,
  raw: Record<string, string>,
  choices: Record<string, string[]>,
): string[] {
  return Object.keys(params).filter((k) => !isValidParamText(params[k], raw[k] ?? "", choices[k]));
}

/** Every param's text coerced back to the type of its saved value (call only when none is invalid). */
export function coerceParams(params: Record<string, unknown>, raw: Record<string, string>): Record<string, unknown> {
  return Object.fromEntries(Object.keys(params).map((k) => [k, coerceParamValue(params[k], raw[k] ?? "")]));
}
