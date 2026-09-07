"""Typed desktop application actions over existing Python authorities."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from ecatvasp.api.application import ApplicationReportFormat, ProjectApplicationService
from ecatvasp.domain import StructureSnapshotId, WorkflowRecipeIdentity
from ecatvasp.storage import ProjectStore
from ecatvasp.workflow import get_workflow_recipe_spec, list_workflow_recipe_specs


@dataclass(frozen=True, slots=True)
class DesktopWorkflowRecipeSummary:
    """Package-level canonical workflow recipe metadata for desktop selection."""

    recipe_id: str
    version: str
    description: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "recipe_id": self.recipe_id,
            "version": self.version,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class DesktopPrepareWorkflowReceipt:
    """Exact durable receipt returned by ProjectApplicationService.prepare_workflow()."""

    project_id: str
    workflow_plan_id: str
    workflow_recipe_id: str
    workflow_recipe_version: str
    root_structure_snapshot_id: str
    plan_hash: str
    planning_hash: str
    reused: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "project_id": self.project_id,
            "workflow_plan_id": self.workflow_plan_id,
            "workflow_recipe_id": self.workflow_recipe_id,
            "workflow_recipe_version": self.workflow_recipe_version,
            "root_structure_snapshot_id": self.root_structure_snapshot_id,
            "plan_hash": self.plan_hash,
            "planning_hash": self.planning_hash,
            "reused": self.reused,
        }


@dataclass(frozen=True, slots=True)
class DesktopReportReceipt:
    """Exact transient report receipt without creating a desktop scientific entity."""

    project_id: str
    report_format: str
    report_contract_version: str
    report_hash: str
    content_sha256: str
    content: str

    def to_dict(self) -> dict[str, object]:
        return {
            "project_id": self.project_id,
            "report_format": self.report_format,
            "report_contract_version": self.report_contract_version,
            "report_hash": self.report_hash,
            "content_sha256": self.content_sha256,
            "content": self.content,
        }


def list_desktop_workflow_recipes() -> tuple[DesktopWorkflowRecipeSummary, ...]:
    """Return canonical package recipe metadata without frontend-owned scientific defaults."""

    return tuple(
        DesktopWorkflowRecipeSummary(
            recipe_id=spec.recipe_id,
            version=spec.version,
            description=spec.description,
        )
        for spec in list_workflow_recipe_specs()
    )


def prepare_workflow_action(
    *,
    project_root: Path | str,
    workflow_recipe_id: str,
    workflow_recipe_version: str,
    root_structure_snapshot_id: str,
    parameters_hash: str | None,
) -> DesktopPrepareWorkflowReceipt:
    """Delegate one typed workflow-plan mutation to ProjectApplicationService."""

    identity = WorkflowRecipeIdentity(
        recipe_id=workflow_recipe_id,
        version=workflow_recipe_version,
    )
    get_workflow_recipe_spec(identity)
    snapshot_id = StructureSnapshotId(UUID(root_structure_snapshot_id))
    receipt = ProjectApplicationService(ProjectStore(project_root)).prepare_workflow(
        workflow_recipe=identity,
        root_structure_snapshot_id=snapshot_id,
        parameters_hash=parameters_hash,
    )
    plan = receipt.plan
    return DesktopPrepareWorkflowReceipt(
        project_id=str(plan.project_id),
        workflow_plan_id=str(plan.id),
        workflow_recipe_id=plan.workflow_recipe.recipe_id,
        workflow_recipe_version=plan.workflow_recipe.version,
        root_structure_snapshot_id=str(plan.root_structure_snapshot_id),
        plan_hash=plan.plan_hash,
        planning_hash=receipt.planning_hash,
        reused=receipt.reused,
    )


def report_action(
    *,
    project_root: Path | str,
    report_format: str,
) -> DesktopReportReceipt:
    """Delegate deterministic report rendering to ProjectApplicationService."""

    result = ProjectApplicationService(ProjectStore(project_root)).report(
        format=ApplicationReportFormat(report_format)
    )
    return DesktopReportReceipt(
        project_id=str(result.report.projection.project_id),
        report_format=result.format.value,
        report_contract_version=result.report.contract_version,
        report_hash=result.report.report_hash,
        content_sha256=sha256(result.content.encode("utf-8")).hexdigest(),
        content=result.content,
    )
