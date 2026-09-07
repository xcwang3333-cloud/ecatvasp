import { invoke as tauriInvoke } from "@tauri-apps/api/core";

import {
  DESKTOP_IPC_CONTRACT_VERSION,
  type DesktopOperation,
  type DesktopRequest,
  type DesktopSuccessResponse,
  type FrontendHandoffPayload,
  type HealthPayload,
  type OpenProjectPayload,
  type ProjectDesktopOperation,
  type StatusPayload,
  assertFrontendHandoffCompatibility,
  assertHealthCompatibility,
  parseDesktopResponse,
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
    const raw = await this.invokeFn<string>("backend_health");
    const response = requireSuccess(parseDesktopResponse<HealthPayload>(raw, "health"));
    assertHealthCompatibility(response);
    this.ready = true;
    return response;
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

  async shutdown(): Promise<void> {
    await this.invokeFn<void>("backend_shutdown");
    this.ready = false;
  }

  private async projectRequest<TPayload>(
    operation: ProjectDesktopOperation,
    projectRoot: string,
  ): Promise<DesktopSuccessResponse<TPayload>> {
    if (!this.ready) {
      throw new Error("desktop backend health handshake is required");
    }
    if (projectRoot.trim().length === 0) {
      throw new Error("project root must not be blank");
    }

    const request = this.buildRequest(operation, projectRoot);
    const raw = await this.invokeFn<string>("backend_exchange", {
      requestJson: JSON.stringify(request),
    });
    return requireSuccess(
      parseDesktopResponse<TPayload>(raw, operation, request.request_id),
    );
  }

  private buildRequest(
    operation: ProjectDesktopOperation,
    projectRoot: string,
  ): DesktopRequest {
    this.requestSequence += 1;
    return {
      protocol_version: DESKTOP_IPC_CONTRACT_VERSION,
      request_id: `desktop-${this.requestSequence}`,
      operation,
      project_root: projectRoot,
    };
  }
}

export function isProjectOperation(operation: DesktopOperation): operation is ProjectDesktopOperation {
  return operation !== "health";
}
