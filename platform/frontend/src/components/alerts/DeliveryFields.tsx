import { FREQUENCIES, TEMPLATE_HELP } from "../../lib/alertConditions";

/** How and when an alert fires and what it says, as the forms type them. */
export interface Delivery {
  frequency: string;
  /** "YYYY-MM-DD" (end of that local day), or "" = never. */
  expires: string;
  template: string;
  webhookUrl: string;
}

interface Props {
  value: Delivery;
  onChange: (value: Delivery) => void;
}

/**
 * Story 33.8: an alert's delivery inputs -- frequency, expiry, message template and webhook --
 * shared by the chart's Create Alert dialog and the Alerts page's create form and edit dialog.
 */
export default function DeliveryFields({ value, onChange }: Props) {
  const set = (patch: Partial<Delivery>) => onChange({ ...value, ...patch });
  return (
    <>
      <label>
        Frequency
        <select aria-label="Frequency" value={value.frequency} onChange={(e) => set({ frequency: e.target.value })}>
          {FREQUENCIES.map((f) => (
            <option key={f.value} value={f.value}>
              {f.label}
            </option>
          ))}
        </select>
      </label>
      <label>
        Expires (blank = never)
        <input aria-label="Expiration date" type="date" value={value.expires} onChange={(e) => set({ expires: e.target.value })} />
      </label>
      <label>
        Message ({TEMPLATE_HELP})
        <textarea aria-label="Message template" rows={3} value={value.template} onChange={(e) => set({ template: e.target.value })} />
      </label>
      <label>
        Webhook URL (optional -- alerts always go to Telegram when the server has it configured)
        <input
          aria-label="Webhook URL"
          type="url"
          placeholder="https://"
          value={value.webhookUrl}
          onChange={(e) => set({ webhookUrl: e.target.value })}
        />
      </label>
    </>
  );
}
