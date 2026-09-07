import { describe, expect, it } from "vitest";

import {
  DESKTOP_PREFERENCES_CONTRACT_VERSION,
  MAX_RECENT_PROJECTS,
  defaultDesktopPreferences,
  parseDesktopPreferences,
  withOpenedProject,
  withoutRecentProject,
} from "./contracts";

describe("desktop preferences contract", () => {
  it("keeps a bounded deduplicated MRU list without rewriting path strings", () => {
    let preferences = defaultDesktopPreferences();
    const roots = Array.from({ length: MAX_RECENT_PROJECTS + 2 }, (_, index) =>
      index === 0 ? String.raw`C:\Research Work\project-${index}` : `/work/project-${index}`,
    );

    for (const root of roots) {
      preferences = withOpenedProject(preferences, root);
    }
    preferences = withOpenedProject(preferences, roots[0]);

    expect(preferences.current_project_root).toBe(roots[0]);
    expect(preferences.recent_project_roots[0]).toBe(roots[0]);
    expect(preferences.recent_project_roots).toHaveLength(MAX_RECENT_PROJECTS);
    expect(new Set(preferences.recent_project_roots).size).toBe(MAX_RECENT_PROJECTS);
  });

  it("fails closed on unknown fields, duplicate recent paths, and version drift", () => {
    expect(() =>
      parseDesktopPreferences({
        contract_version: DESKTOP_PREFERENCES_CONTRACT_VERSION,
        current_project_root: null,
        recent_project_roots: [],
        scientific_hash: "must-not-exist",
      }),
    ).toThrow(/unknown field/);

    expect(() =>
      parseDesktopPreferences({
        contract_version: DESKTOP_PREFERENCES_CONTRACT_VERSION,
        current_project_root: "/project",
        recent_project_roots: ["/project", "/project"],
      }),
    ).toThrow(/MRU constraints/);

    expect(() =>
      parseDesktopPreferences({
        contract_version: "ecatvasp-desktop-preferences-v2",
        current_project_root: null,
        recent_project_roots: [],
      }),
    ).toThrow(/unsupported/);
  });

  it("forgetting a recent project clears current only when identities match exactly", () => {
    const preferences = {
      contract_version: DESKTOP_PREFERENCES_CONTRACT_VERSION,
      current_project_root: "/project-a",
      recent_project_roots: ["/project-a", "/project-b"],
    } as const;

    const retained = withoutRecentProject(
      { ...preferences, recent_project_roots: [...preferences.recent_project_roots] },
      "/project-b",
    );
    expect(retained.current_project_root).toBe("/project-a");
    expect(retained.recent_project_roots).toEqual(["/project-a"]);

    const cleared = withoutRecentProject(
      { ...preferences, recent_project_roots: [...preferences.recent_project_roots] },
      "/project-a",
    );
    expect(cleared.current_project_root).toBeNull();
  });
});
