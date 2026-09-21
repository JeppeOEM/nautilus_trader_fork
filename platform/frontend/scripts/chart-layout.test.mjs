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
