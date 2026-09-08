export const CALCULATION_IPC_VERSION = "ecatvasp-desktop-ipc-v2" as const;

export type CalculationTask = "slab" | "adsorbate" | "gas_reference";
export type KPointKind = "explicit_mesh" | "gamma_only" | "reciprocal_density" | "kspacing";
export type KPointCentering = "gamma" | "monkhorst_pack";
export type SpinTreatment = "unpolarized" | "collinear" | "noncollinear";
export type VacuumAxis = "a" | "b" | "c";

export interface CalculationRootSummary {
  structure_snapshot_id: string;
  structure_variant_id: string | null;
  label: string;
  atom_count: number;
  elements: string[];
  eligible_task: CalculationTask;
  is_conformer: boolean;
}

export interface ExistingWorkflowSummary {
  workflow_plan_id: string;
  workflow_recipe_id: string;
  workflow_recipe_version: string;
  root_structure_snapshot_id: string;
  step_count: number;
}

export interface CalculationCatalog {
  project_id: string;
  project_name: string;
  roots: CalculationRootSummary[];
  existing_workflows: ExistingWorkflowSummary[];
  tasks: Array<{
    task: CalculationTask;
    workflow_recipe_id: string;
    workflow_recipe_version: string;
  }>;
  scientific_profile: {
    standard_name: string;
    default_ediffg_ev_per_angstrom: number;
    default_precision: string;
    default_frequency_potim_angstrom: number;
    default_dos_nedos: number;
    numerical_lock_policy: "validated_convergence_evidence_required";
  };
}

export interface CalculationCatalogPayload {
  project_root: string;
  catalog: CalculationCatalog;
}

export interface PotcarSymbolInput {
  element: string;
  symbol: string;
}

export interface WizardMethodInput {
  xc_functional: string;
  potcar_family: string;
  potcar_root: string;
  potcar_symbols: PotcarSymbolInput[];
  spin_treatment?: SpinTreatment;
  engine_version?: string;
  dispersion_model?: string;
}

export interface WizardProtocolInput {
  encut_ev: number;
  kpoint_kind: KPointKind;
  kpoint_mesh?: [number, number, number];
  kpoint_value?: number;
  kpoint_centering?: KPointCentering;
  vacuum_axis?: VacuumAxis;
  precision?: string;
  ediff_ev?: number;
  ediffg_ev_per_angstrom?: number;
  ismear?: number;
  sigma_ev?: number;
  isym?: number;
}

export interface WizardRecipeInput {
  frequency_potim_angstrom?: number;
  frequency_atom_uids?: string[];
  dos_nedos?: number;
  lobster_nbands?: number;
}

export interface PrepareCalculationWorkflowInput {
  task: CalculationTask;
  root_structure_snapshot_id: string;
  method: WizardMethodInput;
  protocol: WizardProtocolInput;
  recipe: WizardRecipeInput;
}

export interface WizardStepSummary {
  step_key: string;
  recipe_id: string;
  calculation_type: string;
  method_fingerprint_id: string | null;
  fingerprint_hash: string | null;
  materialized: boolean;
  blocker_codes: string[];
}

export interface PrepareCalculationWorkflowPayload {
  project_root: string;
  project_id: string;
  workflow_plan_id: string;
  workflow_recipe_id: string;
  root_structure_snapshot_id: string;
  task: CalculationTask;
  system_kind: "slab_2d" | "molecule_0d" | "periodic_3d";
  reused_plan: boolean;
  steps: WizardStepSummary[];
}

export interface EncCutEvidenceInput {
  core_method_hash: string;
  potcar_spec_hash: string;
  tested_encuts_ev: number[];
  selected_encut_ev: number;
  analysis_hash: string;
}

export interface KPointEvidenceInput {
  core_method_hash: string;
  system_kind: "slab_2d" | "molecule_0d" | "periodic_3d";
  tested_plan_hashes: string[];
  selected_plan_hash: string;
  analysis_hash: string;
}

export interface MaterializeCalculationStepInput {
  workflow_plan_id: string;
  step_key: string;
  method_fingerprint_id: string;
  task: CalculationTask;
  method: WizardMethodInput;
  protocol: WizardProtocolInput;
  numerical_evidence: {
    encut: EncCutEvidenceInput;
    kpoints?: KPointEvidenceInput;
  };
}

export interface MaterializeCalculationStepPayload {
  project_root: string;
  project_id: string;
  workflow_plan_id: string;
  step_key: string;
  calculation_id: string;
  workflow_step_binding_id: string;
  reused: boolean;
}

export type CalculationOperation =
  | "calculation_catalog"
  | "prepare_calculation_workflow"
  | "materialize_calculation_step";

interface CalculationSuccess<T> {
  protocol_version: typeof CALCULATION_IPC_VERSION;
  request_id: string;
  operation: CalculationOperation;
  ok: true;
  payload: T;
}

interface CalculationFailure {
  protocol_version: typeof CALCULATION_IPC_VERSION;
  request_id: string;
  operation: CalculationOperation;
  ok: false;
  error: { code: string; message: string };
}

export type CalculationResponse<T> = CalculationSuccess<T> | CalculationFailure;

export function parseCalculationResponse<T>(
  raw: string,
  operation: CalculationOperation,
  requestId: string,
): T {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new Error("calculation backend response is not valid JSON");
  }
  if (!isRecord(value)) throw new Error("calculation backend response must be an object");
  if (value.protocol_version !== CALCULATION_IPC_VERSION) {
    throw new Error("calculation backend protocol version mismatch");
  }
  if (value.request_id !== requestId || value.operation !== operation) {
    throw new Error("calculation backend response correlation mismatch");
  }
  if (value.ok === false) {
    if (!isRecord(value.error) || typeof value.error.message !== "string") {
      throw new Error("calculation backend error payload is invalid");
    }
    throw new Error(value.error.message);
  }
  if (value.ok !== true || !isRecord(value.payload)) {
    throw new Error("calculation backend success payload is invalid");
  }
  return value.payload as T;
}

export function assertCalculationCatalogPayload(
  payload: CalculationCatalogPayload,
  projectRoot: string,
): void {
  const value: unknown = payload;
  if (!isRecord(value) || value.project_root !== projectRoot || !isRecord(value.catalog)) {
    throw new Error("calculation catalog payload is invalid");
  }
  if (!Array.isArray(value.catalog.roots) || !Array.isArray(value.catalog.tasks)) {
    throw new Error("calculation catalog collections are invalid");
  }
}

export function assertPreparationPayload(
  payload: PrepareCalculationWorkflowPayload,
  projectRoot: string,
): void {
  const value: unknown = payload;
  if (!isRecord(value) || value.project_root !== projectRoot || !Array.isArray(value.steps)) {
    throw new Error("calculation preparation payload is invalid");
  }
  if (typeof value.workflow_plan_id !== "string" || typeof value.task !== "string") {
    throw new Error("calculation preparation identity is invalid");
  }
}

export function assertMaterializationPayload(
  payload: MaterializeCalculationStepPayload,
  projectRoot: string,
): void {
  const value: unknown = payload;
  if (
    !isRecord(value) ||
    value.project_root !== projectRoot ||
    typeof value.calculation_id !== "string" ||
    typeof value.workflow_step_binding_id !== "string" ||
    typeof value.reused !== "boolean"
  ) {
    throw new Error("calculation materialization payload is invalid");
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
