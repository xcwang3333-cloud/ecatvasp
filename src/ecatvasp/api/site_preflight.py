"""Application boundary for transient real-site preflight diagnostics."""

from __future__ import annotations

from ecatvasp.execution.preflight import PreflightReport, PreflightService
from ecatvasp.execution.site_profile import SiteProfile


class SitePreflightApplicationService:
    """Expose real-site diagnostics without creating or mutating project scientific state."""

    def __init__(self, preflight_service: PreflightService | None = None) -> None:
        self._preflight_service = (
            PreflightService() if preflight_service is None else preflight_service
        )

    def run(self, site_profile: SiteProfile) -> PreflightReport:
        """Run one transient preflight against the supplied user-local profile."""

        return self._preflight_service.run(site_profile)


__all__ = ["SitePreflightApplicationService"]
