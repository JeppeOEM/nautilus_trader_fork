import { type KeyboardEvent, useEffect, useId, useMemo, useRef, useState } from "react";

import { HttpError, type MarketItem, fetchMarkets } from "../../api/client";
import { validateCompareInput } from "../../lib/compare";
import { formatFixed } from "../../pages/filters";
import SettingsDialogShell from "./SettingsDialogShell";

export type SymbolSearchMode = "navigate" | "compare";

interface Props {
  /** `navigate` opens the picked market's chart; `compare` adds it as a compare symbol. */
  mode: SymbolSearchMode;
  /** The chart's own instrument (compare mode never offers it). */
  instrumentId: string;
  /** Compare mode: the symbols already compared (not offered, and counted against the maximum). */
  compared?: readonly string[];
  onPick: (instrumentId: string) => void;
  onClose: () => void;
}

/** The rows matching every whitespace token of `query` (case-insensitive) in symbol, venue or id, in
 * the server's order. */
function matching(markets: readonly MarketItem[], query: string): MarketItem[] {
  const tokens = query.toLowerCase().split(/\s+/).filter((t) => t.length > 0);
  if (tokens.length === 0) return [...markets];
  return markets.filter((m) => {
    const text = `${m.symbol} ${m.venue} ${m.instrument_id}`.toLowerCase();
    return tokens.every((t) => text.includes(t));
  });
}

function loadFailure(err: unknown, compare: boolean): string {
  const base =
    err instanceof HttpError && err.status === 503
      ? "No venue's market list is live"
      : "The market list could not be loaded";
  return compare ? `${base}; type an instrument id.` : `${base}.`;
}

/** The ranking engine's USD 24 h volume as the Rankings table shows it (millions); `—` when the ranking
 * has none for the id (a spot or unranked market), never 0. */
function volumeText(volume: number | null): string {
  return volume === null || !Number.isFinite(volume) ? "—" : `${formatFixed(volume, { decimals: 2, scale: 1e6 })}M`;
}

/**
 * Story 33.12: the symbol search, one dialog for opening another market (`navigate`: `/`, Ctrl/Cmd+K
 * or the symbol button) and for adding a compare symbol (`compare`: the Compare button or Alt+C,
 * replacing Story 33.9's text field). Every live market from `GET /api/markets` with its venue,
 * market and 24 h volume, filtered by every typed token; ↑/↓ move the highlight, Enter picks it. In
 * compare mode a typed id is still accepted when nothing matches (the market list may be down), and
 * an id the chart cannot compare is refused inline with the reason (`validateCompareInput`).
 */
// One empty list for every render: a fresh `[]` default would re-run the row filter on each render.
const NONE_COMPARED: readonly string[] = [];

export default function SymbolSearch({ mode, instrumentId, compared = NONE_COMPARED, onPick, onClose }: Props) {
  const compare = mode === "compare";
  const [query, setQuery] = useState("");
  const [markets, setMarkets] = useState<MarketItem[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [staleVenues, setStaleVenues] = useState<string[]>([]);
  const [highlight, setHighlight] = useState(0);
  const [refusal, setRefusal] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    // Navigate lists every market; compare asks with the chart's id, so the server leaves it out and
    // orders the same asset on other venues first.
    fetchMarkets(compare ? instrumentId : undefined)
      .then((response) => {
        if (cancelled) return;
        setMarkets(response.items);
        setStaleVenues(response.stale_venues);
        setLoadError(null); // an earlier fetch's failure no longer describes the list shown
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setMarkets([]);
        setLoadError(loadFailure(err, compare));
      });
    return () => {
      cancelled = true;
    };
  }, [compare, instrumentId]);

  const offered = useMemo(
    () =>
      compare
        ? (markets ?? []).filter((m) => !compared.includes(m.instrument_id) && m.instrument_id !== instrumentId)
        : (markets ?? []),
    [compare, markets, compared, instrumentId],
  );
  const rows = useMemo(() => matching(offered, query), [offered, query]);
  const active = Math.min(highlight, Math.max(0, rows.length - 1));
  // Option ids for `aria-activedescendant`: focus stays in the input, so a screen reader learns the
  // highlighted row from it.
  const idBase = useId();
  const optionId = (i: number): string => `${idBase}-option-${i}`;
  const listRef = useRef<HTMLUListElement | null>(null);
  useEffect(() => {
    // ↑/↓ past the list's visible rows would otherwise move the highlight out of sight. jsdom has no
    // `scrollIntoView`, hence the guard.
    const row = listRef.current?.querySelector<HTMLElement>(`[id="${idBase}-option-${active}"]`);
    if (typeof row?.scrollIntoView === "function") row.scrollIntoView({ block: "nearest" });
  }, [idBase, active, rows]);

  const pick = (iid: string): void => {
    if (compare) {
      const result = validateCompareInput(iid, instrumentId, compared);
      if (!result.ok) {
        setRefusal(result.reason);
        return;
      }
      onPick(result.iid);
    } else {
      onPick(iid);
    }
    onClose();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>): void => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setHighlight(Math.min(Math.max(0, active + step), Math.max(0, rows.length - 1)));
      return;
    }
    if (event.key !== "Enter") return;
    event.preventDefault();
    if (rows.length > 0) pick(rows[active].instrument_id);
    else if (compare && query.trim().length > 0) pick(query); // a typed id, the list down or not
  };

  return (
    <SettingsDialogShell title={compare ? "Compare symbol" : "Symbol search"} className="symbol-search" onClose={onClose}>
      <div className="indicator-dialog-head">
        <input
          type="search"
          aria-label={compare ? "Search a market to compare" : "Search markets"}
          placeholder={compare ? "symbol, venue or id (BTC-USD-PERP.HYPERLIQUID)" : "symbol, venue or id"}
          value={query}
          autoFocus
          aria-controls={`${idBase}-list`}
          aria-activedescendant={rows.length > 0 ? optionId(active) : undefined}
          onChange={(e) => {
            setQuery(e.target.value);
            setHighlight(0);
            setRefusal(null);
          }}
          onKeyDown={onKeyDown}
        />
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>
      {refusal !== null && <p role="alert">{refusal}</p>}
      {loadError !== null && <p role="status">{loadError}</p>}
      {loadError === null && staleVenues.length > 0 && (
        <p role="status">Market list stale for {staleVenues.join(", ")} (no update for over 3 min)</p>
      )}
      {markets === null && <p className="indicator-dialog-empty">Loading the market list...</p>}
      {markets !== null && loadError === null && rows.length === 0 && <p className="indicator-dialog-empty">No market matches</p>}
      <ul
        ref={listRef}
        id={`${idBase}-list`}
        className="indicator-dialog-results symbol-search-results"
        role="listbox"
        aria-label="Markets"
      >
        {rows.map((m, i) => (
          <li key={m.instrument_id} id={optionId(i)} role="option" aria-selected={i === active}>
            <button type="button" className={i === active ? "symbol-search-active" : undefined} onClick={() => pick(m.instrument_id)}>
              <span>
                <strong>{m.symbol}</strong> <span className="indicator-dialog-tag">{m.instrument_id}</span>
              </span>
              <span className="indicator-dialog-tag">
                {m.venue} · {m.market} · 24h {volumeText(m.volume24h)}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </SettingsDialogShell>
  );
}
