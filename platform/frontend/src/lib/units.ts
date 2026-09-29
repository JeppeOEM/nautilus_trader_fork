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
