import { useState } from "react";

import { ANCHORED_VWAP_SOURCES, type AnchoredVwapSource, STORED_VWAP_SOURCE } from "../../lib/anchoredVwap";
import {
  type AnchoredVpDrawing,
  type AnchoredVpForm,
  type AnchoredVwapDrawing,
  DEFAULT_DRAWING_LINE_STYLE,
  DEFAULT_DRAWING_LINE_WIDTH,
  type Drawing,
  type FibDrawing,
  type FibExtensionDrawing,
  type FibLevel,
  type InstrumentPrecision,
  LINE_STYLES,
  type LabelSide,
  type LineStyleName,
  MAX_FONT_SIZE,
  MAX_LINE_WIDTH,
  MAX_TEXT_LENGTH,
  MIN_FONT_SIZE,
  type PositionDrawing,
  type PositionForm,
  type TextDrawing,
  type TextForm as TextFormFields,
  anchoredVpToForm,
  parseAnchoredVpForm,
  parsePositionForm,
  parseTextForm,
  positionToForm,
  safeDecimal,
  textLength,
} from "../../lib/drawings";
import { chartVar } from "./chartTheme";
import SettingsDialogShell from "./SettingsDialogShell";

interface Props {
  drawing: Drawing;
  /** The instrument's decimals as the page holds them (null until the first candles response). Only
   * the position form reads them, and the page opens a position's dialog only once they are known. */
  precision: InstrumentPrecision | null;
  /** Receives the drawing as edited; the page persists it through the one drawings resource. */
  onApply: (next: Drawing) => void;
  onRemove: () => void;
  onClose: () => void;
}

// <input type="color"> only takes #rrggbb; anything else would silently show black.
const asHex = (color: string): string => (/^#[0-9a-f]{6}$/i.test(color) ? color : "#000000");

const LINE_WIDTHS = Array.from({ length: MAX_LINE_WIDTH }, (_, i) => i + 1);

function FibForm({ drawing, onApply, onRemove, onClose }: Omit<Props, "drawing" | "precision"> & { drawing: FibDrawing | FibExtensionDrawing }) {
  const [levels, setLevels] = useState<FibLevel[]>(drawing.levels);
  const [extendRight, setExtendRight] = useState(drawing.extend_right);
  const [labelSide, setLabelSide] = useState<LabelSide>(drawing.label_side);
  const [lineWidth, setLineWidth] = useState(drawing.line_width);
  const edit = (ratio: number, patch: Partial<FibLevel>): void =>
    setLevels((all) => all.map((l) => (l.ratio === ratio ? { ...l, ...patch } : l)));

  return (
    <>
      <h2>{drawing.kind === "fib_extension" ? "Fibonacci extension" : "Fibonacci retracement"}</h2>
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
  const [source, setSource] = useState<AnchoredVwapSource>(drawing.source);
  // Story 33.6: the stored source has no bands (the stored columns carry no per-trade prices to
  // spread them); its saved `bands` switch is kept for when the source is switched back.
  const stored = source === STORED_VWAP_SOURCE;
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
          <select value={source} aria-label="Source" onChange={(e) => setSource(e.target.value as AnchoredVwapSource)}>
            {ANCHORED_VWAP_SOURCES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label>
          <input
            type="checkbox"
            aria-label="Bands on"
            checked={bands && !stored}
            disabled={stored}
            onChange={(e) => setBands(e.target.checked)}
          />
          Bands (±1σ, ±2σ)
        </label>
        {stored && <p className="indicator-dialog-tag">Stored source: bands need per-trade prices.</p>}
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

/** Story 33.10: the line-like kinds' one form (and the rectangle's fill opacity). */
type LineKindDrawing = Extract<Drawing, { kind: "hline" | "trendline" | "ray" | "extended" | "arrow" | "vline" | "rect" | "channel" | "price_range" | "date_range" }>;

const LINE_KINDS: ReadonlySet<Drawing["kind"]> = new Set([
  "hline",
  "trendline",
  "ray",
  "extended",
  "arrow",
  "vline",
  "rect",
  "channel",
  "price_range",
  "date_range",
]);

const isLineKind = (d: Drawing): d is LineKindDrawing => LINE_KINDS.has(d.kind);

/** The fill opacities offered, as fractions: 0 to 100 % in 5 % steps (plus a stored value off them). */
const OPACITY_STEPS = Array.from({ length: 21 }, (_, i) => i / 20);

/**
 * The line kind as the form leaves it: only what the operator changed is written, so a drawing that
 * stored no colour, width or style (absent = the drawing token, 1 px, solid) keeps them absent when
 * applied unchanged, like `withFlag` keeps a cleared flag absent.
 */
function withLook(
  drawing: LineKindDrawing,
  look: { color: string; width: number; style: LineStyleName; opacity: number },
  initial: { color: string; width: number; style: LineStyleName },
): LineKindDrawing {
  const next: LineKindDrawing = { ...drawing };
  if (look.color !== initial.color) next.color = look.color;
  if (look.width !== initial.width) next.line_width = look.width;
  if (look.style !== initial.style) next.line_style = look.style;
  if (next.kind === "rect") next.fill_opacity = look.opacity;
  return next;
}

function LineForm({ drawing, onApply, onRemove, onClose }: Omit<Props, "drawing" | "precision"> & { drawing: LineKindDrawing }) {
  // No stored colour means the drawing token (what the line is drawn in): the picker shows that.
  const [initial] = useState(() => ({
    color: drawing.color ?? chartVar("--chart-drawing"),
    width: drawing.line_width ?? DEFAULT_DRAWING_LINE_WIDTH,
    style: drawing.line_style ?? DEFAULT_DRAWING_LINE_STYLE,
  }));
  const [color, setColor] = useState(initial.color);
  const [width, setWidth] = useState(initial.width);
  const [style, setStyle] = useState<LineStyleName>(initial.style);
  // The stored fraction exactly: a hand-edited 0.33 is offered as itself and written back unchanged
  // unless another step is picked (never rounded to the 5 % grid).
  const stored = drawing.kind === "rect" ? drawing.fill_opacity : 0;
  const [opacity, setOpacity] = useState(stored);
  const opacities = OPACITY_STEPS.includes(stored) ? OPACITY_STEPS : [...OPACITY_STEPS, stored].sort((a, b) => a - b);
  return (
    <>
      <section aria-label="Style">
        <h3>Style</h3>
        <label>
          Colour:
          <input type="color" aria-label="Line colour" value={asHex(color)} onChange={(e) => setColor(e.target.value)} />
        </label>
        <label>
          Width:
          <select aria-label="Line width" value={width} onChange={(e) => setWidth(Number(e.target.value))}>
            {LINE_WIDTHS.map((w) => (
              <option key={w} value={w}>
                {w} px
              </option>
            ))}
          </select>
        </label>
        <label>
          Style:
          <select aria-label="Line style" value={style} onChange={(e) => setStyle(e.target.value as LineStyleName)}>
            {LINE_STYLES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        {drawing.kind === "rect" && (
          <label>
            Fill opacity:
            <select aria-label="Fill opacity" value={String(opacity)} onChange={(e) => setOpacity(Number(e.target.value))}>
              {opacities.map((fraction) => (
                <option key={fraction} value={String(fraction)}>
                  {safeDecimal(fraction * 100, OPACITY_STEPS.includes(fraction) ? 0 : 2)} %
                </option>
              ))}
            </select>
          </label>
        )}
      </section>
      <Footer
        onApply={() => {
          onApply(withLook(drawing, { color, width, style, opacity }, initial));
          onClose();
        }}
        onRemove={onRemove}
        onClose={onClose}
      />
    </>
  );
}

function TextSettings({ drawing, onApply, onRemove, onClose }: Omit<Props, "drawing" | "precision"> & { drawing: TextDrawing }) {
  const [form, setForm] = useState<TextFormFields>({ text: drawing.text, fontSize: String(drawing.font_size) });
  const [initialColor] = useState(drawing.color ?? chartVar("--chart-drawing"));
  const [color, setColor] = useState(initialColor);
  const [refusal, setRefusal] = useState<string | null>(null);
  function apply(): void {
    // An unchanged colour keeps a note that stored none without one (absent = the drawing token).
    const next = parseTextForm(drawing, form, color === initialColor ? undefined : color);
    if (typeof next === "string") {
      setRefusal(next);
      return;
    }
    onApply(next);
    onClose();
  }
  return (
    <>
      <section aria-label="Text">
        <h3>Text</h3>
        <textarea
          aria-label="Note text"
          rows={3}
          value={form.text}
          onChange={(e) => setForm((f) => ({ ...f, text: e.target.value }))}
        />
        <p className="indicator-dialog-tag">
          {safeDecimal(textLength(form.text), 0)} / {safeDecimal(MAX_TEXT_LENGTH, 0)} characters
        </p>
      </section>
      <section aria-label="Style">
        <h3>Style</h3>
        <label>
          Font size ({safeDecimal(MIN_FONT_SIZE, 0)}–{safeDecimal(MAX_FONT_SIZE, 0)}):
          <input
            type="text"
            inputMode="numeric"
            aria-label="Font size"
            value={form.fontSize}
            onChange={(e) => setForm((f) => ({ ...f, fontSize: e.target.value }))}
          />
        </label>
        <label>
          Colour:
          <input type="color" aria-label="Text colour" value={asHex(color)} onChange={(e) => setColor(e.target.value)} />
        </label>
      </section>
      {refusal && <p role="alert">{refusal}</p>}
      <Footer onApply={apply} onRemove={onRemove} onClose={onClose} />
    </>
  );
}

/**
 * The settings modal of every drawing kind: Story 33.10's line form (colour, width, style; a
 * rectangle's fill opacity) for the horizontal and vertical lines, the trendline, ray, extended line,
 * arrow, rectangle, channel and ranges, the text form (text, font size, colour), a Fibonacci
 * retracement or extension (each ratio on/off and colour, extend right, label side, line width), an
 * Anchored VP (rows, value area, colours), an Anchored VWAP (source, bands,
 * colours) or a position (entry / stop / target prices, width in bars, and the optional
 * account size and risk % behind the "Size:" label), on the shared `SettingsDialogShell` Story 32.3
 * introduced for the legend gear. Esc, Cancel or a backdrop click change nothing.
 */
const DIALOG_TITLES: Record<Drawing["kind"], string> = {
  hline: "Horizontal line settings",
  trendline: "Trendline settings",
  ray: "Ray settings",
  extended: "Extended line settings",
  arrow: "Arrow settings",
  vline: "Vertical line settings",
  rect: "Rectangle settings",
  channel: "Parallel channel settings",
  price_range: "Price range settings",
  date_range: "Date range settings",
  text: "Text settings",
  fib: "Fibonacci settings",
  fib_extension: "Fibonacci extension settings",
  position: "Position settings",
  anchored_vp: "Anchored volume profile settings",
  anchored_vwap: "Anchored VWAP settings",
};

export default function DrawingSettingsDialog({ drawing, precision, onApply, onRemove, onClose }: Props) {
  const actions = { onApply, onRemove, onClose };
  return (
    <SettingsDialogShell title={DIALOG_TITLES[drawing.kind]} onClose={onClose}>
      {isLineKind(drawing) && <LineForm drawing={drawing} {...actions} />}
      {drawing.kind === "text" && <TextSettings drawing={drawing} {...actions} />}
      {(drawing.kind === "fib" || drawing.kind === "fib_extension") && <FibForm drawing={drawing} {...actions} />}
      {drawing.kind === "position" && precision && <PositionSettings drawing={drawing} precision={precision} {...actions} />}
      {drawing.kind === "anchored_vp" && <AnchoredVpSettings drawing={drawing} {...actions} />}
      {drawing.kind === "anchored_vwap" && <AnchoredVwapSettings drawing={drawing} {...actions} />}
    </SettingsDialogShell>
  );
}
