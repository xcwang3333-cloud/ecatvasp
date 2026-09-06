"""Deterministic project-level read models for application and presentation layers."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
import json

from ecatvasp.domain.ids import ProjectId
from ecatvasp.storage.model import ProjectBundle

StatusCounts = tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class WorkspaceEntityCounts:
    """Explicit counts for every persisted entity family in one project bundle."""

    projects: int
    catalysts: int
    structure_variants: int
    structure_snapshots: int
    active_sites: int
    adsorption_states: int
    state_conformers: int
    method_fingerprints: int
    workflow_plans: int
    calculations: int
    workflow_step_bindings: int
    execution_attempts: int
    remote_jobs: int
    artifacts: int
    analyses: int
    provenance_records: int
    dependency_records: int


@dataclass(frozen=True, slots=True)
class WorkspaceStatusSummary:
    """Observed lifecycle states without collapsing scientific and execution domains."""

    calculations: StatusCounts = ()
    analyses: StatusCounts = ()
    execution_attempts: StatusCounts = ()
    scheduler_jobs: StatusCounts = ()


@dataclass(frozen=True, slots=True)
class WorkspaceProjection:
    """Immutable, non-scientific projection of one validated project bundle."""

    project_id: ProjectId
    project_name: str
    project_slug: str
    schema_version: int
    description: str | None
    created_at: datetime
    entity_counts: WorkspaceEntityCounts
    statuses: WorkspaceStatusSummary
    projection_hash: str = field(init=False)

    def __post_init__(self) -> None:
        payload = {
            "project_id": str(self.project_id),
            "project_name": self.project_name,
            "project_slug": self.project_slug,
            "schema_version": self.schema_version,
            "description": self.description,
            "created_at": self.created_at.isoformat(),
            "entity_counts": {
                "projects": self.entity_counts.projects,
                "catalysts": self.entity_counts.catalysts,
                "structure_variants": self.entity_counts.structure_variants,
                "structure_snapshots": self.entity_counts.structure_snapshots,
                "active_sites": self.entity_counts.active_sites,
                "adsorption_states": self.entity_counts.adsorption_states,
                "state_conformers": self.entity_counts.state_conformers,
                "method_fingerprints": self.entity_counts.method_fingerprints,
                "workflow_plans": self.entity_counts.workflow_plans,
                "calculations": self.entity_counts.calculations,
                "workflow_step_bindings": self.entity_counts.workflow_step_bindings,
                "execution_attempts": self.entity_counts.execution_attempts,
                "remote_jobs": self.entity_counts.remote_jobs,
                "artifacts": self.entity_counts.artifacts,
                "analyses": self.entity_counts.analyses,
                "provenance_records": self.entity_counts.provenance_records,
                "dependency_records": self.entity_counts.dependency_records,
            },
            "statuses": {
                "calculations": self.statuses.calculations,
                "analyses": self.statuses.analyses,
                "execution_attempts": self.statuses.execution_attempts,
                "scheduler_jobs": self.statuses.scheduler_jobs,
            },
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        object.__setattr__(self, "projection_hash", sha256(canonical.encode()).hexdigest())


def build_workspace_projection(bundle: ProjectBundle) -> WorkspaceProjection:
    """Return a deterministic read model after validating project graph integrity."""

    bundle.validate()
    project = bundle.project
    return WorkspaceProjection(
        project_id=project.id,
        project_name=project.name,
        project_slug=project.slug,
        schema_version=project.schema_version,
        description=project.description,
        created_at=project.created_at,
        entity_counts=WorkspaceEntityCounts(
            projects=1,
            catalysts=len(bundle.catalysts),
            structure_variants=len(bundle.structure_variants),
            structure_snapshots=len(bundle.structure_snapshots),
            active_sites=len(bundle.active_sites),
            adsorption_states=len(bundle.adsorption_states),
            state_conformers=len(bundle.state_conformers),
            method_fingerprints=len(bundle.method_fingerprints),
            workflow_plans=len(bundle.workflow_plans),
            calculations=len(bundle.calculations),
            workflow_step_bindings=len(bundle.workflow_step_bindings),
            execution_attempts=len(bundle.execution_attempts),
            remote_jobs=len(bundle.remote_jobs),
            artifacts=len(bundle.artifacts),
            analyses=len(bundle.analyses),
            provenance_records=len(bundle.provenance_records),
            dependency_records=len(bundle.dependency_records),
        ),
        statuses=WorkspaceStatusSummary(
            calculations=_status_counts(item.status for item in bundle.calculations),
            analyses=_status_counts(item.status for item in bundle.analyses),
            execution_attempts=_status_counts(item.status for item in bundle.execution_attempts),
            scheduler_jobs=_status_counts(item.state for item in bundle.remote_jobs),
        ),
    )


def _status_counts(values: Iterable[StrEnum]) -> StatusCounts:
    counts = Counter(value.value for value in values)
    return tuple(sorted(counts.items()))
