import { useEffect, useRef, useState } from "react";

import { createAlert } from "../../api/client";
import type { PriceLineSpec } from "./LightweightChart";

export const DEFAULT_TEMPLATE = "{{ticker}} price crossed {{close}} ({{time}})";

const STATIC_SOURCE = "static";

const FREQUENCIES = [
  { value: "once_per_bar_close", label: "Once per bar close" },
  { value: "once_per_bar", label: "Once per bar" },
  { value: "only_once", label: "Only once" },
];

interface AlertDialogProps {
  open: boolean;
  onClose: () => void;
  instrumentId: string;
  barSeconds: number;
  /** The chart's placed horizontal lines (Story 18.1) -- selectable as the crossing target. */
  priceLines: PriceLineSpec[];
}

/** Date input value ("YYYY-MM-DD", or "" = never) -> end of that local day in epoch ns. */
function expiryNs(date: string): number | null {
  if (!date) return null;
  return new Date(`${date}T23:59:59`).getTime() * 1_000_000;
}

export default function AlertDialog({ open, onClose, instrumentId, barSeconds, priceLines }: AlertDialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const [source, setSource] = useState(STATIC_SOURCE);
  const [levelText, setLevelText] = useState("");
  const [frequency, setFrequency] = useState("once_per_bar_close");
  const [expires, setExpires] = useState("");
  const [template, setTemplate] = useState(DEFAULT_TEMPLATE);
  const [webhookUrl, setWebhookUrl] = useState("");
  const [error, setError] = useState<string | null>(null);

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

  const line = priceLines.find((l) => l.id === source);
  // A removed line silently falls back to the typed value rather than a stale price.
  const level = line ? line.price : Number(levelText);

  async function save(): Promise<void> {
    setError(null);
    if (!Number.isFinite(level) || (!line && levelText.trim() === "")) {
      setError("Enter a price level.");
      return;
    }
    try {
      await createAlert({
        instrument_id: instrumentId,
        level,
        frequency,
        bar_seconds: barSeconds,
        expires_at_ns: expiryNs(expires),
        template,
        webhook_url: webhookUrl.trim(),
      });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save alert.");
    }
  }

  return (
    <dialog ref={ref} aria-label="Create Alert" onClose={onClose}>
      <h3>Create Alert &mdash; {instrumentId}</h3>
      <label>
        Price crosses
        <select aria-label="Condition target" value={source} onChange={(e) => setSource(e.target.value)}>
          <option value={STATIC_SOURCE}>Value</option>
          {priceLines.map((l) => (
            <option key={l.id} value={l.id}>
              Horizontal line @ {l.price.toFixed(2)}
            </option>
          ))}
        </select>
      </label>
      {!line && (
        <input
          aria-label="Price level"
          type="number"
          step="any"
          value={levelText}
          onChange={(e) => setLevelText(e.target.value)}
        />
      )}
      <label>
        Frequency
        <select aria-label="Frequency" value={frequency} onChange={(e) => setFrequency(e.target.value)}>
          {FREQUENCIES.map((f) => (
            <option key={f.value} value={f.value}>
              {f.label}
            </option>
          ))}
        </select>
      </label>
      <label>
        Expires (blank = never)
        <input aria-label="Expiration date" type="date" value={expires} onChange={(e) => setExpires(e.target.value)} />
      </label>
      <label>
        Message ({"{{ticker}} {{close}} {{time}} {{interval}}"})
        <textarea
          aria-label="Message template"
          rows={3}
          value={template}
          onChange={(e) => setTemplate(e.target.value)}
        />
      </label>
      <label>
        Webhook URL (optional -- alerts always go to Telegram when the server has it configured)
        <input
          aria-label="Webhook URL"
          type="url"
          placeholder="https://"
          value={webhookUrl}
          onChange={(e) => setWebhookUrl(e.target.value)}
        />
      </label>
      {error && <p style={{ color: "var(--color-danger)" }}>{error}</p>}
      <div>
        <button type="button" onClick={() => void save()}>
          Create
        </button>
        <button type="button" onClick={onClose}>
          Cancel
        </button>
      </div>
    </dialog>
  );
}
