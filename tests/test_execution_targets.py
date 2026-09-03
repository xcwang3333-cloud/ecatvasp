from __future__ import annotations

from dataclasses import replace

import pytest

from ecatvasp.domain import SchedulerType
from ecatvasp.execution import (
    ExecutionTargetProfile,
    SshSecurityPolicy,
    TransportKind,
)


def test_local_execution_target_is_scheduler_free_and_hashable() -> None:
    target = ExecutionTargetProfile(
        target_id="local-workstation",
        transport=TransportKind.LOCAL,
        potcar_resolver_id="vasp-pbe54-local",
        vasp_executable="vasp_std",
    )

    snapshot = target.sanitized_environment()

    assert target.scheduler is None
    assert len(target.target_hash) == 64
    assert snapshot.target_hash == target.target_hash
    assert snapshot.target_id == "local-workstation"
    assert snapshot.scheduler is None


def test_ssh_target_requires_strict_system_openssh_boundary() -> None:
    target = ExecutionTargetProfile(
        target_id="cluster-a",
        transport=TransportKind.SSH,
        scheduler=SchedulerType.SLURM,
        host_alias="hpc-a",
        remote_work_root="/scratch/xiaochen/ecatvasp",
        potcar_resolver_id="vasp-pbe54-cluster-a",
        launcher="srun",
        module_loads=("vasp/6.4.3", "intel/2025"),
        ssh_security=SshSecurityPolicy(),
    )

    snapshot = target.sanitized_environment()

    assert snapshot.scheduler is SchedulerType.SLURM
    assert snapshot.launcher == "srun"
    assert snapshot.module_loads == ("vasp/6.4.3", "intel/2025")
    assert not hasattr(snapshot, "host_alias")
    assert not hasattr(snapshot, "remote_work_root")


@pytest.mark.parametrize(
    "policy",
    [
        SshSecurityPolicy,
    ],
)
def test_ssh_security_policy_defaults_are_non_permissive(policy: type[SshSecurityPolicy]) -> None:
    value = policy()

    assert value.use_system_openssh is True
    assert value.strict_host_key_checking is True
    assert value.batch_mode is True
    assert value.allow_password_prompt is False


def test_ssh_security_policy_rejects_weakened_settings() -> None:
    with pytest.raises(ValueError, match="strict host-key"):
        SshSecurityPolicy(strict_host_key_checking=False)
    with pytest.raises(ValueError, match="password prompting"):
        SshSecurityPolicy(allow_password_prompt=True)
    with pytest.raises(ValueError, match="system OpenSSH"):
        SshSecurityPolicy(use_system_openssh=False)


def test_ssh_target_rejects_inline_host_syntax_and_unsafe_remote_roots() -> None:
    base = ExecutionTargetProfile(
        target_id="cluster-a",
        transport=TransportKind.SSH,
        scheduler=SchedulerType.SLURM,
        host_alias="hpc-a",
        remote_work_root="/scratch/xiaochen/ecatvasp",
        potcar_resolver_id="vasp-pbe54-cluster-a",
        ssh_security=SshSecurityPolicy(),
    )

    with pytest.raises(ValueError, match="host_alias"):
        replace(base, host_alias="user@hpc-a")
    with pytest.raises(ValueError, match="absolute POSIX"):
        replace(base, remote_work_root="scratch/ecatvasp")
    with pytest.raises(ValueError, match="non-root"):
        replace(base, remote_work_root="/")
    with pytest.raises(ValueError, match="whitespace"):
        replace(base, remote_work_root="/scratch/my work")


def test_target_hash_changes_with_execution_environment_not_scientific_identity() -> None:
    target = ExecutionTargetProfile(
        target_id="cluster-a",
        transport=TransportKind.SSH,
        scheduler=SchedulerType.SLURM,
        host_alias="hpc-a",
        remote_work_root="/scratch/xiaochen/ecatvasp",
        potcar_resolver_id="vasp-pbe54-cluster-a",
        module_loads=("vasp/6.4.3",),
        ssh_security=SshSecurityPolicy(),
    )
    changed = replace(target, module_loads=("vasp/6.5.0",))

    assert target.target_hash != changed.target_hash
