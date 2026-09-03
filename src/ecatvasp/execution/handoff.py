"""Final v0.4 execution-result handoff without scientific interpretation."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath

from ecatvasp.domain import (
    Artifact,
    ArtifactAvailability,
    ArtifactType,
    Calculation,
    ExecutionAttempt,
    ExecutionAttemptProducerRef,
    ExecutionAttemptStatus,
    SchedulerState,
    canonical_sha256,
)
from ecatvasp.domain.ids import (
    ArtifactId,
    CalculationId,
    ExecutionAttemptId,
    RemoteJobId,
)
from ecatvasp.execution.local import LocalExecutionResult
from ecatvasp.execution.provenance import validate_execution_attempt_plan
from ecatvasp.execution.retrieval import RemoteRetrievalPackage
from ecatvasp.execution.runtime import LocalRuntimePackage
from ecatvasp.execution.targets import ExecutionEnvironmentSnapshot, TransportKind
from ecatvasp.vasp.execution_plan import ExecutionPlan

_HANDOFF_ATTEMPT_STATES = frozenset(
    {
        ExecutionAttemptStatus.EXITED,
        ExecutionAttemptStatus.RETRIEVING,
        ExecutionAttemptStatus.PARSED,
        ExecutionAttemptStatus.FAILED,
        ExecutionAttemptStatus.CANCELLED,
    }
)
_REQUIRED_OUTPUT_STATES = frozenset(
    {
        ExecutionAttemptStatus.EXITED,
        ExecutionAttemptStatus.RETRIEVING,
        ExecutionAttemptStatus.PARSED,
    }
)


class ExecutionHandoffError(RuntimeError):
    """Raised when execution results cannot cross the parsing boundary safely."""


class ExecutionHandoffSource(StrEnum):
    """Concrete execution path that produced one result handoff."""

    LOCAL = "local"
    REMOTE = "remote"


@dataclass(frozen=True, slots=True)
class LocalOutputPackage:
    """Integrity-verified local VASP outputs persisted outside the transient run directory."""

    project_root: Path
    attempt: ExecutionAttempt
    output_artifacts: tuple[Artifact, ...]

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.output_artifacts, key=lambda item: item.artifact_type.value))
        if len({item.artifact_type for item in ordered}) != len(ordered):
            raise ValueError("LocalOutputPackage artifact types must be unique")
        expected_producer = ExecutionAttemptProducerRef(self.attempt.id)
        if any(item.producer != expected_producer for item in ordered):
            raise ValueError("local output Artifacts must be produced by the exact attempt")
        object.__setattr__(self, "output_artifacts", ordered)


@dataclass(frozen=True, slots=True)
class ExecutionResultHandoff:
    """Auditable execution result passed to future parsing without claiming convergence."""

    calculation_id: CalculationId
    execution_attempt_id: ExecutionAttemptId
    attempt_number: int
    plan_hash: str
    input_manifest_hash: str
    execution_settings_hash: str
    target: ExecutionEnvironmentSnapshot
    source: ExecutionHandoffSource
    output_artifacts: tuple[Artifact, ...]
    remote_job_id: RemoteJobId | None = None
    scheduler_state: SchedulerState | None = None
    process_exit_code: int | None = None
    retrieval_manifest_artifact_id: ArtifactId | None = None
    handoff_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.attempt_number < 1:
            raise ValueError("attempt_number must be positive")
        _validate_sha256(self.plan_hash, "plan_hash")
        _validate_sha256(self.input_manifest_hash, "input_manifest_hash")
        _validate_sha256(self.execution_settings_hash, "execution_settings_hash")

        ordered = tuple(sorted(self.output_artifacts, key=lambda item: item.artifact_type.value))
        if len({item.artifact_type for item in ordered}) != len(ordered):
            raise ValueError("ExecutionResultHandoff artifact types must be unique")
        expected_producer = ExecutionAttemptProducerRef(self.execution_attempt_id)
        if any(item.producer != expected_producer for item in ordered):
            raise ValueError("handoff output Artifacts must be produced by the exact attempt")
        object.__setattr__(self, "output_artifacts", ordered)

        if self.source is ExecutionHandoffSource.LOCAL:
            if self.target.transport is not TransportKind.LOCAL:
                raise ValueError("local execution handoff requires a LOCAL target snapshot")
            if self.remote_job_id is not None or self.scheduler_state is not None:
                raise ValueError("local execution handoff cannot carry scheduler identity")
            if self.retrieval_manifest_artifact_id is not None:
                raise ValueError("local execution handoff cannot carry a remote retrieval manifest")
        else:
            if self.target.transport is not TransportKind.SSH:
                raise ValueError("remote execution handoff requires an SSH target snapshot")
            if self.remote_job_id is None or self.scheduler_state is None:
                raise ValueError("remote execution handoff requires RemoteJob provenance")
            if self.retrieval_manifest_artifact_id is None:
                raise ValueError("remote execution handoff requires retrieval-manifest provenance")
            if self.process_exit_code is not None:
                raise ValueError("remote execution handoff does not invent a local process exit code")

        object.__setattr__(
            self,
            "handoff_hash",
            canonical_sha256(
                {
                    "calculation_id": self.calculation_id,
                    "execution_attempt_id": self.execution_attempt_id,
                    "attempt_number": self.attempt_number,
                    "plan_hash": self.plan_hash,
                    "input_manifest_hash": self.input_manifest_hash,
                    "execution_settings_hash": self.execution_settings_hash,
                    "target": self.target,
                    "source": self.source,
                    "output_artifacts": ordered,
                    "remote_job_id": self.remote_job_id,
                    "scheduler_state": self.scheduler_state,
                    "process_exit_code": self.process_exit_code,
                    "retrieval_manifest_artifact_id": self.retrieval_manifest_artifact_id,
                }
            ),
        )

    @property
    def locally_available_output_artifact_ids(self) -> tuple[ArtifactId, ...]:
        """Artifacts that a local parser may consume without another retrieval operation."""

        return tuple(
            item.id
            for item in self.output_artifacts
            if item.availability in {ArtifactAvailability.LOCAL, ArtifactAvailability.BOTH}
        )


def collect_local_outputs(
    *,
    project_root: Path | str,
    calculation: Calculation,
    plan: ExecutionPlan,
    runtime: LocalRuntimePackage,
    result: LocalExecutionResult,
) -> LocalOutputPackage:
    """Persist and integrity-record local VASP outputs without parsing their scientific content."""

    _validate_local_result_context(
        project_root=project_root,
        calculation=calculation,
        plan=plan,
        runtime=runtime,
        result=result,
    )
    _require_unique_expected_artifact_types(plan)

    root = Path(project_root).resolve()
    output_root = root / "artifacts" / "execution" / str(result.attempt.id) / "outputs"
    artifacts: list[Artifact] = []
    for expected in plan.expected_outputs:
        source = _local_run_output(runtime.run_directory, expected.relative_path)
        if not source.exists():
            if expected.required and result.attempt.status in _REQUIRED_OUTPUT_STATES:
                raise ExecutionHandoffError(
                    f"required local VASP output is missing: {expected.role}"
                )
            artifacts.append(
                Artifact(
                    artifact_type=expected.artifact_type,
                    producer=ExecutionAttemptProducerRef(result.attempt.id),
                    availability=ArtifactAvailability.MISSING,
                    retrieval_policy=expected.retrieval_policy,
                )
            )
            continue
        if not source.is_file():
            raise ExecutionHandoffError(
                f"local VASP output exists but is not a file: {expected.relative_path}"
            )

        size_bytes = source.stat().st_size
        sha256 = _sha256_file(source)
        destination = _project_output_path(root, output_root, expected.relative_path)
        _persist_verified_output(
            source=source,
            destination=destination,
            expected_sha256=sha256,
            expected_size=size_bytes,
        )
        artifacts.append(
            Artifact(
                artifact_type=expected.artifact_type,
                producer=ExecutionAttemptProducerRef(result.attempt.id),
                availability=ArtifactAvailability.LOCAL,
                retrieval_policy=expected.retrieval_policy,
                local_path=destination.relative_to(root).as_posix(),
                size_bytes=size_bytes,
                sha256=sha256,
            )
        )

    return LocalOutputPackage(
        project_root=root,
        attempt=result.attempt,
        output_artifacts=tuple(artifacts),
    )


def build_local_execution_handoff(
    *,
    calculation: Calculation,
    plan: ExecutionPlan,
    runtime: LocalRuntimePackage,
    result: LocalExecutionResult,
    outputs: LocalOutputPackage,
) -> ExecutionResultHandoff:
    """Build the scheduler-free final execution handoff for a local attempt."""

    validate_execution_attempt_plan(
        plan=plan,
        calculation=calculation,
        attempt=result.attempt,
    )
    if result.attempt.status not in _HANDOFF_ATTEMPT_STATES:
        raise ExecutionHandoffError("local attempt has not reached an execution-result boundary")
    if runtime.attempt.id != result.attempt.id or outputs.attempt.id != result.attempt.id:
        raise ExecutionHandoffError("local runtime/result/output packages refer to different attempts")
    if runtime.plan.plan_hash != plan.plan_hash:
        raise ExecutionHandoffError("local runtime package does not use this ExecutionPlan")
    _validate_output_contract(plan, result.attempt, outputs.output_artifacts)

    process_exit_code = (
        result.command_result.exit_code if result.command_result is not None else None
    )
    return ExecutionResultHandoff(
        calculation_id=calculation.id,
        execution_attempt_id=result.attempt.id,
        attempt_number=result.attempt.attempt_number,
        plan_hash=plan.plan_hash,
        input_manifest_hash=plan.input_manifest_sha256,
        execution_settings_hash=plan.execution_settings_hash,
        target=runtime.target.sanitized_environment(),
        source=ExecutionHandoffSource.LOCAL,
        output_artifacts=outputs.output_artifacts,
        process_exit_code=process_exit_code,
    )


def build_remote_execution_handoff(
    *,
    calculation: Calculation,
    plan: ExecutionPlan,
    retrieval: RemoteRetrievalPackage,
) -> ExecutionResultHandoff:
    """Build the final remote execution handoff from one verified retrieval package."""

    attempt = retrieval.attempt
    validate_execution_attempt_plan(
        plan=plan,
        calculation=calculation,
        attempt=attempt,
    )
    if attempt.status not in _HANDOFF_ATTEMPT_STATES:
        raise ExecutionHandoffError("remote attempt has not reached an execution-result boundary")
    if retrieval.manifest.plan_hash != plan.plan_hash:
        raise ExecutionHandoffError("retrieval manifest does not pin this ExecutionPlan")
    if retrieval.remote_job.execution_attempt_id != attempt.id:
        raise ExecutionHandoffError("retrieval RemoteJob belongs to another ExecutionAttempt")
    _validate_output_contract(plan, attempt, retrieval.output_artifacts)

    return ExecutionResultHandoff(
        calculation_id=calculation.id,
        execution_attempt_id=attempt.id,
        attempt_number=attempt.attempt_number,
        plan_hash=plan.plan_hash,
        input_manifest_hash=plan.input_manifest_sha256,
        execution_settings_hash=plan.execution_settings_hash,
        target=retrieval.manifest.target,
        source=ExecutionHandoffSource.REMOTE,
        output_artifacts=retrieval.output_artifacts,
        remote_job_id=retrieval.remote_job.id,
        scheduler_state=retrieval.remote_job.state,
        retrieval_manifest_artifact_id=retrieval.manifest_artifact.id,
    )


def _validate_local_result_context(
    *,
    project_root: Path | str,
    calculation: Calculation,
    plan: ExecutionPlan,
    runtime: LocalRuntimePackage,
    result: LocalExecutionResult,
) -> None:
    validate_execution_attempt_plan(
        plan=plan,
        calculation=calculation,
        attempt=result.attempt,
    )
    if result.attempt.status not in _HANDOFF_ATTEMPT_STATES:
        raise ExecutionHandoffError("local output collection requires a terminal/post-exit attempt")
    if runtime.target.transport is not TransportKind.LOCAL:
        raise ExecutionHandoffError("local output collection requires a LOCAL execution target")
    if runtime.plan.plan_hash != plan.plan_hash:
        raise ExecutionHandoffError("local runtime package does not use this ExecutionPlan")
    if runtime.attempt.id != result.attempt.id:
        raise ExecutionHandoffError("LocalExecutionResult does not belong to runtime attempt")
    if Path(project_root).resolve() != runtime.project_root.resolve():
        raise ExecutionHandoffError("project_root does not match LocalRuntimePackage")
    if result.launched and result.command_result is None:
        raise ExecutionHandoffError("launched local execution is missing its process result")
    if not result.launched and result.command_result is not None:
        raise ExecutionHandoffError("unlaunched local execution cannot carry a process result")


def _validate_output_contract(
    plan: ExecutionPlan,
    attempt: ExecutionAttempt,
    artifacts: tuple[Artifact, ...],
) -> None:
    _require_unique_expected_artifact_types(plan)
    expected = {item.artifact_type: item for item in plan.expected_outputs}
    actual = {item.artifact_type: item for item in artifacts}
    if len(actual) != len(artifacts):
        raise ExecutionHandoffError("output Artifact types must be unique")
    if set(actual) != set(expected):
        raise ExecutionHandoffError("execution output Artifacts do not match ExecutionPlan contract")

    producer = ExecutionAttemptProducerRef(attempt.id)
    for artifact_type, output in expected.items():
        artifact = actual[artifact_type]
        if artifact.producer != producer:
            raise ExecutionHandoffError("execution output Artifact producer does not match attempt")
        if output.required and attempt.status in _REQUIRED_OUTPUT_STATES:
            if artifact.availability is ArtifactAvailability.MISSING:
                raise ExecutionHandoffError(
                    f"required execution output is missing at handoff: {output.role}"
                )
        if artifact.availability is not ArtifactAvailability.MISSING:
            if artifact.sha256 is None or artifact.size_bytes is None:
                raise ExecutionHandoffError(
                    "available execution outputs require SHA-256 and size provenance"
                )


def _require_unique_expected_artifact_types(plan: ExecutionPlan) -> None:
    types = tuple(item.artifact_type for item in plan.expected_outputs)
    if len(types) != len(set(types)):
        raise ExecutionHandoffError(
            "v0.4 final handoff requires unique expected output ArtifactTypes"
        )


def _local_run_output(run_directory: Path, relative_path: str) -> Path:
    relative = PurePosixPath(relative_path)
    candidate = run_directory.joinpath(*relative.parts).resolve()
    run_root = run_directory.resolve()
    if not candidate.is_relative_to(run_root):
        raise ExecutionHandoffError("local VASP output path escaped run_directory")
    return candidate


def _project_output_path(root: Path, output_root: Path, relative_path: str) -> Path:
    relative = PurePosixPath(relative_path)
    candidate = output_root.joinpath(*relative.parts).resolve()
    if not candidate.is_relative_to(root):
        raise ExecutionHandoffError("persisted local output path escaped project_root")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    return candidate


def _persist_verified_output(
    *,
    source: Path,
    destination: Path,
    expected_sha256: str,
    expected_size: int,
) -> None:
    if destination.exists():
        if not destination.is_file():
            raise ExecutionHandoffError("persisted local output destination is not a file")
        if destination.stat().st_size != expected_size:
            raise ExecutionHandoffError("existing persisted local output size differs")
        if _sha256_file(destination) != expected_sha256:
            raise ExecutionHandoffError("existing persisted local output hash differs")
        return

    temporary = destination.with_name(destination.name + ".part")
    temporary.unlink(missing_ok=True)
    try:
        shutil.copyfile(source, temporary)
        if temporary.stat().st_size != expected_size:
            raise ExecutionHandoffError("persisted local output size verification failed")
        if _sha256_file(temporary) != expected_sha256:
            raise ExecutionHandoffError("persisted local output hash verification failed")
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_sha256(value: str, field_name: str) -> None:
    normalized = value.lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{field_name} must be a SHA-256 digest")
