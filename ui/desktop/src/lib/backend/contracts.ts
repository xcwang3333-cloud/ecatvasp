export const DESKTOP_IPC_CONTRACT_VERSION = "ecatvasp-desktop-ipc-v1" as const;
export const FRONTEND_HANDOFF_CONTRACT_VERSION = "ecatvasp-frontend-handoff-v1" as const;

export const DESKTOP_OPERATIONS = [
  "health",
  "open_project",
  "status",
  "frontend_handoff",
  "application_report",
  "prepare_workflow",
] as const;

export type DesktopOperation = (typeof DESKTOP_OPERATIONS)[number];
export type ProjectDesktopOperation = Exclude<DesktopOperation, "health">;
export type ReportFormat = "json" | "csv" | "markdown";

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonObject | JsonValue[];
export interface JsonObject {
  [key: string]: JsonValue;
}

export interface DesktopRequest {
  protocol_version: typeof DESKTOP_IPC_CONTRACT_VERSION;
  request_id: string;
  operation: DesktopOperation;
  project_root?: string;
  report_format?: ReportFormat;
  workflow_recipe_id?: string;
  workflow_recipe_version?: string;
  root_structure_snapshot_id?: string;
  parameters_hash?: string;
}

export interface DesktopErrorPayload {
  code: string;
  message: string;
}

export interface DesktopSuccessResponse<TPayload> {
  protocol_version: typeof DESKTOP_IPC_CONTRACT_VERSION;
  request_id: string;
  operation: DesktopOperation;
  ok: true;
  payload: TPayload;
}

export interface DesktopFailureResponse {
  protocol_version: typeof DESKTOP_IPC_CONTRACT_VERSION;
  request_id: string;
  operation: DesktopOperation;
  ok: false;
  error: DesktopErrorPayload;
}

export type DesktopResponse<TPayload> =
  | DesktopSuccessResponse<TPayload>
  | DesktopFailureResponse;

export interface WorkflowRecipeSummary {
  recipe_id: string;
  version: string;
  description: string | null;
}

export interface HealthPayload {
  backend_version: string;
  frontend_handoff_contract_version: typeof FRONTEND_HANDOFF_CONTRACT_VERSION;
  operations: DesktopOperation[];
  stateless_project_requests: true;
  workflow_recipes: WorkflowRecipeSummary[];
}

export interface OpenProjectPayload {
  project_root: string;
  project_id: string;
  project_name: string;
  project_slug: string;
  schema_version: number;
}

export type CountPair = [string, number];

export interface StatusPayload extends OpenProjectPayload {
  projection_hash: string;
  calculations: CountPair[];
  analyses: CountPair[];
  execution_attempts: CountPair[];
  scheduler_jobs: CountPair[];
  freshness: CountPair[];
  attention_rows: number;
}

export interface FrontendCapability {
  name: string;
  contract_version: string;
}

export interface FrontendHandoffV1 {
  contract_version: typeof FRONTEND_HANDOFF_CONTRACT_VERSION;
  backend_version: string;
  capabilities: FrontendCapability[];
  matterviz_target_version: string;
  report: JsonObject;
  handoff_hash: string;
}

export interface FrontendHandoffPayload {
  project_root: string;
  handoff: FrontendHandoffV1;
}

export interface ApplicationReportPayload {
  project_root: string;
  project_id: string;
  report_format: ReportFormat;
  report_contract_version: string;
  report_hash: string;
  content_sha256: string;
  content: string;
}

export interface PrepareWorkflowInput {
  workflow_recipe_id: string;
  workflow_recipe_version: string;
  root_structure_snapshot_id: string;
  parameters_hash?: string;
}

export interface PrepareWorkflowPayload {
  project_root: string;
  project_id: string;
  workflow_plan_id: string;
  workflow_recipe_id: string;
  workflow_recipe_version: string;
  root_structure_snapshot_id: string;
  plan_hash: string;
  planning_hash: string;
  reused: boolean;
}

export const BACKEND_RUNTIME_STATES = [
  "not_started",
  "starting",
  "ready",
  "exited",
  "unavailable",
] as const;
export type BackendRuntimeState = (typeof BACKEND_RUNTIME_STATES)[number];

export const BACKEND_FAILURE_KINDS = [
  "spawn",
  "transport",
  "compatibility",
  "shutdown",
] as const;
export type BackendFailureKind = (typeof BACKEND_FAILURE_KINDS)[number];

export interface BackendRuntimeDiagnostics {
  backend_state: BackendRuntimeState;
  restart_count: number;
  last_failure_kind: BackendFailureKind | null;
}

export interface ReportExportReceipt {
  file_name: string;
  report_format: ReportFormat;
  content_sha256: string;
  bytes_written: number;
  reused: boolean;
}

export class DesktopContractError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DesktopContractError";
  }
}

export class DesktopBackendError extends Error {
  readonly code: string;

  constructor(error: DesktopErrorPayload) {
    super(error.message);
    this.name = "DesktopBackendError";
    this.code = error.code;
  }
}

export function parseDesktopResponse<TPayload>(
  raw: string,
  expectedOperation: DesktopOperation,
  expectedRequestId?: string,
): DesktopResponse<TPayload> {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new DesktopContractError("desktop backend response is not valid JSON");
  }

  if (!isRecord(value)) {
    throw new DesktopContractError("desktop backend response must be an object");
  }
  if (value.protocol_version !== DESKTOP_IPC_CONTRACT_VERSION) {
    throw new DesktopContractError("unsupported desktop IPC contract version");
  }
  if (value.operation !== expectedOperation) {
    throw new DesktopContractError("desktop backend response operation mismatch");
  }
  if (typeof value.request_id !== "string" || value.request_id.length === 0) {
    throw new DesktopContractError("desktop backend response request_id is invalid");
  }
  if (expectedRequestId !== undefined && value.request_id !== expectedRequestId) {
    throw new DesktopContractError("desktop backend response request_id mismatch");
  }
  if (typeof value.ok !== "boolean") {
    throw new DesktopContractError("desktop backend response ok flag is invalid");
  }

  if (value.ok) {
    if (!isRecord(value.payload) || "error" in value) {
      throw new DesktopContractError("successful desktop response payload is invalid");
    }
    return value as unknown as DesktopSuccessResponse<TPayload>;
  }

  if (!isRecord(value.error) || "payload" in value) {
    throw new DesktopContractError("failed desktop response error is invalid");
  }
  if (typeof value.error.code !== "string" || typeof value.error.message !== "string") {
    throw new DesktopContractError("failed desktop response error fields are invalid");
  }
  return value as unknown as DesktopFailureResponse;
}

export function requireSuccess<TPayload>(
  response: DesktopResponse<TPayload>,
): DesktopSuccessResponse<TPayload> {
  if (!response.ok) {
    throw new DesktopBackendError(response.error);
  }
  return response;
}

export function assertHealthCompatibility(
  response: DesktopSuccessResponse<HealthPayload>,
): void {
  const payload: unknown = response.payload;
  if (!isRecord(payload)) {
    throw new DesktopContractError("desktop backend health payload is invalid");
  }
  if (typeof payload.backend_version !== "string") {
    throw new DesktopContractError("desktop backend version is invalid");
  }
  if (!payload.backend_version.startsWith("1.")) {
    throw new DesktopContractError("unsupported backend package major version");
  }
  if (payload.frontend_handoff_contract_version !== FRONTEND_HANDOFF_CONTRACT_VERSION) {
    throw new DesktopContractError("unsupported frontend handoff contract version");
  }
  if (payload.stateless_project_requests !== true) {
    throw new DesktopContractError("desktop backend must advertise stateless project requests");
  }
  if (
    !Array.isArray(payload.operations) ||
    !payload.operations.every((operation) => typeof operation === "string")
  ) {
    throw new DesktopContractError("desktop backend operations are invalid");
  }
  for (const operation of DESKTOP_OPERATIONS) {
    if (!payload.operations.includes(operation)) {
      throw new DesktopContractError(`desktop backend is missing operation: ${operation}`);
    }
  }
  if (!Array.isArray(payload.workflow_recipes) || payload.workflow_recipes.length === 0) {
    throw new DesktopContractError("desktop backend workflow recipes are invalid");
  }
  const seen = new Set<string>();
  for (const recipe of payload.workflow_recipes) {
    if (!isRecord(recipe)) {
      throw new DesktopContractError("desktop workflow recipe is invalid");
    }
    if (
      typeof recipe.recipe_id !== "string" ||
      recipe.recipe_id.trim().length === 0 ||
      typeof recipe.version !== "string" ||
      recipe.version.trim().length === 0 ||
      !(recipe.description === null || typeof recipe.description === "string")
    ) {
      throw new DesktopContractError("desktop workflow recipe fields are invalid");
    }
    const key = `${recipe.recipe_id}@${recipe.version}`;
    if (seen.has(key)) {
      throw new DesktopContractError("desktop workflow recipe identities must be unique");
    }
    seen.add(key);
  }
}

export function assertFrontendHandoffCompatibility(handoff: FrontendHandoffV1): void {
  if (handoff.contract_version !== FRONTEND_HANDOFF_CONTRACT_VERSION) {
    throw new DesktopContractError("unsupported frontend handoff contract version");
  }
}

export function assertApplicationReportPayload(
  payload: ApplicationReportPayload,
  expectedProjectRoot: string,
  expectedFormat: ReportFormat,
): void {
  const value: unknown = payload;
  if (!isRecord(value)) {
    throw new DesktopContractError("application report receipt is invalid");
  }
  if (value.project_root !== expectedProjectRoot) {
    throw new DesktopContractError("application report receipt project root mismatch");
  }
  if (typeof value.project_id !== "string" || value.project_id.length === 0) {
    throw new DesktopContractError("application report receipt project id is invalid");
  }
  if (value.report_format !== expectedFormat) {
    throw new DesktopContractError("application report receipt format mismatch");
  }
  if (
    typeof value.report_contract_version !== "string" ||
    value.report_contract_version.length === 0
  ) {
    throw new DesktopContractError("application report contract version is invalid");
  }
  requireSha256(value.report_hash, "application report hash");
  requireSha256(value.content_sha256, "application report content hash");
  if (typeof value.content !== "string") {
    throw new DesktopContractError("application report content is invalid");
  }
}

export function assertPrepareWorkflowPayload(
  payload: PrepareWorkflowPayload,
  expectedProjectRoot: string,
  input: PrepareWorkflowInput,
): void {
  const value: unknown = payload;
  if (!isRecord(value)) {
    throw new DesktopContractError("prepare workflow receipt is invalid");
  }
  if (value.project_root !== expectedProjectRoot) {
    throw new DesktopContractError("prepare workflow receipt project root mismatch");
  }
  for (const [field, item] of [
    ["project id", value.project_id],
    ["workflow plan id", value.workflow_plan_id],
  ] as const) {
    if (typeof item !== "string" || item.length === 0) {
      throw new DesktopContractError(`prepare workflow receipt ${field} is invalid`);
    }
  }
  if (value.workflow_recipe_id !== input.workflow_recipe_id) {
    throw new DesktopContractError("prepare workflow receipt recipe id mismatch");
  }
  if (value.workflow_recipe_version !== input.workflow_recipe_version) {
    throw new DesktopContractError("prepare workflow receipt recipe version mismatch");
  }
  if (value.root_structure_snapshot_id !== input.root_structure_snapshot_id) {
    throw new DesktopContractError("prepare workflow receipt root snapshot mismatch");
  }
  requireSha256(value.plan_hash, "prepare workflow plan hash");
  requireSha256(value.planning_hash, "prepare workflow planning hash");
  if (typeof value.reused !== "boolean") {
    throw new DesktopContractError("prepare workflow receipt reused flag is invalid");
  }
}

export function parseBackendRuntimeDiagnostics(raw: string): BackendRuntimeDiagnostics {
  const value = parseLocalRuntimeJson(raw, "desktop backend diagnostics");
  requireExactKeys(value, ["backend_state", "restart_count", "last_failure_kind"]);
  if (
    typeof value.backend_state !== "string" ||
    !BACKEND_RUNTIME_STATES.includes(value.backend_state as BackendRuntimeState)
  ) {
    throw new DesktopContractError("desktop backend diagnostic state is invalid");
  }
  if (
    typeof value.restart_count !== "number" ||
    !Number.isSafeInteger(value.restart_count) ||
    value.restart_count < 0
  ) {
    throw new DesktopContractError("desktop backend restart count is invalid");
  }
  if (
    value.last_failure_kind !== null &&
    (typeof value.last_failure_kind !== "string" ||
      !BACKEND_FAILURE_KINDS.includes(value.last_failure_kind as BackendFailureKind))
  ) {
    throw new DesktopContractError("desktop backend failure category is invalid");
  }
  return value as unknown as BackendRuntimeDiagnostics;
}

export function parseReportExportReceipt(
  raw: string,
  report: ApplicationReportPayload,
): ReportExportReceipt {
  const value = parseLocalRuntimeJson(raw, "desktop report export receipt");
  requireExactKeys(value, [
    "file_name",
    "report_format",
    "content_sha256",
    "bytes_written",
    "reused",
  ]);
  if (value.report_format !== report.report_format) {
    throw new DesktopContractError("desktop report export format mismatch");
  }
  if (value.content_sha256 !== report.content_sha256) {
    throw new DesktopContractError("desktop report export content hash mismatch");
  }
  const extension =
    report.report_format === "markdown" ? "md" : report.report_format;
  const expectedFileName = `ecatvasp-report-${report.content_sha256}.${extension}`;
  if (value.file_name !== expectedFileName) {
    throw new DesktopContractError("desktop report export filename is invalid");
  }
  const expectedBytes = new TextEncoder().encode(report.content).byteLength;
  if (value.bytes_written !== expectedBytes) {
    throw new DesktopContractError("desktop report export byte count mismatch");
  }
  if (typeof value.reused !== "boolean") {
    throw new DesktopContractError("desktop report export reuse flag is invalid");
  }
  return value as unknown as ReportExportReceipt;
}

function parseLocalRuntimeJson(raw: string, label: string): Record<string, unknown> {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new DesktopContractError(`${label} is not valid JSON`);
  }
  if (!isRecord(value)) {
    throw new DesktopContractError(`${label} must be an object`);
  }
  return value;
}

function requireExactKeys(value: Record<string, unknown>, expected: string[]): void {
  const actual = Object.keys(value).sort();
  const wanted = [...expected].sort();
  if (actual.length !== wanted.length || actual.some((item, index) => item !== wanted[index])) {
    throw new DesktopContractError("desktop local runtime payload contains unexpected fields");
  }
}

function requireSha256(value: unknown, fieldName: string): void {
  if (typeof value !== "string" || !/^[0-9a-fA-F]{64}$/.test(value)) {
    throw new DesktopContractError(`${fieldName} is invalid`);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
