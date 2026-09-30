import { describe, expect, it } from "vitest";
import { formatPaise } from "./formatPaise";

describe("formatPaise", () => {
  it.each([
    [199999, "₹1,999.99"],
    [12345678, "₹1,23,456.78"],
    [5, "₹0.05"],
    [0, "₹0.00"],
    [99, "₹0.99"],
    [100, "₹1.00"],
    [99999, "₹999.99"],
    [100000, "₹1,000.00"],
    [10000000, "₹1,00,000.00"],
    [1234567890, "₹1,23,45,678.90"],
    [Number.MAX_SAFE_INTEGER, "₹9,00,71,99,25,47,409.91"],
  ])("formats %i paise as %s", (paise, expected) => {
    expect(formatPaise(paise)).toBe(expected);
  });

  it("keeps the sign on negative amounts (refunds)", () => {
    expect(formatPaise(-5)).toBe("-₹0.05");
    expect(formatPaise(-12345678)).toBe("-₹1,23,456.78");
    expect(formatPaise(-0)).toBe("₹0.00");
  });

  it.each([1.5, 0.1, NaN, Infinity, -Infinity, 2 ** 53, -(2 ** 53)])("throws on %s", (bad) => {
    expect(() => formatPaise(bad)).toThrow(Error);
  });

  it("throws on non-numbers that slip past the type checker", () => {
    expect(() => formatPaise("100" as unknown as number)).toThrow(Error);
    expect(() => formatPaise(null as unknown as number)).toThrow(Error);
  });

  it("agrees with Intl en-IN formatting across a spread of amounts", () => {
    // Intl divides by 100 in floating point, which is exact enough to serve
    // as an independent oracle at these sizes; formatPaise itself never does.
    const intl = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" });
    let seed = 42;
    const next = () => (seed = (seed * 1103515245 + 12345) % 2 ** 31);
    const samples = [1, 10, 999, 1000, 100001, 999999999];
    for (let i = 0; i < 500; i++) samples.push(next() * (i % 7) + (i % 101));

    for (const paise of samples) {
      expect(formatPaise(paise)).toBe(intl.format(paise / 100));
    }
  });
});
