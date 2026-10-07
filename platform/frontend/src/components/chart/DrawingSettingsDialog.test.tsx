import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Drawing, RectDrawing, TextDrawing, TrendlineDrawing } from "../../lib/drawings";
import DrawingSettingsDialog from "./DrawingSettingsDialog";

const A = { time: 100, price: 100 };
const B = { time: 200, price: 110 };

function open(drawing: Drawing) {
  const onApply = vi.fn();
  const onClose = vi.fn();
  render(<DrawingSettingsDialog drawing={drawing} precision={null} onApply={onApply} onRemove={vi.fn()} onClose={onClose} />);
  return { onApply, onClose };
}

describe("DrawingSettingsDialog line form (Story 33.10)", () => {
  const line: TrendlineDrawing = { kind: "trendline", id: "trendline-1", anchors: [A, B] };

  it("writes back a drawing applied unchanged exactly as it was: no colour, width or style added", () => {
    const { onApply, onClose } = open(line);
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledWith(line);
    expect(Object.keys(onApply.mock.calls[0][0] as object).sort()).toEqual(["anchors", "id", "kind"]);
    expect(onClose).toHaveBeenCalled();
  });

  it("writes only what the operator changed", () => {
    const { onApply } = open(line);
    fireEvent.change(screen.getByLabelText("Line width"), { target: { value: "3" } });
    fireEvent.change(screen.getByLabelText("Line style"), { target: { value: "dashed" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledWith({ ...line, line_width: 3, line_style: "dashed" });
  });

  it("keeps a stored width and style, and sets the colour picked", () => {
    const styled: TrendlineDrawing = { ...line, line_width: 2, line_style: "dotted", color: "#112233" };
    const { onApply } = open(styled);
    fireEvent.change(screen.getByLabelText("Line colour"), { target: { value: "#445566" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledWith({ ...styled, color: "#445566" });
  });

  it("writes a rectangle's off-grid opacity back unchanged, and the step picked", () => {
    const rect: RectDrawing = { kind: "rect", id: "rect-1", anchors: [A, B], fill_opacity: 0.33 };
    const first = open(rect);
    expect(screen.getByRole("option", { name: "33.00 %" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(first.onApply).toHaveBeenCalledWith(rect);

    first.onApply.mockClear();
    fireEvent.change(screen.getByLabelText("Fill opacity"), { target: { value: "0.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(first.onApply).toHaveBeenCalledWith({ ...rect, fill_opacity: 0.5 });
  });
});

describe("DrawingSettingsDialog text form (Story 33.10)", () => {
  const note: TextDrawing = { kind: "text", id: "text-1", anchor: A, text: "Text", font_size: 14, color: "#112233" };

  it("applies the text and font size", () => {
    const { onApply } = open(note);
    fireEvent.change(screen.getByLabelText("Note text"), { target: { value: "Breakout" } });
    fireEvent.change(screen.getByLabelText("Font size"), { target: { value: "20" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledWith({ ...note, text: "Breakout", font_size: 20 });
  });

  it("refuses blank text inline, applies nothing and stays open", () => {
    const { onApply, onClose } = open(note);
    fireEvent.change(screen.getByLabelText("Note text"), { target: { value: " \u001c " } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(screen.getByRole("alert")).toHaveTextContent("The text must not be empty");
    expect(onApply).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("counts characters as the server does: an emoji is one", () => {
    open(note);
    fireEvent.change(screen.getByLabelText("Note text"), { target: { value: "😀😀" } });
    expect(screen.getByText("2 / 500 characters")).toBeInTheDocument();
  });
});
