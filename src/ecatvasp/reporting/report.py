"""Deterministic scientific reporting over existing workspace and presentation contracts.

Reports are exported read models, not scientific analyses or persisted workflow history.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from hashlib import sha256
from typing import TypeAlias
from uuid import UUID

from ecatvasp.provenance import FreshnessState, scientific_hash
from ecatvasp.storage.model import ProjectBundle
from ecatvasp.visualization import (
    CohpPresentationDataset,
    DosPresentationDataset,
    ReactionDiagramPresentationDataset,
    StructurePresentationDataset,
)
from ecatvasp.workflow import WorkflowGateError, resolve_workflow_binding_generations
from ecatvasp.workspace import (
    WorkflowReadinessDashboard,
    WorkspaceScientificInventory,
    build_scientific_inventory,
    build_workspace_projection,
)
from ecatvasp.workspace.projection import WorkspaceProjection

REPORT_CONTRACT_VERSION = "ecatvasp-scientific-report-v1"

ScientificPresentation: TypeAlias = (
    StructurePresentationDataset
    | DosPresentationDataset
    | CohpPresentationDataset
    | ReactionDiagramPresentationDataset
)


class ScientificReportError(ValueError):
    """Raised when report inputs cannot be combined without stale or foreign state."""


@dataclass(frozen=True, slots=True)
class ScientificReport:
    """Transient source-linked report composed from authoritative v0.9 read models."""

    projection: WorkspaceProjection
    inventory: WorkspaceScientificInventory
    readiness: tuple[WorkflowReadinessDashboard, ...] = ()
    presentations: tuple[ScientificPresentation, ...] = ()
    contract_version: str = REPORT_CONTRACT_VERSION
    report_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.contract_version != REPORT_CONTRACT_VERSION:
            raise ScientificReportError("unsupported scientific report contract version")
        if self.inventory.project_id != self.projection.project_id:
            raise ScientificReportError("report inventory belongs to another Project")
        payload = _report_payload(self, include_report_hash=False)
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        object.__setattr__(self, "report_hash", sha256(canonical.encode("utf-8")).hexdigest())

    def to_dict(self) -> dict[str, object]:
        """Return the complete deterministic JSON-compatible report manifest."""

        return _report_payload(self, include_report_hash=True)


def build_scientific_report(
    bundle: ProjectBundle,
    *,
    inventory: WorkspaceScientificInventory | None = None,
    readiness: tuple[WorkflowReadinessDashboard, ...] = (),
    presentations: tuple[ScientificPresentation, ...] = (),
) -> ScientificReport:
    """Compose existing authorities without recomputing scientific results."""

    bundle.validate()
    projection = build_workspace_projection(bundle)
    selected_inventory = build_scientific_inventory(bundle) if inventory is None else inventory
    _validate_inventory(bundle=bundle, inventory=selected_inventory)
    _validate_readiness(bundle=bundle, dashboards=readiness)
    _validate_presentations(bundle=bundle, presentations=presentations)
    return ScientificReport(
        projection=projection,
        inventory=selected_inventory,
        readiness=tuple(sorted(readiness, key=lambda item: str(item.workflow_plan_id))),
        presentations=tuple(sorted(presentations, key=_presentation_sort_key)),
    )


def render_report_json(report: ScientificReport) -> str:
    """Render the full report manifest as deterministic UTF-8 JSON text."""

    return (
        json.dumps(
            report.to_dict(),
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )


def render_inventory_csv(report: ScientificReport) -> str:
    """Render deterministic entity/provenance/dependency/freshness inspection rows."""

    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        (
            "entity_id",
            "entity_kind",
            "display_label",
            "status_domain",
            "status",
            "current_scientific_hash",
            "freshness_state",
            "freshness_reason_codes",
            "attention_codes",
            "scientific_ancestor_ids",
            "provenance",
            "incoming_dependencies",
            "outgoing_dependencies",
        )
    )
    for row in report.inventory.rows:
        writer.writerow(
            (
                str(row.entity_id),
                row.entity_kind.value,
                row.display_label,
                "" if row.status_domain is None else row.status_domain.value,
                "" if row.status is None else row.status,
                "" if row.current_scientific_hash is None else row.current_scientific_hash,
                row.freshness.state.value,
                _join(item.code for item in row.freshness.reasons),
                _join(row.attention_codes),
                _join(str(item) for item in row.scientific_ancestor_ids),
                _join(
                    (
                        f"{item.provenance_id}:{item.tool}@{item.tool_version}"
                        for item in row.provenance
                    )
                ),
                _join(_dependency_token(item) for item in row.incoming_dependencies),
                _join(_dependency_token(item) for item in row.outgoing_dependencies),
            )
        )
    return output.getvalue()


def render_report_markdown(report: ScientificReport) -> str:
    """Render a deterministic human-readable report without hiding attention states."""

    projection = report.projection
    lines = [
        "# ECatVASP Scientific Report",
        "",
        f"- Report contract: `{report.contract_version}`",
        f"- Report hash: `{report.report_hash}`",
        f"- Project: `{projection.project_slug}` ({projection.project_name})",
        f"- Project ID: `{projection.project_id}`",
        f"- Schema version: `{projection.schema_version}`",
        f"- Workspace projection hash: `{projection.projection_hash}`",
        "",
        "## Project inventory summary",
        "",
        "| Entity family | Count |",
        "| --- | ---: |",
    ]
    for label, count in _entity_count_items(projection):
        lines.append(f"| {_md(label)} | {count} |")

    lines.extend(
        [
            "",
            "## Lifecycle status",
            "",
            "| Status domain | Status | Count |",
            "| --- | --- | ---: |",
        ]
    )
    status_rows = _status_rows(projection)
    if status_rows:
        for domain, status, count in status_rows:
            lines.append(f"| {_md(domain)} | {_md(status)} | {count} |")
    else:
        lines.append("| none | none | 0 |")

    lines.extend(["", "## Attention and freshness", ""])
    attention_rows = tuple(
        row
        for row in report.inventory.rows
        if row.attention_codes or row.freshness.state is not FreshnessState.FRESH
    )
    if not attention_rows:
        lines.append("No stale, invalid, superseded, blocked, or otherwise non-fresh inventory rows.")
    else:
        lines.extend(
            [
                "| Entity | Kind | Status | Freshness | Attention | Freshness reasons |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for row in attention_rows:
            status = "" if row.status is None else row.status
            lines.append(
                "| "
                + " | ".join(
                    (
                        f"`{row.entity_id}`",
                        _md(row.entity_kind.value),
                        _md(status),
                        _md(row.freshness.state.value),
                        _md(", ".join(row.attention_codes)),
                        _md(", ".join(item.code for item in row.freshness.reasons)),
                    )
                )
                + " |"
            )

    lines.extend(["", "## Provenance records", ""])
    provenance = tuple(
        item
        for row in report.inventory.rows
        for item in row.provenance
    )
    if not provenance:
        lines.append("No provenance records are attached to inventory entities.")
    else:
        lines.extend(
            [
                "| Subject | Provenance ID | Tool | Version | Parameters hash | Method fingerprint |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for item in sorted(provenance, key=lambda value: (str(value.subject_id), str(value.provenance_id))):
            lines.append(
                "| "
                + " | ".join(
                    (
                        f"`{item.subject_id}`",
                        f"`{item.provenance_id}`",
                        _md(item.tool),
                        _md(item.tool_version),
                        _optional_code(item.parameters_hash),
                        _optional_code(item.method_fingerprint_id),
                    )
                )
                + " |"
            )

    lines.extend(["", "## Dependency links", ""])
    if not report.inventory.dependencies:
        lines.append("No dependency records are present.")
    else:
        lines.extend(
            [
                "| Dependency | Kind | Role | Upstream | Downstream | Recorded hash |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for item in report.inventory.dependencies:
            lines.append(
                "| "
                + " | ".join(
                    (
                        f"`{item.dependency_id}`",
                        _md(item.kind.value),
                        _md(item.role),
                        f"`{item.upstream_id}`",
                        f"`{item.downstream_id}`",
                        f"`{item.recorded_hash}`",
                    )
                )
                + " |"
            )

    lines.extend(["", "## Workflow readiness", ""])
    if not report.readiness:
        lines.append("No workflow readiness dashboards were supplied to this report.")
    for dashboard in report.readiness:
        lines.extend(
            [
                f"### Workflow `{dashboard.workflow_plan_id}`",
                "",
                "| Step | Scientific state | Readiness | Calculation status | Generation | Gate reasons | Orchestration |",
                "| --- | --- | --- | --- | ---: | --- | --- |",
            ]
        )
        for step in dashboard.steps:
            generation = "" if step.current_generation is None else str(step.current_generation)
            orchestration = (
                "" if step.orchestration_action is None else step.orchestration_action.value
            )
            calculation_status = "" if step.calculation_status is None else step.calculation_status
            lines.append(
                "| "
                + " | ".join(
                    (
                        _md(step.step_key),
                        _md(step.scientific_state.value),
                        _md(step.readiness.value),
                        _md(calculation_status),
                        generation,
                        _md(", ".join(step.gate_reason_codes)),
                        _md(orchestration),
                    )
                )
                + " |"
            )
        if dashboard.edges:
            lines.extend(
                [
                    "",
                    "| Edge | Role | Verdict | Reasons |",
                    "| --- | --- | --- | --- |",
                ]
            )
            for edge in dashboard.edges:
                label = f"{edge.upstream_step_key} -> {edge.downstream_step_key}"
                lines.append(
                    f"| {_md(label)} | {_md(edge.role)} | {_md(edge.verdict.value)} | "
                    f"{_md(', '.join(edge.reason_codes))} |"
                )
        lines.append("")

    lines.extend(["## Scientific presentations", ""])
    if not report.presentations:
        lines.append("No scientific presentation datasets were supplied to this report.")
    for presentation in report.presentations:
        lines.extend(_presentation_markdown(presentation))

    return "\n".join(lines).rstrip() + "\n"


def _validate_inventory(
    *,
    bundle: ProjectBundle,
    inventory: WorkspaceScientificInventory,
) -> None:
    if inventory.project_id != bundle.project.id:
        raise ScientificReportError("report inventory belongs to another Project")
    expected_entity_ids = {
        _uuid_id(item) for item in bundle.provenance_entities()
    }
    actual_entity_ids = {item.entity_id for item in inventory.rows}
    if actual_entity_ids != expected_entity_ids:
        raise ScientificReportError(
            "report inventory does not cover the exact current project entity set"
        )
    expected_dependencies = {
        (
            item.id,
            item.upstream_id,
            item.downstream_id,
            item.kind,
            item.role,
            item.recorded_hash,
        )
        for item in bundle.dependency_records
    }
    actual_dependencies = {
        (
            item.dependency_id,
            item.upstream_id,
            item.downstream_id,
            item.kind,
            item.role,
            item.recorded_hash,
        )
        for item in inventory.dependencies
    }
    if actual_dependencies != expected_dependencies:
        raise ScientificReportError(
            "report inventory dependency projection does not match current project state"
        )


def _validate_readiness(
    *,
    bundle: ProjectBundle,
    dashboards: tuple[WorkflowReadinessDashboard, ...],
) -> None:
    ids = tuple(item.workflow_plan_id for item in dashboards)
    if len(ids) != len(set(ids)):
        raise ScientificReportError("workflow readiness dashboards must be unique by plan id")
    plan_by_id = {item.id: item for item in bundle.workflow_plans}
    for dashboard in dashboards:
        plan = plan_by_id.get(dashboard.workflow_plan_id)
        if plan is None:
            raise ScientificReportError("workflow readiness dashboard belongs to another Project")
        try:
            current = resolve_workflow_binding_generations(
                plan=plan,
                bindings=bundle.workflow_step_bindings,
                calculations=bundle.calculations,
            )
        except WorkflowGateError as error:
            raise ScientificReportError(str(error)) from error
        if tuple(item.step_key for item in dashboard.steps) != tuple(
            item.step_key for item in current
        ):
            raise ScientificReportError("workflow readiness step set is not current")
        for view, selection in zip(dashboard.steps, current, strict=True):
            current_binding_id = (
                None if selection.current_binding is None else selection.current_binding.id
            )
            current_generation = (
                None if selection.current_binding is None else selection.current_binding.generation
            )
            calculation_id = (
                None if selection.current_calculation is None else selection.current_calculation.id
            )
            if (
                view.current_binding_id != current_binding_id
                or view.current_generation != current_generation
                or view.calculation_id != calculation_id
                or view.superseded_binding_ids != selection.superseded_binding_ids
                or view.superseded_calculation_ids != selection.superseded_calculation_ids
            ):
                raise ScientificReportError(
                    "workflow readiness dashboard is stale relative to current generation history"
                )


def _validate_presentations(
    *,
    bundle: ProjectBundle,
    presentations: tuple[ScientificPresentation, ...],
) -> None:
    snapshot_by_id = {item.id: item for item in bundle.structure_snapshots}
    for presentation in presentations:
        if isinstance(presentation, StructurePresentationDataset):
            snapshot = snapshot_by_id.get(presentation.structure_snapshot_id)
            if snapshot is None:
                raise ScientificReportError("structure presentation belongs to another Project")
            if presentation.source_scientific_hash != scientific_hash(snapshot):
                raise ScientificReportError(
                    "structure presentation source hash does not match current StructureSnapshot"
                )
        elif isinstance(presentation, (DosPresentationDataset, CohpPresentationDataset)):
            if presentation.structure_snapshot_id not in snapshot_by_id:
                raise ScientificReportError(
                    "electronic presentation references a StructureSnapshot outside the Project"
                )
        elif isinstance(presentation, ReactionDiagramPresentationDataset):
            if presentation.project_id != bundle.project.id:
                raise ScientificReportError("reaction-diagram presentation belongs to another Project")
        else:
            raise ScientificReportError(
                f"unsupported scientific presentation type: {type(presentation).__name__}"
            )


def _report_payload(
    report: ScientificReport,
    *,
    include_report_hash: bool,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "contract_version": report.contract_version,
        "project": _projection_dict(report.projection),
        "inventory": _inventory_dict(report.inventory),
        "workflow_readiness": [_readiness_dict(item) for item in report.readiness],
        "presentations": [_presentation_dict(item) for item in report.presentations],
    }
    if include_report_hash:
        payload["report_hash"] = report.report_hash
    return payload


def _projection_dict(projection: WorkspaceProjection) -> dict[str, object]:
    counts = projection.entity_counts
    return {
        "project_id": str(projection.project_id),
        "project_name": projection.project_name,
        "project_slug": projection.project_slug,
        "schema_version": projection.schema_version,
        "description": projection.description,
        "created_at": projection.created_at.isoformat(),
        "projection_hash": projection.projection_hash,
        "entity_counts": {key: value for key, value in _entity_count_items(projection)},
        "statuses": {
            "calculations": [list(item) for item in projection.statuses.calculations],
            "analyses": [list(item) for item in projection.statuses.analyses],
            "execution_attempts": [list(item) for item in projection.statuses.execution_attempts],
            "scheduler_jobs": [list(item) for item in projection.statuses.scheduler_jobs],
        },
    }


def _inventory_dict(inventory: WorkspaceScientificInventory) -> dict[str, object]:
    return {
        "project_id": str(inventory.project_id),
        "rows": [_inventory_row_dict(item) for item in inventory.rows],
        "dependencies": [_dependency_dict(item) for item in inventory.dependencies],
    }


def _inventory_row_dict(row: object) -> dict[str, object]:
    from ecatvasp.workspace import WorkspaceInventoryRow

    if not isinstance(row, WorkspaceInventoryRow):
        raise ScientificReportError("invalid workspace inventory row")
    return {
        "entity_id": str(row.entity_id),
        "entity_kind": row.entity_kind.value,
        "display_label": row.display_label,
        "status_domain": None if row.status_domain is None else row.status_domain.value,
        "status": row.status,
        "current_scientific_hash": row.current_scientific_hash,
        "provenance": [
            {
                "provenance_id": str(item.provenance_id),
                "subject_id": str(item.subject_id),
                "tool": item.tool,
                "tool_version": item.tool_version,
                "parameters_hash": item.parameters_hash,
                "method_fingerprint_id": (
                    None if item.method_fingerprint_id is None else str(item.method_fingerprint_id)
                ),
                "created_at": item.created_at.isoformat(),
            }
            for item in row.provenance
        ],
        "incoming_dependencies": [_dependency_dict(item) for item in row.incoming_dependencies],
        "outgoing_dependencies": [_dependency_dict(item) for item in row.outgoing_dependencies],
        "scientific_ancestor_ids": [str(item) for item in row.scientific_ancestor_ids],
        "freshness": {
            "state": row.freshness.state.value,
            "reasons": [
                {
                    "code": item.code,
                    "upstream_id": None if item.upstream_id is None else str(item.upstream_id),
                    "dependency_id": (
                        None if item.dependency_id is None else str(item.dependency_id)
                    ),
                }
                for item in row.freshness.reasons
            ],
        },
        "attention_codes": list(row.attention_codes),
    }


def _dependency_dict(item: object) -> dict[str, object]:
    from ecatvasp.workspace import WorkspaceDependencyView

    if not isinstance(item, WorkspaceDependencyView):
        raise ScientificReportError("invalid workspace dependency view")
    return {
        "dependency_id": str(item.dependency_id),
        "upstream_id": str(item.upstream_id),
        "downstream_id": str(item.downstream_id),
        "kind": item.kind.value,
        "role": item.role,
        "recorded_hash": item.recorded_hash,
    }


def _readiness_dict(dashboard: WorkflowReadinessDashboard) -> dict[str, object]:
    return {
        "workflow_plan_id": str(dashboard.workflow_plan_id),
        "steps": [
            {
                "step_key": item.step_key,
                "scientific_state": item.scientific_state.value,
                "readiness": item.readiness.value,
                "gate_reason_codes": list(item.gate_reason_codes),
                "freshness_state": (
                    None if item.freshness_state is None else item.freshness_state.value
                ),
                "current_binding_id": (
                    None if item.current_binding_id is None else str(item.current_binding_id)
                ),
                "current_generation": item.current_generation,
                "calculation_id": (
                    None if item.calculation_id is None else str(item.calculation_id)
                ),
                "calculation_status": item.calculation_status,
                "superseded_binding_ids": [str(value) for value in item.superseded_binding_ids],
                "superseded_calculation_ids": [
                    str(value) for value in item.superseded_calculation_ids
                ],
                "execution_attempts": [
                    {
                        "execution_attempt_id": str(attempt.execution_attempt_id),
                        "calculation_id": str(attempt.calculation_id),
                        "attempt_number": attempt.attempt_number,
                        "status": attempt.status,
                        "previous_attempt_id": (
                            None
                            if attempt.previous_attempt_id is None
                            else str(attempt.previous_attempt_id)
                        ),
                        "remote_jobs": [
                            {
                                "remote_job_id": str(job.remote_job_id),
                                "execution_attempt_id": str(job.execution_attempt_id),
                                "scheduler": job.scheduler,
                                "scheduler_job_id": job.scheduler_job_id,
                                "state": job.state,
                                "remote_directory": job.remote_directory,
                            }
                            for job in attempt.remote_jobs
                        ],
                    }
                    for attempt in item.execution_attempts
                ],
                "orchestration_action": (
                    None
                    if item.orchestration_action is None
                    else item.orchestration_action.value
                ),
                "orchestration_reason_codes": list(item.orchestration_reason_codes),
            }
            for item in dashboard.steps
        ],
        "edges": [
            {
                "upstream_step_key": item.upstream_step_key,
                "downstream_step_key": item.downstream_step_key,
                "role": item.role,
                "verdict": item.verdict.value,
                "source_binding_id": (
                    None if item.source_binding_id is None else str(item.source_binding_id)
                ),
                "accepted_structure_snapshot_id": (
                    None
                    if item.accepted_structure_snapshot_id is None
                    else str(item.accepted_structure_snapshot_id)
                ),
                "reason_codes": list(item.reason_codes),
            }
            for item in dashboard.edges
        ],
    }


def _presentation_dict(presentation: ScientificPresentation) -> dict[str, object]:
    return {
        "kind": _presentation_kind(presentation),
        "payload": presentation.to_dict(),
    }


def _presentation_kind(presentation: ScientificPresentation) -> str:
    if isinstance(presentation, StructurePresentationDataset):
        return "structure"
    if isinstance(presentation, DosPresentationDataset):
        return "dos_pdos"
    if isinstance(presentation, CohpPresentationDataset):
        return "cohp_icohp"
    if isinstance(presentation, ReactionDiagramPresentationDataset):
        return "reaction_diagram"
    raise ScientificReportError(
        f"unsupported scientific presentation type: {type(presentation).__name__}"
    )


def _presentation_sort_key(presentation: ScientificPresentation) -> tuple[str, str, str]:
    if isinstance(presentation, StructurePresentationDataset):
        return (
            "structure",
            str(presentation.structure_snapshot_id),
            presentation.source_scientific_hash,
        )
    if isinstance(presentation, DosPresentationDataset):
        return (
            "dos_pdos",
            str(presentation.structure_snapshot_id),
            presentation.source_content_hash,
        )
    if isinstance(presentation, CohpPresentationDataset):
        return (
            "cohp_icohp",
            str(presentation.structure_snapshot_id),
            presentation.source_content_hash,
        )
    if isinstance(presentation, ReactionDiagramPresentationDataset):
        return (
            "reaction_diagram",
            str(presentation.project_id),
            presentation.source_result_hash,
        )
    raise ScientificReportError(
        f"unsupported scientific presentation type: {type(presentation).__name__}"
    )


def _presentation_markdown(presentation: ScientificPresentation) -> list[str]:
    if isinstance(presentation, StructurePresentationDataset):
        return [
            f"### Structure `{presentation.structure_snapshot_id}`",
            "",
            f"- Source scientific hash: `{presentation.source_scientific_hash}`",
            f"- MatterViz contract: `{presentation.matterviz.contract_version}`",
            f"- Interactive runtime available: `{str(presentation.matterviz.runtime.interactive_available).lower()}`",
            "",
        ]
    if isinstance(presentation, DosPresentationDataset):
        return [
            f"### DOS/PDOS `{presentation.structure_snapshot_id}`",
            "",
            f"- Source content hash: `{presentation.source_content_hash}`",
            f"- Energy reference: `{presentation.source_energy_reference.value}`",
            f"- Fermi energy: {presentation.fermi_energy_ev} {presentation.energy_unit}",
            f"- Units: energy={presentation.energy_unit}; density={presentation.density_unit}",
            f"- Series count: {len(presentation.series)}",
            "",
        ]
    if isinstance(presentation, CohpPresentationDataset):
        return [
            f"### COHP/ICOHP `{presentation.structure_snapshot_id}`",
            "",
            f"- Source content hash: `{presentation.source_content_hash}`",
            f"- Energy reference: `{presentation.energy_reference.value}`",
            f"- Sign convention: `{presentation.sign_convention}`",
            f"- Units: energy={presentation.energy_unit}; bond length={presentation.bond_length_unit}",
            f"- Interaction count: {len(presentation.interactions)}",
            "",
        ]
    if isinstance(presentation, ReactionDiagramPresentationDataset):
        conditions = presentation.requested_conditions
        lines = [
            f"### Reaction diagram `{presentation.source_result_hash}`",
            "",
            f"- Project ID: `{presentation.project_id}`",
            f"- Potential-view result hash: `{presentation.potential_view_result_hash}`",
            f"- Conditions: T={conditions.temperature_k} K; U={conditions.potential_v} {presentation.potential_unit} vs {conditions.potential_reference.upper()}; pH={conditions.ph}; semantics={conditions.ph_semantics}",
            f"- Free-energy unit: {presentation.free_energy_unit}",
            f"- States/steps: {len(presentation.states)}/{len(presentation.steps)}",
            "",
        ]
        if presentation.descriptors:
            lines.extend(
                [
                    "| Descriptor | Kind | Value | Unit | Source result hash |",
                    "| --- | --- | ---: | --- | --- |",
                ]
            )
            for item in presentation.descriptors:
                lines.append(
                    f"| {_md(item.key)} | {_md(item.kind)} | {item.value} | {_md(item.unit)} | "
                    f"`{item.source_result_hash}` |"
                )
            lines.append("")
        return lines
    raise ScientificReportError(
        f"unsupported scientific presentation type: {type(presentation).__name__}"
    )


def _entity_count_items(projection: WorkspaceProjection) -> tuple[tuple[str, int], ...]:
    counts = projection.entity_counts
    return (
        ("projects", counts.projects),
        ("catalysts", counts.catalysts),
        ("structure_variants", counts.structure_variants),
        ("structure_snapshots", counts.structure_snapshots),
        ("active_sites", counts.active_sites),
        ("adsorption_states", counts.adsorption_states),
        ("state_conformers", counts.state_conformers),
        ("method_fingerprints", counts.method_fingerprints),
        ("workflow_plans", counts.workflow_plans),
        ("calculations", counts.calculations),
        ("workflow_step_bindings", counts.workflow_step_bindings),
        ("execution_attempts", counts.execution_attempts),
        ("remote_jobs", counts.remote_jobs),
        ("artifacts", counts.artifacts),
        ("analyses", counts.analyses),
        ("provenance_records", counts.provenance_records),
        ("dependency_records", counts.dependency_records),
    )


def _status_rows(projection: WorkspaceProjection) -> tuple[tuple[str, str, int], ...]:
    groups = (
        ("calculation_scientific", projection.statuses.calculations),
        ("analysis", projection.statuses.analyses),
        ("execution_attempt", projection.statuses.execution_attempts),
        ("scheduler", projection.statuses.scheduler_jobs),
    )
    return tuple(
        (domain, status, count)
        for domain, values in groups
        for status, count in values
    )


def _dependency_token(item: object) -> str:
    from ecatvasp.workspace import WorkspaceDependencyView

    if not isinstance(item, WorkspaceDependencyView):
        raise ScientificReportError("invalid workspace dependency view")
    return (
        f"{item.dependency_id}:{item.kind.value}:{item.role}:"
        f"{item.upstream_id}->{item.downstream_id}"
    )


def _uuid_id(value: object) -> UUID:
    entity_id = getattr(value, "id", None)
    if not isinstance(entity_id, UUID):
        raise ScientificReportError("reportable project entities must expose UUID identity")
    return entity_id


def _join(values: object) -> str:
    if not hasattr(values, "__iter__"):
        raise ScientificReportError("report list field must be iterable")
    return ";".join(str(item) for item in values)  # type: ignore[arg-type]


def _md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _optional_code(value: object | None) -> str:
    return "" if value is None else f"`{value}`"
