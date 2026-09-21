import { useEffect, useRef, useState } from "react";

import { fetchIndicatorCatalog } from "../../api/client";
import type { IndicatorCatalogEntry, IndicatorConfigEntry } from "../../api/schema";
import { coerceParamValue, isValidParamText } from "./paramCoercion";

interface IndicatorPickerProps {
  /** Where the selection lives: the chart page wraps its per-coin GET/PUT here, the
   * Technicals tab wraps the screener-wide ones (Story 17.5) -- the add/remove/param UI
   * itself is shared, only persistence differs. */
  fetchConfig: () => Promise<IndicatorConfigEntry[]>;
  saveConfig: (entries: IndicatorConfigEntry[]) => Promise<unknown>;
  /** Re-runs the initial load when it changes (a different coin, or an outside edit such as
   * a column removed from a table header). */
  reloadKey: string | number;
  /** Blocks edits while another writer's save is in flight (a Technicals header action), so this
   * picker can't PUT a list built from its own now-stale copy and undo that change. */
  disabled?: boolean;
  /** Called with the freshly-persisted list every time it changes (initial load, add,
   * remove, or param-apply) -- `ChartPage.tsx` feeds this straight into its own `panes`
   * `useMemo`. Never called with an intermediate/unsaved draft (no auto-save-per-keystroke,
   * spec's "Never" list) -- only after a successful `PUT`, or the initial `GET`. */
  onEntriesChange: (entries: IndicatorConfigEntry[]) => void;
  /** Spec §A4.1's TradingView-style search dialog: opened by the chart toolbar's
   * "Indicators" button. Omitted (Technicals tab) = no dialog, just the select+Add below. */
  dialogOpen?: boolean;
  onDialogClose?: () => void;
  /** Allow the same indicator several times with different params (RSI(14) + RSI(21)). Off by
   * default: the Technicals tab's filter fields are keyed by indicator name alone. */
  multiInstance?: boolean;
}

function hasInstance(
  entries: IndicatorConfigEntry[],
  name: string,
  params: Record<string, unknown>,
  multiInstance: boolean,
): boolean {
  return entries.some(
    (e) => e.name === name && (!multiInstance || JSON.stringify(e.params ?? {}) === JSON.stringify(params)),
  );
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
  disabled = false,
  onEntriesChange,
  dialogOpen = false,
  onDialogClose,
  multiInstance = false,
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

  function addByName(name: string): void {
    const catalogEntry = catalog[name];
    if (!catalogEntry) return;
    const params = defaultParamsFor(catalogEntry);
    if (hasInstance(entries, name, params, multiInstance)) return; // already added
    persist([...entries, { name, params, category: catalogEntry.category }]);
  }

  function handleAdd(): void {
    addByName(selectedName);
  }

  function handleRemove(index: number): void {
    persist(entries.filter((_, i) => i !== index));
  }

  function handleApplyParams(index: number, params: Record<string, unknown>): void {
    const others = entries.filter((_, i) => i !== index);
    if (hasInstance(others, entries[index].name, params, multiInstance)) {
      // Two identical instances would share one series key and draw on top of each other.
      setError(`${entries[index].name} with those params is already added`);
      return;
    }
    persist(entries.map((e, i) => (i === index ? { ...e, params } : e)));
  }

  return (
    <div>
      {onDialogClose && (
        <IndicatorDialog
          open={dialogOpen}
          onClose={onDialogClose}
          catalog={catalog}
          addedNames={Object.keys(catalog).filter((n) =>
            hasInstance(entries, n, defaultParamsFor(catalog[n]), multiInstance),
          )}
          disabled={disabled}
          onAdd={addByName}
        />
      )}
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
        <button type="button" onClick={handleAdd} disabled={!selectedName || disabled}>
          Add
        </button>
      </div>
      <ul>
        {entries.map((entry, index) => (
          <IndicatorEntryRow
            key={`${entry.name}:${JSON.stringify(entry.params)}`}
            entry={entry}
            disabled={disabled}
            onRemove={() => handleRemove(index)}
            onApplyParams={(params) => handleApplyParams(index, params)}
          />
        ))}
      </ul>
    </div>
  );
}

function IndicatorEntryRow({
  entry,
  disabled,
  onRemove,
  onApplyParams,
}: {
  entry: IndicatorConfigEntry;
  disabled: boolean;
  onRemove: () => void;
  onApplyParams: (params: Record<string, unknown>) => void;
}) {
  // Lazy-initialized from this entry's own persisted params; not re-synced from props on
  // every render -- this row is the sole writer of its own entry's params (via
  // onApplyParams -> persist -> onEntriesChange), so `entry.params` never changes out
  // from under an already-mounted row for reasons other than this row's own edit, which
  // already agrees with `draft` (avoids an effect-driven setState re-render loop).
  const params = entry.params ?? {};
  const [raw, setRaw] = useState<Record<string, string>>(() =>
    Object.fromEntries(Object.entries(params).map(([k, v]) => [k, String(v)])),
  );
  // Invalid text is shown, not silently reverted -- coerceParamValue alone would keep the
  // prior value and leave the field looking like it ignored the keystroke.
  const invalidKeys = Object.keys(params).filter((k) => !isValidParamText(params[k], raw[k] ?? ""));

  return (
    <li>
      <span>{entry.name}</span>
      {Object.keys(params).map((key) => (
        <label key={key}>
          {key}:
          <input
            value={raw[key] ?? ""}
            aria-invalid={invalidKeys.includes(key)}
            onChange={(e) => setRaw((prev) => ({ ...prev, [key]: e.target.value }))}
          />
        </label>
      ))}
      {invalidKeys.length > 0 && <span role="alert">Invalid value for {invalidKeys.join(", ")}</span>}
      {Object.keys(params).length > 0 && (
        <button
          type="button"
          disabled={disabled || invalidKeys.length > 0}
          onClick={() =>
            onApplyParams(
              Object.fromEntries(Object.keys(params).map((k) => [k, coerceParamValue(params[k], raw[k] ?? "")])),
            )
          }
        >
          Apply
        </button>
      )}
      <button type="button" disabled={disabled} onClick={onRemove}>
        Remove
      </button>
    </li>
  );
}

type DialogCategory = "all" | "overlay" | "oscillator";

// Spec §A4.1's flat category list is just Overlays + Oscillators; the catalog's
// "histogram" panel is a kind of oscillator here.
function dialogCategoryOf(entry: IndicatorCatalogEntry): DialogCategory {
  return entry.panel === "overlay" ? "overlay" : "oscillator";
}

function IndicatorDialog({
  open,
  onClose,
  catalog,
  addedNames,
  disabled,
  onAdd,
}: {
  open: boolean;
  onClose: () => void;
  catalog: Record<string, IndicatorCatalogEntry>;
  addedNames: string[];
  disabled: boolean;
  onAdd: (name: string) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<DialogCategory>("all");

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      // showModal gives Esc-to-close, a backdrop and focus trapping natively; jsdom lacks it.
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    } else if (!open && dialog.open) {
      if (typeof dialog.close === "function") dialog.close();
      else dialog.removeAttribute("open");
    }
  }, [open]);

  const needle = query.trim().toLowerCase();
  const results = Object.entries(catalog).filter(
    ([name, entry]) =>
      name.toLowerCase().includes(needle) && (category === "all" || dialogCategoryOf(entry) === category),
  );

  return (
    <dialog ref={ref} className="indicator-dialog" aria-label="Indicators" onClose={onClose}>
      <div className="indicator-dialog-head">
        <input
          type="search"
          placeholder="Search indicators"
          aria-label="Search indicators"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button type="button" aria-label="Close indicators" onClick={onClose}>
          &times;
        </button>
      </div>
      <div className="indicator-dialog-cats" role="group" aria-label="Category">
        {(["all", "overlay", "oscillator"] as const).map((c) => (
          <button
            key={c}
            type="button"
            className={category === c ? "tabbtn active" : "tabbtn"}
            aria-pressed={category === c}
            onClick={() => setCategory(c)}
          >
            {c === "all" ? "All" : c === "overlay" ? "Overlays" : "Oscillators"}
          </button>
        ))}
      </div>
      <ul className="indicator-dialog-results">
        {results.map(([name, entry]) => {
          const added = addedNames.includes(name);
          return (
            <li key={name}>
              <button type="button" disabled={added || disabled} onClick={() => onAdd(name)}>
                <span>{name}</span>
                <span className="indicator-dialog-tag">{added ? "added" : dialogCategoryOf(entry)}</span>
              </button>
            </li>
          );
        })}
        {results.length === 0 && <li className="indicator-dialog-empty">No matches</li>}
      </ul>
    </dialog>
  );
}
