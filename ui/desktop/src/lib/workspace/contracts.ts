import type { FrontendHandoffV1, JsonObject, JsonValue } from "../backend/contracts";

export const SCIENTIFIC_REPORT_CONTRACT_VERSION = "ecatvasp-scientific-report-v1" as const;
export const SCIENTIFIC_PRESENTATION_CONTRACT_VERSION =
  "ecatvasp-scientific-presentation-v1" as const;
export const MATTERVIZ_CONTRACT_VERSION = "ecatvasp-matterviz-v1" as const;
export const MATTERVIZ_TARGET_VERSION = "0.6.0" as const;

export type StatusPair = [string, number];

export interface WorkspaceProject {
  project_id: string;
  project_name: string;
  project_slug: string;
  schema_version: number;
  description: string | null;
  created_at: string;
  projection_hash: string;
  entity_counts: Record<string, number>;
  statuses: {
    calculations: StatusPair[];
    analyses: StatusPair[];
    execution_attempts: StatusPair[];
    scheduler_jobs: StatusPair[];
  };
}

export interface FreshnessReason {
  code: string;
  upstream_id: string | null;
  dependency_id: string | null;
}

export interface WorkspaceProvenance {
  provenance_id: string;
  subject_id: string;
  tool: string;
  tool_version: string;
  parameters_hash: string | null;
  method_fingerprint_id: string | null;
  created_at: string;
}

export interface WorkspaceDependency {
  dependency_id: string;
  upstream_id: string;
  downstream_id: string;
  kind: string;
  role: string;
  recorded_hash: string;
}

export interface WorkspaceInventoryRow {
  entity_id: string;
  entity_kind: string;
  display_label: string;
  status_domain: string | null;
  status: string | null;
  current_scientific_hash: string | null;
  provenance: WorkspaceProvenance[];
  incoming_dependencies: WorkspaceDependency[];
  outgoing_dependencies: WorkspaceDependency[];
  scientific_ancestor_ids: string[];
  freshness: {
    state: string;
    reasons: FreshnessReason[];
  };
  attention_codes: string[];
}

export interface WorkspaceInventory {
  project_id: string;
  rows: WorkspaceInventoryRow[];
  dependencies: WorkspaceDependency[];
}

export interface WorkflowRemoteJob {
  remote_job_id: string;
  execution_attempt_id: string;
  scheduler: string;
  scheduler_job_id: string;
  state: string;
  remote_directory: string;
}

export interface WorkflowExecutionAttempt {
  execution_attempt_id: string;
  calculation_id: string;
  attempt_number: number;
  status: string;
  previous_attempt_id: string | null;
  remote_jobs: WorkflowRemoteJob[];
}

export interface WorkflowReadinessStep {
  step_key: string;
  scientific_state: string;
  readiness: string;
  gate_reason_codes: string[];
  freshness_state: string | null;
  current_binding_id: string | null;
  current_generation: number | null;
  calculation_id: string | null;
  calculation_status: string | null;
  superseded_binding_ids: string[];
  superseded_calculation_ids: string[];
  execution_attempts: WorkflowExecutionAttempt[];
  orchestration_action: string | null;
  orchestration_reason_codes: string[];
}

export interface WorkflowReadinessEdge {
  upstream_step_key: string;
  downstream_step_key: string;
  role: string;
  verdict: string;
  source_binding_id: string | null;
  accepted_structure_snapshot_id: string | null;
  reason_codes: string[];
}

export interface WorkflowReadinessDashboard {
  workflow_plan_id: string;
  steps: WorkflowReadinessStep[];
  edges: WorkflowReadinessEdge[];
}

export interface MatterVizBindingSegment {
  adsorbate_index: number;
  site_index: number;
}

export interface MatterVizPayload {
  contract_version: typeof MATTERVIZ_CONTRACT_VERSION;
  target_version: typeof MATTERVIZ_TARGET_VERSION;
  structure: JsonObject;
  atom_index_map: {
    atom_uid_by_viewer_index: string[];
    viewer_index_by_atom_uid: Record<string, number>;
  };
  overlay: {
    active_center_indices: number[];
    bound_adsorbate_indices: number[];
    binding_segments: MatterVizBindingSegment[];
    side_labels: JsonObject[];
    binding_mode: string | null;
    state_label: string | null;
    conformer_name: string | null;
  };
  display_policy: {
    cell_type: "original";
    supercell_scaling: "1x1x1";
    apply_supercell_scaling: false;
    show_image_atoms: false;
  };
  runtime: {
    interactive_available: boolean;
    fallback_kind: string;
    message: string | null;
  };
  fallback_extxyz: string;
}

export interface StructurePresentation {
  kind: "structure";
  structure_snapshot_id: string;
  source_scientific_hash: string;
  matterviz: MatterVizPayload;
}

export interface DosPresentation {
  kind: "dos_pdos";
  structure_snapshot_id: string;
  source_content_hash: string;
  atom_index_map_sha256: string;
  source_energy_reference: string;
  fermi_energy_ev: number;
  energy_unit: string;
  density_unit: string;
  native_energies_ev: number[];
  energies_ev_relative_to_fermi: number[];
  series: JsonObject[];
}

export interface CohpPresentation {
  kind: "cohp_icohp";
  structure_snapshot_id: string;
  source_content_hash: string;
  atom_index_map_sha256: string;
  energy_reference: string;
  source_fermi_energy_ev: number;
  sign_convention: string;
  energy_unit: string;
  bond_length_unit: string;
  energies_ev_relative_to_fermi: number[];
  average_series: JsonObject[];
  interactions: JsonObject[];
}

export interface ReactionConditionView {
  temperature_k: number;
  potential_v: number;
  ph: number;
  potential_reference: string;
  ph_semantics: string;
  parameters_hash: string;
}

export interface ReactionStateView {
  index: number;
  state_key: string;
  cumulative_free_energy_ev: number;
}

export interface ReactionStepView {
  index: number;
  step_key: string;
  from_state_key: string;
  to_state_key: string;
  che_coefficient: number;
  potential_slope_ev_per_v: number;
  baseline_delta_g_ev: number;
  target_delta_g_ev: number;
}

export interface ReactionDiagramPresentation {
  kind: "reaction_diagram";
  project_id: string;
  source_result_hash: string;
  potential_view_result_hash: string;
  pathway_definition_hash: string;
  baseline_pathway_result_hash: string;
  baseline_conditions: ReactionConditionView;
  requested_conditions: ReactionConditionView;
  free_energy_unit: string;
  potential_unit: string;
  states: ReactionStateView[];
  steps: ReactionStepView[];
  descriptors: JsonObject[];
  sources: JsonObject[];
}

export type ScientificPresentation =
  | StructurePresentation
  | DosPresentation
  | CohpPresentation
  | ReactionDiagramPresentation;

export interface ScientificWorkspace {
  backend_version: string;
  handoff_hash: string;
  report_hash: string;
  project: WorkspaceProject;
  inventory: WorkspaceInventory;
  workflow_readiness: WorkflowReadinessDashboard[];
  presentations: ScientificPresentation[];
}

export class DesktopWorkspaceContractError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DesktopWorkspaceContractError";
  }
}

export function parseScientificWorkspace(handoff: FrontendHandoffV1): ScientificWorkspace {
  requireCapability(handoff, "workspace-report", SCIENTIFIC_REPORT_CONTRACT_VERSION);
  requireCapability(
    handoff,
    "scientific-presentation",
    SCIENTIFIC_PRESENTATION_CONTRACT_VERSION,
  );
  requireCapability(handoff, "matterviz", MATTERVIZ_CONTRACT_VERSION);
  if (handoff.matterviz_target_version !== MATTERVIZ_TARGET_VERSION) {
    throw new DesktopWorkspaceContractError("unsupported MatterViz target version");
  }

  const report = expectRecord(handoff.report, "scientific report");
  if (report.contract_version !== SCIENTIFIC_REPORT_CONTRACT_VERSION) {
    throw new DesktopWorkspaceContractError("unsupported scientific report contract version");
  }

  const project = parseProject(expectRecord(report.project, "report project"));
  const inventory = parseInventory(expectRecord(report.inventory, "report inventory"));
  if (inventory.project_id !== project.project_id) {
    throw new DesktopWorkspaceContractError("workspace inventory belongs to another project");
  }
  const workflowReadiness = expectArray(
    report.workflow_readiness,
    "workflow readiness",
  ).map(parseReadinessDashboard);
  const presentations = expectArray(report.presentations, "scientific presentations").map(
    parsePresentation,
  );
  validateStructurePresentationCurrentness(presentations, inventory);

  return {
    backend_version: expectString(handoff.backend_version, "backend version"),
    handoff_hash: expectSha256(handoff.handoff_hash, "handoff hash"),
    report_hash: expectSha256(report.report_hash, "report hash"),
    project,
    inventory,
    workflow_readiness: workflowReadiness,
    presentations,
  };
}

function parseProject(value: Record<string, unknown>): WorkspaceProject {
  const statuses = expectRecord(value.statuses, "workspace statuses");
  return {
    project_id: expectString(value.project_id, "project id"),
    project_name: expectString(value.project_name, "project name"),
    project_slug: expectString(value.project_slug, "project slug"),
    schema_version: expectInteger(value.schema_version, "schema version"),
    description: expectNullableString(value.description, "project description"),
    created_at: expectString(value.created_at, "project created_at"),
    projection_hash: expectSha256(value.projection_hash, "workspace projection hash"),
    entity_counts: parseCountRecord(expectRecord(value.entity_counts, "entity counts")),
    statuses: {
      calculations: parseStatusPairs(statuses.calculations, "Calculation statuses"),
      analyses: parseStatusPairs(statuses.analyses, "Analysis statuses"),
      execution_attempts: parseStatusPairs(
        statuses.execution_attempts,
        "ExecutionAttempt statuses",
      ),
      scheduler_jobs: parseStatusPairs(statuses.scheduler_jobs, "scheduler statuses"),
    },
  };
}

function parseInventory(value: Record<string, unknown>): WorkspaceInventory {
  return {
    project_id: expectString(value.project_id, "inventory project id"),
    rows: expectArray(value.rows, "inventory rows").map(parseInventoryRow),
    dependencies: expectArray(value.dependencies, "inventory dependencies").map(parseDependency),
  };
}

function parseInventoryRow(value: unknown): WorkspaceInventoryRow {
  const row = expectRecord(value, "inventory row");
  const freshness = expectRecord(row.freshness, "inventory freshness");
  return {
    entity_id: expectString(row.entity_id, "inventory entity id"),
    entity_kind: expectString(row.entity_kind, "inventory entity kind"),
    display_label: expectString(row.display_label, "inventory display label"),
    status_domain: expectNullableString(row.status_domain, "inventory status domain"),
    status: expectNullableString(row.status, "inventory status"),
    current_scientific_hash: expectNullableSha256(
      row.current_scientific_hash,
      "current scientific hash",
    ),
    provenance: expectArray(row.provenance, "inventory provenance").map(parseProvenance),
    incoming_dependencies: expectArray(
      row.incoming_dependencies,
      "incoming dependencies",
    ).map(parseDependency),
    outgoing_dependencies: expectArray(
      row.outgoing_dependencies,
      "outgoing dependencies",
    ).map(parseDependency),
    scientific_ancestor_ids: expectStringArray(
      row.scientific_ancestor_ids,
      "scientific ancestor ids",
    ),
    freshness: {
      state: expectString(freshness.state, "freshness state"),
      reasons: expectArray(freshness.reasons, "freshness reasons").map(parseFreshnessReason),
    },
    attention_codes: expectStringArray(row.attention_codes, "attention codes"),
  };
}

function parseFreshnessReason(value: unknown): FreshnessReason {
  const reason = expectRecord(value, "freshness reason");
  return {
    code: expectString(reason.code, "freshness reason code"),
    upstream_id: expectNullableString(reason.upstream_id, "freshness upstream id"),
    dependency_id: expectNullableString(reason.dependency_id, "freshness dependency id"),
  };
}

function parseProvenance(value: unknown): WorkspaceProvenance {
  const item = expectRecord(value, "provenance record");
  return {
    provenance_id: expectString(item.provenance_id, "provenance id"),
    subject_id: expectString(item.subject_id, "provenance subject id"),
    tool: expectString(item.tool, "provenance tool"),
    tool_version: expectString(item.tool_version, "provenance tool version"),
    parameters_hash: expectNullableSha256(item.parameters_hash, "provenance parameters hash"),
    method_fingerprint_id: expectNullableString(
      item.method_fingerprint_id,
      "provenance MethodFingerprint id",
    ),
    created_at: expectString(item.created_at, "provenance created_at"),
  };
}

function parseDependency(value: unknown): WorkspaceDependency {
  const item = expectRecord(value, "dependency record");
  return {
    dependency_id: expectString(item.dependency_id, "dependency id"),
    upstream_id: expectString(item.upstream_id, "dependency upstream id"),
    downstream_id: expectString(item.downstream_id, "dependency downstream id"),
    kind: expectString(item.kind, "dependency kind"),
    role: expectString(item.role, "dependency role"),
    recorded_hash: expectSha256(item.recorded_hash, "dependency recorded hash"),
  };
}

function parseReadinessDashboard(value: unknown): WorkflowReadinessDashboard {
  const dashboard = expectRecord(value, "workflow readiness dashboard");
  return {
    workflow_plan_id: expectString(dashboard.workflow_plan_id, "workflow plan id"),
    steps: expectArray(dashboard.steps, "workflow readiness steps").map(parseReadinessStep),
    edges: expectArray(dashboard.edges, "workflow readiness edges").map(parseReadinessEdge),
  };
}

function parseReadinessStep(value: unknown): WorkflowReadinessStep {
  const step = expectRecord(value, "workflow readiness step");
  return {
    step_key: expectString(step.step_key, "workflow step key"),
    scientific_state: expectString(step.scientific_state, "workflow scientific state"),
    readiness: expectString(step.readiness, "workflow readiness"),
    gate_reason_codes: expectStringArray(step.gate_reason_codes, "workflow gate reasons"),
    freshness_state: expectNullableString(step.freshness_state, "workflow freshness state"),
    current_binding_id: expectNullableString(step.current_binding_id, "workflow binding id"),
    current_generation: expectNullableInteger(step.current_generation, "workflow generation"),
    calculation_id: expectNullableString(step.calculation_id, "workflow Calculation id"),
    calculation_status: expectNullableString(
      step.calculation_status,
      "workflow Calculation status",
    ),
    superseded_binding_ids: expectStringArray(
      step.superseded_binding_ids,
      "superseded binding ids",
    ),
    superseded_calculation_ids: expectStringArray(
      step.superseded_calculation_ids,
      "superseded Calculation ids",
    ),
    execution_attempts: expectArray(
      step.execution_attempts,
      "workflow ExecutionAttempts",
    ).map(parseExecutionAttempt),
    orchestration_action: expectNullableString(
      step.orchestration_action,
      "workflow orchestration action",
    ),
    orchestration_reason_codes: expectStringArray(
      step.orchestration_reason_codes,
      "workflow orchestration reasons",
    ),
  };
}

function parseExecutionAttempt(value: unknown): WorkflowExecutionAttempt {
  const attempt = expectRecord(value, "workflow ExecutionAttempt");
  return {
    execution_attempt_id: expectString(attempt.execution_attempt_id, "ExecutionAttempt id"),
    calculation_id: expectString(attempt.calculation_id, "ExecutionAttempt Calculation id"),
    attempt_number: expectInteger(attempt.attempt_number, "ExecutionAttempt number"),
    status: expectString(attempt.status, "ExecutionAttempt status"),
    previous_attempt_id: expectNullableString(
      attempt.previous_attempt_id,
      "previous ExecutionAttempt id",
    ),
    remote_jobs: expectArray(attempt.remote_jobs, "RemoteJobs").map(parseRemoteJob),
  };
}

function parseRemoteJob(value: unknown): WorkflowRemoteJob {
  const job = expectRecord(value, "RemoteJob");
  return {
    remote_job_id: expectString(job.remote_job_id, "RemoteJob id"),
    execution_attempt_id: expectString(job.execution_attempt_id, "RemoteJob ExecutionAttempt id"),
    scheduler: expectString(job.scheduler, "scheduler"),
    scheduler_job_id: expectString(job.scheduler_job_id, "scheduler job id"),
    state: expectString(job.state, "scheduler state"),
    remote_directory: expectString(job.remote_directory, "remote directory"),
  };
}

function parseReadinessEdge(value: unknown): WorkflowReadinessEdge {
  const edge = expectRecord(value, "workflow readiness edge");
  return {
    upstream_step_key: expectString(edge.upstream_step_key, "upstream workflow step"),
    downstream_step_key: expectString(edge.downstream_step_key, "downstream workflow step"),
    role: expectString(edge.role, "workflow edge role"),
    verdict: expectString(edge.verdict, "workflow edge verdict"),
    source_binding_id: expectNullableString(edge.source_binding_id, "workflow source binding id"),
    accepted_structure_snapshot_id: expectNullableString(
      edge.accepted_structure_snapshot_id,
      "accepted StructureSnapshot id",
    ),
    reason_codes: expectStringArray(edge.reason_codes, "workflow edge reasons"),
  };
}

function parsePresentation(value: unknown): ScientificPresentation {
  const record = expectRecord(value, "scientific presentation");
  const kind = expectString(record.kind, "scientific presentation kind");
  const payload = expectRecord(record.payload, "scientific presentation payload");
  if (payload.contract_version !== SCIENTIFIC_PRESENTATION_CONTRACT_VERSION) {
    throw new DesktopWorkspaceContractError("unsupported scientific presentation contract version");
  }
  switch (kind) {
    case "structure":
      return parseStructurePresentation(payload);
    case "dos_pdos":
      return parseDosPresentation(payload);
    case "cohp_icohp":
      return parseCohpPresentation(payload);
    case "reaction_diagram":
      return parseReactionPresentation(payload);
    default:
      throw new DesktopWorkspaceContractError(`unsupported scientific presentation kind: ${kind}`);
  }
}

function parseStructurePresentation(payload: Record<string, unknown>): StructurePresentation {
  return {
    kind: "structure",
    structure_snapshot_id: expectString(payload.structure_snapshot_id, "StructureSnapshot id"),
    source_scientific_hash: expectSha256(
      payload.source_scientific_hash,
      "structure source scientific hash",
    ),
    matterviz: parseMatterViz(expectRecord(payload.matterviz, "MatterViz payload")),
  };
}

function parseMatterViz(value: Record<string, unknown>): MatterVizPayload {
  if (value.contract_version !== MATTERVIZ_CONTRACT_VERSION) {
    throw new DesktopWorkspaceContractError("unsupported MatterViz contract version");
  }
  if (value.target_version !== MATTERVIZ_TARGET_VERSION) {
    throw new DesktopWorkspaceContractError("unsupported MatterViz target version");
  }
  const structure = expectJsonObject(value.structure, "MatterViz structure");
  const sites = expectArray(structure.sites, "MatterViz sites");
  if (sites.length === 0) {
    throw new DesktopWorkspaceContractError("MatterViz structure must contain sites");
  }
  const atomMap = expectRecord(value.atom_index_map, "MatterViz atom index map");
  const atomUidByIndex = expectStringArray(
    atomMap.atom_uid_by_viewer_index,
    "MatterViz atom uid index map",
  );
  const indexByAtomUid = parseIndexRecord(
    expectRecord(atomMap.viewer_index_by_atom_uid, "MatterViz reverse atom index map"),
  );
  if (atomUidByIndex.length !== sites.length) {
    throw new DesktopWorkspaceContractError("MatterViz site and atom-index lengths differ");
  }
  for (const [index, atomUid] of atomUidByIndex.entries()) {
    if (indexByAtomUid[atomUid] !== index) {
      throw new DesktopWorkspaceContractError("MatterViz atom-index map is not bijective");
    }
    const site = expectRecord(sites[index], "MatterViz site");
    const properties = expectRecord(site.properties, "MatterViz site properties");
    if (properties.ecatvasp_atom_uid !== atomUid) {
      throw new DesktopWorkspaceContractError("MatterViz site atom uid does not match index map");
    }
  }
  if (Object.keys(indexByAtomUid).length !== atomUidByIndex.length) {
    throw new DesktopWorkspaceContractError("MatterViz reverse atom-index map contains extra ids");
  }

  const overlay = expectRecord(value.overlay, "MatterViz overlay");
  const activeCenterIndices = parseIndices(
    overlay.active_center_indices,
    sites.length,
    "MatterViz active-center indices",
  );
  const boundAdsorbateIndices = parseIndices(
    overlay.bound_adsorbate_indices,
    sites.length,
    "MatterViz adsorbate indices",
  );
  const bindingSegments = expectArray(
    overlay.binding_segments,
    "MatterViz binding segments",
  ).map((segment) => {
    const item = expectRecord(segment, "MatterViz binding segment");
    const adsorbateIndex = expectIndex(
      item.adsorbate_index,
      sites.length,
      "MatterViz adsorbate index",
    );
    const siteIndex = expectIndex(item.site_index, sites.length, "MatterViz site index");
    if (adsorbateIndex === siteIndex) {
      throw new DesktopWorkspaceContractError("MatterViz binding segment requires two sites");
    }
    return { adsorbate_index: adsorbateIndex, site_index: siteIndex };
  });

  const policy = expectRecord(value.display_policy, "MatterViz display policy");
  if (
    policy.cell_type !== "original" ||
    policy.supercell_scaling !== "1x1x1" ||
    policy.apply_supercell_scaling !== false ||
    policy.show_image_atoms !== false
  ) {
    throw new DesktopWorkspaceContractError("MatterViz display policy changes the frozen index frame");
  }
  const runtime = expectRecord(value.runtime, "MatterViz runtime");

  return {
    contract_version: MATTERVIZ_CONTRACT_VERSION,
    target_version: MATTERVIZ_TARGET_VERSION,
    structure,
    atom_index_map: {
      atom_uid_by_viewer_index: atomUidByIndex,
      viewer_index_by_atom_uid: indexByAtomUid,
    },
    overlay: {
      active_center_indices: activeCenterIndices,
      bound_adsorbate_indices: boundAdsorbateIndices,
      binding_segments: bindingSegments,
      side_labels: expectArray(overlay.side_labels, "MatterViz side labels").map((item) =>
        expectJsonObject(item, "MatterViz side label"),
      ),
      binding_mode: expectNullableString(overlay.binding_mode, "MatterViz binding mode"),
      state_label: expectNullableString(overlay.state_label, "MatterViz state label"),
      conformer_name: expectNullableString(overlay.conformer_name, "MatterViz conformer name"),
    },
    display_policy: {
      cell_type: "original",
      supercell_scaling: "1x1x1",
      apply_supercell_scaling: false,
      show_image_atoms: false,
    },
    runtime: {
      interactive_available: expectBoolean(
        runtime.interactive_available,
        "MatterViz interactive availability",
      ),
      fallback_kind: expectString(runtime.fallback_kind, "MatterViz fallback kind"),
      message: expectNullableString(runtime.message, "MatterViz runtime message"),
    },
    fallback_extxyz: expectString(value.fallback_extxyz, "MatterViz extXYZ fallback"),
  };
}

function parseDosPresentation(payload: Record<string, unknown>): DosPresentation {
  return {
    kind: "dos_pdos",
    structure_snapshot_id: expectString(payload.structure_snapshot_id, "DOS StructureSnapshot id"),
    source_content_hash: expectSha256(payload.source_content_hash, "DOS source content hash"),
    atom_index_map_sha256: expectSha256(payload.atom_index_map_sha256, "DOS atom index hash"),
    source_energy_reference: expectString(payload.source_energy_reference, "DOS energy reference"),
    fermi_energy_ev: expectNumber(payload.fermi_energy_ev, "DOS Fermi energy"),
    energy_unit: expectString(payload.energy_unit, "DOS energy unit"),
    density_unit: expectString(payload.density_unit, "DOS density unit"),
    native_energies_ev: expectNumberArray(payload.native_energies_ev, "DOS native energy grid"),
    energies_ev_relative_to_fermi: expectNumberArray(
      payload.energies_ev_relative_to_fermi,
      "DOS Fermi-relative grid",
    ),
    series: expectArray(payload.series, "DOS series").map((item) =>
      expectJsonObject(item, "DOS series record"),
    ),
  };
}

function parseCohpPresentation(payload: Record<string, unknown>): CohpPresentation {
  const signConvention = expectString(payload.sign_convention, "COHP sign convention");
  if (signConvention !== "lobster_native") {
    throw new DesktopWorkspaceContractError("desktop COHP view requires LOBSTER native sign");
  }
  return {
    kind: "cohp_icohp",
    structure_snapshot_id: expectString(payload.structure_snapshot_id, "COHP StructureSnapshot id"),
    source_content_hash: expectSha256(payload.source_content_hash, "COHP source content hash"),
    atom_index_map_sha256: expectSha256(payload.atom_index_map_sha256, "COHP atom index hash"),
    energy_reference: expectString(payload.energy_reference, "COHP energy reference"),
    source_fermi_energy_ev: expectNumber(payload.source_fermi_energy_ev, "COHP Fermi energy"),
    sign_convention: signConvention,
    energy_unit: expectString(payload.energy_unit, "COHP energy unit"),
    bond_length_unit: expectString(payload.bond_length_unit, "COHP bond length unit"),
    energies_ev_relative_to_fermi: expectNumberArray(
      payload.energies_ev_relative_to_fermi,
      "COHP energy grid",
    ),
    average_series: expectArray(payload.average_series, "COHP average series").map((item) =>
      expectJsonObject(item, "COHP average series record"),
    ),
    interactions: expectArray(payload.interactions, "COHP interactions").map((item) =>
      expectJsonObject(item, "COHP interaction record"),
    ),
  };
}

function parseReactionPresentation(payload: Record<string, unknown>): ReactionDiagramPresentation {
  return {
    kind: "reaction_diagram",
    project_id: expectString(payload.project_id, "reaction project id"),
    source_result_hash: expectSha256(payload.source_result_hash, "reaction source result hash"),
    potential_view_result_hash: expectSha256(
      payload.potential_view_result_hash,
      "reaction potential-view hash",
    ),
    pathway_definition_hash: expectSha256(
      payload.pathway_definition_hash,
      "reaction pathway definition hash",
    ),
    baseline_pathway_result_hash: expectSha256(
      payload.baseline_pathway_result_hash,
      "reaction baseline pathway hash",
    ),
    baseline_conditions: parseReactionConditions(
      expectRecord(payload.baseline_conditions, "baseline CHE conditions"),
    ),
    requested_conditions: parseReactionConditions(
      expectRecord(payload.requested_conditions, "requested CHE conditions"),
    ),
    free_energy_unit: expectString(payload.free_energy_unit, "reaction free-energy unit"),
    potential_unit: expectString(payload.potential_unit, "reaction potential unit"),
    states: expectArray(payload.states, "reaction states").map(parseReactionState),
    steps: expectArray(payload.steps, "reaction steps").map(parseReactionStep),
    descriptors: expectArray(payload.descriptors, "reaction descriptors").map((item) =>
      expectJsonObject(item, "reaction descriptor"),
    ),
    sources: expectArray(payload.sources, "reaction sources").map((item) =>
      expectJsonObject(item, "reaction source"),
    ),
  };
}

function parseReactionConditions(value: Record<string, unknown>): ReactionConditionView {
  return {
    temperature_k: expectNumber(value.temperature_k, "CHE temperature"),
    potential_v: expectNumber(value.potential_v, "CHE potential"),
    ph: expectNumber(value.ph, "CHE pH"),
    potential_reference: expectString(value.potential_reference, "CHE potential reference"),
    ph_semantics: expectString(value.ph_semantics, "CHE pH semantics"),
    parameters_hash: expectSha256(value.parameters_hash, "CHE parameters hash"),
  };
}

function parseReactionState(value: unknown): ReactionStateView {
  const state = expectRecord(value, "reaction state");
  return {
    index: expectInteger(state.index, "reaction state index"),
    state_key: expectString(state.state_key, "reaction state key"),
    cumulative_free_energy_ev: expectNumber(
      state.cumulative_free_energy_ev,
      "reaction state free energy",
    ),
  };
}

function parseReactionStep(value: unknown): ReactionStepView {
  const step = expectRecord(value, "reaction step");
  return {
    index: expectInteger(step.index, "reaction step index"),
    step_key: expectString(step.step_key, "reaction step key"),
    from_state_key: expectString(step.from_state_key, "reaction from-state key"),
    to_state_key: expectString(step.to_state_key, "reaction to-state key"),
    che_coefficient: expectNumber(step.che_coefficient, "reaction CHE coefficient"),
    potential_slope_ev_per_v: expectNumber(
      step.potential_slope_ev_per_v,
      "reaction potential slope",
    ),
    baseline_delta_g_ev: expectNumber(step.baseline_delta_g_ev, "reaction baseline delta G"),
    target_delta_g_ev: expectNumber(step.target_delta_g_ev, "reaction target delta G"),
  };
}

function validateStructurePresentationCurrentness(
  presentations: ScientificPresentation[],
  inventory: WorkspaceInventory,
): void {
  const rowById = new Map(inventory.rows.map((row) => [row.entity_id, row]));
  for (const presentation of presentations) {
    if (presentation.kind !== "structure") continue;
    const row = rowById.get(presentation.structure_snapshot_id);
    if (row === undefined || row.current_scientific_hash === null) {
      throw new DesktopWorkspaceContractError(
        "structure presentation has no current scientific hash in workspace inventory",
      );
    }
    if (row.current_scientific_hash !== presentation.source_scientific_hash) {
      throw new DesktopWorkspaceContractError(
        "structure presentation source hash is stale relative to workspace inventory",
      );
    }
  }
}

function requireCapability(
  handoff: FrontendHandoffV1,
  name: string,
  contractVersion: string,
): void {
  const capability = handoff.capabilities.find((item) => item.name === name);
  if (capability === undefined || capability.contract_version !== contractVersion) {
    throw new DesktopWorkspaceContractError(`missing or incompatible frontend capability: ${name}`);
  }
}

function parseCountRecord(value: Record<string, unknown>): Record<string, number> {
  const result: Record<string, number> = {};
  for (const [key, count] of Object.entries(value)) {
    result[key] = expectNonNegativeInteger(count, `entity count ${key}`);
  }
  return result;
}

function parseStatusPairs(value: unknown, label: string): StatusPair[] {
  return expectArray(value, label).map((pair) => {
    if (!Array.isArray(pair) || pair.length !== 2) {
      throw new DesktopWorkspaceContractError(`${label} must contain [status, count] pairs`);
    }
    return [expectString(pair[0], `${label} status`), expectNonNegativeInteger(pair[1], label)];
  });
}

function parseIndexRecord(value: Record<string, unknown>): Record<string, number> {
  const result: Record<string, number> = {};
  for (const [atomUid, index] of Object.entries(value)) {
    result[atomUid] = expectNonNegativeInteger(index, "MatterViz reverse index");
  }
  return result;
}

function parseIndices(value: unknown, siteCount: number, label: string): number[] {
  const indices = expectArray(value, label).map((index) => expectIndex(index, siteCount, label));
  if (new Set(indices).size !== indices.length) {
    throw new DesktopWorkspaceContractError(`${label} contain duplicate indices`);
  }
  return indices;
}

function expectIndex(value: unknown, siteCount: number, label: string): number {
  const index = expectNonNegativeInteger(value, label);
  if (index >= siteCount) {
    throw new DesktopWorkspaceContractError(`${label} is out of range`);
  }
  return index;
}

function expectRecord(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new DesktopWorkspaceContractError(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function expectJsonObject(value: unknown, label: string): JsonObject {
  return expectRecord(value, label) as JsonObject;
}

function expectArray(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) {
    throw new DesktopWorkspaceContractError(`${label} must be an array`);
  }
  return value;
}

function expectString(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new DesktopWorkspaceContractError(`${label} must be a non-empty string`);
  }
  return value;
}

function expectNullableString(value: unknown, label: string): string | null {
  return value === null ? null : expectString(value, label);
}

function expectSha256(value: unknown, label: string): string {
  const hash = expectString(value, label).toLowerCase();
  if (!/^[0-9a-f]{64}$/.test(hash)) {
    throw new DesktopWorkspaceContractError(`${label} must be a SHA-256 digest`);
  }
  return hash;
}

function expectNullableSha256(value: unknown, label: string): string | null {
  return value === null ? null : expectSha256(value, label);
}

function expectBoolean(value: unknown, label: string): boolean {
  if (typeof value !== "boolean") {
    throw new DesktopWorkspaceContractError(`${label} must be boolean`);
  }
  return value;
}

function expectNumber(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new DesktopWorkspaceContractError(`${label} must be finite`);
  }
  return value;
}

function expectInteger(value: unknown, label: string): number {
  const number = expectNumber(value, label);
  if (!Number.isInteger(number)) {
    throw new DesktopWorkspaceContractError(`${label} must be an integer`);
  }
  return number;
}

function expectNonNegativeInteger(value: unknown, label: string): number {
  const number = expectInteger(value, label);
  if (number < 0) {
    throw new DesktopWorkspaceContractError(`${label} must not be negative`);
  }
  return number;
}

function expectNullableInteger(value: unknown, label: string): number | null {
  return value === null ? null : expectInteger(value, label);
}

function expectStringArray(value: unknown, label: string): string[] {
  return expectArray(value, label).map((item) => expectString(item, label));
}

function expectNumberArray(value: unknown, label: string): number[] {
  return expectArray(value, label).map((item) => expectNumber(item, label));
}

export function jsonValue(value: unknown, label: string): JsonValue {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return value;
  }
  if (Array.isArray(value)) {
    return value.map((item) => jsonValue(item, label));
  }
  const record = expectRecord(value, label);
  const result: JsonObject = {};
  for (const [key, item] of Object.entries(record)) {
    result[key] = jsonValue(item, label);
  }
  return result;
}
