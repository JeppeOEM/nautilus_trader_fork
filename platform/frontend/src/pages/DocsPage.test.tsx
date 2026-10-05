import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import DocsPage from "./DocsPage";

describe("DocsPage", () => {
  it("renders the indicators home with a known section", () => {
    render(
      <MemoryRouter initialEntries={["/docs"]}>
        <DocsPage />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: "Indicator Reference" })).toBeInTheDocument();
    expect(screen.getAllByText("Microprice").length).toBeGreaterThan(0);
  });

  it("renders a KB detail page's ported content, including the architecture diagram", () => {
    render(
      <MemoryRouter initialEntries={["/docs/kb/architecture"]}>
        <DocsPage />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: "System Architecture" })).toBeInTheDocument();
    expect(screen.getByText("Module map")).toBeInTheDocument();
    expect(document.querySelector("figure.diagram svg")).not.toBeNull();
  });

  it("documents how a chart gap is drawn and what the cap means (Story 32.1)", () => {
    render(
      <MemoryRouter initialEntries={["/docs/kb/chart-gaps"]}>
        <DocsPage />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: "Gaps: How Missing Data Is Drawn" })).toBeInTheDocument();
    expect(screen.getByText(/at most/).textContent).toMatch(/720 slots/);
    expect(screen.getAllByText(/\(compressed\)/).length).toBeGreaterThan(0);
  });

  it("documents the drawing tools and where drawings are saved (Story 32.5)", () => {
    render(
      <MemoryRouter initialEntries={["/docs/kb/chart-drawings"]}>
        <DocsPage />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: /Drawings: Fibonacci, Long\/Short/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Where it is saved" })).toBeInTheDocument();
    expect(screen.getAllByText(/chart_drawings\.toml/).length).toBeGreaterThan(0);
  });

  it("documents the footprint's modes, imbalances and historical-only limit (Story 32.8)", () => {
    render(
      <MemoryRouter initialEntries={["/docs/kb/chart-footprint"]}>
        <DocsPage />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: /Volume Footprint/ })).toBeInTheDocument();
    for (const name of ["Display modes", "Imbalances", "Known limits"]) {
      expect(screen.getByRole("heading", { name })).toBeInTheDocument();
    }
    expect(screen.getByText(/Historical only\./)).toBeInTheDocument();
  });

  it("lists all nine profile tools with what each anchors to and counts (Story 32.7)", () => {
    render(
      <MemoryRouter initialEntries={["/docs/kb/chart-profiles"]}>
        <DocsPage />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: "Volume Profiles: the Nine Tools" })).toBeInTheDocument();
    const table = screen.getByRole("heading", { name: "The nine tools" }).closest(".sec")!.querySelector("table")!;
    const tools = [...table.querySelectorAll("tbody tr")].filter((row) => row.querySelector("td")).map((row) => row.querySelector("td")!.textContent);
    expect(tools).toEqual([
      "FRVP Fixed range",
      "VRVP Visible range",
      "SVP Session",
      "SVP HD",
      "PVP Periodic",
      "Auto Anchored VP",
      "Anchored VP (AVP)",
      "Anchored VWAP (AVWAP)",
      "TPO Time Price Opportunity",
    ]);
    expect(screen.getAllByText(/buildVolumeProfile/).length).toBeGreaterThan(0);
  });
});
