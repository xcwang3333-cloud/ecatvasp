"""Concrete scheduler-free local executor for v0.4 Block 3."""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from ecatvasp.domain import (
    Artifact,
    ArtifactAvailability,
    ArtifactType,
    ExecutionAttempt,
    ExecutionAttemptProducerRef,
    ExecutionAttemptStatus,
    RetrievalPolicy,
)
from ecatvasp.execution.adapters import CommandResult, CommandSpec
from ecatvasp.execution.runtime import LocalRuntimePackage, RuntimeMaterializationError


class LocalExecutionError(RuntimeError):
    """Raised when one prepared local runtime cannot be executed safely."""


@dataclass(frozen=True, slots=True)
class LocalExecutionResult:
    """Process-level local execution result without assigning scientific success."""

    attempt: ExecutionAttempt
    command: CommandSpec
    launched: bool
    command_result: CommandResult | None
    artifacts: tuple[Artifact, ...]


class LocalExecutor:
    """Execute one prepared scheduler-free local VASP runtime with ``shell=False``."""

    def execute(self, package: LocalRuntimePackage) -> LocalExecutionResult:
        if package.attempt.status is not ExecutionAttemptStatus.STAGING:
            raise LocalExecutionError(
                "LocalExecutor requires a STAGING ExecutionAttempt"
            )
        if package.target.launcher is not None:
            raise LocalExecutionError(
                "Block 3 LocalExecutor does not synthesize launcher command lines"
            )
        settings = package.plan.execution_settings
        if settings.mpi_ranks not in {None, 1}:
            raise LocalExecutionError(
                "Block 3 LocalExecutor supports only one MPI rank"
            )

        command = CommandSpec(argv=(package.target.vasp_executable,))
        artifact_directory = package.project_root.joinpath(
            *package.artifact_directory_relative.split("/")
        )
        stdout_path = artifact_directory / "stdout.txt"
        stderr_path = artifact_directory / "stderr.txt"
        if stdout_path.exists() or stderr_path.exists():
            raise LocalExecutionError(
                "LocalExecutor refuses to overwrite existing process logs"
            )

        started_at = datetime.now(UTC)
        environment = os.environ.copy()
        if settings.omp_threads is not None:
            environment["OMP_NUM_THREADS"] = str(settings.omp_threads)

        try:
            completed = subprocess.run(
                command.argv,
                cwd=package.run_directory,
                env=environment,
                capture_output=True,
                check=False,
                shell=False,
            )
        except OSError as exc:
            finished_at = datetime.now(UTC)
            stderr = (
                f"{type(exc).__name__}: {exc}\n"
            ).encode("utf-8", errors="replace")
            stdout = b""
            _write_log(stdout_path, stdout)
            _write_log(stderr_path, stderr)
            artifacts = package.artifacts + (
                _log_artifact(
                    package=package,
                    path=stdout_path,
                    artifact_type=ArtifactType.STDOUT,
                ),
                _log_artifact(
                    package=package,
                    path=stderr_path,
                    artifact_type=ArtifactType.STDERR,
                ),
            )
            failed = replace(
                package.attempt,
                status=ExecutionAttemptStatus.FAILED,
                started_at=started_at,
                finished_at=finished_at,
            )
            return LocalExecutionResult(
                attempt=failed,
                command=command,
                launched=False,
                command_result=None,
                artifacts=artifacts,
            )

        finished_at = datetime.now(UTC)
        _write_log(stdout_path, completed.stdout)
        _write_log(stderr_path, completed.stderr)
        artifacts = package.artifacts + (
            _log_artifact(
                package=package,
                path=stdout_path,
                artifact_type=ArtifactType.STDOUT,
            ),
            _log_artifact(
                package=package,
                path=stderr_path,
                artifact_type=ArtifactType.STDERR,
            ),
        )
        exited = replace(
            package.attempt,
            status=ExecutionAttemptStatus.EXITED,
            started_at=started_at,
            finished_at=finished_at,
        )
        return LocalExecutionResult(
            attempt=exited,
            command=command,
            launched=True,
            command_result=CommandResult(
                exit_code=completed.returncode,
                stdout=completed.stdout.decode("utf-8", errors="replace"),
                stderr=completed.stderr.decode("utf-8", errors="replace"),
            ),
            artifacts=artifacts,
        )


def _write_log(path: Path, body: bytes) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(body)
    except FileExistsError as exc:
        raise LocalExecutionError(
            f"LocalExecutor refuses to overwrite {path.name}"
        ) from exc


def _log_artifact(
    *,
    package: LocalRuntimePackage,
    path: Path,
    artifact_type: ArtifactType,
) -> Artifact:
    try:
        relative = path.relative_to(package.project_root)
    except ValueError as exc:
        raise RuntimeMaterializationError(
            "local execution log escaped project_root"
        ) from exc
    body = path.read_bytes()
    return Artifact(
        artifact_type=artifact_type,
        producer=ExecutionAttemptProducerRef(package.attempt.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=relative.as_posix(),
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
    )
