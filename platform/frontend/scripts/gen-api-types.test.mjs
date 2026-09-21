// Node's built-in test runner (node:test) -- no extra dependency for testing a Node script
// (Story 15.1 code-review follow-up: this generator had zero test coverage, which is how the
// nullable-field bug below shipped in the first place).
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

const scriptPath = fileURLToPath(new URL("./gen-api-types.mjs", import.meta.url));

function generate(schemas) {
  const dir = mkdtempSync(join(tmpdir(), "gen-api-types-test-"));
  const inputPath = join(dir, "openapi.json");
  const outputPath = join(dir, "schema.ts");
  writeFileSync(inputPath, JSON.stringify({ components: { schemas } }));
  try {
    execFileSync("node", [scriptPath, inputPath, outputPath]);
    return readFileSync(outputPath, "utf-8");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test("required and optional primitive fields", () => {
  const out = generate({
    Model: {
      type: "object",
      required: ["a"],
      properties: { a: { type: "string" }, b: { type: "integer" } },
    },
  });
  assert.match(out, /export interface Model \{\n {2}a: string;\n {2}b\?: number;\n\}/);
});

test("nullable field (Pydantic Optional[T]/T | None -> anyOf [T, null]) stays T | null, not unknown", () => {
  const out = generate({
    Model: {
      type: "object",
      required: ["note"],
      properties: { note: { anyOf: [{ type: "string" }, { type: "null" }] } },
    },
  });
  assert.match(out, /note: string \| null;/);
  assert.doesNotMatch(out, /unknown/);
});

test("array and $ref fields", () => {
  const out = generate({
    Item: { type: "object", required: ["id"], properties: { id: { type: "string" } } },
    Model: {
      type: "object",
      required: ["items"],
      properties: { items: { type: "array", items: { $ref: "#/components/schemas/Item" } } },
    },
  });
  assert.match(out, /items: Item\[\];/);
});

test("HTTPValidationError/ValidationError schemas are skipped", () => {
  const out = generate({
    HTTPValidationError: { type: "object", properties: {} },
    ValidationError: { type: "object", properties: {} },
    Model: { type: "object", properties: {} },
  });
  assert.doesNotMatch(out, /HTTPValidationError/);
  assert.doesNotMatch(out, /export interface ValidationError/);
  assert.match(out, /export interface Model/);
});
