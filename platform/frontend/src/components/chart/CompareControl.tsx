import SymbolSearch from "./SymbolSearch";

interface CompareControlProps {
  instrumentId: string;
  /** The compare symbols already drawn. */
  symbols: readonly string[];
  /** Lines mode: compares are not drawn there. */
  disabled: boolean;
  /** Whether the compare search is open: held by the page, so `Alt+C` opens the same dialog. */
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAdd: (instrumentId: string) => void;
}

/**
 * Story 33.9 / 33.12: the header's Compare button. It opens the symbol search in compare mode
 * (`SymbolSearch`): every live market from `GET /api/markets` but the chart's own and those already
 * compared, a typed id accepted while the list is down, and an id the chart cannot compare refused
 * inline with the reason (`validateCompareInput`) and nothing saved. Disabled in Lines mode, which
 * also closes an open search.
 */
export default function CompareControl({ instrumentId, symbols, disabled, open, onOpenChange, onAdd }: CompareControlProps) {
  return (
    <>
      <button
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open && !disabled}
        disabled={disabled}
        title={disabled ? "Compare draws on candle bars: switch to Candles" : "Add a compare symbol (Alt+C)"}
        onClick={() => onOpenChange(!open)}
      >
        Compare
      </button>
      {open && !disabled && (
        <SymbolSearch
          mode="compare"
          instrumentId={instrumentId}
          compared={symbols}
          onPick={onAdd}
          onClose={() => onOpenChange(false)}
        />
      )}
    </>
  );
}
