import { afterEach, describe, expect, it, vi } from "vitest";

import { BUILT_IN_LAYOUT, normalizeLayout } from "./chartLayout";
import { DEFAULT_SESSION_COUNT, MAX_SESSIONS } from "./sessionProfile";
import { DEFAULT_VOLUME_PROFILE_SETTINGS } from "./volumeProfile";

// A layout as a server that predates DW-151/153 stores it: no sessions, colours or toggles.
const { sessions, up_color, down_color, show_poc, show_value_area, ...legacyProfile } = BUILT_IN_LAYOUT.volume_profile;
const withProfile = (profile: Record<string, unknown>) => ({ ...BUILT_IN_LAYOUT, volume_profile: profile });

afterEach(() => vi.restoreAllMocks());

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
