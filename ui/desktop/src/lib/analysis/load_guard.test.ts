import { describe, expect, it } from "vitest";

import { LatestElectronicAnalysisLoad } from "./load_guard";

describe("Electronic Analysis load guard", () => {
  it("rejects stale A responses after A -> B -> A switching", () => {
    const guard = new LatestElectronicAnalysisLoad();
    const firstA = guard.begin("A");
    const b = guard.begin("B");
    const secondA = guard.begin("A");

    expect(guard.isCurrent(firstA, "A")).toBe(false);
    expect(guard.isCurrent(b, "A")).toBe(false);
    expect(guard.isCurrent(secondA, "A")).toBe(true);

    guard.invalidate();
    expect(guard.isCurrent(secondA, "A")).toBe(false);
  });
});
