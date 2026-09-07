import { invoke as tauriInvoke } from "@tauri-apps/api/core";

import {
  type ApplicationReportPayload,
  type BackendRuntimeDiagnostics,
  type FrontendHandoffPayload,
  type JsonObject,
  type OpenProjectPayload,
  type PrepareWorkflowInput,
  type PrepareWorkflowPayload,
  type ReportExportReceipt,
  type ReportFormat,
  type StatusPayload,
  assertApplicationReportPayload,
  assertFrontendHandoffCompatibility,
  assertPrepareWorkflowPayload,
  parseBackendRuntimeDiagnostics,
  parseReportExportReceipt,
} from "./contracts";
import {
  DESKTOP_IPC_V2_CONTRACT_VERSION,
  type BuildAdsorbateConformerInput,
  type BuildGrapheneInput,
  type BuildMultiMetalInput,
  type BuildSingleMetalInput,
  type CreateActiveSiteInput,
  type CreateCatalystInput,
  type CreateProjectInput,
  type DesktopModelCatalogPayload,
  type DesktopProjectDashboardPayload,
  type DesktopStructurePresentationPayload,
  type DesktopV2HealthPayload,
  type DesktopV2Operation,
  type DesktopV2SuccessResponse,
  type ImportStructureInput,
  type MutateStructureInput,
  type StructureModelReceipt,
  assertDesktopV2HealthCompatibility,
  assertModelCatalogPayload,
  assertProjectDashboardPayload,
  assertStructurePresentationPayload,
  parseDesktopV2Response,
  requireDesktopV2Success,
} from "./contracts-v2";
import type { InvokeFn } from "./client";

interface V2WireRequest extends Record<string, unknown> {
  protocol_version: typeof DESKTOP_IPC_V2_CONTRACT_VERSION;
  request_id: string;
  operation: DesktopV2Operation;
}

export class DesktopBackendClientV2 {
  private readonly invokeFn: InvokeFn;
  private requestSequence = 0;
  private ready = false;

  constructor(invokeFn: InvokeFn = tauriInvoke) {
    this.invokeFn = invokeFn;
  }

  async connect(): Promise<DesktopV2SuccessResponse<DesktopV2HealthPayload>> {
    this.ready = false;
    const raw = await this.invokeFn<string>("backend_health");
    const response = requireDesktopV2Success(
      parseDesktopV2Response<DesktopV2HealthPayload>(raw, "health"),
    );
    assertDesktopV2HealthCompatibility(response);
    this.ready = true;
    return response;
  }

  async restart(): Promise<DesktopV2SuccessResponse<DesktopV2HealthPayload>> {
    this.ready = false;
    const raw = await this.invokeFn<string>("backend_restart");
    const response = requireDesktopV2Success(
      parseDesktopV2Response<DesktopV2HealthPayload>(raw, "health"),
    );
    assertDesktopV2HealthCompatibility(response);
    this.ready = true;
    return response;
  }

  async diagnostics(): Promise<BackendRuntimeDiagnostics> {
    const raw = await this.invokeFn<string>("backend_diagnostics");
    return parseBackendRuntimeDiagnostics(raw);
  }

  async openProject(projectRoot: string): Promise<DesktopV2SuccessResponse<OpenProjectPayload>> {
    return this.projectRead<OpenProjectPayload>("open_project", projectRoot);
  }

  async status(projectRoot: string): Promise<DesktopV2SuccessResponse<StatusPayload>> {
    return this.projectRead<StatusPayload>("status", projectRoot);
  }

  async frontendHandoff(
    projectRoot: string,
  ): Promise<DesktopV2SuccessResponse<FrontendHandoffPayload>> {
    const response = await this.projectRead<FrontendHandoffPayload>(
      "frontend_handoff",
      projectRoot,
    );
    assertFrontendHandoffCompatibility(response.payload.handoff);
    return response;
  }

  async projectDashboard(
    projectRoot: string,
  ): Promise<DesktopV2SuccessResponse<DesktopProjectDashboardPayload>> {
    const response = await this.projectRead<DesktopProjectDashboardPayload>(
      "project_dashboard",
      projectRoot,
    );
    assertProjectDashboardPayload(response.payload, projectRoot);
    return response;
  }

  async modelCatalog(
    projectRoot: string,
  ): Promise<DesktopV2SuccessResponse<DesktopModelCatalogPayload>> {
    const response = await this.projectRead<DesktopModelCatalogPayload>(
      "model_catalog",
      projectRoot,
    );
    assertModelCatalogPayload(response.payload, projectRoot);
    return response;
  }

  async structurePresentation(
    projectRoot: string,
    structureSnapshotId: string,
  ): Promise<DesktopV2SuccessResponse<DesktopStructurePresentationPayload>> {
    const response = await this.projectCommand<DesktopStructurePresentationPayload>(
      "structure_presentation",
      projectRoot,
      { structure_snapshot_id: structureSnapshotId },
    );
    assertStructurePresentationPayload(response.payload, projectRoot, structureSnapshotId);
    return response;
  }

  async createProject(
    input: CreateProjectInput,
  ): Promise<DesktopV2SuccessResponse<OpenProjectPayload>> {
    this.requireReady();
    if (input.project_root.trim().length === 0) throw new Error("project root must not be blank");
    return this.exchange<OpenProjectPayload>(this.request("create_project", {
      project_root: input.project_root,
      name: input.name,
      slug: input.slug,
      ...(input.description === undefined ? {} : { description: input.description }),
    }));
  }

  async createCatalyst(
    projectRoot: string,
    input: CreateCatalystInput,
  ): Promise<DesktopV2SuccessResponse<JsonObject>> {
    return this.projectCommand<JsonObject>("create_catalyst", projectRoot, { ...input });
  }

  async buildGraphene(
    projectRoot: string,
    input: BuildGrapheneInput,
  ): Promise<DesktopV2SuccessResponse<StructureModelReceipt>> {
    return this.projectCommand<StructureModelReceipt>(
      "build_graphene_model",
      projectRoot,
      { ...input },
    );
  }

  async importStructure(
    projectRoot: string,
    input: ImportStructureInput,
  ): Promise<DesktopV2SuccessResponse<StructureModelReceipt & JsonObject>> {
    return this.projectCommand<StructureModelReceipt & JsonObject>(
      "import_structure_model",
      projectRoot,
      { ...input },
    );
  }

  async mutateStructure(
    projectRoot: string,
    input: MutateStructureInput,
  ): Promise<DesktopV2SuccessResponse<StructureModelReceipt & JsonObject>> {
    return this.projectCommand<StructureModelReceipt & JsonObject>(
      "mutate_structure_model",
      projectRoot,
      { ...input },
    );
  }

  async buildSingleMetalSite(
    projectRoot: string,
    input: BuildSingleMetalInput,
  ): Promise<DesktopV2SuccessResponse<StructureModelReceipt & JsonObject>> {
    return this.projectCommand<StructureModelReceipt & JsonObject>(
      "build_single_metal_site",
      projectRoot,
      { ...input },
    );
  }

  async buildMultiMetalSite(
    projectRoot: string,
    input: BuildMultiMetalInput,
  ): Promise<DesktopV2SuccessResponse<StructureModelReceipt & JsonObject>> {
    return this.projectCommand<StructureModelReceipt & JsonObject>(
      "build_multi_metal_site",
      projectRoot,
      { ...input },
    );
  }

  async createActiveSite(
    projectRoot: string,
    input: CreateActiveSiteInput,
  ): Promise<DesktopV2SuccessResponse<JsonObject>> {
    return this.projectCommand<JsonObject>("create_active_site", projectRoot, { ...input });
  }

  async buildAdsorbateConformer(
    projectRoot: string,
    input: BuildAdsorbateConformerInput,
  ): Promise<DesktopV2SuccessResponse<JsonObject>> {
    return this.projectCommand<JsonObject>(
      "build_adsorbate_conformer",
      projectRoot,
      { ...input },
    );
  }

  async applicationReport(
    projectRoot: string,
    reportFormat: ReportFormat,
  ): Promise<DesktopV2SuccessResponse<ApplicationReportPayload>> {
    const response = await this.projectCommand<ApplicationReportPayload>(
      "application_report",
      projectRoot,
      { report_format: reportFormat },
    );
    assertApplicationReportPayload(response.payload, projectRoot, reportFormat);
    return response;
  }

  async prepareWorkflow(
    projectRoot: string,
    input: PrepareWorkflowInput,
  ): Promise<DesktopV2SuccessResponse<PrepareWorkflowPayload>> {
    const response = await this.projectCommand<PrepareWorkflowPayload>(
      "prepare_workflow",
      projectRoot,
      {
        workflow_recipe_id: input.workflow_recipe_id,
        workflow_recipe_version: input.workflow_recipe_version,
        root_structure_snapshot_id: input.root_structure_snapshot_id,
        ...(input.parameters_hash === undefined ? {} : { parameters_hash: input.parameters_hash }),
      },
    );
    assertPrepareWorkflowPayload(response.payload, projectRoot, input);
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

  async shutdown(): Promise<void> {
    try {
      await this.invokeFn<void>("backend_shutdown");
    } finally {
      this.ready = false;
    }
  }

  private async projectRead<TPayload>(
    operation: "open_project" | "status" | "frontend_handoff" | "project_dashboard" | "model_catalog",
    projectRoot: string,
  ): Promise<DesktopV2SuccessResponse<TPayload>> {
    return this.projectCommand<TPayload>(operation, projectRoot, {});
  }

  private async projectCommand<TPayload>(
    operation: DesktopV2Operation,
    projectRoot: string,
    fields: Record<string, unknown>,
  ): Promise<DesktopV2SuccessResponse<TPayload>> {
    this.requireReadyProject(projectRoot);
    return this.exchange<TPayload>(this.request(operation, { project_root: projectRoot, ...fields }));
  }

  private request(
    operation: DesktopV2Operation,
    fields: Record<string, unknown>,
  ): V2WireRequest {
    return {
      protocol_version: DESKTOP_IPC_V2_CONTRACT_VERSION,
      request_id: this.nextRequestId(),
      operation,
      ...fields,
    };
  }

  private async exchange<TPayload>(
    request: V2WireRequest,
  ): Promise<DesktopV2SuccessResponse<TPayload>> {
    let raw: string;
    try {
      raw = await this.invokeFn<string>("backend_exchange", {
        requestJson: JSON.stringify(request),
      });
    } catch (error: unknown) {
      this.ready = false;
      throw error;
    }

    try {
      return requireDesktopV2Success(
        parseDesktopV2Response<TPayload>(raw, request.operation, request.request_id),
      );
    } catch (error: unknown) {
      this.ready = false;
      throw error;
    }
  }

  private requireReady(): void {
    if (!this.ready) throw new Error("desktop backend health handshake is required");
  }

  private requireReadyProject(projectRoot: string): void {
    this.requireReady();
    if (projectRoot.trim().length === 0) throw new Error("project root must not be blank");
  }

  private nextRequestId(): string {
    this.requestSequence += 1;
    return `desktop-v2-${this.requestSequence}`;
  }
}
