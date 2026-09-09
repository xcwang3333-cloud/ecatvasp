import { describe, expect, it } from "vitest";

import {
  DEFAULT_VISIBLE_ROWS,
  hiddenRowCount,
  nextVisibleLimit,
  visibleRows,
} from "./visible_rows";

describe("visible row windowing", () => {
  it("keeps canonical rows complete while bounding rendered rows", () => {
    const rows = Array.from({ length: 125 }, (_, index) => index + 1);

    expect(visibleRows(rows, DEFAULT_VISIBLE_ROWS)).toEqual(rows.slice(0, 50));
    expect(rows).toHaveLength(125);
    expect(hiddenRowCount(rows.length, DEFAULT_VISIBLE_ROWS)).toBe(75);
    expect(nextVisibleLimit(DEFAULT_VISIBLE_ROWS, rows.length)).toBe(100);
    expect(nextVisibleLimit(100, rows.length)).toBe(125);
  });

  it("fails closed to deterministic defaults for invalid limits", () => {
    const rows = Array.from({ length: 70 }, (_, index) => index);

    expect(visibleRows(rows, 0)).toHaveLength(DEFAULT_VISIBLE_ROWS);
    expect(hiddenRowCount(rows.length, Number.NaN)).toBe(20);
    expect(nextVisibleLimit(0, rows.length)).toBe(70);
  });
});
