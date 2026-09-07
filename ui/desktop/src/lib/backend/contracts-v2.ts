import {
  DesktopBackendError,
  DesktopContractError,
  FRONTEND_HANDOFF_CONTRACT_VERSION,
  type ApplicationReportPayload,
  type FrontendHandoffPayload,
  type JsonObject,
  type OpenProjectPayload,
  type PrepareWorkflowInput,
  type PrepareWorkflowPayload,
  type ReportFormat,
  type StatusPayload,
} from "./contracts";

export const DESKTOP_IPC_V2_CONTRACT_VERSION = "ecatvasp-desktop-ipc-v2" as const;
export const DESKTOP_IPC_V1_CONTRACT_VERSION = "ecatvasp-desktop-ipc-v1" as const;

export const DESKTOP_V2_OPERATIONS = [
  "health",
  "open_project",
  "status",
  "frontend_handoff",
  "application_report",
  "prepare_workflow",
  "project_dashboard",
] as const;

export type DesktopV2Operation = (typeof DESKTOP_V2_OPERATIONS)[number];
export type DesktopV2ProjectOperation = Exclude<DesktopV2Operation, "health">;

export interface DesktopV2HealthRequest {
  protocol_version: typeof DESKTOP_IPC_V2_CONTRACT_VERSION;
  request_id: string;
  operation: "health";
}

export interface DesktopV2ProjectRequest {
  protocol_version: typeof DESKTOP_IPC_V2_CONTRACT_VERSION;
  request_id: string;
  operation: "open_project" | "status" | "frontend_handoff" | "project_dashboard";
  project_root: string;
}

export interface DesktopV2ApplicationReportRequest {
  protocol_version: typeof DESKTOP_IPC_V2_CONTRACT_VERSION;
  request_id: string;
  operation: "application_report";
  project_root: string;
  report_format: ReportFormat;
}

export interface DesktopV2PrepareWorkflowRequest extends PrepareWorkflowInput {
  protocol_version: typeof DESKTOP_IPC_V2_CONTRACT_VERSION;
  request_id: string;
  operation: "prepare_workflow";
  project_root: string;
}

export type DesktopV2Request =
  | DesktopV2HealthRequest
  | DesktopV2ProjectRequest
  | DesktopV2ApplicationReportRequest
  | DesktopV2PrepareWorkflowRequest;

export interface DesktopV2ErrorPayload {
  code: string;
  message: string;
}

export interface DesktopV2SuccessResponse<TPayload> {
  protocol_version: typeof DESKTOP_IPC_V2_CONTRACT_VERSION;
  request_id: string;
  operation: DesktopV2Operation;
  ok: true;
  payload: TPayload;
}

export interface DesktopV2FailureResponse {
  protocol_version: typeof DESKTOP_IPC_V2_CONTRACT_VERSION;
  request_id: string;
  operation: DesktopV2Operation;
  ok: false;
  error: DesktopV2ErrorPayload;
}

export type DesktopV2Response<TPayload> =
  | DesktopV2SuccessResponse<TPayload>
  | DesktopV2FailureResponse;

export interface DesktopV2HealthPayload {
  backend_version: string;
  frontend_handoff_contract_version: typeof FRONTEND_HANDOFF_CONTRACT_VERSION;
  operations: DesktopV2Operation[];
  stateless_project_requests: true;
  supported_protocol_versions: [
    typeof DESKTOP_IPC_V1_CONTRACT_VERSION,
    typeof DESKTOP_IPC_V2_CONTRACT_VERSION,
  ];
  workflow_recipes: Array<{
    recipe_id: string;
    version: string;
    description: string | null;
  }>;
}

export type CountPair = [string, number];

export interface DesktopProjectDashboard {
  project_id: string;
  project_name: string;
  project_slug: string;
  schema_version: number;
  model_counts: CountPair[];
  workflow_counts: CountPair[];
  calculations: CountPair[];
  analyses: CountPair[];
  execution_attempts: CountPair[];
  scheduler_jobs: CountPair[];
  freshness: CountPair[];
  attention_rows: number;
}

export interface DesktopProjectDashboardPayload {
  project_root: string;
  dashboard: DesktopProjectDashboard;
}

export type DesktopV2KnownPayload =
  | DesktopV2HealthPayload
  | OpenProjectPayload
  | StatusPayload
  | FrontendHandoffPayload
  | ApplicationReportPayload
  | PrepareWorkflowPayload
  | DesktopProjectDashboardPayload
  | JsonObject;

export function parseDesktopV2Response<TPayload>(
  raw: string,
  expectedOperation: DesktopV2Operation,
  expectedRequestId?: string,
): DesktopV2Response<TPayload> {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new DesktopContractError("desktop v2 backend response is not valid JSON");
  }
  if (!isRecord(value)) {
    throw new DesktopContractError("desktop v2 backend response must be an object");
  }
  if (value.protocol_version !== DESKTOP_IPC_V2_CONTRACT_VERSION) {
    throw new DesktopContractError("unsupported desktop IPC v2 contract version");
  }
  if (value.operation !== expectedOperation) {
    throw new DesktopContractError("desktop v2 backend response operation mismatch");
  }
  if (typeof value.request_id !== "string" || value.request_id.length === 0) {
    throw new DesktopContractError("desktop v2 backend response request_id is invalid");
  }
  if (expectedRequestId !== undefined && value.request_id !== expectedRequestId) {
    throw new DesktopContractError("desktop v2 backend response request_id mismatch");
  }
  if (typeof value.ok !== "boolean") {
    throw new DesktopContractError("desktop v2 backend response ok flag is invalid");
  }
  if (value.ok) {
    if (!isRecord(value.payload) || "error" in value) {
      throw new DesktopContractError("successful desktop v2 response payload is invalid");
    }
    return value as unknown as DesktopV2SuccessResponse<TPayload>;
  }
  if (!isRecord(value.error) || "payload" in value) {
    throw new DesktopContractError("failed desktop v2 response error is invalid");
  }
  if (typeof value.error.code !== "string" || typeof value.error.message !== "string") {
    throw new DesktopContractError("failed desktop v2 response error fields are invalid");
  }
  return value as unknown as DesktopV2FailureResponse;
}

export function requireDesktopV2Success<TPayload>(
  response: DesktopV2Response<TPayload>,
): DesktopV2SuccessResponse<TPayload> {
  if (!response.ok) {
    throw new DesktopBackendError(response.error);
  }
  return response;
}

export function assertDesktopV2HealthCompatibility(
  response: DesktopV2SuccessResponse<DesktopV2HealthPayload>,
): void {
  const payload: unknown = response.payload;
  if (!isRecord(payload)) {
    throw new DesktopContractError("desktop v2 health payload is invalid");
  }
  if (typeof payload.backend_version !== "string" || !payload.backend_version.startsWith("1.")) {
    throw new DesktopContractError("unsupported desktop v2 backend package major version");
  }
  if (payload.frontend_handoff_contract_version !== FRONTEND_HANDOFF_CONTRACT_VERSION) {
    throw new DesktopContractError("unsupported frontend handoff contract version");
  }
  if (payload.stateless_project_requests !== true) {
    throw new DesktopContractError("desktop v2 backend must advertise stateless project requests");
  }
  if (!Array.isArray(payload.operations)) {
    throw new DesktopContractError("desktop v2 backend operations are invalid");
  }
  for (const operation of DESKTOP_V2_OPERATIONS) {
    if (!payload.operations.includes(operation)) {
      throw new DesktopContractError(`desktop v2 backend is missing operation: ${operation}`);
    }
  }
  if (
    !Array.isArray(payload.supported_protocol_versions) ||
    payload.supported_protocol_versions.length !== 2 ||
    payload.supported_protocol_versions[0] !== DESKTOP_IPC_V1_CONTRACT_VERSION ||
    payload.supported_protocol_versions[1] !== DESKTOP_IPC_V2_CONTRACT_VERSION
  ) {
    throw new DesktopContractError("desktop v2 protocol compatibility catalog is invalid");
  }
  if (!Array.isArray(payload.workflow_recipes) || payload.workflow_recipes.length === 0) {
    throw new DesktopContractError("desktop v2 workflow recipe catalog is invalid");
  }
}

export function assertProjectDashboardPayload(
  payload: DesktopProjectDashboardPayload,
  expectedProjectRoot: string,
): void {
  const value: unknown = payload;
  if (!isRecord(value) || value.project_root !== expectedProjectRoot || !isRecord(value.dashboard)) {
    throw new DesktopContractError("desktop project dashboard payload is invalid");
  }
  const dashboard = value.dashboard;
  for (const field of ["project_id", "project_name", "project_slug"] as const) {
    if (typeof dashboard[field] !== "string" || dashboard[field].length === 0) {
      throw new DesktopContractError(`desktop project dashboard ${field} is invalid`);
    }
  }
  if (dashboard.schema_version !== 3) {
    throw new DesktopContractError("desktop project dashboard schema version is unsupported");
  }
  for (const field of [
    "model_counts",
    "workflow_counts",
    "calculations",
    "analyses",
    "execution_attempts",
    "scheduler_jobs",
    "freshness",
  ] as const) {
    if (!isCountPairs(dashboard[field])) {
      throw new DesktopContractError(`desktop project dashboard ${field} is invalid`);
    }
  }
  if (
    typeof dashboard.attention_rows !== "number" ||
    !Number.isSafeInteger(dashboard.attention_rows) ||
    dashboard.attention_rows < 0
  ) {
    throw new DesktopContractError("desktop project dashboard attention count is invalid");
  }
}

function isCountPairs(value: unknown): value is CountPair[] {
  return (
    Array.isArray(value) &&
    value.every(
      (item) =>
        Array.isArray(item) &&
        item.length === 2 &&
        typeof item[0] === "string" &&
        typeof item[1] === "number" &&
        Number.isSafeInteger(item[1]) &&
        item[1] >= 0,
    )
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
