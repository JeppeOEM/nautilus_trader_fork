import {
  SESSION_PERIODS,
  SESSION_PRESETS,
  type SessionPeriod,
  type SessionPreset, type SessionProfileSettings,
  MAX_SESSIONS,
} from "../../lib/sessionProfile";
import VolumeProfileSettingsPanel from "./VolumeProfileSettings";

interface Props {
  /** The active preset and its settings, or null when no session profile is on. */
  active: { preset: SessionPreset; period: SessionPeriod; settings: SessionProfileSettings } | null;
  candlesMode: boolean;
  /** How many sessions/periods are actually drawn right now (0 while history loads). */
  renderedCount: number;
  onAdd: (preset: SessionPreset) => void;
  onRemove: () => void;
  onPeriodChange: (period: SessionPeriod) => void;
  onSettingsChange: (next: SessionProfileSettings) => void;
}

// Story 18.8/18.9 (AC #1/#3): SVP, SVP HD and PVP are buttons over ONE component/state -- picking
// one while the other is on switches the preset (one session profile per chart). Placed next
// to the VRVP control for the same reason (chart-only overlay, not a server-catalog entry).
export default function SessionProfileControl({ active, candlesMode, renderedCount, onAdd, onRemove, onPeriodChange, onSettingsChange }: Props) {
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
          {active.preset === "pvp" && (
            <label>
              Period
              <select
                aria-label="Profile period"
                value={active.period}
                onChange={(e) => onPeriodChange(e.target.value as SessionPeriod)}
              >
                {SESSION_PERIODS.map((period) => (
                  <option key={period} value={period}>
                    {period}
                  </option>
                ))}
              </select>
            </label>
          )}
          {candlesMode && renderedCount < active.settings.sessionCount && (
            <span>
              Showing {renderedCount} of {active.settings.sessionCount} (history still loading, or beyond the fetch window)
            </span>
          )}
          {!candlesMode && <span>Shown in Candles mode only</span>}
          <VolumeProfileSettingsPanel
            title="Session volume profile settings"
            value={active.settings}
            onChange={onSettingsChange}
            maxSessions={MAX_SESSIONS}
          />
        </div>
      )}
    </div>
  );
}
