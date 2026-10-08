import { useState } from "react";

import { AUTO_ANCHOR_PRESETS, type AutoAnchorPreset } from "../../lib/autoAnchor";
import {
  MAX_SESSIONS,
  SESSION_PERIODS,
  SESSION_PERIOD_LABELS,
  SESSION_PRESETS,
  type SessionPeriod,
  type SessionPreset,
  type SessionProfileSettings,
} from "../../lib/sessionProfile";
import { type TimeZoneSetting, formatDateTime, timeZoneLabel } from "../../lib/time";
import { MAX_IB_MINUTES, MIN_IB_MINUTES } from "../../lib/tpo";
import type { VolumeProfileSettings } from "../../lib/volumeProfile";
import SettingsDialogShell from "./SettingsDialogShell";
import VolumeProfileSettingsPanel from "./VolumeProfileSettings";

/** The one session-type slot's state (Story 18.8/18.9/32.7): the preset and its options. */
export interface SessionSlot {
  preset: SessionPreset;
  period: SessionPeriod;
  settings: SessionProfileSettings;
  /** Story 32.7: the Auto Anchored preset, the TPO's initial balance minutes and letters switch. */
  anchor: AutoAnchorPreset;
  ibMinutes: number;
  letters: boolean;
}

export type SessionOptions = { anchor?: AutoAnchorPreset; ibMinutes?: number; letters?: boolean };

interface VrvpProps {
  active: boolean;
  settings: VolumeProfileSettings;
  /** The view reaches past the oldest loaded bar, so the profile covers less than is on screen. */
  pastOldest: boolean;
  onAdd: () => void;
  onRemove: () => void;
  onSettingsChange: (next: VolumeProfileSettings) => void;
}

interface SessionProps {
  /** The slot's preset and options, or null when no session-type profile is on. */
  active: SessionSlot | null;
  /** How many sessions/periods are actually drawn right now (0 while history loads). */
  renderedCount: number;
  /** Older session history is being paged in right now. */
  loading: boolean;
  onAdd: (preset: SessionPreset) => void;
  onRemove: () => void;
  onPeriodChange: (period: SessionPeriod) => void;
  onOptionsChange: (options: SessionOptions) => void;
  onSettingsChange: (next: SessionProfileSettings) => void;
}

export interface FrvpRange {
  id: string;
  startTime: number;
  endTime: number;
}

interface FrvpProps {
  ranges: readonly FrvpRange[];
  /** One settings set shared by every placed range (Story 18.6). */
  settings: VolumeProfileSettings;
  /** The rail's FRVP tool is armed: the settings show before the first range is placed (DW-150). */
  armed: boolean;
  /** The rail's FRVP tool cannot be armed right now (Lines mode). */
  drawDisabled: boolean;
  onDraw: () => void;
  onRemove: (id: string) => void;
  onSettingsChange: (next: VolumeProfileSettings) => void;
}

interface Props {
  /** Every volume overlay profiles candle bars, so each draws in Candles mode only. */
  candlesMode: boolean;
  /** Story 33.12: the zone a fixed range's edges print in (the layout's `time_zone`). */
  timeZone: TimeZoneSetting;
  vrvp: VrvpProps;
  session: SessionProps;
  frvp: FrvpProps;
  onClose: () => void;
}

const CANDLES_ONLY = "Candles mode only";
const VRVP_LABEL = "Visible Range Volume Profile";
const FRVP_LABEL = "Fixed Range Volume Profile";

const ANCHOR_LABELS: Record<AutoAnchorPreset, string> = {
  session: "Session",
  week: "Week",
  month: "Month",
  highest_high: "Highest high",
  lowest_low: "Lowest low",
  auto: "Auto (by bar size)",
};

/**
 * The Volume overlays dialog: the chart-only volume overlays' add list on top, and below it the
 * overlays on the chart, each with its settings and a Remove -- the counterpart of the Indicators
 * dialog for the overlays that have no server catalog entry (Story 18.7's reason they never joined
 * it). Every edit applies at once, like the inline controls it replaced: nothing here is a draft.
 *
 * The slot rules are the page's, unchanged: VRVP is one instance; SVP / SVP HD / PVP / TPO / Auto
 * Anchored share ONE session-type slot, so adding one while another is on switches the slot; FRVPs
 * are drawn on the chart with the rail's tool, as many as wanted, under one shared settings set.
 */
export default function VolumeOverlaysDialog({ candlesMode, timeZone, vrvp, session, frvp, onClose }: Props) {
  const showFrvp = frvp.ranges.length > 0 || frvp.armed;
  const nothingOn = !vrvp.active && session.active === null && !showFrvp;
  return (
    <SettingsDialogShell title="Volume overlays" className="volume-overlays-dialog" onClose={onClose}>
      {/* Esc closes this dialog (the native cancel, which a stopped keydown still runs) and nothing
          else: the page's Esc-disarms handler must not drop an FRVP tool armed before it opened. */}
      <div
        className="volume-overlays-content"
        onKeyDown={(e) => {
          if (e.key === "Escape") e.stopPropagation();
        }}
      >
      <div className="indicator-dialog-head">
        <h2>Volume overlays</h2>
        <button type="button" aria-label="Close volume overlays" onClick={onClose}>
          &times;
        </button>
      </div>
      <AddList candlesMode={candlesMode} timeZone={timeZone} vrvp={vrvp} session={session} frvp={frvp} onClose={onClose} />
      <h3>On the chart</h3>
      {nothingOn && <p className="indicator-dialog-empty">Nothing added yet</p>}
      {vrvp.active && <VrvpEntry candlesMode={candlesMode} vrvp={vrvp} />}
      {session.active && <SessionEntry candlesMode={candlesMode} active={session.active} session={session} />}
      {showFrvp && <FrvpEntry candlesMode={candlesMode} timeZone={timeZone} frvp={frvp} />}
      </div>
    </SettingsDialogShell>
  );
}

/**
 * DW-151/DW-153 under the chart: a profile drawn from incomplete data says so where the chart is
 * read, not only inside the dialog (which is closed most of the time). Candles mode only: no volume
 * overlay draws in Lines mode, so there is nothing partial to flag there.
 */
export function VolumeOverlayNotices({
  candlesMode,
  vrvp,
  session,
}: {
  candlesMode: boolean;
  vrvp: Pick<VrvpProps, "active" | "pastOldest">;
  session: Pick<SessionProps, "active" | "renderedCount" | "loading">;
}) {
  if (!candlesMode) return null;
  const notices: string[] = [];
  if (vrvp.active && vrvp.pastOldest) notices.push(`${VRVP_LABEL}: covers the loaded bars only`);
  const active = session.active;
  if (active) {
    const label = SESSION_PRESETS[active.preset].label;
    if (session.loading) notices.push(`${label}: loading session history`);
    if (active.preset === "auto" ? session.renderedCount === 0 : session.renderedCount < active.settings.sessionCount) {
      notices.push(
        active.preset === "auto"
          ? `${label}: not drawn yet`
          : `${label}: ${session.renderedCount} of ${active.settings.sessionCount} sessions drawn`,
      );
    }
  }
  if (notices.length === 0) return null;
  return (
    <ul className="chart-overlay-notices" aria-label="Volume overlay notices">
      {notices.map((notice) => (
        <li key={notice}>{notice}</li>
      ))}
    </ul>
  );
}

function AddRow({
  label,
  ariaLabel,
  tag,
  disabled,
  title,
  onClick,
}: {
  label: string;
  ariaLabel: string;
  tag: string;
  disabled: boolean;
  title?: string;
  onClick: () => void;
}) {
  return (
    <li>
      <button type="button" aria-label={ariaLabel} disabled={disabled} title={title} onClick={onClick}>
        <span>{label}</span>
        <span className="indicator-dialog-tag">{tag}</span>
      </button>
    </li>
  );
}

function sessionAddTag(preset: SessionPreset, active: SessionSlot | null, candlesMode: boolean): string {
  if (active?.preset === preset) return "on the chart";
  if (!candlesMode) return CANDLES_ONLY;
  return active ? `replaces ${SESSION_PRESETS[active.preset].label}` : "add";
}

function AddList({ candlesMode, vrvp, session, frvp, onClose }: Props) {
  return (
    <section aria-label="Add a volume overlay">
      {!candlesMode && <p className="indicator-dialog-tag">Volume overlays draw in Candles mode only: switch to Candles to add one.</p>}
      <ul className="indicator-dialog-results">
        <AddRow
          label={VRVP_LABEL}
          ariaLabel="Add visible range volume profile"
          tag={vrvp.active ? "on the chart" : candlesMode ? "add" : CANDLES_ONLY}
          disabled={vrvp.active || !candlesMode}
          onClick={vrvp.onAdd}
        />
        <AddRow
          label={FRVP_LABEL}
          ariaLabel="Draw fixed range volume profile"
          tag={!frvp.drawDisabled ? "draw on chart" : candlesMode ? "unavailable" : CANDLES_ONLY}
          title="Arms the rail's FRVP tool: drag the range on the chart"
          disabled={frvp.drawDisabled}
          onClick={() => {
            onClose();
            frvp.onDraw();
          }}
        />
      </ul>
      <p className="indicator-dialog-tag">
        One session-type profile at a time: adding another of these replaces the one on the chart.
      </p>
      <ul className="indicator-dialog-results">
        {(Object.keys(SESSION_PRESETS) as SessionPreset[]).map((preset) => (
          <AddRow
            key={preset}
            label={SESSION_PRESETS[preset].label}
            ariaLabel={`Add ${SESSION_PRESETS[preset].label}`}
            tag={sessionAddTag(preset, session.active, candlesMode)}
            disabled={!candlesMode || session.active?.preset === preset}
            onClick={() => session.onAdd(preset)}
          />
        ))}
      </ul>
    </section>
  );
}

function EntryHead({ label, removeLabel, onRemove }: { label: string; removeLabel: string; onRemove: () => void }) {
  return (
    <div className="volume-overlay-head">
      <strong>{label}</strong>
      <button type="button" aria-label={removeLabel} onClick={onRemove}>
        Remove
      </button>
    </div>
  );
}

function VrvpEntry({ candlesMode, vrvp }: { candlesMode: boolean; vrvp: VrvpProps }) {
  return (
    <section className="volume-overlay" aria-label={VRVP_LABEL}>
      <EntryHead label={VRVP_LABEL} removeLabel="Remove visible range volume profile" onRemove={vrvp.onRemove} />
      {!candlesMode && <span>Shown in Candles mode only</span>}
      {/* DW-151: the profile is built from the loaded bars; scrolling further back loads more while
          older history exists. Until then (or for good, at the start of the history) the profile
          must not pass for a profile of the whole view. */}
      {candlesMode && vrvp.pastOldest && (
        <span role="status">Covers loaded bars only: the view reaches past the oldest loaded bar</span>
      )}
      <VolumeProfileSettingsPanel
        title="Visible range volume profile settings"
        value={vrvp.settings}
        onChange={vrvp.onSettingsChange}
      />
    </section>
  );
}

/** The initial-balance field: a local draft, so it can be cleared and retyped; only a whole number the
 * server stores is committed (on change), and a blur puts the saved value back over a refused draft. */
function IbMinutesInput({ value, onCommit }: { value: number; onCommit: (minutes: number) => void }) {
  const [draft, setDraft] = useState(String(value));
  const [seen, setSeen] = useState(value);
  if (value !== seen) {
    setSeen(value);
    setDraft(String(value));
  }
  return (
    <input
      type="number"
      aria-label="Initial balance minutes"
      min={MIN_IB_MINUTES}
      max={MAX_IB_MINUTES}
      step={1}
      value={draft}
      onChange={(e) => {
        setDraft(e.target.value);
        const minutes = Number(e.target.value);
        if (e.target.value.trim() !== "" && Number.isInteger(minutes) && minutes >= MIN_IB_MINUTES && minutes <= MAX_IB_MINUTES) {
          onCommit(minutes);
        }
      }}
      onBlur={() => setDraft(String(value))}
    />
  );
}

/** The preset-specific fields: the Auto Anchored anchor, the TPO's initial balance and letters, and the
 * period of the presets whose period is the operator's to choose. */
function SessionOptionFields({ active, session }: { active: SessionSlot; session: SessionProps }) {
  return (
    <>
      {active.preset === "auto" && (
        <label>
          Anchor
          <select
            aria-label="Anchor"
            value={active.anchor}
            onChange={(e) => session.onOptionsChange({ anchor: e.target.value as AutoAnchorPreset })}
          >
            {AUTO_ANCHOR_PRESETS.map((anchor) => (
              <option key={anchor} value={anchor}>
                {ANCHOR_LABELS[anchor]}
              </option>
            ))}
          </select>
        </label>
      )}
      {active.preset === "tpo" && (
        <>
          <label>
            Initial balance (minutes)
            <IbMinutesInput value={active.ibMinutes} onCommit={(ibMinutes) => session.onOptionsChange({ ibMinutes })} />
          </label>
          <label>
            <input
              type="checkbox"
              aria-label="TPO letters"
              checked={active.letters}
              onChange={(e) => session.onOptionsChange({ letters: e.target.checked })}
            />
            Letters
          </label>
        </>
      )}
      {!SESSION_PRESETS[active.preset].fixedPeriod && (
        <label>
          Period
          <select
            aria-label="Profile period"
            value={active.period}
            onChange={(e) => session.onPeriodChange(e.target.value as SessionPeriod)}
          >
            {SESSION_PERIODS.map((period) => (
              <option key={period} value={period}>
                {SESSION_PERIOD_LABELS[period]}
              </option>
            ))}
          </select>
        </label>
      )}
    </>
  );
}

/** What is drawn against what was asked for: a profile still loading must not pass for a complete one. */
function SessionStatus({ candlesMode, active, session }: { candlesMode: boolean; active: SessionSlot; session: SessionProps }) {
  if (!candlesMode) return <span>Shown in Candles mode only</span>;
  const auto = active.preset === "auto";
  return (
    <>
      {session.loading && <span role="status">Loading session history…</span>}
      {!auto && session.renderedCount < active.settings.sessionCount && (
        <span>
          Showing {session.renderedCount} of {active.settings.sessionCount} (history still loading, or beyond the fetch window)
        </span>
      )}
      {auto && session.renderedCount === 0 && (
        <span>Not drawn yet (history still loading, or the anchor is beyond the fetch window)</span>
      )}
    </>
  );
}

function SessionEntry({ candlesMode, active, session }: { candlesMode: boolean; active: SessionSlot; session: SessionProps }) {
  const label = SESSION_PRESETS[active.preset].label;
  return (
    <section className="volume-overlay" aria-label={label}>
      <EntryHead label={label} removeLabel="Remove session volume profile" onRemove={session.onRemove} />
      <SessionOptionFields active={active} session={session} />
      <SessionStatus candlesMode={candlesMode} active={active} session={session} />
      {active.preset === "auto" ? (
        // One profile from the anchor to the latest bar: no session count to set.
        <VolumeProfileSettingsPanel
          title="Session volume profile settings"
          value={{ ...active.settings, sessionCount: undefined }}
          onChange={(next) => session.onSettingsChange({ ...next, sessionCount: active.settings.sessionCount })}
        />
      ) : (
        <VolumeProfileSettingsPanel
          title="Session volume profile settings"
          value={active.settings}
          onChange={session.onSettingsChange}
          maxSessions={MAX_SESSIONS}
        />
      )}
    </section>
  );
}

/** A range's edges as the operator reads them: date and time to the second (two ranges on sub-minute
 * bars never read alike), in the chart's time zone (`lib/time.ts`). */
function FrvpEntry({ candlesMode, timeZone, frvp }: { candlesMode: boolean; timeZone: TimeZoneSetting; frvp: FrvpProps }) {
  return (
    <section className="volume-overlay" aria-label={FRVP_LABEL}>
      <div className="volume-overlay-head">
        <strong>{FRVP_LABEL}</strong>
      </div>
      {!candlesMode && <span>Shown in Candles mode only</span>}
      {frvp.ranges.length === 0 ? (
        <span>Drag a range on the chart to place one.</span>
      ) : (
        <ul className="volume-overlay-ranges">
          {frvp.ranges.map((range) => (
            <li key={range.id}>
              <span>
                {formatDateTime(range.startTime, timeZone)} to {formatDateTime(range.endTime, timeZone)} {timeZoneLabel(timeZone)}
              </span>
              <button type="button" aria-label={`Remove volume profile ${range.id}`} onClick={() => frvp.onRemove(range.id)}>
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
      <span className="indicator-dialog-tag">These settings apply to every placed range.</span>
      <VolumeProfileSettingsPanel
        title="Fixed range volume profile settings"
        value={frvp.settings}
        onChange={frvp.onSettingsChange}
      />
    </section>
  );
}
