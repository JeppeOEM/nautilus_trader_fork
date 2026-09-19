// Story 18.5 (AC #4): the settings every Volume Profile variant shares, built once.
// Stories 18.6-18.9 mount this from their own gear entries and add only their
// variant-specific fields next to it.

import { useState } from "react";

import { MAX_PROFILE_ROWS, type VolumeProfileSettings } from "../../lib/volumeProfile";

const MAX_ROWS = MAX_PROFILE_ROWS;

const clamp = (n: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, n));

// The text is kept locally while typing so a momentarily empty field (backspacing "24" to
// type "48") isn't snapped back to the committed value mid-edit; only valid numbers are
// committed, and blur drops the draft.
function NumberField({
  label,
  ariaLabel,
  value,
  min,
  max,
  onCommit,
}: {
  label: string;
  ariaLabel: string;
  value: number;
  min: number;
  max: number;
  onCommit: (n: number) => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  return (
    <label>
      {label}
      <input
        type="number"
        aria-label={ariaLabel}
        min={min}
        max={max}
        value={draft ?? String(value)}
        onChange={(e) => {
          const raw = e.target.value;
          setDraft(raw);
          const n = Number(raw);
          if (raw.trim() !== "" && !Number.isNaN(n)) onCommit(clamp(Math.round(n), min, max));
        }}
        onBlur={() => setDraft(null)}
      />
    </label>
  );
}

// Story 18.8 (AC #4): SVP/PVP add "how many past sessions to render"; a value that carries
// `sessionCount` gets that extra field, every other variant's settings are unchanged.
type PanelSettings = VolumeProfileSettings & { sessionCount?: number };

interface Props<T extends PanelSettings> {
  value: T;
  onChange: (next: T) => void;
  /** Accessible name of the group: several panels can be on screen at once. */
  title?: string;
  /** Upper bound of the sessions field (only used when `value.sessionCount` is set). */
  maxSessions?: number;
}

export default function VolumeProfileSettingsPanel<T extends PanelSettings>({ value, onChange, title = "Volume profile settings", maxSessions = 10 }: Props<T>) {
  const set = (patch: Partial<PanelSettings>): void => onChange({ ...value, ...patch });
  return (
    <fieldset aria-label={title}>
      <NumberField
        label="Rows"
        ariaLabel="Row count"
        value={value.rowCount}
        min={1}
        max={MAX_ROWS}
        onCommit={(rowCount) => set({ rowCount })}
      />
      <NumberField
        label="Value area %"
        ariaLabel="Value area percent"
        value={value.valueAreaPercent}
        min={1}
        max={100}
        onCommit={(valueAreaPercent) => set({ valueAreaPercent })}
      />
      {value.sessionCount !== undefined && (
        <NumberField
          label="Sessions"
          ariaLabel="Sessions to render"
          value={value.sessionCount}
          min={1}
          max={maxSessions}
          onCommit={(sessionCount) => set({ sessionCount })}
        />
      )}
      <label>
        Up
        <input type="color" aria-label="Up volume color" value={value.upColor} onChange={(e) => set({ upColor: e.target.value })} />
      </label>
      <label>
        Down
        <input
          type="color"
          aria-label="Down volume color"
          value={value.downColor}
          onChange={(e) => set({ downColor: e.target.value })}
        />
      </label>
      <label>
        <input type="checkbox" aria-label="Show POC" checked={value.showPoc} onChange={(e) => set({ showPoc: e.target.checked })} />
        POC
      </label>
      <label>
        <input
          type="checkbox"
          aria-label="Show value area"
          checked={value.showValueArea}
          onChange={(e) => set({ showValueArea: e.target.checked })}
        />
        Value area
      </label>
    </fieldset>
  );
}
