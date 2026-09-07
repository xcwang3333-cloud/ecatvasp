"""Versioned stateless desktop IPC contract over existing Python authorities."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID

from ecatvasp import __version__
from ecatvasp.api import open_project
from ecatvasp.desktop.actions import (
    list_desktop_workflow_recipes,
    prepare_workflow_action,
    report_action,
)
from ecatvasp.desktop.workspace import build_desktop_frontend_handoff
from ecatvasp.frontend import FRONTEND_HANDOFF_CONTRACT_VERSION
from ecatvasp.storage import (
    MigrationPathError,
    ProjectIntegrityError,
    ProjectStorageError,
    ProjectStore,
    StorageCodecError,
    UnsupportedSchemaVersionError,
)

DESKTOP_IPC_CONTRACT_VERSION = "ecatvasp-desktop-ipc-v1"

_PROJECT_READ_ERRORS = (
    MigrationPathError,
    ProjectIntegrityError,
    ProjectStorageError,
    StorageCodecError,
    UnsupportedSchemaVersionError,
)
_REPORT_FORMATS = frozenset({"json", "csv", "markdown"})
_BASE_REQUEST_FIELDS = frozenset(
    {"protocol_version", "request_id", "operation", "project_root"}
)
_OPERATION_FIELDS: dict["DesktopOperation", frozenset[str]] = {}


class DesktopIPCError(ValueError):
    """Raised when a desktop IPC message violates the frozen transport contract."""


class DesktopOperation(StrEnum):
    """Explicit operations available across the local desktop boundary."""

    HEALTH = "health"
    OPEN_PROJECT = "open_project"
    STATUS = "status"
    FRONTEND_HANDOFF = "frontend_handoff"
    APPLICATION_REPORT = "application_report"
    PREPARE_WORKFLOW = "prepare_workflow"


_OPERATION_FIELDS.update(
    {
        DesktopOperation.HEALTH: frozenset(),
        DesktopOperation.OPEN_PROJECT: frozenset(),
        DesktopOperation.STATUS: frozenset(),
        DesktopOperation.FRONTEND_HANDOFF: frozenset(),
        DesktopOperation.APPLICATION_REPORT: frozenset({"report_format"}),
        DesktopOperation.PREPARE_WORKFLOW: frozenset(
            {
                "workflow_recipe_id",
                "workflow_recipe_version",
                "root_structure_snapshot_id",
                "parameters_hash",
            }
        ),
    }
)


@dataclass(frozen=True, slots=True)
class DesktopRequest:
    """One stateless request with operation-specific typed fields."""

    request_id: str
    operation: DesktopOperation
    project_root: str | None = None
    report_format: str | None = None
    workflow_recipe_id: str | None = None
    workflow_recipe_version: str | None = None
    root_structure_snapshot_id: str | None = None
    parameters_hash: str | None = None
    protocol_version: str = DESKTOP_IPC_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.protocol_version != DESKTOP_IPC_CONTRACT_VERSION:
            raise DesktopIPCError("unsupported desktop IPC contract version")
        if not self.request_id.strip():
            raise DesktopIPCError("request_id must not be blank")
        if not isinstance(self.operation, DesktopOperation):
            raise DesktopIPCError("operation must be a DesktopOperation")
        if self.operation is DesktopOperation.HEALTH:
            if self.project_root is not None:
                raise DesktopIPCError("health request must not include project_root")
        elif self.project_root is None or not self.project_root.strip():
            raise DesktopIPCError(f"{self.operation.value} request requires project_root")

        if self.operation is DesktopOperation.APPLICATION_REPORT:
            self._validate_report_request()
        elif self.operation is DesktopOperation.PREPARE_WORKFLOW:
            self._validate_prepare_workflow_request()
        else:
            self._forbid_action_fields()

    def _validate_report_request(self) -> None:
        if self.report_format not in _REPORT_FORMATS:
            raise DesktopIPCError("application_report requires a supported report_format")
        if any(
            value is not None
            for value in (
                self.workflow_recipe_id,
                self.workflow_recipe_version,
                self.root_structure_snapshot_id,
                self.parameters_hash,
            )
        ):
            raise DesktopIPCError("application_report must not include workflow fields")

    def _validate_prepare_workflow_request(self) -> None:
        if self.report_format is not None:
            raise DesktopIPCError("prepare_workflow must not include report_format")
        for name, value in (
            ("workflow_recipe_id", self.workflow_recipe_id),
            ("workflow_recipe_version", self.workflow_recipe_version),
            ("root_structure_snapshot_id", self.root_structure_snapshot_id),
        ):
            if value is None or not value.strip():
                raise DesktopIPCError(f"prepare_workflow requires {name}")
        assert self.root_structure_snapshot_id is not None
        try:
            UUID(self.root_structure_snapshot_id)
        except ValueError as error:
            raise DesktopIPCError(
                "root_structure_snapshot_id must be a UUID"
            ) from error
        if self.parameters_hash is not None and not _is_sha256(self.parameters_hash):
            raise DesktopIPCError("parameters_hash must be a SHA-256 digest")

    def _forbid_action_fields(self) -> None:
        if any(
            value is not None
            for value in (
                self.report_format,
                self.workflow_recipe_id,
                self.workflow_recipe_version,
                self.root_structure_snapshot_id,
                self.parameters_hash,
            )
        ):
            raise DesktopIPCError(
                f"{self.operation.value} request must not include application action fields"
            )

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "protocol_version": self.protocol_version,
            "request_id": self.request_id,
            "operation": self.operation.value,
        }
        for name in (
            "project_root",
            "report_format",
            "workflow_recipe_id",
            "workflow_recipe_version",
            "root_structure_snapshot_id",
            "parameters_hash",
        ):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
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

            if request.operation is DesktopOperation.APPLICATION_REPORT:
                assert request.report_format is not None
                receipt = report_action(
                    project_root=root,
                    report_format=request.report_format,
                )
                return self._success(
                    request,
                    {"project_root": str(root), **receipt.to_dict()},
                )

            if request.operation is DesktopOperation.PREPARE_WORKFLOW:
                assert request.workflow_recipe_id is not None
                assert request.workflow_recipe_version is not None
                assert request.root_structure_snapshot_id is not None
                receipt = prepare_workflow_action(
                    project_root=root,
                    workflow_recipe_id=request.workflow_recipe_id,
                    workflow_recipe_version=request.workflow_recipe_version,
                    root_structure_snapshot_id=request.root_structure_snapshot_id,
                    parameters_hash=request.parameters_hash,
                )
                return self._success(
                    request,
                    {"project_root": str(root), **receipt.to_dict()},
                )

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
                bundle = ProjectStore(root).open()
                handoff = build_desktop_frontend_handoff(bundle)
                return self._success(
                    request,
                    {
                        "project_root": str(root),
                        "handoff": handoff.to_dict(),
                    },
                )
        except _PROJECT_READ_ERRORS as error:
            return self._failure(
                request,
                code="project_unavailable",
                message=str(error),
            )
        except ValueError as error:
            return self._failure(
                request,
                code="application_rejected",
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

    protocol_version = raw.get("protocol_version")
    request_id = raw.get("request_id")
    operation = raw.get("operation")
    if not isinstance(protocol_version, str):
        raise DesktopIPCError("protocol_version must be a string")
    if not isinstance(request_id, str):
        raise DesktopIPCError("request_id must be a string")
    if not isinstance(operation, str):
        raise DesktopIPCError("operation must be a string")
    try:
        selected_operation = DesktopOperation(operation)
    except ValueError as error:
        raise DesktopIPCError("unsupported desktop operation") from error

    allowed = _BASE_REQUEST_FIELDS | _OPERATION_FIELDS[selected_operation]
    unknown = set(raw) - allowed
    if unknown:
        raise DesktopIPCError(
            f"{selected_operation.value} request contains unknown fields: {sorted(unknown)!r}"
        )

    project_root = _optional_string(raw, "project_root")
    report_format = _optional_string(raw, "report_format")
    workflow_recipe_id = _optional_string(raw, "workflow_recipe_id")
    workflow_recipe_version = _optional_string(raw, "workflow_recipe_version")
    root_structure_snapshot_id = _optional_string(raw, "root_structure_snapshot_id")
    parameters_hash = _optional_string(raw, "parameters_hash")
    return DesktopRequest(
        protocol_version=protocol_version,
        request_id=request_id,
        operation=selected_operation,
        project_root=project_root,
        report_format=report_format,
        workflow_recipe_id=workflow_recipe_id,
        workflow_recipe_version=workflow_recipe_version,
        root_structure_snapshot_id=root_structure_snapshot_id,
        parameters_hash=parameters_hash,
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
        "workflow_recipes": [
            recipe.to_dict() for recipe in list_desktop_workflow_recipes()
        ],
    }


def _pairs(values: tuple[tuple[str, int], ...]) -> list[list[object]]:
    return [[name, count] for name, count in values]


def _optional_string(raw: dict[str, Any], field_name: str) -> str | None:
    if field_name not in raw:
        return None
    value = raw[field_name]
    if not isinstance(value, str):
        raise DesktopIPCError(f"{field_name} must be a string when supplied")
    return value


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdefABCDEF" for character in value)
