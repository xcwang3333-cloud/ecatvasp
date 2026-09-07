import { describe, expect, it } from "vitest";

import {
  DESKTOP_IPC_V1_CONTRACT_VERSION,
  DESKTOP_IPC_V2_CONTRACT_VERSION,
  DESKTOP_V2_OPERATIONS,
  assertDesktopV2HealthCompatibility,
  assertProjectDashboardPayload,
  parseDesktopV2Response,
  requireDesktopV2Success,
  type DesktopProjectDashboardPayload,
  type DesktopV2HealthPayload,
} from "./contracts-v2";

describe("desktop IPC v2 contracts", () => {
  it("accepts the explicit v1/v2 compatibility catalog", () => {
    const raw = JSON.stringify({
      protocol_version: DESKTOP_IPC_V2_CONTRACT_VERSION,
      request_id: "health-v2",
      operation: "health",
      ok: true,
      payload: {
        backend_version: "1.1.0.dev0",
        frontend_handoff_contract_version: "ecatvasp-frontend-handoff-v1",
        operations: DESKTOP_V2_OPERATIONS,
        stateless_project_requests: true,
        supported_protocol_versions: [
          DESKTOP_IPC_V1_CONTRACT_VERSION,
          DESKTOP_IPC_V2_CONTRACT_VERSION,
        ],
        workflow_recipes: [{ recipe_id: "recipe", version: "1", description: null }],
      },
    });

    const response = requireDesktopV2Success(
      parseDesktopV2Response<DesktopV2HealthPayload>(raw, "health", "health-v2"),
    );
    expect(() => assertDesktopV2HealthCompatibility(response)).not.toThrow();
  });

  it("rejects v1 responses when parsing the v2 contract", () => {
    const raw = JSON.stringify({
      protocol_version: DESKTOP_IPC_V1_CONTRACT_VERSION,
      request_id: "health-v1",
      operation: "health",
      ok: true,
      payload: {},
    });
    expect(() => parseDesktopV2Response(raw, "health")).toThrow(
      "unsupported desktop IPC v2 contract version",
    );
  });

  it("validates the page-scoped project dashboard without presentation payloads", () => {
    const payload: DesktopProjectDashboardPayload = {
      project_root: "C:/work/project",
      dashboard: {
        project_id: "project-id",
        project_name: "Project",
        project_slug: "project",
        schema_version: 3,
        model_counts: [["structure_snapshots", 4]],
        workflow_counts: [["workflow_plans", 1]],
        calculations: [["ready", 2]],
        analyses: [["completed", 1]],
        execution_attempts: [["running", 1]],
        scheduler_jobs: [["running", 1]],
        freshness: [["fresh", 9]],
        attention_rows: 0,
      },
    };

    expect(() => assertProjectDashboardPayload(payload, "C:/work/project")).not.toThrow();
    expect("handoff" in payload.dashboard).toBe(false);
    expect("presentations" in payload.dashboard).toBe(false);
  });
});
