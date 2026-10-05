// Story 32.4: the classic light chart. Three guarantees, each one a thing that silently breaks
// otherwise: the fallback table equals the stylesheet, every drawn colour is legible on the white
// canvas, and chart code reads no colour from anywhere but the chart tokens.
/// <reference types="node" />
// The app tsconfig deliberately types only `vite/client`; this test reads source files off disk
// (vitest hands a `.css?raw` import back as an empty string, so Node's fs is the one honest way to
// read the stylesheet), hence the explicit node types reference above.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import { CHART_PANE_TOKENS, CHART_TOKENS, chartPalette, chartVar } from "./chartTheme";

const here = dirname(fileURLToPath(import.meta.url));
const themeCss = readFileSync(join(here, "..", "..", "theme.css"), "utf8");

/** The `--chart-*` declarations inside theme.css's `.chart-workspace { ... }` block. */
function themeChartTokens(): Record<string, string> {
  const block = /\.chart-workspace\s*\{([^}]*)\}/.exec(themeCss);
  if (!block) throw new Error("theme.css has no .chart-workspace block");
  const out: Record<string, string> = {};
  for (const m of block[1].matchAll(/(--chart-[a-z0-9-]+)\s*:\s*([^;]+);/g)) out[m[1]] = m[2].trim();
  return out;
}

/** WCAG 2.1 relative luminance and contrast ratio for #rrggbb colours. */
function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const lin = (c: number) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}
function contrast(a: string, b: string): number {
  const [la, lb] = [luminance(a), luminance(b)];
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

// Every token the chart draws as a line, bar, label or marker on the canvas. Grid, border and the
// crosshair are deliberately faint guides, as on TradingView, and are not held to the floor; nor is
// `--chart-value-area`, a background fill painted at low alpha behind the profile bars (its contrast
// on its own says nothing about what the eye sees).
const DRAWN: (keyof typeof CHART_TOKENS)[] = [
  "--chart-text",
  "--chart-text-dim",
  "--chart-up",
  "--chart-down",
  "--chart-gap",
  "--chart-marker",
  "--chart-drawing",
  "--chart-poc",
  ...CHART_PANE_TOKENS,
];

describe("chart tokens", () => {
  it("the fallback table and theme.css declare the same tokens with the same values", () => {
    expect(themeChartTokens()).toEqual(CHART_TOKENS);
  });

  it("every drawn colour reads at least 3:1 against the chart background (WCAG 2.1 graphics)", () => {
    const bg = CHART_TOKENS["--chart-bg"];
    const failing = DRAWN.map((t) => [t, contrast(CHART_TOKENS[t], bg)] as const).filter(([, c]) => c < 3);
    expect(failing).toEqual([]);
  });

  it("the gap colour is used by no other token, so a gap can never be mistaken for another mark", () => {
    const gap = CHART_TOKENS["--chart-gap"];
    const others = Object.entries(CHART_TOKENS).filter(([n, v]) => n !== "--chart-gap" && v === gap);
    expect(others).toEqual([]);
  });
});

describe("chartVar", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("reads a token from the chart container when one is mounted, else returns the fallback", () => {
    expect(chartVar("--chart-bg")).toBe(CHART_TOKENS["--chart-bg"]);
    const workspace = document.createElement("div");
    workspace.className = "chart-workspace";
    workspace.style.setProperty("--chart-bg", "#123456");
    document.body.appendChild(workspace);
    expect(chartVar("--chart-bg")).toBe("#123456");
    expect(chartVar("--chart-up")).toBe(CHART_TOKENS["--chart-up"]);
    expect(chartPalette()).toEqual(CHART_PANE_TOKENS.map((t) => CHART_TOKENS[t]));
  });
});

describe("chart drawing code takes its colours from the chart tokens and nothing else", () => {
  // The files that draw the chart, read as source text through Vite's raw imports. Dialog chrome
  // (AlertDialog, IndicatorPicker) and the history page's MetricTile keep the page's dark identity
  // on purpose and are not chart drawing code.
  const files: Record<string, string> = {
    ...import.meta.glob(
      ["./LightweightChart.tsx", "./legend.ts", "./paneColors.ts", "../../pages/ChartPage.tsx"],
      { query: "?raw", import: "default", eager: true },
    ),
    ...import.meta.glob(["./primitives/*.ts", "!./primitives/*.test.ts"], {
      query: "?raw",
      import: "default",
      eager: true,
    }),
  } as Record<string, string>;
  const stripComments = (code: string) => code.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
  const FORBIDDEN: [string, RegExp][] = [
    ["a page semantic token (--color-*)", /--color-/],
    ["a raw VGA tone (--vga-*)", /--vga-/],
    ["a hex colour literal", /#[0-9a-fA-F]{3,8}\b/],
    ["an rgb()/rgba() literal", /\brgba?\(/],
  ];

  it("covers the chart component, the legend, the palette, the page and every primitive", () => {
    const names = Object.keys(files);
    expect(names.some((n) => n.endsWith("LightweightChart.tsx"))).toBe(true);
    expect(names.some((n) => n.endsWith("ChartPage.tsx"))).toBe(true);
    expect(names.filter((n) => n.includes("/primitives/")).length).toBeGreaterThan(3);
  });

  it.each(Object.entries(files))("%s", (_name, source) => {
    const code = stripComments(source);
    const hits = FORBIDDEN.filter(([, re]) => re.test(code)).map(([what]) => what);
    expect(hits).toEqual([]);
  });
});
