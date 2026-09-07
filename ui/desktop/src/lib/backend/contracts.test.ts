import { describe, expect, it } from "vitest";

import healthFixture from "./fixtures/health-response.json";
import { DesktopBackendClient, type InvokeFn } from "./client";
import {
  DESKTOP_IPC_CONTRACT_VERSION,
  DesktopContractError,
  type HealthPayload,
  assertHealthCompatibility,
  parseDesktopResponse,
  requireSuccess,
} from "./contracts";

function fixtureJson(overrides: Record<string, unknown> = {}): string {
  return JSON.stringify({ ...healthFixture, ...overrides });
}

describe("desktop contract compatibility", () => {
  it("accepts the Python-owned health fixture and workflow recipe catalog", () => {
    const response = requireSuccess(
      parseDesktopResponse<HealthPayload>(fixtureJson(), "health"),
    );

    expect(() => assertHealthCompatibility(response)).not.toThrow();
    expect(response.payload.stateless_project_requests).toBe(true);
    expect(response.payload.workflow_recipes.map((recipe) => recipe.recipe_id)).toEqual([
      "ECatVASP.Workflow.SlabScientificPreparation",
      "ECatVASP.Workflow.AdsorbateScientificPreparation",
      "ECatVASP.Workflow.GasReferencePreparation",
    ]);
  });

  it("rejects an unknown desktop IPC major contract", () => {
    expect(() =>
      parseDesktopResponse<HealthPayload>(
        fixtureJson({ protocol_version: "ecatvasp-desktop-ipc-v2" }),
        "health",
      ),
    ).toThrowError(DesktopContractError);
  });

  it("rejects an incompatible frontend handoff contract", () => {
    const raw = fixtureJson({
      payload: {
        ...healthFixture.payload,
        frontend_handoff_contract_version: "ecatvasp-frontend-handoff-v2",
      },
    });
    const response = requireSuccess(parseDesktopResponse<HealthPayload>(raw, "health"));

    expect(() => assertHealthCompatibility(response)).toThrowError(
      "unsupported frontend handoff contract version",
    );
  });

  it("rejects malformed health fields with a contract error", () => {
    const raw = fixtureJson({
      payload: {
        ...healthFixture.payload,
        backend_version: 100,
      },
    });
    const response = requireSuccess(parseDesktopResponse<HealthPayload>(raw, "health"));

    expect(() => assertHealthCompatibility(response)).toThrowError(
      new DesktopContractError("desktop backend version is invalid"),
    );
  });

  it("rejects missing typed workflow recipe metadata", () => {
    const raw = fixtureJson({
      payload: {
        ...healthFixture.payload,
        workflow_recipes: [],
      },
    });
    const response = requireSuccess(parseDesktopResponse<HealthPayload>(raw, "health"));

    expect(() => assertHealthCompatibility(response)).toThrowError(
      "desktop backend workflow recipes are invalid",
    );
  });

  it("requires health before any project-scoped request", async () => {
    const invokeFn: InvokeFn = async () => {
      throw new Error("invoke must not be reached before health");
    };
    const client = new DesktopBackendClient(invokeFn);

    await expect(client.status("/project-a")).rejects.toThrow(
      "desktop backend health handshake is required",
    );
    await expect(client.applicationReport("/project-a", "json")).rejects.toThrow(
      "desktop backend health handshake is required",
    );
  });

  it("correlates typed project requests with exact request ids", async () => {
    const calls: Array<{ command: string; args?: Record<string, unknown> }> = [];
    const invokeFn: InvokeFn = async <T>(
      command: string,
      args?: Record<string, unknown>,
    ): Promise<T> => {
      calls.push({ command, args });
      if (command === "backend_health") {
        return fixtureJson() as T;
      }
      if (command === "backend_exchange") {
        const request = JSON.parse(String(args?.requestJson)) as {
          request_id: string;
          operation: string;
          project_root: string;
        };
        return JSON.stringify({
          protocol_version: DESKTOP_IPC_CONTRACT_VERSION,
          request_id: request.request_id,
          operation: request.operation,
          ok: true,
          payload: {
            project_root: request.project_root,
            project_id: "project-id",
            project_name: "Project A",
            project_slug: "project-a",
            schema_version: 3,
          },
        }) as T;
      }
      throw new Error(`unexpected command: ${command}`);
    };
    const client = new DesktopBackendClient(invokeFn);

    await client.connect();
    const response = await client.openProject("/project-a");

    expect(response.payload.schema_version).toBe(3);
    expect(calls.map((call) => call.command)).toEqual([
      "backend_health",
      "backend_exchange",
    ]);
    const request = JSON.parse(String(calls[1].args?.requestJson)) as {
      request_id: string;
      project_root: string;
    };
    expect(request.request_id).toBe("desktop-1");
    expect(request.project_root).toBe("/project-a");
  });

  it("sends only exact fields for typed report and workflow actions", async () => {
    const calls: Record<string, unknown>[] = [];
    const rootSnapshot = "018f0e9e-7c3f-7a11-8b22-123456789abc";
    const invokeFn: InvokeFn = async <T>(
      command: string,
      args?: Record<string, unknown>,
    ): Promise<T> => {
      if (command === "backend_health") return fixtureJson() as T;
      if (command !== "backend_exchange") throw new Error(`unexpected command: ${command}`);
      const request = JSON.parse(String(args?.requestJson)) as Record<string, unknown>;
      calls.push(request);
      if (request.operation === "application_report") {
        return JSON.stringify({
          protocol_version: DESKTOP_IPC_CONTRACT_VERSION,
          request_id: request.request_id,
          operation: request.operation,
          ok: true,
          payload: {
            project_root: request.project_root,
            project_id: "project-id",
            report_format: request.report_format,
            report_contract_version: "ecatvasp-scientific-report-v1",
            report_hash: "a".repeat(64),
            content_sha256: "b".repeat(64),
            content: "{}\n",
          },
        }) as T;
      }
      if (request.operation === "prepare_workflow") {
        return JSON.stringify({
          protocol_version: DESKTOP_IPC_CONTRACT_VERSION,
          request_id: request.request_id,
          operation: request.operation,
          ok: true,
          payload: {
            project_root: request.project_root,
            project_id: "project-id",
            workflow_plan_id: "workflow-plan-id",
            workflow_recipe_id: request.workflow_recipe_id,
            workflow_recipe_version: request.workflow_recipe_version,
            root_structure_snapshot_id: request.root_structure_snapshot_id,
            plan_hash: "c".repeat(64),
            planning_hash: "d".repeat(64),
            reused: false,
          },
        }) as T;
      }
      throw new Error(`unexpected operation: ${String(request.operation)}`);
    };
    const client = new DesktopBackendClient(invokeFn);

    await client.connect();
    const report = await client.applicationReport("/project-a", "json");
    const workflow = await client.prepareWorkflow("/project-a", {
      workflow_recipe_id: "ECatVASP.Workflow.SlabScientificPreparation",
      workflow_recipe_version: "1",
      root_structure_snapshot_id: rootSnapshot,
      parameters_hash: "e".repeat(64),
    });

    expect(report.payload.report_hash).toBe("a".repeat(64));
    expect(workflow.payload.plan_hash).toBe("c".repeat(64));
    expect(Object.keys(calls[0]).sort()).toEqual([
      "operation",
      "project_root",
      "protocol_version",
      "report_format",
      "request_id",
    ]);
    expect(Object.keys(calls[1]).sort()).toEqual([
      "operation",
      "parameters_hash",
      "project_root",
      "protocol_version",
      "request_id",
      "root_structure_snapshot_id",
      "workflow_recipe_id",
      "workflow_recipe_version",
    ]);
    expect(calls[0]).not.toHaveProperty("payload");
    expect(calls[1]).not.toHaveProperty("payload");
  });

  it("rejects malformed typed workflow input before transport", async () => {
    const calls: string[] = [];
    const invokeFn: InvokeFn = async <T>(command: string): Promise<T> => {
      calls.push(command);
      if (command === "backend_health") return fixtureJson() as T;
      throw new Error("backend_exchange must not be reached");
    };
    const client = new DesktopBackendClient(invokeFn);
    await client.connect();

    await expect(
      client.prepareWorkflow("/project-a", {
        workflow_recipe_id: "recipe",
        workflow_recipe_version: "1",
        root_structure_snapshot_id: "snapshot",
        parameters_hash: "not-sha256",
      }),
    ).rejects.toThrow("parameters hash must be SHA-256");
    expect(calls).toEqual(["backend_health"]);
  });
});
