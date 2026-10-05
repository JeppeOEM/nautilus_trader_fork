import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AUTO_ANCHOR_PRESETS } from "./autoAnchor";
import {
  BUILT_IN_LAYOUT,
  FOOTPRINT_DEFAULT_IMBALANCE_RATIO,
  FOOTPRINT_MODES,
  MAX_FOOTPRINT_ROW_TICKS,
  PROFILE_KINDS,
  layoutForSave,
  normalizeLayout,
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
