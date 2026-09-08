import { invoke as tauriInvoke } from "@tauri-apps/api/core";

import type { InvokeFn } from "../backend/client";
import {
  RESULT_CENTER_IPC_VERSION,
  type AnalyzeResultPayload,
  type PromoteResultInput,
  type PromoteResultPayload,
  type ResultCatalogPayload,
  type ResultCenterOperation,
  assertAnalyzeResultPayload,
  assertPromoteResultPayload,
  assertResultCatalogPayload,
  parseResultCenterResponse,
} from "./contracts";

export class ResultCenterClient {
  private sequence = 0;

  constructor(private readonly invokeFn: InvokeFn = tauriInvoke) {}

  async catalog(projectRoot: string): Promise<ResultCatalogPayload> {
    const payload = await this.exchange<ResultCatalogPayload>(
      "result_catalog",
      projectRoot,
      {},
    );
    assertResultCatalogPayload(payload, projectRoot);
    return payload;
  }

  async analyze(projectRoot: string, calculationId: string): Promise<AnalyzeResultPayload> {
    const payload = await this.exchange<AnalyzeResultPayload>(
      "analyze_result",
      projectRoot,
      { calculation_id: calculationId },
    );
    assertAnalyzeResultPayload(payload, projectRoot);
    return payload;
  }

  async promote(
    projectRoot: string,
    input: PromoteResultInput,
  ): Promise<PromoteResultPayload> {
    const payload = await this.exchange<PromoteResultPayload>(
      "promote_result_structure",
      projectRoot,
      { ...input },
    );
    assertPromoteResultPayload(payload, projectRoot);
    return payload;
  }

  private async exchange<T>(
    operation: ResultCenterOperation,
    projectRoot: string,
    fields: Record<string, unknown>,
  ): Promise<T> {
    if (!projectRoot.trim()) throw new Error("project root must not be blank");
    const requestId = `result-center-v2-${++this.sequence}`;
    const raw = await this.invokeFn<string>("backend_exchange", {
      requestJson: JSON.stringify({
        protocol_version: RESULT_CENTER_IPC_VERSION,
        request_id: requestId,
        operation,
        project_root: projectRoot,
        ...fields,
      }),
    });
    return parseResultCenterResponse<T>(raw, operation, requestId);
  }
}
