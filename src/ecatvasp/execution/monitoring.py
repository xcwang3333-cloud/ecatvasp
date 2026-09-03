"""Scheduler reconciliation and bounded VASP runtime telemetry for v0.4 Block 6."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from ecatvasp.domain import (
    Artifact,
    ArtifactAvailability,
    ArtifactType,
    ExecutionAttempt,
    ExecutionAttemptProducerRef,
    ExecutionAttemptStatus,
    RemoteJob,
    RetrievalPolicy,
    SchedulerState,
    SchedulerType,
)
from ecatvasp.domain.method import canonical_json, canonical_sha256
from ecatvasp.execution.adapters import (
    CommandSpec,
    SchedulerObservation,
    TargetRelativePath,
    TransportAdapter,
    validate_adapter_target,
)
from ecatvasp.execution.slurm import SlurmAdapter, SlurmObservationError
from ecatvasp.execution.ssh import remote_absolute_path
from ecatvasp.execution.targets import ExecutionTargetProfile

_ELECTRONIC_STEP = re.compile(r"^\s*(?:DAV|RMM|CG):\s*(\d+)")
_IONIC_STEP = re.compile(r"^\s*(\d+)\s+F=")


class ExecutionMonitoringError(RuntimeError):
    """Raised when scheduler truth cannot be reconciled without guessing."""


@dataclass(frozen=True, slots=True)
class VaspRuntimeProgress:
    """Bounded runtime telemetry that deliberately carries no convergence decision."""

    oszicar_present: bool
    outcar_present: bool
    ionic_step: int | None = None
    electronic_iteration: int | None = None
    reached_required_accuracy_marker: bool = False
    timing_footer_marker: bool = False

    @property
    def progress_hash(self) -> str:
        """Return a deterministic digest for this telemetry observation."""

        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class SlurmMonitoringPackage:
    """One immutable scheduler observation reconciled onto execution-domain views."""

    attempt: ExecutionAttempt
    remote_job: RemoteJob
    observation: SchedulerObservation
    progress: VaspRuntimeProgress
    observed_at: datetime
    artifact: Artifact

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("SlurmMonitoringPackage observed_at must be timezone-aware")
        if self.remote_job.execution_attempt_id != self.attempt.id:
            raise ValueError("RemoteJob must belong to the monitored ExecutionAttempt")
        if self.remote_job.scheduler is not SchedulerType.SLURM:
            raise ValueError("SlurmMonitoringPackage requires a Slurm RemoteJob")
        if self.remote_job.scheduler_job_id != self.observation.scheduler_job_id:
            raise ValueError("scheduler observation must match RemoteJob scheduler id")


def monitor_remote_slurm(
    *,
    project_root: Path | str,
    attempt: ExecutionAttempt,
    remote_job: RemoteJob,
    target: ExecutionTargetProfile,
    transport: TransportAdapter,
    scheduler: SlurmAdapter,
    observed_at: datetime | None = None,
) -> SlurmMonitoringPackage:
    """Query Slurm once, reconcile execution state, and record bounded VASP telemetry."""

    _validate_monitoring_context(
        attempt=attempt,
        remote_job=remote_job,
        target=target,
        transport=transport,
        scheduler=scheduler,
    )
    observation = scheduler.query(
        target=target,
        scheduler_job_id=remote_job.scheduler_job_id,
    )
    return _reconcile_observation(
        project_root=project_root,
        attempt=attempt,
        remote_job=remote_job,
        target=target,
        transport=transport,
        observation=observation,
        observed_at=observed_at,
    )


def cancel_remote_slurm(
    *,
    project_root: Path | str,
    attempt: ExecutionAttempt,
    remote_job: RemoteJob,
    target: ExecutionTargetProfile,
    transport: TransportAdapter,
    scheduler: SlurmAdapter,
    observed_at: datetime | None = None,
) -> SlurmMonitoringPackage:
    """Request cancellation, then reconcile only the scheduler state actually observed."""

    _validate_monitoring_context(
        attempt=attempt,
        remote_job=remote_job,
        target=target,
        transport=transport,
        scheduler=scheduler,
    )
    observation = scheduler.cancel(
        target=target,
        scheduler_job_id=remote_job.scheduler_job_id,
    )
    return _reconcile_observation(
        project_root=project_root,
        attempt=attempt,
        remote_job=remote_job,
        target=target,
        transport=transport,
        observation=observation,
        observed_at=observed_at,
    )


def probe_vasp_runtime_progress(
    *,
    target: ExecutionTargetProfile,
    transport: TransportAdapter,
    remote_directory: str,
) -> VaspRuntimeProgress:
    """Read only small OSZICAR/OUTCAR tails; never retrieve full scientific outputs."""

    directory = TargetRelativePath(remote_directory)
    oszicar = _read_remote_tail(
        target=target,
        transport=transport,
        path=TargetRelativePath(f"{directory.value}/OSZICAR"),
        lines=40,
    )
    outcar = _read_remote_tail(
        target=target,
        transport=transport,
        path=TargetRelativePath(f"{directory.value}/OUTCAR"),
        lines=200,
    )

    ionic_step: int | None = None
    electronic_iteration: int | None = None
    if oszicar is not None:
        for line in oszicar.splitlines():
            electronic_match = _ELECTRONIC_STEP.match(line)
            if electronic_match is not None:
                electronic_iteration = int(electronic_match.group(1))
            ionic_match = _IONIC_STEP.match(line)
            if ionic_match is not None:
                ionic_step = int(ionic_match.group(1))

    outcar_text = outcar or ""
    return VaspRuntimeProgress(
        oszicar_present=oszicar is not None,
        outcar_present=outcar is not None,
        ionic_step=ionic_step,
        electronic_iteration=electronic_iteration,
        reached_required_accuracy_marker=(
            "reached required accuracy" in outcar_text.lower()
        ),
        timing_footer_marker=(
            "General timing and accounting informations for this job" in outcar_text
        ),
    )


def _reconcile_observation(
    *,
    project_root: Path | str,
    attempt: ExecutionAttempt,
    remote_job: RemoteJob,
    target: ExecutionTargetProfile,
    transport: TransportAdapter,
    observation: SchedulerObservation,
    observed_at: datetime | None,
) -> SlurmMonitoringPackage:
    if observation.scheduler_job_id != remote_job.scheduler_job_id:
        raise ExecutionMonitoringError("scheduler observation does not match RemoteJob")
    timestamp = observed_at or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise ExecutionMonitoringError("observed_at must be timezone-aware")
    timestamp = timestamp.astimezone(UTC)

    next_status = _reconciled_attempt_status(attempt.status, observation.state)
    updated_attempt = replace(attempt, status=next_status)
    updated_job = replace(remote_job, state=observation.state)

    if observation.state in {
        SchedulerState.PENDING,
        SchedulerState.UNKNOWN,
        SchedulerState.LOST,
    }:
        progress = VaspRuntimeProgress(oszicar_present=False, outcar_present=False)
    else:
        progress = probe_vasp_runtime_progress(
            target=target,
            transport=transport,
            remote_directory=remote_job.remote_directory,
        )

    artifact = _persist_monitoring_record(
        project_root=project_root,
        attempt=updated_attempt,
        remote_job=updated_job,
        target=target,
        observation=observation,
        progress=progress,
        observed_at=timestamp,
    )
    return SlurmMonitoringPackage(
        attempt=updated_attempt,
        remote_job=updated_job,
        observation=observation,
        progress=progress,
        observed_at=timestamp,
        artifact=artifact,
    )


def _validate_monitoring_context(
    *,
    attempt: ExecutionAttempt,
    remote_job: RemoteJob,
    target: ExecutionTargetProfile,
    transport: TransportAdapter,
    scheduler: SlurmAdapter,
) -> None:
    validate_adapter_target(target=target, transport=transport, scheduler=scheduler)
    if scheduler.transport is not transport:
        raise ExecutionMonitoringError(
            "SlurmAdapter must use the same transport as the monitored RemoteJob"
        )
    if remote_job.execution_attempt_id != attempt.id:
        raise ExecutionMonitoringError("RemoteJob does not belong to ExecutionAttempt")
    if remote_job.scheduler is not SchedulerType.SLURM:
        raise ExecutionMonitoringError("Block 6 monitoring requires a Slurm RemoteJob")
    if attempt.status in {
        ExecutionAttemptStatus.CREATED,
        ExecutionAttemptStatus.STAGING,
        ExecutionAttemptStatus.RETRIEVING,
        ExecutionAttemptStatus.PARSED,
    }:
        raise ExecutionMonitoringError(
            f"ExecutionAttempt status {attempt.status.value!r} is not monitorable"
        )


def _reconciled_attempt_status(
    current: ExecutionAttemptStatus,
    scheduler_state: SchedulerState,
) -> ExecutionAttemptStatus:
    if scheduler_state in {SchedulerState.UNKNOWN, SchedulerState.LOST}:
        return current
    if scheduler_state is SchedulerState.PENDING:
        if current is not ExecutionAttemptStatus.QUEUED:
            raise ExecutionMonitoringError("scheduler PENDING would regress ExecutionAttempt state")
        return ExecutionAttemptStatus.QUEUED
    if scheduler_state is SchedulerState.RUNNING:
        if current not in {ExecutionAttemptStatus.QUEUED, ExecutionAttemptStatus.RUNNING}:
            raise ExecutionMonitoringError("scheduler RUNNING conflicts with terminal attempt state")
        return ExecutionAttemptStatus.RUNNING
    if scheduler_state is SchedulerState.COMPLETED:
        if current in {
            ExecutionAttemptStatus.QUEUED,
            ExecutionAttemptStatus.RUNNING,
            ExecutionAttemptStatus.EXITED,
        }:
            return ExecutionAttemptStatus.EXITED
        raise ExecutionMonitoringError("scheduler COMPLETED conflicts with attempt state")
    if scheduler_state is SchedulerState.CANCELLED:
        if current in {
            ExecutionAttemptStatus.QUEUED,
            ExecutionAttemptStatus.RUNNING,
            ExecutionAttemptStatus.CANCELLED,
        }:
            return ExecutionAttemptStatus.CANCELLED
        raise ExecutionMonitoringError("scheduler CANCELLED conflicts with attempt state")
    if scheduler_state in {
        SchedulerState.FAILED,
        SchedulerState.TIMEOUT,
        SchedulerState.NODE_FAIL,
        SchedulerState.OUT_OF_MEMORY,
    }:
        if current in {
            ExecutionAttemptStatus.QUEUED,
            ExecutionAttemptStatus.RUNNING,
            ExecutionAttemptStatus.FAILED,
        }:
            return ExecutionAttemptStatus.FAILED
        raise ExecutionMonitoringError("scheduler failure conflicts with attempt state")
    raise AssertionError(f"unhandled scheduler state: {scheduler_state}")


def _read_remote_tail(
    *,
    target: ExecutionTargetProfile,
    transport: TransportAdapter,
    path: TargetRelativePath,
    lines: int,
) -> str | None:
    absolute = remote_absolute_path(target, path)
    exists = transport.run(
        target=target,
        command=CommandSpec(argv=("test", "-f", absolute)),
    )
    if exists.exit_code == 1:
        return None
    if exists.exit_code != 0:
        raise ExecutionMonitoringError(f"remote file probe failed for {path.value}")
    tail = transport.run(
        target=target,
        command=CommandSpec(argv=("tail", "-n", str(lines), "--", absolute)),
    )
    if tail.exit_code != 0:
        raise ExecutionMonitoringError(f"remote tail failed for {path.value}")
    return tail.stdout


def _persist_monitoring_record(
    *,
    project_root: Path | str,
    attempt: ExecutionAttempt,
    remote_job: RemoteJob,
    target: ExecutionTargetProfile,
    observation: SchedulerObservation,
    progress: VaspRuntimeProgress,
    observed_at: datetime,
) -> Artifact:
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise ExecutionMonitoringError("project_root must be an existing directory")
    artifact_directory = root / "artifacts" / "execution" / str(attempt.id)
    if not artifact_directory.is_dir():
        raise ExecutionMonitoringError("ExecutionAttempt provenance directory is missing")
    stamp = observed_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = artifact_directory / f"scheduler-observation-{stamp}.json"
    if path.exists():
        raise ExecutionMonitoringError("scheduler observation Artifact already exists")

    text = canonical_json(
        {
            "schema_version": 1,
            "attempt_id": attempt.id,
            "remote_job_id": remote_job.id,
            "scheduler": SchedulerType.SLURM,
            "scheduler_job_id": remote_job.scheduler_job_id,
            "scheduler_state": observation.state,
            "raw_state": observation.raw_state,
            "target": target.sanitized_environment(),
            "remote_directory": remote_job.remote_directory,
            "observed_at": observed_at.isoformat(),
            "vasp_runtime_progress": progress,
        }
    ) + "\n"
    path.write_text(text, encoding="utf-8")
    body = text.encode("utf-8")
    return Artifact(
        artifact_type=ArtifactType.SCHEDULER_RECORD,
        producer=ExecutionAttemptProducerRef(attempt.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=path.relative_to(root).as_posix(),
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
    )


__all__ = [
    "ExecutionMonitoringError",
    "SlurmMonitoringPackage",
    "SlurmObservationError",
    "VaspRuntimeProgress",
    "cancel_remote_slurm",
    "monitor_remote_slurm",
    "probe_vasp_runtime_progress",
]
