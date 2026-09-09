"""Exercise the backend copied by the Windows NSIS installer through production IPC."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ecatvasp import __version__
from ecatvasp.desktop import (
    DESKTOP_IPC_CONTRACT_VERSION,
    DESKTOP_IPC_V2_CONTRACT_VERSION,
    DesktopOperation,
    DesktopV2Operation,
)
from ecatvasp.domain import Project
from ecatvasp.schema.version import SCHEMA_VERSION
from ecatvasp.storage import ProjectBundle, ProjectStore

_DESKTOP_EXE = "ecatvasp-desktop.exe"
_SIDECAR_EXE = "ecatvasp-desktop-backend.exe"
_HOST_PROTOCOL = "ecatvasp-desktop-host-v1"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("install_dir", type=Path)
    args = parser.parse_args()

    install_dir = args.install_dir.resolve()
    if not install_dir.is_dir():
        raise RuntimeError(f"installed desktop directory is missing: {install_dir}")

    desktop = _unique_installed_file(install_dir, _DESKTOP_EXE)
    sidecar = _unique_installed_file(install_dir, _SIDECAR_EXE)
    _assert_within_install_dir(install_dir, desktop)
    _assert_within_install_dir(install_dir, sidecar)

    with TemporaryDirectory(prefix="ecatvasp-installed-e2e-") as directory:
        project_root = Path(directory) / "schema 3 project with spaces"
        project = Project(name="Installed acceptance", slug="installed-acceptance")
        ProjectStore(project_root).save(ProjectBundle(project=project))

        process = _start(sidecar)
        try:
            v1 = _exchange(
                process,
                {
                    "protocol_version": DESKTOP_IPC_CONTRACT_VERSION,
                    "request_id": "installed-v1-health",
                    "operation": "health",
                },
            )
            _assert_v1_health(v1)

            v2 = _exchange(
                process,
                {
                    "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
                    "request_id": "installed-v2-health",
                    "operation": "health",
                },
            )
            _assert_v2_health(v2)

            dashboard = _exchange(
                process,
                {
                    "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
                    "request_id": "installed-dashboard",
                    "operation": "project_dashboard",
                    "project_root": str(project_root),
                },
            )
            _assert_dashboard(dashboard, project_id=str(project.id))

            invalid_field = _exchange_uncorrelated(
                process,
                {
                    "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
                    "request_id": "installed-invalid-field",
                    "operation": "health",
                    "scientific_override": {"converged": True},
                },
            )
            _assert_host_error(invalid_field, "unknown fields")

            generic_mutation = _exchange_uncorrelated(
                process,
                {
                    "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
                    "request_id": "installed-generic-mutation",
                    "operation": "mutate_entity",
                    "project_root": str(project_root),
                },
            )
            _assert_host_error(generic_mutation, "unsupported desktop operation")

            recovered = _exchange(
                process,
                {
                    "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
                    "request_id": "installed-v2-health-after-rejection",
                    "operation": "health",
                },
            )
            _assert_v2_health(recovered)
        finally:
            _finish(process)

        restarted = _start(sidecar)
        try:
            dashboard_after_restart = _exchange(
                restarted,
                {
                    "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
                    "request_id": "installed-dashboard-after-restart",
                    "operation": "project_dashboard",
                    "project_root": str(project_root),
                },
            )
            _assert_dashboard(dashboard_after_restart, project_id=str(project.id))
            _assert_v2_health(
                _exchange(
                    restarted,
                    {
                        "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
                        "request_id": "installed-v2-health-after-restart",
                        "operation": "health",
                    },
                )
            )
        finally:
            _finish(restarted)

        reopened = ProjectStore(project_root).open()
        if reopened.project.id != project.id:
            raise RuntimeError("installed sidecar changed permanent Project identity")
        if reopened.schema_version != SCHEMA_VERSION:
            raise RuntimeError("installed acceptance project schema drifted")

    print(f"Installed desktop: {desktop}")
    print(f"Installed backend: {sidecar}")
    return 0


def _unique_installed_file(install_dir: Path, name: str) -> Path:
    matches = [path.resolve() for path in install_dir.rglob(name) if path.is_file()]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one installed {name}, found {len(matches)} under {install_dir}"
        )
    return matches[0]


def _assert_within_install_dir(install_dir: Path, path: Path) -> None:
    try:
        path.relative_to(install_dir)
    except ValueError as error:
        raise RuntimeError(f"installed artifact escaped install directory: {path}") from error


def _start(sidecar: Path) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [str(sidecar)],
        cwd=sidecar.parent,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )


def _finish(process: subprocess.Popen[str]) -> None:
    if process.stdin is not None:
        process.stdin.close()
    return_code = process.wait(timeout=30)
    stderr = "" if process.stderr is None else process.stderr.read()
    if return_code != 0:
        raise RuntimeError(f"installed backend exited with {return_code}: {stderr.strip()}")
    if stderr.strip():
        raise RuntimeError(f"installed backend wrote unexpected stderr: {stderr.strip()}")


def _write_and_read(
    process: subprocess.Popen[str], request: dict[str, object]
) -> dict[str, Any]:
    if process.stdin is None or process.stdout is None:
        raise RuntimeError("installed backend stdio pipes are unavailable")
    process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
    process.stdin.flush()
    line = process.stdout.readline()
    if not line:
        raise RuntimeError("installed backend closed stdout before responding")
    response = json.loads(line)
    if not isinstance(response, dict):
        raise RuntimeError("installed backend response is not a JSON object")
    return response


def _exchange(
    process: subprocess.Popen[str], request: dict[str, object]
) -> dict[str, Any]:
    response = _write_and_read(process, request)
    if response.get("request_id") != request["request_id"]:
        raise RuntimeError("installed backend response request_id mismatch")
    if response.get("operation") != request["operation"]:
        raise RuntimeError("installed backend response operation mismatch")
    if response.get("protocol_version") != request["protocol_version"]:
        raise RuntimeError("installed backend response protocol_version mismatch")
    return response


def _exchange_uncorrelated(
    process: subprocess.Popen[str], request: dict[str, object]
) -> dict[str, Any]:
    return _write_and_read(process, request)


def _payload(response: dict[str, Any]) -> dict[str, Any]:
    if response.get("ok") is not True:
        raise RuntimeError(f"installed backend operation failed: {response!r}")
    payload = response.get("payload")
    if not isinstance(payload, dict):
        raise RuntimeError("installed backend response payload is invalid")
    return payload


def _assert_v1_health(response: dict[str, Any]) -> None:
    payload = _payload(response)
    if response.get("protocol_version") != DESKTOP_IPC_CONTRACT_VERSION:
        raise RuntimeError("installed backend v1 protocol drifted")
    if payload.get("backend_version") != __version__:
        raise RuntimeError("installed backend package version mismatch")
    if payload.get("operations") != [operation.value for operation in DesktopOperation]:
        raise RuntimeError("installed backend v1 operation set drifted")
    if payload.get("stateless_project_requests") is not True:
        raise RuntimeError("installed backend v1 stateless request contract drifted")


def _assert_v2_health(response: dict[str, Any]) -> None:
    payload = _payload(response)
    if response.get("protocol_version") != DESKTOP_IPC_V2_CONTRACT_VERSION:
        raise RuntimeError("installed backend v2 protocol drifted")
    if payload.get("backend_version") != __version__:
        raise RuntimeError("installed backend package version mismatch")
    if payload.get("operations") != [operation.value for operation in DesktopV2Operation]:
        raise RuntimeError("installed backend v2 operation set drifted")
    if payload.get("supported_protocol_versions") != [
        DESKTOP_IPC_CONTRACT_VERSION,
        DESKTOP_IPC_V2_CONTRACT_VERSION,
    ]:
        raise RuntimeError("installed backend supported protocol set drifted")
    if payload.get("stateless_project_requests") is not True:
        raise RuntimeError("installed backend v2 stateless request contract drifted")


def _assert_dashboard(response: dict[str, Any], *, project_id: str) -> None:
    payload = _payload(response)
    dashboard = payload.get("dashboard")
    if not isinstance(dashboard, dict):
        raise RuntimeError("installed backend project dashboard is missing")
    if dashboard.get("project_id") != project_id:
        raise RuntimeError("installed backend dashboard belongs to the wrong Project")
    if dashboard.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError("installed backend dashboard schema mismatch")
    for namespace in (
        "model_counts",
        "workflow_counts",
        "calculations",
        "analyses",
        "execution_attempts",
        "scheduler_jobs",
        "freshness",
    ):
        if namespace not in dashboard:
            raise RuntimeError(f"installed backend dashboard is missing {namespace}")


def _assert_host_error(response: dict[str, Any], message_fragment: str) -> None:
    if response.get("host_protocol_version") != _HOST_PROTOCOL:
        raise RuntimeError("installed backend host error protocol drifted")
    if response.get("frame_type") != "host_error" or response.get("ok") is not False:
        raise RuntimeError("installed backend did not emit a fail-closed host error")
    error = response.get("error")
    if not isinstance(error, dict) or error.get("code") != "invalid_request":
        raise RuntimeError("installed backend host error payload is invalid")
    message = error.get("message")
    if not isinstance(message, str) or message_fragment not in message:
        raise RuntimeError(
            f"installed backend host error did not contain {message_fragment!r}: {response!r}"
        )


if __name__ == "__main__":  # pragma: no cover - packaging entry point
    raise SystemExit(main())
