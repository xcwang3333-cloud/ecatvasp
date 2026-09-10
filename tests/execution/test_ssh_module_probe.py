from __future__ import annotations

import subprocess

import pytest

from ecatvasp.domain import SchedulerType
from ecatvasp.execution.adapters import CommandSpec
from ecatvasp.execution.ssh import OpenSshTransport, OpenSshTransportError
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
