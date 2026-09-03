"""Strict system-OpenSSH transport for v0.4 remote execution staging."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path, PurePosixPath

from ecatvasp.execution.adapters import (
    CommandResult,
    CommandSpec,
    TargetRelativePath,
)
from ecatvasp.execution.targets import ExecutionTargetProfile, TransportKind

_SAFE_REMOTE_ARG = re.compile(r"^[A-Za-z0-9_./+,:=@%=-]+$")
_SAFE_REMOTE_PATH_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+@%=-]*$")


class OpenSshTransportError(RuntimeError):
    """Raised when a system OpenSSH transport operation cannot be performed safely."""


class OpenSshTransport:
    """Concrete SSH transport that delegates credentials to system OpenSSH.

    Remote commands are accepted only as shell-inert argument tokens. OpenSSH itself invokes the
    remote login shell, so Block 4 deliberately rejects whitespace and shell metacharacters rather
    than attempting quoting or interpolation.
    """

    @property
    def transport_kind(self) -> TransportKind:
        return TransportKind.SSH

    def ensure_directory(
        self,
        *,
        target: ExecutionTargetProfile,
        path: TargetRelativePath,
    ) -> None:
        absolute = remote_absolute_path(target, path)
        result = self.run(
            target=target,
            command=CommandSpec(argv=("mkdir", "-p", "--", absolute)),
        )
        _require_success(result, "remote mkdir")

    def upload(
        self,
        *,
        target: ExecutionTargetProfile,
        local_path: Path,
        destination: TargetRelativePath,
    ) -> None:
        _validate_ssh_target(target)
        source = local_path.resolve()
        if not source.is_file():
            raise OpenSshTransportError("upload source must be an existing file")
        host = _host_alias(target)
        remote = remote_absolute_path(target, destination)
        completed = _run_local(
            (
                "scp",
                "-B",
                "-o",
                "StrictHostKeyChecking=yes",
                str(source),
                f"{host}:{remote}",
            )
        )
        if completed.returncode != 0:
            raise OpenSshTransportError(
                "scp upload failed: " + completed.stderr.decode("utf-8", errors="replace")
            )

    def download(
        self,
        *,
        target: ExecutionTargetProfile,
        source: TargetRelativePath,
        local_path: Path,
    ) -> None:
        _validate_ssh_target(target)
        destination = local_path.resolve()
        if destination.exists():
            raise OpenSshTransportError("download destination must not already exist")
        destination.parent.mkdir(parents=True, exist_ok=True)
        host = _host_alias(target)
        remote = remote_absolute_path(target, source)
        completed = _run_local(
            (
                "scp",
                "-B",
                "-o",
                "StrictHostKeyChecking=yes",
                f"{host}:{remote}",
                str(destination),
            )
        )
        if completed.returncode != 0:
            raise OpenSshTransportError(
                "scp download failed: " + completed.stderr.decode("utf-8", errors="replace")
            )

    def run(
        self,
        *,
        target: ExecutionTargetProfile,
        command: CommandSpec,
    ) -> CommandResult:
        _validate_ssh_target(target)
        if command.cwd is not None:
            raise OpenSshTransportError(
                "Block 4 OpenSshTransport requires absolute remote arguments and no cwd"
            )
        for argument in command.argv:
            if not _SAFE_REMOTE_ARG.fullmatch(argument):
                raise OpenSshTransportError(
                    "remote command arguments must be shell-inert literal tokens"
                )
        completed = _run_local((*_ssh_prefix(target), *command.argv))
        return CommandResult(
            exit_code=completed.returncode,
            stdout=completed.stdout.decode("utf-8", errors="replace"),
            stderr=completed.stderr.decode("utf-8", errors="replace"),
        )


def remote_absolute_path(
    target: ExecutionTargetProfile,
    path: TargetRelativePath,
) -> str:
    """Resolve a target-relative path beneath the configured remote work root."""

    _validate_ssh_target(target)
    root_text = target.remote_work_root
    if root_text is None:
        raise OpenSshTransportError("SSH target is missing remote_work_root")
    root = PurePosixPath(root_text)
    relative = PurePosixPath(path.value)
    for part in relative.parts:
        if not _SAFE_REMOTE_PATH_PART.fullmatch(part):
            raise OpenSshTransportError(
                "SSH target-relative paths must use literal shell-inert path components"
            )
    candidate = root / relative
    if candidate == root or root not in candidate.parents:
        raise OpenSshTransportError("remote path escaped configured work root")
    return candidate.as_posix()


def _ssh_prefix(target: ExecutionTargetProfile) -> tuple[str, ...]:
    return (
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "--",
        _host_alias(target),
    )


def _host_alias(target: ExecutionTargetProfile) -> str:
    if target.host_alias is None:
        raise OpenSshTransportError("SSH target is missing host_alias")
    return target.host_alias


def _validate_ssh_target(target: ExecutionTargetProfile) -> None:
    if target.transport is not TransportKind.SSH:
        raise OpenSshTransportError("OpenSshTransport requires an SSH execution target")
    policy = target.ssh_security
    if policy is None:
        raise OpenSshTransportError("SSH target is missing SshSecurityPolicy")
    if not (
        policy.use_system_openssh
        and policy.strict_host_key_checking
        and policy.batch_mode
        and not policy.allow_password_prompt
    ):
        raise OpenSshTransportError("SSH target violates the frozen credential/security boundary")


def _run_local(argv: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            check=False,
            shell=False,
        )
    except OSError as exc:
        raise OpenSshTransportError(f"OpenSSH process launch failed: {exc}") from exc


def _require_success(result: CommandResult, operation: str) -> None:
    if result.exit_code != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
        raise OpenSshTransportError(f"{operation} failed: {detail}")
