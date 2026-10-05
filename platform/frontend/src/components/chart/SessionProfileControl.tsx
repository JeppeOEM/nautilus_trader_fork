import { useState } from "react";

import { AUTO_ANCHOR_PRESETS, type AutoAnchorPreset } from "../../lib/autoAnchor";
import { MAX_IB_MINUTES, MIN_IB_MINUTES } from "../../lib/tpo";
import {
  SESSION_PERIODS,
  SESSION_PERIOD_LABELS,
  SESSION_PRESETS,
  type SessionPeriod,
  type SessionPreset, type SessionProfileSettings,
  MAX_SESSIONS,
} from "../../lib/sessionProfile";
import VolumeProfileSettingsPanel from "./VolumeProfileSettings";

interface Props {
  /** The active preset and its settings, or null when no session profile is on. */
  active: {
    preset: SessionPreset;
    period: SessionPeriod;
    settings: SessionProfileSettings;
    /** Story 32.7: the Auto Anchored preset, the TPO's initial balance minutes and letters switch. */
    anchor: AutoAnchorPreset;
    ibMinutes: number;
    letters: boolean;
  } | null;
  candlesMode: boolean;
  /** How many sessions/periods are actually drawn right now (0 while history loads). */
  renderedCount: number;
  /** Older session history is being paged in right now. */
  loading: boolean;
  onAdd: (preset: SessionPreset) => void;
  onRemove: () => void;
  onPeriodChange: (period: SessionPeriod) => void;
  onOptionsChange: (options: { anchor?: AutoAnchorPreset; ibMinutes?: number; letters?: boolean }) => void;
  onSettingsChange: (next: SessionProfileSettings) => void;
}

// Story 18.8/18.9 (AC #1/#3): SVP, SVP HD and PVP are buttons over ONE component/state -- picking
// one while the other is on switches the preset (one session profile per chart). Placed next
// to the VRVP control for the same reason (chart-only overlay, not a server-catalog entry).
/** The initial-balance field: a local draft, so it can be cleared and retyped; only a whole number the
 * server stores is committed (on change), and a blur puts the saved value back over a refused draft. */
function IbMinutesInput({ value, onCommit }: { value: number; onCommit: (minutes: number) => void }) {
  const [draft, setDraft] = useState(String(value));
  const [seen, setSeen] = useState(value);
  if (value !== seen) {
    setSeen(value);
    setDraft(String(value));
  }
  return (
    <input
      type="number"
      aria-label="Initial balance minutes"
      min={MIN_IB_MINUTES}
      max={MAX_IB_MINUTES}
      step={1}
      value={draft}
      onChange={(e) => {
        setDraft(e.target.value);
        const minutes = Number(e.target.value);
        if (e.target.value.trim() !== "" && Number.isInteger(minutes) && minutes >= MIN_IB_MINUTES && minutes <= MAX_IB_MINUTES) {
          onCommit(minutes);
        }
      }}
      onBlur={() => setDraft(String(value))}
    />
  );
}

const ANCHOR_LABELS: Record<AutoAnchorPreset, string> = {
  session: "Session",
  week: "Week",
  month: "Month",
  highest_high: "Highest high",
  lowest_low: "Lowest low",
  auto: "Auto (by bar size)",
};

export default function SessionProfileControl({
  active,
  candlesMode,
  renderedCount,
  loading,
  onAdd,
  onRemove,
  onPeriodChange,
  onOptionsChange,
  onSettingsChange,
}: Props) {
  return (
    <div role="group" aria-label="Session volume profiles">
      {(Object.keys(SESSION_PRESETS) as SessionPreset[]).map((preset) => (
        <button
          key={preset}
          type="button"
          aria-label={`Add ${SESSION_PRESETS[preset].label}`}
          disabled={!candlesMode || active?.preset === preset}
          onClick={() => onAdd(preset)}
        >
          Add {SESSION_PRESETS[preset].label}
        </button>
      ))}
      {active && (
        <div>
          <span>{SESSION_PRESETS[active.preset].label}</span>
          <button type="button" aria-label="Remove session volume profile" onClick={onRemove}>
            Remove
          </button>
          {active.preset === "auto" && (
            <label>
              Anchor
              <select
                aria-label="Anchor"
                value={active.anchor}
                onChange={(e) => onOptionsChange({ anchor: e.target.value as AutoAnchorPreset })}
              >
                {AUTO_ANCHOR_PRESETS.map((anchor) => (
                  <option key={anchor} value={anchor}>
                    {ANCHOR_LABELS[anchor]}
                  </option>
                ))}
              </select>
            </label>
          )}
          {active.preset === "tpo" && (
            <>
              <label>
                Initial balance (minutes)
                <IbMinutesInput value={active.ibMinutes} onCommit={(ibMinutes) => onOptionsChange({ ibMinutes })} />
              </label>
              <label>
                <input
                  type="checkbox"
                  aria-label="TPO letters"
                  checked={active.letters}
                  onChange={(e) => onOptionsChange({ letters: e.target.checked })}
                />
                Letters
              </label>
            </>
          )}
          {!SESSION_PRESETS[active.preset].fixedPeriod && (
            <label>
              Period
              <select
                aria-label="Profile period"
                value={active.period}
                onChange={(e) => onPeriodChange(e.target.value as SessionPeriod)}
              >
                {SESSION_PERIODS.map((period) => (
                  <option key={period} value={period}>
                    {SESSION_PERIOD_LABELS[period]}
                  </option>
                ))}
              </select>
            </label>
          )}
          {candlesMode && loading && <span role="status">Loading session history…</span>}
          {candlesMode && active.preset !== "auto" && renderedCount < active.settings.sessionCount && (
            <span>
              Showing {renderedCount} of {active.settings.sessionCount} (history still loading, or beyond the fetch window)
            </span>
          )}
          {candlesMode && active.preset === "auto" && renderedCount === 0 && (
            <span>Not drawn yet (history still loading, or the anchor is beyond the fetch window)</span>
          )}
          {!candlesMode && <span>Shown in Candles mode only</span>}
          {active.preset === "auto" ? (
            // One profile from the anchor to the latest bar: no session count to set.
            <VolumeProfileSettingsPanel
              title="Session volume profile settings"
              value={{ ...active.settings, sessionCount: undefined }}
              onChange={(next) => onSettingsChange({ ...next, sessionCount: active.settings.sessionCount })}
            />
          ) : (
            <VolumeProfileSettingsPanel
              title="Session volume profile settings"
              value={active.settings}
              onChange={onSettingsChange}
              maxSessions={MAX_SESSIONS}
            />
          )}
        </div>
      )}
    </div>
  );
}
