import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { createAlert, deleteAlert, fetchAlerts, fetchRankings, updateAlert } from "../api/client";
import type { AlertResponse } from "../api/schema";
import ConditionFields from "../components/alerts/ConditionFields";
import DeliveryFields, { type Delivery } from "../components/alerts/DeliveryFields";
import SettingsDialogShell from "../components/chart/SettingsDialogShell";
import { catalogEntries, useIndicatorCatalog } from "../hooks/useIndicatorCatalog";
import {
  type AlertCondition,
  type ConditionForm,
  conditionToForm,
  DEFAULT_TEMPLATE,
  emptyForm,
  editedExpiryNs,
  expiryDate,
  expiryNs,
  formToCondition,
  FREQUENCY_LABELS,
} from "../lib/alertConditions";
import { TIMEFRAMES } from "../timeframes";

const NEW_DELIVERY: Delivery = { frequency: "once_per_bar_close", expires: "", template: DEFAULT_TEMPLATE, webhookUrl: "" };

function deliveryOf(alert: AlertResponse): Delivery {
  return {
    frequency: alert.frequency,
    expires: expiryDate(alert.expires_at_ns),
    template: alert.template,
    webhookUrl: alert.webhook_url,
  };
}

/** e.g. "BTCUSDT-LINEAR.BYBIT RSI(period=14) value > 70 on 3600s bars (once per bar)" -- the text is the server's. */
function describeAlert(alert: AlertResponse): string {
  const frequency = FREQUENCY_LABELS[alert.frequency] ?? alert.frequency;
  return `${alert.instrument_id} ${alert.condition_text} (${frequency})`;
}

function errorText(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

function useInstruments(): { ids: string[]; failed: boolean } {
  const { data, isError } = useQuery({ queryKey: ["alerts", "instruments"], queryFn: fetchRankings, retry: false });
  const ids = (data?.items ?? [])
    .map((item) => item.instrument_id)
    .filter((id): id is string => typeof id === "string");
  return { ids: [...new Set(ids)].sort(), failed: isError };
}

/** The create form: any ranked coin, any timeframe, every condition kind -- no chart needed. */
function CreateAlertForm({ onCreated }: { onCreated: () => void }) {
  const { ids, failed } = useInstruments();
  const [instrumentId, setInstrumentId] = useState("");
  const [barSeconds, setBarSeconds] = useState<number>(TIMEFRAMES[0].seconds);
  const [form, setForm] = useState<ConditionForm>(() => emptyForm("price_cross"));
  const [delivery, setDelivery] = useState<Delivery>(NEW_DELIVERY);
  const [error, setError] = useState<string | null>(null);
  const catalog = useIndicatorCatalog(form.kind === "indicator");
  const chosen = instrumentId || ids[0] || "";
  // The default is fixed once the list first loads (state adjusted while rendering, React's
  // pattern for state derived from a prop): a rankings refetch that sorts another id first must
  // never move the coin the operator is looking at.
  if (!instrumentId && ids[0]) setInstrumentId(ids[0]);

  async function create(): Promise<void> {
    setError(null);
    if (!chosen) {
      setError("Choose an instrument.");
      return;
    }
    const result = formToCondition(form, catalogEntries(catalog));
    if ("error" in result) {
      setError(result.error);
      return;
    }
    try {
      await createAlert({
        instrument_id: chosen,
        condition: result.condition,
        frequency: delivery.frequency,
        bar_seconds: barSeconds,
        expires_at_ns: expiryNs(delivery.expires),
        template: delivery.template,
        webhook_url: delivery.webhookUrl.trim(),
      });
      setForm(emptyForm(form.kind));
      onCreated();
    } catch (err) {
      setError(errorText(err, "Failed to create alert."));
    }
  }

  return (
    <section aria-label="New alert" className="alert-form">
      <h2>New alert</h2>
      <label>
        Instrument
        <select aria-label="Instrument" value={chosen} onChange={(e) => setInstrumentId(e.target.value)}>
          {ids.length === 0 && <option value="">{failed ? "Rankings unavailable" : "Loading…"}</option>}
          {chosen && ids.length > 0 && !ids.includes(chosen) && <option value={chosen}>{chosen}</option>}
          {ids.map((id) => (
            <option key={id} value={id}>
              {id}
            </option>
          ))}
        </select>
      </label>
      <label>
        Timeframe
        <select aria-label="Timeframe" value={barSeconds} onChange={(e) => setBarSeconds(Number(e.target.value))}>
          {TIMEFRAMES.map((tf) => (
            <option key={tf.seconds} value={tf.seconds}>
              {tf.label}
            </option>
          ))}
        </select>
      </label>
      <ConditionFields instrumentId={chosen} form={form} onChange={setForm} catalog={catalog} />
      <DeliveryFields value={delivery} onChange={setDelivery} />
      {error && <p style={{ color: "var(--color-danger)" }}>{error}</p>}
      <button type="button" onClick={() => void create()}>
        Create
      </button>
    </section>
  );
}

/**
 * Whether the alert is a triggered `only_once` alert, which an edit may re-arm. The response carries
 * no `triggered` key (the frozen key set: `status` is its view), and `status` shows `invalid` first,
 * so an alert both triggered and invalid is told by its fire: `only_once` and `last_fired_ns`.
 * After a re-arm that fire's time stays, so a re-armed alert that later turned invalid offers the
 * checkbox too; re-arming an alert that is not triggered changes nothing.
 */
function canRearm(alert: AlertResponse): boolean {
  if (alert.status === "triggered") return true;
  return alert.status === "invalid" && alert.frequency === "only_once" && alert.last_fired_ns != null;
}

/** Edit an alert's condition and delivery; its instrument and timeframe are its identity. */
function EditAlertDialog({ alert, onClose, onSaved }: { alert: AlertResponse; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState<ConditionForm>(() => conditionToForm(alert.condition as AlertCondition));
  const [delivery, setDelivery] = useState<Delivery>(() => deliveryOf(alert));
  const [rearm, setRearm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const catalog = useIndicatorCatalog(form.kind === "indicator");

  async function save(): Promise<void> {
    setError(null);
    const result = formToCondition(form, catalogEntries(catalog));
    if ("error" in result) {
      setError(result.error);
      return;
    }
    try {
      await updateAlert(alert.id, {
        condition: result.condition,
        frequency: delivery.frequency,
        expires_at_ns: editedExpiryNs(delivery.expires, alert.expires_at_ns),
        template: delivery.template,
        webhook_url: delivery.webhookUrl.trim(),
        rearm,
      });
      onSaved();
    } catch (err) {
      setError(errorText(err, "Failed to save alert."));
    }
  }

  return (
    <SettingsDialogShell title="Edit Alert" onClose={onClose}>
      <h3>
        Edit Alert &mdash; {alert.instrument_id}, {alert.bar_seconds}s bars
      </h3>
      <ConditionFields instrumentId={alert.instrument_id} form={form} onChange={setForm} catalog={catalog} />
      <DeliveryFields value={delivery} onChange={setDelivery} />
      {canRearm(alert) && (
        <label>
          <input type="checkbox" aria-label="Re-arm" checked={rearm} onChange={(e) => setRearm(e.target.checked)} /> Re-arm
          (fire again)
        </label>
      )}
      {error && <p style={{ color: "var(--color-danger)" }}>{error}</p>}
      <div>
        <button type="button" onClick={() => void save()}>
          Save
        </button>
        <button type="button" onClick={onClose}>
          Cancel
        </button>
      </div>
    </SettingsDialogShell>
  );
}

function AlertRow({ alert, onEdit, onDelete }: { alert: AlertResponse; onEdit: () => void; onDelete: () => void }) {
  return (
    <li>
      {describeAlert(alert)} &mdash; <strong>{alert.status}</strong>
      {alert.status === "invalid" && alert.invalid_reason && (
        <span style={{ color: "var(--color-danger)" }}> ({alert.invalid_reason})</span>
      )}{" "}
      <button type="button" onClick={onEdit}>
        Edit
      </button>{" "}
      <button type="button" onClick={onDelete}>
        Delete
      </button>
    </li>
  );
}

export default function AlertsPage() {
  const queryClient = useQueryClient();
  const { data, isError } = useQuery({ queryKey: ["alerts"], queryFn: fetchAlerts });
  const [editing, setEditing] = useState<AlertResponse | null>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["alerts"], exact: true });
  const remove = useMutation({ mutationFn: deleteAlert, onSuccess: refresh });

  return (
    <div>
      <h1>Alerts</h1>
      <CreateAlertForm onCreated={() => void refresh()} />
      {isError && <p style={{ color: "var(--color-danger)" }}>Failed to load alerts.</p>}
      {!data && !isError && <p className="term-loading">Loading</p>}
      {data && data.length === 0 && <p>No alerts yet.</p>}
      {data && (
        <ul>
          {data.map((alert) => (
            <AlertRow key={alert.id} alert={alert} onEdit={() => setEditing(alert)} onDelete={() => remove.mutate(alert.id)} />
          ))}
        </ul>
      )}
      {remove.isError && <p style={{ color: "var(--color-danger)" }}>Failed to delete alert.</p>}
      {editing && (
        <EditAlertDialog
          alert={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void refresh();
          }}
        />
      )}
    </div>
  );
}
