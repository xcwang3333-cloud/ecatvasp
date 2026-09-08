import { invoke as tauriInvoke } from "@tauri-apps/api/core";

import type { InvokeFn } from "../backend/client";
import {
  CALCULATION_IPC_VERSION,
  type CalculationCatalogPayload,
  type CalculationOperation,
  type MaterializeCalculationStepInput,
  type MaterializeCalculationStepPayload,
  type PrepareCalculationWorkflowInput,
  type PrepareCalculationWorkflowPayload,
  assertCalculationCatalogPayload,
  assertMaterializationPayload,
  assertPreparationPayload,
  parseCalculationResponse,
} from "./contracts";

export class CalculationWizardClient {
  private sequence = 0;

  constructor(private readonly invokeFn: InvokeFn = tauriInvoke) {}

  async catalog(projectRoot: string): Promise<CalculationCatalogPayload> {
    const payload = await this.exchange<CalculationCatalogPayload>(
      "calculation_catalog",
      projectRoot,
      {},
    );
    assertCalculationCatalogPayload(payload, projectRoot);
    return payload;
  }

  async prepare(
    projectRoot: string,
    input: PrepareCalculationWorkflowInput,
  ): Promise<PrepareCalculationWorkflowPayload> {
    const payload = await this.exchange<PrepareCalculationWorkflowPayload>(
      "prepare_calculation_workflow",
      projectRoot,
      { ...input },
    );
    assertPreparationPayload(payload, projectRoot);
    return payload;
  }

  async materialize(
    projectRoot: string,
    input: MaterializeCalculationStepInput,
  ): Promise<MaterializeCalculationStepPayload> {
    const payload = await this.exchange<MaterializeCalculationStepPayload>(
      "materialize_calculation_step",
      projectRoot,
      { ...input },
    );
    assertMaterializationPayload(payload, projectRoot);
    return payload;
  }

  private async exchange<T>(
    operation: CalculationOperation,
    projectRoot: string,
    fields: Record<string, unknown>,
  ): Promise<T> {
    if (!projectRoot.trim()) throw new Error("project root must not be blank");
    const requestId = `calculation-v2-${++this.sequence}`;
    const raw = await this.invokeFn<string>("backend_exchange", {
      requestJson: JSON.stringify({
        protocol_version: CALCULATION_IPC_VERSION,
        request_id: requestId,
        operation,
        project_root: projectRoot,
        ...fields,
      }),
    });
    return parseCalculationResponse<T>(raw, operation, requestId);
  }
}
