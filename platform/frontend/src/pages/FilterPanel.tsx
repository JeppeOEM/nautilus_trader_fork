import { useState } from "react";

import type { FilterPresetItem } from "../api/client";
import { type DisplayPrecision, FILTER_OPERATORS, type FilterCondition, type FilterOperator } from "./filters";

/** A free-text field (e.g. venue: the value stays a string and only `=` applies), or a numeric
 * one carrying its cell's display precision, which its `=` conditions match at. */
export type FilterField =
  | { key: string; label: string; text: true }
  | { key: string; label: string; text?: false; precision: DisplayPrecision };

interface FilterPanelProps {
  fields: FilterField[];
  conditions: FilterCondition[];
  onChange: (next: FilterCondition[]) => void;
  /** Fired when the builder opens -- lets the page lazily load field lists (Technicals outputs) it
   * otherwise wouldn't fetch while another tab is showing. */
  onOpen?: () => void;
  /** Story 33.7: the saved presets, when the page offers them. */
  presets?: PresetControls;
}

/** The page's saved filter presets (Story 33.7) and what the controls do with them. */
export interface PresetControls {
  presets: FilterPresetItem[];
  /** False until the stored list has loaded: Save and Delete PUT the whole list, so they wait. */
  loaded: boolean;
  /** Whether a Save would store any condition: the visible ones, a hidden Technicals one, or one the
   * last recall could not apply. */
  savable: boolean;
  /** The recalled (or just saved) preset's name, shown as a chip until a filter edit. */
  active: string | null;
  /** Why a recalled preset was not applied in full (an unknown field). */
  notice: string | null;
  error: string | null;
  busy: boolean;
  onRecall: (name: string) => void;
  onSave: (name: string) => void;
  onDelete: (name: string) => void;
  /** Load the stored list again after a failed load. */
  onRetry: () => void;
}

/** Recall (replaces the conditions), Save under a name ("Overwrite" when it exists), Delete the
 * last recalled or saved preset (its name is in the button's title). The select returns to its
 * placeholder after each pick, so picking the same preset again (after an edit) recalls it again. */
function PresetBar({ controls }: { controls: PresetControls }) {
  const [lastPicked, setLastPicked] = useState("");
  const [name, setName] = useState("");
  const trimmed = name.trim();
  const exists = controls.presets.some((p) => p.name === trimmed);
  const deleteTarget = controls.presets.some((p) => p.name === lastPicked) ? lastPicked : "";
  const writable = controls.loaded && !controls.busy;
  return (
    <span className="filter-presets">
      <select
        aria-label="Filter preset"
        value=""
        disabled={controls.busy}
        onChange={(e) => {
          if (e.target.value === "") return;
          setLastPicked(e.target.value);
          controls.onRecall(e.target.value);
        }}
      >
        <option value="">presets…</option>
        {controls.presets.map((p) => (
          <option key={p.name} value={p.name}>
            {p.name}
          </option>
        ))}
      </select>
      <button
        type="button"
        aria-label="Delete preset"
        title={deleteTarget === "" ? "pick a preset to delete" : `delete preset ${deleteTarget}`}
        disabled={deleteTarget === "" || !writable}
        onClick={() => controls.onDelete(deleteTarget)}
      >
        Delete
      </button>
      <input aria-label="Preset name" value={name} maxLength={64} onChange={(e) => setName(e.target.value)} />
      <button
        type="button"
        disabled={!controls.savable || trimmed === "" || !writable}
        onClick={() => {
          setLastPicked(trimmed);
          controls.onSave(trimmed);
        }}
      >
        {exists ? "Overwrite" : "Save"}
      </button>
      {controls.active !== null && (
        <span className="filter-chip filter-preset-active" title="active preset">
          {controls.active}
        </span>
      )}
      {controls.notice !== null && <span role="status" className="rankings-empty">{controls.notice}</span>}
      {controls.error !== null && (
        <span role="alert" className="rankings-mode-error">
          presets: {controls.error}
        </span>
      )}
      {!controls.loaded && controls.error !== null && (
        <button type="button" disabled={controls.busy} onClick={controls.onRetry}>
          Retry
        </button>
      )}
    </span>
  );
}

/** `+` opens a `<field> <operator> <value>` builder; saved conditions show as removable chips.
 * Conditions combine with AND only (Story 17.6). */
export default function FilterPanel({ fields, conditions, onChange, onOpen, presets }: FilterPanelProps) {
  const [open, setOpen] = useState(false);
  const [field, setField] = useState("");
  const [op, setOp] = useState<FilterOperator>(">");
  const [value, setValue] = useState("");

  // Fall back when the chosen field disappeared (e.g. its Technicals column was removed).
  const selectedField = fields.some((f) => f.key === field) ? field : (fields[0]?.key ?? "");
  const fieldDef = fields.find((f) => f.key === selectedField);
  const isText = fieldDef?.text === true;
  const canAdd = fieldDef !== undefined && value.trim() !== "" && (isText || Number.isFinite(Number(value)));

  function add(): void {
    if (fieldDef === undefined || !canAdd) return;
    const condition: FilterCondition = fieldDef.text
      ? { field: fieldDef.key, op: "=", value: value.trim() }
      : { field: fieldDef.key, op, value: Number(value), precision: fieldDef.precision };
    onChange([...conditions, condition]);
    setValue("");
    setOpen(false);
  }

  const labelOf = (key: string): string => fields.find((f) => f.key === key)?.label ?? key;

  return (
    <div className="filter-panel">
      <button type="button" className="tabbtn" aria-label="Add filter" onClick={() => {
          if (!open) onOpen?.();
          setOpen((o) => !o);
        }}
      >
        + Filter
      </button>
      {conditions.map((c, i) => (
        <span key={`${c.field}${c.op}${c.value}${i}`} className="filter-chip">
          {labelOf(c.field)} {c.op} {c.value}
          <button
            type="button"
            className="rankings-history-link"
            aria-label={`Remove filter ${labelOf(c.field)} ${c.op} ${c.value}`}
            onClick={() => onChange(conditions.filter((_, j) => j !== i))}
          >
            ×
          </button>
        </span>
      ))}
      {presets !== undefined && <PresetBar controls={presets} />}
      {open && (
        <span className="filter-form">
          <select aria-label="Filter field" value={selectedField} onChange={(e) => setField(e.target.value)}>
            {fields.map((f) => (
              <option key={f.key} value={f.key}>
                {f.label}
              </option>
            ))}
          </select>
          <select aria-label="Filter operator" value={isText ? "=" : op} onChange={(e) => setOp(e.target.value as FilterOperator)}>
            {(isText ? (["="] as FilterOperator[]) : FILTER_OPERATORS).map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
          <input aria-label="Filter value" value={value} onChange={(e) => setValue(e.target.value)} />
          <button type="button" onClick={add} disabled={!canAdd}>
            Add
          </button>
        </span>
      )}
    </div>
  );
}
