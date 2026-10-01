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
});
