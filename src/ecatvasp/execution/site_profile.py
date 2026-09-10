"""User-local site profile contracts for v1.2 real-environment readiness."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum

from ecatvasp.domain import SchedulerType
from ecatvasp.domain.method import canonical_sha256
from ecatvasp.execution.remote import RemotePotcarLibrary
from ecatvasp.execution.targets import (
    ExecutionTargetProfile,
    SshSecurityPolicy,
    TransportKind,
)


class SshMode(StrEnum):
    """Supported credential boundary for reusable real-site profiles."""

    SYSTEM_OPENSSH = "system_openssh"


# Backward-compatible import name from the Block-1 foundation. The canonical scheduler
# vocabulary remains the existing domain SchedulerType rather than a second enum.
SchedulerKind = SchedulerType


@dataclass(frozen=True, slots=True)
class SiteProfile:
    """Reusable operational site configuration; never a ProjectStore scientific entity."""

    site_id: str
    name: str
    host_alias: str
    remote_root: str
    potcar_resolver_id: str
    potcar_family: str
    potcar_root: str
    scheduler_type: SchedulerType = SchedulerType.SLURM
    ssh_mode: SshMode = SshMode.SYSTEM_OPENSSH
    vasp_executable: str = "vasp_std"
    mpi_launcher: str | None = None
    module_loads: tuple[str, ...] = ()
    bader_executable: str | None = None
    lobster_executable: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def validate(self) -> list[str]:
        """Return stable coarse validation fields without weakening canonical target checks."""

        issues: list[str] = []
        required = {
            "site_id": self.site_id,
            "name": self.name,
            "host_alias": self.host_alias,
            "remote_root": self.remote_root,
            "potcar_resolver_id": self.potcar_resolver_id,
            "potcar_family": self.potcar_family,
            "potcar_root": self.potcar_root,
            "vasp_executable": self.vasp_executable,
        }
        issues.extend(name for name, value in required.items() if not value.strip())
        if issues:
            return issues

        try:
            self.to_execution_target()
        except ValueError:
            issues.append("execution_target")
        try:
            self.to_remote_potcar_library()
        except ValueError:
            issues.append("potcar")
        return issues

    @property
    def profile_hash(self) -> str:
        """Hash only operational fields whose change invalidates prior preflight evidence."""

        return canonical_sha256(
            {
                "site_id": self.site_id,
                "host_alias": self.host_alias,
                "remote_root": self.remote_root,
                "scheduler_type": self.scheduler_type,
                "ssh_mode": self.ssh_mode,
                "vasp_executable": self.vasp_executable,
                "mpi_launcher": self.mpi_launcher,
                "module_loads": self.module_loads,
                "potcar_resolver_id": self.potcar_resolver_id,
                "potcar_family": self.potcar_family,
                "potcar_root": self.potcar_root,
                "bader_executable": self.bader_executable,
                "lobster_executable": self.lobster_executable,
            }
        )

    def to_execution_target(self) -> ExecutionTargetProfile:
        """Resolve the profile into the existing execution-target authority."""

        if self.ssh_mode is not SshMode.SYSTEM_OPENSSH:
            raise ValueError("site profile requires the frozen system OpenSSH boundary")
        return ExecutionTargetProfile(
            target_id=self.site_id,
            transport=TransportKind.SSH,
            potcar_resolver_id=self.potcar_resolver_id,
            scheduler=self.scheduler_type,
            host_alias=self.host_alias,
            remote_work_root=self.remote_root,
            vasp_executable=self.vasp_executable,
            launcher=self.mpi_launcher,
            module_loads=self.module_loads,
            ssh_security=SshSecurityPolicy(),
        )

    def to_remote_potcar_library(self) -> RemotePotcarLibrary:
        """Resolve the profile into the existing licensed remote-POTCAR authority."""

        return RemotePotcarLibrary(
            resolver_id=self.potcar_resolver_id,
            family=self.potcar_family,
            root=self.potcar_root,
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
