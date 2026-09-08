"""Durable remote-stage reconstruction for v1.1 Job Center resume."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast
from uuid import UUID

from ecatvasp.api.application import ApplicationServiceError
from ecatvasp.api.execution_plan_resolver import resolve_execution_plan
from ecatvasp.domain import (
    Artifact,
    ArtifactAvailability,
    ArtifactId,
    ArtifactType,
    ExecutionAttempt,
    ExecutionAttemptId,
    ExecutionAttemptProducerRef,
    ExecutionAttemptStatus,
    SchedulerType,
)
from ecatvasp.execution.adapters import TargetRelativePath
from ecatvasp.execution.remote import (
    RemoteStageFileRecord,
    RemoteStageManifest,
    RemoteStagePackage,
)
from ecatvasp.execution.targets import (
    ExecutionEnvironmentSnapshot,
    ExecutionTargetProfile,
    TransportKind,
)
from ecatvasp.storage import ProjectBundle

_STAGE_ARTIFACT_TYPES = frozenset(
    {
        ArtifactType.EXECUTION_PLAN,
        ArtifactType.INCAR,
        ArtifactType.REMOTE_STAGE_MANIFEST,
    }
)


def resolve_remote_stage_package(
    *,
    project_root: Path | str,
    bundle: ProjectBundle,
    attempt_id: ExecutionAttemptId,
    target: ExecutionTargetProfile,
) -> RemoteStagePackage:
    """Reconstruct one persisted STAGING handoff without repeating SSH staging."""

    attempt = _require_attempt(bundle, attempt_id)
    if attempt.status is not ExecutionAttemptStatus.STAGING:
        raise ApplicationServiceError(
            "durable remote-stage resume requires a STAGING ExecutionAttempt"
        )
    plan = resolve_execution_plan(project_root, bundle, attempt.id)
    artifacts = _stage_artifacts(bundle, attempt)
    manifest_artifact = next(
        item
        for item in artifacts
        if item.artifact_type is ArtifactType.REMOTE_STAGE_MANIFEST
    )
    root = Path(project_root).resolve()
    assert manifest_artifact.local_path is not None
    path = (root / manifest_artifact.local_path).resolve()
    if root not in path.parents or not path.is_file():
        raise ApplicationServiceError(
            "remote-stage manifest path is missing or escaped project root"
        )
    body = path.read_bytes()
    if manifest_artifact.sha256 != hashlib.sha256(body).hexdigest():
        raise ApplicationServiceError("remote-stage manifest SHA-256 drift detected")
    try:
        raw = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ApplicationServiceError(
            "remote-stage manifest is not valid UTF-8 JSON"
        ) from error
    manifest = _decode_manifest(raw)
    if manifest.text.encode("utf-8") != body:
        raise ApplicationServiceError(
            "remote-stage manifest content is not canonical for its payload"
        )
    if manifest.attempt_id != attempt.id:
        raise ApplicationServiceError(
            "remote-stage manifest belongs to another ExecutionAttempt"
        )
    if manifest.plan_hash != plan.plan_hash:
        raise ApplicationServiceError(
            "remote-stage manifest does not match durable ExecutionPlan"
        )
    if manifest.execution_settings_hash != plan.execution_settings_hash:
        raise ApplicationServiceError(
            "remote-stage manifest execution settings drift from ExecutionPlan"
        )
    if manifest.environment.target_hash != target.target_hash:
        raise ApplicationServiceError(
            "current execution target does not match staged target identity"
        )
    return RemoteStagePackage(
        project_root=root,
        plan=plan,
        target=target,
        attempt=attempt,
        remote_directory=TargetRelativePath(manifest.remote_directory),
        manifest=manifest,
        artifacts=artifacts,
    )


def _stage_artifacts(
    bundle: ProjectBundle,
    attempt: ExecutionAttempt,
) -> tuple[Artifact, ...]:
    matches = tuple(
        item
        for item in bundle.artifacts
        if isinstance(item.producer, ExecutionAttemptProducerRef)
        and item.producer.id == attempt.id
        and item.artifact_type in _STAGE_ARTIFACT_TYPES
        and item.local_path is not None
        and item.availability in {ArtifactAvailability.LOCAL, ArtifactAvailability.BOTH}
    )
    counts = {
        artifact_type: sum(item.artifact_type is artifact_type for item in matches)
        for artifact_type in _STAGE_ARTIFACT_TYPES
    }
    if any(value != 1 for value in counts.values()) or len(matches) != 3:
        raise ApplicationServiceError(
            "STAGING ExecutionAttempt requires one plan, runtime INCAR, and stage manifest"
        )
    return tuple(sorted(matches, key=lambda item: item.artifact_type.value))


def _decode_manifest(raw: object) -> RemoteStageManifest:
    if not isinstance(raw, dict):
        raise ApplicationServiceError("remote-stage manifest payload must be an object")
    payload = cast(dict[str, object], raw)
    try:
        if payload.get("schema_version") != 1:
            raise ValueError("unsupported remote-stage manifest schema")
        environment_raw = _mapping(payload, "environment")
        scheduler_raw = environment_raw.get("scheduler")
        if scheduler_raw is not None and not isinstance(scheduler_raw, str):
            raise ValueError("scheduler must be null or a string")
        launcher_raw = environment_raw.get("launcher")
        if launcher_raw is not None and not isinstance(launcher_raw, str):
            raise ValueError("launcher must be null or a string")
        module_loads_raw = environment_raw.get("module_loads")
        if not isinstance(module_loads_raw, list) or any(
            not isinstance(item, str) for item in module_loads_raw
        ):
            raise ValueError("module_loads must be a string list")
        environment = ExecutionEnvironmentSnapshot(
            target_id=_string(environment_raw, "target_id"),
            target_hash=_string(environment_raw, "target_hash"),
            transport=TransportKind(_string(environment_raw, "transport")),
            scheduler=(
                SchedulerType(scheduler_raw) if scheduler_raw is not None else None
            ),
            potcar_resolver_id=_string(environment_raw, "potcar_resolver_id"),
            vasp_executable=_string(environment_raw, "vasp_executable"),
            launcher=launcher_raw,
            module_loads=tuple(cast(list[str], module_loads_raw)),
        )
        files = tuple(_decode_file(item) for item in _mapping_list(payload, "files"))
        return RemoteStageManifest(
            attempt_id=ExecutionAttemptId(UUID(_string(payload, "attempt_id"))),
            plan_hash=_string(payload, "plan_hash"),
            execution_settings_hash=_string(payload, "execution_settings_hash"),
            environment=environment,
            remote_directory=_string(payload, "remote_directory"),
            files=files,
        )
    except (TypeError, ValueError) as error:
        raise ApplicationServiceError(
            "remote-stage manifest payload is invalid"
        ) from error


def _decode_file(raw: dict[str, object]) -> RemoteStageFileRecord:
    source = raw.get("source_artifact_id")
    execution = raw.get("execution_artifact_id")
    if source is not None and not isinstance(source, str):
        raise ValueError("source_artifact_id must be null or a string")
    if execution is not None and not isinstance(execution, str):
        raise ValueError("execution_artifact_id must be null or a string")
    licensed = raw.get("licensed")
    if not isinstance(licensed, bool):
        raise ValueError("licensed must be a boolean")
    size = raw.get("size_bytes")
    if isinstance(size, bool) or not isinstance(size, int):
        raise ValueError("size_bytes must be an integer")
    return RemoteStageFileRecord(
        role=_string(raw, "role"),
        relative_path=_string(raw, "relative_path"),
        sha256=_string(raw, "sha256"),
        size_bytes=size,
        source_artifact_id=ArtifactId(UUID(source)) if source is not None else None,
        execution_artifact_id=(
            ArtifactId(UUID(execution)) if execution is not None else None
        ),
        licensed=licensed,
    )


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
        raise ValueError(f"{key} must be a list")
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


__all__ = ["resolve_remote_stage_package"]
