"""Versioned stateless desktop IPC contract over the v0.9 application/frontend seams."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from ecatvasp import __version__
from ecatvasp.api import open_project
from ecatvasp.frontend import FRONTEND_HANDOFF_CONTRACT_VERSION
from ecatvasp.storage import ProjectStorageError, ProjectStore

DESKTOP_IPC_CONTRACT_VERSION = "ecatvasp-desktop-ipc-v1"


class DesktopIPCError(ValueError):
    """Raised when a desktop IPC message violates the frozen transport contract."""


class DesktopOperation(StrEnum):
    """Read-only Block 1 operations available across the local desktop boundary."""

    HEALTH = "health"
    OPEN_PROJECT = "open_project"
    STATUS = "status"
    FRONTEND_HANDOFF = "frontend_handoff"


@dataclass(frozen=True, slots=True)
class DesktopRequest:
    """One stateless local-backend request.

    Project-scoped requests always carry an explicit path. The backend does not retain a current
    project or any scientific/session state between requests.
    """

    request_id: str
    operation: DesktopOperation
    project_root: str | None = None
    protocol_version: str = DESKTOP_IPC_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.protocol_version != DESKTOP_IPC_CONTRACT_VERSION:
            raise DesktopIPCError("unsupported desktop IPC contract version")
        if not self.request_id.strip():
            raise DesktopIPCError("request_id must not be blank")
        if not isinstance(self.operation, DesktopOperation):
            raise DesktopIPCError("operation must be a DesktopOperation")
        if self.operation is DesktopOperation.HEALTH and self.project_root is not None:
            raise DesktopIPCError("health request must not include project_root")
        if (
            self.operation is not DesktopOperation.HEALTH
            and (self.project_root is None or not self.project_root.strip())
        ):
            raise DesktopIPCError(f"{self.operation.value} request requires project_root")

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "protocol_version": self.protocol_version,
            "request_id": self.request_id,
            "operation": self.operation.value,
        }
        if self.project_root is not None:
            payload["project_root"] = self.project_root
        return payload


@dataclass(frozen=True, slots=True)
class DesktopError:
    """Stable client-facing transport error without pretending scientific failure."""

    code: str
    message: str

    def __post_init__(self) -> None:
        if not self.code.strip() or not self.message.strip():
            raise DesktopIPCError("desktop error code and message must not be blank")

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class DesktopResponse:
    """One response envelope for the exact request id and operation."""

    request_id: str
    operation: DesktopOperation
    ok: bool
    payload: dict[str, object] | None = None
    error: DesktopError | None = None
    protocol_version: str = DESKTOP_IPC_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.protocol_version != DESKTOP_IPC_CONTRACT_VERSION:
            raise DesktopIPCError("unsupported desktop IPC contract version")
        if not self.request_id.strip():
            raise DesktopIPCError("request_id must not be blank")
        if not isinstance(self.operation, DesktopOperation):
            raise DesktopIPCError("operation must be a DesktopOperation")
        if self.ok and (self.payload is None or self.error is not None):
            raise DesktopIPCError("successful desktop response requires payload and no error")
        if not self.ok and (self.payload is not None or self.error is None):
            raise DesktopIPCError("failed desktop response requires error and no payload")

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "protocol_version": self.protocol_version,
            "request_id": self.request_id,
            "operation": self.operation.value,
            "ok": self.ok,
        }
        if self.payload is not None:
            result["payload"] = self.payload
        if self.error is not None:
            result["error"] = self.error.to_dict()
        return result


class DesktopBackend:
    """Stateless adapter from desktop requests to existing authoritative Python contracts."""

    def handle(self, request: DesktopRequest) -> DesktopResponse:
        """Handle one request without retaining project or active-session authority."""

        if request.operation is DesktopOperation.HEALTH:
            return self._success(request, _health_payload())

        assert request.project_root is not None
        root = Path(request.project_root)
        try:
            if request.operation is DesktopOperation.OPEN_PROJECT:
                bundle = ProjectStore(root).open()
                payload: dict[str, object] = {
                    "project_root": str(root),
                    "project_id": str(bundle.project.id),
                    "project_name": bundle.project.name,
                    "project_slug": bundle.project.slug,
                    "schema_version": bundle.project.schema_version,
                }
                return self._success(request, payload)

            facade = open_project(root)
            if request.operation is DesktopOperation.STATUS:
                status = facade.status()
                return self._success(
                    request,
                    {
                        "project_root": str(root),
                        "project_id": status.project_id,
                        "project_name": status.project_name,
                        "project_slug": status.project_slug,
                        "schema_version": status.schema_version,
                        "projection_hash": status.projection_hash,
                        "calculations": _pairs(status.calculations),
                        "analyses": _pairs(status.analyses),
                        "execution_attempts": _pairs(status.execution_attempts),
                        "scheduler_jobs": _pairs(status.scheduler_jobs),
                        "freshness": _pairs(status.freshness),
                        "attention_rows": status.attention_rows,
                    },
                )
            if request.operation is DesktopOperation.FRONTEND_HANDOFF:
                handoff = facade.frontend_handoff()
                return self._success(
                    request,
                    {
                        "project_root": str(root),
                        "handoff": handoff.to_dict(),
                    },
                )
        except ProjectStorageError as error:
            return self._failure(
                request,
                code="project_unavailable",
                message=str(error),
            )

        raise DesktopIPCError(f"unsupported desktop operation: {request.operation.value}")

    @staticmethod
    def _success(request: DesktopRequest, payload: dict[str, object]) -> DesktopResponse:
        return DesktopResponse(
            request_id=request.request_id,
            operation=request.operation,
            ok=True,
            payload=payload,
        )

    @staticmethod
    def _failure(
        request: DesktopRequest,
        *,
        code: str,
        message: str,
    ) -> DesktopResponse:
        return DesktopResponse(
            request_id=request.request_id,
            operation=request.operation,
            ok=False,
            error=DesktopError(code=code, message=message),
        )


def decode_desktop_request(line: str) -> DesktopRequest:
    """Decode one strict JSON object suitable for newline-delimited local IPC framing."""

    try:
        raw: Any = json.loads(line)
    except json.JSONDecodeError as error:
        raise DesktopIPCError("desktop request is not valid JSON") from error
    if not isinstance(raw, dict):
        raise DesktopIPCError("desktop request must be a JSON object")

    allowed = {"protocol_version", "request_id", "operation", "project_root"}
    unknown = set(raw) - allowed
    if unknown:
        raise DesktopIPCError(f"desktop request contains unknown fields: {sorted(unknown)!r}")

    protocol_version = raw.get("protocol_version")
    request_id = raw.get("request_id")
    operation = raw.get("operation")
    project_root = raw.get("project_root")
    if not isinstance(protocol_version, str):
        raise DesktopIPCError("protocol_version must be a string")
    if not isinstance(request_id, str):
        raise DesktopIPCError("request_id must be a string")
    if not isinstance(operation, str):
        raise DesktopIPCError("operation must be a string")
    if project_root is not None and not isinstance(project_root, str):
        raise DesktopIPCError("project_root must be a string when supplied")
    try:
        selected_operation = DesktopOperation(operation)
    except ValueError as error:
        raise DesktopIPCError("unsupported desktop operation") from error
    return DesktopRequest(
        protocol_version=protocol_version,
        request_id=request_id,
        operation=selected_operation,
        project_root=project_root,
    )


def encode_desktop_response(response: DesktopResponse) -> str:
    """Render one deterministic single-line JSON response for local process transports."""

    return (
        json.dumps(
            response.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
    )


def _health_payload() -> dict[str, object]:
    return {
        "backend_version": __version__,
        "frontend_handoff_contract_version": FRONTEND_HANDOFF_CONTRACT_VERSION,
        "operations": [operation.value for operation in DesktopOperation],
        "stateless_project_requests": True,
    }


def _pairs(values: tuple[tuple[str, int], ...]) -> list[list[object]]:
    return [[name, count] for name, count in values]
