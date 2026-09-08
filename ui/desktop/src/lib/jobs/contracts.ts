export const JOB_CENTER_IPC_VERSION = "ecatvasp-desktop-ipc-v2" as const;

export type JobCenterOperation =
  | "job_catalog"
  | "prepare_execution"
  | "submit_slurm_job"
  | "refresh_slurm_job"
  | "cancel_slurm_job"
  | "retrieve_job_outputs";

export interface JobCalculationRow {
  calculation_id: string;
  calculation_type: string;
  recipe_id: string;
  scientific_status: string;
  latest_attempt_id: string | null;
  attempt_status: string | null;
  remote_job_id: string | null;
  scheduler_state: string | null;
  scheduler_job_id: string | null;
}

export interface JobCatalogPayload {
  project_root: string;
  project_id: string;
  project_name: string;
  calculations: JobCalculationRow[];
}

export interface ExecutionSettingsInput {
  nodes: number;
  cores: number;
  mpi_ranks: number;
  walltime_seconds: number;
  executable: string;
  ncore?: number;
  kpar?: number;
  memory_mb?: number;
  partition?: string;
  omp_threads?: number;
}

export interface ExecutionTargetInput {
  target_id: string;
  host_alias: string;
  remote_work_root: string;
  potcar_resolver_id: string;
  vasp_executable: string;
  launcher?: string;
  module_loads: string[];
}

export interface RemotePotcarInput {
  resolver_id: string;
  family: string;
  root: string;
}

export interface PrepareExecutionInput {
  calculation_id: string;
  potcar_root: string;
  execution_settings: ExecutionSettingsInput;
  frequency_atom_uids?: string[];
}

export interface ExpectedOutputSummary {
  role: string;
  artifact_type: string;
  relative_path: string;
  retrieval_policy: string;
  required: boolean;
}

export interface PrepareExecutionPayload {
  project_root: string;
  calculation_id: string;
  workflow_plan_id: string;
  step_key: string;
  plan_hash: string;
  execution_settings_hash: string;
  input_manifest_sha256: string;
  reused_input_artifacts: boolean;
  expected_outputs: ExpectedOutputSummary[];
}

export interface SubmitSlurmJobInput extends PrepareExecutionInput {
  target: ExecutionTargetInput;
  remote_potcar: RemotePotcarInput;
}

export interface SubmitSlurmJobPayload {
  project_root: string;
  calculation_id: string;
  attempt_id: string;
  remote_job_id: string;
  attempt_status: string;
  scheduler_state: string;
  scheduler_job_id: string;
  plan_hash: string;
}

export interface RemoteJobInput {
  remote_job_id: string;
  target: ExecutionTargetInput;
}

export interface JobObservationPayload {
  project_root: string;
  calculation_id: string;
  attempt_id: string;
  remote_job_id: string;
  attempt_status: string;
  scheduler_state: string;
  ionic_step: number | null;
  electronic_iteration: number | null;
}

export interface RetrieveJobOutputsInput extends RemoteJobInput {
  requested_roles?: string[];
  release_remote_roles?: string[];
  discard_remote_roles?: string[];
}

export interface RetrieveJobOutputsPayload {
  project_root: string;
  calculation_id: string;
  attempt_id: string;
  remote_job_id: string;
  attempt_status: string;
  retrieved_artifact_ids: string[];
  retrieval_hash: string;
}

interface JobCenterSuccess<T> {
  protocol_version: typeof JOB_CENTER_IPC_VERSION;
  request_id: string;
  operation: JobCenterOperation;
  ok: true;
  payload: T;
}

interface JobCenterFailure {
  protocol_version: typeof JOB_CENTER_IPC_VERSION;
  request_id: string;
  operation: JobCenterOperation;
  ok: false;
  error: { code: string; message: string };
}

export type JobCenterResponse<T> = JobCenterSuccess<T> | JobCenterFailure;

export function parseJobCenterResponse<T>(
  raw: string,
  operation: JobCenterOperation,
  requestId: string,
): T {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new Error("Job Center backend response is not valid JSON");
  }
  if (!isRecord(value)) throw new Error("Job Center backend response must be an object");
  if (value.protocol_version !== JOB_CENTER_IPC_VERSION) {
    throw new Error("Job Center backend protocol version mismatch");
  }
  if (value.request_id !== requestId || value.operation !== operation) {
    throw new Error("Job Center backend response correlation mismatch");
  }
  if (value.ok === false) {
    if (!isRecord(value.error) || typeof value.error.message !== "string") {
      throw new Error("Job Center backend error payload is invalid");
    }
    throw new Error(value.error.message);
  }
  if (value.ok !== true || !isRecord(value.payload)) {
    throw new Error("Job Center backend success payload is invalid");
  }
  return value.payload as T;
}

export function assertJobCatalogPayload(payload: JobCatalogPayload, projectRoot: string): void {
  const value: unknown = payload;
  if (
    !isRecord(value) ||
    value.project_root !== projectRoot ||
    typeof value.project_id !== "string" ||
    typeof value.project_name !== "string" ||
    !Array.isArray(value.calculations)
  ) {
    throw new Error("Job Center catalog payload is invalid");
  }
  for (const row of value.calculations) {
    if (
      !isRecord(row) ||
      typeof row.calculation_id !== "string" ||
      typeof row.scientific_status !== "string"
    ) {
      throw new Error("Job Center calculation row is invalid");
    }
  }
}

export function assertPrepareExecutionPayload(
  payload: PrepareExecutionPayload,
  projectRoot: string,
): void {
  const value: unknown = payload;
  if (
    !isRecord(value) ||
    value.project_root !== projectRoot ||
    typeof value.calculation_id !== "string" ||
    typeof value.plan_hash !== "string" ||
    typeof value.reused_input_artifacts !== "boolean" ||
    !Array.isArray(value.expected_outputs)
  ) {
    throw new Error("Job Center execution preparation payload is invalid");
  }
}

export function assertSubmissionPayload(
  payload: SubmitSlurmJobPayload,
  projectRoot: string,
): void {
  const value: unknown = payload;
  if (
    !isRecord(value) ||
    value.project_root !== projectRoot ||
    typeof value.calculation_id !== "string" ||
    typeof value.attempt_id !== "string" ||
    typeof value.remote_job_id !== "string" ||
    typeof value.scheduler_job_id !== "string"
  ) {
    throw new Error("Job Center submission payload is invalid");
  }
}

export function assertObservationPayload(
  payload: JobObservationPayload,
  projectRoot: string,
): void {
  const value: unknown = payload;
  if (
    !isRecord(value) ||
    value.project_root !== projectRoot ||
    typeof value.remote_job_id !== "string" ||
    typeof value.attempt_status !== "string" ||
    typeof value.scheduler_state !== "string"
  ) {
    throw new Error("Job Center observation payload is invalid");
  }
}

export function assertRetrievalPayload(
  payload: RetrieveJobOutputsPayload,
  projectRoot: string,
): void {
  const value: unknown = payload;
  if (
    !isRecord(value) ||
    value.project_root !== projectRoot ||
    typeof value.remote_job_id !== "string" ||
    typeof value.retrieval_hash !== "string" ||
    !Array.isArray(value.retrieved_artifact_ids)
  ) {
    throw new Error("Job Center retrieval payload is invalid");
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
