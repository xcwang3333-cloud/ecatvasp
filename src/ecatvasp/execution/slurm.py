"""Slurm resource, submission, monitoring, and cancellation for v0.4 Blocks 5-6."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import PurePosixPath

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
from ecatvasp.domain.method import ExecutionSettings, canonical_json, canonical_sha256
from ecatvasp.execution.adapters import (
    CommandSpec,
    SchedulerObservation,
    SchedulerSubmission,
    TargetRelativePath,
    TransportAdapter,
    validate_adapter_target,
)
from ecatvasp.execution.remote import RemoteStagePackage
from ecatvasp.execution.ssh import remote_absolute_path
from ecatvasp.execution.targets import ExecutionTargetProfile, TransportKind

_SAFE_SLURM_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_JOB_ID = re.compile(r"^[0-9]+$")
_SLURM_STATE_TOKEN = re.compile(r"^[A-Z][A-Z0-9_]*$")

_SLURM_STATE_MAP: dict[str, SchedulerState] = {
    "PENDING": SchedulerState.PENDING,
    "CONFIGURING": SchedulerState.PENDING,
    "RESIZING": SchedulerState.PENDING,
    "REQUEUED": SchedulerState.PENDING,
    "REQUEUE_FED": SchedulerState.PENDING,
    "REQUEUE_HOLD": SchedulerState.PENDING,
    "RUNNING": SchedulerState.RUNNING,
    "COMPLETING": SchedulerState.RUNNING,
    "SUSPENDED": SchedulerState.RUNNING,
    "STOPPED": SchedulerState.RUNNING,
    "SIGNALING": SchedulerState.RUNNING,
    "STAGE_OUT": SchedulerState.RUNNING,
    "COMPLETED": SchedulerState.COMPLETED,
    "FAILED": SchedulerState.FAILED,
    "BOOT_FAIL": SchedulerState.FAILED,
    "DEADLINE": SchedulerState.FAILED,
    "PREEMPTED": SchedulerState.FAILED,
    "REVOKED": SchedulerState.FAILED,
    "SPECIAL_EXIT": SchedulerState.FAILED,
    "TIMEOUT": SchedulerState.TIMEOUT,
    "CANCELLED": SchedulerState.CANCELLED,
    "NODE_FAIL": SchedulerState.NODE_FAIL,
    "OUT_OF_MEMORY": SchedulerState.OUT_OF_MEMORY,
}


class SlurmSubmissionError(RuntimeError):
    """Raised when one verified remote stage cannot be submitted safely to Slurm."""


class SlurmObservationError(RuntimeError):
    """Raised when Slurm state cannot be observed without guessing."""


@dataclass(frozen=True, slots=True)
class ResolvedSchedulerResources:
    """Deterministic scheduler resources resolved from one ExecutionSettings value."""

    nodes: int
    cores: int
    mpi_ranks: int
    omp_threads: int
    ranks_per_node: int
    cores_per_node: int
    walltime_seconds: int
    memory_mb_total: int | None = None
    memory_mb_per_node: int | None = None
    queue_name: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "nodes",
            "cores",
            "mpi_ranks",
            "omp_threads",
            "ranks_per_node",
            "cores_per_node",
            "walltime_seconds",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")
        if self.mpi_ranks != self.nodes * self.ranks_per_node:
            raise ValueError("mpi_ranks must equal nodes * ranks_per_node")
        if self.cores != self.nodes * self.cores_per_node:
            raise ValueError("cores must equal nodes * cores_per_node")
        if self.cores != self.mpi_ranks * self.omp_threads:
            raise ValueError("resolved CPU topology must be exact")
        if self.memory_mb_total is None:
            if self.memory_mb_per_node is not None:
                raise ValueError("memory_mb_per_node requires memory_mb_total")
        else:
            if self.memory_mb_total < 1 or self.memory_mb_per_node is None:
                raise ValueError("memory values must be positive and complete")
            if self.memory_mb_per_node < 1:
                raise ValueError("memory_mb_per_node must be positive")
            if self.memory_mb_total != self.nodes * self.memory_mb_per_node:
                raise ValueError("memory_mb_total must equal nodes * memory_mb_per_node")
        if self.queue_name is not None and not _SAFE_SLURM_NAME.fullmatch(self.queue_name):
            raise ValueError("queue_name must be a single safe scheduler identifier")

    @property
    def resource_hash(self) -> str:
        """Return a deterministic hash of scheduler-effective resources."""

        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class SlurmJobScript:
    """Immutable scheduler script generated from verified execution provenance."""

    resources: ResolvedSchedulerResources
    remote_directory: str
    launcher: str | None
    executable: str
    module_loads: tuple[str, ...]
    text: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        TargetRelativePath(self.remote_directory)
        encoded = self.text.encode("utf-8")
        if hashlib.sha256(encoded).hexdigest() != self.sha256:
            raise ValueError("Slurm job script SHA-256 does not match text")
        if len(encoded) != self.size_bytes:
            raise ValueError("Slurm job script size does not match text")


@dataclass(frozen=True, slots=True)
class SlurmSubmissionPackage:
    """Successful Slurm submission plus immutable attempt-level provenance."""

    staged: RemoteStagePackage
    resources: ResolvedSchedulerResources
    job_script: SlurmJobScript
    submission: SchedulerSubmission
    attempt: ExecutionAttempt
    remote_job: RemoteJob
    artifacts: tuple[Artifact, ...]

    def __post_init__(self) -> None:
        if self.attempt.status is not ExecutionAttemptStatus.QUEUED:
            raise ValueError("SlurmSubmissionPackage attempt must be QUEUED")
        if self.remote_job.execution_attempt_id != self.attempt.id:
            raise ValueError("RemoteJob must belong to the submitted ExecutionAttempt")
        if self.remote_job.scheduler is not SchedulerType.SLURM:
            raise ValueError("SlurmSubmissionPackage requires a Slurm RemoteJob")
        if self.remote_job.scheduler_job_id != self.submission.scheduler_job_id:
            raise ValueError("RemoteJob scheduler id must match submission result")


@dataclass(frozen=True, slots=True)
class SlurmAdapter:
    """Concrete Slurm adapter over one transport with fail-closed observation semantics."""

    transport: TransportAdapter

    @property
    def scheduler_type(self) -> SchedulerType:
        return SchedulerType.SLURM

    def submit(
        self,
        *,
        target: ExecutionTargetProfile,
        script: TargetRelativePath,
    ) -> SchedulerSubmission:
        _validate_slurm_target(target, self.transport)
        script_path = PurePosixPath(script.value)
        if script_path.parent == PurePosixPath("."):
            raise SlurmSubmissionError("Slurm job script must live inside an isolated stage")
        stage = TargetRelativePath(script_path.parent.as_posix())
        absolute_stage = remote_absolute_path(target, stage)
        absolute_script = remote_absolute_path(target, script)
        result = self.transport.run(
            target=target,
            command=CommandSpec(
                argv=(
                    "sbatch",
                    "--parsable",
                    f"--chdir={absolute_stage}",
                    absolute_script,
                )
            ),
        )
        if result.exit_code != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
            raise SlurmSubmissionError(f"sbatch failed: {detail}")
        job_id = _parse_sbatch_job_id(result.stdout)
        return SchedulerSubmission(
            scheduler_job_id=job_id,
            raw_stdout=result.stdout,
            raw_stderr=result.stderr,
        )

    def query(
        self,
        *,
        target: ExecutionTargetProfile,
        scheduler_job_id: str,
    ) -> SchedulerObservation:
        _validate_observation_target(target, self.transport)
        _require_numeric_job_id(scheduler_job_id)

        squeue = self.transport.run(
            target=target,
            command=CommandSpec(
                argv=(
                    "squeue",
                    "--noheader",
                    f"--jobs={scheduler_job_id},{scheduler_job_id}",
                    "--format=%T",
                )
            ),
        )
        if squeue.exit_code != 0:
            detail = squeue.stderr.strip() or squeue.stdout.strip() or "no diagnostic output"
            raise SlurmObservationError(f"squeue query failed: {detail}")
        queue_states = [line.strip() for line in squeue.stdout.splitlines() if line.strip()]
        if queue_states:
            raw_state = _require_consistent_slurm_states(queue_states, source="squeue")
            return SchedulerObservation(
                scheduler_job_id=scheduler_job_id,
                state=_normalize_slurm_state(raw_state),
                raw_state=_slurm_state_token(raw_state),
            )

        sacct = self.transport.run(
            target=target,
            command=CommandSpec(
                argv=(
                    "sacct",
                    "--noheader",
                    "--parsable2",
                    f"--jobs={scheduler_job_id}",
                    "--format=JobIDRaw,State",
                )
            ),
        )
        if sacct.exit_code != 0:
            detail = sacct.stderr.strip() or sacct.stdout.strip() or "no diagnostic output"
            raise SlurmObservationError(f"sacct query failed: {detail}")

        accounting_states: list[str] = []
        for line in sacct.stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            fields = stripped.split("|")
            if len(fields) < 2:
                raise SlurmObservationError("sacct returned malformed parsable output")
            if fields[0].strip() == scheduler_job_id:
                state_text = fields[1].strip()
                if not state_text:
                    raise SlurmObservationError("sacct returned a blank state")
                accounting_states.append(state_text)

        if not accounting_states:
            return SchedulerObservation(
                scheduler_job_id=scheduler_job_id,
                state=SchedulerState.LOST,
                raw_state="NOT_FOUND",
            )
        raw_state = _require_consistent_slurm_states(accounting_states, source="sacct")
        return SchedulerObservation(
            scheduler_job_id=scheduler_job_id,
            state=_normalize_slurm_state(raw_state),
            raw_state=_slurm_state_token(raw_state),
        )

    def cancel(
        self,
        *,
        target: ExecutionTargetProfile,
        scheduler_job_id: str,
    ) -> SchedulerObservation:
        _validate_observation_target(target, self.transport)
        _require_numeric_job_id(scheduler_job_id)
        result = self.transport.run(
            target=target,
            command=CommandSpec(argv=("scancel", scheduler_job_id)),
        )
        if result.exit_code != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
            raise SlurmObservationError(f"scancel failed: {detail}")
        return self.query(target=target, scheduler_job_id=scheduler_job_id)


def resolve_scheduler_resources(settings: ExecutionSettings) -> ResolvedSchedulerResources:
    """Resolve portable execution intent into an exact Slurm-compatible CPU topology."""

    required = {
        "nodes": settings.nodes,
        "cores": settings.cores,
        "mpi_ranks": settings.mpi_ranks,
        "walltime_seconds": settings.walltime_seconds,
    }
    missing = sorted(name for name, value in required.items() if value is None)
    if missing:
        raise SlurmSubmissionError(
            "Slurm submission requires explicit execution resources: " + ", ".join(missing)
        )
    nodes = settings.nodes or 0
    cores = settings.cores or 0
    mpi_ranks = settings.mpi_ranks or 0
    walltime_seconds = settings.walltime_seconds or 0

    if mpi_ranks % nodes != 0:
        raise SlurmSubmissionError("mpi_ranks must be divisible by nodes")
    if cores % nodes != 0:
        raise SlurmSubmissionError("cores must be divisible by nodes")
    if settings.omp_threads is None:
        if cores != mpi_ranks:
            raise SlurmSubmissionError(
                "omp_threads must be explicit when cores differs from mpi_ranks"
            )
        omp_threads = 1
    else:
        omp_threads = settings.omp_threads
    if mpi_ranks * omp_threads != cores:
        raise SlurmSubmissionError(
            "Block 5 requires exact CPU topology: mpi_ranks * omp_threads == cores"
        )

    if settings.kpar is not None and mpi_ranks % settings.kpar != 0:
        raise SlurmSubmissionError("mpi_ranks must be divisible by KPAR")
    kpar = settings.kpar or 1
    ranks_per_k_group = mpi_ranks // kpar
    if settings.ncore is not None and ranks_per_k_group % settings.ncore != 0:
        raise SlurmSubmissionError("(mpi_ranks / KPAR) must be divisible by NCORE")

    memory_total = settings.memory_mb
    memory_per_node: int | None = None
    if memory_total is not None:
        if memory_total % nodes != 0:
            raise SlurmSubmissionError("memory_mb must be divisible by nodes")
        memory_per_node = memory_total // nodes

    queue = settings.partition
    if queue is not None and not _SAFE_SLURM_NAME.fullmatch(queue):
        raise SlurmSubmissionError("partition must be one safe Slurm partition identifier")

    return ResolvedSchedulerResources(
        nodes=nodes,
        cores=cores,
        mpi_ranks=mpi_ranks,
        omp_threads=omp_threads,
        ranks_per_node=mpi_ranks // nodes,
        cores_per_node=cores // nodes,
        walltime_seconds=walltime_seconds,
        memory_mb_total=memory_total,
        memory_mb_per_node=memory_per_node,
        queue_name=queue,
    )


def render_slurm_job_script(
    staged: RemoteStagePackage,
    resources: ResolvedSchedulerResources,
) -> SlurmJobScript:
    """Render one immutable Slurm script without changing scientific inputs."""

    target = staged.target
    _validate_slurm_target(target, None)
    if staged.attempt.status is not ExecutionAttemptStatus.STAGING:
        raise SlurmSubmissionError("Slurm rendering requires a STAGING ExecutionAttempt")
    if resources.resource_hash != resolve_scheduler_resources(
        staged.plan.execution_settings
    ).resource_hash:
        raise SlurmSubmissionError("resolved scheduler resources do not match ExecutionPlan")
    if target.vasp_executable != staged.plan.execution_settings.executable:
        raise SlurmSubmissionError("target executable does not match ExecutionPlan executable")
    if target.launcher is None and resources.mpi_ranks > 1:
        raise SlurmSubmissionError("multi-rank Slurm execution requires an explicit launcher")

    lines = [
        "#!/bin/bash",
        f"#SBATCH --job-name=ecatvasp-{str(staged.attempt.id)[:8]}",
        f"#SBATCH --nodes={resources.nodes}",
        f"#SBATCH --ntasks={resources.mpi_ranks}",
        f"#SBATCH --ntasks-per-node={resources.ranks_per_node}",
        f"#SBATCH --cpus-per-task={resources.omp_threads}",
        f"#SBATCH --time={_format_slurm_walltime(resources.walltime_seconds)}",
        "#SBATCH --output=stdout.log",
        "#SBATCH --error=stderr.log",
    ]
    if resources.memory_mb_per_node is not None:
        lines.append(f"#SBATCH --mem={resources.memory_mb_per_node}M")
    if resources.queue_name is not None:
        lines.append(f"#SBATCH --partition={resources.queue_name}")
    lines.extend(["", "set -euo pipefail"])
    for module in target.module_loads:
        lines.append(f"module load {module}")
    lines.append(f"export OMP_NUM_THREADS={resources.omp_threads}")
    command = (
        f"exec {target.launcher} {target.vasp_executable}"
        if target.launcher is not None
        else f"exec {target.vasp_executable}"
    )
    lines.append(command)
    text = "\n".join(lines) + "\n"
    encoded = text.encode("utf-8")
    return SlurmJobScript(
        resources=resources,
        remote_directory=staged.remote_directory.value,
        launcher=target.launcher,
        executable=target.vasp_executable,
        module_loads=target.module_loads,
        text=text,
        sha256=hashlib.sha256(encoded).hexdigest(),
        size_bytes=len(encoded),
    )


def submit_remote_slurm(
    *,
    staged: RemoteStagePackage,
    transport: TransportAdapter,
    scheduler: SlurmAdapter,
    submitted_at: datetime | None = None,
) -> SlurmSubmissionPackage:
    """Materialize, verify, and submit an immutable Slurm script for one remote stage."""

    validate_adapter_target(target=staged.target, transport=transport, scheduler=scheduler)
    if scheduler.transport is not transport:
        raise SlurmSubmissionError("SlurmAdapter must use the same transport as remote staging")
    if staged.attempt.status is not ExecutionAttemptStatus.STAGING:
        raise SlurmSubmissionError("Slurm submission requires a STAGING ExecutionAttempt")
    if submitted_at is not None and submitted_at.tzinfo is None:
        raise SlurmSubmissionError("submitted_at must be timezone-aware")

    resources = resolve_scheduler_resources(staged.plan.execution_settings)
    job_script = render_slurm_job_script(staged, resources)
    script_artifact, script_remote = _materialize_and_stage_job_script(
        staged=staged,
        transport=transport,
        job_script=job_script,
    )
    submission = scheduler.submit(target=staged.target, script=script_remote)
    timestamp = submitted_at or datetime.now(UTC)

    queued_attempt = replace(staged.attempt, status=ExecutionAttemptStatus.QUEUED)
    remote_job = RemoteJob(
        execution_attempt_id=queued_attempt.id,
        scheduler=SchedulerType.SLURM,
        scheduler_job_id=submission.scheduler_job_id,
        remote_directory=staged.remote_directory.value,
        state=SchedulerState.PENDING,
        submitted_at=timestamp,
    )
    scheduler_artifact = _persist_scheduler_record(
        staged=staged,
        resources=resources,
        job_script=job_script,
        submission=submission,
        submitted_at=timestamp,
    )
    return SlurmSubmissionPackage(
        staged=staged,
        resources=resources,
        job_script=job_script,
        submission=submission,
        attempt=queued_attempt,
        remote_job=remote_job,
        artifacts=(*staged.artifacts, script_artifact, scheduler_artifact),
    )


def _materialize_and_stage_job_script(
    *,
    staged: RemoteStagePackage,
    transport: TransportAdapter,
    job_script: SlurmJobScript,
) -> tuple[Artifact, TargetRelativePath]:
    artifact_directory = staged.project_root / "artifacts" / "execution" / str(staged.attempt.id)
    if not artifact_directory.is_dir():
        raise SlurmSubmissionError("ExecutionAttempt provenance directory is missing")
    local_path = artifact_directory / "job.slurm"
    if local_path.exists():
        raise SlurmSubmissionError("job script Artifact already exists")
    local_path.write_text(job_script.text, encoding="utf-8")
    script_remote = TargetRelativePath(f"{staged.remote_directory.value}/job.slurm")
    try:
        transport.upload(target=staged.target, local_path=local_path, destination=script_remote)
        _verify_remote_file(
            target=staged.target,
            transport=transport,
            path=script_remote,
            expected_sha=job_script.sha256,
            expected_size=job_script.size_bytes,
        )
    except Exception:
        local_path.unlink(missing_ok=True)
        raise

    relative = local_path.relative_to(staged.project_root).as_posix()
    return (
        Artifact(
            artifact_type=ArtifactType.JOB_SCRIPT,
            producer=ExecutionAttemptProducerRef(staged.attempt.id),
            availability=ArtifactAvailability.BOTH,
            retrieval_policy=RetrievalPolicy.ALWAYS,
            local_path=relative,
            remote_path=script_remote.value,
            size_bytes=job_script.size_bytes,
            sha256=job_script.sha256,
        ),
        script_remote,
    )


def _persist_scheduler_record(
    *,
    staged: RemoteStagePackage,
    resources: ResolvedSchedulerResources,
    job_script: SlurmJobScript,
    submission: SchedulerSubmission,
    submitted_at: datetime,
) -> Artifact:
    artifact_directory = staged.project_root / "artifacts" / "execution" / str(staged.attempt.id)
    path = artifact_directory / "scheduler-submit.json"
    if path.exists():
        raise SlurmSubmissionError("scheduler submission record already exists")
    stdout_sha256 = hashlib.sha256(submission.raw_stdout.encode("utf-8")).hexdigest()
    stderr_sha256 = hashlib.sha256(submission.raw_stderr.encode("utf-8")).hexdigest()
    text = canonical_json(
        {
            "schema_version": 1,
            "attempt_id": staged.attempt.id,
            "scheduler": SchedulerType.SLURM,
            "scheduler_job_id": submission.scheduler_job_id,
            "target": staged.target.sanitized_environment(),
            "remote_directory": staged.remote_directory.value,
            "resource_hash": resources.resource_hash,
            "resources": resources,
            "job_script_sha256": job_script.sha256,
            "submitted_at": submitted_at.isoformat(),
            "submit_stdout_sha256": stdout_sha256,
            "submit_stderr_sha256": stderr_sha256,
        }
    ) + "\n"
    path.write_text(text, encoding="utf-8")
    body = text.encode("utf-8")
    relative = path.relative_to(staged.project_root).as_posix()
    return Artifact(
        artifact_type=ArtifactType.SCHEDULER_RECORD,
        producer=ExecutionAttemptProducerRef(staged.attempt.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=relative,
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
    )


def _verify_remote_file(
    *,
    target: ExecutionTargetProfile,
    transport: TransportAdapter,
    path: TargetRelativePath,
    expected_sha: str,
    expected_size: int,
) -> None:
    absolute = remote_absolute_path(target, path)
    digest_result = transport.run(
        target=target,
        command=CommandSpec(argv=("sha256sum", "--", absolute)),
    )
    if digest_result.exit_code != 0:
        raise SlurmSubmissionError("remote job-script SHA-256 verification failed")
    fields = digest_result.stdout.strip().split()
    if not fields or fields[0].lower() != expected_sha:
        raise SlurmSubmissionError("remote job-script SHA-256 mismatch")
    size_result = transport.run(
        target=target,
        command=CommandSpec(argv=("stat", "-c", "%s", "--", absolute)),
    )
    if size_result.exit_code != 0:
        raise SlurmSubmissionError("remote job-script size verification failed")
    try:
        size = int(size_result.stdout.strip())
    except ValueError as exc:
        raise SlurmSubmissionError("remote job-script size is not an integer") from exc
    if size != expected_size:
        raise SlurmSubmissionError("remote job-script size mismatch")


def _validate_slurm_target(
    target: ExecutionTargetProfile,
    transport: TransportAdapter | None,
) -> None:
    if target.transport is not TransportKind.SSH or target.scheduler is not SchedulerType.SLURM:
        raise SlurmSubmissionError("Slurm execution requires an SSH+SLURM target")
    if transport is not None and transport.transport_kind is not TransportKind.SSH:
        raise SlurmSubmissionError("Slurm execution requires an SSH transport")


def _validate_observation_target(
    target: ExecutionTargetProfile,
    transport: TransportAdapter,
) -> None:
    try:
        _validate_slurm_target(target, transport)
    except SlurmSubmissionError as exc:
        raise SlurmObservationError(str(exc)) from exc


def _parse_sbatch_job_id(stdout: str) -> str:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise SlurmSubmissionError("sbatch --parsable must return exactly one job-id line")
    job_id = lines[0].split(";", 1)[0]
    if not _JOB_ID.fullmatch(job_id):
        raise SlurmSubmissionError("sbatch returned an unsupported Slurm job id")
    return job_id


def _require_numeric_job_id(job_id: str) -> None:
    if not _JOB_ID.fullmatch(job_id):
        raise SlurmObservationError("Slurm monitoring requires a numeric scheduler job id")


def _slurm_state_token(raw_state: str) -> str:
    stripped = raw_state.strip()
    if not stripped:
        raise SlurmObservationError("Slurm returned a blank scheduler state")
    token = stripped.split(maxsplit=1)[0].rstrip("+").upper()
    if not _SLURM_STATE_TOKEN.fullmatch(token):
        raise SlurmObservationError("Slurm returned an unsafe scheduler state token")
    return token


def _normalize_slurm_state(raw_state: str) -> SchedulerState:
    return _SLURM_STATE_MAP.get(_slurm_state_token(raw_state), SchedulerState.UNKNOWN)


def _require_consistent_slurm_states(states: list[str], *, source: str) -> str:
    tokens = {_slurm_state_token(state) for state in states}
    if len(tokens) != 1:
        raise SlurmObservationError(f"{source} returned conflicting states for one job")
    return next(iter(tokens))


def _format_slurm_walltime(seconds: int) -> str:
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    if days:
        return f"{days}-{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
