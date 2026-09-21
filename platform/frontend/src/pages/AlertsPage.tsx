import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { deleteAlert, fetchAlerts } from "../api/client";
import type { AlertResponse } from "../api/schema";

const FREQUENCY_LABELS: Record<string, string> = {
  once_per_bar_close: "once per bar close",
  once_per_bar: "once per bar",
  only_once: "only once",
};

/** e.g. "BTC-USD-PERP.DYDX price crosses 65000 (once per bar, 60s bars)" */
function describeAlert(alert: AlertResponse): string {
  const frequency = FREQUENCY_LABELS[alert.frequency] ?? alert.frequency;
  return `${alert.instrument_id} price crosses ${alert.level} (${frequency}, ${alert.bar_seconds}s bars)`;
}

export default function AlertsPage() {
  const queryClient = useQueryClient();
  const { data, isError } = useQuery({ queryKey: ["alerts"], queryFn: fetchAlerts });
  const remove = useMutation({
    mutationFn: deleteAlert,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["alerts"] }),
  });

  if (isError) return <p style={{ color: "var(--color-danger)" }}>Failed to load alerts.</p>;
  if (!data) return <p className="term-loading">Loading</p>;
  return (
    <div>
      <h1>Alerts</h1>
      {data.length === 0 && <p>No alerts yet &mdash; create one from a coin&apos;s chart.</p>}
      <ul>
        {data.map((alert) => (
          <li key={alert.id}>
            {describeAlert(alert)} &mdash; <strong>{alert.status}</strong>{" "}
            <button type="button" onClick={() => remove.mutate(alert.id)}>
              Delete
            </button>
          </li>
        ))}
      </ul>
      {remove.isError && <p style={{ color: "var(--color-danger)" }}>Failed to delete alert.</p>}
    </div>
  );
}
