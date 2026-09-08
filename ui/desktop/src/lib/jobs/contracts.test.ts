import { describe, expect, it } from "vitest";

import {
  JOB_CENTER_IPC_VERSION,
  assertJobCatalogPayload,
  parseJobCenterResponse,
  type JobCatalogPayload,
} from "./contracts";

describe("Job Center desktop contract", () => {
  it("parses a correlated catalog without collapsing lifecycle states", () => {
    const payload = parseJobCenterResponse<JobCatalogPayload>(
      JSON.stringify({
        protocol_version: JOB_CENTER_IPC_VERSION,
        request_id: "jobs-1",
        operation: "job_catalog",
        ok: true,
        payload: {
          project_root: "C:/project",
          project_id: "project-1",
          project_name: "FeNC",
          calculations: [
            {
              calculation_id: "calculation-1",
              calculation_type: "relax",
              recipe_id: "ECatVASP.VASP.SlabRelax",
              scientific_status: "draft",
              latest_attempt_id: "attempt-1",
              attempt_status: "exited",
              remote_job_id: "job-1",
              scheduler_state: "completed",
              scheduler_job_id: "12345",
            },
          ],
        },
      }),
      "job_catalog",
      "jobs-1",
    );
    expect(() => assertJobCatalogPayload(payload, "C:/project")).not.toThrow();
    expect(payload.calculations[0]?.scientific_status).toBe("draft");
    expect(payload.calculations[0]?.scheduler_state).toBe("completed");
  });

  it("rejects response correlation drift", () => {
    const raw = JSON.stringify({
      protocol_version: JOB_CENTER_IPC_VERSION,
      request_id: "other",
      operation: "job_catalog",
      ok: true,
      payload: {},
    });
    expect(() => parseJobCenterResponse(raw, "job_catalog", "jobs-1")).toThrow(
      "correlation mismatch",
    );
  });

  it("surfaces backend execution rejection without frontend reinterpretation", () => {
    const raw = JSON.stringify({
      protocol_version: JOB_CENTER_IPC_VERSION,
      request_id: "jobs-2",
      operation: "prepare_execution",
      ok: false,
      error: {
        code: "application_rejected",
        message: "Calculation requires exactly one durable validated numerical-evidence artifact",
      },
    });
    expect(() => parseJobCenterResponse(raw, "prepare_execution", "jobs-2")).toThrow(
      "durable validated numerical-evidence artifact",
    );
  });
});
