import { useEffect, useRef, useState } from "react";

import type { IndicatorCatalogEntry, IndicatorConfigEntry } from "../../api/schema";
import { DEFAULT_SOURCE, PRICE_SOURCES } from "../../lib/indicatorId";
import {
  DEFAULT_LINE_STYLE,
  DEFAULT_LINE_WIDTH,
  LINE_STYLES,
  LINE_WIDTHS,
  type LineStyleName,
  type OutputStyle,
  outputStyle,
} from "../../lib/indicatorStyle";
import ParamInputs from "./ParamInputs";
import { coerceParams, invalidParamKeys, rawFromParams } from "./paramCoercion";

/** One drawn output of the indicator (a legend value): what the Style section edits. */
export interface SettingsOutput {
  label: string;
  kind: "Line" | "Histogram";
  /** The colours the chart draws with while the entry stores none (the pane palette). */
  defaultColor: string;
  defaultUpColor: string;
  defaultDownColor: string;
}

/** What Apply persists: the entry's params, source and its whole per-output style map. */
export interface SettingsPatch {
  params: Record<string, unknown>;
  source: string;
  style: Record<string, Record<string, unknown>>;
}

interface Props {
  /** The legend title, e.g. "SimpleMovingAverage (20)". */
  title: string;
  entry: IndicatorConfigEntry;
  catalogEntry: IndicatorCatalogEntry | undefined;
  outputs: SettingsOutput[];
  disabled: boolean;
  /** Resolves to a refusal (a duplicate instance) or the save's error, which keep the modal open
   * with the message, or to null once the change is saved. */
  onApply: (patch: SettingsPatch) => Promise<string | null>;
  onRemove: () => void;
  onClose: () => void;
}

// <input type="color"> only takes #rrggbb; anything else would silently show black.
const asHex = (color: string): string => (/^#[0-9a-f]{6}$/i.test(color) ? color : "#000000");

/**
 * The legend gear's modal (Story 32.3): Inputs (params through the shared `ParamInputs`, plus a
 * Source select for a `source_selectable` indicator), Style (per output: colour, width 1-4,
 * solid/dashed/dotted; a histogram output gets up and down colours) and a footer (Apply, Cancel,
 * Remove). Esc, Cancel or a backdrop click close it with no change. Mounted only while open, so
 * its drafts always start from the saved entry.
 */
export default function IndicatorSettingsDialog({
  title,
  entry,
  catalogEntry,
  outputs,
  disabled,
  onApply,
  onRemove,
  onClose,
}: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const params = entry.params ?? {};
  const choices = catalogEntry?.choices ?? {};
  const [raw, setRaw] = useState(() => rawFromParams(params));
  const [source, setSource] = useState(entry.source ?? DEFAULT_SOURCE);
  // Only an output the operator touched is written back; the rest keep what the entry holds
  // (nothing, usually: the pane palette default).
  const [drafts, setDrafts] = useState<Record<string, OutputStyle>>({});
  const pressedOnBackdrop = useRef(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const invalidKeys = invalidParamKeys(params, raw, choices);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog || dialog.open) return;
    // showModal gives Esc-to-close, a backdrop and focus trapping natively; jsdom lacks it.
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  }, []);

  const styleOf = (label: string): OutputStyle => ({ ...outputStyle(entry, label), ...drafts[label] });
  const edit = (label: string, patch: OutputStyle): void =>
    setDrafts((all) => ({ ...all, [label]: { ...all[label], ...patch } }));

  async function apply(): Promise<void> {
    const style: Record<string, Record<string, unknown>> = { ...(entry.style ?? {}) };
    for (const [label, draft] of Object.entries(drafts)) {
      style[label] = { ...style[label], ...draft };
    }
    // Closed only once the save landed: a refused or failed save keeps the drafts and says why.
    setSaving(true);
    setRefusal(null);
    const message = await onApply({ params: coerceParams(params, raw), source, style });
    setSaving(false);
    if (message === null) onClose();
    else setRefusal(message);
  }

  return (
    <dialog
      ref={ref}
      className="indicator-dialog indicator-settings"
      aria-label={title}
      onClose={onClose}
      onMouseDown={(e) => {
        pressedOnBackdrop.current = e.target === e.currentTarget;
      }}
      onClick={(e) => {
        // A click on the backdrop, not on the content -- and only when the press began there too:
        // a text-selection drag that ends on the backdrop must not close the dialog.
        if (e.target === e.currentTarget && pressedOnBackdrop.current) onClose();
        pressedOnBackdrop.current = false;
      }}
    >
      <div className="indicator-settings-body">
        <h2>{title}</h2>
        <section aria-label="Inputs">
          <h3>Inputs</h3>
          <ParamInputs
            params={params}
            raw={raw}
            choices={choices}
            invalidKeys={invalidKeys}
            onEdit={(key, value) => setRaw((prev) => ({ ...prev, [key]: value }))}
          />
          {catalogEntry?.source_selectable && (
            <label>
              Source:
              <select value={source} onChange={(e) => setSource(e.target.value)}>
                {!(PRICE_SOURCES as readonly string[]).includes(source) && <option value={source}>{source}</option>}
                {PRICE_SOURCES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </label>
          )}
        </section>
        <section aria-label="Style">
          <h3>Style</h3>
          {outputs.length === 0 && <p>No outputs drawn yet.</p>}
          {outputs.map((output) => {
            const style = styleOf(output.label);
            return (
              <fieldset key={output.label} className="indicator-settings-output">
                <legend>{output.label}</legend>
                {output.kind === "Histogram" ? (
                  <>
                    <label>
                      Up colour:
                      <input
                        type="color"
                        aria-label={`${output.label} up colour`}
                        value={asHex(style.up_color ?? output.defaultUpColor)}
                        onChange={(e) => edit(output.label, { up_color: e.target.value })}
                      />
                    </label>
                    <label>
                      Down colour:
                      <input
                        type="color"
                        aria-label={`${output.label} down colour`}
                        value={asHex(style.down_color ?? output.defaultDownColor)}
                        onChange={(e) => edit(output.label, { down_color: e.target.value })}
                      />
                    </label>
                  </>
                ) : (
                  <>
                    <label>
                      Colour:
                      <input
                        type="color"
                        aria-label={`${output.label} colour`}
                        value={asHex(style.color ?? output.defaultColor)}
                        onChange={(e) => edit(output.label, { color: e.target.value })}
                      />
                    </label>
                    <label>
                      Width:
                      <select
                        aria-label={`${output.label} width`}
                        value={style.line_width ?? DEFAULT_LINE_WIDTH}
                        onChange={(e) => edit(output.label, { line_width: Number(e.target.value) })}
                      >
                        {LINE_WIDTHS.map((w) => (
                          <option key={w} value={w}>
                            {w} px
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Line:
                      <select
                        aria-label={`${output.label} line style`}
                        value={style.line_style ?? DEFAULT_LINE_STYLE}
                        onChange={(e) => edit(output.label, { line_style: e.target.value as LineStyleName })}
                      >
                        {LINE_STYLES.map((s) => (
                          <option key={s} value={s}>
                            {s}
                          </option>
                        ))}
                      </select>
                    </label>
                  </>
                )}
              </fieldset>
            );
          })}
        </section>
        {refusal && <p role="alert">{refusal}</p>}
        <div className="indicator-settings-footer">
          <button type="button" disabled={disabled || saving || invalidKeys.length > 0} onClick={() => void apply()}>
            Apply
          </button>
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          {/* Not while an Apply is in flight: a remove built on its optimistic list would chain onto
              a save that may still fail. */}
          <button
            type="button"
            disabled={disabled || saving}
            onClick={() => {
              onRemove();
              onClose();
            }}
          >
            Remove
          </button>
        </div>
      </div>
    </dialog>
  );
}
