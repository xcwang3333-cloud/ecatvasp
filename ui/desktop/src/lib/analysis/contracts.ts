export const ELECTRONIC_ANALYSIS_IPC_VERSION = "ecatvasp-desktop-ipc-v2" as const;

export type ElectronicAnalysisOperation =
  | "electronic_analysis_catalog"
  | "materialize_dos_analysis"
  | "electronic_analysis_view"
  | "materialize_band_center";

export interface ElectronicFreshness {
  scientific_state: string;
  readiness: string;
  freshness_state: string | null;
  reason_codes: string[];
}

export interface DosSourceRow {
  calculation_id: string;
  scientific_status: string;
  latest_attempt_id: string | null;
  latest_attempt_status: string | null;
  materialization_ready: boolean;
  materialized_analysis_id: string | null;
  reason: string;
}

export interface ElectronicAnalysisRow {
  analysis_id: string;
  analysis_type: string;
  status: string;
  tool: string | null;
  tool_version: string | null;
  input_artifact_count: number;
  freshness: ElectronicFreshness;
  view_supported: boolean;
}

export interface ElectronicAnalysisCatalogPayload {
  project_root: string;
  project_id: string;
  project_name: string;
  dos_sources: DosSourceRow[];
  analyses: ElectronicAnalysisRow[];
}

export interface ElectronicMaterializationPayload {
  project_root: string;
  analysis_id: string;
  artifact_id: string;
  reused: boolean;
  calculation_id?: string;
  source_analysis_id?: string;
}

export interface DosOrbital {
  label: string;
  angular_momentum: number;
}

export interface DosSeries {
  scope: "system" | "atom" | "element";
  spin: "total" | "up" | "down";
  atom_uid: string | null;
  element: string | null;
  orbital: DosOrbital | null;
  values: number[];
}

export interface DosAnalysisView {
  kind: "dos";
  structure_snapshot_id: string;
  source_artifact_id: string;
  energy_reference: string;
  fermi_energy_ev: number;
  energies_ev_native: number[];
  energies_ev_relative_to_fermi: number[];
  series: DosSeries[];
  display_contract: {
    native_axis_is_canonical: true;
    fermi_relative_axis_is_explicit_transform: true;
    spin_down_may_be_mirrored_for_display: true;
  };
}

export interface BaderSite {
  atom_uid: string;
  electron_count: number;
  min_distance_angstrom: number;
  basin_volume_angstrom3: number;
}

export interface BaderAnalysisView {
  kind: "bader";
  structure_snapshot_id: string;
  acf_artifact_id: string;
  source_artifact_id: string;
  reference_mode: string;
  number_of_electrons: number;
  vacuum_charge_e: number | null;
  vacuum_volume_angstrom3: number | null;
  sites: BaderSite[];
  interpretation_contract: "raw_basin_facts_no_oxidation_state_inference";
}

export interface ChargeDifferenceAnalysisView {
  kind: "charge_difference";
  density_artifact_id: string;
  metadata_artifact_id: string;
  grid_shape_xyz: [number, number, number];
  cell_volume_angstrom3: number;
  voxel_volume_angstrom3: number;
  density_unit: string;
  axis_order: string;
  dtype: string;
  delta_convention: string;
  combined_electron_integral: number;
  slab_electron_integral: number;
  adsorbate_electron_integral: number;
  delta_electron_integral: number;
  density_min: number;
  density_max: number;
  volume_payload_included: false;
}

export interface CohpSpinSeries {
  spin: "total" | "up" | "down";
  cohp_values_native: number[];
  negative_cohp_values: number[];
  icohp_values_native: number[];
  icohp_at_fermi_ev_native: number | null;
}

export interface CohpInteraction {
  source_index: number;
  source_label: string;
  atom_uid_a: string;
  atom_uid_b: string;
  element_a: string;
  element_b: string;
  bond_length_angstrom: number;
  cell_a: number[] | null;
  cell_b: number[] | null;
  orbital_a: string | null;
  orbital_b: string | null;
  series: CohpSpinSeries[];
}

export interface CohpAnalysisView {
  kind: "cohp";
  structure_snapshot_id: string;
  cohpcar_artifact_id: string;
  icohplist_artifact_id: string;
  source_artifact_id: string;
  energy_reference: string;
  source_fermi_energy_ev: number;
  energies_ev_relative_to_fermi: number[];
  average_series: CohpSpinSeries[];
  interactions: CohpInteraction[];
  display_contract: {
    native_cohp_is_canonical: true;
    negative_cohp_is_explicit_transform: true;
  };
}

export interface BandCenterAnalysisView {
  kind: "band_center";
  source_analysis_id: string;
  source_artifact_id: string;
  result_artifact_id: string;
  structure_snapshot_id: string;
  descriptor_kind: "band" | "p_band" | "d_band";
  center_ev: number;
  zeroth_moment_states: number;
  first_moment_ev_states: number;
  quadrature_point_count: number;
  contributing_series_count: number;
  selector: {
    scope: "system" | "atom" | "element";
    spin: "total" | "up" | "down" | "sum";
    atom_uid: string | null;
    element: string | null;
  };
  energy_reference: "vasp_native" | "fermi_relative";
  window_lower_ev: number;
  window_upper_ev: number;
  integration_rule: string;
  normalization: string;
}

export type ElectronicAnalysisView =
  | DosAnalysisView
  | BaderAnalysisView
  | ChargeDifferenceAnalysisView
  | CohpAnalysisView
  | BandCenterAnalysisView;

export interface ElectronicAnalysisViewPayload {
  project_root: string;
  project_id: string;
  analysis_id: string;
  analysis_type: string;
  analysis_status: string;
  tool: string | null;
  tool_version: string | null;
  freshness: ElectronicFreshness;
  view: ElectronicAnalysisView;
}

export interface BandCenterInput {
  source_analysis_id: string;
  kind: "band" | "p_band" | "d_band";
  scope: "system" | "atom" | "element";
  spin: "total" | "up" | "down" | "sum";
  atom_uid: string | null;
  element: string | null;
  energy_reference: "vasp_native" | "fermi_relative";
  window_lower_ev: number;
  window_upper_ev: number;
}

export function parseElectronicAnalysisResponse<T>(
  raw: string,
  operation: ElectronicAnalysisOperation,
  requestId: string,
): T {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new Error("Electronic Analysis backend response is not valid JSON");
  }
  if (!isRecord(value)) throw new Error("Electronic Analysis backend response must be an object");
  if (value.protocol_version !== ELECTRONIC_ANALYSIS_IPC_VERSION) {
    throw new Error("Electronic Analysis backend protocol version mismatch");
  }
  if (value.request_id !== requestId || value.operation !== operation) {
    throw new Error("Electronic Analysis backend response correlation mismatch");
  }
  if (value.ok === false) {
    if (!isRecord(value.error) || typeof value.error.message !== "string") {
      throw new Error("Electronic Analysis backend error payload is invalid");
    }
    throw new Error(value.error.message);
  }
  if (value.ok !== true || !isRecord(value.payload)) {
    throw new Error("Electronic Analysis backend success payload is invalid");
  }
  return value.payload as T;
}

export function assertElectronicCatalogPayload(
  payload: ElectronicAnalysisCatalogPayload,
  projectRoot: string,
): void {
  const value: unknown = payload;
  if (
    !isRecord(value) ||
    value.project_root !== projectRoot ||
    typeof value.project_id !== "string" ||
    typeof value.project_name !== "string" ||
    !Array.isArray(value.dos_sources) ||
    !Array.isArray(value.analyses)
  ) {
    throw new Error("Electronic Analysis catalog payload is invalid");
  }
  for (const row of value.dos_sources) assertDosSourceRow(row);
  for (const row of value.analyses) assertAnalysisRow(row);
}

export function assertMaterializationPayload(
  payload: ElectronicMaterializationPayload,
  projectRoot: string,
): void {
  const value: unknown = payload;
  if (
    !isRecord(value) ||
    value.project_root !== projectRoot ||
    typeof value.analysis_id !== "string" ||
    typeof value.artifact_id !== "string" ||
    typeof value.reused !== "boolean"
  ) {
    throw new Error("Electronic Analysis materialization payload is invalid");
  }
}

export function assertElectronicAnalysisViewPayload(
  payload: ElectronicAnalysisViewPayload,
  projectRoot: string,
): void {
  const value: unknown = payload;
  if (
    !isRecord(value) ||
    value.project_root !== projectRoot ||
    typeof value.project_id !== "string" ||
    typeof value.analysis_id !== "string" ||
    typeof value.analysis_type !== "string" ||
    typeof value.analysis_status !== "string" ||
    !isFreshness(value.freshness) ||
    !isRecord(value.view)
  ) {
    throw new Error("Electronic Analysis view payload is invalid");
  }
  assertView(value.view);
}

function assertDosSourceRow(value: unknown): void {
  if (
    !isRecord(value) ||
    typeof value.calculation_id !== "string" ||
    typeof value.scientific_status !== "string" ||
    typeof value.materialization_ready !== "boolean" ||
    typeof value.reason !== "string"
  ) {
    throw new Error("Electronic Analysis DOS source row is invalid");
  }
}

function assertAnalysisRow(value: unknown): void {
  if (
    !isRecord(value) ||
    typeof value.analysis_id !== "string" ||
    typeof value.analysis_type !== "string" ||
    typeof value.status !== "string" ||
    typeof value.input_artifact_count !== "number" ||
    typeof value.view_supported !== "boolean" ||
    !isFreshness(value.freshness)
  ) {
    throw new Error("Electronic Analysis row is invalid");
  }
}

function assertView(value: Record<string, unknown>): void {
  switch (value.kind) {
    case "dos":
      if (
        !Array.isArray(value.energies_ev_native) ||
        !isNumberArray(value.energies_ev_native) ||
        !Array.isArray(value.energies_ev_relative_to_fermi) ||
        !isNumberArray(value.energies_ev_relative_to_fermi) ||
        !Array.isArray(value.series)
      ) {
        throw new Error("Electronic Analysis DOS view is invalid");
      }
      return;
    case "bader":
      if (!Array.isArray(value.sites) || typeof value.number_of_electrons !== "number") {
        throw new Error("Electronic Analysis Bader view is invalid");
      }
      return;
    case "charge_difference":
      if (
        !Array.isArray(value.grid_shape_xyz) ||
        value.grid_shape_xyz.length !== 3 ||
        typeof value.density_min !== "number" ||
        typeof value.density_max !== "number" ||
        value.volume_payload_included !== false
      ) {
        throw new Error("Electronic Analysis charge-difference view is invalid");
      }
      return;
    case "cohp":
      if (
        !Array.isArray(value.energies_ev_relative_to_fermi) ||
        !isNumberArray(value.energies_ev_relative_to_fermi) ||
        !Array.isArray(value.average_series) ||
        !Array.isArray(value.interactions)
      ) {
        throw new Error("Electronic Analysis COHP view is invalid");
      }
      return;
    case "band_center":
      if (
        typeof value.center_ev !== "number" ||
        typeof value.descriptor_kind !== "string" ||
        !isRecord(value.selector)
      ) {
        throw new Error("Electronic Analysis band-center view is invalid");
      }
      return;
    default:
      throw new Error("Electronic Analysis view kind is unsupported");
  }
}

function isFreshness(value: unknown): value is ElectronicFreshness {
  return (
    isRecord(value) &&
    typeof value.scientific_state === "string" &&
    typeof value.readiness === "string" &&
    (value.freshness_state === null || typeof value.freshness_state === "string") &&
    Array.isArray(value.reason_codes) &&
    value.reason_codes.every((item) => typeof item === "string")
  );
}

function isNumberArray(value: unknown[]): value is number[] {
  return value.every((item) => typeof item === "number" && Number.isFinite(item));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
