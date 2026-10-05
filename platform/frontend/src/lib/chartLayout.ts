import { DEFAULT_SESSION_COUNT, MAX_SESSIONS, SESSION_PERIODS } from "./sessionProfile";
import { DEFAULT_VOLUME_PROFILE_SETTINGS } from "./volumeProfile";
import { TIMEFRAMES } from "../timeframes";

// Story 32.6: one coin's chart layout, the shape of `GET/PUT /api/coin/{iid}/layout`
// (`views.preferences.validate_layout`). The OpenAPI schema types the body as a free-form object,
// so this file is the one place the client states its shape and checks what comes back.

export type LayoutMode = "candles" | "lines";
export type ProfileKind = "off" | "visible" | "fixed" | "session";

export interface VolumeProfileLayout {
  kind: ProfileKind;
  rows: number;
  value_area_pct: number;
  /** A session profile's period, one of `SESSION_PERIODS` whatever the kind (the server refuses others). */
  session: string;
  hd: boolean;
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
    start: null,
    end: null,
    sessions: DEFAULT_SESSION_COUNT,
    up_color: DEFAULT_VOLUME_PROFILE_SETTINGS.upColor,
    down_color: DEFAULT_VOLUME_PROFILE_SETTINGS.downColor,
    show_poc: DEFAULT_VOLUME_PROFILE_SETTINGS.showPoc,
    show_value_area: DEFAULT_VOLUME_PROFILE_SETTINGS.showValueArea,
  },
};

const PROFILE_KINDS: readonly ProfileKind[] = ["off", "visible", "fixed", "session"];
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
  for (const [name, ok] of [["kind", kind], ["rows", rows], ["value_area_pct", area], ["session", session]] as const) {
    if (ok === null || ok === undefined) fallbacks.push(`volume_profile.${name}`);
  }
  const profile: VolumeProfileLayout = {
    kind: kind ?? base.kind,
    rows: rows ?? base.rows,
    value_area_pct: area ?? base.value_area_pct,
    session: session ?? base.session,
    hd: typeof raw.hd === "boolean" ? raw.hd : base.hd,
    start: anchor(raw.start),
    end: anchor(raw.end),
    sessions: optional(raw, "sessions", isSessionCount, base.sessions, fallbacks),
    up_color: optional(raw, "up_color", isHexColor, base.up_color, fallbacks),
    down_color: optional(raw, "down_color", isHexColor, base.down_color, fallbacks),
    show_poc: optional(raw, "show_poc", isBool, base.show_poc, fallbacks),
    show_value_area: optional(raw, "show_value_area", isBool, base.show_value_area, fallbacks),
  };
  // A session profile whose period this client does not know would silently draw nothing.
  if (profile.kind === "session" && !(SESSION_PERIODS as readonly string[]).includes(profile.session)) {
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
  };
  if (fallbacks.length > 0) {
    console.error(
      `chart layout: unusable saved value(s) [${fallbacks.join(", ")}] replaced by the built-in default`,
      raw,
    );
  }
  return { layout, fallbacks };
}

/** The PUT body's layout: `start`/`end` are omitted while null (the table cannot store a null). */
export function layoutForSave(layout: ChartLayout): Record<string, unknown> {
  const { start, end, ...profile } = layout.volume_profile;
  return {
    ...layout,
    volume_profile: { ...profile, ...(start === null ? {} : { start }), ...(end === null ? {} : { end }) },
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
