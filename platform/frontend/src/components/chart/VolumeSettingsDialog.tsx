import { useState } from "react";

import { VOLUME_COLOR_MODES, type VolumeColorMode } from "../../lib/chartLayout";
import SettingsDialogShell from "./SettingsDialogShell";

// Story 33.6: the Volume legend row's gear. Its one setting is the layout's `volume_color_by`; the
// caller saves what Apply hands it (`useChartLayout`).

const MODE_LABELS: Record<VolumeColorMode, string> = {
  direction: "Direction (close vs open)",
  delta: "Delta (buy − sell volume)",
};

interface Props {
  colorBy: VolumeColorMode;
  onApply: (next: VolumeColorMode) => void;
  onRemove: () => void;
  onClose: () => void;
}

export default function VolumeSettingsDialog({ colorBy, onApply, onRemove, onClose }: Props) {
  const [draft, setDraft] = useState<VolumeColorMode>(colorBy);
  return (
    <SettingsDialogShell title="Volume settings" onClose={onClose}>
      <h2>Volume</h2>
      <section aria-label="Style">
        <h3>Style</h3>
        <label>
          Colour by:
          <select aria-label="Colour by" value={draft} onChange={(e) => setDraft(e.target.value as VolumeColorMode)}>
            {VOLUME_COLOR_MODES.map((m) => (
              <option key={m} value={m}>
                {MODE_LABELS[m]}
              </option>
            ))}
          </select>
        </label>
        {draft === "delta" && (
          <p className="indicator-dialog-tag">
            Shaded by how one-sided the bar was; a bar with no stored order flow (or no trade) is drawn in the pane colour.
          </p>
        )}
      </section>
      <div className="indicator-settings-footer">
        <button
          type="button"
          onClick={() => {
            onApply(draft);
            onClose();
          }}
        >
          Apply
        </button>
        <button type="button" onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          onClick={() => {
            onRemove();
            onClose();
          }}
        >
          Remove
        </button>
      </div>
    </SettingsDialogShell>
  );
}
