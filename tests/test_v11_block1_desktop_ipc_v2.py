from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest

from ecatvasp import __version__
from ecatvasp.desktop import (
    DESKTOP_IPC_CONTRACT_VERSION,
    DESKTOP_IPC_V2_CONTRACT_VERSION,
    DesktopBackend,
    DesktopBackendV2,
    DesktopIPCError,
    DesktopOperation,
    DesktopRequest,
    DesktopV2HealthRequest,
    DesktopV2Operation,
    DesktopV2ProjectRequest,
    decode_desktop_v2_request,
    decode_versioned_desktop_request,
    run_stdio_host,
)
from ecatvasp.domain import Project
from ecatvasp.schema.version import SCHEMA_VERSION
from ecatvasp.storage import ProjectBundle, ProjectStore


def _store(root: Path, name: str) -> ProjectStore:
    store = ProjectStore(root)
    store.save(ProjectBundle(project=Project(name=name, slug=name.casefold().replace(" ", "-"))))
    return store


def test_v11_keeps_v1_operation_set_frozen_and_adds_separate_v2() -> None:
    assert DESKTOP_IPC_CONTRACT_VERSION == "ecatvasp-desktop-ipc-v1"
    assert [operation.value for operation in DesktopOperation] == [
        "health",
        "open_project",
        "status",
        "frontend_handoff",
        "application_report",
        "prepare_workflow",
    ]
    assert DESKTOP_IPC_V2_CONTRACT_VERSION == "ecatvasp-desktop-ipc-v2"
    assert [operation.value for operation in DesktopV2Operation] == [
        "health",
        "open_project",
        "status",
        "frontend_handoff",
        "application_report",
        "prepare_workflow",
        "project_dashboard",
        "model_catalog",
        "structure_presentation",
        "create_project",
        "create_catalyst",
        "build_graphene_model",
        "import_structure_model",
        "mutate_structure_model",
        "build_single_metal_site",
        "build_multi_metal_site",
        "create_active_site",
        "build_adsorbate_conformer",
        "calculation_catalog",
        "prepare_calculation_workflow",
        "materialize_calculation_step",
        "job_catalog",
        "prepare_execution",
        "submit_slurm_job",
        "refresh_slurm_job",
        "cancel_slurm_job",
        "retrieve_job_outputs",
        "result_catalog",
        "analyze_result",
        "promote_result_structure",
        "electronic_analysis_catalog",
        "materialize_dos_analysis",
        "electronic_analysis_view",
        "materialize_band_center",
        "thermochemistry_catalog",
        "materialize_harmonic_thermochemistry",
        "materialize_gas_reference",
        "thermochemistry_view",
        "reaction_preset_preview",
        "materialize_reaction_diagram",
    ]
    assert SCHEMA_VERSION == 3


def test_v2_decoder_returns_operation_specific_request_types_and_fails_closed() -> None:
    health = decode_desktop_v2_request(
        json.dumps(
            {
                "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
                "request_id": "health-v2",
                "operation": "health",
            }
        )
    )
    assert isinstance(health, DesktopV2HealthRequest)

    dashboard = decode_desktop_v2_request(
        json.dumps(
            {
                "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
                "request_id": "dashboard-v2",
                "operation": "project_dashboard",
                "project_root": "/project",
            }
        )
    )
    assert isinstance(dashboard, DesktopV2ProjectRequest)
    assert dashboard.operation is DesktopV2Operation.PROJECT_DASHBOARD

    with pytest.raises(DesktopIPCError, match="unknown fields"):
        decode_desktop_v2_request(
            json.dumps(
                {
                    "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
                    "request_id": "bad-v2",
                    "operation": "project_dashboard",
                    "project_root": "/project",
                    "scientific_override": {"converged": True},
                }
            )
        )

    with pytest.raises(DesktopIPCError, match="unsupported desktop operation"):
        decode_desktop_v2_request(
            json.dumps(
                {
                    "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
                    "request_id": "generic-v2",
                    "operation": "mutate_entity",
                    "project_root": "/project",
                }
            )
        )


def test_v1_and_v2_health_are_distinct_and_schema_remains_three() -> None:
    v1 = DesktopBackend().handle(
        DesktopRequest(request_id="health-v1", operation=DesktopOperation.HEALTH)
    )
    v2 = DesktopBackendV2().handle(DesktopV2HealthRequest(request_id="health-v2"))

    assert v1.ok is True and v1.payload is not None
    assert v2.ok is True and v2.payload is not None
    assert v1.payload["operations"] == [operation.value for operation in DesktopOperation]
    assert "supported_protocol_versions" not in v1.payload
    assert v2.payload["operations"] == [operation.value for operation in DesktopV2Operation]
    assert v2.payload["supported_protocol_versions"] == [
        DESKTOP_IPC_CONTRACT_VERSION,
        DESKTOP_IPC_V2_CONTRACT_VERSION,
    ]
    assert v1.payload["backend_version"] == v2.payload["backend_version"] == __version__
    assert SCHEMA_VERSION == 3


def test_project_dashboard_is_page_scoped_and_preserves_lifecycle_namespaces(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dashboard"
    _store(root, "Dashboard")
    response = DesktopBackendV2().handle(
        DesktopV2ProjectRequest(
            request_id="dashboard-1",
            operation=DesktopV2Operation.PROJECT_DASHBOARD,
            project_root=str(root),
        )
    )

    assert response.ok is True
    assert response.payload is not None
    dashboard = response.payload["dashboard"]
    assert isinstance(dashboard, dict)
    assert dashboard["schema_version"] == 3
    assert "model_counts" in dashboard
    assert "workflow_counts" in dashboard
    assert "calculations" in dashboard
    assert "analyses" in dashboard
    assert "execution_attempts" in dashboard
    assert "scheduler_jobs" in dashboard
    assert "freshness" in dashboard
    assert "handoff" not in dashboard
    assert "presentations" not in dashboard


def test_stdio_host_can_process_v1_and_v2_in_one_stateless_process(tmp_path: Path) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    _store(root_a, "Project A")
    _store(root_b, "Project B")
    requests = [
        {
            "protocol_version": DESKTOP_IPC_CONTRACT_VERSION,
            "request_id": "v1-health",
            "operation": "health",
        },
        {
            "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
            "request_id": "v2-dashboard-a",
            "operation": "project_dashboard",
            "project_root": str(root_a),
        },
        {
            "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
            "request_id": "v2-dashboard-b",
            "operation": "project_dashboard",
            "project_root": str(root_b),
        },
    ]
    stdin = StringIO("".join(json.dumps(request) + "\n" for request in requests))
    stdout = StringIO()
    stderr = StringIO()

    assert run_stdio_host(stdin, stdout, stderr) == 0
    assert stderr.getvalue() == ""
    responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [item["protocol_version"] for item in responses] == [
        DESKTOP_IPC_CONTRACT_VERSION,
        DESKTOP_IPC_V2_CONTRACT_VERSION,
        DESKTOP_IPC_V2_CONTRACT_VERSION,
    ]
    assert responses[1]["payload"]["dashboard"]["project_name"] == "Project A"
    assert responses[2]["payload"]["dashboard"]["project_name"] == "Project B"


def test_versioned_decoder_rejects_unknown_protocol_without_fallback() -> None:
    with pytest.raises(DesktopIPCError, match="unsupported desktop IPC contract version"):
        decode_versioned_desktop_request(
            json.dumps(
                {
                    "protocol_version": "ecatvasp-desktop-ipc-v99",
                    "request_id": "future",
                    "operation": "health",
                }
            )
        )
