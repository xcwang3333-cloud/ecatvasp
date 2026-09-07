"""Smoke the frozen Windows backend through its real stdio protocol."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ecatvasp import __version__
from ecatvasp.desktop import DESKTOP_IPC_CONTRACT_VERSION
from ecatvasp.domain import Project
from ecatvasp.schema.version import SCHEMA_VERSION
from ecatvasp.storage import ProjectBundle, ProjectStore


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("sidecar", type=Path)
    args = parser.parse_args()
    sidecar = args.sidecar.resolve()
    if not sidecar.is_file():
        raise RuntimeError(f"frozen backend is missing: {sidecar}")

    with TemporaryDirectory(prefix="ecatvasp-packaging-smoke-") as directory:
        project_root = Path(directory) / "project"
        project = Project(name="Packaged smoke", slug="packaged-smoke")
        ProjectStore(project_root).save(ProjectBundle(project=project))

        process = subprocess.Popen(
            [str(sidecar)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        try:
            health = _exchange(
                process,
                {
                    "protocol_version": DESKTOP_IPC_CONTRACT_VERSION,
                    "request_id": "packaging-health",
                    "operation": "health",
                },
            )
            _assert_health(health)
            opened = _exchange(
                process,
                {
                    "protocol_version": DESKTOP_IPC_CONTRACT_VERSION,
                    "request_id": "packaging-open",
                    "operation": "open_project",
                    "project_root": str(project_root),
                },
            )
            _assert_project(opened, project_id=str(project.id))
        finally:
            if process.stdin is not None:
                process.stdin.close()

        return_code = process.wait(timeout=30)
        stderr = "" if process.stderr is None else process.stderr.read()
        if return_code != 0:
            raise RuntimeError(
                f"frozen backend exited with {return_code}: {stderr.strip()}"
            )
        if stderr.strip():
            raise RuntimeError(f"frozen backend wrote unexpected stderr: {stderr.strip()}")
    return 0


def _exchange(
    process: subprocess.Popen[str],
    request: dict[str, object],
) -> dict[str, Any]:
    if process.stdin is None or process.stdout is None:
        raise RuntimeError("frozen backend stdio pipes are unavailable")
    process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
    process.stdin.flush()
    line = process.stdout.readline()
    if not line:
        raise RuntimeError("frozen backend closed stdout before responding")
    response = json.loads(line)
    if not isinstance(response, dict):
        raise RuntimeError("frozen backend response is not a JSON object")
    if response.get("request_id") != request["request_id"]:
        raise RuntimeError("frozen backend response request_id mismatch")
    if response.get("operation") != request["operation"]:
        raise RuntimeError("frozen backend response operation mismatch")
    return response


def _assert_health(response: dict[str, Any]) -> None:
    if response.get("ok") is not True:
        raise RuntimeError(f"frozen backend health failed: {response!r}")
    payload = response.get("payload")
    if not isinstance(payload, dict):
        raise RuntimeError("frozen backend health payload is invalid")
    if response.get("protocol_version") != DESKTOP_IPC_CONTRACT_VERSION:
        raise RuntimeError("frozen backend IPC version mismatch")
    if payload.get("backend_version") != __version__:
        raise RuntimeError("frozen backend package version mismatch")
    if payload.get("stateless_project_requests") is not True:
        raise RuntimeError("frozen backend must preserve stateless project requests")
    operations = payload.get("operations")
    if not isinstance(operations, list) or "open_project" not in operations:
        raise RuntimeError("frozen backend does not advertise open_project")


def _assert_project(response: dict[str, Any], *, project_id: str) -> None:
    if response.get("ok") is not True:
        raise RuntimeError(f"frozen backend project open failed: {response!r}")
    payload = response.get("payload")
    if not isinstance(payload, dict):
        raise RuntimeError("frozen backend project payload is invalid")
    if payload.get("project_id") != project_id:
        raise RuntimeError("frozen backend opened the wrong Project")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError("frozen backend ProjectStore schema mismatch")


if __name__ == "__main__":  # pragma: no cover - packaging entry point
    raise SystemExit(main())
