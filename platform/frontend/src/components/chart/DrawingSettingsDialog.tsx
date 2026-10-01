import { useState } from "react";

import {
  type Drawing,
  type FibDrawing,
  type FibLevel,
  type InstrumentPrecision,
  type LabelSide,
  MAX_LINE_WIDTH,
  type PositionDrawing,
  type PositionForm,
  parsePositionForm,
  positionToForm,
} from "../../lib/drawings";
import SettingsDialogShell from "./SettingsDialogShell";

interface Props {
  drawing: FibDrawing | PositionDrawing;
  precision: InstrumentPrecision;
  /** Receives the drawing as edited; the page persists it through the one drawings resource. */
  onApply: (next: Drawing) => void;
  onRemove: () => void;
  onClose: () => void;
}

// <input type="color"> only takes #rrggbb; anything else would silently show black.
const asHex = (color: string): string => (/^#[0-9a-f]{6}$/i.test(color) ? color : "#000000");

const LINE_WIDTHS = Array.from({ length: MAX_LINE_WIDTH }, (_, i) => i + 1);

function FibForm({ drawing, onApply, onRemove, onClose }: Omit<Props, "drawing" | "precision"> & { drawing: FibDrawing }) {
  const [levels, setLevels] = useState<FibLevel[]>(drawing.levels);
  const [extendRight, setExtendRight] = useState(drawing.extend_right);
  const [labelSide, setLabelSide] = useState<LabelSide>(drawing.label_side);
  const [lineWidth, setLineWidth] = useState(drawing.line_width);
  const edit = (ratio: number, patch: Partial<FibLevel>): void =>
    setLevels((all) => all.map((l) => (l.ratio === ratio ? { ...l, ...patch } : l)));

  return (
    <>
      <h2>Fibonacci retracement</h2>
      <section aria-label="Levels">
        <h3>Levels</h3>
        {levels.map((level) => (
          <div key={level.ratio} className="indicator-settings-output">
            <label>
              <input
                type="checkbox"
                aria-label={`${level.ratio} on`}
                checked={level.enabled}
                onChange={(e) => edit(level.ratio, { enabled: e.target.checked })}
              />
              {level.ratio}
            </label>{" "}
            <input
              type="color"
              aria-label={`${level.ratio} colour`}
              value={asHex(level.color)}
              onChange={(e) => edit(level.ratio, { color: e.target.value })}
            />
          </div>
        ))}
      </section>
      <section aria-label="Style">
        <h3>Style</h3>
        <label>
          <input type="checkbox" checked={extendRight} onChange={(e) => setExtendRight(e.target.checked)} />
          Extend levels to the right
        </label>
        <label>
          Labels:
          <select value={labelSide} onChange={(e) => setLabelSide(e.target.value as LabelSide)}>
            <option value="left">left</option>
            <option value="right">right</option>
          </select>
        </label>
        <label>
          Width:
          <select value={lineWidth} onChange={(e) => setLineWidth(Number(e.target.value))}>
            {LINE_WIDTHS.map((w) => (
              <option key={w} value={w}>
                {w} px
              </option>
            ))}
          </select>
        </label>
      </section>
      <div className="indicator-settings-footer">
        <button
          type="button"
          onClick={() => {
            onApply({ ...drawing, levels, extend_right: extendRight, label_side: labelSide, line_width: lineWidth });
            onClose();
          }}
        >
          Apply
        </button>
        <button type="button" onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          onClick={() => {
            onRemove();
            onClose();
          }}
        >
          Remove
        </button>
      </div>
    </>
  );
}

function PositionSettings({
  drawing,
  precision,
  onApply,
  onRemove,
  onClose,
}: Omit<Props, "drawing"> & { drawing: PositionDrawing }) {
  const [form, setForm] = useState<PositionForm>(() => positionToForm(drawing, precision.price));
  const [refusal, setRefusal] = useState<string | null>(null);
  const field = (key: keyof PositionForm, label: string) => (
    <label>
      {label}:
      <input
        type="text"
        inputMode="decimal"
        aria-label={label}
        value={form[key]}
        onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
      />
    </label>
  );

  function apply(): void {
    const next = parsePositionForm(drawing, form, precision.price);
    if (typeof next === "string") {
      setRefusal(next);
      return;
    }
    onApply(next);
    onClose();
  }

  return (
    <>
      <h2>{drawing.side === "long" ? "Long position" : "Short position"}</h2>
      <section aria-label="Prices">
        <h3>Prices</h3>
        {field("entry", "Entry")}
        {field("stop", "Stop")}
        {field("target", "Target")}
        {field("widthBars", "Width (bars)")}
      </section>
      <section aria-label="Size">
        <h3>Size</h3>
        <p>Both fields, or neither: the size is account × risk % ÷ |entry − stop|.</p>
        {field("account", "Account size")}
        {field("riskPct", "Risk %")}
      </section>
      {refusal && <p role="alert">{refusal}</p>}
      <div className="indicator-settings-footer">
        <button type="button" onClick={apply}>
          Apply
        </button>
        <button type="button" onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          onClick={() => {
            onRemove();
            onClose();
          }}
        >
          Remove
        </button>
      </div>
    </>
  );
}

/**
 * The settings modal of a Fibonacci retracement (each ratio on/off and colour, extend right, label
 * side, line width) or a position (entry / stop / target prices, width in bars, and the optional
 * account size and risk % behind the "Size:" label), on the shared `SettingsDialogShell` Story 32.3
 * introduced for the legend gear. Esc, Cancel or a backdrop click change nothing.
 */
export default function DrawingSettingsDialog({ drawing, precision, onApply, onRemove, onClose }: Props) {
  return (
    <SettingsDialogShell title={drawing.kind === "fib" ? "Fibonacci settings" : "Position settings"} onClose={onClose}>
      {drawing.kind === "fib" ? (
        <FibForm drawing={drawing} onApply={onApply} onRemove={onRemove} onClose={onClose} />
      ) : (
        <PositionSettings drawing={drawing} precision={precision} onApply={onApply} onRemove={onRemove} onClose={onClose} />
      )}
    </SettingsDialogShell>
  );
}
