import { render } from "svelte/server";
import { describe, expect, it } from "vitest";

import type { DesktopBackendClientV2 } from "../backend/client-v2";
import type { ScientificWorkspace } from "../workspace/contracts";
import ApplicationActionsView from "./ApplicationActionsView.svelte";

describe("ApplicationActionsView lazy scientific workspaces", () => {
  it("shows stable analysis navigation without mounting a heavy workspace before selection", () => {
    const workspace = {
      inventory: { rows: [] },
    } as unknown as ScientificWorkspace;

    const { body } = render(ApplicationActionsView, {
      props: {
        client: {} as DesktopBackendClientV2,
        projectRoot: "C:/work/project",
        projectId: "project-id",
        workflowRecipes: [],
        workspace,
        disabled: false,
        onMutation: async () => undefined,
      },
    });

    expect(body).toContain("Results &amp; analysis");
    expect(body).toContain("Scientific workspaces");
    expect(body).toContain("Choose an analysis workspace");
    expect(body).not.toContain("Thermochemistry &amp; Reaction Workspace");
    expect(body).not.toContain("Electronic Analysis Workspace");
    expect(body).not.toContain("Scientific Result Center");
  });
});
