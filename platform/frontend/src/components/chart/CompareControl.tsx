import { useEffect, useId, useState } from "react";

import { HttpError, type MarketItem, fetchMarkets } from "../../api/client";
import { validateCompareInput } from "../../lib/compare";

interface CompareControlProps {
  instrumentId: string;
  /** The compare symbols already drawn. */
  symbols: readonly string[];
  /** Lines mode: compares are not drawn there. */
  disabled: boolean;
  onAdd: (instrumentId: string) => void;
}

function marketsFailure(err: unknown): string {
  if (err instanceof HttpError && err.status === 503) return "No venue's market list is live; type an instrument id.";
  return "The market list could not be loaded; type an instrument id.";
}

/**
 * Story 33.9: the header's Compare button and its inline field. The field suggests every live market
 * from `GET /api/markets` (the same asset on other venues first, as the server orders them); a failed
 * fetch is shown inline (an earlier list is dropped with it) and free text is still accepted; a market
 * already compared is not suggested. An id the chart cannot compare is refused with
 * the reason (`validateCompareInput`) and nothing is saved.
 *
 * Known limit: a text field with a datalist, no symbol search (Story 33.12 adds one).
 */
export default function CompareControl({ instrumentId, symbols, disabled, onAdd }: CompareControlProps) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [refusal, setRefusal] = useState<string | null>(null);
  const [markets, setMarkets] = useState<MarketItem[]>([]);
  const [marketsError, setMarketsError] = useState<string | null>(null);
  const [staleVenues, setStaleVenues] = useState<string[]>([]);
  const listId = useId();

  /** Closing, by any route, forgets the typed text and its refusal: a reopen starts clean. */
  const close = (): void => {
    setOpen(false);
    setText("");
    setRefusal(null);
  };

  // Lines mode disables the control: an open field closes with it rather than reappearing later.
  // Adjusted during render on the prop's change (React's "storing information from previous renders").
  const [wasDisabled, setWasDisabled] = useState(disabled);
  if (disabled !== wasDisabled) {
    setWasDisabled(disabled);
    if (disabled) close();
  }

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    fetchMarkets(instrumentId)
      .then((response) => {
        if (cancelled) return;
        setMarkets(response.items);
        setStaleVenues(response.stale_venues);
        setMarketsError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // An earlier fetch's list is not offered beside the failure: it may be long expired.
        setMarkets([]);
        setStaleVenues([]);
        setMarketsError(marketsFailure(err));
      });
    return () => {
      cancelled = true;
    };
  }, [open, instrumentId]);

  const add = (): void => {
    const result = validateCompareInput(text, instrumentId, symbols);
    if (!result.ok) {
      setRefusal(result.reason);
      return;
    }
    onAdd(result.iid);
    close();
  };

  return (
    <>
      <button
        type="button"
        aria-expanded={open}
        disabled={disabled}
        title={disabled ? "Compare draws on candle bars: switch to Candles" : undefined}
        onClick={() => (open ? close() : setOpen(true))}
      >
        Compare
      </button>
      {open && !disabled && (
        <span role="group" aria-label="Compare symbol">
          <input
            aria-label="Compare instrument id"
            list={listId}
            value={text}
            placeholder="BTC-USD-PERP.HYPERLIQUID"
            onChange={(e) => {
              setText(e.target.value);
              setRefusal(null);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") add();
              if (e.key === "Escape") close();
            }}
          />
          <datalist id={listId}>
            {markets
              .filter((m) => !symbols.includes(m.instrument_id))
              .map((m) => (
                <option key={m.instrument_id} value={m.instrument_id}>
                  {m.same_asset ? `${m.symbol} · ${m.venue} (same asset)` : `${m.symbol} · ${m.venue}`}
                </option>
              ))}
          </datalist>
          <button type="button" onClick={add}>
            Add
          </button>
          {refusal !== null && <span role="alert">{refusal}</span>}
          {marketsError !== null && <span role="status">{marketsError}</span>}
          {marketsError === null && staleVenues.length > 0 && (
            <span role="status">Market list stale for {staleVenues.join(", ")} (no update for over 3 min)</span>
          )}
        </span>
      )}
    </>
  );
}
