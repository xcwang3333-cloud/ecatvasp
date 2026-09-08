"""Durable execution-plan artifact loading for v1.1 application services."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast
from uuid import UUID

from ecatvasp.api.application import ApplicationServiceError
from ecatvasp.domain import (
    ArtifactAvailability,
    ArtifactId,
    ArtifactType,
    CalculationId,
    ExecutionAttempt,
    ExecutionAttemptId,
    ExecutionAttemptProducerRef,
    ExecutionSettings,
    ParameterEntry,
    RetrievalPolicy,
)
from ecatvasp.storage import ProjectBundle
from ecatvasp.vasp.contracts import LatticeAxis, VaspSystemContext, VaspSystemKind
from ecatvasp.vasp.execution_plan import (
    ExecutionPlan,
    ExpectedOutput,
    PotcarResolutionEntry,
    PotcarResolutionRequest,
    StagingInput,
    StagingInputKind,
    VaspRuntimeCapability,
    VaspRuntimeConstraints,
)


def resolve_execution_plan(
    project_root: Path | str,
    bundle: ProjectBundle,
    attempt_id: ExecutionAttemptId,
) -> ExecutionPlan:
    """Load and verify the exact managed execution-plan.json for one attempt."""

    attempt = _require_attempt(bundle, attempt_id)
    matches = tuple(
        item
        for item in bundle.artifacts
        if item.artifact_type is ArtifactType.EXECUTION_PLAN
        and item.producer == ExecutionAttemptProducerRef(attempt.id)
        and item.local_path is not None
        and item.availability in {ArtifactAvailability.LOCAL, ArtifactAvailability.BOTH}
    )
    if len(matches) != 1:
        raise ApplicationServiceError(
            "ExecutionAttempt requires exactly one durable local ExecutionPlan artifact"
        )
    artifact = matches[0]
    root = Path(project_root).resolve()
    path = (root / cast(str, artifact.local_path)).resolve()
    if root not in path.parents or not path.is_file():
        raise ApplicationServiceError(
            "ExecutionPlan artifact path is missing or escaped project root"
        )
    body = path.read_bytes()
    if artifact.sha256 != hashlib.sha256(body).hexdigest():
        raise ApplicationServiceError("ExecutionPlan artifact SHA-256 drift detected")
    try:
        raw = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ApplicationServiceError(
            "ExecutionPlan artifact is not valid UTF-8 JSON"
        ) from error
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ApplicationServiceError("ExecutionPlan artifact has unsupported schema")
    plan_raw = raw.get("plan")
    if not isinstance(plan_raw, dict):
        raise ApplicationServiceError("ExecutionPlan artifact is missing plan payload")
    plan = _decode_execution_plan(cast(dict[str, object], plan_raw))
    if raw.get("plan_hash") != plan.plan_hash:
        raise ApplicationServiceError(
            "ExecutionPlan payload hash does not match artifact envelope"
        )
    if attempt.execution_plan_hash != plan.plan_hash:
        raise ApplicationServiceError(
            "ExecutionPlan artifact does not match durable ExecutionAttempt"
        )
    if attempt.input_manifest_hash != plan.input_manifest_sha256:
        raise ApplicationServiceError(
            "ExecutionPlan input manifest does not match durable attempt"
        )
    return plan


def _decode_execution_plan(raw: dict[str, object]) -> ExecutionPlan:
    try:
        context_raw = _mapping(raw, "system_context")
        vacuum = context_raw.get("vacuum_axis")
        if vacuum is not None and not isinstance(vacuum, str):
            raise ValueError("vacuum_axis must be null or a string")
        context = VaspSystemContext(
            VaspSystemKind(_string(context_raw, "kind")),
            vacuum_axis=LatticeAxis(vacuum) if vacuum is not None else None,
        )
        staging = tuple(
            StagingInput(
                role=_string(item, "role"),
                kind=StagingInputKind(_string(item, "kind")),
                artifact_id=ArtifactId(UUID(_string(item, "artifact_id"))),
                artifact_type=ArtifactType(_string(item, "artifact_type")),
                source_relative_path=_string(item, "source_relative_path"),
                target_relative_path=_string(item, "target_relative_path"),
                sha256=_string(item, "sha256"),
                size_bytes=_integer(item, "size_bytes"),
            )
            for item in _mapping_list(raw, "staging_inputs")
        )
        potcar_raw = _mapping(raw, "potcar_resolution")
        potcars = PotcarResolutionRequest(
            family=_string(potcar_raw, "family"),
            core_method_hash=_string(potcar_raw, "core_method_hash"),
            metadata_hash=_string(potcar_raw, "metadata_hash"),
            entries=tuple(
                PotcarResolutionEntry(
                    element=_string(item, "element"),
                    symbol=_string(item, "symbol"),
                    sha256=_string(item, "sha256"),
                )
                for item in _mapping_list(potcar_raw, "entries")
            ),
            target_relative_path=_string(potcar_raw, "target_relative_path"),
        )
        outputs = tuple(
            ExpectedOutput(
                role=_string(item, "role"),
                artifact_type=ArtifactType(_string(item, "artifact_type")),
                relative_path=_string(item, "relative_path"),
                retrieval_policy=RetrievalPolicy(_string(item, "retrieval_policy")),
                required=_boolean(item, "required"),
            )
            for item in _mapping_list(raw, "expected_outputs")
        )
        constraints_raw = _mapping(raw, "runtime_constraints")
        capabilities_raw = constraints_raw.get("required_capabilities")
        if not isinstance(capabilities_raw, list):
            raise ValueError("required_capabilities must be a list")
        capabilities: list[VaspRuntimeCapability] = []
        for item in capabilities_raw:
            if not isinstance(item, str):
                raise ValueError("required_capabilities must contain strings")
            capabilities.append(VaspRuntimeCapability(item))
        required_version = constraints_raw.get("required_version")
        if required_version is not None and not isinstance(required_version, str):
            raise ValueError("required_version must be null or a string")
        constraints = VaspRuntimeConstraints(
            required_version=required_version,
            required_capabilities=tuple(capabilities),
        )
        settings_raw = _mapping(raw, "execution_settings")
        extras = tuple(
            ParameterEntry(
                _string(item, "name"),
                _parameter_value(item.get("value")),
            )
            for item in _mapping_list(settings_raw, "extra_parameters")
        )
        settings = ExecutionSettings(
            ncore=_optional_integer(settings_raw, "ncore"),
            kpar=_optional_integer(settings_raw, "kpar"),
            nodes=_optional_integer(settings_raw, "nodes"),
            cores=_optional_integer(settings_raw, "cores"),
            memory_mb=_optional_integer(settings_raw, "memory_mb"),
            walltime_seconds=_optional_integer(settings_raw, "walltime_seconds"),
            partition=_optional_string(settings_raw, "partition"),
            mpi_ranks=_optional_integer(settings_raw, "mpi_ranks"),
            omp_threads=_optional_integer(settings_raw, "omp_threads"),
            executable=_string(settings_raw, "executable"),
            extra_parameters=extras,
        )
        return ExecutionPlan(
            calculation_id=CalculationId(UUID(_string(raw, "calculation_id"))),
            recipe_id=_string(raw, "recipe_id"),
            system_context=context,
            input_manifest_artifact_id=ArtifactId(
                UUID(_string(raw, "input_manifest_artifact_id"))
            ),
            input_manifest_sha256=_string(raw, "input_manifest_sha256"),
            preparation_hash=_string(raw, "preparation_hash"),
            staging_inputs=staging,
            potcar_resolution=potcars,
            expected_outputs=outputs,
            runtime_constraints=constraints,
            execution_settings=settings,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ApplicationServiceError(
            "ExecutionPlan artifact payload is invalid"
        ) from error


def _require_attempt(
    bundle: ProjectBundle,
    attempt_id: ExecutionAttemptId,
) -> ExecutionAttempt:
    matches = tuple(item for item in bundle.execution_attempts if item.id == attempt_id)
    if len(matches) != 1:
        raise ApplicationServiceError("ExecutionAttempt is absent or duplicated")
    return matches[0]


def _mapping(raw: dict[str, object], key: str) -> dict[str, object]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return cast(dict[str, object], value)


def _mapping_list(raw: dict[str, object], key: str) -> tuple[dict[str, object], ...]:
    value = raw.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list of objects")
    result: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"{key} must contain only objects")
        result.append(cast(dict[str, object], item))
    return tuple(result)


def _string(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_string(raw: dict[str, object], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be null or a non-empty string")
    return value


def _integer(raw: dict[str, object], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _optional_integer(raw: dict[str, object], key: str) -> int | None:
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be null or an integer")
    return value


def _boolean(raw: dict[str, object], key: str) -> bool:
    value = raw.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _parameter_value(value: object) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("execution parameter values must be scalar")


__all__ = ["resolve_execution_plan"]
