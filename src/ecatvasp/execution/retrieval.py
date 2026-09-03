"""Remote output retrieval, artifact lifecycle, and retention for v0.4 Block 7."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

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
)
from ecatvasp.domain.ids import ExecutionAttemptId, RemoteJobId
from ecatvasp.domain.method import canonical_json, canonical_sha256
from ecatvasp.execution.adapters import CommandSpec, TargetRelativePath, TransportAdapter
from ecatvasp.execution.ssh import remote_absolute_path
from ecatvasp.execution.targets import (
    ExecutionEnvironmentSnapshot,
    ExecutionTargetProfile,
    TransportKind,
)
from ecatvasp.vasp.execution_plan import ExecutionPlan, ExpectedOutput

_TERMINAL_REMOTE_STATES = frozenset(
    {
        SchedulerState.COMPLETED,
        SchedulerState.FAILED,
        SchedulerState.TIMEOUT,
        SchedulerState.CANCELLED,
        SchedulerState.NODE_FAIL,
        SchedulerState.OUT_OF_MEMORY,
    }
)
_RETRIEVABLE_ATTEMPT_STATES = frozenset(
    {
        ExecutionAttemptStatus.EXITED,
        ExecutionAttemptStatus.RETRIEVING,
        ExecutionAttemptStatus.FAILED,
        ExecutionAttemptStatus.CANCELLED,
    }
)


class RetrievalError(RuntimeError):
    """Raised when expected outputs cannot be handled without guessing or data loss."""


@dataclass(frozen=True, slots=True)
class RetrievalFileRecord:
    """One expected output after remote integrity inspection and optional movement."""

    role: str
    artifact_type: ArtifactType
    relative_path: str
    retrieval_policy: RetrievalPolicy
    required: bool
    remote_present: bool
    remote_sha256: str | None
    remote_size_bytes: int | None
    local_retrieved: bool
    local_relative_path: str | None
    remote_retained: bool
    final_availability: ArtifactAvailability

    def __post_init__(self) -> None:
        if not self.role.strip():
            raise ValueError("retrieval role must not be blank")
        TargetRelativePath(self.relative_path)
        if not self.remote_present:
            if self.remote_sha256 is not None or self.remote_size_bytes is not None:
                raise ValueError("missing remote output cannot carry digest or size")
            if self.local_retrieved or self.remote_retained:
                raise ValueError("missing remote output cannot be retrieved or retained")
        if self.local_retrieved and self.local_relative_path is None:
            raise ValueError("retrieved output requires local_relative_path")
        if self.remote_retained and not self.remote_present:
            raise ValueError("remote_retained requires remote_present")


@dataclass(frozen=True, slots=True)
class RetrievalManifest:
    """Portable attempt-level record of one retrieval/retention operation."""

    attempt_id: ExecutionAttemptId
    remote_job_id: RemoteJobId
    plan_hash: str
    target: ExecutionEnvironmentSnapshot
    remote_directory: str
    requested_roles: tuple[str, ...]
    release_remote_roles: tuple[str, ...]
    discard_remote_roles: tuple[str, ...]
    retrieved_at: datetime
    files: tuple[RetrievalFileRecord, ...]

    def __post_init__(self) -> None:
        if self.retrieved_at.tzinfo is None:
            raise ValueError("retrieved_at must be timezone-aware")
        TargetRelativePath(self.remote_directory)
        normalized_hash = self.plan_hash.lower()
        if len(normalized_hash) != 64 or any(
            character not in "0123456789abcdef" for character in normalized_hash
        ):
            raise ValueError("plan_hash must be a SHA-256 digest")
        object.__setattr__(self, "plan_hash", normalized_hash)
        roles = tuple(item.role for item in self.files)
        if roles != tuple(sorted(roles)) or len(roles) != len(set(roles)):
            raise ValueError("retrieval file roles must be unique and sorted")

    @property
    def text(self) -> str:
        """Return canonical portable manifest JSON."""

        return canonical_json(
            {
                "schema_version": 1,
                "attempt_id": self.attempt_id,
                "remote_job_id": self.remote_job_id,
                "plan_hash": self.plan_hash,
                "target": self.target,
                "remote_directory": self.remote_directory,
                "requested_roles": self.requested_roles,
                "release_remote_roles": self.release_remote_roles,
                "discard_remote_roles": self.discard_remote_roles,
                "retrieved_at": self.retrieved_at.isoformat(),
                "files": self.files,
            }
        ) + "\n"

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    @property
    def retrieval_hash(self) -> str:
        """Return deterministic semantic identity independent of local manifest location."""

        return canonical_sha256(
            {
                "attempt_id": self.attempt_id,
                "remote_job_id": self.remote_job_id,
                "plan_hash": self.plan_hash,
                "target_hash": self.target.target_hash,
                "remote_directory": self.remote_directory,
                "requested_roles": self.requested_roles,
                "release_remote_roles": self.release_remote_roles,
                "discard_remote_roles": self.discard_remote_roles,
                "files": self.files,
            }
        )


@dataclass(frozen=True, slots=True)
class RemoteRetrievalPackage:
    """One retrieval result without performing scientific output parsing."""

    attempt: ExecutionAttempt
    remote_job: RemoteJob
    manifest: RetrievalManifest
    output_artifacts: tuple[Artifact, ...]
    manifest_artifact: Artifact

    def __post_init__(self) -> None:
        if self.remote_job.execution_attempt_id != self.attempt.id:
            raise ValueError("retrieval RemoteJob must belong to ExecutionAttempt")
        if self.manifest.attempt_id != self.attempt.id:
            raise ValueError("retrieval manifest must belong to ExecutionAttempt")
        if self.manifest.remote_job_id != self.remote_job.id:
            raise ValueError("retrieval manifest must belong to RemoteJob")
        if self.manifest_artifact.artifact_type is not ArtifactType.RETRIEVAL_MANIFEST:
            raise ValueError("retrieval package requires RETRIEVAL_MANIFEST Artifact")
        expected_producer = ExecutionAttemptProducerRef(self.attempt.id)
        if self.manifest_artifact.producer != expected_producer:
            raise ValueError("retrieval manifest Artifact must be attempt-produced")
        if any(item.producer != expected_producer for item in self.output_artifacts):
            raise ValueError("retrieved output Artifacts must be attempt-produced")

    @property
    def artifacts(self) -> tuple[Artifact, ...]:
        return (*self.output_artifacts, self.manifest_artifact)


def retrieve_remote_outputs(
    *,
    project_root: Path | str,
    plan: ExecutionPlan,
    attempt: ExecutionAttempt,
    remote_job: RemoteJob,
    target: ExecutionTargetProfile,
    transport: TransportAdapter,
    requested_roles: tuple[str, ...] = (),
    release_remote_roles: tuple[str, ...] = (),
    discard_remote_roles: tuple[str, ...] = (),
    retrieved_at: datetime | None = None,
) -> RemoteRetrievalPackage:
    """Inspect expected outputs, retrieve policy-selected files, and record retention state.

    ALWAYS outputs are always downloaded when present. ON_DEMAND and DISCARDABLE outputs are
    downloaded only when explicitly requested. REMOTE_ONLY outputs never cross the transport
    boundary. Remote deletion is opt-in: release requires a verified local copy, while discard is
    permitted only for DISCARDABLE outputs. No VASP scientific parsing happens here.
    """

    _validate_retrieval_context(
        plan=plan,
        attempt=attempt,
        remote_job=remote_job,
        target=target,
        transport=transport,
    )
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise RetrievalError("project_root must be an existing directory")
    timestamp = retrieved_at or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise RetrievalError("retrieved_at must be timezone-aware")
    timestamp = timestamp.astimezone(UTC)

    expected_by_role = {item.role: item for item in plan.expected_outputs}
    requested = _validate_role_selection("requested_roles", requested_roles, expected_by_role)
    release = _validate_role_selection(
        "release_remote_roles", release_remote_roles, expected_by_role
    )
    discard = _validate_role_selection(
        "discard_remote_roles", discard_remote_roles, expected_by_role
    )
    _validate_policy_selection(
        expected_by_role=expected_by_role,
        requested=requested,
        release=release,
        discard=discard,
    )

    download_roles = {
        item.role
        for item in plan.expected_outputs
        if item.retrieval_policy is RetrievalPolicy.ALWAYS
    }
    download_roles.update(requested)
    enforce_required = attempt.status in {
        ExecutionAttemptStatus.EXITED,
        ExecutionAttemptStatus.RETRIEVING,
    }

    remote_paths = {
        item.role: TargetRelativePath(
            f"{remote_job.remote_directory}/{item.relative_path}"
        )
        for item in plan.expected_outputs
    }
    observations = {
        item.role: _inspect_remote_file(
            target=target,
            transport=transport,
            path=remote_paths[item.role],
        )
        for item in plan.expected_outputs
    }
    missing_required = sorted(
        item.role
        for item in plan.expected_outputs
        if item.required and observations[item.role] is None and enforce_required
    )
    if missing_required:
        raise RetrievalError(
            "required remote output is missing: " + ", ".join(missing_required)
        )

    output_directory = root / "artifacts" / "execution" / str(attempt.id) / "outputs"
    output_directory.mkdir(parents=True, exist_ok=True)
    records: list[RetrievalFileRecord] = []
    artifacts: list[Artifact] = []

    for expected in plan.expected_outputs:
        remote_path = remote_paths[expected.role]
        remote_info = observations[expected.role]
        if remote_info is None:
            records.append(
                RetrievalFileRecord(
                    role=expected.role,
                    artifact_type=expected.artifact_type,
                    relative_path=expected.relative_path,
                    retrieval_policy=expected.retrieval_policy,
                    required=expected.required,
                    remote_present=False,
                    remote_sha256=None,
                    remote_size_bytes=None,
                    local_retrieved=False,
                    local_relative_path=None,
                    remote_retained=False,
                    final_availability=ArtifactAvailability.MISSING,
                )
            )
            artifacts.append(
                Artifact(
                    artifact_type=expected.artifact_type,
                    producer=ExecutionAttemptProducerRef(attempt.id),
                    availability=ArtifactAvailability.MISSING,
                    retrieval_policy=expected.retrieval_policy,
                    remote_path=remote_path.value,
                )
            )
            continue

        remote_sha, remote_size = remote_info
        local_relative: str | None = None
        local_retrieved = False
        if expected.role in download_roles:
            destination = _local_output_path(root, output_directory, expected)
            _ensure_local_copy(
                target=target,
                transport=transport,
                remote_path=remote_path,
                destination=destination,
                expected_sha=remote_sha,
                expected_size=remote_size,
            )
            local_relative = destination.relative_to(root).as_posix()
            local_retrieved = True

        remote_retained = True
        if expected.role in release:
            if not local_retrieved:
                raise RetrievalError("remote release requires a verified local copy")
            _delete_remote_file(
                target=target,
                transport=transport,
                path=remote_path,
                expected_sha=remote_sha,
                expected_size=remote_size,
            )
            remote_retained = False
        elif expected.role in discard:
            _delete_remote_file(
                target=target,
                transport=transport,
                path=remote_path,
                expected_sha=remote_sha,
                expected_size=remote_size,
            )
            remote_retained = False

        if local_retrieved and remote_retained:
            availability = ArtifactAvailability.BOTH
        elif local_retrieved:
            availability = ArtifactAvailability.LOCAL
        elif remote_retained:
            availability = ArtifactAvailability.REMOTE
        else:
            availability = ArtifactAvailability.MISSING

        records.append(
            RetrievalFileRecord(
                role=expected.role,
                artifact_type=expected.artifact_type,
                relative_path=expected.relative_path,
                retrieval_policy=expected.retrieval_policy,
                required=expected.required,
                remote_present=True,
                remote_sha256=remote_sha,
                remote_size_bytes=remote_size,
                local_retrieved=local_retrieved,
                local_relative_path=local_relative,
                remote_retained=remote_retained,
                final_availability=availability,
            )
        )
        artifacts.append(
            Artifact(
                artifact_type=expected.artifact_type,
                producer=ExecutionAttemptProducerRef(attempt.id),
                availability=availability,
                retrieval_policy=expected.retrieval_policy,
                local_path=local_relative,
                remote_path=remote_path.value if remote_retained else None,
                size_bytes=remote_size,
                sha256=remote_sha,
            )
        )

    manifest = RetrievalManifest(
        attempt_id=attempt.id,
        remote_job_id=remote_job.id,
        plan_hash=plan.plan_hash,
        target=target.sanitized_environment(),
        remote_directory=remote_job.remote_directory,
        requested_roles=tuple(sorted(requested)),
        release_remote_roles=tuple(sorted(release)),
        discard_remote_roles=tuple(sorted(discard)),
        retrieved_at=timestamp,
        files=tuple(sorted(records, key=lambda item: item.role)),
    )
    manifest_artifact = _persist_retrieval_manifest(
        root=root,
        attempt=attempt,
        manifest=manifest,
    )
    next_attempt = (
        replace(attempt, status=ExecutionAttemptStatus.RETRIEVING)
        if attempt.status is ExecutionAttemptStatus.EXITED
        else attempt
    )
    return RemoteRetrievalPackage(
        attempt=next_attempt,
        remote_job=remote_job,
        manifest=manifest,
        output_artifacts=tuple(sorted(artifacts, key=lambda item: item.artifact_type.value)),
        manifest_artifact=manifest_artifact,
    )


def _validate_retrieval_context(
    *,
    plan: ExecutionPlan,
    attempt: ExecutionAttempt,
    remote_job: RemoteJob,
    target: ExecutionTargetProfile,
    transport: TransportAdapter,
) -> None:
    ssh_mismatch = (
        target.transport is not TransportKind.SSH
        or transport.transport_kind is not TransportKind.SSH
    )
    if ssh_mismatch:
        raise RetrievalError("remote retrieval requires matching SSH target and transport")
    if transport.transport_kind is not target.transport:
        raise RetrievalError("transport does not match ExecutionTargetProfile")
    if plan.calculation_id != attempt.calculation_id:
        raise RetrievalError("ExecutionPlan and ExecutionAttempt belong to different Calculations")
    if attempt.execution_plan_hash != plan.plan_hash:
        raise RetrievalError("ExecutionAttempt does not pin this ExecutionPlan hash")
    if attempt.input_manifest_hash != plan.input_manifest_sha256:
        raise RetrievalError("ExecutionAttempt input manifest hash does not match ExecutionPlan")
    if remote_job.execution_attempt_id != attempt.id:
        raise RetrievalError("RemoteJob does not belong to ExecutionAttempt")
    normalized_directory = TargetRelativePath(remote_job.remote_directory).value
    if normalized_directory != remote_job.remote_directory:
        raise RetrievalError("RemoteJob remote_directory must be normalized target-relative path")
    if remote_job.state not in _TERMINAL_REMOTE_STATES:
        raise RetrievalError("remote retrieval requires a terminal scheduler state")
    if attempt.status not in _RETRIEVABLE_ATTEMPT_STATES:
        raise RetrievalError(f"ExecutionAttempt status {attempt.status.value!r} is not retrievable")


def _validate_role_selection(
    field_name: str,
    roles: tuple[str, ...],
    expected_by_role: dict[str, ExpectedOutput],
) -> set[str]:
    if len(roles) != len(set(roles)):
        raise RetrievalError(f"{field_name} must not contain duplicates")
    unknown = sorted(set(roles) - set(expected_by_role))
    if unknown:
        raise RetrievalError(f"{field_name} contains unknown output roles: {', '.join(unknown)}")
    return set(roles)


def _validate_policy_selection(
    *,
    expected_by_role: dict[str, ExpectedOutput],
    requested: set[str],
    release: set[str],
    discard: set[str],
) -> None:
    overlap = release & discard
    if overlap:
        raise RetrievalError(
            "release_remote_roles and discard_remote_roles overlap: "
            + ", ".join(sorted(overlap))
        )
    for role in requested:
        if expected_by_role[role].retrieval_policy is RetrievalPolicy.REMOTE_ONLY:
            raise RetrievalError(f"REMOTE_ONLY output cannot be requested locally: {role}")
    for role in release:
        if expected_by_role[role].retrieval_policy is RetrievalPolicy.REMOTE_ONLY:
            raise RetrievalError(f"REMOTE_ONLY output must remain remote: {role}")
    download_roles = {
        role
        for role, item in expected_by_role.items()
        if item.retrieval_policy is RetrievalPolicy.ALWAYS
    }
    download_roles.update(requested)
    invalid_release = sorted(release - download_roles)
    if invalid_release:
        raise RetrievalError(
            "release_remote_roles require local retrieval first: "
            + ", ".join(invalid_release)
        )
    invalid_discard = sorted(
        role
        for role in discard
        if expected_by_role[role].retrieval_policy is not RetrievalPolicy.DISCARDABLE
    )
    if invalid_discard:
        raise RetrievalError(
            "only DISCARDABLE outputs may be discarded remotely: "
            + ", ".join(invalid_discard)
        )
    if requested & discard:
        raise RetrievalError("requested outputs cannot be discarded without local retention")


def _inspect_remote_file(
    *,
    target: ExecutionTargetProfile,
    transport: TransportAdapter,
    path: TargetRelativePath,
) -> tuple[str, int] | None:
    absolute = remote_absolute_path(target, path)
    exists = transport.run(
        target=target,
        command=CommandSpec(argv=("test", "-f", absolute)),
    )
    if exists.exit_code == 1:
        return None
    if exists.exit_code != 0:
        raise RetrievalError(f"remote file probe failed for {path.value}")

    digest_result = transport.run(
        target=target,
        command=CommandSpec(argv=("sha256sum", "--", absolute)),
    )
    if digest_result.exit_code != 0:
        raise RetrievalError(f"remote SHA-256 failed for {path.value}")
    fields = digest_result.stdout.strip().split()
    if not fields:
        raise RetrievalError(f"remote SHA-256 output is malformed for {path.value}")
    digest = fields[0].lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise RetrievalError(f"remote SHA-256 output is malformed for {path.value}")

    size_result = transport.run(
        target=target,
        command=CommandSpec(argv=("stat", "-c", "%s", "--", absolute)),
    )
    if size_result.exit_code != 0:
        raise RetrievalError(f"remote size probe failed for {path.value}")
    try:
        size = int(size_result.stdout.strip())
    except ValueError as exc:
        raise RetrievalError(f"remote size is not an integer for {path.value}") from exc
    if size < 0:
        raise RetrievalError(f"remote size must not be negative for {path.value}")
    return digest, size


def _local_output_path(root: Path, output_directory: Path, expected: ExpectedOutput) -> Path:
    relative = PurePosixPath(expected.relative_path)
    destination = (output_directory / Path(*relative.parts)).resolve()
    if not destination.is_relative_to(root):
        raise RetrievalError("local retrieval path resolves outside project_root")
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination


def _ensure_local_copy(
    *,
    target: ExecutionTargetProfile,
    transport: TransportAdapter,
    remote_path: TargetRelativePath,
    destination: Path,
    expected_sha: str,
    expected_size: int,
) -> None:
    if destination.exists():
        if not destination.is_file():
            raise RetrievalError("local output destination exists but is not a file")
        _verify_local_file(destination, expected_sha, expected_size)
        return

    temporary = destination.with_name(destination.name + ".part")
    temporary.unlink(missing_ok=True)
    try:
        transport.download(target=target, source=remote_path, local_path=temporary)
        _verify_local_file(temporary, expected_sha, expected_size)
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _verify_local_file(path: Path, expected_sha: str, expected_size: int) -> None:
    if not path.is_file():
        raise RetrievalError("downloaded output is missing")
    if path.stat().st_size != expected_size:
        raise RetrievalError("downloaded output size does not match remote observation")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected_sha:
        raise RetrievalError("downloaded output SHA-256 does not match remote observation")


def _delete_remote_file(
    *,
    target: ExecutionTargetProfile,
    transport: TransportAdapter,
    path: TargetRelativePath,
    expected_sha: str,
    expected_size: int,
) -> None:
    current = _inspect_remote_file(target=target, transport=transport, path=path)
    if current is None:
        raise RetrievalError(f"remote file disappeared before retention action: {path.value}")
    if current != (expected_sha, expected_size):
        raise RetrievalError(f"remote file changed before retention action: {path.value}")

    absolute = remote_absolute_path(target, path)
    removed = transport.run(
        target=target,
        command=CommandSpec(argv=("rm", "--", absolute)),
    )
    if removed.exit_code != 0:
        raise RetrievalError(f"remote retention release failed for {path.value}")
    exists = transport.run(
        target=target,
        command=CommandSpec(argv=("test", "-f", absolute)),
    )
    if exists.exit_code == 0:
        raise RetrievalError(f"remote file still exists after release: {path.value}")
    if exists.exit_code != 1:
        raise RetrievalError(f"remote release verification failed for {path.value}")


def _persist_retrieval_manifest(
    *,
    root: Path,
    attempt: ExecutionAttempt,
    manifest: RetrievalManifest,
) -> Artifact:
    artifact_directory = root / "artifacts" / "execution" / str(attempt.id)
    if not artifact_directory.is_dir():
        raise RetrievalError("ExecutionAttempt provenance directory is missing")
    stamp = manifest.retrieved_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = artifact_directory / f"retrieval-{stamp}.json"
    if path.exists():
        raise RetrievalError("retrieval manifest Artifact already exists")
    text = manifest.text
    path.write_text(text, encoding="utf-8")
    body = text.encode("utf-8")
    return Artifact(
        artifact_type=ArtifactType.RETRIEVAL_MANIFEST,
        producer=ExecutionAttemptProducerRef(attempt.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=path.relative_to(root).as_posix(),
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
    )


__all__ = [
    "RemoteRetrievalPackage",
    "RetrievalError",
    "RetrievalFileRecord",
    "RetrievalManifest",
    "retrieve_remote_outputs",
]
