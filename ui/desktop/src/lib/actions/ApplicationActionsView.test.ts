import { render } from "svelte/server";
import { describe, expect, it } from "vitest";

import type { DesktopBackendClientV2 } from "../backend/client-v2";
import type { ScientificWorkspace } from "../workspace/contracts";
import ApplicationActionsView from "./ApplicationActionsView.svelte";

describe("ApplicationActionsView lazy scientific workspaces", () => {
  it("does not render heavy scientific workspace contents before selection", () => {
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

    expect(body).toContain("Scientific workspaces");
    expect(body).toContain("Select a scientific workspace to load its current ProjectStore-backed catalog.");
    expect(body).not.toContain("Thermochemistry &amp; Reaction Workspace");
    expect(body).not.toContain("Electronic Analysis Workspace");
    expect(body).not.toContain("Result Center");
  });
});
