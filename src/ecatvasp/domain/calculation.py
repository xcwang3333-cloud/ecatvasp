"""Calculation, execution, and artifact domain entities."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import NewType
from uuid import UUID

from ecatvasp.domain.ids import (
    CalculationId,
    ExecutionAttemptId,
    MethodFingerprintId,
    ProjectId,
    RemoteJobId,
    StructureSnapshotId,
    new_uuid7,
)

ArtifactId = NewType("ArtifactId", UUID)
AnalysisId = NewType("AnalysisId", UUID)


def new_artifact_id() -> ArtifactId:
    return ArtifactId(new_uuid7())


def new_analysis_id() -> AnalysisId:
    return AnalysisId(new_uuid7())


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _normalized_sha256(value: str, field_name: str) -> str:
    normalized = value.lower()
    valid_hex = all(character in "0123456789abcdef" for character in normalized)
    if len(normalized) != 64 or not valid_hex:
        raise ValueError(f"{field_name} must be a 64-character hexadecimal SHA-256 digest")
    return normalized


class CalculationType(StrEnum):
    """Scientific Calculation identity classes supported by ECatVASP."""

    RELAX = "relax"
    STATIC = "static"
    FREQUENCY = "frequency"
    DOS_STATIC = "dos_static"
    CHARGE_STATIC = "charge_static"
    LOBSTER_PREREQUISITE = "lobster_prerequisite"


class CalculationScientificStatus(StrEnum):
    """Scientific lifecycle; scheduler completion is intentionally excluded."""

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
    ICOHPLIST_LOBSTER = "icohplist_lobster"
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
    """Where exact Artifact content is currently available."""

    LOCAL = "local"
    REMOTE = "remote"
    BOTH = "both"
    MISSING = "missing"


class RetrievalPolicy(StrEnum):
    """Default retrieval expectation for output artifacts."""

    ALWAYS = "always"
    ON_DEMAND = "on_demand"
    NEVER = "never"


class AnalysisType(StrEnum):
    """Derived scientific analysis classes."""

    BADER = "bader"
    CHARGE_DIFFERENCE = "charge_difference"
    DOS = "dos"
    PDOS = "pdos"
    COHP = "cohp"
    BAND_CENTER = "band_center"


class AnalysisStatus(StrEnum):
    """Lifecycle for derived analysis independent of Calculation status."""

    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STALE = "stale"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class CalculationProducerRef:
    """Artifact produced deterministically by a Calculation preparation stage."""

    id: CalculationId


@dataclass(frozen=True, slots=True)
class ExecutionAttemptProducerRef:
    """Artifact produced by a specific immutable execution attempt."""

    id: ExecutionAttemptId


@dataclass(frozen=True, slots=True)
class AnalysisProducerRef:
    """Artifact produced by a derived scientific Analysis."""

    id: AnalysisId


ArtifactProducerRef = CalculationProducerRef | ExecutionAttemptProducerRef | AnalysisProducerRef


@dataclass(frozen=True, slots=True)
class Calculation:
    """Immutable scientific Calculation identity; execution attempts are separate."""

    project_id: ProjectId
    calculation_type: CalculationType
    input_structure_snapshot_id: StructureSnapshotId
    recipe_id: str
    method_fingerprint_id: MethodFingerprintId
    id: CalculationId = field(default_factory=lambda: CalculationId(new_uuid7()))
    engine: str = "vasp"
    status: CalculationScientificStatus = CalculationScientificStatus.DRAFT
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        _require_text(self.recipe_id, "recipe_id")
        _require_text(self.engine, "engine")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ExecutionAttempt:
    """One immutable execution try for an existing scientific Calculation."""

    calculation_id: CalculationId
    attempt_number: int
    id: ExecutionAttemptId = field(default_factory=lambda: ExecutionAttemptId(new_uuid7()))
    status: ExecutionAttemptStatus = ExecutionAttemptStatus.CREATED
    execution_target: str | None = None
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if self.attempt_number < 1:
            raise ValueError("attempt_number must be positive")
        if self.execution_target is not None:
            _require_text(self.execution_target, "execution_target")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class RemoteJob:
    """Scheduler record attached to one ExecutionAttempt; never owns scientific identity."""

    execution_attempt_id: ExecutionAttemptId
    scheduler: SchedulerType
    scheduler_job_id: str
    id: RemoteJobId = field(default_factory=lambda: RemoteJobId(new_uuid7()))
    state: SchedulerState = SchedulerState.PENDING
    scheduler_reason: str | None = None
    exit_code: int | None = None
    submitted_at: datetime = field(default_factory=_utc_now)
    last_observed_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_text(self.scheduler_job_id, "scheduler_job_id")
        if self.scheduler_reason is not None:
            _require_text(self.scheduler_reason, "scheduler_reason")
        if self.submitted_at.tzinfo is None:
            raise ValueError("submitted_at must be timezone-aware")
        if self.last_observed_at is not None and self.last_observed_at.tzinfo is None:
            raise ValueError("last_observed_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class Artifact:
    """Metadata for an exact local/remote scientific or execution artifact."""

    artifact_type: ArtifactType
    producer: ArtifactProducerRef
    id: ArtifactId = field(default_factory=new_artifact_id)
    availability: ArtifactAvailability = ArtifactAvailability.MISSING
    retrieval_policy: RetrievalPolicy = RetrievalPolicy.ON_DEMAND
    local_path: str | None = None
    remote_path: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if self.local_path is not None:
            _validate_relative_path(self.local_path, "local_path")
        if self.remote_path is not None:
            _require_text(self.remote_path, "remote_path")
        if self.size_bytes is not None and self.size_bytes < 0:
            raise ValueError("size_bytes must not be negative")
        if self.sha256 is not None:
            object.__setattr__(self, "sha256", _normalized_sha256(self.sha256, "sha256"))
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class Analysis:
    """Derived result over immutable input Artifacts."""

    project_id: ProjectId
    analysis_type: AnalysisType
    input_artifact_ids: tuple[ArtifactId, ...]
    id: AnalysisId = field(default_factory=new_analysis_id)
    status: AnalysisStatus = AnalysisStatus.PLANNED
    tool: str | None = None
    tool_version: str | None = None
    parameters_hash: str | None = None
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if len(self.input_artifact_ids) != len(set(self.input_artifact_ids)):
            raise ValueError("Analysis input_artifact_ids must be unique")
        if self.tool is not None:
            _require_text(self.tool, "tool")
        if self.tool_version is not None:
            _require_text(self.tool_version, "tool_version")
        if self.parameters_hash is not None:
            object.__setattr__(
                self,
                "parameters_hash",
                _normalized_sha256(self.parameters_hash, "parameters_hash"),
            )
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")


def _validate_relative_path(value: str, field_name: str) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value != path.as_posix() or value in {"", "."}:
        raise ValueError(f"{field_name} must be a normalized relative path")
