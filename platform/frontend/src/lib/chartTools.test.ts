import { describe, expect, it } from "vitest";

import { DRAWING_GROUPS, TOOL_GROUPS, groupOfTool, shownTool, toolDef } from "./chartTools";

describe("chart tool groups", () => {
  it("declares every tool in exactly one group, with unique ids and accessible names", () => {
    const tools = TOOL_GROUPS.flatMap((g) => g.tools);
    expect(new Set(tools.map((t) => t.id)).size).toBe(tools.length);
    expect(new Set(tools.map((t) => t.ariaLabel)).size).toBe(tools.length);
    expect(new Set(TOOL_GROUPS.map((g) => g.id)).size).toBe(TOOL_GROUPS.length);
  });

  it("puts Long and Short in one group", () => {
    expect(groupOfTool("long")).toBe(groupOfTool("short"));
    expect(groupOfTool("long")!.label).toBe("Projection");
  });

  it("shows the remembered tool, else the group's first (also for a remembered id no longer in it)", () => {
    const lines = DRAWING_GROUPS.find((g) => g.id === "lines")!;
    expect(shownTool(lines, {}).id).toBe("trendline");
    expect(shownTool(lines, { lines: "hline" }).id).toBe("hline");
    expect(shownTool(lines, { lines: "fib" }).id).toBe("trendline");
  });

  it("looks a tool's definition up by id", () => {
    expect(toolDef("frvp")).toMatchObject({ ariaLabel: "Fixed range volume profile tool", candlesOnly: true });
  });
});
