// Story 33.8: an alert's condition as the forms edit it. The kinds and their fields mirror
// `alerting/domain/conditions.py`'s `CONDITION_KINDS`/`CONDITION_FIELDS` (held to them by
// `data_api/tests/test_alert_conditions_mirror.py`); the server validates every range and is the
// only source of a condition's text (`condition_text`) and of any value it compares -- this module
// only converts between the typed form and the request body.

export const CONDITION_KINDS = [
  "price_cross",
  "price_cross_up",
  "price_cross_down",
  "price_above",
  "price_below",
  "pct_move",
  "channel_exit",
  "indicator",
  "trendline_cross",
  "funding_above",
  "funding_below",
  "oi_change",
  "liquidation_notional",
  "forced_share",
] as const;

export type ConditionKind = (typeof CONDITION_KINDS)[number];

/** Every field of each kind, in the server's normalised order (the mirror test parses this). */
export const CONDITION_FIELDS: Record<ConditionKind, readonly string[]> = {
  price_cross: ["level"],
  price_cross_up: ["level"],
  price_cross_down: ["level"],
  price_above: ["level"],
  price_below: ["level"],
  pct_move: ["pct", "bars"],
  channel_exit: ["upper", "lower"],
  indicator: ["name", "params", "source", "output", "op", "value"],
  trendline_cross: ["drawing_id"],
  funding_above: ["rate"],
  funding_below: ["rate"],
  oi_change: ["pct", "window_s"],
  liquidation_notional: ["notional", "window_s", "side"],
  forced_share: ["share", "window_s"],
};

export const CONDITION_LABELS: Record<ConditionKind, string> = {
  price_cross: "Price crosses",
  price_cross_up: "Price crosses up",
  price_cross_down: "Price crosses down",
  price_above: "Price above",
  price_below: "Price below",
  pct_move: "% move over N bars",
  channel_exit: "Channel exit",
  indicator: "Indicator",
  trendline_cross: "Trendline cross",
  funding_above: "Funding above",
  funding_below: "Funding below",
  oi_change: "Open interest change",
  liquidation_notional: "Liquidation notional",
  forced_share: "Forced share",
};

/** The kinds that carry a price `level` (the chart dialog's horizontal-line target applies). */
export const LEVEL_KINDS: readonly ConditionKind[] = [
  "price_cross",
  "price_cross_up",
  "price_cross_down",
  "price_above",
  "price_below",
];

/** A condition as the API carries it: `{kind, ...fields}`. */
export type AlertCondition = { kind: ConditionKind } & Record<string, unknown>;

export interface FieldOption {
  value: string;
  label: string;
}

/** How a scalar field is typed in: a number, a whole number, or one of a fixed set. */
export interface FieldSpec {
  label: string;
  input: "number" | "integer" | "select";
  options?: readonly FieldOption[];
}

// The fields `ConditionFields` renders generically. `name`, `params`, `source`, `output` (the
// indicator picker) and `drawing_id` (the coin's trendlines) have their own inputs.
export const FIELD_SPECS: Record<string, FieldSpec> = {
  level: { label: "Price level", input: "number" },
  pct: { label: "Change %", input: "number" },
  bars: { label: "Bars", input: "integer" },
  upper: { label: "Upper", input: "number" },
  lower: { label: "Lower", input: "number" },
  op: {
    label: "Operator",
    input: "select",
    options: [
      { value: ">", label: ">" },
      { value: "<", label: "<" },
      { value: "crosses_up", label: "crosses up" },
      { value: "crosses_down", label: "crosses down" },
    ],
  },
  value: { label: "Value", input: "number" },
  rate: { label: "Funding rate", input: "number" },
  window_s: { label: "Window (s)", input: "integer" },
  notional: { label: "Notional", input: "number" },
  side: {
    label: "Side",
    input: "select",
    options: [
      { value: "", label: "both" },
      { value: "long", label: "long" },
      { value: "short", label: "short" },
    ],
  },
  share: { label: "Forced share", input: "number" },
};

/** Every field as typed text (a half-typed "-0." must survive a re-render), params apart. */
export interface ConditionForm {
  kind: ConditionKind;
  fields: Record<string, string>;
  params: Record<string, string>;
  /** The params as stored (an edited alert's): a param the catalog does not type keeps its type. */
  storedParams?: Record<string, unknown>;
}

// A new form's starting text: a blank threshold the operator must type, the usual choices preset.
const FIELD_DEFAULTS: Record<string, string> = { bars: "1", op: ">", source: "close", side: "", window_s: "300" };

export function emptyForm(kind: ConditionKind): ConditionForm {
  const fields: Record<string, string> = {};
  for (const name of CONDITION_FIELDS[kind]) {
    if (name !== "params") fields[name] = FIELD_DEFAULTS[name] ?? "";
  }
  if (kind === "oi_change") fields.window_s = "3600";
  return { kind, fields, params: {} };
}

function asText(value: unknown): string {
  return value === undefined || value === null ? "" : String(value);
}

/** The form an existing condition (an edited alert's, a drawing's prefill) opens with. */
export function conditionToForm(condition: AlertCondition): ConditionForm {
  const form = emptyForm(condition.kind);
  for (const name of CONDITION_FIELDS[condition.kind]) {
    if (name !== "params" && name in condition) form.fields[name] = asText(condition[name]);
  }
  const params = condition.params;
  if (params && typeof params === "object") {
    form.storedParams = { ...(params as Record<string, unknown>) };
    for (const [key, value] of Object.entries(form.storedParams)) form.params[key] = asText(value);
  }
  return form;
}

export type FormResult = { condition: AlertCondition } | { error: string };

function scalar(name: string, text: string): unknown | { error: string } {
  const spec = FIELD_SPECS[name];
  if (!spec || spec.input === "select") return text;
  if (text.trim() === "") return { error: `${spec.label} is required.` };
  const value = Number(text);
  if (!Number.isFinite(value)) return { error: `${spec.label} must be a number.` };
  if (spec.input === "integer" && !Number.isInteger(value)) return { error: `${spec.label} must be a whole number.` };
  return value;
}

/**
 * A param's typed value, by the type of its catalog default, else of its stored value (the server
 * checks the range). Text is never guessed into a number: `AnchoredStoredVWAP`'s `anchor_t` is a
 * digit string and must stay one.
 */
function paramValue(key: string, text: string, typedLike: unknown): unknown | { error: string } {
  if (typeof typedLike === "boolean") return text === "true";
  if (typeof typedLike !== "number") return text;
  const value = Number(text);
  if (text.trim() === "" || !Number.isFinite(value)) return { error: `Parameter ${key} must be a number.` };
  return value;
}

function isError(value: unknown): value is { error: string } {
  return typeof value === "object" && value !== null && "error" in value;
}

/** What `formToCondition` needs of the picker's catalog: each entry's default params. */
export type ParamCatalog = Record<string, { params: Record<string, unknown> }>;

function indicatorParams(form: ConditionForm, catalog: ParamCatalog | undefined): Record<string, unknown> | { error: string } {
  // Only once the catalog has loaded: before it, a param's type is unknown. A name the catalog does
  // not list (an unlisted entry, one since removed) types nothing: its stored params keep their own.
  if (catalog === undefined) return { error: "The indicator catalog has not loaded; an indicator condition cannot be saved yet." };
  const defaults = catalog[form.fields.name ?? ""]?.params ?? {};
  const stored = form.storedParams ?? {};
  const params: Record<string, unknown> = {};
  for (const [key, text] of Object.entries(form.params)) {
    const value = paramValue(key, text, key in defaults ? defaults[key] : stored[key]);
    if (isError(value)) return value;
    params[key] = value;
  }
  return params;
}

/**
 * The request body's condition, or the first field the operator still has to fix. Only typing is
 * checked here (a number where a number goes); every range is the server's 422, shown inline.
 * An indicator's params are typed by the loaded `catalog`'s defaults (undefined: not loaded, and an
 * indicator condition is refused).
 */
export function formToCondition(form: ConditionForm, catalog?: ParamCatalog): FormResult {
  const condition: AlertCondition = { kind: form.kind };
  for (const name of CONDITION_FIELDS[form.kind]) {
    if (name === "params") {
      const params = indicatorParams(form, catalog);
      if (isError(params)) return params;
      condition.params = params;
      continue;
    }
    const text = form.fields[name] ?? "";
    if (name === "side" && text === "") continue; // both sides: the key is omitted
    if ((name === "name" || name === "output" || name === "drawing_id") && text === "") {
      return { error: `Choose ${name === "drawing_id" ? "a trendline" : `the indicator's ${name}`}.` };
    }
    const value = scalar(name, text);
    if (isError(value)) return value;
    condition[name] = value;
  }
  return { condition };
}

export const FREQUENCIES: readonly FieldOption[] = [
  { value: "once_per_bar_close", label: "Once per bar close" },
  { value: "once_per_bar", label: "Once per bar" },
  { value: "only_once", label: "Only once" },
];

export const FREQUENCY_LABELS: Record<string, string> = Object.fromEntries(
  FREQUENCIES.map((f) => [f.value, f.label.toLowerCase()]),
);

/** Every placeholder the server's `render` fills (`{{condition}}` is its own condition text). */
export const DEFAULT_TEMPLATE = "{{ticker}} {{condition}}: {{value}} ({{time}})";
export const TEMPLATE_HELP = "{{ticker}} {{close}} {{time}} {{interval}} {{value}} {{condition}}";

/** Date input value ("YYYY-MM-DD", or "" = never) -> end of that local day in epoch ns. */
export function expiryNs(date: string): number | null {
  if (!date) return null;
  return new Date(`${date}T23:59:59`).getTime() * 1_000_000;
}

/**
 * An edit's expiry: the alert's own `expires_at_ns` while its date is untouched (the date input
 * holds only a day, so re-deriving it would move the expiry to 23:59:59 local of that day), else
 * the end of the newly chosen day.
 */
export function editedExpiryNs(date: string, original: number | null | undefined): number | null {
  const kept = original ?? null;
  return date === expiryDate(kept) ? kept : expiryNs(date);
}

/** The inverse for an edit form: epoch ns -> the local "YYYY-MM-DD" it falls on ("" = never). */
export function expiryDate(ns: number | null | undefined): string {
  if (ns === null || ns === undefined) return "";
  const d = new Date(ns / 1_000_000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
