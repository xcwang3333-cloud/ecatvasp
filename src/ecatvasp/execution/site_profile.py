"""User-local site profile contracts for v1.2 real-environment readiness."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import re


_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class SshMode(StrEnum):
    SYSTEM_OPENSSH = "system_openssh"


class SchedulerKind(StrEnum):
    SLURM = "slurm"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class SiteProfile:
    """Environment configuration only; never a scientific ProjectStore entity."""

    site_id: str
    name: str
    hostname: str
    username: str | None = None
    ssh_mode: SshMode = SshMode.SYSTEM_OPENSSH
    remote_root: str = ""
    scheduler_type: SchedulerKind | None = None
    vasp_path: str = ""
    mpi_launcher: str | None = None
    bader_path: str | None = None
    lobster_path: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def validate(self) -> list[str]:
        issues: list[str] = []
        if not _ID_PATTERN.fullmatch(self.site_id):
            issues.append("site_id")
        if not self.name.strip():
            issues.append("name")
        if not self.hostname.strip():
            issues.append("hostname")
        if not self.remote_root.strip():
            issues.append("remote_root")
        return issues

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
