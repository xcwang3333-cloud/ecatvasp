from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from ecatvasp.domain import SchedulerType
from ecatvasp.execution.adapters import CommandResult, CommandSpec, TargetRelativePath
from ecatvasp.execution.preflight import (
    PreflightCheck,
    PreflightReport,
    PreflightService,
    PreflightStatus,
    ReasonCode,
)
from ecatvasp.execution.site_profile import SiteProfile
from ecatvasp.execution.targets import ExecutionTargetProfile, TransportKind


class FakeTransport:
    transport_kind = TransportKind.SSH

    def __init__(
        self,
        *,
        reachable: bool = True,
        remote_root_exists: bool = True,
        remote_root_readable: bool = True,
        remote_root_writable: bool = True,
        potcar_ready: bool = True,
        available_commands: set[str] | None = None,
    ) -> None:
        self.reachable = reachable
        self.remote_root_exists = remote_root_exists
        self.remote_root_readable = remote_root_readable
        self.remote_root_writable = remote_root_writable
        self.potcar_ready = potcar_ready
        self.available_commands = (
            {"sbatch", "squeue", "sacct", "scancel", "vasp_std", "srun"}
            if available_commands is None
            else set(available_commands)
        )
        self.commands: list[tuple[str, ...]] = []

    def ensure_directory(
        self,
        *,
        target: ExecutionTargetProfile,
        path: TargetRelativePath,
    ) -> None:
        raise AssertionError("preflight must not create remote directories")

    def upload(
        self,
        *,
        target: ExecutionTargetProfile,
        local_path: Path,
        destination: TargetRelativePath,
    ) -> None:
        raise AssertionError("preflight must not upload files")

    def download(
        self,
        *,
        target: ExecutionTargetProfile,
        source: TargetRelativePath,
        local_path: Path,
    ) -> None:
        raise AssertionError("preflight must not download files")

    def run(
        self,
        *,
        target: ExecutionTargetProfile,
        command: CommandSpec,
    ) -> CommandResult:
        argv = command.argv
        self.commands.append(argv)
        if argv == ("true",):
            return CommandResult(exit_code=0 if self.reachable else 255)
        if argv[:2] == ("command", "-v"):
            return CommandResult(exit_code=0 if argv[2] in self.available_commands else 1)
        if argv[:2] == ("test", "-d"):
            if argv[2] == "/scratch/ecatvasp":
                return CommandResult(exit_code=0 if self.remote_root_exists else 1)
            if argv[2] == "/apps/vasp/potpaw_PBE.54":
                return CommandResult(exit_code=0 if self.potcar_ready else 1)
        if argv[:2] == ("test", "-r"):
            if argv[2] == "/scratch/ecatvasp":
                return CommandResult(exit_code=0 if self.remote_root_readable else 1)
            if argv[2] == "/apps/vasp/potpaw_PBE.54":
                return CommandResult(exit_code=0 if self.potcar_ready else 1)
        if argv[:2] == ("test", "-w") and argv[2] == "/scratch/ecatvasp":
            return CommandResult(exit_code=0 if self.remote_root_writable else 1)
        return CommandResult(exit_code=1)


def _profile(**overrides: object) -> SiteProfile:
    values: dict[str, object] = {
        "site_id": "cluster",
        "name": "Cluster",
        "host_alias": "hpc.example",
        "remote_root": "/scratch/ecatvasp",
        "potcar_resolver_id": "vasp-pbe",
        "potcar_family": "PBE_54",
        "potcar_root": "/apps/vasp/potpaw_PBE.54",
        "scheduler_type": SchedulerType.SLURM,
        "vasp_executable": "vasp_std",
        "mpi_launcher": "srun",
        "module_loads": (),
        "bader_executable": "bader",
        "lobster_executable": "lobster",
    }
    values.update(overrides)
    return SiteProfile(**values)  # type: ignore[arg-type]


def _service(transport: FakeTransport, *, local_ssh: bool = True) -> PreflightService:
    def resolve(command: str) -> str | None:
        if local_ssh and command in {"ssh", "scp"}:
            return f"/usr/bin/{command}"
        return None

    return PreflightService(
        transport=transport,
        executable_resolver=resolve,
        clock=lambda: datetime(2026, 9, 10, 14, 30, tzinfo=UTC),
    )


def _check(report_name: str, report: PreflightReport) -> PreflightCheck:
    return next(item for item in report.checks if item.check_name == report_name)


def test_site_profile_resolves_existing_execution_authorities() -> None:
    profile = _profile(module_loads=("vasp/6.4",))

    target = profile.to_execution_target()
    potcars = profile.to_remote_potcar_library()

    assert profile.validate() == []
    assert target.transport is TransportKind.SSH
    assert target.host_alias == profile.host_alias
    assert target.remote_work_root == profile.remote_root
    assert target.scheduler is SchedulerType.SLURM
    assert target.vasp_executable == "vasp_std"
    assert target.launcher == "srun"
    assert target.module_loads == ("vasp/6.4",)
    assert potcars.resolver_id == "vasp-pbe"
    assert potcars.family == "PBE_54"
    assert potcars.root == "/apps/vasp/potpaw_PBE.54"


def test_site_profile_hash_changes_only_with_operational_configuration() -> None:
    profile = _profile()

    assert profile.profile_hash == replace(profile, name="Display only").profile_hash
    assert profile.profile_hash != replace(profile, vasp_executable="vasp_gam").profile_hash
    assert profile.profile_hash != replace(profile, remote_root="/scratch/ecatvasp-2").profile_hash


def test_real_preflight_ready_core_and_optional_tools_do_not_block() -> None:
    transport = FakeTransport()
    profile = _profile()

    report = _service(transport).run(profile)

    assert report.status is PreflightStatus.READY
    assert report.profile_hash == profile.profile_hash
    assert report.timestamp == "2026-09-10T14:30:00+00:00"
    assert _check("scheduler", report).status is PreflightStatus.READY
    assert _check("vasp_executable", report).status is PreflightStatus.READY
    assert _check("mpi_launcher", report).status is PreflightStatus.READY
    assert _check("potcar_library", report).status is PreflightStatus.READY
    assert _check("bader", report).status is PreflightStatus.UNAVAILABLE_OPTIONAL
    assert _check("lobster", report).status is PreflightStatus.UNAVAILABLE_OPTIONAL


def test_preflight_blocks_before_remote_probe_when_local_openssh_is_missing() -> None:
    transport = FakeTransport()

    report = _service(transport, local_ssh=False).run(_profile())

    assert report.status is PreflightStatus.BLOCKED
    assert _check("local_openssh", report).reason_code is ReasonCode.SSH_UNAVAILABLE
    assert transport.commands == []


def test_preflight_blocks_on_noninteractive_ssh_failure() -> None:
    report = _service(FakeTransport(reachable=False)).run(_profile())

    assert report.status is PreflightStatus.BLOCKED
    assert _check("ssh_reachability", report).reason_code is ReasonCode.SSH_UNAVAILABLE


def test_preflight_blocks_on_remote_root_or_vasp_failure() -> None:
    root_report = _service(FakeTransport(remote_root_exists=False)).run(_profile())
    commands = {"sbatch", "squeue", "sacct", "scancel", "srun"}
    vasp_report = _service(FakeTransport(available_commands=commands)).run(_profile())

    assert root_report.status is PreflightStatus.BLOCKED
    assert _check("remote_root_exists", root_report).reason_code is ReasonCode.REMOTE_ROOT_MISSING
    assert vasp_report.status is PreflightStatus.BLOCKED
    assert _check("vasp_executable", vasp_report).reason_code is ReasonCode.VASP_NOT_FOUND


def test_preflight_blocks_on_potcar_library_unavailability() -> None:
    report = _service(FakeTransport(potcar_ready=False)).run(_profile())

    assert report.status is PreflightStatus.BLOCKED
    assert _check("potcar_library", report).reason_code is ReasonCode.POTCAR_UNAVAILABLE


def test_preflight_reports_module_environment_as_unverified_warning() -> None:
    report = _service(FakeTransport()).run(_profile(module_loads=("vasp/6.4",)))

    assert report.status is PreflightStatus.WARNING
    assert (
        _check("module_environment", report).reason_code
        is ReasonCode.MODULE_ENVIRONMENT_UNVERIFIED
    )


def test_preflight_rejects_unsupported_scheduler_without_guessing() -> None:
    report = _service(FakeTransport()).run(_profile(scheduler_type=SchedulerType.PBS))

    assert report.status is PreflightStatus.BLOCKED
    assert _check("scheduler", report).reason_code is ReasonCode.SCHEDULER_UNSUPPORTED


def test_preflight_invalid_profile_returns_only_local_validation_evidence() -> None:
    transport = FakeTransport()
    profile = replace(_profile(), host_alias="", remote_root="", potcar_root="")

    report = _service(transport).run(profile)

    assert report.status is PreflightStatus.BLOCKED
    assert set(profile.validate()) == {"host_alias", "remote_root", "potcar_root"}
    assert tuple(item.check_name for item in report.checks) == ("profile_validation",)
    assert transport.commands == []
