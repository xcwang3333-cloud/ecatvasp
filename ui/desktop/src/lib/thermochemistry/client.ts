import { invoke as tauriInvoke } from "@tauri-apps/api/core";

import type { InvokeFn } from "../backend/client";
import {
  THERMOCHEMISTRY_IPC_VERSION,
  type GasReferenceMaterializationInput,
  type HarmonicMaterializationInput,
  type ReactionDiagramMaterializationPayload,
  type ReactionPreviewPayload,
  type ReactionRequestInput,
  type ThermochemistryAnalysisViewPayload,
  type ThermochemistryCatalogPayload,
  type ThermochemistryMaterializationPayload,
  type ThermochemistryOperation,
  assertMaterializationReceipt,
  assertReactionPreview,
  assertThermochemistryCatalog,
  assertThermochemistryView,
  parseThermochemistryResponse,
} from "./contracts";

export class ThermochemistryClient {
  private sequence = 0;

  constructor(private readonly invokeFn: InvokeFn = tauriInvoke) {}

  async catalog(projectRoot: string): Promise<ThermochemistryCatalogPayload> {
    const payload = await this.exchange<ThermochemistryCatalogPayload>(
      "thermochemistry_catalog",
      projectRoot,
      {},
    );
    assertThermochemistryCatalog(payload, projectRoot);
    return payload;
  }

  async materializeHarmonic(
    projectRoot: string,
    input: HarmonicMaterializationInput,
  ): Promise<ThermochemistryMaterializationPayload> {
    const payload = await this.exchange<ThermochemistryMaterializationPayload>(
      "materialize_harmonic_thermochemistry",
      projectRoot,
      { ...input },
    );
    assertMaterializationReceipt(payload, projectRoot);
    return payload;
  }

  async materializeGasReference(
    projectRoot: string,
    input: GasReferenceMaterializationInput,
  ): Promise<ThermochemistryMaterializationPayload> {
    const payload = await this.exchange<ThermochemistryMaterializationPayload>(
      "materialize_gas_reference",
      projectRoot,
      { ...input },
    );
    assertMaterializationReceipt(payload, projectRoot);
    return payload;
  }

  async view(
    projectRoot: string,
    analysisId: string,
  ): Promise<ThermochemistryAnalysisViewPayload> {
    const payload = await this.exchange<ThermochemistryAnalysisViewPayload>(
      "thermochemistry_view",
      projectRoot,
      { analysis_id: analysisId },
    );
    assertThermochemistryView(payload, projectRoot);
    return payload;
  }

  async reactionPreview(
    projectRoot: string,
    input: ReactionRequestInput,
  ): Promise<ReactionPreviewPayload> {
    const payload = await this.exchange<ReactionPreviewPayload>(
      "reaction_preview",
      projectRoot,
      { ...input },
    );
    assertReactionPreview(payload, projectRoot);
    return payload;
  }

  async materializeReactionDiagram(
    projectRoot: string,
    input: ReactionRequestInput,
  ): Promise<ReactionDiagramMaterializationPayload> {
    const payload = await this.exchange<ReactionDiagramMaterializationPayload>(
      "materialize_reaction_diagram",
      projectRoot,
      { ...input },
    );
    assertMaterializationReceipt(payload, projectRoot);
    return payload;
  }

  async reactionDiagramView(
    projectRoot: string,
    analysisId: string,
  ): Promise<ThermochemistryAnalysisViewPayload> {
    const payload = await this.exchange<ThermochemistryAnalysisViewPayload>(
      "reaction_diagram_view",
      projectRoot,
      { analysis_id: analysisId },
    );
    assertThermochemistryView(payload, projectRoot);
    if (payload.analysis_type !== "reaction_diagram" || payload.view.kind !== "reaction_diagram") {
      throw new Error("Reaction diagram view returned a non-diagram Analysis");
    }
    return payload;
  }

  private async exchange<T>(
    operation: ThermochemistryOperation,
    projectRoot: string,
    fields: Record<string, unknown>,
  ): Promise<T> {
    if (!projectRoot.trim()) throw new Error("project root must not be blank");
    const requestId = `thermochemistry-v2-${++this.sequence}`;
    const raw = await this.invokeFn<string>("backend_exchange", {
      requestJson: JSON.stringify({
        protocol_version: THERMOCHEMISTRY_IPC_VERSION,
        request_id: requestId,
        operation,
        project_root: projectRoot,
        ...fields,
      }),
    });
    return parseThermochemistryResponse<T>(raw, operation, requestId);
  }
}
