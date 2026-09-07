import { describe, expect, it } from "vitest";

import { LatestWorkspaceLoad } from "./load_guard";

describe("LatestWorkspaceLoad", () => {
  it("invalidates older workspace completions when a newer load starts", () => {
    const loads = new LatestWorkspaceLoad();

    const projectA = loads.begin();
    const projectB = loads.begin();

    expect(loads.isCurrent(projectA)).toBe(false);
    expect(loads.isCurrent(projectB)).toBe(true);
  });

  it("invalidates an in-flight load when the workspace is closed or disposed", () => {
    const loads = new LatestWorkspaceLoad();
    const projectA = loads.begin();

    loads.invalidate();

    expect(loads.isCurrent(projectA)).toBe(false);
  });
});
