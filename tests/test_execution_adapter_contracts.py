from __future__ import annotations

from pathlib import Path

import pytest

from ecatvasp.domain import SchedulerState, SchedulerType
from ecatvasp.execution import (
    CommandResult,
    CommandSpec,
    ExecutionTargetProfile,
    SchedulerAdapter,
    SchedulerObservation,
    SchedulerSubmission,
    SshSecurityPolicy,
    TargetRelativePath,
    TransportAdapter,
    TransportKind,
    validate_adapter_target,
)


class _FakeTransport:
    def __init__(self, transport_kind: TransportKind) -> None:
        self._transport_kind = transport_kind

    @property
    def transport_kind(self) -> TransportKind:
        return self._transport_kind

    def ensure_directory(
        self,
        *,
        target: ExecutionTargetProfile,
        path: TargetRelativePath,
    ) -> None:
        _ = (target, path)

    def upload(
        self,
        *,
        target: ExecutionTargetProfile,
        local_path: Path,
        destination: TargetRelativePath,
    ) -> None:
        _ = (target, local_path, destination)

    def download(
        self,
        *,
        target: ExecutionTargetProfile,
        source: TargetRelativePath,
        local_path: Path,
    ) -> None:
        _ = (target, source, local_path)

    def run(
        self,
        *,
        target: ExecutionTargetProfile,
        command: CommandSpec,
    ) -> CommandResult:
        _ = (target, command)
        return CommandResult(exit_code=0)


class _FakeScheduler:
    def __init__(self, scheduler_type: SchedulerType) -> None:
        self._scheduler_type = scheduler_type

    @property
    def scheduler_type(self) -> SchedulerType:
        return self._scheduler_type

    def submit(
        self,
        *,
        target: ExecutionTargetProfile,
        script: TargetRelativePath,
    ) -> SchedulerSubmission:
        _ = (target, script)
        return SchedulerSubmission("12345")

    def query(
        self,
        *,
        target: ExecutionTargetProfile,
        scheduler_job_id: str,
    ) -> SchedulerObservation:
        _ = target
        return SchedulerObservation(scheduler_job_id, SchedulerState.RUNNING, "RUNNING")

    def cancel(
        self,
        *,
        target: ExecutionTargetProfile,
        scheduler_job_id: str,
    ) -> SchedulerObservation:
        _ = target
        return SchedulerObservation(scheduler_job_id, SchedulerState.CANCELLED, "CANCELLED")


def _ssh_target() -> ExecutionTargetProfile:
    return ExecutionTargetProfile(
        target_id="cluster-a",
        transport=TransportKind.SSH,
        scheduler=SchedulerType.SLURM,
        host_alias="hpc-a",
        remote_work_root="/scratch/xiaochen/ecatvasp",
        potcar_resolver_id="vasp-pbe54-cluster-a",
        ssh_security=SshSecurityPolicy(),
    )


def test_fake_adapters_conform_to_runtime_protocols() -> None:
    transport = _FakeTransport(TransportKind.SSH)
    scheduler = _FakeScheduler(SchedulerType.SLURM)

    assert isinstance(transport, TransportAdapter)
    assert isinstance(scheduler, SchedulerAdapter)
    validate_adapter_target(target=_ssh_target(), transport=transport, scheduler=scheduler)


def test_adapter_target_validation_rejects_family_mismatch() -> None:
    target = _ssh_target()

    with pytest.raises(ValueError, match="transport adapter"):
        validate_adapter_target(
            target=target,
            transport=_FakeTransport(TransportKind.LOCAL),
            scheduler=_FakeScheduler(SchedulerType.SLURM),
        )
    with pytest.raises(ValueError, match="scheduler adapter"):
        validate_adapter_target(
            target=target,
            transport=_FakeTransport(TransportKind.SSH),
            scheduler=_FakeScheduler(SchedulerType.PBS),
        )


def test_local_target_rejects_scheduler_adapter() -> None:
    target = ExecutionTargetProfile(
        target_id="local",
        transport=TransportKind.LOCAL,
        potcar_resolver_id="vasp-pbe54-local",
    )

    with pytest.raises(ValueError, match="scheduler adapter is invalid"):
        validate_adapter_target(
            target=target,
            transport=_FakeTransport(TransportKind.LOCAL),
            scheduler=_FakeScheduler(SchedulerType.SLURM),
        )


def test_target_relative_paths_fail_closed_on_escape() -> None:
    assert TargetRelativePath("attempt-1/INCAR").value == "attempt-1/INCAR"

    with pytest.raises(ValueError, match="relative POSIX"):
        TargetRelativePath("/scratch/attempt-1/INCAR")
    with pytest.raises(ValueError, match="parent"):
        TargetRelativePath("attempt-1/../other/INCAR")


def test_command_spec_is_argv_not_shell_text() -> None:
    command = CommandSpec(("sha256sum", "attempt-1/INCAR"), TargetRelativePath("attempt-1"))

    assert command.argv[0] == "sha256sum"
    with pytest.raises(ValueError, match="control characters"):
        CommandSpec(("echo", "unsafe\ncommand"))
