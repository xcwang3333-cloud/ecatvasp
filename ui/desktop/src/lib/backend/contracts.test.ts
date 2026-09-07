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
  it("accepts the Python-owned health fixture", () => {
    const response = requireSuccess(
      parseDesktopResponse<HealthPayload>(fixtureJson(), "health"),
    );

    expect(() => assertHealthCompatibility(response)).not.toThrow();
    expect(response.payload.stateless_project_requests).toBe(true);
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

  it("requires health before any project-scoped request", async () => {
    const invokeFn: InvokeFn = async () => {
      throw new Error("invoke must not be reached before health");
    };
    const client = new DesktopBackendClient(invokeFn);

    await expect(client.status("/project-a")).rejects.toThrow(
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
});
