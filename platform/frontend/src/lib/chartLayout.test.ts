import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AUTO_ANCHOR_PRESETS } from "./autoAnchor";
import {
  BUILT_IN_LAYOUT,
  DEFAULT_COMPARE,
  DEFAULT_PRICE_SCALE,
  DERIVATIVE_KEYS,
  DERIVATIVE_OUTPUTS,
  FOOTPRINT_DEFAULT_IMBALANCE_RATIO,
  LIQUIDATION_MEASURES,
  FOOTPRINT_MODES,
  MAX_FOOTPRINT_ROW_TICKS,
  PROFILE_KINDS,
  VOLUME_COLOR_MODES,
  layoutForSave,
  normalizeLayout,
  sameLayout,
} from "./chartLayout";
import { DEFAULT_SESSION_COUNT, MAX_SESSIONS } from "./sessionProfile";
import { DEFAULT_VOLUME_PROFILE_SETTINGS } from "./volumeProfile";

// The wire of a layout saved before Story 32.7: the profile table has none of the three new keys.
function oldProfile() {
  const { anchor: _a, ib_minutes: _i, letters: _l, ...old } = BUILT_IN_LAYOUT.volume_profile;
  return old;
}
const withProfile = (profile: Record<string, unknown>) => ({ ...BUILT_IN_LAYOUT, volume_profile: profile });
// A layout as a server that predates DW-151/153 stores it: no sessions, colours or toggles.
const { sessions, up_color, down_color, show_poc, show_value_area, ...legacyProfile } = BUILT_IN_LAYOUT.volume_profile;

let errors: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  errors = vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  errors.mockRestore();
});

describe("the volume profile layout's Story 32.7 keys", () => {
  it("knows the two new session-type kinds, mirrored by the server's PROFILE_KINDS", () => {
    expect(PROFILE_KINDS).toEqual(["off", "visible", "fixed", "session", "auto", "tpo"]);
  });

  it("loads a table saved before them unchanged, with the defaults and no complaint", () => {
    const { layout, fallbacks } = normalizeLayout(withProfile({ ...oldProfile(), kind: "session", session: "weekly" }));

    expect(fallbacks).toEqual([]);
    expect(errors).not.toHaveBeenCalled();
    expect(layout.volume_profile).toMatchObject({ kind: "session", session: "weekly", anchor: "auto", ib_minutes: 60, letters: false });
  });

  it("round-trips an auto and a tpo profile with their settings", () => {
    for (const kind of ["auto", "tpo"] as const) {
      const profile = { ...BUILT_IN_LAYOUT.volume_profile, kind, anchor: "highest_high", ib_minutes: 90, letters: true };
      const { layout, fallbacks } = normalizeLayout(withProfile(profile));

      expect(fallbacks).toEqual([]);
      expect(layout.volume_profile).toMatchObject({ kind, anchor: "highest_high", ib_minutes: 90, letters: true });
      expect(layoutForSave(layout).volume_profile).toMatchObject({ kind, anchor: "highest_high", ib_minutes: 90, letters: true });
    }
  });

  it("accepts every anchor preset", () => {
    for (const anchor of AUTO_ANCHOR_PRESETS) {
      const { fallbacks } = normalizeLayout(withProfile({ ...BUILT_IN_LAYOUT.volume_profile, kind: "auto", anchor }));
      expect(fallbacks).toEqual([]);
    }
  });

  it("falls back, loudly and once, for an anchor, length or letters value it cannot use", () => {
    const { layout, fallbacks } = normalizeLayout(
      withProfile({ ...BUILT_IN_LAYOUT.volume_profile, kind: "tpo", anchor: "day", ib_minutes: 0, letters: "yes" }),
    );

    expect(fallbacks).toEqual(["volume_profile.anchor", "volume_profile.ib_minutes", "volume_profile.letters"]);
    expect(layout.volume_profile).toMatchObject({ anchor: "auto", ib_minutes: 60, letters: false });
    expect(errors).toHaveBeenCalledTimes(1);
  });

  it("checks a TPO's session period like a session profile's", () => {
    const { layout, fallbacks } = normalizeLayout(withProfile({ ...BUILT_IN_LAYOUT.volume_profile, kind: "tpo", session: "fortnight" }));

    expect(fallbacks).toContain("volume_profile.session");
    expect(layout.volume_profile.session).toBe("daily");
  });
});

describe("the footprint layout table (Story 32.8)", () => {
  const { footprint: _f, ...preFootprint } = BUILT_IN_LAYOUT;
  const withFootprint = (footprint: unknown) => ({ ...BUILT_IN_LAYOUT, footprint });

  it("has the server's modes, default ratio and row-size cap (views/preferences.py mirrors them)", () => {
    expect(FOOTPRINT_MODES).toEqual(["bid_ask", "delta", "volume"]);
    expect(FOOTPRINT_DEFAULT_IMBALANCE_RATIO).toBe(3);
    expect(MAX_FOOTPRINT_ROW_TICKS).toBe(1_000_000);
    expect(BUILT_IN_LAYOUT.footprint).toEqual({ on: false, row_ticks: 0, mode: "bid_ask", imbalance_ratio: 3, text: true });
  });

  it("loads a layout saved before it with Footprint off, silently", () => {
    const { layout, fallbacks } = normalizeLayout(preFootprint);

    expect(fallbacks).toEqual([]);
    expect(errors).not.toHaveBeenCalled();
    expect(layout.footprint).toEqual(BUILT_IN_LAYOUT.footprint);
  });

  it("round-trips every setting, colours included", () => {
    const footprint = { on: true, row_ticks: 5, mode: "delta", imbalance_ratio: 2.5, text: false, buy_color: "#00ff00", sell_color: "#ff0000" };
    const { layout, fallbacks } = normalizeLayout(withFootprint(footprint));

    expect(fallbacks).toEqual([]);
    expect(layout.footprint).toEqual(footprint);
    expect(layoutForSave(layout).footprint).toEqual(footprint);
  });

  it("saves no colour key while the colours are unset", () => {
    expect(layoutForSave(BUILT_IN_LAYOUT).footprint).toEqual(BUILT_IN_LAYOUT.footprint);
    expect(Object.keys(layoutForSave(BUILT_IN_LAYOUT).footprint as object)).not.toContain("buy_color");
  });

  it("falls back field by field, loudly and once, for values it cannot use", () => {
    const bad = { on: "yes", row_ticks: -1, mode: "bidask", imbalance_ratio: 0.5, text: 1, buy_color: "", sell_color: "#ff0000" };
    const { layout, fallbacks } = normalizeLayout(withFootprint(bad));

    expect(fallbacks).toEqual([
      "footprint.on",
      "footprint.row_ticks",
      "footprint.mode",
      "footprint.imbalance_ratio",
      "footprint.text",
      "footprint.buy_color",
    ]);
    expect(layout.footprint).toEqual({ ...BUILT_IN_LAYOUT.footprint, sell_color: "#ff0000" });
    expect(errors).toHaveBeenCalledTimes(1);
  });

  it("keeps the good fields of a partly bad table", () => {
    const { layout, fallbacks } = normalizeLayout(withFootprint({ ...BUILT_IN_LAYOUT.footprint, on: true, row_ticks: 2.5 }));

    expect(fallbacks).toEqual(["footprint.row_ticks"]);
    expect(layout.footprint).toMatchObject({ on: true, row_ticks: 0 });
  });

  it("falls back to the defaults for a footprint that is not a table", () => {
    const { layout, fallbacks } = normalizeLayout(withFootprint(true));

    expect(fallbacks).toEqual(["footprint"]);
    expect(layout.footprint).toEqual(BUILT_IN_LAYOUT.footprint);
  });
});

describe("normalizeLayout's volume profile settings (DW-151/153)", () => {
  it("defaults the built-in layout's profile to the settings panel's defaults", () => {
    expect({ sessions, up_color, down_color, show_poc, show_value_area }).toEqual({
      sessions: DEFAULT_SESSION_COUNT,
      up_color: DEFAULT_VOLUME_PROFILE_SETTINGS.upColor,
      down_color: DEFAULT_VOLUME_PROFILE_SETTINGS.downColor,
      show_poc: DEFAULT_VOLUME_PROFILE_SETTINGS.showPoc,
      show_value_area: DEFAULT_VOLUME_PROFILE_SETTINGS.showValueArea,
    });
  });

  it("reads a layout saved before the keys existed with the defaults, reporting no fallback", () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});

    const { layout, fallbacks } = normalizeLayout(withProfile(legacyProfile));

    expect(fallbacks).toEqual([]);
    expect(errors).not.toHaveBeenCalled();
    expect(layout.volume_profile).toEqual(BUILT_IN_LAYOUT.volume_profile);
  });

  it("keeps saved values", () => {
    const saved = { ...legacyProfile, sessions: MAX_SESSIONS, up_color: "#ABCDEF", down_color: "#010203", show_poc: false, show_value_area: false };

    const { layout, fallbacks } = normalizeLayout(withProfile(saved));

    expect(fallbacks).toEqual([]);
    expect(layout.volume_profile).toMatchObject({ sessions: 10, up_color: "#ABCDEF", down_color: "#010203", show_poc: false, show_value_area: false });
  });

  it.each([
    ["sessions", 0],
    ["sessions", MAX_SESSIONS + 1],
    ["sessions", 2.5],
    ["up_color", "red"],
    ["up_color", "#fff"],
    ["down_color", 255],
    ["show_poc", "yes"],
    ["show_value_area", 1],
  ])("falls back for an unusable %s (%s), naming it", (key, value) => {
    vi.spyOn(console, "error").mockImplementation(() => {});

    const { layout, fallbacks } = normalizeLayout(withProfile({ ...legacyProfile, [key]: value }));

    expect(fallbacks).toEqual([`volume_profile.${key}`]);
    expect(layout.volume_profile[key as keyof typeof layout.volume_profile]).toEqual(
      BUILT_IN_LAYOUT.volume_profile[key as keyof typeof BUILT_IN_LAYOUT.volume_profile],
    );
  });
});

describe("the derivatives layout table (Story 33.5)", () => {
  const { derivatives: _d, ...preDerivatives } = BUILT_IN_LAYOUT;
  const withDerivatives = (derivatives: unknown) => ({ ...BUILT_IN_LAYOUT, derivatives });

  it("has the server's keys, outputs and measures (views/preferences.py mirrors them)", () => {
    expect(DERIVATIVE_KEYS).toEqual(["oi", "funding", "basis", "mark_index", "liquidations"]);
    expect(DERIVATIVE_OUTPUTS.basis).toEqual(["mark_index", "mark_last"]);
    expect(LIQUIDATION_MEASURES).toEqual(["size", "notional"]);
    expect(BUILT_IN_LAYOUT.derivatives.liquidations).toEqual({ on: false, measure: "size", markers: true });
  });

  it("loads a layout saved before it with every entry off, silently", () => {
    const { layout, fallbacks } = normalizeLayout(preDerivatives);

    expect(fallbacks).toEqual([]);
    expect(errors).not.toHaveBeenCalled();
    expect(layout.derivatives).toEqual(BUILT_IN_LAYOUT.derivatives);
  });

  it("round-trips on/off, styles and the liquidation options, and writes the table whole", () => {
    const derivatives = {
      ...BUILT_IN_LAYOUT.derivatives,
      oi: { on: true, style: { oi: { color: "#26a69a", line_width: 2, line_style: "dashed" } } },
      liquidations: { on: true, measure: "notional", markers: false, style: { liquidations: { up_color: "#00ff00" } } },
    };
    const { layout, fallbacks } = normalizeLayout(withDerivatives(derivatives));

    expect(fallbacks).toEqual([]);
    expect(layout.derivatives).toEqual(derivatives);
    expect(layoutForSave(layout).derivatives).toEqual(derivatives);
    expect(sameLayout(layout, { ...layout, derivatives: { ...layout.derivatives, oi: { on: false } } })).toBe(false);
  });

  it("falls back by name for a bad field, keeping the rest of the entry", () => {
    const bad = {
      ...BUILT_IN_LAYOUT.derivatives,
      oi: { on: "yes", style: { oi: { color: "#26a69a", line_width: 9 }, open: {} } },
      liquidations: { on: true, measure: "usd", markers: true },
      screener: { on: true },
    };
    const { layout, fallbacks } = normalizeLayout(withDerivatives(bad));

    expect(fallbacks).toEqual([
      "derivatives.screener",
      "derivatives.oi.on",
      "derivatives.oi.style.oi.line_width",
      "derivatives.oi.style.open",
      "derivatives.liquidations.measure",
    ]);
    expect(layout.derivatives.oi).toEqual({ on: false, style: { oi: { color: "#26a69a" } } });
    expect(layout.derivatives.liquidations).toEqual({ on: true, measure: "size", markers: true });
    expect(errors).toHaveBeenCalledTimes(1);
  });

  it("falls back to every entry off for a table that is not one", () => {
    const { layout, fallbacks } = normalizeLayout(withDerivatives(7));

    expect(fallbacks).toEqual(["derivatives"]);
    expect(layout.derivatives).toEqual(BUILT_IN_LAYOUT.derivatives);
  });
});

describe("the volume colour mode (Story 33.6)", () => {
  const { volume_color_by: _v, ...preVolumeColor } = BUILT_IN_LAYOUT;

  it("has the server's modes and defaults to direction (views/preferences.py mirrors them)", () => {
    expect(VOLUME_COLOR_MODES).toEqual(["direction", "delta"]);
    expect(BUILT_IN_LAYOUT.volume_color_by).toBe("direction");
  });

  it("loads a layout saved before it colouring by direction, silently", () => {
    const { layout, fallbacks } = normalizeLayout(preVolumeColor);

    expect(fallbacks).toEqual([]);
    expect(errors).not.toHaveBeenCalled();
    expect(layout.volume_color_by).toBe("direction");
  });

  it("round-trips delta through the save, and a change is a change", () => {
    const { layout, fallbacks } = normalizeLayout({ ...BUILT_IN_LAYOUT, volume_color_by: "delta" });

    expect(fallbacks).toEqual([]);
    expect(layout.volume_color_by).toBe("delta");
    expect(layoutForSave(layout).volume_color_by).toBe("delta");
    expect(sameLayout(layout, { ...layout, volume_color_by: "direction" })).toBe(false);
  });

  it("falls back to direction by name for a mode it does not know", () => {
    const { layout, fallbacks } = normalizeLayout({ ...BUILT_IN_LAYOUT, volume_color_by: "buy_sell" });

    expect(fallbacks).toEqual(["volume_color_by"]);
    expect(layout.volume_color_by).toBe("direction");
    expect(errors).toHaveBeenCalledTimes(1);
  });
});

describe("the chart type, price scale and compare keys (Story 33.9)", () => {
  const { chart_type: _t, price_scale: _p, compare: _c, ...pre339 } = BUILT_IN_LAYOUT;
  const IID = "BTCUSDT-LINEAR.BYBIT";

  it("loads a layout saved before them with candles, the default scale and no compare, silently", () => {
    const { layout, fallbacks } = normalizeLayout(pre339);

    expect(fallbacks).toEqual([]);
    expect(errors).not.toHaveBeenCalled();
    expect(layout.chart_type).toBe("candles");
    expect(layout.price_scale).toEqual({ mode: "normal", auto_scale: true, invert: false });
    expect(layout.compare).toEqual({ symbols: [], spread: false });
    expect(DEFAULT_PRICE_SCALE).toEqual(layout.price_scale);
    expect(DEFAULT_COMPARE).toEqual(layout.compare);
  });

  it("round-trips every setting through the save, and each change is a change", () => {
    const saved = {
      ...BUILT_IN_LAYOUT,
      chart_type: "heikin_ashi",
      price_scale: { mode: "log", auto_scale: false, invert: true },
      compare: { symbols: ["BTC-USD-PERP.HYPERLIQUID", "ETHUSDT-LINEAR.BYBIT"], spread: true },
    };
    const { layout, fallbacks } = normalizeLayout(saved, IID);

    expect(fallbacks).toEqual([]);
    expect(layoutForSave(layout)).toMatchObject({
      chart_type: "heikin_ashi",
      price_scale: saved.price_scale,
      compare: saved.compare,
    });
    expect(sameLayout(layout, { ...layout, chart_type: "bars" })).toBe(false);
    expect(sameLayout(layout, { ...layout, price_scale: { ...layout.price_scale, invert: false } })).toBe(false);
    expect(sameLayout(layout, { ...layout, compare: { ...layout.compare, symbols: ["BTC-USD-PERP.HYPERLIQUID"] } })).toBe(false);
  });

  it("falls back by name for an unknown type, mode, flag or table", () => {
    const { layout, fallbacks } = normalizeLayout({
      ...BUILT_IN_LAYOUT,
      chart_type: "renko",
      price_scale: { mode: "x", auto_scale: 1, invert: false, extra: true },
      compare: 5,
    });

    expect(fallbacks).toEqual(["chart_type", "price_scale.extra", "price_scale.mode", "price_scale.auto_scale", "compare"]);
    expect(layout.chart_type).toBe("candles");
    expect(layout.price_scale).toEqual(DEFAULT_PRICE_SCALE);
    expect(layout.compare).toEqual(DEFAULT_COMPARE);
    expect(errors).toHaveBeenCalledTimes(1);
  });

  it("drops an unusable, repeated or fourth compare symbol by name", () => {
    const symbols = ["A.BYBIT", "nosuffix", "A.BYBIT", "B.BYBIT", "C.BYBIT", "D.BYBIT"];
    const { layout, fallbacks } = normalizeLayout({ ...BUILT_IN_LAYOUT, compare: { symbols, spread: "on" } });

    expect(layout.compare).toEqual({ symbols: ["A.BYBIT", "B.BYBIT", "C.BYBIT"], spread: false });
    expect(fallbacks).toEqual(["compare.symbols.1", "compare.symbols.2", "compare.symbols.5", "compare.spread"]);
  });

  it("leaves out a template's compare symbol equal to the coin being opened, quietly", () => {
    const template = { ...BUILT_IN_LAYOUT, compare: { symbols: [IID, "BTC-USD-PERP.HYPERLIQUID"], spread: true } };
    const { layout, fallbacks } = normalizeLayout(template, IID);

    expect(fallbacks).toEqual([]);
    expect(errors).not.toHaveBeenCalled();
    expect(layout.compare).toEqual({ symbols: ["BTC-USD-PERP.HYPERLIQUID"], spread: true });
    expect(template.compare.symbols).toEqual([IID, "BTC-USD-PERP.HYPERLIQUID"]);
  });
});

describe("the drawings_hidden key (Story 33.10)", () => {
  const { drawings_hidden: _h, ...pre3310 } = BUILT_IN_LAYOUT;

  it("loads a layout saved before it with the drawings shown, silently", () => {
    const { layout, fallbacks } = normalizeLayout(pre3310);

    expect(fallbacks).toEqual([]);
    expect(errors).not.toHaveBeenCalled();
    expect(layout.drawings_hidden).toBe(false);
  });

  it("round-trips through the save, and a change is a change", () => {
    const { layout, fallbacks } = normalizeLayout({ ...BUILT_IN_LAYOUT, drawings_hidden: true });

    expect(fallbacks).toEqual([]);
    expect(layoutForSave(layout)).toMatchObject({ drawings_hidden: true });
    expect(sameLayout(layout, { ...layout, drawings_hidden: false })).toBe(false);
  });

  it("falls back to shown by name for a non-boolean", () => {
    const { layout, fallbacks } = normalizeLayout({ ...BUILT_IN_LAYOUT, drawings_hidden: "yes" });

    expect(fallbacks).toEqual(["drawings_hidden"]);
    expect(layout.drawings_hidden).toBe(false);
    expect(errors).toHaveBeenCalledTimes(1);
  });
});

describe("the time zone, session breaks, countdown and last-price keys (Story 33.12)", () => {
  const { time_zone: _z, session_breaks: _s, bar_countdown: _c, last_price: _l, ...pre3312 } = BUILT_IN_LAYOUT;

  it("defaults to UTC, no session breaks, the countdown on and both last-price parts shown", () => {
    expect(BUILT_IN_LAYOUT.time_zone).toBe("utc");
    expect(BUILT_IN_LAYOUT.session_breaks).toBe(false);
    expect(BUILT_IN_LAYOUT.bar_countdown).toBe(true);
    expect(BUILT_IN_LAYOUT.last_price).toEqual({ line: true, label: true });
  });

  it("loads a layout saved before them with the defaults, silently", () => {
    const { layout, fallbacks } = normalizeLayout(pre3312);

    expect(fallbacks).toEqual([]);
    expect(errors).not.toHaveBeenCalled();
    expect(layout).toEqual(BUILT_IN_LAYOUT);
  });

  it("keeps every valid value and round-trips it through the save", () => {
    const saved = {
      ...BUILT_IN_LAYOUT,
      time_zone: "local",
      session_breaks: true,
      bar_countdown: false,
      last_price: { line: false, label: true },
    };
    const { layout, fallbacks } = normalizeLayout(saved);

    expect(fallbacks).toEqual([]);
    expect(layoutForSave(layout)).toMatchObject({
      time_zone: "local",
      session_breaks: true,
      bar_countdown: false,
      last_price: { line: false, label: true },
    });
    expect(normalizeLayout({ ...BUILT_IN_LAYOUT, time_zone: "exchange" }).layout.time_zone).toBe("exchange");
  });

  it("falls back by name for an unknown zone, a non-boolean flag and a malformed last-price table", () => {
    const { layout, fallbacks } = normalizeLayout({
      ...BUILT_IN_LAYOUT,
      time_zone: "Europe/Berlin",
      session_breaks: "yes",
      bar_countdown: 1,
      last_price: { line: "on", label: false, colour: "red", constructor: true },
    });

    // `constructor` too: an inherited name is no last-price key.
    expect(fallbacks).toEqual(["time_zone", "session_breaks", "bar_countdown", "last_price.colour", "last_price.constructor", "last_price.line"]);
    expect(layout.time_zone).toBe("utc");
    expect(layout.session_breaks).toBe(false);
    expect(layout.bar_countdown).toBe(true);
    expect(layout.last_price).toEqual({ line: true, label: false });
    expect(errors).toHaveBeenCalledTimes(1);
  });

  it("replaces a last-price value that is not a table", () => {
    const { layout, fallbacks } = normalizeLayout({ ...BUILT_IN_LAYOUT, last_price: true });

    expect(fallbacks).toEqual(["last_price"]);
    expect(layout.last_price).toEqual({ line: true, label: true });
  });
});
