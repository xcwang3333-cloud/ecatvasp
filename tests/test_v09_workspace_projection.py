from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from ecatvasp.domain.calculation import Analysis, AnalysisStatus, AnalysisType
from ecatvasp.domain.entities import Catalyst, Project
from ecatvasp.domain.ids import ProjectId
from ecatvasp.schema.version import SCHEMA_VERSION
from ecatvasp.storage.model import ProjectBundle, ProjectIntegrityError
from ecatvasp.workspace import build_workspace_projection


def _project() -> Project:
    return Project(
        name="Workspace acceptance",
        slug="workspace-acceptance",
        id=ProjectId(UUID("018f0000-0000-7000-8000-000000000001")),
        schema_version=SCHEMA_VERSION,
        description="v0.9 deterministic workspace projection",
        created_at=datetime(2026, 9, 6, 15, 50, tzinfo=UTC),
    )


def test_workspace_projection_is_deterministic_and_counts_entity_families() -> None:
    project = _project()
    catalyst = Catalyst(project_id=project.id, name="Pb3-N/C", slug="pb3-nc")
    analysis = Analysis(
        project_id=project.id,
        analysis_type=AnalysisType.DOS,
        input_artifact_ids=(),
        status=AnalysisStatus.COMPLETED,
    )
    bundle = ProjectBundle(project=project, catalysts=(catalyst,), analyses=(analysis,))

    first = build_workspace_projection(bundle)
    second = build_workspace_projection(bundle)

    assert first == second
    assert first.projection_hash == second.projection_hash
    assert len(first.projection_hash) == 64
    assert first.project_id == project.id
    assert first.schema_version == 3
    assert first.entity_counts.projects == 1
    assert first.entity_counts.catalysts == 1
    assert first.entity_counts.analyses == 1
    assert first.entity_counts.calculations == 0
    assert first.entity_counts.execution_attempts == 0
    assert first.entity_counts.remote_jobs == 0
    assert first.statuses.analyses == (("completed", 1),)
    assert first.statuses.calculations == ()
    assert first.statuses.execution_attempts == ()
    assert first.statuses.scheduler_jobs == ()


def test_workspace_projection_hash_changes_when_visible_status_changes() -> None:
    project = _project()
    completed = Analysis(
        project_id=project.id,
        analysis_type=AnalysisType.GEOMETRY,
        input_artifact_ids=(),
        status=AnalysisStatus.COMPLETED,
    )
    stale = replace(completed, status=AnalysisStatus.STALE)

    completed_projection = build_workspace_projection(
        ProjectBundle(project=project, analyses=(completed,))
    )
    stale_projection = build_workspace_projection(ProjectBundle(project=project, analyses=(stale,)))

    assert completed_projection.statuses.analyses == (("completed", 1),)
    assert stale_projection.statuses.analyses == (("stale", 1),)
    assert completed_projection.projection_hash != stale_projection.projection_hash


def test_workspace_projection_fails_closed_for_invalid_project_graph() -> None:
    project = _project()
    other_project_id = ProjectId(UUID("018f0000-0000-7000-8000-000000000002"))
    wrong_catalyst = Catalyst(
        project_id=other_project_id,
        name="wrong project catalyst",
        slug="wrong-project-catalyst",
    )
    invalid_bundle = ProjectBundle(project=project, catalysts=(wrong_catalyst,))

    with pytest.raises(ProjectIntegrityError, match="different Project"):
        build_workspace_projection(invalid_bundle)


def test_workspace_projection_does_not_advance_project_schema() -> None:
    projection = build_workspace_projection(ProjectBundle(project=_project()))

    assert SCHEMA_VERSION == 3
    assert projection.schema_version == SCHEMA_VERSION
