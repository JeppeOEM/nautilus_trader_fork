import { useCallback, useEffect, useRef, useState } from "react";

import { type FilterPresetItem, fetchFilterPresets, saveFilterPresets } from "../api/client";
import type { FilterField } from "./FilterPanel";
import { type DisplayPrecision, FILTER_OPERATORS, type FilterCondition, type FilterOperator } from "./filters";

// Story 33.7: named filter presets, one server-side list (`GET`/`PUT /api/rankings/filter-presets`,
// `screener_filter_presets.toml`) -- never localStorage, so every browser sees the same presets.
// A stored condition carries no display precision: it is re-derived from the page's current field
// list on recall, so a column's precision change never makes a stored preset stale.

export type StoredCondition = FilterPresetItem["conditions"][number];

/** The conditions as stored: field, operator, value (the precision stays behind). */
export function toStoredConditions(conditions: FilterCondition[]): StoredCondition[] {
  return conditions.map(({ field, op, value }) => ({ field, op, value }));
}

function isOperator(op: string): op is FilterOperator {
  return (FILTER_OPERATORS as string[]).includes(op);
}

/** Why a stored condition was not applied on recall. */
export type NotAppliedReason =
  | "unknown field"
  | "Technicals columns not loaded"
  | "operator does not fit the field"
  | "value does not fit the field";

/** One stored condition as a live one, or why it cannot be applied at today's field list. */
function recallCondition(
  stored: StoredCondition,
  fields: FilterField[],
  extraPrecision: (field: string) => DisplayPrecision | NotAppliedReason,
): FilterCondition | NotAppliedReason {
  const field = fields.find((f) => f.key === stored.field);
  if (field?.text === true) {
    if (stored.op !== "=") return "operator does not fit the field";
    return typeof stored.value === "string" ? { field: field.key, op: "=", value: stored.value } : "value does not fit the field";
  }
  const precision = field?.precision ?? extraPrecision(stored.field);
  if (typeof precision === "string") return precision;
  if (!isOperator(stored.op)) return "operator does not fit the field";
  if (typeof stored.value !== "number") return "value does not fit the field";
  return { field: stored.field, op: stored.op, value: stored.value, precision };
}

export interface NotApplied {
  /** The condition exactly as stored, kept so a later Save writes it back unchanged. */
  stored: StoredCondition;
  reason: NotAppliedReason;
}

export interface RecalledPreset {
  conditions: FilterCondition[];
  /** Conditions not applied: an unknown field (a removed column) or one they no longer fit. */
  notApplied: NotApplied[];
}

/** A preset's conditions at today's field list; `extraPrecision` resolves fields the list does not
 * hold yet (a Technicals output whose values are still loading), or says why it cannot (an unknown
 * field, or the Technicals columns not loaded yet to tell). Nothing is dropped silently: a
 * condition that cannot be applied is returned in `notApplied` with its reason, and the page keeps
 * it on the next Save while its notice is shown.
 *
 * Known limit: a stored field can outlive its column (a removed Technicals column), and the stored
 * preset keeps it -- it is only reported at each recall (audit D-196). Upgrade path: a server-side
 * check of preset fields against the published column set. */
export function recallPreset(
  preset: FilterPresetItem,
  fields: FilterField[],
  extraPrecision: (field: string) => DisplayPrecision | NotAppliedReason,
): RecalledPreset {
  const recalled: RecalledPreset = { conditions: [], notApplied: [] };
  for (const stored of preset.conditions) {
    const condition = recallCondition(stored, fields, extraPrecision);
    if (typeof condition === "string") recalled.notApplied.push({ stored, reason: condition });
    else recalled.conditions.push(condition);
  }
  return recalled;
}

/** The notice for conditions a recall could not apply, each with its reason. */
export function notAppliedNotice(notApplied: NotApplied[]): string | null {
  if (notApplied.length === 0) return null;
  const items = notApplied.map(({ stored, reason }) => `${stored.field} (${reason})`).join(", ");
  return `not applied: ${items} — kept on save`;
}

/** The list with `name`'s preset replaced in place, or appended when new. */
export function upsertPreset(presets: FilterPresetItem[], preset: FilterPresetItem): FilterPresetItem[] {
  return presets.some((p) => p.name === preset.name)
    ? presets.map((p) => (p.name === preset.name ? preset : p))
    : [...presets, preset];
}

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export interface FilterPresetsState {
  presets: FilterPresetItem[];
  /** True once a GET has succeeded: before it, the list is unknown, and a Save (a whole-list PUT)
   * would replace every stored preset with the new one alone -- so Save and Delete wait for it. */
  loaded: boolean;
  error: string | null;
  busy: boolean;
  /** PUT the whole list; `onSaved` runs only once the server stored it. A failure is shown inline,
   * reported to console.error (the ErrorBar) and the list reloaded from the server. */
  persist: (next: FilterPresetItem[], onSaved?: () => void) => void;
  /** Clear the error and GET the list again: the page offers it after a failed load, which nothing
   * else retries. */
  retry: () => void;
}

export function useFilterPresets(): FilterPresetsState {
  const [presets, setPresets] = useState<FilterPresetItem[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Every GET and PUT takes the next number; a response applies only while it is the latest, so a
  // slow mount GET can never overwrite the list a later PUT stored.
  const latestRequest = useRef(0);

  const reload = useCallback((): void => {
    const request = ++latestRequest.current;
    fetchFilterPresets()
      .then((loadedPresets) => {
        if (request !== latestRequest.current) return;
        setPresets(loadedPresets);
        setLoaded(true);
      })
      .catch((err: unknown) => {
        console.error("RankingsPage: failed to load filter presets", err);
        if (request === latestRequest.current) setError(`load failed: ${message(err)}`);
      });
  }, []);
  useEffect(reload, [reload]);

  function retry(): void {
    setError(null);
    reload();
  }

  function persist(next: FilterPresetItem[], onSaved?: () => void): void {
    const request = ++latestRequest.current;
    setBusy(true);
    setError(null);
    saveFilterPresets(next)
      .then((stored) => {
        if (request === latestRequest.current) {
          setPresets(stored);
          setLoaded(true);
        }
        onSaved?.();
      })
      .catch((err: unknown) => {
        console.error("RankingsPage: failed to save filter presets", err);
        setError(`save failed: ${message(err)}`);
        reload();
      })
      .finally(() => setBusy(false));
  }

  return { presets, loaded, error, busy, persist, retry };
}
