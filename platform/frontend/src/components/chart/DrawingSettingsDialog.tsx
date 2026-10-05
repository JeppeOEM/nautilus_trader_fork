import { useState } from "react";

import { VWAP_SOURCES, type VwapSource } from "../../lib/anchoredVwap";
import {
  type AnchoredVpDrawing,
  type AnchoredVpForm,
  type AnchoredVwapDrawing,
  type Drawing,
  type FibDrawing,
  type FibLevel,
  type InstrumentPrecision,
  type LabelSide,
  MAX_LINE_WIDTH,
  type PositionDrawing,
  type PositionForm,
  anchoredVpToForm,
  parseAnchoredVpForm,
  parsePositionForm,
  positionToForm,
} from "../../lib/drawings";
import { chartVar } from "./chartTheme";
import SettingsDialogShell from "./SettingsDialogShell";

interface Props {
  drawing: FibDrawing | PositionDrawing | AnchoredVpDrawing | AnchoredVwapDrawing;
  /** Null only for an Anchored VP, whose dialog prints no price (the page opens no other without it). */
  precision: InstrumentPrecision | null;
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
}: Omit<Props, "drawing" | "precision"> & { drawing: PositionDrawing; precision: InstrumentPrecision }) {
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

function Footer({ onApply, onRemove, onClose }: { onApply: () => void; onRemove: () => void; onClose: () => void }) {
  return (
    <div className="indicator-settings-footer">
      <button type="button" onClick={onApply}>
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
  );
}

function AnchoredVpSettings({ drawing, onApply, onRemove, onClose }: Omit<Props, "drawing" | "precision"> & { drawing: AnchoredVpDrawing }) {
  const [form, setForm] = useState<AnchoredVpForm>(() => anchoredVpToForm(drawing));
  const [refusal, setRefusal] = useState<string | null>(null);
  function apply(): void {
    const next = parseAnchoredVpForm(drawing, form);
    if (typeof next === "string") {
      setRefusal(next);
      return;
    }
    onApply(next);
    onClose();
  }
  return (
    <>
      <h2>Anchored volume profile</h2>
      <section aria-label="Inputs">
        <h3>Inputs</h3>
        <label>
          Rows:
          <input
            type="text"
            inputMode="numeric"
            aria-label="Rows"
            value={form.rows}
            onChange={(e) => setForm((f) => ({ ...f, rows: e.target.value }))}
          />
        </label>
        <label>
          Value area %:
          <input
            type="text"
            inputMode="decimal"
            aria-label="Value area %"
            value={form.valueAreaPct}
            onChange={(e) => setForm((f) => ({ ...f, valueAreaPct: e.target.value }))}
          />
        </label>
      </section>
      <section aria-label="Style">
        <h3>Style</h3>
        <label>
          Up volume:
          <input
            type="color"
            aria-label="Up volume colour"
            value={asHex(form.upColor)}
            onChange={(e) => setForm((f) => ({ ...f, upColor: e.target.value }))}
          />
        </label>
        <label>
          Down volume:
          <input
            type="color"
            aria-label="Down volume colour"
            value={asHex(form.downColor)}
            onChange={(e) => setForm((f) => ({ ...f, downColor: e.target.value }))}
          />
        </label>
      </section>
      {refusal && <p role="alert">{refusal}</p>}
      <Footer onApply={apply} onRemove={onRemove} onClose={onClose} />
    </>
  );
}

function AnchoredVwapSettings({ drawing, onApply, onRemove, onClose }: Omit<Props, "drawing" | "precision"> & { drawing: AnchoredVwapDrawing }) {
  const [source, setSource] = useState<VwapSource>(drawing.source);
  const [bands, setBands] = useState(drawing.bands);
  // No stored colour means the drawing token (what the line is drawn in): the picker shows that.
  const [color, setColor] = useState(drawing.color ?? "");
  const [bandColor, setBandColor] = useState(drawing.band_color);
  return (
    <>
      <h2>Anchored VWAP</h2>
      <section aria-label="Inputs">
        <h3>Inputs</h3>
        <label>
          Source:
          <select value={source} aria-label="Source" onChange={(e) => setSource(e.target.value as VwapSource)}>
            {VWAP_SOURCES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label>
          <input type="checkbox" aria-label="Bands on" checked={bands} onChange={(e) => setBands(e.target.checked)} />
          Bands (±1σ, ±2σ)
        </label>
      </section>
      <section aria-label="Style">
        <h3>Style</h3>
        <label>
          Line:
          <input type="color" aria-label="Line colour" value={asHex(color === "" ? chartVar("--chart-drawing") : color)} onChange={(e) => setColor(e.target.value)} />
        </label>
        <label>
          Bands:
          <input type="color" aria-label="Band colour" value={asHex(bandColor)} onChange={(e) => setBandColor(e.target.value)} />
        </label>
      </section>
      <Footer
        onApply={() => {
          onApply({ ...drawing, source, bands, band_color: bandColor, ...(color === "" ? {} : { color }) });
          onClose();
        }}
        onRemove={onRemove}
        onClose={onClose}
      />
    </>
  );
}

/**
 * The settings modal of a Fibonacci retracement (each ratio on/off and colour, extend right, label
 * side, line width), an Anchored VP (rows, value area, colours), an Anchored VWAP (source, bands,
 * colours) or a position (entry / stop / target prices, width in bars, and the optional
 * account size and risk % behind the "Size:" label), on the shared `SettingsDialogShell` Story 32.3
 * introduced for the legend gear. Esc, Cancel or a backdrop click change nothing.
 */
const DIALOG_TITLES: Record<Props["drawing"]["kind"], string> = {
  fib: "Fibonacci settings",
  position: "Position settings",
  anchored_vp: "Anchored volume profile settings",
  anchored_vwap: "Anchored VWAP settings",
};

export default function DrawingSettingsDialog({ drawing, precision, onApply, onRemove, onClose }: Props) {
  return (
    <SettingsDialogShell title={DIALOG_TITLES[drawing.kind]} onClose={onClose}>
      {drawing.kind === "fib" && <FibForm drawing={drawing} onApply={onApply} onRemove={onRemove} onClose={onClose} />}
      {drawing.kind === "position" && precision && (
        <PositionSettings drawing={drawing} precision={precision} onApply={onApply} onRemove={onRemove} onClose={onClose} />
      )}
      {drawing.kind === "anchored_vp" && (
        <AnchoredVpSettings drawing={drawing} onApply={onApply} onRemove={onRemove} onClose={onClose} />
      )}
      {drawing.kind === "anchored_vwap" && (
        <AnchoredVwapSettings drawing={drawing} onApply={onApply} onRemove={onRemove} onClose={onClose} />
      )}
    </SettingsDialogShell>
  );
}
