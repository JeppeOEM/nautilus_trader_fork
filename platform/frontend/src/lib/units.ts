/**
 * The one place the web frontend turns the platform's exact integer units into decimals
 * (Story 30.2). A price or size travels from the collector to this page as an integer count
 * of `10^-precision` (`kernel/second_snapshot.py`'s layout, `docs/DATA_DICTIONARY.md` §1),
 * never as a float; only a human-facing value is converted, and only here.
 *
 * Exact by construction: `formatUnits` moves the decimal point in the integer's own digit
 * string (BigInt, no float arithmetic), so `858919` at precision 1 is `"85891.9"`, never
 * `85891.90000000001`. `unitsToNumber` parses that exact string, which gives the nearest
 * double to the decimal -- the same number the kernel's `unit_float` decodes. An integer
 * JavaScript cannot hold exactly (beyond `Number.MAX_SAFE_INTEGER`, e.g. a JSON value that
 * already lost digits in `JSON.parse`) throws instead of being rounded silently.
 */

/** The largest precision a unit may carry: `FIXED_PRECISION` of the Nautilus build (16). */
export const MAX_PRECISION = 16;

export type Units = number | bigint | string;

function checkPrecision(precision: number): void {
  if (!Number.isInteger(precision) || precision < 0 || precision > MAX_PRECISION) {
    throw new RangeError(`precision ${precision} is not an integer in 0..${MAX_PRECISION}`);
  }
}

function toBigInt(units: Units): bigint {
  if (typeof units === "bigint") return units;
  if (typeof units === "number") {
    if (!Number.isSafeInteger(units)) {
      throw new RangeError(`${units} is not a safe integer: its exact units are unknown`);
    }
    return BigInt(units);
  }
  if (!/^-?\d+$/.test(units)) throw new RangeError(`"${units}" is not an integer`);
  return BigInt(units);
}

/** Integer units at `precision` as their exact decimal string ("-0.05", "100", "1.000"). */
export function formatUnits(units: Units, precision: number): string {
  checkPrecision(precision);
  const value = toBigInt(units);
  const negative = value < 0n;
  const digits = (negative ? -value : value).toString().padStart(precision + 1, "0");
  const whole = digits.slice(0, digits.length - precision);
  const fraction = digits.slice(digits.length - precision);
  const text = precision === 0 ? whole : `${whole}.${fraction}`;
  return negative ? `-${text}` : text;
}

/**
 * A float computed in the browser (a Fibonacci level, a position's target, a percentage) as its
 * decimal string at `precision`, through the same integer path as every stored price: the value
 * is rounded once to whole `10^-precision` units, then `formatUnits` moves the point. Never
 * `toFixed` (binary-float artefacts such as `1.005 -> "1.00"`) and never a longer digit string,
 * so a label carries exactly the instrument's decimals and no float noise (`92.36000000000001`).
 * A value too large for its units to be a safe integer throws (a RangeError), never a rounded lie.
 */
export function formatDecimal(value: number, precision: number): string {
  checkPrecision(precision);
  if (!Number.isFinite(value)) throw new RangeError(`${value} is not a finite number`);
  return formatUnits(roundUnits(value, precision), precision);
}

/** `value` rounded to the nearest `10^-precision` (the instrument's price/size grid), as a number. */
export function roundToPrecision(value: number, precision: number): number {
  return unitsToNumber(roundUnits(value, precision), precision);
}

/**
 * `value` in whole `10^-precision` units, half away from zero. The scaled product is first cut to
 * 15 significant digits (all a double carries exactly), so a decimal half-tick that binary floats
 * land just below (`1.005 * 100 = 100.49999999999999`) still rounds up as written (`101`), and a
 * negative half mirrors a positive one (`-2.5 -> -3`, not `Math.round`'s `-2`).
 */
function roundUnits(value: number, precision: number): number {
  const scaled = Number((Math.abs(value) * 10 ** precision).toPrecision(15));
  const units = Math.round(scaled);
  return value < 0 && units !== 0 ? -units : units;
}

/** Integer units as a number for display or plotting, through the exact string. */
export function unitsToNumber(units: Units, precision: number): number {
  return Number(formatUnits(units, precision));
}

/**
 * The stored book layout -> absolute level prices in units, best first: element 0 is the best
 * price, each later one the strictly positive gap to the level above (bids step down, asks
 * up). A non-positive or non-integer gap, or a level beyond safe integers, throws.
 */
export function decodeBookPrices(encoded: readonly number[], side: "bid" | "ask"): number[] {
  const out: number[] = [];
  for (const [i, value] of encoded.entries()) {
    if (!Number.isSafeInteger(value)) throw new RangeError(`${side} element ${i} (${value}) is not a safe integer`);
    if (i === 0) {
      out.push(value);
      continue;
    }
    if (value <= 0) throw new RangeError(`${side} gap ${i} is ${value}: gaps must be positive`);
    const level = side === "bid" ? out[i - 1] - value : out[i - 1] + value;
    if (!Number.isSafeInteger(level)) throw new RangeError(`${side} level ${i} leaves the safe integers`);
    out.push(level);
  }
  return out;
}

// Story 33.5: exact decimal *text* (a funding rate, an open interest, a mark or index price, as
// `views/derivatives.py` and the live `derivs:` frames send them: `kernel.derivs_wire.exact_text`,
// plain positional digits) printed without ever becoming a float. Parsed into a BigInt and a scale,
// rounded (half away from zero) or padded only when asked, and written back digit by digit.

const DECIMAL_TEXT = /^(-?)(\d+)(?:\.(\d+))?$/;

interface DecimalParts {
  negative: boolean;
  /** The digits without the point, as one integer. */
  units: bigint;
  /** How many of them are after the point. */
  scale: number;
}

function parseDecimalText(text: string): DecimalParts {
  const match = DECIMAL_TEXT.exec(text);
  if (match === null) throw new RangeError(`"${text}" is not decimal text`);
  const [, sign, whole, fraction = ""] = match;
  return { negative: sign === "-", units: BigInt(whole + fraction), scale: fraction.length };
}

function writeDecimal({ negative, units, scale }: DecimalParts): string {
  const digits = units.toString().padStart(scale + 1, "0");
  const whole = digits.slice(0, digits.length - scale);
  const text = scale === 0 ? whole : `${whole}.${digits.slice(digits.length - scale)}`;
  return negative && units !== 0n ? `-${text}` : text;
}

/**
 * Exact decimal text, canonical (no leading zeros, a "-0" read as "0"), at its own decimals or, with
 * `places`, padded or rounded half away from zero to exactly that many: `("0.012345", 4)` is
 * `"0.0123"`, `("90", 2)` is `"90.00"`. Anything that is not plain decimal text (a float's `1e-7`,
 * an empty string, `NaN`) throws a `RangeError`, never a guess.
 */
export function formatDecimalText(text: string, places?: number): string {
  const parts = parseDecimalText(text);
  if (places === undefined || places === parts.scale) return writeDecimal(parts);
  checkPrecision(places);
  if (places > parts.scale) {
    return writeDecimal({ ...parts, units: parts.units * 10n ** BigInt(places - parts.scale), scale: places });
  }
  const divisor = 10n ** BigInt(parts.scale - places);
  const quotient = parts.units / divisor;
  const rounded = (parts.units % divisor) * 2n >= divisor ? quotient + 1n : quotient;
  return writeDecimal({ ...parts, units: rounded, scale: places });
}

/** Exact decimal text times `10^digits` (`digits` may be negative): a rate as a percent is
 * `shiftDecimalText(rate, 2)`, moved in the digits, never multiplied as a float. */
export function shiftDecimalText(text: string, digits: number): string {
  if (!Number.isInteger(digits)) throw new RangeError(`${digits} is not an integer shift`);
  const parts = parseDecimalText(text);
  if (digits <= parts.scale) return writeDecimal({ ...parts, scale: parts.scale - digits });
  return writeDecimal({ ...parts, units: parts.units * 10n ** BigInt(digits - parts.scale), scale: 0 });
}

/** A float ratio the server computed (an annualised funding rate) as a percent at `places`, through
 * the exact text path: the value is cut to `places + 2` decimals once, then the point moves. */
export function formatPercent(value: number, places: number): string {
  return formatDecimalText(shiftDecimalText(formatDecimal(value, places + 2), 2), places);
}

/** Milliseconds as `HH:MM:SS` (hours unbounded), whole seconds, clamped at 0: a countdown that has
 * run out reads `00:00:00`, never a negative time. */
export function formatCountdown(ms: number): string {
  const total = Number.isFinite(ms) ? Math.max(0, Math.floor(ms / 1000)) : 0;
  const pad = (n: number): string => String(n).padStart(2, "0");
  return `${pad(Math.floor(total / 3600))}:${pad(Math.floor((total % 3600) / 60))}:${pad(total % 60)}`;
}
