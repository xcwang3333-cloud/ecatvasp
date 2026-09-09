import { describe, expect, it } from "vitest";

import type { InvokeFn } from "../backend/client";
import { ThermochemistryClient } from "./client";
import { THERMOCHEMISTRY_IPC_VERSION } from "./contracts";

describe("ThermochemistryClient transport", () => {
  it("routes Block 7 requests only through backend_exchange_block7", async () => {
    const commands: string[] = [];
    const invokeFn: InvokeFn = async <T>(command: string, args?: Record<string, unknown>) => {
      commands.push(command);
      expect(command).toBe("backend_exchange_block7");
      expect(args).toBeDefined();
      const requestJson = args?.requestJson;
      expect(typeof requestJson).toBe("string");
      const request = JSON.parse(requestJson as string) as {
        protocol_version: string;
        request_id: string;
        operation: string;
        project_root: string;
      };
      expect(request.protocol_version).toBe(THERMOCHEMISTRY_IPC_VERSION);
      expect(request.operation).toBe("thermochemistry_catalog");
      expect(request.project_root).toBe("C:/Research/project");
      return JSON.stringify({
        protocol_version: THERMOCHEMISTRY_IPC_VERSION,
        request_id: request.request_id,
        operation: request.operation,
        ok: true,
        payload: {
          project_root: request.project_root,
          project_id: "project-id",
          project_name: "Project",
          frequency_sources: [],
          gas_reference_registry: [],
          analyses: [],
        },
      }) as T;
    };

    const client = new ThermochemistryClient(invokeFn);
    const catalog = await client.catalog("C:/Research/project");

    expect(catalog.project_id).toBe("project-id");
    expect(commands).toEqual(["backend_exchange_block7"]);
  });
});
