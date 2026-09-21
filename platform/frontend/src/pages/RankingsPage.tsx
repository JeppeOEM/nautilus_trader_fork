import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";

import { fetchRankings, fetchTechnicalsColumns, fetchTechnicalsValues, saveTechnicalsColumns } from "../api/client";
import type { TechnicalsColumn } from "../api/schema";
import IndicatorPicker from "../components/chart/IndicatorPicker";
import { useLiveChannel } from "../hooks/useLiveChannel";
import FilterPanel, { type FilterField } from "./FilterPanel";
import { applyFilters, type FilterCondition } from "./filters";
import { buildGroups, COLUMN_TIMEFRAMES, columnBarSeconds, reorder } from "./technicals";

// Client-side heartbeat staleness threshold: mirrors bot_tui/ranking_state.py's
// _RANKING_STALE_SECONDS = 15.0 (3x ranking_engine's RANKING_HEARTBEAT_SECONDS=5)
// for cross-surface consistency (platform/CLAUDE.md SSOT-03 spirit) -- hand-declared, not
// imported, since no cross-language import path exists between Python and TS.
//
// This answers a different question than `stale_instrument_ids` below: this is
// "data_api/the browser hasn't heard a rankings:live message recently at all" (the
// whole message is old); stale_instrument_ids is ranking_engine's own per-instrument
// market-data-staleness judgment, already computed into the message itself. Both are
// real and both get their own visible marker (Design Notes: "do not conflate").
const RANKING_STALE_MS = 15_000;

// One bulk indicator recompute for every ranked coin per poll -- slow by design.
const TECHNICALS_POLL_MS = 60_000;

// Venue chips (Story 22.10): the *deselected* venues, per viewer, in this browser only.
// Storing only what was switched off is what makes a venue that appears later (a new
// collector) shown by default -- it can't be hidden by state saved before it existed.
// A per-viewer convenience: blocked/throwing storage just means every venue is shown and
// toggles live in memory for the session.
const DESELECTED_VENUES_STORAGE_KEY = "rankings-deselected-venues";

function loadDeselectedVenues(): Set<string> {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(DESELECTED_VENUES_STORAGE_KEY) ?? "[]");
    return new Set(Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === "string") : []);
  } catch {
    return new Set();
  }
}

function saveDeselectedVenues(venues: Set<string>): void {
  try {
    localStorage.setItem(DESELECTED_VENUES_STORAGE_KEY, JSON.stringify([...venues].sort()));
  } catch {
    // storage blocked: the selection just won't survive a reload
  }
}

// Filter-field keys for Technicals outputs: `tech:{entry name}.{output attr}` -- name-based,
// so a saved condition survives reordering/removing other columns.
const TECHNICAL_FIELD_PREFIX = "tech:";

// Hand-declared TS mirror of ml_signals/ranking_columns.py's RANKING_COLS
// (platform/CLAUDE.md SSOT-03) -- same precedent as bot_tui's own urwid renderer
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

  // Technicals columns (Story 17.5): the screener-wide selection, owned/persisted by the shared
  // IndicatorPicker; header actions below (remove/reorder) save directly and bump `reloadKey`
  // so the picker re-reads the same persisted list. Values refresh on a slow poll -- one bulk
  // request for every ranked coin, not per-tick.
  const [technicalsEntries, setTechnicalsEntries] = useState<TechnicalsColumn[]>([]);
  const [reloadKey, setReloadKey] = useState(0);
  const savedLocally = useRef(false); // a header save beat the mount fetch: its result is stale
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const technicalsActive = activeTab === "technicals" && technicalsEntries.length > 0;
  // Filters (Story 17.6) narrow the row set on either tab; a Technicals-field filter therefore
  // needs those columns' entries and values loaded even while Performance is showing.
  const [allFilters, setFilters] = useState<FilterCondition[]>([]);
  // A filter on a Technicals output whose column was removed would exclude every row with no
  // visible cause -- it is ignored (and its chip hidden) with its column, derived here rather
  // than pruned from state so it comes back if the same column is re-added.
  const filters = allFilters.filter((f) => {
    if (!f.field.startsWith(TECHNICAL_FIELD_PREFIX)) return true;
    const name = f.field.slice(TECHNICAL_FIELD_PREFIX.length).replace(/\.[^.]*$/, "");
    return technicalsEntries.some((e) => e.name === name);
  });
  useEffect(() => {
    fetchTechnicalsColumns()
      .then((loaded) => {
        if (!savedLocally.current) setTechnicalsEntries(loaded);
      })
      .catch((err: unknown) => console.error("RankingsPage: failed to load Technicals columns", err));
  }, []);
  // Sticky once the builder has been opened: Technicals outputs only become selectable fields
  // after their first values arrive.
  const [filterBuilderOpened, setFilterBuilderOpened] = useState(false);
  const filtersUseTechnicals =
    filterBuilderOpened || filters.some((f) => f.field.startsWith(TECHNICAL_FIELD_PREFIX));
  const { data: technicalsValues, error: valuesError } = useQuery({
    queryKey: ["technicals-values", JSON.stringify(technicalsEntries)],
    queryFn: () => fetchTechnicalsValues(technicalsEntries),
    enabled: technicalsEntries.length > 0 && (activeTab === "technicals" || filtersUseTechnicals),
    refetchInterval: TECHNICALS_POLL_MS,
    retry: false,
    // No placeholderData: the response is keyed by entry *position*, so keeping the previous
    // selection's values across a reorder/removal would read them under the wrong column.
  });
  const groups = buildGroups(technicalsEntries, technicalsValues);
  const filterFields: FilterField[] = [
    // Conditions compare the raw value; volume24h is displayed in millions but filtered in USD.
    ...RANKING_COLS.map((col) => ({ key: col.key, label: col.key === "volume24h" ? `${col.label} (raw USD)` : col.label })),
    { key: "venue", label: "Venue", text: true },
    { key: "venue_kind", label: "Kind (cex/dex)", text: true },
    { key: "market", label: "Market (perp/spot)", text: true },
    ...groups.flatMap((g) =>
      g.attrs.map((attr) => ({ key: `${TECHNICAL_FIELD_PREFIX}${g.entry.name}.${attr}`, label: `${g.entry.name}.${attr}` })),
    ),
  ];

  function readField(row: RankingRow, field: string): unknown {
    if (!field.startsWith(TECHNICAL_FIELD_PREFIX)) return row[field];
    const path = field.slice(TECHNICAL_FIELD_PREFIX.length);
    const dot = path.lastIndexOf(".");
    const entryIndex = technicalsEntries.findIndex((e) => e.name === path.slice(0, dot));
    return entryIndex < 0 ? undefined : technicalsValues?.[row.instrument_id]?.[`${entryIndex}.${path.slice(dot + 1)}`];
  }

  const [deselectedVenues, setDeselectedVenues] = useState<Set<string>>(loadDeselectedVenues);

  function toggleVenue(venue: string): void {
    const next = new Set(deselectedVenues);
    if (next.has(venue)) next.delete(venue);
    else next.add(venue);
    setDeselectedVenues(next);
    saveDeselectedVenues(next);
  }

  const [technicalsError, setTechnicalsError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Applied to local state immediately, so a rapid second header action builds on this one
  // instead of the pre-PUT list (which would silently undo it); the picker is re-synced from
  // disk afterwards, and on failure too, rolling the optimistic change back.
  function saveEntries(next: TechnicalsColumn[]): void {
    savedLocally.current = true;
    setTechnicalsEntries(next);
    setTechnicalsError(null);
    setSaving(true);
    saveTechnicalsColumns(next)
      .catch((err: unknown) => {
        console.error("RankingsPage: failed to save Technicals columns", err);
        setTechnicalsError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        setSaving(false);
        setReloadKey((k) => k + 1);
      });
  }

  // Live WS ticks take over from the initial REST seed the moment the first one
  // arrives -- row order is message order verbatim, never re-sorted client-side
  // (epics AC3/platform/CLAUDE.md: "no client-side re-sort beyond the active Ranking
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

  // Rank is the row's position in the live message, so it stays the true rank when filtered.
  // Technicals conditions can't be evaluated until their values are loaded -- skipped (not
  // treated as "no match") so the table isn't blanked, with a visible note below.
  const technicalsPending = technicalsValues === undefined;
  const activeFilters = technicalsPending
    ? filters.filter((f) => !f.field.startsWith(TECHNICAL_FIELD_PREFIX))
    : filters;
  const skippedFilters = filters.length - activeFilters.length;
  // Chips narrow by venue first, then the FilterPanel conditions apply; rank is taken on the
  // full message beforehand, so it stays the message rank either way.
  // A deselected venue keeps its chip even while it has no rows, so a hide can always be undone.
  const venues = [
    ...new Set([...rows.flatMap((row) => (typeof row.venue === "string" ? [row.venue] : [])), ...deselectedVenues]),
  ].sort();
  const venueRows = rows
    .map((row, index) => ({ row, rank: index + 1 }))
    .filter(({ row }) => typeof row.venue !== "string" || !deselectedVenues.has(row.venue));
  const visibleRows = applyFilters(
    venueRows,
    activeFilters,
    ({ row }, field) => readField(row, field),
  );

  return (
    <div className="term-box" data-label="Rankings">
      <div className="filter-panel" role="group" aria-label="Venues">
        {venues.map((venue) => {
          const on = !deselectedVenues.has(venue);
          return (
            <button
              key={venue}
              type="button"
              className={`tabbtn${on ? " active" : ""}`}
              aria-pressed={on}
              onClick={() => toggleVenue(venue)}
            >
              {venue}
            </button>
          );
        })}
      </div>
      {rows.length > 0 && venueRows.length === 0 && (
        <p className="rankings-empty">every venue is deselected — select one above to show its coins</p>
      )}
      <FilterPanel
        fields={filterFields}
        conditions={filters}
        onChange={setFilters}
        onOpen={() => setFilterBuilderOpened(true)}
      />
      {skippedFilters > 0 && (
        <p className="rankings-empty">
          {skippedFilters} Technicals filter(s) not applied yet — waiting for indicator values
          {valuesError ? ` (${valuesError instanceof Error ? valuesError.message : String(valuesError)})` : ""}
        </p>
      )}
      <div className="tabs" role="tablist">
        {(["performance", "technicals"] as const).map((tab) => (
          <button
            key={tab}
            type="button"
            role="tab"
            aria-selected={activeTab === tab}
            className={`tabbtn${activeTab === tab ? " active" : ""}`}
            onClick={() => setActiveTab(tab)}
          >
            {tab === "performance" ? "Performance" : "Technicals"}
          </button>
        ))}
      </div>
      <table className="rankings-table">
        <thead>
          <tr>
            <th rowSpan={technicalsActive ? 2 : 1}>Rank</th>
            <th rowSpan={technicalsActive ? 2 : 1}>Instrument</th>
            {activeTab === "performance" && <th>Venue</th>}
            {activeTab === "performance" && <th>Kind</th>}
            {activeTab === "performance" && <th>Market</th>}
            {activeTab === "performance" &&
              RANKING_COLS.map((col) => <th key={col.key}>{col.label}</th>)}
            {technicalsActive &&
              groups.map((group) => (
                <th
                  key={group.entryIndex}
                  colSpan={Math.max(1, group.attrs.length)}
                  draggable
                  onDragStart={() => setDragFrom(group.entryIndex)}
                  onDragEnd={() => setDragFrom(null)}
                  onDragOver={(event) => event.preventDefault()}
                  onDrop={() => {
                    if (dragFrom !== null) saveEntries(reorder(technicalsEntries, dragFrom, group.entryIndex));
                    setDragFrom(null);
                  }}
                >
                  {group.entry.name}
                  {/* The bar size this column is computed on -- always visible, editable in place. */}
                  <select
                    aria-label={`${group.entry.name} timeframe`}
                    value={columnBarSeconds(group.entry)}
                    disabled={saving}
                    onChange={(event) =>
                      saveEntries(
                        technicalsEntries.map((e, i) =>
                          i === group.entryIndex ? { ...e, bar_seconds: Number(event.target.value) } : e,
                        ),
                      )
                    }
                  >
                    {COLUMN_TIMEFRAMES.map((tf) => (
                      <option key={tf.seconds} value={tf.seconds}>
                        {tf.label}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="rankings-history-link"
                    aria-label={`Remove ${group.entry.name} column`}
                    onClick={() => saveEntries(technicalsEntries.filter((_, i) => i !== group.entryIndex))}
                  >
                    ×
                  </button>
                </th>
              ))}
          </tr>
          {technicalsActive && (
            <tr>
              {groups.flatMap((group) =>
                group.attrs.length > 0
                  ? group.attrs.map((attr) => <th key={`${group.entryIndex}.${attr}`}>{attr}</th>)
                  : [<th key={`${group.entryIndex}.pending`}>—</th>],
              )}
            </tr>
          )}
        </thead>
        <tbody>
          {/* No Technicals columns configured yet -- one dim empty-state row spanning the
              pinned Rank/Instrument pair; add one via the picker below the table. */}
          {activeTab === "technicals" && technicalsEntries.length === 0 && (
            <tr>
              <td colSpan={2} className="rankings-empty">
                no columns yet — add one below
              </td>
            </tr>
          )}
          {visibleRows.map(({ row, rank }) => {
            const marketDataStale = staleInstrumentIds.has(row.instrument_id);
            const rowStale = isMessageStale || marketDataStale;
            return (
              <tr
                key={row.instrument_id}
                onClick={() => navigate(`/chart/${row.instrument_id}`)}
                tabIndex={0}
                onKeyDown={(event) => {
                  // Only the row itself -- Enter on the nested history button must not also open the chart.
                  if (event.key === "Enter" && event.target === event.currentTarget) {
                    navigate(`/chart/${row.instrument_id}`);
                  }
                }}
                data-stale={rowStale ? "true" : "false"}
                className="rankings-row"
              >
                <td>{rank}</td>
                <td>
                  {row.instrument_id}
                  {isMessageStale && <span title="rankings feed stale"> ⏱</span>}
                  {marketDataStale && <span title="market data stale"> ⚠</span>}
                  {activeTab === "performance" && (
                    // stopPropagation: the row's own onClick goes to /chart/:iid (Story 17.4).
                    <button
                      type="button"
                      className="rankings-history-link"
                      title="31-day history"
                      aria-label={`31-day history for ${row.instrument_id}`}
                      onClick={(event) => {
                        event.stopPropagation();
                        navigate(`/history/${row.instrument_id}`);
                      }}
                    >
                      ⏲
                    </button>
                  )}
                </td>
                {activeTab === "performance" && <td>{typeof row.venue === "string" ? row.venue : "—"}</td>}
                {activeTab === "performance" && <td>{typeof row.venue_kind === "string" ? row.venue_kind : "—"}</td>}
                {activeTab === "performance" && <td>{typeof row.market === "string" ? row.market : "—"}</td>}
                {activeTab === "performance" &&
                  RANKING_COLS.map((col) => <td key={col.key}>{formatCell(col, row[col.key])}</td>)}
                {technicalsActive &&
                  groups.flatMap((group) =>
                    (group.attrs.length > 0 ? group.attrs : [null]).map((attr) => {
                      const value = attr === null ? null : technicalsValues?.[row.instrument_id]?.[`${group.entryIndex}.${attr}`];
                      return (
                        <td key={`${group.entryIndex}.${attr}`}>
                          {value === null || value === undefined ? "—" : value.toFixed(4)}
                        </td>
                      );
                    }),
                  )}
              </tr>
            );
          })}
        </tbody>
      </table>
      {activeTab === "technicals" && technicalsEntries.length > 0 && (
        <p className="rankings-empty">
          Each column shows the latest value on its own timeframe (selector in the header).
        </p>
      )}
      {activeTab === "technicals" && (technicalsError ?? valuesError) && (
        <p style={{ color: "var(--color-danger)" }}>
          {technicalsError ?? (valuesError instanceof Error ? valuesError.message : String(valuesError))}
        </p>
      )}
      {activeTab === "technicals" && (
        <IndicatorPicker
          fetchConfig={fetchTechnicalsColumns}
          saveConfig={saveTechnicalsColumns}
          reloadKey={reloadKey}
          disabled={saving}
          onEntriesChange={setTechnicalsEntries}
        />
      )}
    </div>
  );
}
