import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AUTO_ANCHOR_PRESETS } from "./autoAnchor";
import { BUILT_IN_LAYOUT, PROFILE_KINDS, layoutForSave, normalizeLayout } from "./chartLayout";

// The wire of a layout saved before Story 32.7: the profile table has none of the three new keys.
function oldProfile() {
  const { anchor: _a, ib_minutes: _i, letters: _l, ...old } = BUILT_IN_LAYOUT.volume_profile;
  return old;
}
const withProfile = (profile: Record<string, unknown>) => ({ ...BUILT_IN_LAYOUT, volume_profile: profile });

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
