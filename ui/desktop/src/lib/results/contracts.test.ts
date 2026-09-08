import { describe, expect, it } from "vitest";

import {
  RESULT_CENTER_IPC_VERSION,
  assertAnalyzeResultPayload,
  assertPromoteResultPayload,
  assertResultCatalogPayload,
  parseResultCenterResponse,
} from "./contracts";

const root = "C:/Research/project";

describe("Result Center contracts", () => {
  it("parses a correlated catalog response", () => {
    const raw = JSON.stringify({
      protocol_version: RESULT_CENTER_IPC_VERSION,
      request_id: "result-1",
      operation: "result_catalog",
      ok: true,
      payload: {
        project_root: root,
        project_id: "project-id",
        project_name: "Project",
        calculations: [],
      },
    });
    const payload = parseResultCenterResponse(raw, "result_catalog", "result-1");
    expect(payload).toMatchObject({ project_root: root });
  });

  it("rejects response correlation drift", () => {
    const raw = JSON.stringify({
      protocol_version: RESULT_CENTER_IPC_VERSION,
      request_id: "other",
      operation: "result_catalog",
      ok: true,
      payload: {},
    });
    expect(() => parseResultCenterResponse(raw, "result_catalog", "result-1")).toThrow(
      /correlation mismatch/,
    );
  });

  it("validates catalog, analysis, and promotion payloads", () => {
    expect(() =>
      assertResultCatalogPayload(
        {
          project_root: root,
          project_id: "project-id",
          project_name: "Project",
          calculations: [
            {
              calculation_id: "calc",
              calculation_type: "relax",
              recipe_id: "recipe",
              scientific_status: "converged",
              latest_attempt_id: "attempt",
              attempt_status: "parsed",
              retrieved_artifact_types: ["outcar", "contcar"],
              analyzed: true,
              analysis_ready: false,
              analysis_readiness_reason: "already analyzed",
              promotion_ready: true,
              promotion_variant_id: "variant",
            },
          ],
        },
        root,
      ),
    ).not.toThrow();

    expect(() =>
      assertAnalyzeResultPayload(
        {
          project_root: root,
          calculation_id: "calc",
          attempt_id: "attempt",
          intake_hash: "a".repeat(64),
          scientific_verdict: "converged",
          electronic_verdict: "converged",
          ionic_verdict: "converged",
          free_energy_toten_ev: -10.2,
          energy_without_entropy_ev: -10.1,
          energy_sigma0_ev: -10.15,
          fermi_energy_ev: 1.2,
          ionic_steps: 12,
          electronic_steps: 4,
          termination_observed: true,
          evidence_codes: [],
          force_count: 32,
          frequency_mode_count: 0,
        },
        root,
      ),
    ).not.toThrow();

    expect(() =>
      assertPromoteResultPayload(
        {
          project_root: root,
          calculation_id: "calc",
          structure_variant_id: "variant",
          promoted_structure_snapshot_id: "relaxed",
          parent_structure_snapshot_id: "input",
          scientific_verdict: "converged",
          source_artifact_id: "contcar-artifact",
          source_sha256: "b".repeat(64),
        },
        root,
      ),
    ).not.toThrow();
  });
});
