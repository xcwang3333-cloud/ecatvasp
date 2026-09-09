import { describe, expect, it } from "vitest";

import {
  THERMOCHEMISTRY_IPC_VERSION,
  assertReactionPreview,
  assertThermochemistryCatalog,
  assertThermochemistryView,
  parseThermochemistryResponse,
} from "./contracts";

const root = "C:/Research/project";
const freshness = {
  scientific_state: "completed",
  readiness: "satisfied",
  freshness_state: "fresh",
  reason_codes: [] as string[],
};

const conditions = {
  temperature_k: 298.15,
  potential_v: 0,
  ph: 0,
  potential_reference: "she" as const,
  ph_semantics: "explicit_activity" as const,
};

const potentialView = {
  pathway_hash: "a".repeat(64),
  baseline_result_hash: "b".repeat(64),
  baseline_conditions: conditions,
  target_conditions: { ...conditions, potential_v: -0.2 },
  delta_che_condition_ev: 0.2,
  state_keys: ["clean", "H_star"],
  step_views: [
    {
      step_key: "her_h_adsorption",
      che_coefficient: -1,
      potential_slope_ev_per_v: 1,
      baseline_delta_g_ev: 0.1,
      target_delta_g_ev: -0.1,
    },
  ],
  cumulative_state_free_energies_ev: [0, -0.1],
  result_hash: "c".repeat(64),
};

describe("Thermochemistry & Reaction contracts", () => {
  it("parses correlated v2 responses and rejects correlation drift", () => {
    const raw = JSON.stringify({
      protocol_version: THERMOCHEMISTRY_IPC_VERSION,
      request_id: "thermo-1",
      operation: "thermochemistry_catalog",
      ok: true,
      payload: {
        project_root: root,
        project_id: "project-id",
        project_name: "Project",
        frequency_sources: [],
        gas_reference_registry: [],
        analyses: [],
      },
    });
    expect(
      parseThermochemistryResponse(raw, "thermochemistry_catalog", "thermo-1"),
    ).toMatchObject({ project_root: root });
    expect(() =>
      parseThermochemistryResponse(raw, "thermochemistry_catalog", "stale"),
    ).toThrow(/correlation mismatch/);
  });

  it("accepts Python-derived catalog freshness and atom identity", () => {
    expect(() =>
      assertThermochemistryCatalog(
        {
          project_root: root,
          project_id: "project-id",
          project_name: "Project",
          frequency_sources: [
            {
              calculation_id: "calc",
              calculation_type: "gas_frequency",
              scientific_status: "converged",
              source_ready: true,
              attempt_id: "attempt",
              source_analysis_id: "source-analysis",
              source_artifact_id: "source-artifact",
              structure_snapshot_id: "snapshot",
              atoms: [{ atom_uid: "atom-a", element: "H" }],
              reason: "canonical replay verified",
            },
          ],
          gas_reference_registry: [
            { species: "H2", state_label: "electronic_ground_state", reference_hash: "d".repeat(64) },
          ],
          analyses: [
            {
              analysis_id: "analysis",
              analysis_type: "thermochemistry",
              status: "completed",
              tool: "ecatvasp.thermo.harmonic-surface-adsorbate",
              tool_version: "1",
              input_artifact_count: 1,
              freshness,
              view_supported: true,
            },
          ],
        },
        root,
      ),
    ).not.toThrow();
  });

  it("accepts component-resolved thermochemistry without deriving scalar science", () => {
    expect(() =>
      assertThermochemistryView(
        {
          project_root: root,
          project_id: "project-id",
          analysis_id: "analysis",
          analysis_type: "thermochemistry",
          analysis_status: "completed",
          tool: "thermo",
          tool_version: "1",
          artifact_id: "artifact",
          freshness,
          view: {
            kind: "harmonic_surface_adsorbate",
            source_receipt: { source_artifact_id: "source" },
            result_hash: "e".repeat(64),
            result: {
              identity: { subject_kind: "adsorbate" },
              components: { electronic_energy_ev: -10, zpe_ev: 0.2 },
              mode_selection: { accepted_mode_indices: [1, 2] },
              result_hash: "e".repeat(64),
            },
          },
        },
        root,
      ),
    ).not.toThrow();
  });

  it("accepts backend-provided reaction states, steps and descriptors", () => {
    expect(() =>
      assertReactionPreview(
        {
          project_root: root,
          preset_kind: "her_volmer_heyrovsky",
          preset_hash: "f".repeat(64),
          pathway_definition: {},
          baseline_result: {},
          potential_view: potentialView,
          descriptor_definitions: [
            {
              key: "delta_g_h_star",
              kind: "her_delta_g_h_star",
              value: -0.1,
              unit: "eV",
              source_result_hash: "1".repeat(64),
              pathway_hash: null,
              baseline_result_hash: null,
              definition_hash: "2".repeat(64),
            },
          ],
        },
        root,
      ),
    ).not.toThrow();
  });

  it("rejects reaction arrays whose ordered-state shape is inconsistent", () => {
    expect(() =>
      assertReactionPreview(
        {
          project_root: root,
          preset_kind: "her_volmer_heyrovsky",
          preset_hash: "f".repeat(64),
          pathway_definition: {},
          baseline_result: {},
          potential_view: { ...potentialView, cumulative_state_free_energies_ev: [0] },
          descriptor_definitions: [],
        },
        root,
      ),
    ).toThrow(/potential view/);
  });
});
