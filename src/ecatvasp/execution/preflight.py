"""Local configuration preflight contracts.

This module intentionally does not perform SSH, scheduler, or executable discovery.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from ecatvasp.execution.site_profile import SiteProfile


class PreflightStatus(StrEnum):
    READY = "READY"
    BLOCKED = "BLOCKED"
    WARNING = "WARNING"
    UNAVAILABLE_OPTIONAL = "UNAVAILABLE_OPTIONAL"


class ReasonCode(StrEnum):
    SSH_UNAVAILABLE = "SSH_UNAVAILABLE"
    REMOTE_ROOT_MISSING = "REMOTE_ROOT_MISSING"
    REMOTE_ROOT_NOT_WRITABLE = "REMOTE_ROOT_NOT_WRITABLE"
    SCHEDULER_NOT_FOUND = "SCHEDULER_NOT_FOUND"
    VASP_NOT_FOUND = "VASP_NOT_FOUND"
    OPTIONAL_TOOL_MISSING = "OPTIONAL_TOOL_MISSING"
    INVALID_SITE_PROFILE = "INVALID_SITE_PROFILE"


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    check_name: str
    status: PreflightStatus
    reason_code: ReasonCode | None
    message: str


@dataclass(frozen=True, slots=True)
class PreflightReport:
    site_id: str
    status: PreflightStatus
    checks: tuple[PreflightCheck, ...]
    timestamp: str


class PreflightService:
    """Validate local profile shape only."""

    def run(self, site_profile: SiteProfile) -> PreflightReport:
        checks: list[PreflightCheck] = []
        issues = site_profile.validate()
        if issues:
            checks.append(
                PreflightCheck(
                    "profile_validation",
                    PreflightStatus.BLOCKED,
                    ReasonCode.INVALID_SITE_PROFILE,
                    ",".join(issues),
                )
            )
        else:
            checks.append(
                PreflightCheck(
                    "profile_validation",
                    PreflightStatus.READY,
                    None,
                    "site profile syntax is valid",
                )
            )

        status = (
            PreflightStatus.BLOCKED
            if issues
            else PreflightStatus.READY
        )
        return PreflightReport(
            site_id=site_profile.site_id,
            status=status,
            checks=tuple(checks),
            timestamp=datetime.now(UTC).isoformat(),
        )
