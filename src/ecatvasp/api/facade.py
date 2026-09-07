"""Versioned headless Python facade over the Block 6 application service."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import UUID

from ecatvasp.api.application import (
    ApplicationReportFormat,
    ApplicationReportResult,
    ProjectApplicationService,
)
from ecatvasp.storage import ProjectStore
from ecatvasp.workspace import (
    WorkspaceDependencyView,
    WorkspaceFreshnessReasonView,
    WorkspaceInventoryRow,
    WorkspaceProvenanceView,
)

HEADLESS_CONTRACT_VERSION = "ecatvasp-headless-project-v1"


@dataclass(frozen=True, slots=True)
class HeadlessFreshnessReason:
    """JSON-compatible exact freshness reason."""

    code: str
    upstream_id: str | None
    dependency_id: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class HeadlessProvenanceRecord:
    """JSON-compatible provenance receipt."""

    provenance_id: str
    subject_id: str
    tool: str
    tool_version: str
    parameters_hash: str | None
    method_fingerprint_id: str | None
    created_at: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class HeadlessDependencyRecord:
    """JSON-compatible exact dependency edge."""

    dependency_id: str
    upstream_id: str
    downstream_id: str
    kind: str
    role: str
    recorded_hash: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class HeadlessInventoryRow:
    """Serialized Block 2 inventory row without scientific reinterpretation."""

    entity_id: str
    entity_kind: str
    display_label: str
    status_domain: str | None
    status: str | None
    current_scientific_hash: str | None
    provenance: tuple[HeadlessProvenanceRecord, ...]
    incoming_dependencies: tuple[HeadlessDependencyRecord, ...]
    outgoing_dependencies: tuple[HeadlessDependencyRecord, ...]
    scientific_ancestor_ids: tuple[str, ...]
    freshness_state: str
    freshness_reasons: tuple[HeadlessFreshnessReason, ...]
    attention_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "entity_id": self.entity_id,
            "entity_kind": self.entity_kind,
            "display_label": self.display_label,
            "status_domain": self.status_domain,
            "status": self.status,
            "current_scientific_hash": self.current_scientific_hash,
            "provenance": [item.to_dict() for item in self.provenance],
            "incoming_dependencies": [
                item.to_dict() for item in self.incoming_dependencies
            ],
            "outgoing_dependencies": [
                item.to_dict() for item in self.outgoing_dependencies
            ],
            "scientific_ancestor_ids": list(self.scientific_ancestor_ids),
            "freshness": {
                "state": self.freshness_state,
                "reasons": [item.to_dict() for item in self.freshness_reasons],
            },
            "attention_codes": list(self.attention_codes),
        }


@dataclass(frozen=True, slots=True)
class HeadlessProjectInspection:
    """Complete deterministic headless inspection document for one reopened project."""

    project_id: str
    project_name: str
    project_slug: str
    schema_version: int
    description: str | None
    created_at: str
    projection_hash: str
    entity_counts: tuple[tuple[str, int], ...]
    lifecycle_statuses: tuple[tuple[str, tuple[tuple[str, int], ...]], ...]
    inventory: tuple[HeadlessInventoryRow, ...]
    contract_version: str = HEADLESS_CONTRACT_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "project": {
                "id": self.project_id,
                "name": self.project_name,
                "slug": self.project_slug,
                "schema_version": self.schema_version,
                "description": self.description,
                "created_at": self.created_at,
                "projection_hash": self.projection_hash,
            },
            "entity_counts": {key: value for key, value in self.entity_counts},
            "lifecycle_statuses": {
                domain: {status: count for status, count in values}
                for domain, values in self.lifecycle_statuses
            },
            "inventory": [item.to_dict() for item in self.inventory],
        }


@dataclass(frozen=True, slots=True)
class HeadlessAttentionRow:
    """Compact exact attention row reused by status clients."""

    entity_id: str
    entity_kind: str
    status_domain: str | None
    status: str | None
    freshness_state: str
    freshness_reason_codes: tuple[str, ...]
    attention_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "entity_id": self.entity_id,
            "entity_kind": self.entity_kind,
            "status_domain": self.status_domain,
            "status": self.status,
            "freshness_state": self.freshness_state,
            "freshness_reason_codes": list(self.freshness_reason_codes),
            "attention_codes": list(self.attention_codes),
        }


@dataclass(frozen=True, slots=True)
class HeadlessProjectStatus:
    """Compact status document that keeps lifecycle domains explicitly separate."""

    project_id: str
    project_slug: str
    calculation_scientific: tuple[tuple[str, int], ...]
    analysis: tuple[tuple[str, int], ...]
    execution_attempt: tuple[tuple[str, int], ...]
    scheduler: tuple[tuple[str, int], ...]
    attention: tuple[HeadlessAttentionRow, ...]
    contract_version: str = HEADLESS_CONTRACT_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "project_id": self.project_id,
            "project_slug": self.project_slug,
            "lifecycle_statuses": {
                "calculation_scientific": dict(self.calculation_scientific),
                "analysis": dict(self.analysis),
                "execution_attempt": dict(self.execution_attempt),
                "scheduler": dict(self.scheduler),
            },
            "attention": [item.to_dict() for item in self.attention],
        }


class HeadlessProjectFacade:
    """Dependency-light facade for headless clients and the Block 7 CLI."""

    def __init__(self, service: ProjectApplicationService) -> None:
        self._service = service

    @property
    def service(self) -> ProjectApplicationService:
        """Expose the typed Block 6 application service for Python clients."""

        return self._service

    def inspect(self) -> HeadlessProjectInspection:
        """Return a complete headless inspection document from current ProjectStore state."""

        result = self._service.inspect()
        projection = result.projection
        return HeadlessProjectInspection(
            project_id=str(projection.project_id),
            project_name=projection.project_name,
            project_slug=projection.project_slug,
            schema_version=projection.schema_version,
            description=projection.description,
            created_at=projection.created_at.isoformat(),
            projection_hash=projection.projection_hash,
            entity_counts=_entity_counts(projection.entity_counts),
            lifecycle_statuses=(
                ("calculation_scientific", projection.statuses.calculations),
                ("analysis", projection.statuses.analyses),
                ("execution_attempt", projection.statuses.execution_attempts),
                ("scheduler", projection.statuses.scheduler_jobs),
            ),
            inventory=tuple(_inventory_row(row) for row in result.inventory.rows),
        )

    def status(self) -> HeadlessProjectStatus:
        """Return compact separated lifecycle and attention state from a fresh inspection."""

        inspection = self.inspect()
        statuses = dict(inspection.lifecycle_statuses)
        attention = tuple(
            HeadlessAttentionRow(
                entity_id=row.entity_id,
                entity_kind=row.entity_kind,
                status_domain=row.status_domain,
                status=row.status,
                freshness_state=row.freshness_state,
                freshness_reason_codes=tuple(
                    reason.code for reason in row.freshness_reasons
                ),
                attention_codes=row.attention_codes,
            )
            for row in inspection.inventory
            if row.attention_codes or row.freshness_state != "fresh"
        )
        return HeadlessProjectStatus(
            project_id=inspection.project_id,
            project_slug=inspection.project_slug,
            calculation_scientific=statuses["calculation_scientific"],
            analysis=statuses["analysis"],
            execution_attempt=statuses["execution_attempt"],
            scheduler=statuses["scheduler"],
            attention=attention,
        )

    def report(
        self,
        *,
        format: ApplicationReportFormat | str,
    ) -> ApplicationReportResult:
        """Delegate deterministic scientific reporting to the Block 6 application service."""

        return self._service.report(format=format)


def open_project(root: Path | str) -> HeadlessProjectFacade:
    """Open an ECatVASP project root without adding any headless session state."""

    return HeadlessProjectFacade(ProjectApplicationService(ProjectStore(Path(root))))


def render_headless_json(
    document: HeadlessProjectInspection | HeadlessProjectStatus,
) -> str:
    """Render a deterministic UTF-8 JSON document."""

    return json.dumps(
        document.to_dict(),
        sort_keys=True,
        indent=2,
        ensure_ascii=False,
    ) + "\n"


def render_inspection_text(document: HeadlessProjectInspection) -> str:
    """Render a concise human-readable project inspection from the same headless document."""

    lines = [
        f"ECatVASP project: {document.project_slug} ({document.project_name})",
        f"Project ID: {document.project_id}",
        f"Schema version: {document.schema_version}",
        f"Projection hash: {document.projection_hash}",
        "Entities:",
    ]
    lines.extend(f"  {name}: {count}" for name, count in document.entity_counts)
    lines.append("Lifecycle status:")
    for domain, values in document.lifecycle_statuses:
        rendered = ", ".join(f"{state}={count}" for state, count in values) or "none"
        lines.append(f"  {domain}: {rendered}")
    lines.append(f"Inventory rows: {len(document.inventory)}")
    return "\n".join(lines) + "\n"


def render_status_text(document: HeadlessProjectStatus) -> str:
    """Render separated lifecycle namespaces and exact attention rows."""

    lines = [
        f"ECatVASP status: {document.project_slug}",
        f"Project ID: {document.project_id}",
    ]
    for domain, values in (
        ("calculation_scientific", document.calculation_scientific),
        ("analysis", document.analysis),
        ("execution_attempt", document.execution_attempt),
        ("scheduler", document.scheduler),
    ):
        rendered = ", ".join(f"{state}={count}" for state, count in values) or "none"
        lines.append(f"{domain}: {rendered}")
    lines.append(f"attention_rows: {len(document.attention)}")
    for row in document.attention:
        codes = ",".join(row.attention_codes) or "none"
        reasons = ",".join(row.freshness_reason_codes) or "none"
        lines.append(
            f"  {row.entity_kind}:{row.entity_id} freshness={row.freshness_state} "
            f"attention={codes} reasons={reasons}"
        )
    return "\n".join(lines) + "\n"


def _entity_counts(counts: object) -> tuple[tuple[str, int], ...]:
    values = asdict(counts)
    return tuple((key, int(value)) for key, value in values.items())


def _inventory_row(row: WorkspaceInventoryRow) -> HeadlessInventoryRow:
    return HeadlessInventoryRow(
        entity_id=str(row.entity_id),
        entity_kind=row.entity_kind.value,
        display_label=row.display_label,
        status_domain=None if row.status_domain is None else row.status_domain.value,
        status=row.status,
        current_scientific_hash=row.current_scientific_hash,
        provenance=tuple(_provenance(item) for item in row.provenance),
        incoming_dependencies=tuple(
            _dependency(item) for item in row.incoming_dependencies
        ),
        outgoing_dependencies=tuple(
            _dependency(item) for item in row.outgoing_dependencies
        ),
        scientific_ancestor_ids=tuple(str(item) for item in row.scientific_ancestor_ids),
        freshness_state=row.freshness.state.value,
        freshness_reasons=tuple(
            _freshness_reason(item) for item in row.freshness.reasons
        ),
        attention_codes=row.attention_codes,
    )


def _provenance(item: WorkspaceProvenanceView) -> HeadlessProvenanceRecord:
    return HeadlessProvenanceRecord(
        provenance_id=str(item.provenance_id),
        subject_id=str(item.subject_id),
        tool=item.tool,
        tool_version=item.tool_version,
        parameters_hash=item.parameters_hash,
        method_fingerprint_id=_optional_uuid(item.method_fingerprint_id),
        created_at=item.created_at.isoformat(),
    )


def _dependency(item: WorkspaceDependencyView) -> HeadlessDependencyRecord:
    return HeadlessDependencyRecord(
        dependency_id=str(item.dependency_id),
        upstream_id=str(item.upstream_id),
        downstream_id=str(item.downstream_id),
        kind=item.kind.value,
        role=item.role,
        recorded_hash=item.recorded_hash,
    )


def _freshness_reason(item: WorkspaceFreshnessReasonView) -> HeadlessFreshnessReason:
    return HeadlessFreshnessReason(
        code=item.code,
        upstream_id=_optional_uuid(item.upstream_id),
        dependency_id=_optional_uuid(item.dependency_id),
    )


def _optional_uuid(value: UUID | None) -> str | None:
    return None if value is None else str(value)
