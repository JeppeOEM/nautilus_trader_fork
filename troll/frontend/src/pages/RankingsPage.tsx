import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";

import { fetchRankings } from "../api/client";
import { useLiveChannel } from "../hooks/useLiveChannel";

// Client-side heartbeat staleness threshold: mirrors bot_tui/ranking_state.py's
// _RANKING_STALE_SECONDS = 15.0 (3x ranking_engine's RANKING_HEARTBEAT_SECONDS=5)
// for cross-surface consistency (troll/CLAUDE.md SSOT-03 spirit) -- hand-declared, not
// imported, since no cross-language import path exists between Python and TS.
//
// This answers a different question than `stale_instrument_ids` below: this is
// "data_api/the browser hasn't heard a rankings:live message recently at all" (the
// whole message is old); stale_instrument_ids is ranking_engine's own per-instrument
// market-data-staleness judgment, already computed into the message itself. Both are
// real and both get their own visible marker (Design Notes: "do not conflate").
const RANKING_STALE_MS = 15_000;

// Hand-declared TS mirror of ml_signals/ranking_columns.py's RANKING_COLS
// (troll/CLAUDE.md SSOT-03) -- same precedent as bot_tui's own urwid renderer
// independently mirroring the same metadata. If ranking_columns.py's column list
// changes, port the change here too; this is deliberately a short, flat array so that
// drift is easy to notice.
interface RankingColumn {
  key: string;
  label: string;
  format: (value: unknown) => string;
}

function fmtSigned(v: number, decimals: number): string {
  return `${v >= 0 ? "+" : ""}${v.toFixed(decimals)}`;
}

function fmtFixed(v: number, decimals: number): string {
  return v.toFixed(decimals);
}

function fmtPercent(v: number): string {
  return `${fmtSigned(v, 2)}%`;
}

function fmtMillions(v: number): string {
  return `${(v / 1e6).toFixed(3)}M`;
}

const RANKING_COLS: RankingColumn[] = [
  { key: "ofi_10_z", label: "OFI10z", format: (v) => fmtSigned(v as number, 2) },
  { key: "obi_10", label: "OBI10", format: (v) => fmtFixed(v as number, 3) },
  { key: "obi_5", label: "OBI5", format: (v) => fmtFixed(v as number, 3) },
  { key: "obi_3", label: "OBI3", format: (v) => fmtFixed(v as number, 3) },
  { key: "cvd", label: "CVD", format: (v) => fmtSigned(v as number, 2) },
  { key: "spread", label: "Spread", format: (v) => fmtFixed(v as number, 6) },
  { key: "volume_delta", label: "Vol d 60s", format: (v) => fmtSigned(v as number, 2) },
  { key: "price", label: "Price", format: (v) => fmtFixed(v as number, 4) },
  { key: "pct_1h", label: "1h %", format: (v) => fmtPercent(v as number) },
  { key: "pct_24h", label: "24h %", format: (v) => fmtPercent(v as number) },
  { key: "pct_1w", label: "1w %", format: (v) => fmtPercent(v as number) },
  { key: "pct_1m", label: "1m %", format: (v) => fmtPercent(v as number) },
  { key: "volatility", label: "Vol(catalog)", format: (v) => fmtFixed(v as number, 6) },
  { key: "volatility_score", label: "Vol Score", format: (v) => fmtFixed(v as number, 6) },
  { key: "volume24h", label: "Vol24h", format: (v) => fmtMillions(v as number) },
];

function formatCell(col: RankingColumn, value: unknown): string {
  if (value === null || value === undefined) return "—";
  try {
    return col.format(value);
  } catch (err) {
    // Log rather than silently swallow -- a formatting exception means the upstream
    // value's shape/type doesn't match this column's expectation, a real bug worth
    // surfacing, not just an inert "ERR" cell with no trace.
    console.error(`RankingsPage: failed to format column "${col.key}"`, value, err);
    return "ERR";
  }
}

// /ws/live's rankings:live relay shape -- hand-written per the architecture spine's
// AD-F5 WS exception (no OpenAPI coverage for WebSocket frames); mirrors
// ranking_engine/engine.py:605-687's _build_rankings_message() wire format exactly.
// Never rename "ranks" here -- /ws/live relays it verbatim, unlike GET /api/rankings's
// "items" (epics AC2: "no reshaping beyond channel subscription").
export interface RankingRow {
  instrument_id: string;
  [key: string]: unknown;
}

export interface RankingsLiveMessage {
  mode: string;
  updated_at: number;
  ranks: RankingRow[];
  stale_instrument_ids: string[];
}

export default function RankingsPage() {
  const navigate = useNavigate();
  const { data } = useQuery({ queryKey: ["rankings"], queryFn: fetchRankings, retry: false });
  const live = useLiveChannel<RankingsLiveMessage>();

  // A row must go visibly stale purely from wall-clock time passing, even if no new
  // message ever arrives again (a genuinely dead feed) -- reading Date.now() directly
  // during render wouldn't re-evaluate without some other trigger, so a 1s tick state
  // forces a re-render to recheck staleness (also resolves oxlint's react(purity)
  // warning against calling Date.now() inline during render).
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const interval = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(interval);
  }, []);

  // Which column set renders to the right of the pinned Rank/Instrument columns
  // (Story 17.1). Deliberately plain useState, never in the useQuery key or any
  // effect deps -- a tab switch may only change the JSX branch below, never
  // refetch rankings or open a new live-channel subscription (AC #4). The tab
  // bar itself reuses the shared .tabs/.tabbtn pattern from theme.css (the same
  // classes DocsPage's sidebar tabs use) rather than a second tab visual style.
  const [activeTab, setActiveTab] = useState<"performance" | "technicals">("performance");

  // Live WS ticks take over from the initial REST seed the moment the first one
  // arrives -- row order is message order verbatim, never re-sorted client-side
  // (epics AC3/troll/CLAUDE.md: "no client-side re-sort beyond the active Ranking
  // Mode already reflected in that order").
  const rows: RankingRow[] = live.latest?.ranks ?? (data?.items as unknown as RankingRow[] | undefined) ?? [];
  const updatedAtNs: number | undefined = live.latest?.updated_at ?? data?.updated_at;
  const staleInstrumentIds = new Set<string>(
    live.latest?.stale_instrument_ids ?? data?.stale_instrument_ids ?? [],
  );

  // Two distinct staleness signals (Design Notes -- do not conflate): this one is
  // "the whole message hasn't been refreshed recently" (a heartbeat miss); the other,
  // per-instrument stale_instrument_ids set above, is ranking_engine's own market-data
  // staleness judgment already baked into the message.
  const isMessageStale = updatedAtNs !== undefined && now - updatedAtNs / 1_000_000 > RANKING_STALE_MS;

  // Before the bus has cached anything (GET /api/rankings 503, no /ws/live message
  // yet), show a loading state -- never an empty table (I/O matrix: "first load,
  // before any message cached").
  if (rows.length === 0 && updatedAtNs === undefined) {
    return <p className="term-loading">Loading rankings…</p>;
  }

  return (
    <div className="term-box" data-label="Rankings">
      <div className="tabs">
        <div
          className={`tabbtn${activeTab === "performance" ? " active" : ""}`}
          onClick={() => setActiveTab("performance")}
        >
          Performance
        </div>
        <div
          className={`tabbtn${activeTab === "technicals" ? " active" : ""}`}
          onClick={() => setActiveTab("technicals")}
        >
          Technicals
        </div>
      </div>
      <table className="rankings-table">
        <thead>
          <tr>
            <th>Rank</th>
            <th>Instrument</th>
            {activeTab === "performance" &&
              RANKING_COLS.map((col) => <th key={col.key}>{col.label}</th>)}
          </tr>
        </thead>
        <tbody>
          {/* Technicals has no user-managed columns yet (Story 17.5 adds them) --
              one dim empty-state row spanning the table's full width, which on
              this tab is exactly the pinned Rank/Instrument pair. */}
          {activeTab === "technicals" && (
            <tr>
              <td colSpan={2} className="rankings-empty">
                no columns yet — click + to add one
              </td>
            </tr>
          )}
          {rows.map((row, index) => {
            const marketDataStale = staleInstrumentIds.has(row.instrument_id);
            const rowStale = isMessageStale || marketDataStale;
            return (
              <tr
                key={row.instrument_id}
                onClick={() => navigate(`/chart/${row.instrument_id}`)}
                data-stale={rowStale ? "true" : "false"}
                className="rankings-row"
              >
                <td>{index + 1}</td>
                <td>
                  {row.instrument_id}
                  {isMessageStale && <span title="rankings feed stale"> ⏱</span>}
                  {marketDataStale && <span title="market data stale"> ⚠</span>}
                </td>
                {activeTab === "performance" &&
                  RANKING_COLS.map((col) => <td key={col.key}>{formatCell(col, row[col.key])}</td>)}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
