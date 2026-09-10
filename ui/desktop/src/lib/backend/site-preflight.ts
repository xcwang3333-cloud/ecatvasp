import { DesktopContractError } from "./contracts";

export type SitePreflightStatus = "READY" | "BLOCKED" | "WARNING" | "UNAVAILABLE_OPTIONAL";

export interface SiteProfileInput {
  site_id: string;
  name: string;
  host_alias: string;
  remote_root: string;
  potcar_resolver_id: string;
  potcar_family: string;
  potcar_root: string;
  scheduler_type?: "slurm";
  ssh_mode?: "system_openssh";
  vasp_executable?: string;
  mpi_launcher?: string;
  module_loads?: string[];
  bader_executable?: string;
  lobster_executable?: string;
}

export interface SitePreflightCheck {
  check_name: string;
  status: SitePreflightStatus;
  reason_code: string | null;
  message: string;
  evidence: string[];
}

export interface SitePreflightPayload {
  site_id: string;
  profile_hash: string;
  status: SitePreflightStatus;
  timestamp: string;
  transient: true;
  checks: SitePreflightCheck[];
}

const STATUSES = new Set<SitePreflightStatus>([
  "READY",
  "BLOCKED",
  "WARNING",
  "UNAVAILABLE_OPTIONAL",
]);
const SHA256 = /^[0-9a-f]{64}$/;

export function assertSitePreflightPayload(
  payload: SitePreflightPayload,
  expectedSiteId: string,
): void {
  const value: unknown = payload;
  if (!isRecord(value) || value.site_id !== expectedSiteId) {
    throw new DesktopContractError("site preflight identity is invalid");
  }
  if (typeof value.profile_hash !== "string" || !SHA256.test(value.profile_hash)) {
    throw new DesktopContractError("site preflight profile hash is invalid");
  }
  if (!isStatus(value.status)) {
    throw new DesktopContractError("site preflight status is invalid");
  }
  if (typeof value.timestamp !== "string" || value.timestamp.length === 0 || value.transient !== true) {
    throw new DesktopContractError("site preflight transient evidence metadata is invalid");
  }
  if (!Array.isArray(value.checks) || !value.checks.every(isCheck)) {
    throw new DesktopContractError("site preflight checks are invalid");
  }
}

function isCheck(value: unknown): value is SitePreflightCheck {
  if (!isRecord(value)) return false;
  return (
    typeof value.check_name === "string" &&
    value.check_name.length > 0 &&
    isStatus(value.status) &&
    (value.reason_code === null || typeof value.reason_code === "string") &&
    typeof value.message === "string" &&
    Array.isArray(value.evidence) &&
    value.evidence.every((item) => typeof item === "string")
  );
}

function isStatus(value: unknown): value is SitePreflightStatus {
  return typeof value === "string" && STATUSES.has(value as SitePreflightStatus);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
