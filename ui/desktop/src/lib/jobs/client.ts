import { invoke as tauriInvoke } from "@tauri-apps/api/core";

import type { InvokeFn } from "../backend/client";
import {
  JOB_CENTER_IPC_VERSION,
  type JobCatalogPayload,
  type JobCenterOperation,
  type JobObservationPayload,
  type PrepareExecutionInput,
  type PrepareExecutionPayload,
  type RemoteJobInput,
  type RetrieveJobOutputsInput,
  type RetrieveJobOutputsPayload,
  type SubmitSlurmJobInput,
  type SubmitSlurmJobPayload,
  assertJobCatalogPayload,
  assertObservationPayload,
  assertPrepareExecutionPayload,
  assertRetrievalPayload,
  assertSubmissionPayload,
  parseJobCenterResponse,
} from "./contracts";

export class JobCenterClient {
  private sequence = 0;

  constructor(private readonly invokeFn: InvokeFn = tauriInvoke) {}

  async catalog(projectRoot: string): Promise<JobCatalogPayload> {
    const payload = await this.exchange<JobCatalogPayload>("job_catalog", projectRoot, {});
    assertJobCatalogPayload(payload, projectRoot);
    return payload;
  }

  async prepare(
    projectRoot: string,
    input: PrepareExecutionInput,
  ): Promise<PrepareExecutionPayload> {
    const payload = await this.exchange<PrepareExecutionPayload>(
      "prepare_execution",
      projectRoot,
      { ...input },
    );
    assertPrepareExecutionPayload(payload, projectRoot);
    return payload;
  }

  async submit(
    projectRoot: string,
    input: SubmitSlurmJobInput,
  ): Promise<SubmitSlurmJobPayload> {
    const payload = await this.exchange<SubmitSlurmJobPayload>(
      "submit_slurm_job",
      projectRoot,
      { ...input },
    );
    assertSubmissionPayload(payload, projectRoot);
    return payload;
  }

  async refresh(projectRoot: string, input: RemoteJobInput): Promise<JobObservationPayload> {
    const payload = await this.exchange<JobObservationPayload>(
      "refresh_slurm_job",
      projectRoot,
      { ...input },
    );
    assertObservationPayload(payload, projectRoot);
    return payload;
  }

  async cancel(projectRoot: string, input: RemoteJobInput): Promise<JobObservationPayload> {
    const payload = await this.exchange<JobObservationPayload>(
      "cancel_slurm_job",
      projectRoot,
      { ...input },
    );
    assertObservationPayload(payload, projectRoot);
    return payload;
  }

  async retrieve(
    projectRoot: string,
    input: RetrieveJobOutputsInput,
  ): Promise<RetrieveJobOutputsPayload> {
    const payload = await this.exchange<RetrieveJobOutputsPayload>(
      "retrieve_job_outputs",
      projectRoot,
      { ...input },
    );
    assertRetrievalPayload(payload, projectRoot);
    return payload;
  }

  private async exchange<T>(
    operation: JobCenterOperation,
    projectRoot: string,
    fields: Record<string, unknown>,
  ): Promise<T> {
    if (!projectRoot.trim()) throw new Error("project root must not be blank");
    const requestId = `job-center-v2-${++this.sequence}`;
    const raw = await this.invokeFn<string>("backend_exchange", {
      requestJson: JSON.stringify({
        protocol_version: JOB_CENTER_IPC_VERSION,
        request_id: requestId,
        operation,
        project_root: projectRoot,
        ...fields,
      }),
    });
    return parseJobCenterResponse<T>(raw, operation, requestId);
  }
}
