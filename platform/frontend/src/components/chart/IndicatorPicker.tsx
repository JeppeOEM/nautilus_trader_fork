import { type Ref, useEffect, useImperativeHandle, useRef, useState } from "react";

import { fetchIndicatorCatalog } from "../../api/client";
import type { IndicatorCatalogEntry, IndicatorConfigEntry } from "../../api/schema";
import { DERIVATIVE_KEYS, DERIVATIVE_LABELS, type DerivativeKey } from "../../lib/chartLayout";
import { DEFAULT_SOURCE, copyId, entryId, indicatorId, valuesId } from "../../lib/indicatorId";
import IndicatorSettingsDialog, { type SettingsOutput, type SettingsPatch } from "./IndicatorSettingsDialog";
import ParamInputs from "./ParamInputs";
import { coerceParams, invalidParamKeys, rawFromParams } from "./paramCoercion";

/** What the chart's legend buttons drive (Story 32.3): each acts on one configured instance, by
 * its `indicatorId`, and persists through the same path as an add. */
export interface IndicatorPickerHandle {
  toggleHidden: (id: string) => void;
  remove: (id: string) => void;
  openSettings: (id: string) => void;
}

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
  /** Show the select + Add + list below (the Technicals tab, its only editing surface). The chart
   * page sets it false: it adds through the dialog and edits from the legend. */
  showEntryList?: boolean;
  /** Allow the same indicator several times with different params (RSI(14) + RSI(21)). Off by
   * default: the Technicals tab's filter fields are keyed by indicator name alone. */
  multiInstance?: boolean;
  /** Story 32.2: volume is the dialog's pinned first entry (no params, no catalog row), so its
   * state lives with the caller. The row shows only when both are given (not the Technicals tab). */
  volumeOn?: boolean;
  onVolumeChange?: (on: boolean) => void;
  /** Story 32.8: the Footprint toggle, pinned next to Volume (its state is the coin's layout, held
   * by the caller); shown only when both are given. `footprintCandlesOnly` notes that it draws in
   * Candles mode only, while another mode is active. */
  footprintOn?: boolean;
  onFootprintChange?: (on: boolean) => void;
  footprintCandlesOnly?: boolean;
  /** Story 33.5: the pinned Derivatives group (Open Interest, Funding, Basis, Mark / Index,
   * Liquidations), each entry's on-state from the coin's layout; shown only when both are given.
   * `derivativesDisabled` (a spot instrument) disables every checkbox and tags the group
   * "spot: no derivatives", the saved on-states kept. */
  derivatives?: Readonly<Record<DerivativeKey, boolean>>;
  onDerivativeChange?: (key: DerivativeKey, on: boolean) => void;
  derivativesDisabled?: boolean;
  /** The chart is not in Candles mode: the derivatives share the candles' bar axis, so the group is
   * disabled and tagged "Candles mode only" (the Footprint's rule). */
  derivativesCandlesOnly?: boolean;
  /** The legend's eye/gear/x land here (chart page only). */
  ref?: Ref<IndicatorPickerHandle>;
  /** The settings modal's title (the legend title) and Style rows for one instance id. */
  titleFor?: (entry: IndicatorConfigEntry) => string;
  outputsFor?: (id: string) => SettingsOutput[];
}

/** Whether `entries` already hold an entry drawn under the id `name`/`params`/`source`/`instance`
 * would take (single-instance: any entry of `name`). Two entries under one id would share one
 * series key and legend row, so neither could be removed or restyled alone. */
function hasInstance(
  entries: IndicatorConfigEntry[],
  name: string,
  params: Record<string, unknown>,
  multiInstance: boolean,
  source: string = DEFAULT_SOURCE,
  instance?: number,
): boolean {
  const id = copyId(indicatorId(name, params, source), instance);
  return entries.some((e) => e.name === name && (!multiInstance || entryId(e) === id));
}

/** The copy number a new entry of `name`/`params`/`source` takes (chart UX rework, 2026-10-08): 1
 * when none is configured, else one above the highest configured copy, so an indicator can be added
 * again with its default settings and then set apart in its settings. */
function nextInstance(entries: IndicatorConfigEntry[], name: string, params: Record<string, unknown>, source: string = DEFAULT_SOURCE): number {
  const id = indicatorId(name, params, source);
  const copies = entries.filter((e) => valuesId(e) === id).map((e) => e.instance ?? 1);
  return copies.length === 0 ? 1 : Math.max(...copies) + 1;
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
  showEntryList = true,
  multiInstance = false,
  volumeOn,
  onVolumeChange,
  footprintOn,
  onFootprintChange,
  footprintCandlesOnly = false,
  derivatives,
  onDerivativeChange,
  derivativesDisabled = false,
  derivativesCandlesOnly = false,
  ref,
  titleFor,
  outputsFor,
}: IndicatorPickerProps) {
  const [catalog, setCatalog] = useState<Record<string, IndicatorCatalogEntry>>({});
  const [entries, setEntries] = useState<IndicatorConfigEntry[]>([]);
  // The latest list, updated the moment it changes (not on the next render): the legend's handlers
  // are called back to back, and each must build on the previous one's result, not a stale render.
  const entriesRef = useRef<IndicatorConfigEntry[]>([]);
  function applyEntries(next: IndicatorConfigEntry[]): void {
    entriesRef.current = next;
    setEntries(next);
  }
  // Each entry object's identity, stable across optimistic edits and rollbacks: a patched copy
  // inherits its original's (`patched`), and a rollback restores the very objects it replaced.
  const identities = useRef(new WeakMap<IndicatorConfigEntry, number>());
  const lastIdentity = useRef(0);
  function identityOf(entry: IndicatorConfigEntry): number {
    let identity = identities.current.get(entry);
    if (identity === undefined) {
      identity = ++lastIdentity.current;
      identities.current.set(entry, identity);
    }
    return identity;
  }
  function patched(entry: IndicatorConfigEntry, patch: Partial<IndicatorConfigEntry>): IndicatorConfigEntry {
    const next = { ...entry, ...patch };
    identities.current.set(next, identityOf(entry));
    return next;
  }
  const indexOfIdentity = (identity: number): number =>
    entriesRef.current.findIndex((e) => identityOf(e) === identity);
  const [selectedName, setSelectedName] = useState("");
  const [error, setError] = useState<string | null>(null);
  // The entry the settings modal edits, by identity, not by id or position: an Apply changes the
  // entry's id (params, source) the moment it is applied optimistically, and a failed save's
  // rollback can shift positions (a remove put back). The modal stays mounted -- with its drafts --
  // until the save lands or fails. `outputs` is the Style rows as drawn when it opened: the
  // optimistic id has no series yet. `open` remounts it per opening.
  const [settings, setSettings] = useState<{ identity: number; open: number; outputs: SettingsOutput[] } | null>(
    null,
  );
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
    setSettings(null); // another coin's list
    fetchConfig()
      .then((result) => {
        if (cancelled || hasLocalChangeRef.current) return;
        applyEntries(result);
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

  // Resolves to null once saved, or to the save's error message after the rollback.
  function persist(next: IndicatorConfigEntry[]): Promise<string | null> {
    // Applied optimistically (before the PUT resolves) so a rapid second Add/Remove/Apply
    // builds its own `next` from this call's result, not a stale pre-request snapshot --
    // without this, two overlapping persist() calls each compute `next` from the same old
    // `entries`, and whichever PUT response lands last silently discards the other's
    // change. Rolled back to `previous` on failure.
    const previous = entriesRef.current;
    applyEntries(next);
    setError(null);
    return saveConfig(next)
      .then(() => {
        hasLocalChangeRef.current = true;
        onEntriesChange(next);
        return null;
      })
      .catch((err: unknown) => {
        console.error("IndicatorPicker: failed to save config", err);
        applyEntries(previous);
        const message = err instanceof Error ? err.message : String(err);
        setError(message);
        return message;
      });
  }

  function addByName(name: string): void {
    const catalogEntry = catalog[name];
    if (!catalogEntry) return;
    const params = defaultParamsFor(catalogEntry);
    const current = entriesRef.current;
    if (!multiInstance && hasInstance(current, name, params, false)) return; // already added
    const instance = multiInstance ? nextInstance(current, name, params) : 1;
    void persist([...current, { name, params, category: catalogEntry.category, ...(instance > 1 ? { instance } : {}) }]);
  }

  function handleAdd(): void {
    addByName(selectedName);
  }

  function handleRemove(index: number): void {
    void persist(entriesRef.current.filter((_, i) => i !== index));
  }

  // Merges `patch` into one entry. Resolves to the refusal message (a duplicate instance) or the
  // save's error, or to null once the change is persisted.
  function handleApply(index: number, patch: Partial<SettingsPatch>): Promise<string | null> {
    const current = entriesRef.current;
    const entry = current[index];
    const others = current.filter((_, i) => i !== index);
    if (
      hasInstance(
        others,
        entry.name,
        patch.params ?? entry.params ?? {},
        multiInstance,
        patch.source ?? entry.source ?? DEFAULT_SOURCE,
        entry.instance,
      )
    ) {
      // Two identical instances would share one series key and draw on top of each other.
      const message = multiInstance
        ? `${entry.name} with those params and source is already added`
        : `${entry.name} is already added`;
      setError(message);
      return Promise.resolve(message);
    }
    return persist(current.map((e, i) => (i === index ? patched(e, patch) : e)));
  }

  function handleApplyParams(index: number, params: Record<string, unknown>): void {
    void handleApply(index, { params });
  }

  const indexOfId = (id: string): number => entriesRef.current.findIndex((e) => entryId(e) === id);
  useImperativeHandle(ref, () => ({
    toggleHidden: (id) => {
      const index = indexOfId(id);
      if (index !== -1) {
        void persist(entriesRef.current.map((e, i) => (i === index ? patched(e, { hidden: !e.hidden }) : e)));
      }
    },
    remove: (id) => {
      const index = indexOfId(id);
      if (index !== -1) handleRemove(index);
    },
    openSettings: (id) => {
      const index = indexOfId(id);
      if (index === -1) return;
      const identity = identityOf(entriesRef.current[index]);
      const outputs = outputsFor?.(id) ?? [];
      setSettings((s) => ({ identity, open: (s?.open ?? 0) + 1, outputs }));
    },
  }));

  // -1 once its entry is gone (removed, or a rolled-back add): the modal closes.
  const settingsIndex = settings === null ? -1 : entries.findIndex((e) => identityOf(e) === settings.identity);
  // Resolved at call time, against the latest list, never a render's position.
  function applySettings(identity: number, patch: Partial<SettingsPatch>): Promise<string | null> {
    const index = indexOfIdentity(identity);
    return index === -1 ? Promise.resolve("This indicator was removed") : handleApply(index, patch);
  }
  function removeSettings(identity: number): void {
    const index = indexOfIdentity(identity);
    if (index !== -1) handleRemove(index);
  }

  return (
    <div>
      {onDialogClose && (
        <IndicatorDialog
          open={dialogOpen}
          onClose={onDialogClose}
          catalog={catalog}
          addedNames={Object.keys(catalog).filter((n) => entries.some((e) => e.name === n))}
          multiInstance={multiInstance}
          disabled={disabled}
          onAdd={addByName}
          volumeOn={volumeOn}
          onVolumeChange={onVolumeChange}
          footprintOn={footprintOn}
          onFootprintChange={onFootprintChange}
          footprintCandlesOnly={footprintCandlesOnly}
          derivatives={derivatives}
          onDerivativeChange={onDerivativeChange}
          derivativesDisabled={derivativesDisabled}
          derivativesCandlesOnly={derivativesCandlesOnly}
        />
      )}
      {error && <p style={{ color: "var(--color-danger)" }}>{error}</p>}
      {settings !== null && settingsIndex !== -1 && (
        <IndicatorSettingsDialog
          key={settings.open}
          title={titleFor ? titleFor(entries[settingsIndex]) : entries[settingsIndex].name}
          entry={entries[settingsIndex]}
          catalogEntry={catalog[entries[settingsIndex].name]}
          outputs={settings.outputs}
          disabled={disabled}
          onApply={(patch) => applySettings(settings.identity, patch)}
          onRemove={() => removeSettings(settings.identity)}
          onClose={() => setSettings(null)}
        />
      )}
      {/* The chart page adds through the dialog above and edits/hides/removes from the legend
          (Story 32.3, DESIGN-03: one add path). The Technicals tab has neither, so its list,
          select and Add stay -- its only editing surface. */}
      {showEntryList && (
        <>
          <h3>Indicators</h3>
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
                choices={catalog[entry.name]?.choices ?? {}}
                disabled={disabled}
                onRemove={() => handleRemove(index)}
                onApplyParams={(params) => handleApplyParams(index, params)}
              />
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function IndicatorEntryRow({
  entry,
  choices,
  disabled,
  onRemove,
  onApplyParams,
}: {
  entry: IndicatorConfigEntry;
  /** The catalog's allowed names per enum param (Story 27.7): those params get a dropdown. */
  choices: Record<string, string[]>;
  disabled: boolean;
  onRemove: () => void;
  onApplyParams: (params: Record<string, unknown>) => void;
}) {
  // Lazy-initialized from this entry's own persisted params; not re-synced from props on
  // every render -- this row is the sole writer of its own entry's params (via
  // onApplyParams -> persist -> onEntriesChange), so `entry.params` never changes out
  // from under an already-mounted row for reasons other than this row's own edit.
  const params = entry.params ?? {};
  const [raw, setRaw] = useState(() => rawFromParams(params));
  const invalidKeys = invalidParamKeys(params, raw, choices);

  return (
    <li>
      <span>{entry.name}</span>
      <ParamInputs
        params={params}
        raw={raw}
        choices={choices}
        invalidKeys={invalidKeys}
        onEdit={(key, value) => setRaw((prev) => ({ ...prev, [key]: value }))}
      />
      {Object.keys(params).length > 0 && (
        <button
          type="button"
          disabled={disabled || invalidKeys.length > 0}
          onClick={() => onApplyParams(coerceParams(params, raw))}
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
  multiInstance,
  disabled,
  onAdd,
  volumeOn,
  onVolumeChange,
  footprintOn,
  onFootprintChange,
  footprintCandlesOnly,
  derivatives,
  onDerivativeChange,
  derivativesDisabled,
  derivativesCandlesOnly,
}: {
  open: boolean;
  onClose: () => void;
  catalog: Record<string, IndicatorCatalogEntry>;
  addedNames: string[];
  /** An added indicator can be added again (another copy), so its row stays enabled. */
  multiInstance: boolean;
  disabled: boolean;
  onAdd: (name: string) => void;
  volumeOn?: boolean;
  onVolumeChange?: (on: boolean) => void;
  footprintOn?: boolean;
  onFootprintChange?: (on: boolean) => void;
  footprintCandlesOnly: boolean;
  derivatives?: Readonly<Record<DerivativeKey, boolean>>;
  onDerivativeChange?: (key: DerivativeKey, on: boolean) => void;
  derivativesDisabled: boolean;
  derivativesCandlesOnly: boolean;
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
      {((onVolumeChange && volumeOn !== undefined) || (onFootprintChange && footprintOn !== undefined)) && (
        <div className="indicator-dialog-pinned">
          {onVolumeChange && volumeOn !== undefined && (
            <label>
              <input
                type="checkbox"
                checked={volumeOn}
                disabled={disabled}
                onChange={(e) => onVolumeChange(e.target.checked)}
              />
              <span>Volume</span>
            </label>
          )}
          {onFootprintChange && footprintOn !== undefined && (
            <label>
              <input
                type="checkbox"
                checked={footprintOn}
                disabled={disabled}
                onChange={(e) => onFootprintChange(e.target.checked)}
              />
              <span>Footprint</span>
              {footprintCandlesOnly && <span className="indicator-dialog-tag">Candles mode only</span>}
            </label>
          )}
        </div>
      )}
      {derivatives && onDerivativeChange && (
        <div className="indicator-dialog-pinned" role="group" aria-label="Derivatives">
          <span className="indicator-dialog-group">Derivatives</span>
          {derivativesDisabled && <span className="indicator-dialog-tag">spot: no derivatives</span>}
          {!derivativesDisabled && derivativesCandlesOnly && <span className="indicator-dialog-tag">Candles mode only</span>}
          {DERIVATIVE_KEYS.map((key) => (
            <label key={key}>
              <input
                type="checkbox"
                checked={derivatives[key]}
                disabled={disabled || derivativesDisabled || derivativesCandlesOnly}
                onChange={(e) => onDerivativeChange(key, e.target.checked)}
              />
              <span>{DERIVATIVE_LABELS[key]}</span>
            </label>
          ))}
        </div>
      )}
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
              <button type="button" disabled={(added && !multiInstance) || disabled} onClick={() => onAdd(name)}>
                <span>{name}</span>
                <span className="indicator-dialog-tag">
                  {added ? (multiInstance ? "added · click to add another" : "added") : dialogCategoryOf(entry)}
                </span>
              </button>
            </li>
          );
        })}
        {results.length === 0 && <li className="indicator-dialog-empty">No matches</li>}
      </ul>
    </dialog>
  );
}
