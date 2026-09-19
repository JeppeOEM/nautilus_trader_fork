/** True when `raw` is a well-formed replacement for a `previous`-typed param -- what
 * `coerceParamValue` would otherwise silently swallow by keeping the prior value. */
export function isValidParamText(previous: unknown, raw: string): boolean {
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
