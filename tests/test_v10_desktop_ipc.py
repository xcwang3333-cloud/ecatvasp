from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from ecatvasp import __version__
from ecatvasp.desktop import (
    DESKTOP_IPC_CONTRACT_VERSION,
    DesktopBackend,
    DesktopIPCError,
    DesktopOperation,
    DesktopRequest,
    decode_desktop_request,
    encode_desktop_response,
)
from ecatvasp.domain import Project
from ecatvasp.frontend import FRONTEND_HANDOFF_CONTRACT_VERSION
from ecatvasp.schema import SCHEMA_VERSION
from ecatvasp.storage import ProjectBundle, ProjectStore


def _project_store(root: Path, *, name: str, slug: str) -> tuple[ProjectStore, Project]:
    project = Project(name=name, slug=slug)
    store = ProjectStore(root)
    store.save(ProjectBundle(project=project))
    return store, project


def _request(
    request_id: str,
    operation: DesktopOperation,
    root: Path | None = None,
) -> DesktopRequest:
    return DesktopRequest(
        request_id=request_id,
        operation=operation,
        project_root=None if root is None else str(root),
    )


def test_desktop_ipc_health_is_versioned_stateless_and_deterministic() -> None:
    backend = DesktopBackend()
    response = backend.handle(_request("health-1", DesktopOperation.HEALTH))

    assert response.ok is True
    assert response.payload == {
        "backend_version": "1.0.0.dev0",
        "frontend_handoff_contract_version": FRONTEND_HANDOFF_CONTRACT_VERSION,
        "operations": [operation.value for operation in DesktopOperation],
        "stateless_project_requests": True,
    }
    assert __version__ == "1.0.0.dev0"
    assert SCHEMA_VERSION == 3
    assert encode_desktop_response(response) == encode_desktop_response(response)
    assert encode_desktop_response(response).endswith("\n")
    assert "\n" not in encode_desktop_response(response)[:-1]


def test_desktop_request_codec_fails_closed_on_contract_drift() -> None:
    request = decode_desktop_request(
        json.dumps(
            {
                "protocol_version": DESKTOP_IPC_CONTRACT_VERSION,
                "request_id": "status-1",
                "operation": "status",
                "project_root": "/project",
            }
        )
    )
    assert request.operation is DesktopOperation.STATUS
    assert request.project_root == "/project"

    with pytest.raises(DesktopIPCError, match="unsupported desktop IPC contract version"):
        decode_desktop_request(
            json.dumps(
                {
                    "protocol_version": "ecatvasp-desktop-ipc-v2",
                    "request_id": "status-2",
                    "operation": "status",
                    "project_root": "/project",
                }
            )
        )
    with pytest.raises(DesktopIPCError, match="unsupported desktop operation"):
        decode_desktop_request(
            json.dumps(
                {
                    "protocol_version": DESKTOP_IPC_CONTRACT_VERSION,
                    "request_id": "status-3",
                    "operation": "mutate_anything",
                    "project_root": "/project",
                }
            )
        )
    with pytest.raises(DesktopIPCError, match="unknown fields"):
        decode_desktop_request(
            json.dumps(
                {
                    "protocol_version": DESKTOP_IPC_CONTRACT_VERSION,
                    "request_id": "status-4",
                    "operation": "status",
                    "project_root": "/project",
                    "scientific_override": True,
                }
            )
        )
    with pytest.raises(DesktopIPCError, match="health request must not include project_root"):
        DesktopRequest(
            request_id="health-2",
            operation=DesktopOperation.HEALTH,
            project_root="/project",
        )


def test_desktop_backend_reopens_project_and_does_not_retain_active_session(tmp_path: Path) -> None:
    root_a = tmp_path / "project-a"
    root_b = tmp_path / "project-b"
    store_a, project_a = _project_store(root_a, name="Project A", slug="project-a")
    _project_store(root_b, name="Project B", slug="project-b")
    backend = DesktopBackend()

    opened = backend.handle(_request("open-a", DesktopOperation.OPEN_PROJECT, root_a))
    assert opened.ok is True
    assert opened.payload is not None
    assert opened.payload["project_id"] == str(project_a.id)
    assert opened.payload["project_name"] == "Project A"
    assert opened.payload["schema_version"] == 3

    store_a.save(ProjectBundle(project=replace(project_a, name="Project A updated")))
    status_a = backend.handle(_request("status-a", DesktopOperation.STATUS, root_a))
    status_b = backend.handle(_request("status-b", DesktopOperation.STATUS, root_b))

    assert status_a.ok is True
    assert status_a.payload is not None
    assert status_a.payload["project_name"] == "Project A updated"
    assert status_b.ok is True
    assert status_b.payload is not None
    assert status_b.payload["project_name"] == "Project B"
    assert status_a.payload["project_id"] != status_b.payload["project_id"]


def test_desktop_frontend_handoff_reuses_existing_v09_contract(tmp_path: Path) -> None:
    root = tmp_path / "handoff-project"
    _project_store(root, name="Desktop handoff", slug="desktop-handoff")
    response = DesktopBackend().handle(
        _request("handoff-1", DesktopOperation.FRONTEND_HANDOFF, root)
    )

    assert response.ok is True
    encoded = json.loads(encode_desktop_response(response))
    assert encoded["protocol_version"] == DESKTOP_IPC_CONTRACT_VERSION
    assert encoded["payload"]["handoff"]["contract_version"] == FRONTEND_HANDOFF_CONTRACT_VERSION
    assert encoded["payload"]["handoff"]["report"]["projection"]["project_name"] == (
        "Desktop handoff"
    )


def test_desktop_missing_project_fails_closed_without_fake_payload(tmp_path: Path) -> None:
    response = DesktopBackend().handle(
        _request("missing-1", DesktopOperation.STATUS, tmp_path / "missing")
    )

    assert response.ok is False
    assert response.payload is None
    assert response.error is not None
    assert response.error.code == "project_unavailable"
    encoded = json.loads(encode_desktop_response(response))
    assert "payload" not in encoded
    assert encoded["error"]["code"] == "project_unavailable"
