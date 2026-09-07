import { invoke as tauriInvoke } from "@tauri-apps/api/core";

import {
  DESKTOP_IPC_CONTRACT_VERSION,
  type ApplicationReportPayload,
  type BackendRuntimeDiagnostics,
  type DesktopOperation,
  type DesktopRequest,
  type DesktopSuccessResponse,
  type FrontendHandoffPayload,
  type HealthPayload,
  type OpenProjectPayload,
  type PrepareWorkflowInput,
  type PrepareWorkflowPayload,
  type ProjectDesktopOperation,
  type ReportExportReceipt,
  type ReportFormat,
  type StatusPayload,
  assertApplicationReportPayload,
  assertFrontendHandoffCompatibility,
  assertHealthCompatibility,
  assertPrepareWorkflowPayload,
  parseBackendRuntimeDiagnostics,
  parseDesktopResponse,
  parseReportExportReceipt,
  requireSuccess,
} from "./contracts";

export type InvokeFn = <T>(
  command: string,
  args?: Record<string, unknown>,
) => Promise<T>;

export class DesktopBackendClient {
  private readonly invokeFn: InvokeFn;
  private requestSequence = 0;
  private ready = false;

  constructor(invokeFn: InvokeFn = tauriInvoke) {
    this.invokeFn = invokeFn;
  }

  async connect(): Promise<DesktopSuccessResponse<HealthPayload>> {
    this.ready = false;
    const raw = await this.invokeFn<string>("backend_health");
    const response = requireSuccess(parseDesktopResponse<HealthPayload>(raw, "health"));
    assertHealthCompatibility(response);
    this.ready = true;
    return response;
  }

  async restart(): Promise<DesktopSuccessResponse<HealthPayload>> {
    this.ready = false;
    const raw = await this.invokeFn<string>("backend_restart");
    const response = requireSuccess(parseDesktopResponse<HealthPayload>(raw, "health"));
    assertHealthCompatibility(response);
    this.ready = true;
    return response;
  }

  async diagnostics(): Promise<BackendRuntimeDiagnostics> {
    const raw = await this.invokeFn<string>("backend_diagnostics");
    return parseBackendRuntimeDiagnostics(raw);
  }

  async openProject(projectRoot: string): Promise<DesktopSuccessResponse<OpenProjectPayload>> {
    return this.projectRequest<OpenProjectPayload>("open_project", projectRoot);
  }

  async status(projectRoot: string): Promise<DesktopSuccessResponse<StatusPayload>> {
    return this.projectRequest<StatusPayload>("status", projectRoot);
  }

  async frontendHandoff(
    projectRoot: string,
  ): Promise<DesktopSuccessResponse<FrontendHandoffPayload>> {
    const response = await this.projectRequest<FrontendHandoffPayload>(
      "frontend_handoff",
      projectRoot,
    );
    assertFrontendHandoffCompatibility(response.payload.handoff);
    return response;
  }

  async applicationReport(
    projectRoot: string,
    reportFormat: ReportFormat,
  ): Promise<DesktopSuccessResponse<ApplicationReportPayload>> {
    this.requireReadyProject(projectRoot);
    const request: DesktopRequest = {
      protocol_version: DESKTOP_IPC_CONTRACT_VERSION,
      request_id: this.nextRequestId(),
      operation: "application_report",
      project_root: projectRoot,
      report_format: reportFormat,
    };
    const response = await this.exchange<ApplicationReportPayload>(request);
    assertApplicationReportPayload(response.payload, projectRoot, reportFormat);
    return response;
  }

  async exportReport(
    outputDirectory: string,
    report: ApplicationReportPayload,
  ): Promise<ReportExportReceipt> {
    if (outputDirectory.trim().length === 0) {
      throw new Error("desktop report export requires an explicit output directory");
    }
    const raw = await this.invokeFn<string>("desktop_export_report", {
      outputDirectory,
      reportFormat: report.report_format,
      contentSha256: report.content_sha256,
      content: report.content,
    });
    return parseReportExportReceipt(raw, report);
  }

  async prepareWorkflow(
    projectRoot: string,
    input: PrepareWorkflowInput,
  ): Promise<DesktopSuccessResponse<PrepareWorkflowPayload>> {
    this.requireReadyProject(projectRoot);
    if (
      input.workflow_recipe_id.trim().length === 0 ||
      input.workflow_recipe_version.trim().length === 0 ||
      input.root_structure_snapshot_id.trim().length === 0
    ) {
      throw new Error("prepare workflow requires explicit recipe and root snapshot identity");
    }
    if (
      input.parameters_hash !== undefined &&
      !/^[0-9a-fA-F]{64}$/.test(input.parameters_hash)
    ) {
      throw new Error("prepare workflow parameters hash must be SHA-256 when supplied");
    }
    const request: DesktopRequest = {
      protocol_version: DESKTOP_IPC_CONTRACT_VERSION,
      request_id: this.nextRequestId(),
      operation: "prepare_workflow",
      project_root: projectRoot,
      workflow_recipe_id: input.workflow_recipe_id,
      workflow_recipe_version: input.workflow_recipe_version,
      root_structure_snapshot_id: input.root_structure_snapshot_id,
      ...(input.parameters_hash === undefined
        ? {}
        : { parameters_hash: input.parameters_hash }),
    };
    const response = await this.exchange<PrepareWorkflowPayload>(request);
    assertPrepareWorkflowPayload(response.payload, projectRoot, input);
    return response;
  }

  async shutdown(): Promise<void> {
    try {
      await this.invokeFn<void>("backend_shutdown");
    } finally {
      this.ready = false;
    }
  }

  private async projectRequest<TPayload>(
    operation: Exclude<ProjectDesktopOperation, "application_report" | "prepare_workflow">,
    projectRoot: string,
  ): Promise<DesktopSuccessResponse<TPayload>> {
    this.requireReadyProject(projectRoot);
    const request: DesktopRequest = {
      protocol_version: DESKTOP_IPC_CONTRACT_VERSION,
      request_id: this.nextRequestId(),
      operation,
      project_root: projectRoot,
    };
    return this.exchange<TPayload>(request);
  }

  private async exchange<TPayload>(
    request: DesktopRequest,
  ): Promise<DesktopSuccessResponse<TPayload>> {
    let raw: string;
    try {
      raw = await this.invokeFn<string>("backend_exchange", {
        requestJson: JSON.stringify(request),
      });
    } catch (error: unknown) {
      this.ready = false;
      throw error;
    }

    let response;
    try {
      response = parseDesktopResponse<TPayload>(raw, request.operation, request.request_id);
    } catch (error: unknown) {
      this.ready = false;
      throw error;
    }
    return requireSuccess(response);
  }

  private requireReadyProject(projectRoot: string): void {
    if (!this.ready) {
      throw new Error("desktop backend health handshake is required");
    }
    if (projectRoot.trim().length === 0) {
      throw new Error("project root must not be blank");
    }
  }

  private nextRequestId(): string {
    this.requestSequence += 1;
    return `desktop-${this.requestSequence}`;
  }
}

export function isProjectOperation(operation: DesktopOperation): operation is ProjectDesktopOperation {
  return operation !== "health";
}
