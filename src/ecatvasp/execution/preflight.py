"""Typed real-site preflight over the existing SSH/execution authority.

Preflight produces transient operational evidence only. It never creates or mutates a
Calculation, ExecutionAttempt, RemoteJob, ProjectStore entity, or scientific convergence state.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Protocol, runtime_checkable

from ecatvasp.domain import SchedulerType
from ecatvasp.execution.adapters import CommandResult, CommandSpec, TransportAdapter
from ecatvasp.execution.site_profile import SiteProfile
from ecatvasp.execution.ssh import OpenSshTransport
from ecatvasp.execution.targets import ExecutionTargetProfile, TransportKind


class PreflightStatus(StrEnum):
    READY = "READY"
    BLOCKED = "BLOCKED"
    WARNING = "WARNING"
    UNAVAILABLE_OPTIONAL = "UNAVAILABLE_OPTIONAL"


class ReasonCode(StrEnum):
    SSH_UNAVAILABLE = "SSH_UNAVAILABLE"
    REMOTE_ROOT_MISSING = "REMOTE_ROOT_MISSING"
    REMOTE_ROOT_NOT_READABLE = "REMOTE_ROOT_NOT_READABLE"
    REMOTE_ROOT_NOT_WRITABLE = "REMOTE_ROOT_NOT_WRITABLE"
    SCHEDULER_NOT_FOUND = "SCHEDULER_NOT_FOUND"
    SCHEDULER_UNSUPPORTED = "SCHEDULER_UNSUPPORTED"
    MODULE_ENVIRONMENT_UNVERIFIED = "MODULE_ENVIRONMENT_UNVERIFIED"
    MODULE_ENVIRONMENT_UNAVAILABLE = "MODULE_ENVIRONMENT_UNAVAILABLE"
    EXECUTABLE_IDENTITY_UNRESOLVED = "EXECUTABLE_IDENTITY_UNRESOLVED"
    VASP_NOT_FOUND = "VASP_NOT_FOUND"
    MPI_LAUNCHER_NOT_FOUND = "MPI_LAUNCHER_NOT_FOUND"
    POTCAR_UNAVAILABLE = "POTCAR_UNAVAILABLE"
    OPTIONAL_TOOL_MISSING = "OPTIONAL_TOOL_MISSING"
    INVALID_SITE_PROFILE = "INVALID_SITE_PROFILE"


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    check_name: str
    status: PreflightStatus
    reason_code: ReasonCode | None
    message: str
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PreflightReport:
    site_id: str
    profile_hash: str
    status: PreflightStatus
    checks: tuple[PreflightCheck, ...]
    timestamp: str


ExecutableResolver = Callable[[str], str | None]
Clock = Callable[[], datetime]


@runtime_checkable
class ModuleEnvironmentProbeAdapter(Protocol):
    """Optional transport capability for one bounded configured-module environment probe."""

    def probe_module_environment(
        self,
        *,
        target: ExecutionTargetProfile,
        command: str | None = None,
    ) -> CommandResult: ...


class PreflightService:
    """Run fail-closed site diagnostics without creating execution or scientific state."""

    def __init__(
        self,
        *,
        transport: TransportAdapter | None = None,
        executable_resolver: ExecutableResolver | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._transport = OpenSshTransport() if transport is None else transport
        self._executable_resolver = (
            shutil.which if executable_resolver is None else executable_resolver
        )
        self._clock = _utc_now if clock is None else clock

    def run(self, site_profile: SiteProfile) -> PreflightReport:
        checks: list[PreflightCheck] = []
        issues = site_profile.validate()
        if issues:
            checks.append(
                PreflightCheck(
                    "profile_validation",
                    PreflightStatus.BLOCKED,
                    ReasonCode.INVALID_SITE_PROFILE,
                    "site profile is invalid",
                    tuple(sorted(issues)),
                )
            )
            return self._report(site_profile, checks)

        checks.append(
            PreflightCheck(
                "profile_validation",
                PreflightStatus.READY,
                None,
                "site profile matches the frozen execution-target boundary",
            )
        )

        if self._transport.transport_kind is not TransportKind.SSH:
            raise ValueError("real-site preflight requires an SSH TransportAdapter")

        missing_local = tuple(
            command
            for command in ("ssh", "scp")
            if self._executable_resolver(command) is None
        )
        if missing_local:
            checks.append(
                PreflightCheck(
                    "local_openssh",
                    PreflightStatus.BLOCKED,
                    ReasonCode.SSH_UNAVAILABLE,
                    "required system OpenSSH client commands are unavailable",
                    tuple(f"missing={command}" for command in missing_local),
                )
            )
            return self._report(site_profile, checks)

        checks.append(
            PreflightCheck(
                "local_openssh",
                PreflightStatus.READY,
                None,
                "system OpenSSH client commands are available",
                ("ssh=available", "scp=available"),
            )
        )

        target = site_profile.to_execution_target()
        reachability = self._remote(target, ("true",))
        if reachability.exit_code != 0:
            checks.append(
                PreflightCheck(
                    "ssh_reachability",
                    PreflightStatus.BLOCKED,
                    ReasonCode.SSH_UNAVAILABLE,
                    "non-interactive SSH reachability failed",
                    (f"exit_code={reachability.exit_code}",),
                )
            )
            return self._report(site_profile, checks)

        checks.append(
            PreflightCheck(
                "ssh_reachability",
                PreflightStatus.READY,
                None,
                "non-interactive SSH reachability succeeded",
            )
        )

        root = site_profile.remote_root
        if not self._remote_test(target, "-d", root):
            checks.append(
                PreflightCheck(
                    "remote_root_exists",
                    PreflightStatus.BLOCKED,
                    ReasonCode.REMOTE_ROOT_MISSING,
                    "configured remote work root does not exist",
                )
            )
        else:
            checks.append(
                PreflightCheck(
                    "remote_root_exists",
                    PreflightStatus.READY,
                    None,
                    "configured remote work root exists",
                )
            )
            checks.append(
                self._access_check(
                    target=target,
                    check_name="remote_root_readable",
                    flag="-r",
                    path=root,
                    reason=ReasonCode.REMOTE_ROOT_NOT_READABLE,
                    success_message="configured remote work root is readable",
                    failure_message="configured remote work root is not readable",
                )
            )
            checks.append(
                self._access_check(
                    target=target,
                    check_name="remote_root_writable",
                    flag="-w",
                    path=root,
                    reason=ReasonCode.REMOTE_ROOT_NOT_WRITABLE,
                    success_message="configured remote work root is writable",
                    failure_message="configured remote work root is not writable",
                )
            )

        checks.append(self._scheduler_check(target, site_profile.scheduler_type))

        module_status: PreflightStatus | None = None
        if site_profile.module_loads:
            module_check = self._module_environment_check(target)
            checks.append(module_check)
            module_status = module_check.status

        module_environment = module_status is PreflightStatus.READY
        module_blocked = module_status is PreflightStatus.BLOCKED
        if not module_blocked:
            checks.append(
                self._command_check(
                    target=target,
                    check_name="vasp_executable",
                    command=site_profile.vasp_executable,
                    missing_reason=ReasonCode.VASP_NOT_FOUND,
                    optional=False,
                    module_environment=module_environment,
                )
            )

            if site_profile.mpi_launcher is not None:
                checks.append(
                    self._command_check(
                        target=target,
                        check_name="mpi_launcher",
                        command=site_profile.mpi_launcher,
                        missing_reason=ReasonCode.MPI_LAUNCHER_NOT_FOUND,
                        optional=False,
                        module_environment=module_environment,
                    )
                )

        potcar_root = site_profile.potcar_root
        potcar_ready = self._remote_test(target, "-d", potcar_root) and self._remote_test(
            target, "-r", potcar_root
        )
        checks.append(
            PreflightCheck(
                "potcar_library",
                PreflightStatus.READY if potcar_ready else PreflightStatus.BLOCKED,
                None if potcar_ready else ReasonCode.POTCAR_UNAVAILABLE,
                (
                    "configured remote POTCAR library root is available"
                    if potcar_ready
                    else "configured remote POTCAR library root is unavailable"
                ),
                (
                    f"resolver_id={site_profile.potcar_resolver_id}",
                    f"family={site_profile.potcar_family}",
                ),
            )
        )

        if not module_blocked:
            for check_name, command in (
                ("bader", site_profile.bader_executable),
                ("lobster", site_profile.lobster_executable),
            ):
                if command is not None:
                    checks.append(
                        self._command_check(
                            target=target,
                            check_name=check_name,
                            command=command,
                            missing_reason=ReasonCode.OPTIONAL_TOOL_MISSING,
                            optional=True,
                            module_environment=module_environment,
                        )
                    )

        return self._report(site_profile, checks)

    def _remote(
        self,
        target: ExecutionTargetProfile,
        argv: tuple[str, ...],
    ) -> CommandResult:
        try:
            return self._transport.run(target=target, command=CommandSpec(argv=argv))
        except RuntimeError:
            return CommandResult(exit_code=255)

    def _remote_test(
        self,
        target: ExecutionTargetProfile,
        flag: str,
        path: str,
    ) -> bool:
        return self._remote(target, ("test", flag, path)).exit_code == 0

    def _access_check(
        self,
        *,
        target: ExecutionTargetProfile,
        check_name: str,
        flag: str,
        path: str,
        reason: ReasonCode,
        success_message: str,
        failure_message: str,
    ) -> PreflightCheck:
        ready = self._remote_test(target, flag, path)
        return PreflightCheck(
            check_name,
            PreflightStatus.READY if ready else PreflightStatus.BLOCKED,
            None if ready else reason,
            success_message if ready else failure_message,
        )

    def _scheduler_check(
        self,
        target: ExecutionTargetProfile,
        scheduler: SchedulerType,
    ) -> PreflightCheck:
        if scheduler is not SchedulerType.SLURM:
            return PreflightCheck(
                "scheduler",
                PreflightStatus.BLOCKED,
                ReasonCode.SCHEDULER_UNSUPPORTED,
                "configured scheduler is not supported by the current real-site preflight",
                (f"scheduler={scheduler.value}",),
            )
        commands = ("sbatch", "squeue", "sacct", "scancel")
        missing = tuple(
            command
            for command in commands
            if not self._command_exists(target, command)
        )
        return PreflightCheck(
            "scheduler",
            PreflightStatus.READY if not missing else PreflightStatus.BLOCKED,
            None if not missing else ReasonCode.SCHEDULER_NOT_FOUND,
            (
                "required Slurm commands are available"
                if not missing
                else "one or more required Slurm commands are unavailable"
            ),
            tuple(f"missing={command}" for command in missing),
        )

    def _module_environment_check(
        self,
        target: ExecutionTargetProfile,
    ) -> PreflightCheck:
        modules = tuple(f"module={item}" for item in target.module_loads)
        if not isinstance(self._transport, ModuleEnvironmentProbeAdapter):
            return PreflightCheck(
                "module_environment",
                PreflightStatus.WARNING,
                ReasonCode.MODULE_ENVIRONMENT_UNVERIFIED,
                "configured modules cannot be verified by the active transport capability",
                modules,
            )
        try:
            result = self._transport.probe_module_environment(target=target)
        except RuntimeError:
            result = CommandResult(exit_code=255)
        ready = result.exit_code == 0
        return PreflightCheck(
            "module_environment",
            PreflightStatus.READY if ready else PreflightStatus.BLOCKED,
            None if ready else ReasonCode.MODULE_ENVIRONMENT_UNAVAILABLE,
            (
                "configured modules load successfully in the bounded login-shell probe"
                if ready
                else "configured modules failed the bounded login-shell probe"
            ),
            modules + (() if ready else (f"exit_code={result.exit_code}",)),
        )

    def _command_exists(self, target: ExecutionTargetProfile, command: str) -> bool:
        return self._remote(target, ("command", "-v", command)).exit_code == 0

    def _command_probe(
        self,
        *,
        target: ExecutionTargetProfile,
        command: str,
        module_environment: bool,
    ) -> CommandResult:
        if not module_environment:
            return self._remote(target, ("command", "-v", command))
        if not isinstance(self._transport, ModuleEnvironmentProbeAdapter):
            return CommandResult(exit_code=255)
        try:
            return self._transport.probe_module_environment(target=target, command=command)
        except RuntimeError:
            return CommandResult(exit_code=255)

    def _command_check(
        self,
        *,
        target: ExecutionTargetProfile,
        check_name: str,
        command: str,
        missing_reason: ReasonCode,
        optional: bool,
        module_environment: bool = False,
    ) -> PreflightCheck:
        result = self._command_probe(
            target=target,
            command=command,
            module_environment=module_environment,
        )
        environment_evidence = (
            ("environment=configured_modules",) if module_environment else ()
        )
        if result.exit_code == 0:
            resolved_path = _resolved_executable_path(result.stdout)
            if resolved_path is None:
                return PreflightCheck(
                    check_name,
                    (
                        PreflightStatus.UNAVAILABLE_OPTIONAL
                        if optional
                        else PreflightStatus.BLOCKED
                    ),
                    ReasonCode.EXECUTABLE_IDENTITY_UNRESOLVED,
                    (
                        "optional executable command did not resolve to one normalized absolute "
                        "POSIX path"
                        if optional
                        else "required executable command did not resolve to one normalized "
                        "absolute POSIX path"
                    ),
                    (f"command={command}", *environment_evidence),
                )
            return PreflightCheck(
                check_name,
                PreflightStatus.READY,
                None,
                "configured executable command is available with a resolved path identity",
                (
                    f"command={command}",
                    f"resolved_path={resolved_path}",
                    *environment_evidence,
                ),
            )
        return PreflightCheck(
            check_name,
            (
                PreflightStatus.UNAVAILABLE_OPTIONAL
                if optional
                else PreflightStatus.BLOCKED
            ),
            missing_reason,
            (
                "optional executable command is unavailable"
                if optional
                else "required executable command is unavailable"
            ),
            (f"command={command}", *environment_evidence),
        )

    def _report(
        self,
        site_profile: SiteProfile,
        checks: list[PreflightCheck],
    ) -> PreflightReport:
        statuses = {item.status for item in checks}
        if PreflightStatus.BLOCKED in statuses:
            status = PreflightStatus.BLOCKED
        elif PreflightStatus.WARNING in statuses:
            status = PreflightStatus.WARNING
        else:
            status = PreflightStatus.READY
        observed = self._clock()
        if observed.tzinfo is None:
            raise ValueError("preflight clock must return an offset-aware datetime")
        return PreflightReport(
            site_id=site_profile.site_id,
            profile_hash=site_profile.profile_hash,
            status=status,
            checks=tuple(checks),
            timestamp=observed.astimezone(UTC).isoformat(),
        )


def _resolved_executable_path(stdout: str) -> str | None:
    """Return one normalized absolute POSIX path from untrusted ``command -v`` output."""

    lines = tuple(line.strip() for line in stdout.splitlines() if line.strip())
    if len(lines) != 1:
        return None
    candidate = lines[0]
    if any(character.isspace() for character in candidate) or "\x00" in candidate:
        return None
    path = PurePosixPath(candidate)
    if not path.is_absolute() or ".." in path.parts or path.as_posix() != candidate:
        return None
    return candidate


def _utc_now() -> datetime:
    return datetime.now(UTC)
