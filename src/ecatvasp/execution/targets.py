"""Execution-target and security contracts for v0.4 execution adapters."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

from ecatvasp.domain import SchedulerType
from ecatvasp.domain.method import canonical_sha256

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_COMMAND_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_MODULE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+@/-]*$")


class TransportKind(StrEnum):
    """Execution transport family independent from scheduler semantics."""

    LOCAL = "local"
    SSH = "ssh"


@dataclass(frozen=True, slots=True)
class SshSecurityPolicy:
    """Non-negotiable SSH safety boundary for ECatVASP-managed targets.

    Authentication remains delegated to the user's system OpenSSH configuration/agent. ECatVASP
    does not accept passwords, private-key bodies, tokens, or permissive host-key policies here.
    """

    use_system_openssh: bool = True
    strict_host_key_checking: bool = True
    batch_mode: bool = True
    allow_password_prompt: bool = False

    def __post_init__(self) -> None:
        if not self.use_system_openssh:
            raise ValueError("SSH targets must use the system OpenSSH credential boundary")
        if not self.strict_host_key_checking:
            raise ValueError("SSH targets require strict host-key verification")
        if not self.batch_mode:
            raise ValueError("SSH targets require non-interactive batch mode")
        if self.allow_password_prompt:
            raise ValueError("SSH password prompting is outside the ECatVASP credential boundary")


@dataclass(frozen=True, slots=True)
class ExecutionEnvironmentSnapshot:
    """Sanitized execution-target provenance safe to persist with an attempt later."""

    target_id: str
    target_hash: str
    transport: TransportKind
    scheduler: SchedulerType | None
    potcar_resolver_id: str
    vasp_executable: str
    launcher: str | None
    module_loads: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExecutionTargetProfile:
    """User-local execution target configuration; never a scientific-domain entity."""

    target_id: str
    transport: TransportKind
    potcar_resolver_id: str
    scheduler: SchedulerType | None = None
    host_alias: str | None = None
    remote_work_root: str | None = None
    vasp_executable: str = "vasp_std"
    launcher: str | None = None
    module_loads: tuple[str, ...] = ()
    ssh_security: SshSecurityPolicy | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.target_id, "target_id")
        _require_identifier(self.potcar_resolver_id, "potcar_resolver_id")
        _require_command_name(self.vasp_executable, "vasp_executable")
        if self.launcher is not None:
            _require_command_name(self.launcher, "launcher")

        if len(self.module_loads) != len(set(self.module_loads)):
            raise ValueError("module_loads must not contain duplicates")
        for module in self.module_loads:
            if not _MODULE_PATTERN.fullmatch(module):
                raise ValueError("module_loads must contain safe module identifiers")

        if self.transport is TransportKind.LOCAL:
            if self.scheduler is not None:
                raise ValueError("LOCAL execution targets do not attach a remote scheduler")
            if self.host_alias is not None or self.remote_work_root is not None:
                raise ValueError("LOCAL execution targets must not define SSH host paths")
            if self.ssh_security is not None:
                raise ValueError("LOCAL execution targets must not define SSH security policy")
            return

        if self.scheduler is None:
            raise ValueError("SSH execution targets require an explicit scheduler family")
        if self.host_alias is None:
            raise ValueError("SSH execution targets require host_alias")
        _require_identifier(self.host_alias, "host_alias")
        if self.remote_work_root is None:
            raise ValueError("SSH execution targets require remote_work_root")
        _validate_remote_root(self.remote_work_root)
        if self.ssh_security is None:
            raise ValueError("SSH execution targets require an explicit SshSecurityPolicy")

    @property
    def target_hash(self) -> str:
        """Return deterministic execution-target identity outside scientific fingerprints."""

        return canonical_sha256(self)

    def sanitized_environment(self) -> ExecutionEnvironmentSnapshot:
        """Return provenance without hostnames, remote directories, or credential material."""

        return ExecutionEnvironmentSnapshot(
            target_id=self.target_id,
            target_hash=self.target_hash,
            transport=self.transport,
            scheduler=self.scheduler,
            potcar_resolver_id=self.potcar_resolver_id,
            vasp_executable=self.vasp_executable,
            launcher=self.launcher,
            module_loads=self.module_loads,
        )


def _require_identifier(value: str, field_name: str) -> None:
    if not _ID_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} must be a non-blank portable identifier")


def _require_command_name(value: str, field_name: str) -> None:
    if not _COMMAND_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} must be a portable command name, not a path or shell text")


def _validate_remote_root(value: str) -> None:
    if any(character in value for character in ("\n", "\r", "\x00")):
        raise ValueError("remote_work_root must not contain control characters")
    if any(character.isspace() for character in value):
        raise ValueError("remote_work_root must not contain whitespace")
    path = PurePosixPath(value)
    if not path.is_absolute() or path == PurePosixPath("/"):
        raise ValueError("remote_work_root must be an explicit non-root absolute POSIX path")
    if ".." in path.parts:
        raise ValueError("remote_work_root must not traverse parent directories")
