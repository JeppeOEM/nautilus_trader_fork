// Story 33.12: the one place the chart page turns a time into text. The layout's `time_zone` picks the
// zone; it changes formatting only. A bar's `time` (and every `t` the server sends) stays UTC epoch
// seconds, so the zone can never move a bar, a drawing anchor or a replay cut (audit D-218).
//
// Mirrored by `views/preferences.py`'s `TIME_ZONES` (`test_time_zone_and_last_price_settings_mirror_the_frontend`
// reads the two literals below, so keep each on its one line).

export type TimeZoneSetting = "utc" | "local" | "exchange";
export const TIME_ZONES: readonly TimeZoneSetting[] = ["utc", "local", "exchange"];

/** The select's label of each zone. */
export const TIME_ZONE_LABELS: Record<TimeZoneSetting, string> = {
  utc: "UTC",
  local: "Local",
  exchange: "Exchange",
};

const DAY_SECONDS = 86_400;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

interface ClockParts {
  year: number;
  month: number; // 0-based
  day: number;
  hours: number;
  minutes: number;
  seconds: number;
}

// Known limit: every collected venue (Bybit, Hyperliquid, dYdX) runs its sessions, funding and
// settlement on UTC, so `exchange` resolves to UTC. Upgrade path: a venue -> IANA zone map read
// through `Intl.DateTimeFormat` once a venue with a local trading session is collected.
function usesUtc(zone: TimeZoneSetting): boolean {
  return zone !== "local";
}

function partsOf(sec: number, zone: TimeZoneSetting): ClockParts | null {
  const date = new Date(sec * 1000);
  // A time Date cannot hold would print "NaN" everywhere; the callers print a placeholder instead.
  if (Number.isNaN(date.getTime())) return null;
  if (usesUtc(zone)) {
    return {
      year: date.getUTCFullYear(),
      month: date.getUTCMonth(),
      day: date.getUTCDate(),
      hours: date.getUTCHours(),
      minutes: date.getUTCMinutes(),
      seconds: date.getUTCSeconds(),
    };
  }
  return {
    year: date.getFullYear(),
    month: date.getMonth(),
    day: date.getDate(),
    hours: date.getHours(),
    minutes: date.getMinutes(),
    seconds: date.getSeconds(),
  };
}

const pad = (n: number): string => String(n).padStart(2, "0");

const dateText = (p: ClockParts): string => `${p.year}-${pad(p.month + 1)}-${pad(p.day)}`;
const clockText = (p: ClockParts, withSeconds: boolean): string =>
  withSeconds ? `${pad(p.hours)}:${pad(p.minutes)}:${pad(p.seconds)}` : `${pad(p.hours)}:${pad(p.minutes)}`;

/** The zone's short name, printed after a time where the zone is not otherwise on screen. */
export function timeZoneLabel(zone: TimeZoneSetting): string {
  return zone === "local" ? "local" : "UTC";
}

/**
 * The crosshair's time label (`localization.timeFormatter`): `YYYY-MM-DD HH:MM`, with seconds only
 * where the time has them (a Lines-mode snapshot second), so a minute bar never reads `:00` noise.
 * A bar of a day or longer (`barSeconds`, 1D/1W) prints its UTC date alone in every zone: its bucket
 * is a UTC day, so the date is the bar's own label, and a local clock time would name an instant
 * inside the bucket the bar does not stand for.
 */
export function formatChartTime(sec: number, zone: TimeZoneSetting, barSeconds: number | null = null): string {
  if (barSeconds !== null && barSeconds >= DAY_SECONDS) {
    const day = partsOf(sec, "utc");
    return day === null ? "invalid time" : dateText(day);
  }
  const p = partsOf(sec, zone);
  return p === null ? "invalid time" : `${dateText(p)} ${clockText(p, sec % 60 !== 0)}`;
}

/** A date and time to the second (a fixed range's edges), without the zone's name. */
export function formatDateTime(sec: number, zone: TimeZoneSetting): string {
  const p = partsOf(sec, zone);
  return p === null ? "invalid time" : `${dateText(p)} ${clockText(p, true)}`;
}

/** A nanosecond event time (`ts_event`) as `HH:MM:SS` (the Liquidation tape). */
export function formatClock(tsNs: number, zone: TimeZoneSetting): string {
  const p = partsOf(Math.floor(tsNs / 1_000_000) / 1000, zone);
  return p === null ? "invalid time" : clockText(p, true);
}

// lightweight-charts' `TickMarkType` values (Year, Month, DayOfMonth, Time, TimeWithSeconds). Read as
// numbers: the enum is a runtime object the tests' module mock does not carry.
const TICK_YEAR = 0;
const TICK_MONTH = 1;
const TICK_DAY = 2;
const TICK_TIME_WITH_SECONDS = 4;

/** A Year/Month/Day tick's plain calendar label. */
function calendarLabel(p: ClockParts, tickMarkType: number): string {
  if (tickMarkType === TICK_YEAR) return String(p.year);
  if (tickMarkType === TICK_MONTH) return MONTHS[p.month];
  return String(p.day);
}

/** A Year/Month/Day tick that does not fall on the zone's midnight: the date and the clock time, so
 * the label names the instant the tick sits on. */
function offMidnightLabel(p: ClockParts, tickMarkType: number): string {
  const clock = clockText(p, false);
  if (tickMarkType === TICK_YEAR) return `${dateText(p)} ${clock}`;
  if (tickMarkType === TICK_MONTH) return `${MONTHS[p.month]} ${p.day} ${clock}`;
  return `${p.day} ${clock}`;
}

/**
 * The time axis' `tickMarkFormatter` for a zone: the label the library would print, in that zone. The
 * library still decides where ticks go: its Year/Month/Day ticks sit on UTC boundaries. In UTC (and
 * `exchange`) those are midnights and print the plain calendar label. In `local` a calendar label
 * would misname the instant (at UTC-5 the 2026-10-07 00:00 UTC tick is 6 Oct 19:00, the Jan-1 Year
 * tick is still 2025), so a Day/Month/Year tick that is not local midnight prints the local date and
 * time (`6 19:00`, `Sep 30 19:00`, `2025-12-31 19:00`); at local midnight it keeps the plain label.
 */
export function tickMarkFormatter(zone: TimeZoneSetting): (time: unknown, tickMarkType: number) => string | null {
  return (time, tickMarkType) => {
    if (typeof time !== "number") return null; // a business-day time: the library's own label
    const p = partsOf(time, zone);
    if (p === null) return null;
    if (tickMarkType > TICK_DAY) return clockText(p, tickMarkType === TICK_TIME_WITH_SECONDS);
    // UTC ticks sit on UTC midnights by construction; only a local label can name a different instant.
    const midnight = p.hours === 0 && p.minutes === 0 && p.seconds === 0;
    return usesUtc(zone) || midnight ? calendarLabel(p, tickMarkType) : offMidnightLabel(p, tickMarkType);
  };
}

/**
 * The session breaks: the time of the first bar of each UTC day, the oldest loaded bar excluded (it
 * starts the data, not a day). Always UTC, whatever the display zone: the venues' day (funding,
 * daily candles) is the UTC day. None at a bar of a day or longer, where every bar is its own day.
 */
export function sessionBreakTimes(times: readonly number[], barSeconds: number): number[] {
  if (barSeconds >= DAY_SECONDS) return [];
  const breaks: number[] = [];
  for (let i = 1; i < times.length; i++) {
    if (Math.floor(times[i] / DAY_SECONDS) !== Math.floor(times[i - 1] / DAY_SECONDS)) breaks.push(times[i]);
  }
  return breaks;
}

/**
 * Seconds until the last bar's close, by the viewer's clock (audit D-219). The close is the first
 * whole-bar boundary strictly after both `lastT` and `nowSec`: `lastT + barSeconds * (floor((now -
 * lastT) / barSeconds) + 1)`, at least one bar after `lastT`. So when the forming bar's own close has
 * passed and no new bar has arrived yet, it counts to the close of the bar that must be forming, and
 * `now` exactly on a boundary already counts the next bar. Works for any bucket alignment (weekly).
 */
export function barCountdown(lastT: number, barSeconds: number, nowSec: number): number {
  const bars = Math.max(1, Math.floor((nowSec - lastT) / barSeconds) + 1);
  // A viewer's clock behind the bar's open (`now < lastT`, clock skew) would count more than one bar;
  // no bar lasts longer than its size, so the label never exceeds it.
  return Math.min(barSeconds, lastT + barSeconds * bars - nowSec);
}

/** A countdown as `mm:ss`, `h:mm:ss` from an hour, `Nd hh:mm` from a day (whole seconds, rounded up). */
export function formatCountdown(seconds: number): string {
  const total = Math.max(0, Math.ceil(seconds));
  const days = Math.floor(total / DAY_SECONDS);
  const hours = Math.floor((total % DAY_SECONDS) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (days > 0) return `${days}d ${pad(hours)}:${pad(minutes)}`;
  if (hours > 0) return `${hours}:${pad(minutes)}:${pad(secs)}`;
  return `${pad(minutes)}:${pad(secs)}`;
}
