"""Fail-closed intake of managed VASP result Artifacts for v0.5.

This layer verifies execution/result provenance and local file integrity only. It
performs no scientific parsing, convergence classification, Calculation status
mutation, or relaxed-structure promotion.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ecatvasp.domain import (
    Artifact,
    ArtifactAvailability,
    Calculation,
    CalculationEngine,
    CalculationType,
    ExecutionAttempt,
    ExecutionAttemptProducerRef,
    ExecutionAttemptStatus,
    RetrievalPolicy,
    canonical_sha256,
)
from ecatvasp.domain.ids import ArtifactId, CalculationId, ExecutionAttemptId
from ecatvasp.vasp.execution_plan import ExecutionPlan, ExpectedOutput
from ecatvasp.vasp.results import (
    VaspResultSource,
    VaspResultSourceRole,
    result_source_artifact_type,
)

_PARSEABLE_ATTEMPT_STATES = frozenset(
    {
        ExecutionAttemptStatus.EXITED,
        ExecutionAttemptStatus.RETRIEVING,
        ExecutionAttemptStatus.PARSED,
        ExecutionAttemptStatus.FAILED,
        ExecutionAttemptStatus.CANCELLED,
    }
)
_RELAX_CALCULATION_TYPES = frozenset(
    {CalculationType.RELAX, CalculationType.GAS_RELAX}
)


class VaspResultIntakeError(ValueError):
    """Raised when managed result Artifacts cannot be accepted without guessing."""


def _validate_relative_path(value: str, field_name: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or ".." in path.parts:
        raise VaspResultIntakeError(
            f"{field_name} must be a normalized relative POSIX path"
        )
    if value in {"", "."}:
        raise VaspResultIntakeError(f"{field_name} must not be empty")
    return value


@dataclass(frozen=True, slots=True)
class VaspResultInputFile:
    """One verified local file ready to be consumed by a scientific parser adapter."""

    source: VaspResultSource
    expected_output_path: str
    local_relative_path: str
    size_bytes: int
    retrieval_policy: RetrievalPolicy

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "expected_output_path",
            _validate_relative_path(self.expected_output_path, "expected_output_path"),
        )
        object.__setattr__(
            self,
            "local_relative_path",
            _validate_relative_path(self.local_relative_path, "local_relative_path"),
        )
        if self.size_bytes < 0:
            raise VaspResultIntakeError("size_bytes must not be negative")


@dataclass(frozen=True, slots=True)
class VaspResultArtifactIntake:
    """Portable identity for one exact set of parse-ready managed VASP outputs."""

    calculation_id: CalculationId
    calculation_type: CalculationType
    recipe_id: str
    attempt_id: ExecutionAttemptId
    attempt_number: int
    plan_hash: str
    input_manifest_hash: str
    files: tuple[VaspResultInputFile, ...]
    intake_hash: str = field(init=False)

    def __post_init__(self) -> None:
        normalized_files = tuple(
            sorted(self.files, key=lambda item: item.source.role.value)
        )
        roles = tuple(item.source.role for item in normalized_files)
        if len(roles) != len(set(roles)):
            raise VaspResultIntakeError("result intake source roles must be unique")
        artifact_ids = tuple(item.source.artifact_id for item in normalized_files)
        if len(artifact_ids) != len(set(artifact_ids)):
            raise VaspResultIntakeError("result intake Artifact ids must be unique")
        if VaspResultSourceRole.OUTCAR not in roles:
            raise VaspResultIntakeError("result intake requires a verified OUTCAR")
        if self.attempt_number < 1:
            raise VaspResultIntakeError("attempt_number must be positive")
        object.__setattr__(self, "files", normalized_files)
        object.__setattr__(
            self,
            "intake_hash",
            canonical_sha256(
                {
                    "calculation_id": self.calculation_id,
                    "calculation_type": self.calculation_type,
                    "recipe_id": self.recipe_id,
                    "attempt_id": self.attempt_id,
                    "attempt_number": self.attempt_number,
                    "plan_hash": self.plan_hash,
                    "input_manifest_hash": self.input_manifest_hash,
                    "sources": tuple(
                        {
                            "role": item.source.role,
                            "artifact_id": item.source.artifact_id,
                            "artifact_type": item.source.artifact_type,
                            "sha256": item.source.sha256,
                            "size_bytes": item.size_bytes,
                            "expected_output_path": item.expected_output_path,
                            "retrieval_policy": item.retrieval_policy,
                        }
                        for item in normalized_files
                    ),
                }
            ),
        )

    @property
    def sources(self) -> tuple[VaspResultSource, ...]:
        """Return durable content-addressed source identities in deterministic order."""

        return tuple(item.source for item in self.files)

    @property
    def input_artifact_ids(self) -> tuple[ArtifactId, ...]:
        """Return exact Artifact inputs suitable for a RESULT_PARSE Analysis."""

        return tuple(item.source.artifact_id for item in self.files)


def build_vasp_result_artifact_intake(
    *,
    project_root: Path | str,
    calculation: Calculation,
    plan: ExecutionPlan,
    attempt: ExecutionAttempt,
    artifacts: tuple[Artifact, ...],
) -> VaspResultArtifactIntake:
    """Validate exact managed outputs and return the parse-ready source bundle.

    Optional source Artifacts that exist only remotely, are archived, or are explicitly
    missing are not silently downloaded here; they are simply absent from this intake.
    Required sources must already be locally available and content-addressed.
    """

    root = Path(project_root).resolve()
    if not root.is_dir():
        raise VaspResultIntakeError("project_root must be an existing directory")
    _validate_execution_identity(
        calculation=calculation,
        plan=plan,
        attempt=attempt,
    )
    contracts = _result_source_contracts(calculation=calculation, plan=plan)

    artifact_ids = tuple(item.id for item in artifacts)
    if len(artifact_ids) != len(set(artifact_ids)):
        raise VaspResultIntakeError("supplied Artifact ids must be unique")

    source_types = {item.artifact_type for item in contracts.values()}
    candidates_by_type: dict[object, list[Artifact]] = {}
    for artifact in artifacts:
        if artifact.artifact_type in source_types:
            candidates_by_type.setdefault(artifact.artifact_type, []).append(artifact)

    files: list[VaspResultInputFile] = []
    for role, expected in sorted(contracts.items(), key=lambda item: item[0].value):
        required = _source_is_required(
            role=role,
            expected=expected,
            calculation_type=calculation.calculation_type,
        )
        candidates = candidates_by_type.get(expected.artifact_type, [])
        if not candidates:
            if required:
                raise VaspResultIntakeError(
                    f"required result source is missing: {role.value}"
                )
            continue
        if len(candidates) != 1:
            raise VaspResultIntakeError(
                f"result source is ambiguous for role {role.value!r}"
            )
        artifact = candidates[0]
        _validate_artifact_contract(
            artifact=artifact,
            expected=expected,
            attempt=attempt,
        )

        if artifact.availability not in {
            ArtifactAvailability.LOCAL,
            ArtifactAvailability.BOTH,
        }:
            if required:
                raise VaspResultIntakeError(
                    f"required result source is not locally available: {role.value}"
                )
            continue

        local_relative_path, size_bytes, sha256 = _verify_local_artifact(
            root=root,
            artifact=artifact,
            role=role,
        )
        files.append(
            VaspResultInputFile(
                source=VaspResultSource(
                    role=role,
                    artifact_id=artifact.id,
                    artifact_type=artifact.artifact_type,
                    sha256=sha256,
                ),
                expected_output_path=expected.relative_path,
                local_relative_path=local_relative_path,
                size_bytes=size_bytes,
                retrieval_policy=artifact.retrieval_policy,
            )
        )

    return VaspResultArtifactIntake(
        calculation_id=calculation.id,
        calculation_type=calculation.calculation_type,
        recipe_id=calculation.recipe_id,
        attempt_id=attempt.id,
        attempt_number=attempt.attempt_number,
        plan_hash=plan.plan_hash,
        input_manifest_hash=plan.input_manifest_sha256,
        files=tuple(files),
    )


def _validate_execution_identity(
    *,
    calculation: Calculation,
    plan: ExecutionPlan,
    attempt: ExecutionAttempt,
) -> None:
    if calculation.engine is not CalculationEngine.VASP:
        raise VaspResultIntakeError("result intake supports only VASP Calculations")
    if plan.calculation_id != calculation.id or attempt.calculation_id != calculation.id:
        raise VaspResultIntakeError(
            "Calculation, ExecutionPlan, and ExecutionAttempt identities must match"
        )
    if plan.recipe_id != calculation.recipe_id:
        raise VaspResultIntakeError("ExecutionPlan recipe does not match Calculation")
    if attempt.execution_plan_hash != plan.plan_hash:
        raise VaspResultIntakeError("ExecutionAttempt does not pin this ExecutionPlan")
    if attempt.input_manifest_hash != plan.input_manifest_sha256:
        raise VaspResultIntakeError(
            "ExecutionAttempt input manifest does not match ExecutionPlan"
        )
    if attempt.status not in _PARSEABLE_ATTEMPT_STATES:
        raise VaspResultIntakeError(
            f"ExecutionAttempt status {attempt.status.value!r} is not parse-ready"
        )


def _result_source_contracts(
    *,
    calculation: Calculation,
    plan: ExecutionPlan,
) -> dict[VaspResultSourceRole, ExpectedOutput]:
    expected_by_role = {item.role: item for item in plan.expected_outputs}
    contracts: dict[VaspResultSourceRole, ExpectedOutput] = {}
    for role in VaspResultSourceRole:
        expected = expected_by_role.get(role.value)
        if expected is None:
            continue
        required_type = result_source_artifact_type(role)
        if expected.artifact_type is not required_type:
            raise VaspResultIntakeError(
                f"ExecutionPlan role {role.value!r} has incompatible ArtifactType"
            )
        contracts[role] = expected

    outcar = contracts.get(VaspResultSourceRole.OUTCAR)
    if outcar is None or not outcar.required:
        raise VaspResultIntakeError(
            "ExecutionPlan must declare required OUTCAR for scientific result intake"
        )

    if calculation.calculation_type in _RELAX_CALCULATION_TYPES:
        contcar = contracts.get(VaspResultSourceRole.CONTCAR)
        if contcar is None or not contcar.required:
            raise VaspResultIntakeError(
                "relaxation result intake requires required CONTCAR in ExecutionPlan"
            )
    return contracts


def _source_is_required(
    *,
    role: VaspResultSourceRole,
    expected: ExpectedOutput,
    calculation_type: CalculationType,
) -> bool:
    if role is VaspResultSourceRole.OUTCAR:
        return True
    if (
        role is VaspResultSourceRole.CONTCAR
        and calculation_type in _RELAX_CALCULATION_TYPES
    ):
        return True
    return expected.required


def _validate_artifact_contract(
    *,
    artifact: Artifact,
    expected: ExpectedOutput,
    attempt: ExecutionAttempt,
) -> None:
    producer = ExecutionAttemptProducerRef(attempt.id)
    if artifact.producer != producer:
        raise VaspResultIntakeError(
            "result source Artifact must be produced by the exact ExecutionAttempt"
        )
    if artifact.artifact_type is not expected.artifact_type:
        raise VaspResultIntakeError("result source ArtifactType does not match ExecutionPlan")
    if artifact.retrieval_policy is not expected.retrieval_policy:
        raise VaspResultIntakeError(
            "result source retrieval policy does not match ExecutionPlan"
        )


def _verify_local_artifact(
    *,
    root: Path,
    artifact: Artifact,
    role: VaspResultSourceRole,
) -> tuple[str, int, str]:
    if artifact.local_path is None:
        raise VaspResultIntakeError(
            f"locally available source {role.value!r} requires local_path"
        )
    if artifact.sha256 is None or artifact.size_bytes is None:
        raise VaspResultIntakeError(
            f"locally available source {role.value!r} requires SHA-256 and size"
        )
    relative = _validate_relative_path(artifact.local_path, "Artifact.local_path")
    path = (root / Path(*PurePosixPath(relative).parts)).resolve()
    if not path.is_relative_to(root):
        raise VaspResultIntakeError("result source resolves outside project_root")
    if not path.is_file():
        raise VaspResultIntakeError(
            f"result source file is missing for role {role.value!r}"
        )
    actual_size = path.stat().st_size
    if actual_size != artifact.size_bytes:
        raise VaspResultIntakeError(
            f"result source size changed for role {role.value!r}"
        )
    actual_sha = _sha256_file(path)
    if actual_sha != artifact.sha256.lower():
        raise VaspResultIntakeError(
            f"result source SHA-256 changed for role {role.value!r}"
        )
    return relative, actual_size, actual_sha


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
