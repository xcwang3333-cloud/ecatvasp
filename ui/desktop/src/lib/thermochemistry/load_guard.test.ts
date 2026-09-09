import { describe, expect, it } from "vitest";

import { LatestThermochemistryLoad } from "./load_guard";

describe("LatestThermochemistryLoad", () => {
  it("rejects stale A to B to A responses", () => {
    const guard = new LatestThermochemistryLoad();
    const firstA = guard.begin("/project-a");
    const projectB = guard.begin("/project-b");
    const secondA = guard.begin("/project-a");

    expect(guard.isCurrent(firstA, "/project-a")).toBe(false);
    expect(guard.isCurrent(projectB, "/project-a")).toBe(false);
    expect(guard.isCurrent(secondA, "/project-a")).toBe(true);
  });

  it("rejects an older same-project refresh", () => {
    const guard = new LatestThermochemistryLoad();
    const oldRefresh = guard.begin("/project");
    const newRefresh = guard.begin("/project");

    expect(guard.isCurrent(oldRefresh, "/project")).toBe(false);
    expect(guard.isCurrent(newRefresh, "/project")).toBe(true);
  });

  it("invalidates outstanding mutation receipts", () => {
    const guard = new LatestThermochemistryLoad();
    const mutation = guard.begin("/project");
    guard.invalidate();

    expect(guard.isCurrent(mutation, "/project")).toBe(false);
  });
});
