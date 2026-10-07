import { useEffect, useRef, useState } from "react";

import { fetchCoinDrawings, type IndicatorCatalogEntry } from "../../api/client";
import { type CatalogState, catalogEntries } from "../../hooks/useIndicatorCatalog";
import {
  CONDITION_FIELDS,
  CONDITION_KINDS,
  CONDITION_LABELS,
  type ConditionForm,
  type ConditionKind,
  emptyForm,
  FIELD_SPECS,
} from "../../lib/alertConditions";
import type { TrendlineDrawing } from "../../lib/drawings";
import { PRICE_SOURCES } from "../../lib/indicatorId";

type Loaded<T> = { status: "loading" } | { status: "error" } | { status: "ready"; value: T };

interface Props {
  /** The coin a `trendline_cross` picks its drawing from. */
  instrumentId: string;
  form: ConditionForm;
  onChange: (form: ConditionForm) => void;
  /** The picker's catalog (`useIndicatorCatalog`), which also types the params on save. */
  catalog: CatalogState;
}

/**
 * The coin's saved trendlines, fetched while the trendline field is shown. The answer is held with
 * the coin it is for, so another coin reads as loading until its own list arrives -- never as the
 * previous coin's lines.
 */
function useTrendlines(instrumentId: string): Loaded<TrendlineDrawing[]> {
  const [held, setHeld] = useState<{ instrumentId: string; state: Loaded<TrendlineDrawing[]> } | null>(null);
  useEffect(() => {
    if (!instrumentId) return;
    let live = true;
    const answer = (state: Loaded<TrendlineDrawing[]>) => live && setHeld({ instrumentId, state });
    fetchCoinDrawings(instrumentId)
      .then((all) => answer({ status: "ready", value: all.filter((d) => d.kind === "trendline") }))
      .catch(() => answer({ status: "error" }));
    return () => {
      live = false;
    };
  }, [instrumentId]);
  return held?.instrumentId === instrumentId ? held.state : { status: "loading" };
}

/** Switch kind, keeping what the operator typed into a field both kinds share (a level, a window). */
function withKind(form: ConditionForm, kind: ConditionKind): ConditionForm {
  const next = emptyForm(kind);
  for (const name of Object.keys(next.fields)) {
    if (form.fields[name]) next.fields[name] = form.fields[name];
  }
  return kind === form.kind ? { ...next, params: form.params, storedParams: form.storedParams } : next;
}

function withIndicator(form: ConditionForm, name: string, entry: IndicatorCatalogEntry | undefined): ConditionForm {
  const params = Object.fromEntries(Object.entries(entry?.params ?? {}).map(([k, v]) => [k, String(v)]));
  const fields = { ...form.fields, name, source: "close", output: entry?.outputs[0] ?? "" };
  return { ...form, fields, params, storedParams: undefined };
}

function ScalarField({ name, form, onChange }: { name: string } & Omit<Props, "instrumentId" | "catalog">) {
  const spec = FIELD_SPECS[name];
  const text = form.fields[name] ?? "";
  const set = (value: string) => onChange({ ...form, fields: { ...form.fields, [name]: value } });
  if (spec.input === "select") {
    return (
      <label>
        {spec.label}
        <select aria-label={spec.label} value={text} onChange={(e) => set(e.target.value)}>
          {spec.options?.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
    );
  }
  return (
    <label>
      {spec.label}
      <input
        aria-label={spec.label}
        type="number"
        step={spec.input === "integer" ? 1 : "any"}
        value={text}
        onChange={(e) => set(e.target.value)}
      />
    </label>
  );
}

function ParamField({
  name,
  entry,
  form,
  onChange,
}: { name: string; entry: IndicatorCatalogEntry } & Omit<Props, "instrumentId" | "catalog">) {
  const text = form.params[name] ?? "";
  const fallback = entry.params[name];
  const set = (value: string) => onChange({ ...form, params: { ...form.params, [name]: value } });
  const choices = entry.choices?.[name] ?? (typeof fallback === "boolean" ? ["true", "false"] : null);
  return (
    <label>
      {name}
      {choices ? (
        <select aria-label={`Parameter ${name}`} value={text} onChange={(e) => set(e.target.value)}>
          {text && !choices.includes(text) && <option value={text}>{text}</option>}
          {choices.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      ) : (
        <input
          aria-label={`Parameter ${name}`}
          type={typeof fallback === "number" ? "number" : "text"}
          step="any"
          value={text}
          onChange={(e) => set(e.target.value)}
        />
      )}
    </label>
  );
}

function IndicatorFields({ form, onChange, catalog }: Omit<Props, "instrumentId">) {
  const entries = catalogEntries(catalog) ?? {};
  const name = form.fields.name ?? "";
  const entry = entries[name];
  if (catalog.status === "error") {
    return (
      <p style={{ color: "var(--color-danger)" }}>
        Failed to load the indicator catalog; an indicator condition cannot be saved until it loads.
      </p>
    );
  }
  const set = (field: string, value: string) => onChange({ ...form, fields: { ...form.fields, [field]: value } });
  return (
    <>
      <label>
        Indicator
        <select aria-label="Indicator" value={name} onChange={(e) => onChange(withIndicator(form, e.target.value, entries[e.target.value]))}>
          <option value="">{catalog.status === "loading" ? "Loading…" : "Choose…"}</option>
          {name && !entry && <option value={name}>{name}</option>}
          {Object.keys(entries)
            .sort()
            .map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
        </select>
      </label>
      {entry &&
        Object.keys(entry.params).map((p) => <ParamField key={p} name={p} entry={entry} form={form} onChange={onChange} />)}
      {entry?.source_selectable && (
        <label>
          Source
          <select aria-label="Source" value={form.fields.source} onChange={(e) => set("source", e.target.value)}>
            {PRICE_SOURCES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
      )}
      <label>
        Output
        <select aria-label="Output" value={form.fields.output ?? ""} onChange={(e) => set("output", e.target.value)}>
          {/* A stored output the entry no longer lists stays shown as what Save would send. */}
          {form.fields.output && !entry?.outputs.includes(form.fields.output) && (
            <option value={form.fields.output}>{form.fields.output}</option>
          )}
          {entry?.outputs.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      </label>
      <ScalarField name="op" form={form} onChange={onChange} />
      <ScalarField name="value" form={form} onChange={onChange} />
    </>
  );
}

function TrendlineField({ instrumentId, form, onChange }: Omit<Props, "catalog">) {
  const trendlines = useTrendlines(instrumentId);
  const chosen = form.fields.drawing_id ?? "";
  if (trendlines.status === "error") return <p style={{ color: "var(--color-danger)" }}>Failed to load this coin&apos;s drawings.</p>;
  const lines = trendlines.status === "ready" ? trendlines.value : [];
  if (trendlines.status === "ready" && lines.length === 0 && !chosen) {
    return <p>No trendlines on this coin&apos;s chart &mdash; draw one first.</p>;
  }
  return (
    <label>
      Trendline
      <select
        aria-label="Trendline"
        value={chosen}
        onChange={(e) => onChange({ ...form, fields: { ...form.fields, drawing_id: e.target.value } })}
      >
        <option value="">Choose…</option>
        {chosen && !lines.some((l) => l.id === chosen) && <option value={chosen}>{chosen}</option>}
        {lines.map((l) => (
          <option key={l.id} value={l.id}>
            {l.id} ({l.anchors[0].price} → {l.anchors[1].price})
          </option>
        ))}
      </select>
    </label>
  );
}

/**
 * Story 33.8: an alert condition's inputs -- the kind, then that kind's fields. An indicator is
 * picked from the catalog (its params, a source when it takes one, an output); a trendline from the
 * coin's saved drawings. It edits only the typed form: the condition's text and every value it
 * compares are the server's.
 */
export default function ConditionFields({ instrumentId, form, onChange, catalog }: Props) {
  // A trendline belongs to one coin: another coin never keeps the previous coin's drawing id.
  const shownFor = useRef(instrumentId);
  useEffect(() => {
    if (shownFor.current === instrumentId) return;
    shownFor.current = instrumentId;
    if (form.fields.drawing_id) onChange({ ...form, fields: { ...form.fields, drawing_id: "" } });
  }, [instrumentId, form, onChange]);
  return (
    <fieldset className="alert-condition">
      <legend>Condition</legend>
      <label>
        Kind
        <select aria-label="Condition kind" value={form.kind} onChange={(e) => onChange(withKind(form, e.target.value as ConditionKind))}>
          {CONDITION_KINDS.map((k) => (
            <option key={k} value={k}>
              {CONDITION_LABELS[k]}
            </option>
          ))}
        </select>
      </label>
      {form.kind === "indicator" && <IndicatorFields form={form} onChange={onChange} catalog={catalog} />}
      {form.kind === "trendline_cross" && <TrendlineField instrumentId={instrumentId} form={form} onChange={onChange} />}
      {form.kind !== "indicator" &&
        CONDITION_FIELDS[form.kind]
          .filter((name) => name in FIELD_SPECS)
          .map((name) => <ScalarField key={name} name={name} form={form} onChange={onChange} />)}
    </fieldset>
  );
}
