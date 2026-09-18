import { useEffect, useRef, useState } from "react";

import { fetchIndicatorCatalog } from "../../api/client";
import type { IndicatorCatalogEntry, IndicatorConfigEntry } from "../../api/schema";
import { coerceParamValue } from "./paramCoercion";

interface IndicatorPickerProps {
  /** Where the selection lives: the chart page wraps its per-coin GET/PUT here, the
   * Technicals tab wraps the screener-wide ones (Story 17.5) -- the add/remove/param UI
   * itself is shared, only persistence differs. */
  fetchConfig: () => Promise<IndicatorConfigEntry[]>;
  saveConfig: (entries: IndicatorConfigEntry[]) => Promise<unknown>;
  /** Re-runs the initial load when it changes (a different coin, or an outside edit such as
   * a column removed from a table header). */
  reloadKey: string | number;
  /** Called with the freshly-persisted list every time it changes (initial load, add,
   * remove, or param-apply) -- `ChartPage.tsx` feeds this straight into its own `panes`
   * `useMemo`. Never called with an intermediate/unsaved draft (no auto-save-per-keystroke,
   * spec's "Never" list) -- only after a successful `PUT`, or the initial `GET`. */
  onEntriesChange: (entries: IndicatorConfigEntry[]) => void;
}

function defaultParamsFor(catalogEntry: IndicatorCatalogEntry): Record<string, unknown> {
  return { ...catalogEntry.params };
}

/**
 * Add/remove/param-controls for a persisted indicator selection (Story 15.6; generalized over its persistence in Story 17.5). The
 * catalog list always comes from `GET /api/indicators/catalog` (spec's "Never": no
 * hand-duplicated frontend catalog); every change -- add, remove, or a param "Apply" --
 * fires exactly one `saveConfig` call with the FULL updated list, never a
 * separate ad hoc endpoint and never on every keystroke.
 *
 * Owns no chart/pane state itself -- `onEntriesChange` is this component's entire surface
 * toward `ChartPage.tsx`, which alone decides how entries become panes (AD-F4: no
 * component outside `LightweightChart.tsx` calls `chart.addPane()`/`removePane()`).
 */
export default function IndicatorPicker({
  fetchConfig,
  saveConfig,
  reloadKey,
  onEntriesChange,
}: IndicatorPickerProps) {
  const [catalog, setCatalog] = useState<Record<string, IndicatorCatalogEntry>>({});
  const [entries, setEntries] = useState<IndicatorConfigEntry[]>([]);
  const [selectedName, setSelectedName] = useState("");
  const [error, setError] = useState<string | null>(null);
  // Guards the initial-load GET below against clobbering a newer, already-persisted local
  // change: if the user adds/removes/applies an indicator before that GET resolves, its
  // response is a stale snapshot -- applying it would silently revert the visible picker
  // state (and every pane downstream) even though the newer state already landed on disk,
  // and any *subsequent* edit would then build on that stale list and permanently drop the
  // earlier change on its own next PUT. Reset per reloadKey via the effect below.
  const hasLocalChangeRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    fetchIndicatorCatalog()
      .then((result) => {
        if (cancelled) return;
        setCatalog(result);
        setSelectedName((current) => current || Object.keys(result)[0] || "");
      })
      .catch((err: unknown) => console.error("IndicatorPicker: failed to load catalog", err));
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    hasLocalChangeRef.current = false;
    fetchConfig()
      .then((result) => {
        if (cancelled || hasLocalChangeRef.current) return;
        setEntries(result);
        onEntriesChange(result);
      })
      .catch((err: unknown) => console.error("IndicatorPicker: failed to load config", err));
    return () => {
      cancelled = true;
    };
    // fetchConfig/onEntriesChange are stable caller-owned functions -- only reloadKey
    // should re-trigger this fetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reloadKey]);

  function persist(next: IndicatorConfigEntry[]): void {
    // Applied optimistically (before the PUT resolves) so a rapid second Add/Remove/Apply
    // builds its own `next` from this call's result, not a stale pre-request snapshot --
    // without this, two overlapping persist() calls each compute `next` from the same old
    // `entries`, and whichever PUT response lands last silently discards the other's
    // change. Rolled back to `previous` on failure.
    const previous = entries;
    setEntries(next);
    setError(null);
    saveConfig(next)
      .then(() => {
        hasLocalChangeRef.current = true;
        onEntriesChange(next);
      })
      .catch((err: unknown) => {
        console.error("IndicatorPicker: failed to save config", err);
        setEntries(previous);
        setError(err instanceof Error ? err.message : String(err));
      });
  }

  function handleAdd(): void {
    const catalogEntry = catalog[selectedName];
    if (!catalogEntry) return;
    if (entries.some((e) => e.name === selectedName)) return; // already added
    const next: IndicatorConfigEntry[] = [
      ...entries,
      { name: selectedName, params: defaultParamsFor(catalogEntry), category: catalogEntry.category },
    ];
    persist(next);
  }

  function handleRemove(name: string): void {
    persist(entries.filter((e) => e.name !== name));
  }

  function handleApplyParams(name: string, params: Record<string, unknown>): void {
    persist(entries.map((e) => (e.name === name ? { ...e, params } : e)));
  }

  return (
    <div>
      <h3>Indicators</h3>
      {error && <p style={{ color: "var(--color-danger)" }}>{error}</p>}
      <div>
        <select value={selectedName} onChange={(e) => setSelectedName(e.target.value)}>
          {Object.entries(catalog).map(([name, entry]) => (
            <option key={name} value={name}>
              {name} ({entry.category})
            </option>
          ))}
        </select>
        <button type="button" onClick={handleAdd} disabled={!selectedName}>
          Add
        </button>
      </div>
      <ul>
        {entries.map((entry) => (
          <IndicatorEntryRow
            key={entry.name}
            entry={entry}
            onRemove={() => handleRemove(entry.name)}
            onApplyParams={(params) => handleApplyParams(entry.name, params)}
          />
        ))}
      </ul>
    </div>
  );
}

function IndicatorEntryRow({
  entry,
  onRemove,
  onApplyParams,
}: {
  entry: IndicatorConfigEntry;
  onRemove: () => void;
  onApplyParams: (params: Record<string, unknown>) => void;
}) {
  // Lazy-initialized from this entry's own persisted params; not re-synced from props on
  // every render -- this row is the sole writer of its own entry's params (via
  // onApplyParams -> persist -> onEntriesChange), so `entry.params` never changes out
  // from under an already-mounted row for reasons other than this row's own edit, which
  // already agrees with `draft` (avoids an effect-driven setState re-render loop).
  const [draft, setDraft] = useState<Record<string, unknown>>(() => entry.params ?? {});

  return (
    <li>
      <span>{entry.name}</span>
      {Object.entries(draft).map(([key, value]) => (
        <label key={key}>
          {key}:
          <input
            value={String(value)}
            onChange={(e) => setDraft((prev) => ({ ...prev, [key]: coerceParamValue(value, e.target.value) }))}
          />
        </label>
      ))}
      {Object.keys(draft).length > 0 && (
        <button type="button" onClick={() => onApplyParams(draft)}>
          Apply
        </button>
      )}
      <button type="button" onClick={onRemove}>
        Remove
      </button>
    </li>
  );
}
