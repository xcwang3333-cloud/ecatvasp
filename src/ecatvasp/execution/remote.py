"""SSH remote staging and licensed POTCAR integrity gates for v0.4 Block 4."""

from __future__ import annotations

import hashlib
import re
import tempfile
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
from ecatvasp.execution.adapters import (
    CommandSpec,
    TargetRelativePath,
    TransportAdapter,
)
from ecatvasp.execution.provenance import validate_execution_attempt_plan
from ecatvasp.execution.runtime import _render_runtime_incar
from ecatvasp.execution.ssh import remote_absolute_path
from ecatvasp.execution.targets import (
    ExecutionEnvironmentSnapshot,
    ExecutionTargetProfile,
    TransportKind,
)
from ecatvasp.vasp.execution_plan import ExecutionPlan, StagingInput

_SAFE_PATH_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+@%=-]*$")


class RemoteStagingError(RuntimeError):
    """Raised when an immutable ExecutionPlan cannot pass remote staging integrity gates."""


@dataclass(frozen=True, slots=True)
class RemotePotcarLibrary:
    """User-local configuration for one licensed POTCAR tree already present remotely."""

    resolver_id: str
    family: str
    root: str

    def __post_init__(self) -> None:
        if not self.resolver_id.strip() or not self.family.strip():
            raise ValueError("remote POTCAR resolver id and family must not be blank")
        _validate_absolute_safe_path(self.root, "remote POTCAR root")


@dataclass(frozen=True, slots=True)
class RemoteStageFileRecord:
    """Digest-level provenance for one file verified in the isolated remote stage."""

    role: str
    relative_path: str
    sha256: str
    size_bytes: int
    source_artifact_id: ArtifactId | None = None
    execution_artifact_id: ArtifactId | None = None
    licensed: bool = False

    def __post_init__(self) -> None:
        if not self.role.strip():
            raise ValueError("remote stage role must not be blank")
        TargetRelativePath(self.relative_path)
        _validate_sha256(self.sha256, "sha256")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must not be negative")


@dataclass(frozen=True, slots=True)
class RemoteStageManifest:
    """Portable manifest for a fully verified remote staging directory."""

    attempt_id: ExecutionAttemptId
    plan_hash: str
    execution_settings_hash: str
    environment: ExecutionEnvironmentSnapshot
    remote_directory: str
    files: tuple[RemoteStageFileRecord, ...]
    text: str = field(init=False)
    sha256: str = field(init=False)

    def __post_init__(self) -> None:
        _validate_sha256(self.plan_hash, "plan_hash")
        _validate_sha256(self.execution_settings_hash, "execution_settings_hash")
        TargetRelativePath(self.remote_directory)
        ordered = tuple(sorted(self.files, key=lambda item: item.role))
        if len({item.role for item in ordered}) != len(ordered):
            raise ValueError("remote stage manifest roles must be unique")
        if len({item.relative_path for item in ordered}) != len(ordered):
            raise ValueError("remote stage manifest paths must be unique")
        object.__setattr__(self, "files", ordered)
        payload = {
            "schema_version": 1,
            "attempt_id": self.attempt_id,
            "plan_hash": self.plan_hash,
            "execution_settings_hash": self.execution_settings_hash,
            "environment": self.environment,
            "remote_directory": self.remote_directory,
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
class RemoteStagePackage:
    """A verified remote stage plus local redistribution-safe execution provenance."""

    project_root: Path
    plan: ExecutionPlan
    target: ExecutionTargetProfile
    attempt: ExecutionAttempt
    remote_directory: TargetRelativePath
    manifest: RemoteStageManifest
    artifacts: tuple[Artifact, ...]

    def __post_init__(self) -> None:
        if self.attempt.status is not ExecutionAttemptStatus.STAGING:
            raise ValueError("RemoteStagePackage attempt must be STAGING")
        if self.attempt.execution_plan_hash != self.plan.plan_hash:
            raise ValueError("RemoteStagePackage attempt does not pin its ExecutionPlan")


def stage_remote_runtime(
    *,
    project_root: Path | str,
    plan: ExecutionPlan,
    calculation: Calculation,
    attempt: ExecutionAttempt,
    target: ExecutionTargetProfile,
    transport: TransportAdapter,
    potcars: RemotePotcarLibrary,
) -> RemoteStagePackage:
    """Create and integrity-verify one isolated SSH stage without moving POTCAR bodies locally."""

    validate_execution_attempt_plan(plan=plan, calculation=calculation, attempt=attempt)
    if attempt.status is not ExecutionAttemptStatus.CREATED:
        raise RemoteStagingError("remote staging requires a CREATED ExecutionAttempt")
    _validate_stage_target(plan, target, transport, potcars)

    root = Path(project_root).resolve()
    if not root.is_dir():
        raise RemoteStagingError("project_root must be an existing directory")
    artifact_relative = PurePosixPath("artifacts", "execution", str(attempt.id))
    artifact_directory = root.joinpath(*artifact_relative.parts)
    if artifact_directory.exists():
        raise RemoteStagingError("ExecutionAttempt artifact directory already exists")

    remote_parent = TargetRelativePath("execution")
    remote_directory = TargetRelativePath(f"execution/{attempt.id}")
    transport.ensure_directory(target=target, path=remote_parent)
    mkdir = transport.run(
        target=target,
        command=CommandSpec(
            argv=("mkdir", "--", remote_absolute_path(target, remote_directory))
        ),
    )
    _require_remote_success(mkdir.exit_code, mkdir.stderr, "create isolated remote stage")

    with tempfile.TemporaryDirectory(prefix="ecatvasp-remote-stage-") as temporary:
        temp = Path(temporary)
        records, runtime_incar, plan_bytes = _stage_safe_inputs(
            project_root=root,
            plan=plan,
            attempt=attempt,
            target=target,
            transport=transport,
            remote_directory=remote_directory,
            temporary_directory=temp,
        )
        records.append(
            _resolve_remote_potcar(
                plan=plan,
                target=target,
                transport=transport,
                remote_directory=remote_directory,
                potcars=potcars,
            )
        )

        manifest = RemoteStageManifest(
            attempt_id=attempt.id,
            plan_hash=plan.plan_hash,
            execution_settings_hash=plan.execution_settings_hash,
            environment=target.sanitized_environment(),
            remote_directory=remote_directory.value,
            files=tuple(records),
        )
        manifest_temp = temp / "remote-stage-manifest.json"
        manifest_temp.write_text(manifest.text, encoding="utf-8")
        manifest_remote = _stage_path(remote_directory, "remote-stage-manifest.json")
        _upload_and_verify(
            target=target,
            transport=transport,
            local_path=manifest_temp,
            destination=manifest_remote,
            expected_sha=manifest.sha256,
            expected_size=len(manifest.text.encode("utf-8")),
        )

        artifact_directory.mkdir(parents=True, exist_ok=False)
        try:
            runtime_incar_path = artifact_directory / "INCAR.runtime"
            runtime_incar_path.write_bytes(runtime_incar)
            plan_path = artifact_directory / "execution-plan.json"
            plan_path.write_bytes(plan_bytes)
            manifest_path = artifact_directory / "remote-stage-manifest.json"
            manifest_path.write_text(manifest.text, encoding="utf-8")
            runtime_artifact = _artifact_from_file(
                project_root=root,
                path=runtime_incar_path,
                artifact_type=ArtifactType.INCAR,
                attempt=attempt,
            )
            plan_artifact = _artifact_from_file(
                project_root=root,
                path=plan_path,
                artifact_type=ArtifactType.EXECUTION_PLAN,
                attempt=attempt,
            )
            manifest_artifact = _artifact_from_file(
                project_root=root,
                path=manifest_path,
                artifact_type=ArtifactType.REMOTE_STAGE_MANIFEST,
                attempt=attempt,
            )
            if manifest_artifact.sha256 != manifest.sha256:
                raise RemoteStagingError("remote stage manifest Artifact digest mismatch")
        except Exception:
            for path in artifact_directory.iterdir():
                path.unlink(missing_ok=True)
            artifact_directory.rmdir()
            raise

    staged_attempt = replace(attempt, status=ExecutionAttemptStatus.STAGING)
    return RemoteStagePackage(
        project_root=root,
        plan=plan,
        target=target,
        attempt=staged_attempt,
        remote_directory=remote_directory,
        manifest=manifest,
        artifacts=(plan_artifact, runtime_artifact, manifest_artifact),
    )


def _stage_safe_inputs(
    *,
    project_root: Path,
    plan: ExecutionPlan,
    attempt: ExecutionAttempt,
    target: ExecutionTargetProfile,
    transport: TransportAdapter,
    remote_directory: TargetRelativePath,
    temporary_directory: Path,
) -> tuple[list[RemoteStageFileRecord], bytes, bytes]:
    records: list[RemoteStageFileRecord] = []
    source_incar: tuple[StagingInput, bytes] | None = None
    reserved = {"POTCAR", "execution-plan.json", "remote-stage-manifest.json"}
    for item in plan.staging_inputs:
        if item.target_relative_path in reserved:
            raise RemoteStagingError("ExecutionPlan staging path collides with remote runtime path")
        body = _read_staging_source(project_root, item)
        if item.role == "incar":
            source_incar = (item, body)
            continue
        destination = _stage_path(remote_directory, item.target_relative_path)
        source = _project_file(project_root, item.source_relative_path)
        _upload_and_verify(
            target=target,
            transport=transport,
            local_path=source,
            destination=destination,
            expected_sha=item.sha256,
            expected_size=item.size_bytes,
        )
        records.append(
            RemoteStageFileRecord(
                role=item.role,
                relative_path=item.target_relative_path,
                sha256=item.sha256,
                size_bytes=item.size_bytes,
                source_artifact_id=item.artifact_id,
            )
        )
    if source_incar is None:
        raise RemoteStagingError("ExecutionPlan is missing source INCAR")

    incar_input, incar_bytes = source_incar
    try:
        source_text = incar_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RemoteStagingError("source INCAR must be UTF-8 text") from exc
    try:
        runtime_text = _render_runtime_incar(source_text, plan)
    except ValueError as exc:
        raise RemoteStagingError(str(exc)) from exc
    runtime_incar = runtime_text.encode("utf-8")
    runtime_incar_sha = hashlib.sha256(runtime_incar).hexdigest()
    runtime_temp = temporary_directory / "INCAR"
    runtime_temp.write_bytes(runtime_incar)
    _upload_and_verify(
        target=target,
        transport=transport,
        local_path=runtime_temp,
        destination=_stage_path(remote_directory, "INCAR"),
        expected_sha=runtime_incar_sha,
        expected_size=len(runtime_incar),
    )
    records.append(
        RemoteStageFileRecord(
            role="incar",
            relative_path="INCAR",
            sha256=runtime_incar_sha,
            size_bytes=len(runtime_incar),
            source_artifact_id=incar_input.artifact_id,
        )
    )

    plan_text = canonical_json(
        {"schema_version": 1, "plan_hash": plan.plan_hash, "plan": plan}
    ) + "\n"
    plan_bytes = plan_text.encode("utf-8")
    plan_temp = temporary_directory / "execution-plan.json"
    plan_temp.write_bytes(plan_bytes)
    plan_sha = hashlib.sha256(plan_bytes).hexdigest()
    _upload_and_verify(
        target=target,
        transport=transport,
        local_path=plan_temp,
        destination=_stage_path(remote_directory, "execution-plan.json"),
        expected_sha=plan_sha,
        expected_size=len(plan_bytes),
    )
    records.append(
        RemoteStageFileRecord(
            role="execution_plan",
            relative_path="execution-plan.json",
            sha256=plan_sha,
            size_bytes=len(plan_bytes),
        )
    )
    return records, runtime_incar, plan_bytes


def _resolve_remote_potcar(
    *,
    plan: ExecutionPlan,
    target: ExecutionTargetProfile,
    transport: TransportAdapter,
    remote_directory: TargetRelativePath,
    potcars: RemotePotcarLibrary,
) -> RemoteStageFileRecord:
    request = plan.potcar_resolution
    if request.target_relative_path != "POTCAR":
        raise RemoteStagingError("remote VASP runtime requires POTCAR target path")
    sources: list[str] = []
    for entry in request.entries:
        _validate_path_part(entry.symbol, "POTCAR symbol")
        source = (PurePosixPath(potcars.root) / entry.symbol / "POTCAR").as_posix()
        digest, _ = _remote_digest_and_size(target, transport, source)
        if digest != entry.sha256:
            raise RemoteStagingError(
                f"remote POTCAR hash mismatch for {entry.element}/{entry.symbol}"
            )
        sources.append(source)

    destination = remote_absolute_path(target, _stage_path(remote_directory, "POTCAR"))
    first = transport.run(
        target=target,
        command=CommandSpec(argv=("cp", "--", sources[0], destination)),
    )
    _require_remote_success(first.exit_code, first.stderr, "materialize remote POTCAR")
    for source in sources[1:]:
        append = transport.run(
            target=target,
            command=CommandSpec(
                argv=(
                    "dd",
                    f"if={source}",
                    f"of={destination}",
                    "oflag=append",
                    "conv=notrunc",
                    "status=none",
                )
            ),
        )
        _require_remote_success(append.exit_code, append.stderr, "append remote POTCAR")
    digest, size = _remote_digest_and_size(target, transport, destination)
    return RemoteStageFileRecord(
        role="potcar",
        relative_path="POTCAR",
        sha256=digest,
        size_bytes=size,
        licensed=True,
    )


def _upload_and_verify(
    *,
    target: ExecutionTargetProfile,
    transport: TransportAdapter,
    local_path: Path,
    destination: TargetRelativePath,
    expected_sha: str,
    expected_size: int,
) -> None:
    transport.upload(target=target, local_path=local_path, destination=destination)
    absolute = remote_absolute_path(target, destination)
    digest, size = _remote_digest_and_size(target, transport, absolute)
    if digest != expected_sha or size != expected_size:
        raise RemoteStagingError(
            f"remote staging integrity mismatch for {destination.value}"
        )


def _remote_digest_and_size(
    target: ExecutionTargetProfile,
    transport: TransportAdapter,
    absolute_path: str,
) -> tuple[str, int]:
    digest_result = transport.run(
        target=target,
        command=CommandSpec(argv=("sha256sum", "--", absolute_path)),
    )
    _require_remote_success(digest_result.exit_code, digest_result.stderr, "remote sha256sum")
    fields = digest_result.stdout.strip().split()
    if not fields:
        raise RemoteStagingError("remote sha256sum returned no digest")
    digest = fields[0].lower()
    _validate_sha256(digest, "remote sha256")
    size_result = transport.run(
        target=target,
        command=CommandSpec(argv=("stat", "-c", "%s", "--", absolute_path)),
    )
    _require_remote_success(size_result.exit_code, size_result.stderr, "remote stat")
    try:
        size = int(size_result.stdout.strip())
    except ValueError as exc:
        raise RemoteStagingError("remote stat returned an invalid size") from exc
    if size < 0:
        raise RemoteStagingError("remote stat returned a negative size")
    return digest, size


def _validate_stage_target(
    plan: ExecutionPlan,
    target: ExecutionTargetProfile,
    transport: TransportAdapter,
    potcars: RemotePotcarLibrary,
) -> None:
    if target.transport is not TransportKind.SSH or target.scheduler is None:
        raise RemoteStagingError("Block 4 remote staging requires an SSH scheduler target")
    if transport.transport_kind is not TransportKind.SSH:
        raise RemoteStagingError("remote staging requires an SSH TransportAdapter")
    if target.vasp_executable != plan.execution_settings.executable:
        raise RemoteStagingError("ExecutionTargetProfile executable does not match ExecutionPlan")
    if target.potcar_resolver_id != potcars.resolver_id:
        raise RemoteStagingError("remote POTCAR resolver identity does not match target")
    if plan.potcar_resolution.family != potcars.family:
        raise RemoteStagingError("remote POTCAR family does not match ExecutionPlan")


def _stage_path(directory: TargetRelativePath, child: str) -> TargetRelativePath:
    _validate_relative_child(child)
    return TargetRelativePath((PurePosixPath(directory.value) / child).as_posix())


def _read_staging_source(project_root: Path, item: StagingInput) -> bytes:
    source = _project_file(project_root, item.source_relative_path)
    body = source.read_bytes()
    if len(body) != item.size_bytes:
        raise RemoteStagingError(f"staging source size changed: {item.source_relative_path}")
    if hashlib.sha256(body).hexdigest() != item.sha256:
        raise RemoteStagingError(f"staging source hash changed: {item.source_relative_path}")
    return body


def _project_file(project_root: Path, relative_text: str) -> Path:
    relative = TargetRelativePath(relative_text)
    path = project_root.joinpath(*PurePosixPath(relative.value).parts).resolve()
    if not path.is_relative_to(project_root) or not path.is_file():
        raise RemoteStagingError(f"project staging file is missing: {relative_text}")
    return path


def _artifact_from_file(
    *,
    project_root: Path,
    path: Path,
    artifact_type: ArtifactType,
    attempt: ExecutionAttempt,
) -> Artifact:
    body = path.read_bytes()
    relative = path.relative_to(project_root).as_posix()
    return Artifact(
        artifact_type=artifact_type,
        producer=ExecutionAttemptProducerRef(attempt.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=relative,
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
    )


def _require_remote_success(exit_code: int, stderr: str, operation: str) -> None:
    if exit_code != 0:
        detail = stderr.strip() or "no diagnostic output"
        raise RemoteStagingError(f"{operation} failed: {detail}")


def _validate_sha256(value: str, field_name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")


def _validate_absolute_safe_path(value: str, field_name: str) -> None:
    path = PurePosixPath(value)
    if not path.is_absolute() or path == PurePosixPath("/") or ".." in path.parts:
        raise ValueError(f"{field_name} must be a non-root absolute POSIX path")
    for part in path.parts[1:]:
        _validate_path_part(part, field_name)


def _validate_path_part(value: str, field_name: str) -> None:
    if not _SAFE_PATH_PART.fullmatch(value):
        raise ValueError(f"{field_name} contains unsafe shell/path syntax")


def _validate_relative_child(value: str) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or path == PurePosixPath(".") or ".." in path.parts:
        raise RemoteStagingError("remote stage child must be a normalized relative path")
