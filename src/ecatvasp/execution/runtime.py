"""Runtime materialization for local v0.4 execution attempts."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath

from ecatvasp.domain import (
    Artifact,
    ArtifactAvailability,
    ArtifactType,
    Calculation,
    ExecutionAttempt,
    ExecutionAttemptProducerRef,
    ExecutionAttemptStatus,
    RetrievalPolicy,
)
from ecatvasp.domain.ids import ArtifactId, ExecutionAttemptId
from ecatvasp.domain.method import canonical_json
from ecatvasp.execution.provenance import validate_execution_attempt_plan
from ecatvasp.execution.targets import (
    ExecutionEnvironmentSnapshot,
    ExecutionTargetProfile,
    TransportKind,
)
from ecatvasp.vasp.execution_plan import ExecutionPlan, StagingInput
from ecatvasp.vasp.potcar import ResolvedPotcarSet

_RESERVED_RUNTIME_PATHS = frozenset(
    {"execution-plan.json", "runtime-input-manifest.json"}
)
_EXECUTION_INCAR_KEYS = frozenset({"NCORE", "KPAR", "NPAR"})


class RuntimeMaterializationError(ValueError):
    """Raised when one immutable ExecutionPlan cannot become a safe local runtime."""


@dataclass(frozen=True, slots=True)
class LocalPotcarResolution:
    """Local licensed POTCAR resolution selected by one logical resolver."""

    resolver_id: str
    resolved: ResolvedPotcarSet

    def __post_init__(self) -> None:
        if not self.resolver_id.strip():
            raise ValueError("resolver_id must not be blank")


@dataclass(frozen=True, slots=True)
class RuntimeFileRecord:
    """Digest-level record for one file present in the transient run directory."""

    role: str
    relative_path: str
    sha256: str
    size_bytes: int
    source_artifact_id: ArtifactId | None = None
    execution_artifact_id: ArtifactId | None = None
    licensed: bool = False

    def __post_init__(self) -> None:
        if not self.role.strip():
            raise ValueError("runtime file role must not be blank")
        _validate_relative_path(self.relative_path, "relative_path")
        _validate_sha256(self.sha256, "sha256")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must not be negative")


@dataclass(frozen=True, slots=True)
class RuntimeInputManifest:
    """Portable provenance for the exact transient inputs consumed by a run."""

    attempt_id: ExecutionAttemptId
    plan_hash: str
    execution_settings_hash: str
    environment: ExecutionEnvironmentSnapshot
    files: tuple[RuntimeFileRecord, ...]
    text: str = field(init=False)
    sha256: str = field(init=False)

    def __post_init__(self) -> None:
        _validate_sha256(self.plan_hash, "plan_hash")
        _validate_sha256(self.execution_settings_hash, "execution_settings_hash")
        ordered = tuple(sorted(self.files, key=lambda item: item.role))
        if len({item.role for item in ordered}) != len(ordered):
            raise ValueError("runtime manifest roles must be unique")
        if len({item.relative_path for item in ordered}) != len(ordered):
            raise ValueError("runtime manifest paths must be unique")
        object.__setattr__(self, "files", ordered)
        payload = {
            "schema_version": 1,
            "attempt_id": self.attempt_id,
            "plan_hash": self.plan_hash,
            "execution_settings_hash": self.execution_settings_hash,
            "environment": self.environment,
            "files": ordered,
        }
        text = canonical_json(payload) + "\n"
        object.__setattr__(self, "text", text)
        object.__setattr__(
            self,
            "sha256",
            hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )


@dataclass(frozen=True, slots=True)
class LocalRuntimePackage:
    """Prepared local run plus persistent execution provenance artifacts."""

    project_root: Path
    run_directory: Path
    artifact_directory_relative: str
    plan: ExecutionPlan
    target: ExecutionTargetProfile
    attempt: ExecutionAttempt
    manifest: RuntimeInputManifest
    artifacts: tuple[Artifact, ...]

    def __post_init__(self) -> None:
        if self.attempt.status is not ExecutionAttemptStatus.STAGING:
            raise ValueError("LocalRuntimePackage attempt must be in STAGING state")
        if self.attempt.execution_plan_hash != self.plan.plan_hash:
            raise ValueError("LocalRuntimePackage attempt does not pin its ExecutionPlan")
        _validate_relative_path(
            self.artifact_directory_relative,
            "artifact_directory_relative",
        )


def materialize_local_runtime(
    *,
    project_root: Path | str,
    run_directory: Path | str,
    plan: ExecutionPlan,
    calculation: Calculation,
    attempt: ExecutionAttempt,
    target: ExecutionTargetProfile,
    potcars: LocalPotcarResolution,
) -> LocalRuntimePackage:
    """Materialize one exact local VASP runtime without persisting licensed POTCAR bodies."""

    validate_execution_attempt_plan(
        plan=plan,
        calculation=calculation,
        attempt=attempt,
    )
    if attempt.status is not ExecutionAttemptStatus.CREATED:
        raise RuntimeMaterializationError("local runtime requires a CREATED ExecutionAttempt")
    _validate_local_target(plan, target)
    _validate_potcar_resolution(plan, target, potcars)

    root = Path(project_root).resolve()
    if not root.is_dir():
        raise RuntimeMaterializationError("project_root must be an existing directory")
    run = Path(run_directory).resolve()
    if run == root or run.is_relative_to(root):
        raise RuntimeMaterializationError(
            "local run_directory must be transient and outside project_root"
        )
    if run.exists():
        raise RuntimeMaterializationError("local run_directory must not already exist")

    artifact_relative = PurePosixPath("artifacts", "execution", str(attempt.id))
    artifact_directory = root.joinpath(*artifact_relative.parts)
    if artifact_directory.exists():
        raise RuntimeMaterializationError(
            "ExecutionAttempt artifact directory already exists"
        )

    run.mkdir(parents=True, exist_ok=False)
    artifact_directory.mkdir(parents=True, exist_ok=False)
    try:
        return _materialize_into_directories(
            project_root=root,
            run_directory=run,
            artifact_directory=artifact_directory,
            artifact_relative=artifact_relative,
            plan=plan,
            attempt=attempt,
            target=target,
            potcars=potcars,
        )
    except Exception:
        shutil.rmtree(run, ignore_errors=True)
        shutil.rmtree(artifact_directory, ignore_errors=True)
        raise


def _materialize_into_directories(
    *,
    project_root: Path,
    run_directory: Path,
    artifact_directory: Path,
    artifact_relative: PurePosixPath,
    plan: ExecutionPlan,
    attempt: ExecutionAttempt,
    target: ExecutionTargetProfile,
    potcars: LocalPotcarResolution,
) -> LocalRuntimePackage:
    staging_targets = {item.target_relative_path for item in plan.staging_inputs}
    reserved = set(_RESERVED_RUNTIME_PATHS)
    reserved.add(plan.potcar_resolution.target_relative_path)
    collision = staging_targets & reserved
    if collision:
        names = ", ".join(sorted(collision))
        raise RuntimeMaterializationError(
            f"ExecutionPlan staging paths collide with runtime-reserved paths: {names}"
        )

    records: list[RuntimeFileRecord] = []
    source_incar: tuple[StagingInput, bytes] | None = None
    for item in plan.staging_inputs:
        data = _read_staging_source(project_root, item)
        if item.role == "incar":
            source_incar = (item, data)
            continue
        destination = _runtime_path(run_directory, item.target_relative_path)
        _write_new(destination, data)
        records.append(
            RuntimeFileRecord(
                role=item.role,
                relative_path=item.target_relative_path,
                sha256=item.sha256,
                size_bytes=item.size_bytes,
                source_artifact_id=item.artifact_id,
            )
        )

    if source_incar is None:
        raise RuntimeMaterializationError("ExecutionPlan is missing the source INCAR")
    incar_input, incar_bytes = source_incar
    try:
        source_text = incar_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeMaterializationError("source INCAR must be UTF-8 text") from exc
    runtime_incar = _render_runtime_incar(source_text, plan)
    runtime_incar_bytes = runtime_incar.encode("utf-8")
    _write_new(run_directory / "INCAR", runtime_incar_bytes)
    persistent_incar = artifact_directory / "INCAR.runtime"
    _write_new(persistent_incar, runtime_incar_bytes)
    runtime_incar_artifact = _artifact_from_persistent_file(
        project_root=project_root,
        path=persistent_incar,
        artifact_type=ArtifactType.INCAR,
        attempt=attempt,
    )
    records.append(
        RuntimeFileRecord(
            role="incar",
            relative_path="INCAR",
            sha256=runtime_incar_artifact.sha256 or "",
            size_bytes=runtime_incar_artifact.size_bytes or 0,
            source_artifact_id=incar_input.artifact_id,
            execution_artifact_id=runtime_incar_artifact.id,
        )
    )

    potcar_bytes = _concatenate_verified_potcars(plan, potcars)
    potcar_path = _runtime_path(
        run_directory,
        plan.potcar_resolution.target_relative_path,
    )
    _write_new(potcar_path, potcar_bytes)
    records.append(
        RuntimeFileRecord(
            role="potcar",
            relative_path=plan.potcar_resolution.target_relative_path,
            sha256=hashlib.sha256(potcar_bytes).hexdigest(),
            size_bytes=len(potcar_bytes),
            licensed=True,
        )
    )

    plan_text = canonical_json(
        {
            "schema_version": 1,
            "plan_hash": plan.plan_hash,
            "plan": plan,
        }
    ) + "\n"
    plan_bytes = plan_text.encode("utf-8")
    persistent_plan = artifact_directory / "execution-plan.json"
    _write_new(persistent_plan, plan_bytes)
    _write_new(run_directory / "execution-plan.json", plan_bytes)
    plan_artifact = _artifact_from_persistent_file(
        project_root=project_root,
        path=persistent_plan,
        artifact_type=ArtifactType.EXECUTION_PLAN,
        attempt=attempt,
    )
    records.append(
        RuntimeFileRecord(
            role="execution_plan",
            relative_path="execution-plan.json",
            sha256=plan_artifact.sha256 or "",
            size_bytes=plan_artifact.size_bytes or 0,
            execution_artifact_id=plan_artifact.id,
        )
    )

    manifest = RuntimeInputManifest(
        attempt_id=attempt.id,
        plan_hash=plan.plan_hash,
        execution_settings_hash=plan.execution_settings_hash,
        environment=target.sanitized_environment(),
        files=tuple(records),
    )
    manifest_bytes = manifest.text.encode("utf-8")
    persistent_manifest = artifact_directory / "runtime-input-manifest.json"
    _write_new(persistent_manifest, manifest_bytes)
    _write_new(run_directory / "runtime-input-manifest.json", manifest_bytes)
    manifest_artifact = _artifact_from_persistent_file(
        project_root=project_root,
        path=persistent_manifest,
        artifact_type=ArtifactType.DERIVED_DATASET,
        attempt=attempt,
    )
    if manifest_artifact.sha256 != manifest.sha256:
        raise RuntimeMaterializationError("runtime manifest Artifact digest mismatch")

    staged_attempt = replace(attempt, status=ExecutionAttemptStatus.STAGING)
    return LocalRuntimePackage(
        project_root=project_root,
        run_directory=run_directory,
        artifact_directory_relative=artifact_relative.as_posix(),
        plan=plan,
        target=target,
        attempt=staged_attempt,
        manifest=manifest,
        artifacts=(
            plan_artifact,
            runtime_incar_artifact,
            manifest_artifact,
        ),
    )


def _validate_local_target(
    plan: ExecutionPlan,
    target: ExecutionTargetProfile,
) -> None:
    if target.transport is not TransportKind.LOCAL or target.scheduler is not None:
        raise RuntimeMaterializationError(
            "Block 3 LocalExecutor requires a scheduler-free LOCAL target"
        )
    if target.vasp_executable != plan.execution_settings.executable:
        raise RuntimeMaterializationError(
            "ExecutionTargetProfile executable does not match ExecutionPlan"
        )
    if target.launcher is not None:
        raise RuntimeMaterializationError(
            "Block 3 local execution does not support launcher command synthesis"
        )
    if target.module_loads:
        raise RuntimeMaterializationError(
            "Block 3 local execution does not support module loading"
        )

    settings = plan.execution_settings
    scheduler_fields = {
        "nodes": settings.nodes,
        "cores": settings.cores,
        "memory_mb": settings.memory_mb,
        "walltime_seconds": settings.walltime_seconds,
        "partition": settings.partition,
    }
    configured = sorted(name for name, value in scheduler_fields.items() if value is not None)
    if configured:
        raise RuntimeMaterializationError(
            "scheduler resource settings are invalid for Block 3 local execution: "
            + ", ".join(configured)
        )
    if settings.mpi_ranks not in {None, 1}:
        raise RuntimeMaterializationError(
            "Block 3 LocalExecutor supports only one MPI rank"
        )


def _validate_potcar_resolution(
    plan: ExecutionPlan,
    target: ExecutionTargetProfile,
    potcars: LocalPotcarResolution,
) -> None:
    if potcars.resolver_id != target.potcar_resolver_id:
        raise RuntimeMaterializationError(
            "POTCAR resolver identity does not match ExecutionTargetProfile"
        )
    request = plan.potcar_resolution
    resolved = potcars.resolved
    if request.target_relative_path != "POTCAR":
        raise RuntimeMaterializationError("local VASP runtime requires POTCAR target path")
    if resolved.spec.core_method_hash != request.core_method_hash:
        raise RuntimeMaterializationError("resolved POTCAR core method hash mismatch")
    if resolved.spec.metadata_hash != request.metadata_hash:
        raise RuntimeMaterializationError("resolved POTCAR metadata hash mismatch")
    families = {item.family for item in resolved.spec.entries}
    if families != {request.family}:
        raise RuntimeMaterializationError("resolved POTCAR family mismatch")
    if len(resolved.files) != len(request.entries):
        raise RuntimeMaterializationError("resolved POTCAR entry count mismatch")
    for expected, actual in zip(request.entries, resolved.files, strict=True):
        entry = actual.entry
        if (entry.element, entry.symbol, entry.sha256) != (
            expected.element,
            expected.symbol,
            expected.sha256,
        ):
            raise RuntimeMaterializationError("resolved POTCAR identity/order mismatch")


def _concatenate_verified_potcars(
    plan: ExecutionPlan,
    potcars: LocalPotcarResolution,
) -> bytes:
    bodies: list[bytes] = []
    for expected, actual in zip(
        plan.potcar_resolution.entries,
        potcars.resolved.files,
        strict=True,
    ):
        if not actual.path.is_file():
            raise RuntimeMaterializationError(
                f"licensed POTCAR is missing for {expected.element}/{expected.symbol}"
            )
        body = actual.path.read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        if digest != expected.sha256:
            raise RuntimeMaterializationError(
                f"licensed POTCAR hash mismatch for {expected.element}/{expected.symbol}"
            )
        bodies.append(body)
    return b"".join(bodies)


def _render_runtime_incar(source: str, plan: ExecutionPlan) -> str:
    existing = _incar_keys(source)
    leaked = sorted(existing & _EXECUTION_INCAR_KEYS)
    if leaked:
        raise RuntimeMaterializationError(
            "scientific INCAR already contains execution-only keys: "
            + ", ".join(leaked)
        )

    settings = plan.execution_settings
    extra = {item.name.upper(): item.value for item in settings.extra_parameters}
    unsupported = sorted(set(extra) - {"NPAR"})
    if unsupported:
        raise RuntimeMaterializationError(
            "unsupported execution extra_parameters for runtime INCAR: "
            + ", ".join(unsupported)
        )
    npar = extra.get("NPAR")
    if npar is not None and (
        isinstance(npar, bool) or not isinstance(npar, int) or npar < 1
    ):
        raise RuntimeMaterializationError("NPAR must be a positive integer")
    if npar is not None and settings.ncore is not None:
        raise RuntimeMaterializationError("NCORE and NPAR must not both be set")

    overlay: list[tuple[str, int]] = []
    if settings.ncore is not None:
        overlay.append(("NCORE", settings.ncore))
    if settings.kpar is not None:
        overlay.append(("KPAR", settings.kpar))
    if isinstance(npar, int) and not isinstance(npar, bool):
        overlay.append(("NPAR", npar))

    text = source if source.endswith("\n") else source + "\n"
    if not overlay:
        return text
    lines = ["# ECatVASP execution overlay"]
    lines.extend(f"{name} = {value}" for name, value in overlay)
    return text + "\n".join(lines) + "\n"


def _incar_keys(text: str) -> frozenset[str]:
    keys: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].split("!", 1)[0].strip()
        if "=" not in line:
            continue
        key = line.split("=", 1)[0].strip().upper()
        if key:
            keys.add(key)
    return frozenset(keys)


def _read_staging_source(project_root: Path, item: StagingInput) -> bytes:
    source = _project_path(project_root, item.source_relative_path)
    if not source.is_file():
        raise RuntimeMaterializationError(
            f"staging source is missing: {item.source_relative_path}"
        )
    body = source.read_bytes()
    if len(body) != item.size_bytes:
        raise RuntimeMaterializationError(
            f"staging source size changed: {item.source_relative_path}"
        )
    if hashlib.sha256(body).hexdigest() != item.sha256:
        raise RuntimeMaterializationError(
            f"staging source hash changed: {item.source_relative_path}"
        )
    return body


def _artifact_from_persistent_file(
    *,
    project_root: Path,
    path: Path,
    artifact_type: ArtifactType,
    attempt: ExecutionAttempt,
) -> Artifact:
    body = path.read_bytes()
    relative = path.relative_to(project_root)
    return Artifact(
        artifact_type=artifact_type,
        producer=ExecutionAttemptProducerRef(attempt.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=PurePosixPath(*relative.parts).as_posix(),
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
    )


def _project_path(root: Path, value: str) -> Path:
    _validate_relative_path(value, "project-relative path")
    path = root.joinpath(*PurePosixPath(value).parts).resolve()
    if not path.is_relative_to(root):
        raise RuntimeMaterializationError("project-relative path escapes project_root")
    return path


def _runtime_path(root: Path, value: str) -> Path:
    _validate_relative_path(value, "runtime-relative path")
    path = root.joinpath(*PurePosixPath(value).parts).resolve()
    if not path.is_relative_to(root):
        raise RuntimeMaterializationError("runtime-relative path escapes run_directory")
    return path


def _validate_relative_path(value: str, field_name: str) -> None:
    path = PurePosixPath(value)
    if (
        not value
        or value == "."
        or path.is_absolute()
        or path.as_posix() != value
        or ".." in path.parts
    ):
        raise ValueError(f"{field_name} must be a normalized relative POSIX path")


def _validate_sha256(value: str, field_name: str) -> None:
    normalized = value.lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{field_name} must be a 64-character hexadecimal SHA-256 digest")


def _write_new(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(body)
    except FileExistsError as exc:
        raise RuntimeMaterializationError(
            f"runtime materialization refuses to overwrite: {path.name}"
        ) from exc
