import type { VolumeColorMode } from "./chartLayout";

/**
 * Story 33.6: the colour of one Volume bar, by the layout's `volume_color_by`. A plotting
 * conversion only (the one place the browser does arithmetic on the stored order flow): every
 * indicator formula over these columns is the server's (`views.indicator_picker`, SSOT-01).
 *
 * - `direction`: `up` when the bar closed at or above its open, else `down`.
 * - `delta`: the sign of `buy_v - sell_v` picks `up` or `down`, shaded by how one-sided the bar
 *   was, `|buy_v - sell_v| / (buy_v + sell_v)`, never below `MIN_DELTA_ALPHA` so a balanced bar
 *   stays visible. An exactly balanced bar is `neutral` at that same floor: the least one-sided
 *   bar is the lightest, never the most prominent. A bar whose flow is unknown (null: before
 *   Story 33.3's migration) or that traded nothing is `neutral` at full opacity -- never a guessed
 *   side (DATA-01).
 *
 * Known limit: `buy_v`/`sell_v` arrive as JSON numbers in integer units, and `JSON.parse` turns an
 * integer past 2^53 into the nearest double (`docs/DATA_DICTIONARY.md` §2.15's JSON-range limit,
 * audit D-191), so only a bar whose volumes exceed 2^53 units can have its sign or shade misjudged
 * when the two sides differ by less than that rounding. Upgrade path: §2.15's string encoding of
 * the integer columns (an added key, AD-D12), compared as BigInt here.
 */

/** The lightest a `delta` bar is ever shaded, so a nearly balanced bar never vanishes. */
export const MIN_DELTA_ALPHA = 0.25;

/** What one bar contributes to its colour: the candle's open/close and the stored flow. */
export interface VolumeBarFlow {
  o?: number | null;
  c?: number | null;
  buy_v?: number | null;
  sell_v?: number | null;
}

const HEX6 = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i;
const HEX3 = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/i;
const RGB = /^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)$/i;

/**
 * `color` at `alpha` as `rgba(...)`, `alpha` multiplying the colour's own (an `rgba()` token at 0.5
 * shaded at 0.4 is 0.2). The chart tokens are `#rrggbb` (theme.css, `CHART_TOKENS`); `#rgb` and
 * integer `rgb()`/`rgba()` are read too. Known limit: any other CSS colour form (a named
 * colour, `hsl()`, a percentage or fractional `rgb()` channel) is returned unshaded, at its own opacity. Upgrade path: resolve it through a
 * canvas `fillStyle` round trip.
 */
export function withAlpha(color: string, alpha: number): string {
  const text = color.trim();
  const hex6 = HEX6.exec(text);
  const hex3 = HEX3.exec(text);
  const rgb = RGB.exec(text);
  let channels: number[] | null = null;
  let own = 1;
  if (hex6) channels = hex6.slice(1).map((h) => parseInt(h, 16));
  else if (hex3) channels = hex3.slice(1).map((h) => parseInt(h + h, 16));
  else if (rgb) {
    channels = rgb.slice(1, 4).map(Number);
    if (rgb[4] !== undefined) own = Math.min(1, Number(rgb[4]));
  }
  if (channels === null) return color;
  return `rgba(${channels.join(", ")}, ${Number((alpha * own).toFixed(3))})`;
}

function deltaColor(bar: VolumeBarFlow, up: string, down: string, neutral: string): string {
  const { buy_v: buy, sell_v: sell } = bar;
  if (typeof buy !== "number" || typeof sell !== "number" || !Number.isFinite(buy) || !Number.isFinite(sell)) {
    return neutral;
  }
  const total = buy + sell;
  const delta = buy - sell;
  if (!(total > 0)) return neutral;
  if (delta === 0) return withAlpha(neutral, MIN_DELTA_ALPHA);
  const alpha = Math.max(MIN_DELTA_ALPHA, Math.min(1, Math.abs(delta) / total));
  return withAlpha(delta > 0 ? up : down, alpha);
}

/** The bar's colour under `mode` (see the module comment). */
export function volumeBarColor(
  bar: VolumeBarFlow,
  mode: VolumeColorMode,
  up: string,
  down: string,
  neutral: string,
): string {
  if (mode === "delta") return deltaColor(bar, up, down, neutral);
  const { o, c } = bar;
  if (typeof o !== "number" || typeof c !== "number" || !Number.isFinite(o) || !Number.isFinite(c)) return neutral;
  return c >= o ? up : down;
}
