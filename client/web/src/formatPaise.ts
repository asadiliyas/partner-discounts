/**
 * Format integer paise as rupees: en-IN digit grouping, exactly two decimals,
 * integer maths only (no floating-point division of the amount).
 *
 *   formatPaise(199999)   === "₹1,999.99"
 *   formatPaise(12345678) === "₹1,23,456.78"
 *   formatPaise(5)        === "₹0.05"
 *   formatPaise(0)        === "₹0.00"
 *
 * Throws an Error if `paise` is not an integer.
 */
export function formatPaise(paise: number): string {
  // Safe integers only: above 2^53 a number may not be the paise it claims to be.
  if (typeof paise !== "number" || !Number.isSafeInteger(paise)) {
    throw new Error(`formatPaise expects an integer number of paise, got ${String(paise)}`);
  }

  const value = BigInt(paise);
  const negative = value < 0n;
  const abs = negative ? -value : value;
  const rupees = (abs / 100n).toString();
  const fraction = (abs % 100n).toString().padStart(2, "0");

  return `${negative ? "-" : ""}₹${groupIndian(rupees)}.${fraction}`;
}

/** "12345678" -> "1,23,45,678": the last three digits, then groups of two. */
function groupIndian(digits: string): string {
  if (digits.length <= 3) return digits;
  const lastThree = digits.slice(-3);
  const rest = digits.slice(0, -3).replace(/\B(?=(\d{2})+$)/g, ",");
  return `${rest},${lastThree}`;
}
