export const DEFAULT_VISIBLE_ROWS = 50;
export const VISIBLE_ROWS_STEP = 50;

export function visibleRows<T>(rows: readonly T[], limit: number): readonly T[] {
  const safeLimit = Number.isSafeInteger(limit) && limit > 0 ? limit : DEFAULT_VISIBLE_ROWS;
  return rows.slice(0, safeLimit);
}

export function hiddenRowCount(total: number, limit: number): number {
  if (!Number.isSafeInteger(total) || total <= 0) return 0;
  const safeLimit = Number.isSafeInteger(limit) && limit > 0 ? limit : DEFAULT_VISIBLE_ROWS;
  return Math.max(0, total - safeLimit);
}

export function nextVisibleLimit(current: number, total: number): number {
  const safeCurrent = Number.isSafeInteger(current) && current > 0 ? current : DEFAULT_VISIBLE_ROWS;
  const safeTotal = Number.isSafeInteger(total) && total > 0 ? total : 0;
  return Math.min(safeTotal, safeCurrent + VISIBLE_ROWS_STEP);
}
