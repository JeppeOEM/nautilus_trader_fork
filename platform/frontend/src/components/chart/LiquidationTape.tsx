import type { LiquidationItem } from "../../api/schema";
import { formatUnits } from "../../lib/units";
import { safeText } from "./derivativePanes";

interface Props {
  /** The rows to list, oldest first (the newest `TAPE_ROWS`, already cut at a replay's time). */
  rows: readonly LiquidationItem[];
  /** The first page answered. */
  loaded: boolean;
  /** The first page came back empty: the instrument has no liquidation feed. */
  noFeed: boolean;
  /** Operator-facing load failure, else null. */
  error: string | null;
}

/** `ts_event` (ns) as UTC `HH:MM:SS`. */
function clock(tsNs: number): string {
  return new Date(Math.floor(tsNs / 1_000_000)).toISOString().slice(11, 19);
}

/**
 * Story 33.5: the Liquidation tape, a panel beside the chart listing the newest liquidations, newest
 * first: time, the liquidated side (a long's is a forced sell), size, the bankruptcy price and the
 * server's notional, every number through `lib/units.ts` at the row's own precisions (one it cannot
 * print exactly, a notional past 2^53, reads `—`, logged once). An id whose
 * first page came back empty has no feed (only Bybit linear has one) and says so.
 */
export default function LiquidationTape({ rows, loaded, noFeed, error }: Props) {
  const newestFirst = [...rows].reverse();
  return (
    <aside className="liquidation-tape" aria-label="Liquidation tape">
      <strong>Liquidations</strong>
      {error !== null && <p role="alert">Liquidations: {error}</p>}
      {noFeed && error === null && <p className="liquidation-tape-note">no liquidation feed for this instrument</p>}
      {loaded && !noFeed && rows.length === 0 && <p className="liquidation-tape-note">none up to the replay time</p>}
      {!loaded && error === null && <p className="liquidation-tape-note">Loading...</p>}
      {rows.length > 0 && (
        <div className="liquidation-tape-body">
          <table>
            <thead>
              <tr>
                <th>time</th>
                <th>side</th>
                <th>size</th>
                <th>price</th>
                <th>notional</th>
              </tr>
            </thead>
            <tbody>
              {newestFirst.map((row) => (
                <tr key={row.venue_event_id} className={row.side === "long" ? "liquidation-tape-long" : "liquidation-tape-short"}>
                  <td>{clock(row.ts_event)}</td>
                  <td>{row.side}</td>
                  <td>{safeText(() => formatUnits(row.size_units, row.size_precision))}</td>
                  <td title={row.price_kind}>{safeText(() => formatUnits(row.price_units, row.price_precision))}</td>
                  <td>{safeText(() => formatUnits(row.notional_units, row.notional_precision))}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </aside>
  );
}
