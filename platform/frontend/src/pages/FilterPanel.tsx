import { useState } from "react";

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
}

/** `+` opens a `<field> <operator> <value>` builder; saved conditions show as removable chips.
 * Conditions combine with AND only (Story 17.6). */
export default function FilterPanel({ fields, conditions, onChange, onOpen }: FilterPanelProps) {
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
