import { useEffect, useRef, useState } from "react";

import { createAlert } from "../../api/client";
import {
  type AlertCondition,
  type ConditionForm,
  conditionToForm,
  DEFAULT_TEMPLATE,
  emptyForm,
  expiryNs,
  formToCondition,
  LEVEL_KINDS,
} from "../../lib/alertConditions";
import { catalogEntries, useIndicatorCatalog } from "../../hooks/useIndicatorCatalog";
import ConditionFields from "../alerts/ConditionFields";
import DeliveryFields, { type Delivery } from "../alerts/DeliveryFields";
import type { PriceLineSpec } from "./LightweightChart";

const STATIC_SOURCE = "static";

const NEW_DELIVERY: Delivery = { frequency: "once_per_bar_close", expires: "", template: DEFAULT_TEMPLATE, webhookUrl: "" };

interface AlertDialogProps {
  open: boolean;
  onClose: () => void;
  instrumentId: string;
  barSeconds: number;
  /** The chart's placed horizontal lines (Story 18.1) -- selectable as a price kind's level. */
  priceLines: PriceLineSpec[];
  /**
   * Story 33.8: the condition the dialog opens with -- a horizontal line's "Add alert…" prefills a
   * `price_cross` at its price, a trendline's a `trendline_cross` naming it. Absent: an empty price
   * cross.
   */
  initialCondition?: AlertCondition | null;
  /**
   * Save the chart's drawings now (`useChartDrawings.saveNow`). The server checks a
   * `trendline_cross` against the saved drawings file and the engine reads its anchors from it, while
   * the chart saves a drawn or dragged line only after a debounce: Create awaits this first.
   */
  saveDrawings?: () => Promise<void>;
}

/**
 * The chart's Create Alert dialog: every condition kind (`ConditionFields`) on this coin and bar
 * width, with the horizontal-line target kept in the toolbar for the price kinds.
 */
export default function AlertDialog({
  open,
  onClose,
  instrumentId,
  barSeconds,
  priceLines,
  initialCondition,
  saveDrawings,
}: AlertDialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const [form, setForm] = useState<ConditionForm>(() => emptyForm("price_cross"));
  const [target, setTarget] = useState(STATIC_SOURCE);
  const [delivery, setDelivery] = useState<Delivery>(NEW_DELIVERY);
  const [error, setError] = useState<string | null>(null);
  const catalog = useIndicatorCatalog(open && form.kind === "indicator");

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      // Each opening starts from its prefill, never from the last dialog's half-typed form or
      // delivery.
      setForm(initialCondition ? conditionToForm(initialCondition) : emptyForm("price_cross"));
      setTarget(STATIC_SOURCE);
      setDelivery(NEW_DELIVERY);
      setError(null);
      // showModal gives Esc-to-close, a backdrop and focus trapping natively; jsdom lacks it.
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    } else if (!open && dialog.open) {
      if (typeof dialog.close === "function") dialog.close();
      else dialog.removeAttribute("open");
    }
  }, [open, initialCondition]);

  // The chosen horizontal line, while a price kind reads its level from one: the level shown and
  // saved is the line's price now (a dragged line moves it); a line removed meanwhile leaves the
  // level it last showed (copied into the form when chosen).
  const targetLine = LEVEL_KINDS.includes(form.kind) ? priceLines.find((l) => l.id === target) : undefined;
  const shown = targetLine ? { ...form, fields: { ...form.fields, level: String(targetLine.price) } } : form;

  function chooseTarget(id: string): void {
    setTarget(id);
    const line = priceLines.find((l) => l.id === id);
    if (line) setForm((f) => ({ ...f, fields: { ...f.fields, level: String(line.price) } }));
  }

  function changeForm(next: ConditionForm): void {
    // Typing a level detaches it from the line: the typed value is what is saved.
    if (next.fields.level !== shown.fields.level) setTarget(STATIC_SOURCE);
    setForm(next);
  }

  async function save(): Promise<void> {
    setError(null);
    const result = formToCondition(shown, catalogEntries(catalog));
    if ("error" in result) {
      setError(result.error);
      return;
    }
    const { condition } = result;
    if (condition.kind === "trendline_cross" && saveDrawings) {
      try {
        await saveDrawings();
      } catch (err) {
        setError(`The trendline is not saved yet, so no alert can watch it: ${err instanceof Error ? err.message : String(err)}`);
        return;
      }
    }
    try {
      await createAlert({
        instrument_id: instrumentId,
        condition,
        frequency: delivery.frequency,
        bar_seconds: barSeconds,
        expires_at_ns: expiryNs(delivery.expires),
        template: delivery.template,
        webhook_url: delivery.webhookUrl.trim(),
      });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save alert.");
    }
  }

  return (
    <dialog ref={ref} aria-label="Create Alert" onClose={onClose}>
      <h3>Create Alert &mdash; {instrumentId}</h3>
      {LEVEL_KINDS.includes(form.kind) && (
        <label>
          Level from
          <select aria-label="Condition target" value={target} onChange={(e) => chooseTarget(e.target.value)}>
            <option value={STATIC_SOURCE}>Value</option>
            {priceLines.map((l) => (
              <option key={l.id} value={l.id}>
                Horizontal line @ {l.price}
              </option>
            ))}
          </select>
        </label>
      )}
      {open && <ConditionFields instrumentId={instrumentId} form={shown} onChange={changeForm} catalog={catalog} />}
      <DeliveryFields value={delivery} onChange={setDelivery} />
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
