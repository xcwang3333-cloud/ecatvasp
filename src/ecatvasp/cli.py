"""Headless command-line interface over the v0.9 Python application facade."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from ecatvasp.api import ApplicationReportFormat, ApplicationServiceError, open_project
from ecatvasp.storage import ProjectStorageError


def build_parser() -> argparse.ArgumentParser:
    """Build the stable Block 7 CLI parser without creating application state."""

    parser = argparse.ArgumentParser(
        prog="ecatvasp",
        description="ECatVASP headless project inspection and scientific reporting",
    )
    parser.add_argument(
        "--project",
        required=True,
        type=Path,
        help="Path to an existing ECatVASP ProjectStore directory",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="Inspect current project inventory")
    inspect_parser.add_argument("--json", action="store_true", help="Emit deterministic JSON")

    status_parser = subparsers.add_parser("status", help="Show lifecycle and freshness status")
    status_parser.add_argument("--json", action="store_true", help="Emit deterministic JSON")

    report_parser = subparsers.add_parser("report", help="Render a deterministic scientific report")
    report_parser.add_argument(
        "--format",
        choices=tuple(item.value for item in ApplicationReportFormat),
        default=ApplicationReportFormat.MARKDOWN.value,
        help="Report rendering format",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run one CLI command and return a process-style exit code."""

    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    parser = build_parser()
    args = parser.parse_args(argv)
    facade = open_project(args.project)

    try:
        if args.command == "inspect":
            inspection = facade.inspect()
            text = (
                _inspection_json(inspection)
                if args.json
                else _inspection_text(inspection)
            )
        elif args.command == "status":
            status = facade.status()
            text = _status_json(status) if args.json else _status_text(status)
        elif args.command == "report":
            result = facade.report(format=ApplicationReportFormat(args.format))
            text = result.content
        else:  # pragma: no cover - argparse guarantees the command set.
            parser.error(f"unsupported command: {args.command}")
            return 2
    except (ApplicationServiceError, ProjectStorageError) as error:
        errors.write(f"ecatvasp: error: {error}\n")
        return 2

    output.write(text)
    if text and not text.endswith("\n"):
        output.write("\n")
    return 0


def _inspection_json(inspection: object) -> str:
    from ecatvasp.api import ApplicationInspectionResult

    if not isinstance(inspection, ApplicationInspectionResult):
        raise TypeError("inspection must be ApplicationInspectionResult")
    projection = inspection.projection
    payload = {
        "project": {
            "id": str(projection.project_id),
            "name": projection.project_name,
            "slug": projection.project_slug,
            "schema_version": projection.schema_version,
            "projection_hash": projection.projection_hash,
        },
        "entity_counts": {
            name: value
            for name, value in _entity_count_items(projection.entity_counts)
        },
        "lifecycle_status": {
            "calculations": list(projection.statuses.calculations),
            "analyses": list(projection.statuses.analyses),
            "execution_attempts": list(projection.statuses.execution_attempts),
            "scheduler_jobs": list(projection.statuses.scheduler_jobs),
        },
        "inventory": [
            {
                "entity_id": str(row.entity_id),
                "entity_kind": row.entity_kind.value,
                "label": row.display_label,
                "status_domain": None if row.status_domain is None else row.status_domain.value,
                "status": row.status,
                "freshness": row.freshness.state.value,
                "freshness_reasons": [reason.code for reason in row.freshness.reasons],
                "attention_codes": list(row.attention_codes),
            }
            for row in inspection.inventory.rows
        ],
    }
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _inspection_text(inspection: object) -> str:
    from ecatvasp.api import ApplicationInspectionResult

    if not isinstance(inspection, ApplicationInspectionResult):
        raise TypeError("inspection must be ApplicationInspectionResult")
    projection = inspection.projection
    attention = sum(1 for row in inspection.inventory.rows if row.attention_codes)
    return (
        f"Project: {projection.project_name} ({projection.project_slug})\n"
        f"Project ID: {projection.project_id}\n"
        f"Schema: {projection.schema_version}\n"
        f"Projection hash: {projection.projection_hash}\n"
        f"Inventory rows: {len(inspection.inventory.rows)}\n"
        f"Attention rows: {attention}\n"
    )


def _status_json(status: object) -> str:
    from ecatvasp.api import HeadlessProjectStatus

    if not isinstance(status, HeadlessProjectStatus):
        raise TypeError("status must be HeadlessProjectStatus")
    payload = {
        "project_id": status.project_id,
        "project_name": status.project_name,
        "project_slug": status.project_slug,
        "schema_version": status.schema_version,
        "projection_hash": status.projection_hash,
        "calculations": list(status.calculations),
        "analyses": list(status.analyses),
        "execution_attempts": list(status.execution_attempts),
        "scheduler_jobs": list(status.scheduler_jobs),
        "freshness": list(status.freshness),
        "attention_rows": status.attention_rows,
    }
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _status_text(status: object) -> str:
    from ecatvasp.api import HeadlessProjectStatus

    if not isinstance(status, HeadlessProjectStatus):
        raise TypeError("status must be HeadlessProjectStatus")
    lines = [
        f"Project: {status.project_name} ({status.project_slug})",
        f"Project ID: {status.project_id}",
        f"Schema: {status.schema_version}",
        f"Calculations: {_format_counts(status.calculations)}",
        f"Analyses: {_format_counts(status.analyses)}",
        f"Execution attempts: {_format_counts(status.execution_attempts)}",
        f"Scheduler jobs: {_format_counts(status.scheduler_jobs)}",
        f"Freshness: {_format_counts(status.freshness)}",
        f"Attention rows: {status.attention_rows}",
    ]
    return "\n".join(lines) + "\n"


def _format_counts(counts: tuple[tuple[str, int], ...]) -> str:
    return "none" if not counts else ", ".join(f"{name}={count}" for name, count in counts)


def _entity_count_items(counts: object) -> tuple[tuple[str, int], ...]:
    from ecatvasp.workspace import WorkspaceEntityCounts

    if not isinstance(counts, WorkspaceEntityCounts):
        raise TypeError("counts must be WorkspaceEntityCounts")
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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
