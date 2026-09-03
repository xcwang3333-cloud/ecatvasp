"""Pure v0.4 final execution-handoff acceptance and cross-layer integrity checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ecatvasp.domain import (
    Artifact,
    ArtifactType,
    Calculation,
    CalculationId,
    ExecutionAttempt,
    ExecutionAttemptId,
    ExecutionAttemptProducerRef,
    ExecutionAttemptStatus,
    RemoteJob,
    RemoteJobId,
    SchedulerState,
    SchedulerType,
    canonical_sha256,
)
from ecatvasp.execution.batch import BatchDispatchSnapshot, BatchNodeState
from ecatvasp.execution.provenance import validate_execution_attempt_plan
from ecatvasp.execution.retrieval import RemoteRetrievalPackage
from ecatvasp.execution.targets import ExecutionTargetProfile, TransportKind
from ecatvasp.vasp.execution_plan import ExecutionPlan


class ExecutionAcceptanceError(ValueError):
    """Raised when the v0.4 execution handoff cannot be accepted without guessing."""


class ExecutionHandoffStage(StrEnum):
    """Execution-only milestone; none of these values assert scientific convergence."""

    CREATED = "created"
    STAGED = "staged"
    SUBMITTED = "submitted"
    RUNNING = "running"
    EXITED = "exited"
    RETRIEVAL = "retrieval"
    PARSED = "parsed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ExecutionAcceptanceReport:
    """Deterministic, portable summary of one accepted v0.4 execution handoff."""

    calculation_id: CalculationId
    plan_hash: str
    attempt_id: ExecutionAttemptId
    attempt_number: int
    attempt_status: ExecutionAttemptStatus
    stage: ExecutionHandoffStage
    target_hash: str
    artifact_types: tuple[ArtifactType, ...]
    remote_job_id: RemoteJobId | None = None
    scheduler_state: SchedulerState | None = None
    retrieval_hash: str | None = None
    batch_node_id: str | None = None
    batch_state: BatchNodeState | None = None
    scientific_convergence_assessed: bool = False
    checks: tuple[str, ...] = ()
    acceptance_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.attempt_number < 1:
            raise ValueError("attempt_number must be positive")
        if self.scientific_convergence_assessed:
            raise ValueError("v0.4 execution acceptance must not assess scientific convergence")
        normalized_types = tuple(sorted(set(self.artifact_types), key=lambda item: item.value))
        object.__setattr__(self, "artifact_types", normalized_types)
        object.__setattr__(
            self,
            "acceptance_hash",
            canonical_sha256(
                {
                    "calculation_id": self.calculation_id,
                    "plan_hash": self.plan_hash,
                    "attempt_id": self.attempt_id,
                    "attempt_number": self.attempt_number,
                    "attempt_status": self.attempt_status,
                    "stage": self.stage,
                    "target_hash": self.target_hash,
                    "artifact_types": normalized_types,
                    "remote_job_id": self.remote_job_id,
                    "scheduler_state": self.scheduler_state,
                    "retrieval_hash": self.retrieval_hash,
                    "batch_node_id": self.batch_node_id,
                    "batch_state": self.batch_state,
                    "scientific_convergence_assessed": False,
                    "checks": self.checks,
                }
            ),
        )


def validate_v04_execution_handoff(
    *,
    calculation: Calculation,
    plan: ExecutionPlan,
    attempt: ExecutionAttempt,
    target: ExecutionTargetProfile,
    execution_artifacts: tuple[Artifact, ...] = (),
    remote_job: RemoteJob | None = None,
    retrieval: RemoteRetrievalPackage | None = None,
    batch_snapshot: BatchDispatchSnapshot | None = None,
    batch_node_id: str | None = None,
) -> ExecutionAcceptanceReport:
    """Validate one v0.4 execution handoff without transport, scheduler, or parsing side effects.

    This is a cross-layer integrity gate, not a workflow engine.  It validates already-persisted
    facts from Blocks 1-9 and deliberately does not infer scientific convergence from scheduler
    completion, OUTCAR markers, retrieval, or parser state.
    """

    checks: list[str] = []
    try:
        validate_execution_attempt_plan(
            plan=plan,
            calculation=calculation,
            attempt=attempt,
        )
    except ValueError as error:
        raise ExecutionAcceptanceError(str(error)) from error
    checks.append("calculation-plan-attempt provenance")

    _validate_execution_artifacts(attempt=attempt, artifacts=execution_artifacts)
    checks.append("execution artifact ownership")

    stage = _stage_for_attempt(attempt.status)
    _validate_stage_artifact_contract(
        stage=stage,
        target=target,
        artifacts=execution_artifacts,
        retrieval=retrieval,
    )
    checks.append("stage artifact contract")

    _validate_target_and_remote_job(
        attempt=attempt,
        target=target,
        remote_job=remote_job,
    )
    checks.append("target and remote-job boundary")

    retrieval_hash: str | None = None
    if retrieval is not None:
        _validate_retrieval(
            plan=plan,
            attempt=attempt,
            target=target,
            remote_job=remote_job,
            retrieval=retrieval,
        )
        retrieval_hash = retrieval.manifest.retrieval_hash
        checks.append("retrieval provenance and expected-output coverage")

    batch_state: BatchNodeState | None = None
    if batch_snapshot is not None or batch_node_id is not None:
        if batch_snapshot is None or batch_node_id is None:
            raise ExecutionAcceptanceError(
                "batch_snapshot and batch_node_id must be supplied together"
            )
        batch_state = _validate_batch_observation(
            snapshot=batch_snapshot,
            node_id=batch_node_id,
            plan=plan,
            attempt=attempt,
        )
        checks.append("scheduler-DAG observation")

    report_artifacts = list(execution_artifacts)
    if retrieval is not None:
        report_artifacts.extend(retrieval.artifacts)

    return ExecutionAcceptanceReport(
        calculation_id=calculation.id,
        plan_hash=plan.plan_hash,
        attempt_id=attempt.id,
        attempt_number=attempt.attempt_number,
        attempt_status=attempt.status,
        stage=stage,
        target_hash=target.target_hash,
        artifact_types=tuple(item.artifact_type for item in report_artifacts),
        remote_job_id=remote_job.id if remote_job is not None else None,
        scheduler_state=remote_job.state if remote_job is not None else None,
        retrieval_hash=retrieval_hash,
        batch_node_id=batch_node_id,
        batch_state=batch_state,
        checks=tuple(checks),
    )


def _validate_execution_artifacts(
    *,
    attempt: ExecutionAttempt,
    artifacts: tuple[Artifact, ...],
) -> None:
    ids = tuple(item.id for item in artifacts)
    if len(ids) != len(set(ids)):
        raise ExecutionAcceptanceError("execution artifact ids must be unique")
    producer = ExecutionAttemptProducerRef(attempt.id)
    for artifact in artifacts:
        if artifact.producer != producer:
            raise ExecutionAcceptanceError(
                "execution acceptance artifacts must be produced by the supplied ExecutionAttempt"
            )


def _validate_stage_artifact_contract(
    *,
    stage: ExecutionHandoffStage,
    target: ExecutionTargetProfile,
    artifacts: tuple[Artifact, ...],
    retrieval: RemoteRetrievalPackage | None,
) -> None:
    types = {item.artifact_type for item in artifacts}
    if target.transport is TransportKind.SSH and stage in {
        ExecutionHandoffStage.STAGED,
        ExecutionHandoffStage.SUBMITTED,
        ExecutionHandoffStage.RUNNING,
        ExecutionHandoffStage.EXITED,
        ExecutionHandoffStage.RETRIEVAL,
        ExecutionHandoffStage.PARSED,
        ExecutionHandoffStage.FAILED,
        ExecutionHandoffStage.CANCELLED,
    }:
        required_stage = {
            ArtifactType.EXECUTION_PLAN,
            ArtifactType.INCAR,
            ArtifactType.REMOTE_STAGE_MANIFEST,
        }
        missing = sorted((item.value for item in required_stage.difference(types)))
        if missing:
            raise ExecutionAcceptanceError(
                "SSH execution handoff is missing staged provenance artifacts: "
                + ", ".join(missing)
            )
    if target.transport is TransportKind.SSH and stage in {
        ExecutionHandoffStage.SUBMITTED,
        ExecutionHandoffStage.RUNNING,
        ExecutionHandoffStage.EXITED,
        ExecutionHandoffStage.RETRIEVAL,
        ExecutionHandoffStage.PARSED,
        ExecutionHandoffStage.FAILED,
        ExecutionHandoffStage.CANCELLED,
    }:
        required_submission = {ArtifactType.JOB_SCRIPT, ArtifactType.SCHEDULER_RECORD}
        missing = sorted((item.value for item in required_submission.difference(types)))
        if missing:
            raise ExecutionAcceptanceError(
                "submitted SSH execution is missing scheduler provenance artifacts: "
                + ", ".join(missing)
            )
    if stage is ExecutionHandoffStage.RETRIEVAL and retrieval is None:
        raise ExecutionAcceptanceError(
            "RETRIEVING attempt requires its RemoteRetrievalPackage for final handoff acceptance"
        )


def _validate_target_and_remote_job(
    *,
    attempt: ExecutionAttempt,
    target: ExecutionTargetProfile,
    remote_job: RemoteJob | None,
) -> None:
    if target.transport is TransportKind.LOCAL:
        if target.scheduler is not None:
            raise ExecutionAcceptanceError("local execution target must remain scheduler-free")
        if remote_job is not None:
            raise ExecutionAcceptanceError("local execution handoff cannot carry a RemoteJob")
        return

    if target.transport is not TransportKind.SSH:
        raise ExecutionAcceptanceError("unsupported execution transport in v0.4 acceptance")
    if target.scheduler is not SchedulerType.SLURM:
        raise ExecutionAcceptanceError(
            "v0.4 final remote acceptance supports only the concrete Slurm scheduler"
        )

    needs_job = attempt.status in {
        ExecutionAttemptStatus.QUEUED,
        ExecutionAttemptStatus.RUNNING,
        ExecutionAttemptStatus.EXITED,
        ExecutionAttemptStatus.RETRIEVING,
        ExecutionAttemptStatus.PARSED,
        ExecutionAttemptStatus.FAILED,
        ExecutionAttemptStatus.CANCELLED,
    }
    if needs_job and remote_job is None:
        raise ExecutionAcceptanceError(
            "submitted/terminal SSH ExecutionAttempt requires its persisted RemoteJob"
        )
    if remote_job is None:
        return
    if remote_job.execution_attempt_id != attempt.id:
        raise ExecutionAcceptanceError("RemoteJob does not belong to ExecutionAttempt")
    if remote_job.scheduler is not target.scheduler:
        raise ExecutionAcceptanceError("RemoteJob scheduler does not match execution target")
    expected_directory = f"execution/{attempt.id}"
    if remote_job.remote_directory != expected_directory:
        raise ExecutionAcceptanceError(
            "RemoteJob directory does not match the isolated ExecutionAttempt stage"
        )
    _validate_scheduler_attempt_state(attempt.status, remote_job.state)


def _validate_scheduler_attempt_state(
    attempt_status: ExecutionAttemptStatus,
    scheduler_state: SchedulerState,
) -> None:
    compatible: dict[SchedulerState, set[ExecutionAttemptStatus]] = {
        SchedulerState.PENDING: {
            ExecutionAttemptStatus.QUEUED,
            ExecutionAttemptStatus.RUNNING,
        },
        SchedulerState.RUNNING: {ExecutionAttemptStatus.RUNNING},
        SchedulerState.COMPLETED: {
            ExecutionAttemptStatus.EXITED,
            ExecutionAttemptStatus.RETRIEVING,
            ExecutionAttemptStatus.PARSED,
        },
        SchedulerState.FAILED: {ExecutionAttemptStatus.FAILED},
        SchedulerState.TIMEOUT: {ExecutionAttemptStatus.FAILED},
        SchedulerState.NODE_FAIL: {ExecutionAttemptStatus.FAILED},
        SchedulerState.OUT_OF_MEMORY: {ExecutionAttemptStatus.FAILED},
        SchedulerState.CANCELLED: {ExecutionAttemptStatus.CANCELLED},
        SchedulerState.UNKNOWN: {
            ExecutionAttemptStatus.QUEUED,
            ExecutionAttemptStatus.RUNNING,
        },
        SchedulerState.LOST: {
            ExecutionAttemptStatus.QUEUED,
            ExecutionAttemptStatus.RUNNING,
        },
    }
    if attempt_status not in compatible[scheduler_state]:
        raise ExecutionAcceptanceError(
            "scheduler state and ExecutionAttempt state are not a valid persisted v0.4 pair"
        )


def _validate_retrieval(
    *,
    plan: ExecutionPlan,
    attempt: ExecutionAttempt,
    target: ExecutionTargetProfile,
    remote_job: RemoteJob | None,
    retrieval: RemoteRetrievalPackage,
) -> None:
    if retrieval.attempt != attempt:
        raise ExecutionAcceptanceError("retrieval package does not match supplied ExecutionAttempt")
    if remote_job is None or retrieval.remote_job != remote_job:
        raise ExecutionAcceptanceError("retrieval package does not match supplied RemoteJob")
    if retrieval.manifest.plan_hash != plan.plan_hash:
        raise ExecutionAcceptanceError("retrieval manifest does not pin supplied ExecutionPlan")
    if retrieval.manifest.target.target_hash != target.target_hash:
        raise ExecutionAcceptanceError("retrieval manifest target does not match execution target")
    expected = {
        item.role: (item.artifact_type, item.retrieval_policy, item.required)
        for item in plan.expected_outputs
    }
    observed = {
        item.role: (item.artifact_type, item.retrieval_policy, item.required)
        for item in retrieval.manifest.files
    }
    if observed != expected:
        raise ExecutionAcceptanceError(
            "retrieval manifest does not cover the exact ExecutionPlan expected-output contract"
        )
    producer = ExecutionAttemptProducerRef(attempt.id)
    for artifact in retrieval.artifacts:
        if artifact.producer != producer:
            raise ExecutionAcceptanceError(
                "retrieval artifacts must be produced by the supplied ExecutionAttempt"
            )


def _validate_batch_observation(
    *,
    snapshot: BatchDispatchSnapshot,
    node_id: str,
    plan: ExecutionPlan,
    attempt: ExecutionAttempt,
) -> BatchNodeState:
    observation = next(
        (item for item in snapshot.observations if item.node_id == node_id),
        None,
    )
    if observation is None:
        raise ExecutionAcceptanceError("batch node is absent from scheduler-DAG snapshot")
    if observation.latest_attempt_id != attempt.id:
        raise ExecutionAcceptanceError("batch observation does not reference supplied latest attempt")
    if observation.latest_plan_hash != plan.plan_hash:
        raise ExecutionAcceptanceError("batch observation does not pin supplied ExecutionPlan")

    compatible: dict[ExecutionAttemptStatus, set[BatchNodeState]] = {
        ExecutionAttemptStatus.CREATED: {BatchNodeState.RESERVED},
        ExecutionAttemptStatus.STAGING: {BatchNodeState.STAGING},
        ExecutionAttemptStatus.QUEUED: {BatchNodeState.QUEUED},
        ExecutionAttemptStatus.RUNNING: {BatchNodeState.RUNNING},
        ExecutionAttemptStatus.EXITED: {BatchNodeState.COMPLETE},
        ExecutionAttemptStatus.RETRIEVING: {BatchNodeState.COMPLETE},
        ExecutionAttemptStatus.PARSED: {BatchNodeState.COMPLETE},
        ExecutionAttemptStatus.FAILED: {
            BatchNodeState.RECOVERY_REQUIRED,
            BatchNodeState.READY,
        },
        ExecutionAttemptStatus.CANCELLED: {
            BatchNodeState.RECOVERY_REQUIRED,
            BatchNodeState.READY,
        },
    }
    if observation.state not in compatible[attempt.status]:
        raise ExecutionAcceptanceError(
            "batch scheduler-DAG state is inconsistent with supplied ExecutionAttempt"
        )
    return observation.state


def _stage_for_attempt(status: ExecutionAttemptStatus) -> ExecutionHandoffStage:
    mapping = {
        ExecutionAttemptStatus.CREATED: ExecutionHandoffStage.CREATED,
        ExecutionAttemptStatus.STAGING: ExecutionHandoffStage.STAGED,
        ExecutionAttemptStatus.QUEUED: ExecutionHandoffStage.SUBMITTED,
        ExecutionAttemptStatus.RUNNING: ExecutionHandoffStage.RUNNING,
        ExecutionAttemptStatus.EXITED: ExecutionHandoffStage.EXITED,
        ExecutionAttemptStatus.RETRIEVING: ExecutionHandoffStage.RETRIEVAL,
        ExecutionAttemptStatus.PARSED: ExecutionHandoffStage.PARSED,
        ExecutionAttemptStatus.FAILED: ExecutionHandoffStage.FAILED,
        ExecutionAttemptStatus.CANCELLED: ExecutionHandoffStage.CANCELLED,
    }
    return mapping[status]
