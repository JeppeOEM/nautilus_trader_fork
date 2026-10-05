import { useState } from "react";

import { FOOTPRINT_MODES, type FootprintMode, type FootprintSettings, MAX_FOOTPRINT_ROW_TICKS } from "../../lib/chartLayout";
import { chartVar } from "./chartTheme";
import SettingsDialogShell from "./SettingsDialogShell";

// Story 32.8: the Footprint legend row's gear. The settings are the layout's `footprint` table; the
// caller saves what Apply hands it, and only a changed row size refetches (the hook's one input that
// changes the served bars).

const MODE_LABELS: Record<FootprintMode, string> = {
  bid_ask: "Bid × Ask (sell × buy)",
  delta: "Delta (buy − sell)",
  volume: "Volume (total)",
};

const asHex = (color: string): string => (/^#[0-9a-f]{6}$/i.test(color) ? color : "#000000");

interface Draft {
  auto: boolean;
  ticks: string;
  mode: FootprintMode;
  ratio: string;
  text: boolean;
  buyColor: string | undefined;
  sellColor: string | undefined;
}

const draftOf = (s: FootprintSettings): Draft => ({
  auto: s.row_ticks === 0,
  ticks: String(s.row_ticks === 0 ? 1 : s.row_ticks),
  mode: s.mode,
  ratio: String(s.imbalance_ratio),
  text: s.text,
  buyColor: s.buy_color,
  sellColor: s.sell_color,
});

/** The draft as settings, or the reason it cannot be applied. */
function settingsOf(draft: Draft, on: boolean): FootprintSettings | string {
  const ticks = Number(draft.ticks);
  if (!draft.auto && !(Number.isInteger(ticks) && ticks >= 1 && ticks <= MAX_FOOTPRINT_ROW_TICKS)) {
    return `Row size must be a whole number of ticks from 1 to ${MAX_FOOTPRINT_ROW_TICKS}.`;
  }
  const ratio = Number(draft.ratio);
  if (draft.ratio.trim() === "" || !Number.isFinite(ratio) || ratio < 1) return "Imbalance ratio must be a number of at least 1.";
  return {
    on,
    row_ticks: draft.auto ? 0 : ticks,
    mode: draft.mode,
    imbalance_ratio: ratio,
    text: draft.text,
    ...(draft.buyColor ? { buy_color: draft.buyColor } : {}),
    ...(draft.sellColor ? { sell_color: draft.sellColor } : {}),
  };
}

interface Props {
  settings: FootprintSettings;
  onApply: (next: FootprintSettings) => void;
  onRemove: () => void;
  onClose: () => void;
}

export default function FootprintSettingsDialog({ settings, onApply, onRemove, onClose }: Props) {
  const [draft, setDraft] = useState<Draft>(() => draftOf(settings));
  const edit = (patch: Partial<Draft>): void => setDraft((prev) => ({ ...prev, ...patch }));
  const result = settingsOf(draft, settings.on);
  const refusal = typeof result === "string" ? result : null;

  return (
    <SettingsDialogShell title="Footprint settings" onClose={onClose}>
      <h2>Footprint</h2>
      <section aria-label="Inputs">
        <h3>Inputs</h3>
        <label>
          Row size:
          <select aria-label="Row size" value={draft.auto ? "auto" : "fixed"} onChange={(e) => edit({ auto: e.target.value === "auto" })}>
            <option value="auto">Auto (at most 24 rows)</option>
            <option value="fixed">Fixed ticks</option>
          </select>
        </label>
        {!draft.auto && (
          <label>
            Ticks per row:
            <input
              type="number"
              aria-label="Ticks per row"
              min={1}
              max={MAX_FOOTPRINT_ROW_TICKS}
              step={1}
              value={draft.ticks}
              onChange={(e) => edit({ ticks: e.target.value })}
            />
          </label>
        )}
        <label>
          Display:
          <select aria-label="Display mode" value={draft.mode} onChange={(e) => edit({ mode: e.target.value as FootprintMode })}>
            {FOOTPRINT_MODES.map((m) => (
              <option key={m} value={m}>
                {MODE_LABELS[m]}
              </option>
            ))}
          </select>
        </label>
        <label>
          Imbalance ratio:
          <input
            type="number"
            aria-label="Imbalance ratio"
            min={1}
            step={0.5}
            value={draft.ratio}
            onChange={(e) => edit({ ratio: e.target.value })}
          />
        </label>
      </section>
      <section aria-label="Style">
        <h3>Style</h3>
        <label>
          <input type="checkbox" aria-label="Show numbers" checked={draft.text} onChange={(e) => edit({ text: e.target.checked })} />
          Numbers (hidden when zoomed out)
        </label>
        <label>
          Buy colour:
          <input
            type="color"
            aria-label="Buy colour"
            value={asHex(draft.buyColor ?? chartVar("--chart-up"))}
            onChange={(e) => edit({ buyColor: e.target.value })}
          />
        </label>
        <label>
          Sell colour:
          <input
            type="color"
            aria-label="Sell colour"
            value={asHex(draft.sellColor ?? chartVar("--chart-down"))}
            onChange={(e) => edit({ sellColor: e.target.value })}
          />
        </label>
        <button type="button" onClick={() => edit({ buyColor: undefined, sellColor: undefined })}>
          Default colours
        </button>
      </section>
      {refusal && <p role="alert">{refusal}</p>}
      <div className="indicator-settings-footer">
        <button
          type="button"
          disabled={refusal !== null}
          onClick={() => {
            if (typeof result === "string") return;
            onApply(result);
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
