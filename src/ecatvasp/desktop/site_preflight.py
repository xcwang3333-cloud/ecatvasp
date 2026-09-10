"""Desktop-facing action for transient real-site preflight diagnostics."""

from __future__ import annotations

from typing import Any

from ecatvasp.api.site_preflight import SitePreflightApplicationService
from ecatvasp.domain import SchedulerType
from ecatvasp.execution.preflight import PreflightCheck, PreflightReport
from ecatvasp.execution.site_profile import SiteProfile, SshMode


def site_preflight_action(
    profile: dict[str, Any],
    *,
    service: SitePreflightApplicationService | None = None,
) -> dict[str, object]:
    """Run preflight from a user-local profile and return sanitized transient evidence."""

    selected_service = SitePreflightApplicationService() if service is None else service
    report = selected_service.run(_site_profile(profile))
    return _report_payload(report)


def _site_profile(raw: dict[str, Any]) -> SiteProfile:
    modules_raw = raw.get("module_loads", [])
    if not isinstance(modules_raw, list):
        raise ValueError("profile.module_loads must be a string list")
    return SiteProfile(
        site_id=str(raw["site_id"]),
        name=str(raw["name"]),
        host_alias=str(raw["host_alias"]),
        remote_root=str(raw["remote_root"]),
        potcar_resolver_id=str(raw["potcar_resolver_id"]),
        potcar_family=str(raw["potcar_family"]),
        potcar_root=str(raw["potcar_root"]),
        scheduler_type=SchedulerType(str(raw.get("scheduler_type", SchedulerType.SLURM.value))),
        ssh_mode=SshMode(str(raw.get("ssh_mode", SshMode.SYSTEM_OPENSSH.value))),
        vasp_executable=str(raw.get("vasp_executable", "vasp_std")),
        mpi_launcher=_optional_text(raw.get("mpi_launcher")),
        module_loads=tuple(str(item) for item in modules_raw),
        bader_executable=_optional_text(raw.get("bader_executable")),
        lobster_executable=_optional_text(raw.get("lobster_executable")),
    )


def _report_payload(report: PreflightReport) -> dict[str, object]:
    return {
        "site_id": report.site_id,
        "profile_hash": report.profile_hash,
        "status": report.status.value,
        "timestamp": report.timestamp,
        "transient": True,
        "checks": [_check_payload(check) for check in report.checks],
    }


def _check_payload(check: PreflightCheck) -> dict[str, object]:
    return {
        "check_name": check.check_name,
        "status": check.status.value,
        "reason_code": None if check.reason_code is None else check.reason_code.value,
        "message": check.message,
        "evidence": list(check.evidence),
    }


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional site-profile command fields must be strings")
    return value


__all__ = ["site_preflight_action"]
