// Story 32.6: a chart setting lives on the server (the coin's layout, indicators, drawings), never in
// the browser. Source-level guard, like chartTheme.test.ts: the page and every hook may read no
// `chart-*` localStorage key and write none, except the two one-time imports of what older versions
// stored, each named here with exactly the keys it may read.
import { describe, expect, it } from "vitest";

const files = {
  ...import.meta.glob(["./*.ts", "!./*.test.ts"], { query: "?raw", import: "default", eager: true }),
  ...import.meta.glob(["../pages/ChartPage.tsx"], { query: "?raw", import: "default", eager: true }),
  // Story 33.12: the watchlist rail, the symbol search, the shortcut sheet, the compare picker and the
  // time and shortcut modules -- the watchlist is server-side, fullscreen is never kept anywhere.
  ...import.meta.glob(
    [
      "../components/chart/{WatchlistRail,SymbolSearch,ShortcutSheet,CompareControl}.tsx",
      "../lib/{time,shortcuts}.ts",
    ],
    { query: "?raw", import: "default", eager: true },
  ),
} as Record<string, string>;

/** Story 33.12's files: they touch no browser storage at all. */
const STORY_33_12_FILES = [
  "./useFullscreen.ts",
  "./useWatchlist.ts",
  "../components/chart/WatchlistRail.tsx",
  "../components/chart/SymbolSearch.tsx",
  "../components/chart/ShortcutSheet.tsx",
  "../components/chart/CompareControl.tsx",
  "../lib/time.ts",
  "../lib/shortcuts.ts",
];

const stripComments = (code: string) => code.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");

/** The only files that may name a `chart-*` browser key, and the key prefixes each may name. */
const LEGACY_IMPORTS: Record<string, string[]> = {
  "./useChartDrawings.ts": ["chart-hlines:"],
  "./useChartLayout.ts": ["chart-timeframe:", "chart-volume:"],
};

describe("no chart setting is kept in the browser", () => {
  it("covers the page and the hooks, including both one-time imports", () => {
    const names = Object.keys(files);
    expect(names).toContain("../pages/ChartPage.tsx");
    expect(names).toContain("./useChartLayout.ts");
    expect(names).toContain("./useChartDrawings.ts");
  });

  it.each(Object.entries(files))("%s reads only its allow-listed legacy keys and writes none", (name, source) => {
    const code = stripComments(source);
    const keys = [...code.matchAll(/["'`](chart-[a-z-]+:)/g)].map((m) => m[1]);
    expect([...new Set(keys)].sort()).toEqual([...(LEGACY_IMPORTS[name] ?? [])].sort());
    expect(code).not.toMatch(/localStorage\s*\.\s*setItem/);
    expect(code).not.toMatch(/sessionStorage/);
  });

  it("keeps the watchlist, fullscreen and the Story 33.12 settings out of browser storage", () => {
    for (const name of STORY_33_12_FILES) {
      expect(Object.keys(files)).toContain(name);
      expect(stripComments(files[name])).not.toMatch(/localStorage|sessionStorage|indexedDB/);
    }
  });

  it("the allow-listed files touch the keys only to read and remove them", () => {
    for (const name of Object.keys(LEGACY_IMPORTS)) {
      const calls = [...stripComments(files[name]).matchAll(/localStorage\s*\.\s*(\w+)/g)].map((m) => m[1]);
      expect(calls.length).toBeGreaterThan(0);
      expect(new Set(calls)).toEqual(new Set(["getItem", "removeItem"]));
    }
  });
});
