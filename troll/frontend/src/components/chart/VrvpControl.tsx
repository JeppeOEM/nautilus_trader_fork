import { type VolumeProfileSettings } from "../../lib/volumeProfile";
import VolumeProfileSettingsPanel from "./VolumeProfileSettings";

interface Props {
  active: boolean;
  /** VRVP profiles candle bars, so it only draws in Candles mode. */
  candlesMode: boolean;
  settings: VolumeProfileSettings;
  onAdd: () => void;
  onRemove: () => void;
  onSettingsChange: (next: VolumeProfileSettings) => void;
}

// Story 18.7 (AC #1/#4): the Visible Range Volume Profile's add/remove entry, next to the
// Indicators picker. It is deliberately NOT a picker catalog entry: that catalog is
// server-driven and persisted per coin, while VRVP is a chart-only overlay with no
// backend counterpart. One instance per chart -- "Add" while active is a no-op re-add of
// the same instance, never a second stacked profile.
export default function VrvpControl({ active, candlesMode, settings, onAdd, onRemove, onSettingsChange }: Props) {
  return (
    <div role="group" aria-label="Chart overlays">
      <h3>Chart overlays</h3>
      <button
        type="button"
        aria-label="Add visible range volume profile"
        disabled={active || !candlesMode}
        onClick={onAdd}
      >
        Add Visible Range Volume Profile
      </button>
      {active && !candlesMode && <span>Shown in Candles mode only</span>}
      {active && (
        <div>
          <span>Visible Range Volume Profile</span>
          <button type="button" aria-label="Remove visible range volume profile" onClick={onRemove}>
            Remove
          </button>
          <VolumeProfileSettingsPanel value={settings} onChange={onSettingsChange} />
        </div>
      )}
    </div>
  );
}
