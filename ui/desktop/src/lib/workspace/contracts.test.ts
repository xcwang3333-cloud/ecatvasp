import { describe, expect, it } from "vitest";

import type { FrontendHandoffV1 } from "../backend/contracts";
import {
  DesktopWorkspaceContractError,
  parseScientificWorkspace,
} from "./contracts";

const SHA_A = "a".repeat(64);
const SHA_B = "b".repeat(64);
const SHA_C = "c".repeat(64);
const SHA_D = "d".repeat(64);
const SNAPSHOT_ID = "018f4000-0000-7000-8000-000000000001";
const ANALYSIS_ID = "018f4000-0000-7000-8000-000000000002";
const DEPENDENCY_ID = "018f4000-0000-7000-8000-000000000003";
const PROVENANCE_ID = "018f4000-0000-7000-8000-000000000004";
const WORKFLOW_ID = "018f4000-0000-7000-8000-000000000005";
const CALCULATION_ID = "018f4000-0000-7000-8000-000000000006";
const BINDING_ID = "018f4000-0000-7000-8000-000000000007";
const ATTEMPT_ID = "018f4000-0000-7000-8000-000000000008";
const JOB_ID = "018f4000-0000-7000-8000-000000000009";
const ATOM_C = "018f4000-0000-7000-8000-000000000010";
const ATOM_O = "018f4000-0000-7000-8000-000000000011";

function handoffFixture(): FrontendHandoffV1 {
  return {
    contract_version: "ecatvasp-frontend-handoff-v1",
    backend_version: "1.0.0.dev0",
    capabilities: [
      {
        name: "workspace-report",
        contract_version: "ecatvasp-scientific-report-v1",
      },
      {
        name: "scientific-presentation",
        contract_version: "ecatvasp-scientific-presentation-v1",
      },
      { name: "matterviz", contract_version: "ecatvasp-matterviz-v1" },
    ],
    matterviz_target_version: "0.6.0",
    handoff_hash: SHA_D,
    report: {
      contract_version: "ecatvasp-scientific-report-v1",
      report_hash: SHA_C,
      project: {
        project_id: "018f4000-0000-7000-8000-000000000100",
        project_name: "Desktop workspace fixture",
        project_slug: "desktop-workspace-fixture",
        schema_version: 3,
        description: "Scientific workspace contract fixture",
        created_at: "2026-09-07T00:00:00+00:00",
        projection_hash: SHA_B,
        entity_counts: {
          structure_snapshots: 1,
          calculations: 1,
          analyses: 1,
          execution_attempts: 1,
          remote_jobs: 1,
        },
        statuses: {
          calculations: [["scientifically_converged", 1]],
          analyses: [["completed", 1]],
          execution_attempts: [["completed", 1]],
          scheduler_jobs: [["completed", 1]],
        },
      },
      inventory: {
        project_id: "018f4000-0000-7000-8000-000000000100",
        rows: [
          {
            entity_id: SNAPSHOT_ID,
            entity_kind: "structure_snapshot",
            display_label: "current slab",
            status_domain: null,
            status: null,
            current_scientific_hash: SHA_A,
            provenance: [],
            incoming_dependencies: [],
            outgoing_dependencies: [
              {
                dependency_id: DEPENDENCY_ID,
                upstream_id: SNAPSHOT_ID,
                downstream_id: ANALYSIS_ID,
                kind: "scientific",
                role: "input_structure",
                recorded_hash: SHA_A,
              },
            ],
            scientific_ancestor_ids: [],
            freshness: { state: "fresh", reasons: [] },
            attention_codes: [],
          },
          {
            entity_id: ANALYSIS_ID,
            entity_kind: "analysis",
            display_label: "geometry analysis",
            status_domain: "analysis",
            status: "completed",
            current_scientific_hash: SHA_B,
            provenance: [
              {
                provenance_id: PROVENANCE_ID,
                subject_id: ANALYSIS_ID,
                tool: "ecatvasp.geometry",
                tool_version: "1.0.0.dev0",
                parameters_hash: SHA_C,
                method_fingerprint_id: "018f4000-0000-7000-8000-000000000200",
                created_at: "2026-09-07T00:01:00+00:00",
              },
            ],
            incoming_dependencies: [
              {
                dependency_id: DEPENDENCY_ID,
                upstream_id: SNAPSHOT_ID,
                downstream_id: ANALYSIS_ID,
                kind: "scientific",
                role: "input_structure",
                recorded_hash: SHA_A,
              },
            ],
            outgoing_dependencies: [],
            scientific_ancestor_ids: [SNAPSHOT_ID],
            freshness: {
              state: "stale",
              reasons: [
                {
                  code: "scientific_hash_changed",
                  upstream_id: SNAPSHOT_ID,
                  dependency_id: DEPENDENCY_ID,
                },
              ],
            },
            attention_codes: ["stale_scientific_dependency"],
          },
        ],
        dependencies: [
          {
            dependency_id: DEPENDENCY_ID,
            upstream_id: SNAPSHOT_ID,
            downstream_id: ANALYSIS_ID,
            kind: "scientific",
            role: "input_structure",
            recorded_hash: SHA_A,
          },
        ],
      },
      workflow_readiness: [
        {
          workflow_plan_id: WORKFLOW_ID,
          steps: [
            {
              step_key: "relax",
              scientific_state: "passed",
              readiness: "satisfied",
              gate_reason_codes: [],
              freshness_state: "fresh",
              current_binding_id: BINDING_ID,
              current_generation: 1,
              calculation_id: CALCULATION_ID,
              calculation_status: "scientifically_converged",
              superseded_binding_ids: [],
              superseded_calculation_ids: [],
              execution_attempts: [
                {
                  execution_attempt_id: ATTEMPT_ID,
                  calculation_id: CALCULATION_ID,
                  attempt_number: 1,
                  status: "completed",
                  previous_attempt_id: null,
                  remote_jobs: [
                    {
                      remote_job_id: JOB_ID,
                      execution_attempt_id: ATTEMPT_ID,
                      scheduler: "slurm",
                      scheduler_job_id: "812345",
                      state: "completed",
                      remote_directory: "/scratch/project/relax",
                    },
                  ],
                },
              ],
              orchestration_action: "satisfied",
              orchestration_reason_codes: [],
            },
          ],
          edges: [],
        },
      ],
      presentations: [
        {
          kind: "structure",
          payload: {
            contract_version: "ecatvasp-scientific-presentation-v1",
            structure_snapshot_id: SNAPSHOT_ID,
            source_scientific_hash: SHA_A,
            matterviz: {
              contract_version: "ecatvasp-matterviz-v1",
              target_version: "0.6.0",
              structure: {
                sites: [
                  {
                    species: [{ element: "C", occu: 1, oxidation_state: 0 }],
                    abc: [0, 0, 0.5],
                    xyz: [0, 0, 7.5],
                    label: "C",
                    properties: { ecatvasp_atom_uid: ATOM_C },
                  },
                  {
                    species: [{ element: "O", occu: 1, oxidation_state: 0 }],
                    abc: [0.5, 0.5, 0.55],
                    xyz: [1.5, 1.5, 8.25],
                    label: "O",
                    properties: { ecatvasp_atom_uid: ATOM_O },
                  },
                ],
                lattice: {
                  matrix: [
                    [3, 0, 0],
                    [0, 3, 0],
                    [0, 0, 15],
                  ],
                  pbc: [true, true, true],
                  volume: 135,
                  a: 3,
                  b: 3,
                  c: 15,
                  alpha: 90,
                  beta: 90,
                  gamma: 90,
                },
                properties: { ecatvasp_contract_version: "ecatvasp-matterviz-v1" },
              },
              atom_index_map: {
                atom_uid_by_viewer_index: [ATOM_C, ATOM_O],
                viewer_index_by_atom_uid: { [ATOM_C]: 0, [ATOM_O]: 1 },
              },
              overlay: {
                active_center_indices: [0],
                bound_adsorbate_indices: [1],
                binding_segments: [{ adsorbate_index: 1, site_index: 0 }],
                side_labels: [],
                binding_mode: "atop",
                state_label: "*O",
                conformer_name: null,
              },
              display_policy: {
                cell_type: "original",
                supercell_scaling: "1x1x1",
                apply_supercell_scaling: false,
                show_image_atoms: false,
              },
              runtime: {
                interactive_available: true,
                fallback_kind: "extxyz+manifest",
                message: null,
              },
              fallback_extxyz: "2\nProperties=species:S:1:pos:R:3\nC 0 0 7.5\nO 1.5 1.5 8.25\n",
            },
          },
        },
      ],
    },
  };
}

function presentationPayload(
  handoff: FrontendHandoffV1,
): Record<string, unknown> {
  const report = handoff.report as unknown as {
    presentations: Array<{ payload: Record<string, unknown> }>;
  };
  return report.presentations[0].payload;
}

describe("scientific workspace transport", () => {
  it("preserves separate lifecycle domains and authoritative scientific inspection state", () => {
    const workspace = parseScientificWorkspace(handoffFixture());

    expect(workspace.project.statuses.calculations).toEqual([["scientifically_converged", 1]]);
    expect(workspace.project.statuses.analyses).toEqual([["completed", 1]]);
    expect(workspace.project.statuses.execution_attempts).toEqual([["completed", 1]]);
    expect(workspace.project.statuses.scheduler_jobs).toEqual([["completed", 1]]);

    const stale = workspace.inventory.rows.find((row) => row.entity_id === ANALYSIS_ID);
    expect(stale?.freshness.state).toBe("stale");
    expect(stale?.freshness.reasons.map((reason) => reason.code)).toEqual([
      "scientific_hash_changed",
    ]);
    expect(stale?.attention_codes).toEqual(["stale_scientific_dependency"]);
    expect(stale?.provenance[0]).toMatchObject({
      provenance_id: PROVENANCE_ID,
      tool: "ecatvasp.geometry",
      parameters_hash: SHA_C,
    });
    expect(workspace.inventory.dependencies[0]).toMatchObject({
      kind: "scientific",
      role: "input_structure",
      recorded_hash: SHA_A,
    });

    const readiness = workspace.workflow_readiness[0].steps[0];
    expect(readiness.scientific_state).toBe("passed");
    expect(readiness.readiness).toBe("satisfied");
    expect(readiness.calculation_status).toBe("scientifically_converged");
    expect(readiness.execution_attempts[0].status).toBe("completed");
    expect(readiness.execution_attempts[0].remote_jobs[0].state).toBe("completed");

    const structure = workspace.presentations[0];
    expect(structure.kind).toBe("structure");
    if (structure.kind === "structure") {
      expect(structure.source_scientific_hash).toBe(SHA_A);
      expect(structure.matterviz.atom_index_map.atom_uid_by_viewer_index).toEqual([
        ATOM_C,
        ATOM_O,
      ]);
      expect(structure.matterviz.overlay.binding_segments).toEqual([
        { adsorbate_index: 1, site_index: 0 },
      ]);
    }
  });

  it("rejects a stale structure source hash relative to the same inventory", () => {
    const handoff = handoffFixture();
    presentationPayload(handoff).source_scientific_hash = SHA_D;

    expect(() => parseScientificWorkspace(handoff)).toThrowError(
      new DesktopWorkspaceContractError(
        "structure presentation source hash is stale relative to workspace inventory",
      ),
    );
  });

  it("rejects atom-index drift and display-policy changes", () => {
    const atomDrift = handoffFixture();
    const matterviz = presentationPayload(atomDrift).matterviz as unknown as {
      atom_index_map: { viewer_index_by_atom_uid: Record<string, number> };
    };
    matterviz.atom_index_map.viewer_index_by_atom_uid[ATOM_C] = 1;
    expect(() => parseScientificWorkspace(atomDrift)).toThrow(/not bijective/);

    const displayDrift = handoffFixture();
    const displayMatterviz = presentationPayload(displayDrift).matterviz as unknown as {
      display_policy: { show_image_atoms: boolean };
    };
    displayMatterviz.display_policy.show_image_atoms = true;
    expect(() => parseScientificWorkspace(displayDrift)).toThrow(/frozen index frame/);
  });
});
