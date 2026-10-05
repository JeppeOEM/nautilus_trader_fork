import { AUTO_ANCHOR_PRESETS, type AutoAnchorPreset, DEFAULT_AUTO_ANCHOR } from "./autoAnchor";
import { SESSION_PERIODS } from "./sessionProfile";
import { DEFAULT_IB_MINUTES, MAX_IB_MINUTES, MIN_IB_MINUTES } from "./tpo";
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
  },
  footprint: {
    on: false,
    row_ticks: 0,
    mode: "bid_ask",
    imbalance_ratio: FOOTPRINT_DEFAULT_IMBALANCE_RATIO,
    text: true,
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
 * (the table cannot store a null, and the server refuses an empty colour). */
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
