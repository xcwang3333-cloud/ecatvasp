export const THERMOCHEMISTRY_IPC_VERSION = "ecatvasp-desktop-ipc-v2" as const;

export type ThermochemistryOperation =
  | "thermochemistry_catalog"
  | "materialize_harmonic_thermochemistry"
  | "materialize_gas_reference"
  | "thermochemistry_view"
  | "reaction_preview"
  | "materialize_reaction_diagram"
  | "reaction_diagram_view";

export interface ThermochemistryFreshness {
  scientific_state: string;
  readiness: string;
  freshness_state: string | null;
  reason_codes: string[];
}

export interface FrequencySourceAtom {
  atom_uid: string;
  element: string;
}

export interface FrequencySourceRow {
  calculation_id: string;
  calculation_type: "frequency" | "gas_frequency";
  scientific_status: string;
  source_ready: boolean;
  attempt_id: string | null;
  source_analysis_id: string | null;
  source_artifact_id: string | null;
  structure_snapshot_id?: string | null;
  atoms?: FrequencySourceAtom[];
  reason: string;
}

export interface GasReferenceRegistryRow {
  species: "H2" | "H2O" | "O2" | "CO" | "CO2";
  state_label: string;
  reference_hash: string;
}

export interface ThermochemistryAnalysisRow {
  analysis_id: string;
  analysis_type: "thermochemistry" | "reaction_diagram";
  status: string;
  tool: string | null;
  tool_version: string | null;
  input_artifact_count: number;
  freshness: ThermochemistryFreshness;
  view_supported: boolean;
}

export interface ThermochemistryCatalogPayload {
  project_root: string;
  project_id: string;
  project_name: string;
  frequency_sources: FrequencySourceRow[];
  gas_reference_registry: GasReferenceRegistryRow[];
  analyses: ThermochemistryAnalysisRow[];
}

export interface ModeExclusionInput {
  mode_index: number;
  reason: "imaginary" | "low_frequency" | "constrained" | "translational" | "rotational";
  note?: string;
}

export interface GasAtomicMassInput {
  atom_uid: string;
  mass_amu: number;
  isotopologue_label?: string;
}

export interface HarmonicMaterializationInput {
  calculation_id: string;
  subject_kind: "surface" | "adsorbate";
  temperature_k: number;
  electronic_energy_kind: "energy_sigma0_ev" | "energy_without_entropy_ev" | "free_energy_toten_ev";
  electronic_entropy_policy: "neglected";
  frequency_cutoff_cm_inverse: number;
  imaginary_mode_policy: "reject_any" | "exclude_explicit";
  low_frequency_policy: "reject_below_cutoff" | "exclude_explicit";
  exclusions: ModeExclusionInput[];
}

export interface GasReferenceMaterializationInput {
  calculation_id: string;
  species: "H2" | "H2O" | "O2" | "CO" | "CO2";
  temperature_k: number;
  pressure_pa: number;
  standard_state: "ideal_gas_1_bar" | "ideal_gas_1_atm";
  electronic_energy_kind: "energy_sigma0_ev" | "energy_without_entropy_ev" | "free_energy_toten_ev";
  electronic_entropy_policy: "neglected" | "spin_degeneracy";
  geometry_kind: "monatomic" | "linear" | "nonlinear";
  symmetry_number: number;
  spin_multiplicity: number;
  atomic_masses: GasAtomicMassInput[];
  frequency_cutoff_cm_inverse: number;
  imaginary_mode_policy: "reject_any" | "exclude_explicit";
  low_frequency_policy: "reject_below_cutoff" | "exclude_explicit";
  exclusions: ModeExclusionInput[];
}

export interface ThermochemistryMaterializationPayload {
  project_root: string;
  analysis_id: string;
  artifact_id: string;
  calculation_id: string;
  reused: boolean;
}

export interface ReactionDiagramMaterializationPayload {
  project_root: string;
  analysis_id: string;
  artifact_id: string;
  preset_kind: ReactionPresetKind;
  reused: boolean;
  dataset_hash?: string;
}

export interface ThermochemistryAnalysisViewPayload {
  project_root: string;
  project_id: string;
  analysis_id: string;
  analysis_type: "thermochemistry" | "reaction_diagram";
  analysis_status: string;
  tool: string | null;
  tool_version: string | null;
  artifact_id: string;
  freshness: ThermochemistryFreshness;
  view: ThermochemistryScientificView;
}

export interface ThermochemistryResultView {
  kind: "harmonic_surface_adsorbate" | "ideal_gas_reference" | "reference_correction";
  source_receipt: Record<string, unknown>;
  result_hash: string;
  result: {
    identity: Record<string, unknown>;
    components: Record<string, unknown>;
    mode_selection: Record<string, unknown> | null;
    result_hash: string;
  };
}

export interface ReactionDescriptorDefinition {
  key: string;
  kind: "limiting_potential" | "reversible_potential" | "oer_theoretical_overpotential" | "her_delta_g_h_star";
  value: number;
  unit: "V" | "eV";
  source_result_hash: string;
  pathway_hash: string | null;
  baseline_result_hash: string | null;
  definition_hash: string;
}

export interface ReactionConditions {
  temperature_k: number;
  potential_v: number;
  ph: number;
  potential_reference: "she" | "rhe";
  ph_semantics: "explicit_activity" | "included_in_rhe";
}

export interface PotentialStepView {
  step_key: string;
  che_coefficient: number;
  potential_slope_ev_per_v: number;
  baseline_delta_g_ev: number;
  target_delta_g_ev: number;
}

export interface PotentialPathwayView {
  pathway_hash: string;
  baseline_result_hash: string;
  baseline_conditions: ReactionConditions;
  target_conditions: ReactionConditions;
  delta_che_condition_ev: number;
  state_keys: string[];
  step_views: PotentialStepView[];
  cumulative_state_free_energies_ev: number[];
  result_hash: string;
}

export interface ReactionDiagramDataset {
  project_id: string;
  pathway_definition_hash: string;
  baseline_pathway_result_hash: string;
  baseline_conditions: ReactionConditions;
  requested_conditions: ReactionConditions;
  potential_view: PotentialPathwayView;
  source_receipts: Array<Record<string, unknown>>;
  descriptor_definitions: ReactionDescriptorDefinition[];
  result_hash: string;
}

export interface ReactionDiagramView {
  kind: "reaction_diagram";
  dataset_hash: string;
  dataset: ReactionDiagramDataset;
}

export type ThermochemistryScientificView = ThermochemistryResultView | ReactionDiagramView;

export type ReactionPresetKind =
  | "her_volmer_heyrovsky"
  | "orr_associative_4e"
  | "oer_associative_4e"
  | "co2rr_to_co_2e";

export interface HERBindings {
  clean_surface_analysis_id: string;
  h_adsorbed_analysis_id: string;
  h2_reference_analysis_id: string;
}

export interface ORRBindings {
  clean_surface_analysis_id: string;
  ooh_adsorbed_analysis_id: string;
  o_adsorbed_analysis_id: string;
  oh_adsorbed_analysis_id: string;
  o2_reference_analysis_id: string;
  h2o_reference_analysis_id: string;
  h2_reference_analysis_id: string;
}

export interface OERBindings {
  clean_surface_analysis_id: string;
  oh_adsorbed_analysis_id: string;
  o_adsorbed_analysis_id: string;
  ooh_adsorbed_analysis_id: string;
  h2o_reference_analysis_id: string;
  o2_reference_analysis_id: string;
  h2_reference_analysis_id: string;
}

export interface CO2RRBindings {
  clean_surface_analysis_id: string;
  cooh_adsorbed_analysis_id: string;
  co_adsorbed_analysis_id: string;
  co2_reference_analysis_id: string;
  h2o_reference_analysis_id: string;
  co_reference_analysis_id: string;
  h2_reference_analysis_id: string;
}

export type ReactionBindings = HERBindings | ORRBindings | OERBindings | CO2RRBindings;

export interface ReactionRequestInput {
  preset_kind: ReactionPresetKind;
  bindings: ReactionBindings;
  baseline_conditions: ReactionConditions;
  requested_conditions: ReactionConditions;
}

export interface ReactionPreviewPayload {
  project_root: string;
  preset_kind: ReactionPresetKind;
  preset_hash: string;
  pathway_definition: Record<string, unknown>;
  baseline_result: Record<string, unknown>;
  potential_view: PotentialPathwayView;
  descriptor_definitions: ReactionDescriptorDefinition[];
}

export function parseThermochemistryResponse<T>(
  raw: string,
  operation: ThermochemistryOperation,
  requestId: string,
): T {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new Error("Thermochemistry backend response is not valid JSON");
  }
  if (!isRecord(value)) throw new Error("Thermochemistry backend response must be an object");
  if (value.protocol_version !== THERMOCHEMISTRY_IPC_VERSION) {
    throw new Error("Thermochemistry backend protocol version mismatch");
  }
  if (value.request_id !== requestId || value.operation !== operation) {
    throw new Error("Thermochemistry backend response correlation mismatch");
  }
  if (value.ok === false) {
    if (!isRecord(value.error) || typeof value.error.message !== "string") {
      throw new Error("Thermochemistry backend error payload is invalid");
    }
    throw new Error(value.error.message);
  }
  if (value.ok !== true || !isRecord(value.payload)) {
    throw new Error("Thermochemistry backend success payload is invalid");
  }
  return value.payload as T;
}

export function assertThermochemistryCatalog(
  payload: ThermochemistryCatalogPayload,
  projectRoot: string,
): void {
  const value: unknown = payload;
  if (
    !isRecord(value) || value.project_root !== projectRoot ||
    typeof value.project_id !== "string" || typeof value.project_name !== "string" ||
    !Array.isArray(value.frequency_sources) || !Array.isArray(value.gas_reference_registry) ||
    !Array.isArray(value.analyses)
  ) throw new Error("Thermochemistry catalog payload is invalid");
  for (const row of value.frequency_sources) assertFrequencySource(row);
  for (const row of value.analyses) assertAnalysisRow(row);
}

export function assertThermochemistryView(
  payload: ThermochemistryAnalysisViewPayload,
  projectRoot: string,
): void {
  const value: unknown = payload;
  if (
    !isRecord(value) || value.project_root !== projectRoot || typeof value.project_id !== "string" ||
    typeof value.analysis_id !== "string" || typeof value.analysis_type !== "string" ||
    typeof value.artifact_id !== "string" || !isFreshness(value.freshness) || !isRecord(value.view)
  ) throw new Error("Thermochemistry view payload is invalid");
  if (value.analysis_type === "reaction_diagram") assertReactionDiagramView(value.view);
  else assertThermochemistryResultView(value.view);
}

export function assertReactionPreview(payload: ReactionPreviewPayload, projectRoot: string): void {
  const value: unknown = payload;
  if (
    !isRecord(value) || value.project_root !== projectRoot || typeof value.preset_kind !== "string" ||
    typeof value.preset_hash !== "string" || !isRecord(value.potential_view) ||
    !Array.isArray(value.descriptor_definitions)
  ) throw new Error("Reaction preview payload is invalid");
  assertPotentialView(value.potential_view);
  for (const descriptor of value.descriptor_definitions) assertDescriptor(descriptor);
}

export function assertMaterializationReceipt(
  payload: ThermochemistryMaterializationPayload | ReactionDiagramMaterializationPayload,
  projectRoot: string,
): void {
  const value: unknown = payload;
  if (
    !isRecord(value) || value.project_root !== projectRoot || typeof value.analysis_id !== "string" ||
    typeof value.artifact_id !== "string" || typeof value.reused !== "boolean"
  ) throw new Error("Thermochemistry materialization receipt is invalid");
}

function assertFrequencySource(value: unknown): void {
  if (
    !isRecord(value) || typeof value.calculation_id !== "string" ||
    typeof value.calculation_type !== "string" || typeof value.scientific_status !== "string" ||
    typeof value.source_ready !== "boolean" || typeof value.reason !== "string"
  ) throw new Error("Thermochemistry frequency source row is invalid");
  if (value.atoms !== undefined && (!Array.isArray(value.atoms) || value.atoms.some((item) =>
    !isRecord(item) || typeof item.atom_uid !== "string" || typeof item.element !== "string"
  ))) throw new Error("Thermochemistry frequency source atoms are invalid");
}

function assertAnalysisRow(value: unknown): void {
  if (
    !isRecord(value) || typeof value.analysis_id !== "string" ||
    typeof value.analysis_type !== "string" || typeof value.status !== "string" ||
    typeof value.input_artifact_count !== "number" || typeof value.view_supported !== "boolean" ||
    !isFreshness(value.freshness)
  ) throw new Error("Thermochemistry analysis row is invalid");
}

function assertThermochemistryResultView(value: Record<string, unknown>): void {
  if (
    !matches(value.kind, ["harmonic_surface_adsorbate", "ideal_gas_reference", "reference_correction"]) ||
    typeof value.result_hash !== "string" || !isRecord(value.source_receipt) || !isRecord(value.result)
  ) throw new Error("Thermochemistry scientific view is invalid");
}

function assertReactionDiagramView(value: Record<string, unknown>): void {
  if (value.kind !== "reaction_diagram" || typeof value.dataset_hash !== "string" || !isRecord(value.dataset)) {
    throw new Error("Reaction diagram view is invalid");
  }
  const dataset = value.dataset;
  if (!isRecord(dataset.potential_view) || !Array.isArray(dataset.descriptor_definitions)) {
    throw new Error("Reaction diagram dataset is invalid");
  }
  assertPotentialView(dataset.potential_view);
  for (const descriptor of dataset.descriptor_definitions) assertDescriptor(descriptor);
}

function assertPotentialView(value: Record<string, unknown>): void {
  if (
    !Array.isArray(value.state_keys) || !value.state_keys.every((item) => typeof item === "string") ||
    !Array.isArray(value.step_views) || !Array.isArray(value.cumulative_state_free_energies_ev) ||
    !isFiniteNumberArray(value.cumulative_state_free_energies_ev) ||
    value.cumulative_state_free_energies_ev.length !== value.state_keys.length ||
    value.step_views.length !== Math.max(0, value.state_keys.length - 1)
  ) throw new Error("Reaction potential view is invalid");
  for (const step of value.step_views) {
    if (!isRecord(step) || typeof step.step_key !== "string" || !finiteFields(step, [
      "che_coefficient", "potential_slope_ev_per_v", "baseline_delta_g_ev", "target_delta_g_ev",
    ])) throw new Error("Reaction step view is invalid");
  }
}

function assertDescriptor(value: unknown): void {
  if (
    !isRecord(value) || typeof value.key !== "string" || typeof value.kind !== "string" ||
    typeof value.unit !== "string" || typeof value.value !== "number" || !Number.isFinite(value.value)
  ) throw new Error("Reaction descriptor is invalid");
}

function isFreshness(value: unknown): value is ThermochemistryFreshness {
  return isRecord(value) && typeof value.scientific_state === "string" &&
    typeof value.readiness === "string" &&
    (value.freshness_state === null || typeof value.freshness_state === "string") &&
    Array.isArray(value.reason_codes) && value.reason_codes.every((item) => typeof item === "string");
}

function finiteFields(value: Record<string, unknown>, fields: string[]): boolean {
  return fields.every((field) => typeof value[field] === "number" && Number.isFinite(value[field] as number));
}

function isFiniteNumberArray(value: unknown[]): value is number[] {
  return value.every((item) => typeof item === "number" && Number.isFinite(item));
}

function matches(value: unknown, choices: string[]): value is string {
  return typeof value === "string" && choices.includes(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
