export const RESULT_CENTER_IPC_VERSION = "ecatvasp-desktop-ipc-v2" as const;

export type ResultCenterOperation =
  | "result_catalog"
  | "analyze_result"
  | "promote_result_structure";

export interface ResultCalculationRow {
  calculation_id: string;
  calculation_type: string;
  recipe_id: string;
  scientific_status: string;
  latest_attempt_id: string | null;
  attempt_status: string | null;
  retrieved_artifact_types: string[];
  analyzed: boolean;
  analysis_ready: boolean;
  analysis_readiness_reason: string;
  promotion_ready: boolean;
  promotion_variant_id: string | null;
}

export interface ResultCatalogPayload {
  project_root: string;
  project_id: string;
  project_name: string;
  calculations: ResultCalculationRow[];
}

export interface AnalyzeResultPayload {
  project_root: string;
  calculation_id: string;
  attempt_id: string;
  intake_hash: string;
  scientific_verdict: string;
  electronic_verdict: string;
  ionic_verdict: string;
  free_energy_toten_ev: number | null;
  energy_without_entropy_ev: number | null;
  energy_sigma0_ev: number | null;
  fermi_energy_ev: number | null;
  ionic_steps: number | null;
  electronic_steps: number | null;
  termination_observed: boolean | null;
  evidence_codes: string[];
  force_count: number;
  frequency_mode_count: number;
}

export interface PromoteResultInput {
  calculation_id: string;
  label?: string;
}

export interface PromoteResultPayload {
  project_root: string;
  calculation_id: string;
  structure_variant_id: string;
  promoted_structure_snapshot_id: string;
  parent_structure_snapshot_id: string;
  scientific_verdict: string;
  source_artifact_id: string;
  source_sha256: string;
}

interface ResultCenterSuccess<T> {
  protocol_version: typeof RESULT_CENTER_IPC_VERSION;
  request_id: string;
  operation: ResultCenterOperation;
  ok: true;
  payload: T;
}

interface ResultCenterFailure {
  protocol_version: typeof RESULT_CENTER_IPC_VERSION;
  request_id: string;
  operation: ResultCenterOperation;
  ok: false;
  error: { code: string; message: string };
}

export type ResultCenterResponse<T> = ResultCenterSuccess<T> | ResultCenterFailure;

export function parseResultCenterResponse<T>(
  raw: string,
  operation: ResultCenterOperation,
  requestId: string,
): T {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new Error("Result Center backend response is not valid JSON");
  }
  if (!isRecord(value)) throw new Error("Result Center backend response must be an object");
  if (value.protocol_version !== RESULT_CENTER_IPC_VERSION) {
    throw new Error("Result Center backend protocol version mismatch");
  }
  if (value.request_id !== requestId || value.operation !== operation) {
    throw new Error("Result Center backend response correlation mismatch");
  }
  if (value.ok === false) {
    if (!isRecord(value.error) || typeof value.error.message !== "string") {
      throw new Error("Result Center backend error payload is invalid");
    }
    throw new Error(value.error.message);
  }
  if (value.ok !== true || !isRecord(value.payload)) {
    throw new Error("Result Center backend success payload is invalid");
  }
  return value.payload as T;
}

export function assertResultCatalogPayload(
  payload: ResultCatalogPayload,
  projectRoot: string,
): void {
  const value: unknown = payload;
  if (
    !isRecord(value) ||
    value.project_root !== projectRoot ||
    typeof value.project_id !== "string" ||
    typeof value.project_name !== "string" ||
    !Array.isArray(value.calculations)
  ) {
    throw new Error("Result Center catalog payload is invalid");
  }
  for (const row of value.calculations) {
    if (
      !isRecord(row) ||
      typeof row.calculation_id !== "string" ||
      typeof row.scientific_status !== "string" ||
      typeof row.analysis_ready !== "boolean" ||
      typeof row.promotion_ready !== "boolean" ||
      !Array.isArray(row.retrieved_artifact_types)
    ) {
      throw new Error("Result Center calculation row is invalid");
    }
  }
}

export function assertAnalyzeResultPayload(
  payload: AnalyzeResultPayload,
  projectRoot: string,
): void {
  const value: unknown = payload;
  if (
    !isRecord(value) ||
    value.project_root !== projectRoot ||
    typeof value.calculation_id !== "string" ||
    typeof value.attempt_id !== "string" ||
    typeof value.intake_hash !== "string" ||
    typeof value.scientific_verdict !== "string" ||
    typeof value.force_count !== "number" ||
    typeof value.frequency_mode_count !== "number" ||
    !Array.isArray(value.evidence_codes)
  ) {
    throw new Error("Result Center analysis payload is invalid");
  }
}

export function assertPromoteResultPayload(
  payload: PromoteResultPayload,
  projectRoot: string,
): void {
  const value: unknown = payload;
  if (
    !isRecord(value) ||
    value.project_root !== projectRoot ||
    typeof value.calculation_id !== "string" ||
    typeof value.structure_variant_id !== "string" ||
    typeof value.promoted_structure_snapshot_id !== "string" ||
    typeof value.parent_structure_snapshot_id !== "string" ||
    typeof value.scientific_verdict !== "string"
  ) {
    throw new Error("Result Center promotion payload is invalid");
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
