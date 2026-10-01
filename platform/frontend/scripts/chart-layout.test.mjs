import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

// jsdom has no layout engine, so a collapsed chart can't be observed in Vitest. Regression:
// .chart-workspace is a flex row and its .term-box child had no flex:1, so the chart box
// shrank to a ~30px sliver and nothing rendered. Pin the rule that prevents it.
test("chart box fills the workspace row next to the tool rail", () => {
  const css = readFileSync(new URL("../src/index.css", import.meta.url), "utf8");
  const rule = css.match(/\.chart-workspace\s*>\s*\.term-box\s*\{([^}]*)\}/)?.[1] ?? "";
  assert.match(rule, /flex:\s*1/);
  assert.match(rule, /min-width:\s*0/);
});

// Story 32.2: panes grow the page, so nothing between the chart container and <body> may clip
// or fix its height -- the operator scrolls the page. jsdom has no layout, so pin the CSS.
const CHART_ANCESTORS = new Set(["html", "body", "#root", ".chart-workspace", ".term-box"]);

test("no ancestor of the chart clips overflow or fixes a height", () => {
  for (const file of ["../src/index.css", "../src/theme.css"]) {
    const sheet = readFileSync(new URL(file, import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    for (const [, selectorList, body] of sheet.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const names = selectorList.split(",").map((n) => n.trim());
      if (!names.some((n) => CHART_ANCESTORS.has(n))) continue;
      // auto/scroll would make the ancestor the scroll container instead of the page.
      assert.doesNotMatch(
        body,
        /overflow(-[xy])?:[^;]*\b(hidden|clip|auto|scroll)\b/,
        `${selectorList.trim()} must not clip or scroll overflow`,
      );
      assert.doesNotMatch(body, /(^|[;\s])(max-)?height:/, `${selectorList.trim()} must not set a fixed height`);
    }
  }
});
