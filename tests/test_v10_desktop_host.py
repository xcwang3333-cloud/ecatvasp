from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from dataclasses import replace
from io import StringIO
from pathlib import Path

from ecatvasp.desktop import (
    DESKTOP_HOST_CONTRACT_VERSION,
    DESKTOP_IPC_CONTRACT_VERSION,
    HOST_EXIT_BACKEND_FAILURE,
    HOST_EXIT_OK,
    DesktopOperation,
    DesktopRequest,
    DesktopResponse,
    run_stdio_host,
)
from ecatvasp.domain import Project
from ecatvasp.storage import ProjectBundle, ProjectStore


def _request_line(
    request_id: str,
    operation: DesktopOperation,
    project_root: Path | None = None,
) -> str:
    payload: dict[str, object] = {
        "protocol_version": DESKTOP_IPC_CONTRACT_VERSION,
        "request_id": request_id,
        "operation": operation.value,
    }
    if project_root is not None:
        payload["project_root"] = str(project_root)
    return json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"


def _project_store(root: Path) -> tuple[ProjectStore, Project]:
    project = Project(name="Desktop host project", slug="desktop-host-project")
    store = ProjectStore(root)
    store.save(ProjectBundle(project=project))
    return store, project


def test_stdio_host_empty_input_is_clean_graceful_eof() -> None:
    stdout = StringIO()
    stderr = StringIO()

    exit_code = run_stdio_host(StringIO(""), stdout, stderr)

    assert exit_code == HOST_EXIT_OK
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == ""


def test_stdio_host_health_is_first_request_readiness_handshake() -> None:
    stdout = StringIO()
    stderr = StringIO()

    exit_code = run_stdio_host(
        StringIO(_request_line("health-1", DesktopOperation.HEALTH)),
        stdout,
        stderr,
    )

    assert exit_code == HOST_EXIT_OK
    assert stderr.getvalue() == ""
    lines = stdout.getvalue().splitlines()
    assert len(lines) == 1
    response = json.loads(lines[0])
    assert response["protocol_version"] == DESKTOP_IPC_CONTRACT_VERSION
    assert response["request_id"] == "health-1"
    assert response["operation"] == "health"
    assert response["ok"] is True
    assert response["payload"]["stateless_project_requests"] is True


def test_stdio_host_isolates_malformed_line_and_continues_serving() -> None:
    stdin = StringIO("{not-json}\n" + _request_line("health-2", DesktopOperation.HEALTH))
    stdout = StringIO()
    stderr = StringIO()

    exit_code = run_stdio_host(stdin, stdout, stderr)

    assert exit_code == HOST_EXIT_OK
    assert stderr.getvalue() == ""
    frames = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert len(frames) == 2
    assert frames[0] == {
        "error": {
            "code": "invalid_request",
            "message": "desktop request is not valid JSON",
        },
        "frame_type": "host_error",
        "host_protocol_version": DESKTOP_HOST_CONTRACT_VERSION,
        "ok": False,
    }
    assert frames[1]["request_id"] == "health-2"
    assert frames[1]["ok"] is True


def test_stdio_host_restart_reopens_latest_project_store_state(tmp_path: Path) -> None:
    root = tmp_path / "project"
    store, project = _project_store(root)

    first_stdout = StringIO()
    first_stderr = StringIO()
    first_input = (
        _request_line("health-3", DesktopOperation.HEALTH)
        + _request_line("open-1", DesktopOperation.OPEN_PROJECT, root)
    )
    assert run_stdio_host(StringIO(first_input), first_stdout, first_stderr) == HOST_EXIT_OK
    assert first_stderr.getvalue() == ""
    first_frames = [json.loads(line) for line in first_stdout.getvalue().splitlines()]
    assert first_frames[0]["operation"] == "health"
    assert first_frames[1]["payload"]["project_name"] == "Desktop host project"

    store.save(ProjectBundle(project=replace(project, name="Desktop host project updated")))

    second_stdout = StringIO()
    second_stderr = StringIO()
    second_input = _request_line("status-1", DesktopOperation.STATUS, root)
    assert run_stdio_host(StringIO(second_input), second_stdout, second_stderr) == HOST_EXIT_OK
    assert second_stderr.getvalue() == ""
    second_response = json.loads(second_stdout.getvalue())
    assert second_response["payload"]["project_name"] == "Desktop host project updated"
    assert second_response["payload"]["project_id"] == str(project.id)


def test_desktop_module_entrypoint_uses_stdout_only_for_protocol_frames() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "ecatvasp.desktop"],
        input=_request_line("health-process", DesktopOperation.HEALTH),
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )

    assert completed.returncode == HOST_EXIT_OK
    assert completed.stderr == ""
    lines = completed.stdout.splitlines()
    assert len(lines) == 1
    response = json.loads(lines[0])
    assert response["request_id"] == "health-process"
    assert response["ok"] is True


def test_desktop_sidecar_console_script_adds_no_runtime_dependency() -> None:
    root = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    assert metadata["project"]["scripts"]["ecatvasp-desktop-backend"] == (
        "ecatvasp.desktop.host:main"
    )
    assert metadata["project"]["dependencies"] == ["ase>=3.29,<4", "numpy>=1.26"]


class _ExplodingBackend:
    def handle(self, request: DesktopRequest) -> DesktopResponse:
        raise RuntimeError(f"private failure for {request.request_id}")


def test_unexpected_backend_failure_exits_nonzero_without_fake_response() -> None:
    stdout = StringIO()
    stderr = StringIO()

    exit_code = run_stdio_host(
        StringIO(_request_line("fatal-1", DesktopOperation.HEALTH)),
        stdout,
        stderr,
        backend=_ExplodingBackend(),
    )

    assert exit_code == HOST_EXIT_BACKEND_FAILURE
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == (
        "ecatvasp-desktop-backend: fatal backend failure: RuntimeError\n"
    )
    assert "private failure" not in stderr.getvalue()
