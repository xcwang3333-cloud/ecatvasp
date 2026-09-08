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

  it("keeps a mutation lifecycle independent from nested read generations", () => {
    const reads = new LatestElectronicAnalysisLoad();
    const mutations = new LatestElectronicAnalysisLoad();
    const mutation = mutations.begin("A");

    reads.begin("A");
    reads.begin("A");
    expect(mutations.isCurrent(mutation, "A")).toBe(true);

    mutations.invalidate();
    expect(mutations.isCurrent(mutation, "A")).toBe(false);
  });
});
