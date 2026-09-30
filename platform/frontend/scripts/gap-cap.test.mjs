import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

// Story 32.1: the per-hole gap cap is mirrored by value in both languages (a seam run and a
// backend run must compress identically). Each side's own test pins 720; this one reads both
// definitions, so changing one side alone fails here instead of drifting silently.
function capIn(relativePath, pattern) {
  const source = readFileSync(new URL(relativePath, import.meta.url), "utf8");
  const match = source.match(pattern);
  assert.ok(match, `MAX_GAP_ROWS_PER_GAP not found in ${relativePath}`);
  return Number(match[1]);
}

test("the frontend's gap cap is the backend's", () => {
  const backend = capIn("../../views/chart_series.py", /^MAX_GAP_ROWS_PER_GAP = (\d+)$/m);
  const frontend = capIn("../src/lib/gaps.ts", /^export const MAX_GAP_ROWS_PER_GAP = (\d+);$/m);
  assert.equal(frontend, backend);
});
