import { useEffect, useState } from "react";
import { Link } from "react-router";

import { useWatchlist, type ChartWatchlist } from "../../hooks/useWatchlist";
import { useLiveChannel } from "../../hooks/useLiveChannel";
import { formatFixed } from "../../pages/filters";
import { RANKING_STALE_MS, type RankingRow, type RankingsLiveMessage } from "../../pages/RankingsPage";

interface Props {
  /** The chart's instrument: the pin toggle acts on it. */
  instrumentId: string;
}

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

// The Rankings table's own precisions for these two columns (`RANKING_COLS`' `price` and `pct_24h`),
// so a pinned coin reads the same number here as on the Rankings page.
function priceText(row: RankingRow | undefined): string {
  return finite(row?.price) ? formatFixed(row.price, { decimals: 4 }) : "—";
}

function pctText(value: number): string {
  return `${value >= 0 ? "+" : ""}${formatFixed(value, { decimals: 2 })}%`;
}

function pinTitle(watchlist: ChartWatchlist): string | undefined {
  if (watchlist.loaded) return undefined;
  return watchlist.loadFailed ? "The saved watchlist could not be loaded (retrying)" : "Waiting for the saved watchlist";
}

/** Why a row's price and 24h % are not live, or null when they are. */
function staleReason(connected: boolean, messageStale: boolean, stale: ReadonlySet<string>, iid: string): string | null {
  if (!connected) return "Live rankings disconnected: no live price";
  // A connected socket whose rankings stopped (the ranking engine down, data_api still up): the
  // Rankings page's own heartbeat threshold.
  if (messageStale) return "Live rankings stale: no update for over 15 s";
  return stale.has(iid) ? "No fresh market data for this instrument (the ranking engine marks it stale)" : null;
}

function textOf(row: RankingRow | undefined, key: string): string | undefined {
  const value = row?.[key];
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

/**
 * Story 33.12: the watchlist rail beside the chart -- the server-side pinned list (`useWatchlist`),
 * each row priced live from `rankings:live` (the ranking engine's published `price` and `pct_24h`, the
 * same numbers the Rankings page shows; SSOT-02). An id the ranking does not carry (a spot market
 * outside the ranked set) or a null field reads `—`, never 0; so does every row while the socket is
 * disconnected or the message is older than the Rankings page's heartbeat threshold, and an id in the
 * message's `stale_instrument_ids`, never a frozen value posing as live.
 * A row click opens its chart; the toggle at the top pins or unpins the chart's own instrument.
 */
export default function WatchlistRail({ instrumentId }: Props) {
  // Mounted only while the rail is open (and kept across a coin change: the page renders it beside the
  // per-coin chart), so a closed rail costs no request and no socket.
  const watchlist = useWatchlist();
  const live = useLiveChannel<RankingsLiveMessage>();
  // A dead feed sends no message to re-render on: a 1 s tick re-checks the message's age (the Rankings
  // page's discipline).
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const interval = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(interval);
  }, []);
  const updatedAtNs = live.latest?.updated_at;
  const messageStale = updatedAtNs !== undefined && now - updatedAtNs / 1_000_000 > RANKING_STALE_MS;
  const ranks = new Map((live.latest?.ranks ?? []).map((row) => [row.instrument_id, row] as const));
  // A disconnected socket keeps its last message and a stale instrument keeps its last price: neither
  // is shown as if live (DATA-01).
  const stale = new Set(live.latest?.stale_instrument_ids ?? []);
  const pinned = watchlist.instruments.includes(instrumentId);

  return (
    <aside className="watchlist-rail" aria-label="Watchlist">
      <div className="watchlist-rail-head">
        <strong>Watchlist</strong>
        <button
          type="button"
          aria-pressed={pinned}
          disabled={!watchlist.loaded}
          title={pinTitle(watchlist)}
          onClick={() => (pinned ? watchlist.unpin(instrumentId) : watchlist.pin(instrumentId))}
        >
          {pinned ? "Unpin" : "Pin"} this chart
        </button>
      </div>
      {watchlist.error !== null && <p role="alert">{watchlist.error}</p>}
      {!live.connected && watchlist.instruments.length > 0 && (
        <p className="watchlist-rail-note">Live prices paused: rankings disconnected.</p>
      )}
      {live.connected && messageStale && watchlist.instruments.length > 0 && (
        <p className="watchlist-rail-note">Live prices paused: rankings stale.</p>
      )}
      {watchlist.loaded && watchlist.instruments.length === 0 && (
        <p className="watchlist-rail-note">Nothing pinned yet.</p>
      )}
      <ul className="watchlist-rail-list">
        {watchlist.instruments.map((iid) => {
          const reason = staleReason(live.connected, messageStale, stale, iid);
          const row = ranks.get(iid);
          const pct = reason === null ? row?.pct_24h : null;
          return (
            <li key={iid} className={iid === instrumentId ? "watchlist-rail-current" : undefined}>
              <Link to={`/chart/${encodeURIComponent(iid)}`} title={iid}>
                <span className="watchlist-rail-symbol">{textOf(row, "symbol") ?? iid}</span>
                <span className="watchlist-rail-venue">{textOf(row, "venue") ?? "—"}</span>
                <span className="watchlist-rail-price" title={reason ?? undefined}>
                  {reason === null ? priceText(row) : "—"}
                </span>
                <span className={finite(pct) ? (pct >= 0 ? "watchlist-rail-up" : "watchlist-rail-down") : undefined}>
                  {finite(pct) ? pctText(pct) : "—"}
                </span>
              </Link>
              <button type="button" aria-label={`Unpin ${iid}`} onClick={() => watchlist.unpin(iid)}>
                ×
              </button>
            </li>
          );
        })}
      </ul>
    </aside>
  );
}
