import { describe, expect, it } from "vitest";

import healthFixture from "./fixtures/health-response.json";
import { DesktopBackendClient, type InvokeFn } from "./client";
import {
  DESKTOP_IPC_CONTRACT_VERSION,
  DesktopContractError,
  type ApplicationReportPayload,
  type HealthPayload,
  assertHealthCompatibility,
  parseBackendRuntimeDiagnostics,
  parseDesktopResponse,
  parseReportExportReceipt,
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

  it("invalidates readiness on transport failure and requires explicit restart", async () => {
    const calls: string[] = [];
    let failTransport = true;
    const invokeFn: InvokeFn = async <T>(
      command: string,
      args?: Record<string, unknown>,
    ): Promise<T> => {
      calls.push(command);
      if (command === "backend_health" || command === "backend_restart") {
        return fixtureJson() as T;
      }
      if (command === "backend_exchange") {
        if (failTransport) {
          failTransport = false;
          throw new Error("simulated transport failure");
        }
        const request = JSON.parse(String(args?.requestJson)) as Record<string, unknown>;
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
            projection_hash: "a".repeat(64),
            calculations: [],
            analyses: [],
            execution_attempts: [],
            scheduler_jobs: [],
            freshness: [],
            attention_rows: 0,
          },
        }) as T;
      }
      throw new Error(`unexpected command: ${command}`);
    };
    const client = new DesktopBackendClient(invokeFn);

    await client.connect();
    await expect(client.status("/project-a")).rejects.toThrow("simulated transport failure");
    const callsAfterFailure = calls.length;
    await expect(client.status("/project-a")).rejects.toThrow(
      "desktop backend health handshake is required",
    );
    expect(calls.length).toBe(callsAfterFailure);

    await client.restart();
    const status = await client.status("/project-a");
    expect(status.payload.project_id).toBe("project-id");
    expect(calls).toContain("backend_restart");
  });

  it("keeps readiness after a correlated application-level backend rejection", async () => {
    let requestCount = 0;
    const invokeFn: InvokeFn = async <T>(
      command: string,
      args?: Record<string, unknown>,
    ): Promise<T> => {
      if (command === "backend_health") return fixtureJson() as T;
      if (command !== "backend_exchange") throw new Error(`unexpected command: ${command}`);
      requestCount += 1;
      const request = JSON.parse(String(args?.requestJson)) as Record<string, unknown>;
      if (requestCount === 1) {
        return JSON.stringify({
          protocol_version: DESKTOP_IPC_CONTRACT_VERSION,
          request_id: request.request_id,
          operation: request.operation,
          ok: false,
          error: { code: "project_unavailable", message: "project unavailable" },
        }) as T;
      }
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
    };
    const client = new DesktopBackendClient(invokeFn);

    await client.connect();
    await expect(client.openProject("/missing")).rejects.toThrow("project unavailable");
    const reopened = await client.openProject("/project-a");
    expect(reopened.payload.project_id).toBe("project-id");
    expect(requestCount).toBe(2);
  });

  it("accepts only sanitized runtime diagnostics fields", () => {
    expect(
      parseBackendRuntimeDiagnostics(
        JSON.stringify({
          backend_state: "unavailable",
          restart_count: 2,
          last_failure_kind: "transport",
        }),
      ),
    ).toEqual({
      backend_state: "unavailable",
      restart_count: 2,
      last_failure_kind: "transport",
    });

    expect(() =>
      parseBackendRuntimeDiagnostics(
        JSON.stringify({
          backend_state: "ready",
          restart_count: 0,
          last_failure_kind: null,
          project_root: "C:/secret/project",
        }),
      ),
    ).toThrow("unexpected fields");
  });

  it("exports exact report bytes without accepting a frontend filename", async () => {
    const digest = "f".repeat(64);
    const report: ApplicationReportPayload = {
      project_root: "/project-a",
      project_id: "project-id",
      report_format: "markdown",
      report_contract_version: "ecatvasp-scientific-report-v1",
      report_hash: "e".repeat(64),
      content_sha256: digest,
      content: "# Current report\n",
    };
    let capturedArgs: Record<string, unknown> | undefined;
    const invokeFn: InvokeFn = async <T>(
      command: string,
      args?: Record<string, unknown>,
    ): Promise<T> => {
      if (command !== "desktop_export_report") throw new Error(`unexpected command: ${command}`);
      capturedArgs = args;
      return JSON.stringify({
        file_name: `ecatvasp-report-${digest}.md`,
        report_format: "markdown",
        content_sha256: digest,
        bytes_written: new TextEncoder().encode(report.content).byteLength,
        reused: false,
      }) as T;
    };
    const client = new DesktopBackendClient(invokeFn);

    const receipt = await client.exportReport("C:/exports", report);

    expect(receipt.file_name).toBe(`ecatvasp-report-${digest}.md`);
    expect(capturedArgs).toEqual({
      outputDirectory: "C:/exports",
      reportFormat: "markdown",
      contentSha256: digest,
      content: report.content,
    });
    expect(capturedArgs).not.toHaveProperty("fileName");
    expect(capturedArgs).not.toHaveProperty("projectRoot");
  });

  it("rejects export receipts that leak runtime paths", () => {
    const digest = "d".repeat(64);
    const report: ApplicationReportPayload = {
      project_root: "/project-a",
      project_id: "project-id",
      report_format: "json",
      report_contract_version: "ecatvasp-scientific-report-v1",
      report_hash: "c".repeat(64),
      content_sha256: digest,
      content: "{}\n",
    };

    expect(() =>
      parseReportExportReceipt(
        JSON.stringify({
          file_name: `ecatvasp-report-${digest}.json`,
          report_format: "json",
          content_sha256: digest,
          bytes_written: 3,
          reused: false,
          output_directory: "C:/secret",
        }),
        report,
      ),
    ).toThrow("unexpected fields");
  });
});
