"""Transport and scheduler adapter interfaces for v0.4 execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol, runtime_checkable

from ecatvasp.domain import SchedulerState, SchedulerType
from ecatvasp.execution.targets import ExecutionTargetProfile, TransportKind


@dataclass(frozen=True, slots=True)
class TargetRelativePath:
    """Path confined beneath one execution target's configured work root."""

    value: str

    def __post_init__(self) -> None:
        if any(character in self.value for character in ("\n", "\r", "\x00")):
            raise ValueError("target-relative paths must not contain control characters")
        path = PurePosixPath(self.value)
        if path.is_absolute() or path == PurePosixPath("."):
            raise ValueError("target-relative paths must be non-empty relative POSIX paths")
        if ".." in path.parts:
            raise ValueError("target-relative paths must not traverse parent directories")
        object.__setattr__(self, "value", path.as_posix())


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """Argument-vector command contract; adapters must not reinterpret it as shell text."""

    argv: tuple[str, ...]
    cwd: TargetRelativePath | None = None

    def __post_init__(self) -> None:
        if not self.argv:
            raise ValueError("CommandSpec requires at least one argv element")
        for argument in self.argv:
            if not argument or any(character in argument for character in ("\n", "\r", "\x00")):
                raise ValueError(
                    "command arguments must be non-empty and free of control characters"
                )


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Transport command result without assigning scientific meaning to the exit code."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True, slots=True)
class SchedulerSubmission:
    """Scheduler identity returned after one successful submission operation."""

    scheduler_job_id: str
    raw_stdout: str = ""
    raw_stderr: str = ""

    def __post_init__(self) -> None:
        _require_scheduler_job_id(self.scheduler_job_id)


@dataclass(frozen=True, slots=True)
class SchedulerObservation:
    """One normalized scheduler observation plus the source scheduler state text."""

    scheduler_job_id: str
    state: SchedulerState
    raw_state: str

    def __post_init__(self) -> None:
        _require_scheduler_job_id(self.scheduler_job_id)
        if not self.raw_state.strip():
            raise ValueError("raw_state must not be blank")


@runtime_checkable
class TransportAdapter(Protocol):
    """Filesystem/command transport boundary implemented by Local and SSH backends."""

    @property
    def transport_kind(self) -> TransportKind: ...

    def ensure_directory(
        self,
        *,
        target: ExecutionTargetProfile,
        path: TargetRelativePath,
    ) -> None: ...

    def upload(
        self,
        *,
        target: ExecutionTargetProfile,
        local_path: Path,
        destination: TargetRelativePath,
    ) -> None: ...

    def download(
        self,
        *,
        target: ExecutionTargetProfile,
        source: TargetRelativePath,
        local_path: Path,
    ) -> None: ...

    def run(
        self,
        *,
        target: ExecutionTargetProfile,
        command: CommandSpec,
    ) -> CommandResult: ...


@runtime_checkable
class SchedulerAdapter(Protocol):
    """Scheduler control boundary; rendering/resource resolution are deferred to later blocks."""

    @property
    def scheduler_type(self) -> SchedulerType: ...

    def submit(
        self,
        *,
        target: ExecutionTargetProfile,
        script: TargetRelativePath,
    ) -> SchedulerSubmission: ...

    def query(
        self,
        *,
        target: ExecutionTargetProfile,
        scheduler_job_id: str,
    ) -> SchedulerObservation: ...

    def cancel(
        self,
        *,
        target: ExecutionTargetProfile,
        scheduler_job_id: str,
    ) -> SchedulerObservation: ...


def validate_adapter_target(
    *,
    target: ExecutionTargetProfile,
    transport: TransportAdapter,
    scheduler: SchedulerAdapter | None,
) -> None:
    """Fail closed when adapter families do not match one execution target profile."""

    if transport.transport_kind is not target.transport:
        raise ValueError("transport adapter does not match ExecutionTargetProfile")
    if target.scheduler is None:
        if scheduler is not None:
            raise ValueError("scheduler adapter is invalid for a scheduler-free execution target")
        return
    if scheduler is None:
        raise ValueError("execution target requires a scheduler adapter")
    if scheduler.scheduler_type is not target.scheduler:
        raise ValueError("scheduler adapter does not match ExecutionTargetProfile")


def _require_scheduler_job_id(value: str) -> None:
    if not value.strip() or any(character in value for character in ("\n", "\r", "\x00")):
        raise ValueError("scheduler_job_id must be non-blank and free of control characters")
