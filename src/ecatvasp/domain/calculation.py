"""Calculation, execution, artifact, and analysis domain entities."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import ClassVar, TypeAlias

from ecatvasp.domain.ids import (
    AnalysisId,
    ArtifactId,
    CalculationId,
    ExecutionAttemptId,
    MethodFingerprintId,
    ProjectId,
    RemoteJobId,
    StructureSnapshotId,
    new_analysis_id,
    new_artifact_id,
    new_calculation_id,
    new_execution_attempt_id,
    new_remote_job_id,
)


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _validate_sha256(value: str | None, field_name: str) -> None:
    if value is None:
        return
    if len(value) != 64 or any(character not in "0123456789abcdefABCDEF" for character in value):
        raise ValueError(f"{field_name} must be a 64-character hexadecimal SHA-256 digest")


def _validate_time_window(started_at: datetime | None, finished_at: datetime | None) -> None:
    if finished_at is not None and started_at is None:
        raise ValueError("finished_at requires started_at")
    if started_at is not None and finished_at is not None and finished_at < started_at:
        raise ValueError("finished_at must not be earlier than started_at")


class CalculationType(StrEnum):
    """Scientific calculation tasks that execute an electronic-structure engine."""

    RELAX = "relax"
    STATIC = "static"
    FREQUENCY = "frequency"
    DOS_STATIC = "dos_static"
    CHARGE_STATIC = "charge_static"
    LOBSTER_PREREQUISITE = "lobster_prerequisite"
    GAS_RELAX = "gas_relax"
    GAS_FREQUENCY = "gas_frequency"


class CalculationEngine(StrEnum):
    """Electronic-structure engine used by a Calculation."""

    VASP = "vasp"


class CalculationScientificStatus(StrEnum):
    """Scientific state, deliberately separate from scheduler state."""

    DRAFT = "draft"
    READY = "ready"
    BLOCKED = "blocked"
    SUBMITTED = "submitted"
    RUNNING = "running"
    PARSING = "parsing"
    CONVERGED = "converged"
    COMPLETED_UNCONVERGED = "completed_unconverged"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STALE = "stale"
    INVALID = "invalid"


class ExecutionAttemptStatus(StrEnum):
    """Lifecycle state for one immutable execution attempt."""

    CREATED = "created"
    STAGING = "staging"
    QUEUED = "queued"
    RUNNING = "running"
    EXITED = "exited"
    RETRIEVING = "retrieving"
    PARSED = "parsed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SchedulerType(StrEnum):
    """Normalized scheduler family attached to a RemoteJob."""

    SLURM = "slurm"
    PBS = "pbs"
    LSF = "lsf"
    OTHER = "other"


class SchedulerState(StrEnum):
    """Normalized scheduler state; it does not imply scientific success."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    NODE_FAIL = "node_fail"
    OUT_OF_MEMORY = "out_of_memory"
    UNKNOWN = "unknown"
    LOST = "lost"


class ArtifactType(StrEnum):
    """Scientific and execution artifacts tracked by metadata."""

    POSCAR = "poscar"
    CONTCAR = "contcar"
    INCAR = "incar"
    KPOINTS = "kpoints"
    POTCAR_SPEC = "potcar_spec"
    OUTCAR = "outcar"
    OSZICAR = "oszicar"
    VASPRUN_XML = "vasprun_xml"
    VASPOUT_H5 = "vaspout_h5"
    CHGCAR = "chgcar"
    AECCAR0 = "aeccar0"
    AECCAR1 = "aeccar1"
    AECCAR2 = "aeccar2"
    WAVECAR = "wavecar"
    DOSCAR = "doscar"
    ACF_DAT = "acf_dat"
    COHPCAR_LOBSTER = "cohpcar_lobster"
    EXECUTION_PLAN = "execution_plan"
    JOB_SCRIPT = "job_script"
    STDOUT = "stdout"
    STDERR = "stderr"
    REMOTE_STAGE_MANIFEST = "remote_stage_manifest"
    RETRIEVAL_MANIFEST = "retrieval_manifest"
    SCHEDULER_RECORD = "scheduler_record"
    PARSED_RESULT = "parsed_result"
    DERIVED_DATASET = "derived_dataset"


class ArtifactAvailability(StrEnum):
    """Where an artifact is currently available."""

    LOCAL = "local"
    REMOTE = "remote"
    BOTH = "both"
    MISSING = "missing"
    ARCHIVED = "archived"


class RetrievalPolicy(StrEnum):
    """Default movement policy for potentially large artifacts."""

    ALWAYS = "always"
    ON_DEMAND = "on_demand"
    REMOTE_ONLY = "remote_only"
    DISCARDABLE = "discardable"


class AnalysisType(StrEnum):
    """Derived scientific analyses; these are not VASP Calculation types."""

    RESULT_PARSE = "result_parse"
    CONVERGENCE = "convergence"
    BADER = "bader"
    CHARGE_DIFFERENCE = "charge_difference"
    DOS = "dos"
    PDOS = "pdos"
    COHP = "cohp"
    BAND_CENTER = "band_center"
    GEOMETRY = "geometry"
    THERMOCHEMISTRY = "thermochemistry"


class AnalysisStatus(StrEnum):
    """Lifecycle of a derived analysis independent from Calculation status."""

    DRAFT = "draft"
    READY = "ready"
    BLOCKED = "blocked"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STALE = "stale"
    INVALID = "invalid"


class ArtifactProducerKind(StrEnum):
    """Kinds of immutable domain objects allowed to produce an Artifact."""

    CALCULATION = "calculation"
    EXECUTION_ATTEMPT = "execution_attempt"
    ANALYSIS = "analysis"


@dataclass(frozen=True, slots=True)
class CalculationProducerRef:
    """Reference to a Calculation that materialized a calculation-level artifact."""

    id: CalculationId
    kind: ClassVar[ArtifactProducerKind] = ArtifactProducerKind.CALCULATION


@dataclass(frozen=True, slots=True)
class ExecutionAttemptProducerRef:
    """Reference to an attempt that produced a concrete runtime artifact."""

    id: ExecutionAttemptId
    kind: ClassVar[ArtifactProducerKind] = ArtifactProducerKind.EXECUTION_ATTEMPT


@dataclass(frozen=True, slots=True)
class AnalysisProducerRef:
    """Reference to an Analysis that produced a derived artifact."""

    id: AnalysisId
    kind: ClassVar[ArtifactProducerKind] = ArtifactProducerKind.ANALYSIS


ArtifactProducerRef: TypeAlias = (
    CalculationProducerRef | ExecutionAttemptProducerRef | AnalysisProducerRef
)


@dataclass(frozen=True, slots=True)
class Calculation:
    """One scientific calculation intent, independent from any execution attempt."""

    project_id: ProjectId
    calculation_type: CalculationType
    input_structure_snapshot_id: StructureSnapshotId
    recipe_id: str
    method_fingerprint_id: MethodFingerprintId
    id: CalculationId = field(default_factory=new_calculation_id)
    engine: CalculationEngine = CalculationEngine.VASP
    status: CalculationScientificStatus = CalculationScientificStatus.DRAFT
    slug: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.recipe_id, "recipe_id")
        if self.slug is not None:
            _require_text(self.slug, "slug")


@dataclass(frozen=True, slots=True)
class ExecutionAttempt:
    """One immutable run attempt for a Calculation."""

    calculation_id: CalculationId
    attempt_number: int
    id: ExecutionAttemptId = field(default_factory=new_execution_attempt_id)
    status: ExecutionAttemptStatus = ExecutionAttemptStatus.CREATED
    previous_attempt_id: ExecutionAttemptId | None = None
    input_manifest_hash: str | None = None
    execution_plan_hash: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.attempt_number < 1:
            raise ValueError("attempt_number must be positive")
        if self.previous_attempt_id == self.id:
            raise ValueError("an ExecutionAttempt cannot reference itself as previous")
        _validate_sha256(self.input_manifest_hash, "input_manifest_hash")
        _validate_sha256(self.execution_plan_hash, "execution_plan_hash")
        _validate_time_window(self.started_at, self.finished_at)


@dataclass(frozen=True, slots=True)
class RemoteJob:
    """Scheduler record for one ExecutionAttempt, separate from scientific status."""

    execution_attempt_id: ExecutionAttemptId
    scheduler: SchedulerType
    scheduler_job_id: str
    remote_directory: str
    id: RemoteJobId = field(default_factory=new_remote_job_id)
    state: SchedulerState = SchedulerState.PENDING
    submitted_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_text(self.scheduler_job_id, "scheduler_job_id")
        _require_text(self.remote_directory, "remote_directory")
        _validate_time_window(self.started_at, self.finished_at)
        if (
            self.started_at is not None
            and self.submitted_at is not None
            and self.started_at < self.submitted_at
        ):
            raise ValueError("started_at must not be earlier than submitted_at")


@dataclass(frozen=True, slots=True)
class Artifact:
    """Metadata for a scientific or execution file without embedding its contents."""

    artifact_type: ArtifactType
    producer: ArtifactProducerRef
    id: ArtifactId = field(default_factory=new_artifact_id)
    availability: ArtifactAvailability = ArtifactAvailability.MISSING
    retrieval_policy: RetrievalPolicy = RetrievalPolicy.ON_DEMAND
    local_path: str | None = None
    remote_path: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None

    def __post_init__(self) -> None:
        if self.size_bytes is not None and self.size_bytes < 0:
            raise ValueError("size_bytes must not be negative")
        _validate_sha256(self.sha256, "sha256")

        if self.availability is ArtifactAvailability.LOCAL and self.local_path is None:
            raise ValueError("LOCAL artifact availability requires local_path")
        if self.availability is ArtifactAvailability.REMOTE and self.remote_path is None:
            raise ValueError("REMOTE artifact availability requires remote_path")
        if (
            self.availability is ArtifactAvailability.BOTH
            and (self.local_path is None or self.remote_path is None)
        ):
            raise ValueError("BOTH artifact availability requires local_path and remote_path")
        if self.retrieval_policy is RetrievalPolicy.REMOTE_ONLY and self.remote_path is None:
            raise ValueError("REMOTE_ONLY retrieval policy requires remote_path")


@dataclass(frozen=True, slots=True)
class Analysis:
    """Derived scientific interpretation consuming existing artifacts."""

    project_id: ProjectId
    analysis_type: AnalysisType
    input_artifact_ids: tuple[ArtifactId, ...]
    id: AnalysisId = field(default_factory=new_analysis_id)
    status: AnalysisStatus = AnalysisStatus.DRAFT
    tool: str | None = None
    tool_version: str | None = None
    parameters_hash: str | None = None

    def __post_init__(self) -> None:
        if len(self.input_artifact_ids) != len(set(self.input_artifact_ids)):
            raise ValueError("input_artifact_ids must be unique")
        if self.tool is not None:
            _require_text(self.tool, "tool")
        if self.tool_version is not None:
            _require_text(self.tool_version, "tool_version")
        _validate_sha256(self.parameters_hash, "parameters_hash")
