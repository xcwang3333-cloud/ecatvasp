import { describe, expect, it } from "vitest";

import { isPositiveSafeInteger } from "./input_validation";

describe("thermochemistry scientific integer inputs", () => {
  it("accepts only positive safe integers", () => {
    expect(isPositiveSafeInteger(1)).toBe(true);
    expect(isPositiveSafeInteger(3)).toBe(true);

    for (const value of [0, -1, 1.8, Number.NaN, Number.POSITIVE_INFINITY, Number.MAX_SAFE_INTEGER + 1]) {
      expect(isPositiveSafeInteger(value)).toBe(false);
    }
  });
});
