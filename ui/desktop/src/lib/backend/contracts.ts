export const DESKTOP_IPC_CONTRACT_VERSION = "ecatvasp-desktop-ipc-v1" as const;
export const FRONTEND_HANDOFF_CONTRACT_VERSION = "ecatvasp-frontend-handoff-v1" as const;

export const DESKTOP_OPERATIONS = [
  "health",
  "open_project",
  "status",
  "frontend_handoff",
] as const;

export type DesktopOperation = (typeof DESKTOP_OPERATIONS)[number];
export type ProjectDesktopOperation = Exclude<DesktopOperation, "health">;

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

export interface HealthPayload {
  backend_version: string;
  frontend_handoff_contract_version: typeof FRONTEND_HANDOFF_CONTRACT_VERSION;
  operations: DesktopOperation[];
  stateless_project_requests: true;
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
}

export function assertFrontendHandoffCompatibility(handoff: FrontendHandoffV1): void {
  if (handoff.contract_version !== FRONTEND_HANDOFF_CONTRACT_VERSION) {
    throw new DesktopContractError("unsupported frontend handoff contract version");
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
