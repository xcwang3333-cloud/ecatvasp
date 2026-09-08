import { describe, expect, it } from "vitest";

import { LatestProjectRequestGuard } from "./latestProjectRequest";

describe("LatestProjectRequestGuard", () => {
  it("rejects an older A response after A -> B -> A navigation", () => {
    const guard = new LatestProjectRequestGuard();

    const firstA = guard.begin("/project/A");
    const b = guard.begin("/project/B");
    const secondA = guard.begin("/project/A");

    expect(guard.isCurrent(firstA, "/project/A")).toBe(false);
    expect(guard.isCurrent(b, "/project/A")).toBe(false);
    expect(guard.isCurrent(secondA, "/project/A")).toBe(true);
  });

  it("lets a newer refresh supersede an older request for the same project", () => {
    const guard = new LatestProjectRequestGuard();

    const first = guard.begin("/project/A");
    const refresh = guard.begin("/project/A");

    expect(guard.isCurrent(first, "/project/A")).toBe(false);
    expect(guard.isCurrent(refresh, "/project/A")).toBe(true);
  });
});
