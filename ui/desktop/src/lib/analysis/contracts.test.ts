import { describe, expect, it } from "vitest";

import {
  ELECTRONIC_ANALYSIS_IPC_VERSION,
  assertElectronicAnalysisViewPayload,
  assertElectronicCatalogPayload,
  assertMaterializationPayload,
  parseElectronicAnalysisResponse,
} from "./contracts";

const root = "C:/Research/project";

describe("Electronic Analysis contracts", () => {
  it("parses correlated v2 responses and rejects correlation drift", () => {
    const raw = JSON.stringify({
      protocol_version: ELECTRONIC_ANALYSIS_IPC_VERSION,
      request_id: "analysis-1",
      operation: "electronic_analysis_catalog",
      ok: true,
      payload: {
        project_root: root,
        project_id: "project-id",
        project_name: "Project",
        dos_sources: [],
        analyses: [],
      },
    });
    const payload = parseElectronicAnalysisResponse(
      raw,
      "electronic_analysis_catalog",
      "analysis-1",
    );
    expect(payload).toMatchObject({ project_root: root });

    expect(() =>
      parseElectronicAnalysisResponse(raw, "electronic_analysis_catalog", "other"),
    ).toThrow(/correlation mismatch/);
  });

  it("validates catalog and materialization project-root correlation", () => {
    expect(() =>
      assertElectronicCatalogPayload(
        {
          project_root: root,
          project_id: "project-id",
          project_name: "Project",
          dos_sources: [
            {
              calculation_id: "calc",
              scientific_status: "converged",
              latest_attempt_id: "attempt",
              latest_attempt_status: "parsed",
              materialization_ready: true,
              materialized_analysis_id: null,
              reason: "exact managed DOSCAR and atom map are canonical-parse-ready",
            },
          ],
          analyses: [
            {
              analysis_id: "analysis",
              analysis_type: "dos",
              status: "completed",
              tool: "ecatvasp.analysis.doscar",
              tool_version: "1",
              input_artifact_count: 2,
              freshness: {
                scientific_state: "completed",
                readiness: "satisfied",
                freshness_state: "fresh",
                reason_codes: [],
              },
              view_supported: true,
            },
          ],
        },
        root,
      ),
    ).not.toThrow();

    expect(() =>
      assertMaterializationPayload(
        {
          project_root: root,
          analysis_id: "analysis",
          artifact_id: "artifact",
          calculation_id: "calc",
          reused: false,
        },
        root,
      ),
    ).not.toThrow();

    expect(() =>
      assertMaterializationPayload(
        {
          project_root: "C:/Other",
          analysis_id: "analysis",
          artifact_id: "artifact",
          reused: false,
        },
        root,
      ),
    ).toThrow(/materialization payload/);
  });

  it("accepts canonical DOS presentation with explicit native and Fermi-relative axes", () => {
    expect(() =>
      assertElectronicAnalysisViewPayload(
        {
          project_root: root,
          project_id: "project-id",
          analysis_id: "analysis",
          analysis_type: "dos",
          analysis_status: "completed",
          tool: "ecatvasp.analysis.doscar",
          tool_version: "1",
          freshness: {
            scientific_state: "completed",
            readiness: "satisfied",
            freshness_state: "fresh",
            reason_codes: [],
          },
          view: {
            kind: "dos",
            structure_snapshot_id: "snapshot",
            source_artifact_id: "artifact",
            energy_reference: "vasp_native",
            fermi_energy_ev: 0.2,
            energies_ev_native: [-1, 1],
            energies_ev_relative_to_fermi: [-1.2, 0.8],
            series: [],
            display_contract: {
              native_axis_is_canonical: true,
              fermi_relative_axis_is_explicit_transform: true,
              spin_down_may_be_mirrored_for_display: true,
            },
          },
        },
        root,
      ),
    ).not.toThrow();
  });

  it("accepts canonical COHP presentation with explicit sign transform", () => {
    expect(() =>
      assertElectronicAnalysisViewPayload(
        {
          project_root: root,
          project_id: "project-id",
          analysis_id: "cohp-analysis",
          analysis_type: "cohp",
          analysis_status: "completed",
          tool: "lobster",
          tool_version: "5",
          freshness: {
            scientific_state: "completed",
            readiness: "satisfied",
            freshness_state: "fresh",
            reason_codes: [],
          },
          view: {
            kind: "cohp",
            structure_snapshot_id: "snapshot",
            cohpcar_artifact_id: "cohpcar",
            icohplist_artifact_id: "icohplist",
            source_artifact_id: "canonical",
            energy_reference: "lobster_fermi_relative",
            source_fermi_energy_ev: 2.1,
            energies_ev_relative_to_fermi: [-1, 0, 1],
            average_series: [],
            interactions: [],
            display_contract: {
              native_cohp_is_canonical: true,
              negative_cohp_is_explicit_transform: true,
            },
          },
        },
        root,
      ),
    ).not.toThrow();
  });
});
