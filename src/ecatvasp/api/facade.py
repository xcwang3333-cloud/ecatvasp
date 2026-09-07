"""Path-scoped Python facade for headless ECatVASP project use.

The facade retains only the project path. Every operation constructs a fresh
``ProjectApplicationService`` over ``ProjectStore`` so current durable state remains authoritative.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from ecatvasp.api.application import (
    ApplicationInspectionResult,
    ApplicationReportFormat,
    ApplicationReportResult,
    ProjectApplicationService,
)
from ecatvasp.provenance import FreshnessState
from ecatvasp.storage import ProjectStore

StatusCounts = tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class HeadlessProjectStatus:
    """Deterministic project status preserving separate lifecycle namespaces."""

    project_id: str
    project_name: str
    project_slug: str
    schema_version: int
    projection_hash: str
    calculations: StatusCounts
    analyses: StatusCounts
    execution_attempts: StatusCounts
    scheduler_jobs: StatusCounts
    freshness: StatusCounts
    attention_rows: int


class ProjectFacade:
    """Small Python entry point shared by notebooks, scripts, and the CLI."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        """Return the project directory without opening or caching project state."""

        return self._root

    def application(self) -> ProjectApplicationService:
        """Return a fresh application service bound to the same durable project path."""

        return ProjectApplicationService(ProjectStore(self._root))

    def inspect(self) -> ApplicationInspectionResult:
        """Reopen current project state and return existing workspace/inventory projections."""

        return self.application().inspect()

    def status(self) -> HeadlessProjectStatus:
        """Return a deterministic status summary without collapsing lifecycle domains."""

        inspection = self.inspect()
        projection = inspection.projection
        freshness_counts = Counter(row.freshness.state.value for row in inspection.inventory.rows)
        attention_rows = sum(
            1
            for row in inspection.inventory.rows
            if row.attention_codes or row.freshness.state is not FreshnessState.FRESH
        )
        return HeadlessProjectStatus(
            project_id=str(projection.project_id),
            project_name=projection.project_name,
            project_slug=projection.project_slug,
            schema_version=projection.schema_version,
            projection_hash=projection.projection_hash,
            calculations=projection.statuses.calculations,
            analyses=projection.statuses.analyses,
            execution_attempts=projection.statuses.execution_attempts,
            scheduler_jobs=projection.statuses.scheduler_jobs,
            freshness=tuple(sorted(freshness_counts.items())),
            attention_rows=attention_rows,
        )

    def report(
        self,
        *,
        format: ApplicationReportFormat = ApplicationReportFormat.MARKDOWN,
    ) -> ApplicationReportResult:
        """Render the current project through the Block 5 reporting authority."""

        return self.application().report(format=format)


def open_project(root: Path | str) -> ProjectFacade:
    """Create a path-scoped facade without opening or mutating the project."""

    return ProjectFacade(root)
