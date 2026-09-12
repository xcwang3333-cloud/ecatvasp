from __future__ import annotations

import subprocess

import pytest

from ecatvasp.domain import SchedulerType
from ecatvasp.execution.adapters import CommandSpec
from ecatvasp.execution.ssh import (
    OpenSshTimeoutError,
    OpenSshTransport,
    OpenSshTransportError,
)
from ecatvasp.execution.targets import (
    ExecutionTargetProfile,
    SshSecurityPolicy,
    TransportKind,
)


def _target() -> ExecutionTargetProfile:
    return ExecutionTargetProfile(
        target_id="hpc-prod",
        transport=TransportKind.SSH,
        scheduler=SchedulerType.SLURM,
        host_alias="cluster-a",
        remote_work_root="/scratch/ecatvasp",
        potcar_resolver_id="pbe54-remote",
        vasp_executable="vasp_std",
        launcher="srun",
        module_loads=("intel/2026", "vasp/6.4"),
        ssh_security=SshSecurityPolicy(),
    )


def test_openssh_module_probe_renders_only_bounded_validated_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(
        argv: tuple[str, ...],
        *,
        capture_output: bool,
        check: bool,
        shell: bool,
    ) -> subprocess.CompletedProcess[bytes]:
        assert capture_output is True
        assert check is False
        assert shell is False
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, b"/apps/vasp_std\n", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = OpenSshTransport().probe_module_environment(
        target=_target(),
        command="vasp_std",
    )

    assert result.exit_code == 0
    assert result.stdout == "/apps/vasp_std\n"
    assert len(calls) == 1
    assert calls[0][:7] == (
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "--",
        "cluster-a",
    )
    assert len(calls[0]) == 8
    remote_command = calls[0][-1]
    assert remote_command.startswith("bash -lc ")
    assert "set -euo pipefail" in remote_command
    assert "module load intel/2026" in remote_command
    assert "module load vasp/6.4" in remote_command
    assert "command -v vasp_std" in remote_command


def test_module_probe_does_not_relax_generic_shell_injection_boundary() -> None:
    transport = OpenSshTransport()
    target = _target()

    with pytest.raises(OpenSshTransportError, match="portable command name"):
        transport.probe_module_environment(target=target, command="vasp_std;id")

    with pytest.raises(OpenSshTransportError, match="shell-inert"):
        transport.run(
            target=target,
            command=CommandSpec(argv=("echo", "unsafe;token")),
        )


def test_configured_command_timeout_bounds_module_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_timeout: list[float] = []

    def fake_run(
        argv: tuple[str, ...],
        *,
        capture_output: bool,
        check: bool,
        shell: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        observed_timeout.append(timeout)
        return subprocess.CompletedProcess(argv, 0, b"/apps/vasp_std\n", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = OpenSshTransport(command_timeout_seconds=30).probe_module_environment(
        target=_target(),
        command="vasp_std",
    )

    assert result.exit_code == 0
    assert observed_timeout == [30.0]


def test_configured_command_timeout_is_typed_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(
        argv: tuple[str, ...],
        *,
        capture_output: bool,
        check: bool,
        shell: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(argv, timeout)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(OpenSshTimeoutError) as captured:
        OpenSshTransport(command_timeout_seconds=12.5).run(
            target=_target(),
            command=CommandSpec(argv=("test", "-d", "/scratch/ecatvasp/private")),
        )

    error = captured.value
    assert isinstance(error, OpenSshTransportError)
    assert error.code == "SSH_TRANSPORT_TIMEOUT"
    assert error.operation == "command"
    assert error.timeout_seconds == 12.5
    diagnostic = str(error)
    assert "12.5" in diagnostic
    assert "cluster-a" not in diagnostic
    assert "/scratch/ecatvasp/private" not in diagnostic
    assert "test" not in diagnostic
    assert error.__cause__ is None


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True, "30"])
def test_command_timeout_must_be_finite_and_positive(value: object) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        OpenSshTransport(command_timeout_seconds=value)  # type: ignore[arg-type]
