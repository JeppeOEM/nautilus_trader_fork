import { AUTO_ANCHOR_PRESETS, type AutoAnchorPreset, DEFAULT_AUTO_ANCHOR } from "./autoAnchor";
import { DEFAULT_SESSION_COUNT, MAX_SESSIONS, SESSION_PERIODS } from "./sessionProfile";
import { DEFAULT_IB_MINUTES, MAX_IB_MINUTES, MIN_IB_MINUTES } from "./tpo";
import { DEFAULT_VOLUME_PROFILE_SETTINGS } from "./volumeProfile";
import { type OutputStyle, LINE_STYLES } from "./indicatorStyle";
import { TIMEFRAMES } from "../timeframes";

// Story 32.6: one coin's chart layout, the shape of `GET/PUT /api/coin/{iid}/layout`
// (`views.preferences.validate_layout`). The OpenAPI schema types the body as a free-form object,
// so this file is the one place the client states its shape and checks what comes back.

export type LayoutMode = "candles" | "lines";
// Story 32.7: `auto` (the Auto Anchored profile) and `tpo` are session-type kinds: the one session
// slot holds at most one of svp / pvp / auto / tpo.
export type ProfileKind = "off" | "visible" | "fixed" | "session" | "auto" | "tpo";

export interface VolumeProfileLayout {
  kind: ProfileKind;
  rows: number;
  value_area_pct: number;
  /** A session profile's period, one of `SESSION_PERIODS` whatever the kind (the server refuses others). */
  session: string;
  hd: boolean;
  /** Story 32.7: the Auto Anchored preset (one of `AUTO_ANCHOR_PRESETS`, the server refuses others). */
  anchor: AutoAnchorPreset;
  /** Story 32.7: the TPO's initial balance length in minutes, `MIN_IB_MINUTES`..`MAX_IB_MINUTES`. */
  ib_minutes: number;
  /** Story 32.7: the TPO shows the touching candles' letters instead of blocks. */
  letters: boolean;
  /** The fixed range's anchors (UTC seconds), `null` while the kind is not "fixed". */
  start: number | null;
  end: number | null;
  /** DW-151/153: how many sessions a session profile draws (1..MAX_SESSIONS), and the saved
   * kind's colours (`#rrggbb`) and POC / Value Area toggles. Optional on the server: a layout
   * saved before them reads back with the defaults below. */
  sessions: number;
  up_color: string;
  down_color: string;
  show_poc: boolean;
  show_value_area: boolean;
}

// Story 32.8: the volume footprint's settings, the optional `footprint` table of the layout. Mirrored
// by `views/preferences.py` (`FOOTPRINT_MODES`, `FOOTPRINT_DEFAULT_IMBALANCE_RATIO`,
// `MAX_FOOTPRINT_ROW_TICKS`; `test_footprint_settings_mirror_the_frontend` pins the pairs, so keep
// the plain `export const NAME = N;` form it reads).
export type FootprintMode = "bid_ask" | "delta" | "volume";
export const FOOTPRINT_MODES: readonly FootprintMode[] = ["bid_ask", "delta", "volume"];
export const FOOTPRINT_DEFAULT_IMBALANCE_RATIO = 3;
export const MAX_FOOTPRINT_ROW_TICKS = 1000000;

export interface FootprintSettings {
  on: boolean;
  /** Price ticks per row; 0 = auto (the server's smallest size giving at most 24 rows per bar). */
  row_ticks: number;
  mode: FootprintMode;
  /** A diagonal imbalance is flagged at `side >= ratio * opposite` (>= 1). */
  imbalance_ratio: number;
  /** The cells' numbers and the per-bar footer; off = heat only. */
  text: boolean;
  /** Absent = the chart's `--chart-up` / `--chart-down` token (never stored as null). */
  buy_color?: string;
  sell_color?: string;
}

// Story 33.5: the chart's pinned Derivatives group, the optional `derivatives` table of the layout.
// Mirrored by `views/preferences.py` (`DERIVATIVE_KEYS`, `DERIVATIVE_OUTPUTS`, `LIQUIDATION_MEASURES`;
// `test_derivatives_settings_mirror_the_frontend` pins them, so keep the literal forms it reads).
export type DerivativeKey = "oi" | "funding" | "basis" | "mark_index" | "liquidations";
export const DERIVATIVE_KEYS: readonly DerivativeKey[] = ["oi", "funding", "basis", "mark_index", "liquidations"];
/** Each entry's output labels: the keys of its `style` table (and its settings dialog's rows). */
export const DERIVATIVE_OUTPUTS: Record<DerivativeKey, readonly string[]> = {
  oi: ["oi"],
  funding: ["rate"],
  basis: ["mark_index", "mark_last"],
  mark_index: ["mark", "index"],
  liquidations: ["liquidations"],
};
/** The Indicators dialog's and the legend's name of each entry. */
export const DERIVATIVE_LABELS: Record<DerivativeKey, string> = {
  oi: "Open Interest",
  funding: "Funding",
  basis: "Basis",
  mark_index: "Mark / Index",
  liquidations: "Liquidations",
};
export type LiquidationMeasure = "size" | "notional";
export const LIQUIDATION_MEASURES: readonly LiquidationMeasure[] = ["size", "notional"];

export interface DerivativeEntry {
  on: boolean;
  /** Per output label; absent = the chart's token colours and the library's line defaults. */
  style?: Record<string, OutputStyle>;
}

export interface LiquidationsEntry extends DerivativeEntry {
  /** The bars plot the liquidated size (base units) or the notional (quote units). */
  measure: LiquidationMeasure;
  /** Series markers on the price pane at each liquidation's bankruptcy price. */
  markers: boolean;
}

export interface DerivativesLayout {
  oi: DerivativeEntry;
  funding: DerivativeEntry;
  basis: DerivativeEntry;
  mark_index: DerivativeEntry;
  liquidations: LiquidationsEntry;
}

export interface ChartLayout {
  bar_seconds: number;
  mode: LayoutMode;
  volume: boolean;
  crosshair: boolean;
  /** Pane id ("price", "volume" or an indicator instance id) -> px. */
  pane_heights: Record<string, number>;
  /** The zoom: how many bars are on screen. Never the absolute scroll position. */
  visible_bars: number;
  volume_profile: VolumeProfileLayout;
  footprint: FootprintSettings;
  /** Optional on the wire (absent = every entry off); always present once normalised. */
  derivatives: DerivativesLayout;
}

export const BUILT_IN_LAYOUT: ChartLayout = {
  bar_seconds: 60,
  mode: "candles",
  volume: true,
  crosshair: true,
  pane_heights: {},
  visible_bars: 120,
  volume_profile: {
    kind: "off",
    rows: 24,
    value_area_pct: 70,
    session: "daily",
    hd: false,
    anchor: DEFAULT_AUTO_ANCHOR,
    ib_minutes: DEFAULT_IB_MINUTES,
    letters: false,
    start: null,
    end: null,
    sessions: DEFAULT_SESSION_COUNT,
    up_color: DEFAULT_VOLUME_PROFILE_SETTINGS.upColor,
    down_color: DEFAULT_VOLUME_PROFILE_SETTINGS.downColor,
    show_poc: DEFAULT_VOLUME_PROFILE_SETTINGS.showPoc,
    show_value_area: DEFAULT_VOLUME_PROFILE_SETTINGS.showValueArea,
  },
  footprint: {
    on: false,
    row_ticks: 0,
    mode: "bid_ask",
    imbalance_ratio: FOOTPRINT_DEFAULT_IMBALANCE_RATIO,
    text: true,
  },
  derivatives: {
    oi: { on: false },
    funding: { on: false },
    basis: { on: false },
    mark_index: { on: false },
    liquidations: { on: false, measure: "size", markers: true },
  },
};

export const PROFILE_KINDS: readonly ProfileKind[] = ["off", "visible", "fixed", "session", "auto", "tpo"];
const MAX_ROWS = 500;

type Raw = Record<string, unknown>;

function isRecord(value: unknown): value is Raw {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isInt(value: unknown, min: number, max: number): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= min && value <= max;
}

function copyHeights(raw: unknown, fallbacks: string[]): Record<string, number> {
  if (!isRecord(raw)) {
    fallbacks.push("pane_heights");
    return {};
  }
  const out: Record<string, number> = {};
  for (const [pane, px] of Object.entries(raw)) {
    if (isInt(px, 1, 10_000)) out[pane] = px;
    else fallbacks.push(`pane_heights.${pane}`);
  }
  return out;
}

const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;

/** An optional key (DW-151/153): absent is the default with no fallback reported (a layout saved
 * before the key existed is not wrong), present but unusable is a named fallback. */
function optional<T>(raw: Raw, key: string, ok: (value: unknown) => value is T, fallback: T, fallbacks: string[]): T {
  if (!(key in raw)) return fallback;
  const value = raw[key];
  if (ok(value)) return value;
  fallbacks.push(`volume_profile.${key}`);
  return fallback;
}

const isBool = (value: unknown): value is boolean => typeof value === "boolean";
const isHexColor = (value: unknown): value is string => typeof value === "string" && HEX_COLOR.test(value);
const isSessionCount = (value: unknown): value is number => isInt(value, 1, MAX_SESSIONS);

function profileOf(raw: unknown, fallbacks: string[]): VolumeProfileLayout {
  const base = BUILT_IN_LAYOUT.volume_profile;
  if (!isRecord(raw)) {
    fallbacks.push("volume_profile");
    return { ...base };
  }
  const anchor = (value: unknown): number | null => (isInt(value, 0, Number.MAX_SAFE_INTEGER) ? value : null);
  const kind = PROFILE_KINDS.find((k) => k === raw.kind);
  const rows = isInt(raw.rows, 2, MAX_ROWS) ? raw.rows : null;
  const area = typeof raw.value_area_pct === "number" && raw.value_area_pct > 0 && raw.value_area_pct <= 100 ? raw.value_area_pct : null;
  const session = typeof raw.session === "string" && raw.session.length > 0 ? raw.session : null;
  // The three Story 32.7 keys are optional on the wire (a file saved before it has none): absent is
  // the default, silently; present but unusable falls back loudly like every other field.
  const anchorPreset = AUTO_ANCHOR_PRESETS.find((a) => a === raw.anchor);
  const ibMinutes = isInt(raw.ib_minutes, MIN_IB_MINUTES, MAX_IB_MINUTES) ? raw.ib_minutes : null;
  const absent = (key: string): boolean => !(key in raw);
  for (const [name, ok] of [["kind", kind], ["rows", rows], ["value_area_pct", area], ["session", session]] as const) {
    if (ok === null || ok === undefined) fallbacks.push(`volume_profile.${name}`);
  }
  if (anchorPreset === undefined && !absent("anchor")) fallbacks.push("volume_profile.anchor");
  if (ibMinutes === null && !absent("ib_minutes")) fallbacks.push("volume_profile.ib_minutes");
  if (typeof raw.letters !== "boolean" && !absent("letters")) fallbacks.push("volume_profile.letters");
  const profile: VolumeProfileLayout = {
    kind: kind ?? base.kind,
    rows: rows ?? base.rows,
    value_area_pct: area ?? base.value_area_pct,
    session: session ?? base.session,
    hd: typeof raw.hd === "boolean" ? raw.hd : base.hd,
    anchor: anchorPreset ?? base.anchor,
    ib_minutes: ibMinutes ?? base.ib_minutes,
    letters: typeof raw.letters === "boolean" ? raw.letters : base.letters,
    start: anchor(raw.start),
    end: anchor(raw.end),
    sessions: optional(raw, "sessions", isSessionCount, base.sessions, fallbacks),
    up_color: optional(raw, "up_color", isHexColor, base.up_color, fallbacks),
    down_color: optional(raw, "down_color", isHexColor, base.down_color, fallbacks),
    show_poc: optional(raw, "show_poc", isBool, base.show_poc, fallbacks),
    show_value_area: optional(raw, "show_value_area", isBool, base.show_value_area, fallbacks),
  };
  // A session-type profile whose period this client does not know would silently draw nothing.
  if ((profile.kind === "session" || profile.kind === "tpo") && !(SESSION_PERIODS as readonly string[]).includes(profile.session)) {
    fallbacks.push("volume_profile.session");
    profile.session = "daily";
  }
  // A fixed range needs both anchors, in order.
  if (profile.kind === "fixed" && (profile.start === null || profile.end === null || profile.start > profile.end)) {
    fallbacks.push("volume_profile.start/end");
    profile.kind = "off";
    profile.start = null;
    profile.end = null;
  }
  return profile;
}

const isColor = (value: unknown): value is string => typeof value === "string" && value.length > 0;

/**
 * The footprint table: absent (a layout saved before Story 32.8) is the default, silently; a present
 * table's unusable fields fall back one by one, each named in `fallbacks`.
 */
function footprintOf(raw: unknown, fallbacks: string[]): FootprintSettings {
  const base = BUILT_IN_LAYOUT.footprint;
  if (raw === undefined) return { ...base };
  if (!isRecord(raw)) {
    fallbacks.push("footprint");
    return { ...base };
  }
  const out: FootprintSettings = { ...base };
  const take = <K extends keyof FootprintSettings>(key: K, ok: boolean): void => {
    if (ok) out[key] = raw[key] as FootprintSettings[K];
    else fallbacks.push(`footprint.${key}`);
  };
  take("on", typeof raw.on === "boolean");
  take("row_ticks", isInt(raw.row_ticks, 0, MAX_FOOTPRINT_ROW_TICKS));
  take("mode", FOOTPRINT_MODES.some((m) => m === raw.mode));
  const ratio = raw.imbalance_ratio;
  take("imbalance_ratio", typeof ratio === "number" && Number.isFinite(ratio) && ratio >= 1);
  take("text", typeof raw.text === "boolean");
  for (const key of ["buy_color", "sell_color"] as const) {
    if (key in raw) take(key, isColor(raw[key]));
  }
  return out;
}

const STYLE_COLOR_KEYS = ["color", "up_color", "down_color"] as const;

/** One output's stored style, each field kept when usable and otherwise named in `fallbacks`. */
function outputStyleOf(raw: unknown, path: string, fallbacks: string[]): OutputStyle {
  if (!isRecord(raw)) {
    fallbacks.push(path);
    return {};
  }
  const out: OutputStyle = {};
  for (const [key, value] of Object.entries(raw)) {
    if ((STYLE_COLOR_KEYS as readonly string[]).includes(key) && isColor(value)) out[key as (typeof STYLE_COLOR_KEYS)[number]] = value;
    else if (key === "line_width" && isInt(value, 1, 4)) out.line_width = value;
    else if (key === "line_style" && LINE_STYLES.some((s) => s === value)) out.line_style = value as OutputStyle["line_style"];
    else fallbacks.push(`${path}.${key}`);
  }
  return out;
}

function stylesOf(raw: unknown, key: DerivativeKey, path: string, fallbacks: string[]): Record<string, OutputStyle> | undefined {
  if (raw === undefined) return undefined;
  if (!isRecord(raw)) {
    fallbacks.push(path);
    return undefined;
  }
  const out: Record<string, OutputStyle> = {};
  for (const [output, style] of Object.entries(raw)) {
    if (DERIVATIVE_OUTPUTS[key].includes(output)) out[output] = outputStyleOf(style, `${path}.${output}`, fallbacks);
    else fallbacks.push(`${path}.${output}`);
  }
  return out;
}

/** One entry: each unusable field falls back to the default by name. */
function derivativeEntryOf(raw: unknown, key: DerivativeKey, fallbacks: string[]): DerivativeEntry & Partial<LiquidationsEntry> {
  const base = BUILT_IN_LAYOUT.derivatives[key];
  const path = `derivatives.${key}`;
  if (!isRecord(raw)) {
    fallbacks.push(path);
    return { ...base };
  }
  const out: DerivativeEntry & Partial<LiquidationsEntry> = { ...base };
  if (typeof raw.on === "boolean") out.on = raw.on;
  else fallbacks.push(`${path}.on`);
  if (key === "liquidations") {
    if (LIQUIDATION_MEASURES.some((m) => m === raw.measure)) out.measure = raw.measure as LiquidationMeasure;
    else fallbacks.push(`${path}.measure`);
    if (typeof raw.markers === "boolean") out.markers = raw.markers;
    else fallbacks.push(`${path}.markers`);
  }
  const style = stylesOf(raw.style, key, `${path}.style`, fallbacks);
  if (style !== undefined) out.style = style;
  return out;
}

/**
 * The derivatives table: absent (a layout saved before Story 33.5) is every entry off, silently; a
 * present table's unusable entries and fields fall back one by one, each named in `fallbacks`.
 */
export function derivativesOf(raw: unknown, fallbacks: string[]): DerivativesLayout {
  const base = BUILT_IN_LAYOUT.derivatives;
  if (raw === undefined) return structuredClone(base);
  if (!isRecord(raw)) {
    fallbacks.push("derivatives");
    return structuredClone(base);
  }
  for (const key of Object.keys(raw)) {
    if (!DERIVATIVE_KEYS.some((k) => k === key)) fallbacks.push(`derivatives.${key}`);
  }
  return {
    oi: derivativeEntryOf(raw.oi, "oi", fallbacks),
    funding: derivativeEntryOf(raw.funding, "funding", fallbacks),
    basis: derivativeEntryOf(raw.basis, "basis", fallbacks),
    mark_index: derivativeEntryOf(raw.mark_index, "mark_index", fallbacks),
    liquidations: derivativeEntryOf(raw.liquidations, "liquidations", fallbacks) as LiquidationsEntry,
  };
}

/**
 * The layout the server returned, made safe to render: any field this client cannot use (a
 * `bar_seconds` outside `TIMEFRAMES`, an unknown `mode`, a malformed number) falls back to the
 * built-in default for that field, never a blank chart. Every fallback is named in ONE
 * `console.error` (visible in the ErrorBar) and reported in `fallbacks`, so the caller saves the
 * corrected layout back instead of failing the same way on every open.
 */
export function normalizeLayout(raw: unknown): { layout: ChartLayout; fallbacks: string[] } {
  const fallbacks: string[] = [];
  const source: Raw = isRecord(raw) ? raw : {};
  if (!isRecord(raw)) fallbacks.push("layout");
  const bar = TIMEFRAMES.find((t) => t.seconds === source.bar_seconds)?.seconds;
  if (bar === undefined) fallbacks.push("bar_seconds");
  const mode: LayoutMode | undefined = source.mode === "candles" || source.mode === "lines" ? source.mode : undefined;
  if (mode === undefined) fallbacks.push("mode");
  if (typeof source.volume !== "boolean") fallbacks.push("volume");
  if (typeof source.crosshair !== "boolean") fallbacks.push("crosshair");
  const bars = source.visible_bars;
  const barsOk = typeof bars === "number" && Number.isFinite(bars) && bars > 0 && bars <= 100_000;
  if (!barsOk) fallbacks.push("visible_bars");
  const layout: ChartLayout = {
    bar_seconds: bar ?? BUILT_IN_LAYOUT.bar_seconds,
    mode: mode ?? BUILT_IN_LAYOUT.mode,
    volume: typeof source.volume === "boolean" ? source.volume : BUILT_IN_LAYOUT.volume,
    crosshair: typeof source.crosshair === "boolean" ? source.crosshair : BUILT_IN_LAYOUT.crosshair,
    pane_heights: copyHeights(source.pane_heights, fallbacks),
    visible_bars: barsOk ? bars : BUILT_IN_LAYOUT.visible_bars,
    volume_profile: profileOf(source.volume_profile, fallbacks),
    footprint: footprintOf(source.footprint, fallbacks),
    derivatives: derivativesOf(source.derivatives, fallbacks),
  };
  if (fallbacks.length > 0) {
    console.error(
      `chart layout: unusable saved value(s) [${fallbacks.join(", ")}] replaced by the built-in default`,
      raw,
    );
  }
  return { layout, fallbacks };
}

/** The PUT body's layout: `start`/`end` are omitted while null and the footprint colours while unset
 * (the table cannot store a null, and the server refuses an empty colour). The derivatives table is
 * always written whole (the server refuses a present table missing an entry). */
export function layoutForSave(layout: ChartLayout): Record<string, unknown> {
  const { start, end, ...profile } = layout.volume_profile;
  const { buy_color, sell_color, ...footprint } = layout.footprint;
  return {
    ...layout,
    volume_profile: { ...profile, ...(start === null ? {} : { start }), ...(end === null ? {} : { end }) },
    footprint: {
      ...footprint,
      ...(isColor(buy_color) ? { buy_color } : {}),
      ...(isColor(sell_color) ? { sell_color } : {}),
    },
  };
}

/** A layout's canonical text: object keys sorted, so `pane_heights` in another order is equal. */
export function layoutKey(layout: ChartLayout): string {
  return JSON.stringify(layout, (_key, value: unknown) =>
    isRecord(value) ? Object.fromEntries(Object.entries(value).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))) : value,
  );
}

/** Whether two layouts are equal in every field (the save's "nothing changed" test). */
export function sameLayout(a: ChartLayout, b: ChartLayout): boolean {
  return layoutKey(a) === layoutKey(b);
}
