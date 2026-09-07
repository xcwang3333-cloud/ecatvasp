"""Desktop read-model composition over current authoritative scientific state."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from ecatvasp.frontend import FrontendHandoff, build_frontend_handoff
from ecatvasp.provenance import FreshnessState
from ecatvasp.storage import ProjectBundle
from ecatvasp.visualization import build_structure_presentation
from ecatvasp.workspace import build_scientific_inventory, build_workspace_projection

StatusCounts = tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class DesktopProjectDashboard:
    """Small task-oriented project summary with no presentation or scientific authority."""

    project_id: str
    project_name: str
    project_slug: str
    schema_version: int
    model_counts: StatusCounts
    workflow_counts: StatusCounts
    calculations: StatusCounts
    analyses: StatusCounts
    execution_attempts: StatusCounts
    scheduler_jobs: StatusCounts
    freshness: StatusCounts
    attention_rows: int

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic page-scoped transport representation."""

        return {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "project_slug": self.project_slug,
            "schema_version": self.schema_version,
            "model_counts": _pairs(self.model_counts),
            "workflow_counts": _pairs(self.workflow_counts),
            "calculations": _pairs(self.calculations),
            "analyses": _pairs(self.analyses),
            "execution_attempts": _pairs(self.execution_attempts),
            "scheduler_jobs": _pairs(self.scheduler_jobs),
            "freshness": _pairs(self.freshness),
            "attention_rows": self.attention_rows,
        }


def build_desktop_project_dashboard(bundle: ProjectBundle) -> DesktopProjectDashboard:
    """Build a compact dashboard without constructing every scientific presentation.

    The dashboard composes existing workspace/freshness authorities and preserves their separate
    lifecycle namespaces. Counts are transient read-model data; they are not persisted scientific
    state and do not infer scheduler success as scientific convergence.
    """

    projection = build_workspace_projection(bundle)
    inventory = build_scientific_inventory(bundle)
    freshness_counts = Counter(row.freshness.state.value for row in inventory.rows)
    attention_rows = sum(
        1
        for row in inventory.rows
        if row.attention_codes or row.freshness.state is not FreshnessState.FRESH
    )
    return DesktopProjectDashboard(
        project_id=str(projection.project_id),
        project_name=projection.project_name,
        project_slug=projection.project_slug,
        schema_version=projection.schema_version,
        model_counts=(
            ("catalysts", len(bundle.catalysts)),
            ("structure_variants", len(bundle.structure_variants)),
            ("structure_snapshots", len(bundle.structure_snapshots)),
            ("active_sites", len(bundle.active_sites)),
            ("adsorption_states", len(bundle.adsorption_states)),
            ("state_conformers", len(bundle.state_conformers)),
        ),
        workflow_counts=(
            ("workflow_plans", len(bundle.workflow_plans)),
            ("workflow_step_bindings", len(bundle.workflow_step_bindings)),
        ),
        calculations=projection.statuses.calculations,
        analyses=projection.statuses.analyses,
        execution_attempts=projection.statuses.execution_attempts,
        scheduler_jobs=projection.statuses.scheduler_jobs,
        freshness=tuple(sorted(freshness_counts.items())),
        attention_rows=attention_rows,
    )


def build_desktop_frontend_handoff(bundle: ProjectBundle) -> FrontendHandoff:
    """Build the frozen v1 desktop handoff without persisting or inferring scientific state.

    This compatibility path intentionally keeps the v1 behavior of creating presentations for every
    current StructureSnapshot. v1.1 task-oriented pages use page-scoped v2 reads instead; later
    performance work can optimize read internals without changing this frozen handoff contract.
    Workflow readiness and result-specific presentations remain caller-owned exact authorities.
    """

    presentations = tuple(
        build_structure_presentation(snapshot)
        for snapshot in bundle.structure_snapshots
    )
    return build_frontend_handoff(bundle, presentations=presentations)


def _pairs(values: StatusCounts) -> list[list[object]]:
    return [[name, count] for name, count in values]
