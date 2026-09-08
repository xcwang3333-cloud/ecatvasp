import { invoke as tauriInvoke } from "@tauri-apps/api/core";

import type { InvokeFn } from "../backend/client";
import {
  ELECTRONIC_ANALYSIS_IPC_VERSION,
  type BandCenterInput,
  type ElectronicAnalysisCatalogPayload,
  type ElectronicAnalysisOperation,
  type ElectronicAnalysisViewPayload,
  type ElectronicMaterializationPayload,
  assertElectronicAnalysisViewPayload,
  assertElectronicCatalogPayload,
  assertMaterializationPayload,
  parseElectronicAnalysisResponse,
} from "./contracts";

export class ElectronicAnalysisClient {
  private sequence = 0;

  constructor(private readonly invokeFn: InvokeFn = tauriInvoke) {}

  async catalog(projectRoot: string): Promise<ElectronicAnalysisCatalogPayload> {
    const payload = await this.exchange<ElectronicAnalysisCatalogPayload>(
      "electronic_analysis_catalog",
      projectRoot,
      {},
    );
    assertElectronicCatalogPayload(payload, projectRoot);
    return payload;
  }

  async materializeDos(
    projectRoot: string,
    calculationId: string,
  ): Promise<ElectronicMaterializationPayload> {
    const payload = await this.exchange<ElectronicMaterializationPayload>(
      "materialize_dos_analysis",
      projectRoot,
      { calculation_id: calculationId },
    );
    assertMaterializationPayload(payload, projectRoot);
    return payload;
  }

  async view(
    projectRoot: string,
    analysisId: string,
  ): Promise<ElectronicAnalysisViewPayload> {
    const payload = await this.exchange<ElectronicAnalysisViewPayload>(
      "electronic_analysis_view",
      projectRoot,
      { analysis_id: analysisId },
    );
    assertElectronicAnalysisViewPayload(payload, projectRoot);
    return payload;
  }

  async materializeBandCenter(
    projectRoot: string,
    input: BandCenterInput,
  ): Promise<ElectronicMaterializationPayload> {
    const payload = await this.exchange<ElectronicMaterializationPayload>(
      "materialize_band_center",
      projectRoot,
      { ...input },
    );
    assertMaterializationPayload(payload, projectRoot);
    return payload;
  }

  private async exchange<T>(
    operation: ElectronicAnalysisOperation,
    projectRoot: string,
    fields: Record<string, unknown>,
  ): Promise<T> {
    if (!projectRoot.trim()) throw new Error("project root must not be blank");
    const requestId = `electronic-analysis-v2-${++this.sequence}`;
    const raw = await this.invokeFn<string>("backend_exchange", {
      requestJson: JSON.stringify({
        protocol_version: ELECTRONIC_ANALYSIS_IPC_VERSION,
        request_id: requestId,
        operation,
        project_root: projectRoot,
        ...fields,
      }),
    });
    return parseElectronicAnalysisResponse<T>(raw, operation, requestId);
  }
}
