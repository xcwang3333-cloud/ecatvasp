"""v1.1 task-oriented desktop IPC v2 without changing the frozen v1 contract."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias, TypeGuard
from uuid import UUID

from ecatvasp import __version__
from ecatvasp.api import open_project
from ecatvasp.desktop.actions import (
    list_desktop_workflow_recipes,
    prepare_workflow_action,
    report_action,
)
from ecatvasp.desktop.model_studio import (
    build_adsorbate_conformer_action,
    build_graphene_action,
    build_multi_metal_action,
    build_single_metal_action,
    create_active_site_action,
    create_catalyst_action,
    create_project_action,
    import_structure_action,
    model_catalog_action,
    mutate_structure_action,
    structure_presentation_action,
)
from ecatvasp.desktop.protocol import (
    DESKTOP_IPC_CONTRACT_VERSION,
    DesktopError,
    DesktopIPCError,
)
from ecatvasp.desktop.protocol_v2_common import (
    DESKTOP_IPC_V2_CONTRACT_VERSION,
    DesktopV2Operation,
)
from ecatvasp.desktop.protocol_v2_model import (
    DesktopV2BuildAdsorbateConformerRequest,
    DesktopV2BuildGrapheneRequest,
    DesktopV2BuildMultiMetalRequest,
    DesktopV2BuildSingleMetalRequest,
    DesktopV2CreateActiveSiteRequest,
    DesktopV2CreateCatalystRequest,
    DesktopV2CreateProjectRequest,
    DesktopV2ImportStructureRequest,
    DesktopV2ModelRequest,
    DesktopV2MutateStructureRequest,
    DesktopV2StructurePresentationRequest,
    decode_desktop_v2_model_request,
    is_desktop_v2_model_request,
)
from ecatvasp.desktop.workspace import (
    build_desktop_frontend_handoff,
    build_desktop_project_dashboard,
)
from ecatvasp.frontend import FRONTEND_HANDOFF_CONTRACT_VERSION
from ecatvasp.storage import (
    MigrationPathError,
    ProjectIntegrityError,
    ProjectStorageError,
    ProjectStore,
    StorageCodecError,
    UnsupportedSchemaVersionError,
)

_PROJECT_READ_ERRORS = (
    MigrationPathError,
    ProjectIntegrityError,
    ProjectStorageError,
    StorageCodecError,
    UnsupportedSchemaVersionError,
)
_REPORT_FORMATS = frozenset({"json", "csv", "markdown"})
_BASE_FIELDS = frozenset({"protocol_version", "request_id", "operation"})
_PROJECT_FIELDS = _BASE_FIELDS | {"project_root"}
_PROJECT_READ_OPERATIONS = frozenset(
    {
        DesktopV2Operation.OPEN_PROJECT,
        DesktopV2Operation.STATUS,
        DesktopV2Operation.FRONTEND_HANDOFF,
        DesktopV2Operation.PROJECT_DASHBOARD,
        DesktopV2Operation.MODEL_CATALOG,
    }
)


@dataclass(frozen=True, slots=True)
class DesktopV2HealthRequest:
    """Versioned v2 health request with no project/session authority."""

    request_id: str
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.HEALTH

    def __post_init__(self) -> None:
        _validate_common(self.protocol_version, self.request_id)
        if self.operation is not DesktopV2Operation.HEALTH:
            raise DesktopIPCError("v2 health request operation is invalid")

    def to_dict(self) -> dict[str, object]:
        return _base_payload(self.protocol_version, self.request_id, self.operation)


@dataclass(frozen=True, slots=True)
class DesktopV2ProjectRequest:
    """One project-scoped v2 read request with no operation-specific payload."""

    request_id: str
    operation: DesktopV2Operation
    project_root: str
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION

    def __post_init__(self) -> None:
        _validate_common(self.protocol_version, self.request_id)
        if self.operation not in _PROJECT_READ_OPERATIONS:
            raise DesktopIPCError("v2 project read operation is invalid")
        _require_nonblank(self.project_root, "project_root")

    def to_dict(self) -> dict[str, object]:
        return {
            **_base_payload(self.protocol_version, self.request_id, self.operation),
            "project_root": self.project_root,
        }


@dataclass(frozen=True, slots=True)
class DesktopV2ApplicationReportRequest:
    """Typed deterministic report request."""

    request_id: str
    project_root: str
    report_format: str
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.APPLICATION_REPORT

    def __post_init__(self) -> None:
        _validate_common(self.protocol_version, self.request_id)
        _require_nonblank(self.project_root, "project_root")
        if self.operation is not DesktopV2Operation.APPLICATION_REPORT:
            raise DesktopIPCError("v2 report request operation is invalid")
        if self.report_format not in _REPORT_FORMATS:
            raise DesktopIPCError("application_report requires a supported report_format")

    def to_dict(self) -> dict[str, object]:
        return {
            **_base_payload(self.protocol_version, self.request_id, self.operation),
            "project_root": self.project_root,
            "report_format": self.report_format,
        }


@dataclass(frozen=True, slots=True)
class DesktopV2PrepareWorkflowRequest:
    """Typed workflow-intent request preserving the existing application authority."""

    request_id: str
    project_root: str
    workflow_recipe_id: str
    workflow_recipe_version: str
    root_structure_snapshot_id: str
    parameters_hash: str | None = None
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.PREPARE_WORKFLOW

    def __post_init__(self) -> None:
        _validate_common(self.protocol_version, self.request_id)
        _require_nonblank(self.project_root, "project_root")
        if self.operation is not DesktopV2Operation.PREPARE_WORKFLOW:
            raise DesktopIPCError("v2 prepare-workflow request operation is invalid")
        for name, value in (
            ("workflow_recipe_id", self.workflow_recipe_id),
            ("workflow_recipe_version", self.workflow_recipe_version),
            ("root_structure_snapshot_id", self.root_structure_snapshot_id),
        ):
            _require_nonblank(value, name)
        try:
            UUID(self.root_structure_snapshot_id)
        except ValueError as error:
            raise DesktopIPCError("root_structure_snapshot_id must be a UUID") from error
        if self.parameters_hash is not None and not _is_sha256(self.parameters_hash):
            raise DesktopIPCError("parameters_hash must be a SHA-256 digest")

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            **_base_payload(self.protocol_version, self.request_id, self.operation),
            "project_root": self.project_root,
            "workflow_recipe_id": self.workflow_recipe_id,
            "workflow_recipe_version": self.workflow_recipe_version,
            "root_structure_snapshot_id": self.root_structure_snapshot_id,
        }
        if self.parameters_hash is not None:
            payload["parameters_hash"] = self.parameters_hash
        return payload


DesktopV2Request: TypeAlias = (
    DesktopV2HealthRequest
    | DesktopV2ProjectRequest
    | DesktopV2ApplicationReportRequest
    | DesktopV2PrepareWorkflowRequest
    | DesktopV2ModelRequest
)


def is_desktop_v2_request(value: object) -> TypeGuard[DesktopV2Request]:
    """Return whether one decoded request belongs to the v2 protocol family."""

    return isinstance(
        value,
        (
            DesktopV2HealthRequest,
            DesktopV2ProjectRequest,
            DesktopV2ApplicationReportRequest,
            DesktopV2PrepareWorkflowRequest,
        ),
    ) or is_desktop_v2_model_request(value)


@dataclass(frozen=True, slots=True)
class DesktopV2Response:
    """Strict v2 response envelope correlated to one typed request."""

    request_id: str
    operation: DesktopV2Operation
    ok: bool
    payload: dict[str, object] | None = None
    error: DesktopError | None = None
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION

    def __post_init__(self) -> None:
        _validate_common(self.protocol_version, self.request_id)
        if not isinstance(self.operation, DesktopV2Operation):
            raise DesktopIPCError("operation must be a DesktopV2Operation")
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


class DesktopBackendV2:
    """Stateless task-oriented adapter over existing Python authorities."""

    def handle(self, request: DesktopV2Request) -> DesktopV2Response:
        """Handle one v2 request without retaining project/session authority."""

        if isinstance(request, DesktopV2HealthRequest):
            return self._success(request.request_id, request.operation, _health_payload())

        if is_desktop_v2_model_request(request):
            return self._handle_model_request(request)

        if isinstance(request, DesktopV2ApplicationReportRequest):
            root = Path(request.project_root)
            try:
                try:
                    report_receipt = report_action(
                        project_root=root,
                        report_format=request.report_format,
                    )
                except _PROJECT_READ_ERRORS:
                    raise
                except ValueError as error:
                    return self._application_failure(request, error)
                return self._success(
                    request.request_id,
                    request.operation,
                    {"project_root": str(root), **report_receipt.to_dict()},
                )
            except _PROJECT_READ_ERRORS as error:
                return self._project_failure(request.request_id, request.operation, error)

        if isinstance(request, DesktopV2PrepareWorkflowRequest):
            root = Path(request.project_root)
            try:
                try:
                    workflow_receipt = prepare_workflow_action(
                        project_root=root,
                        workflow_recipe_id=request.workflow_recipe_id,
                        workflow_recipe_version=request.workflow_recipe_version,
                        root_structure_snapshot_id=request.root_structure_snapshot_id,
                        parameters_hash=request.parameters_hash,
                    )
                except _PROJECT_READ_ERRORS:
                    raise
                except ValueError as error:
                    return self._application_failure(request, error)
                return self._success(
                    request.request_id,
                    request.operation,
                    {"project_root": str(root), **workflow_receipt.to_dict()},
                )
            except _PROJECT_READ_ERRORS as error:
                return self._project_failure(request.request_id, request.operation, error)

        root = Path(request.project_root)
        try:
            if request.operation is DesktopV2Operation.OPEN_PROJECT:
                bundle = ProjectStore(root).open()
                return self._success(
                    request.request_id,
                    request.operation,
                    {
                        "project_root": str(root),
                        "project_id": str(bundle.project.id),
                        "project_name": bundle.project.name,
                        "project_slug": bundle.project.slug,
                        "schema_version": bundle.project.schema_version,
                    },
                )
            if request.operation is DesktopV2Operation.STATUS:
                status = open_project(root).status()
                return self._success(
                    request.request_id,
                    request.operation,
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
            if request.operation is DesktopV2Operation.FRONTEND_HANDOFF:
                bundle = ProjectStore(root).open()
                handoff = build_desktop_frontend_handoff(bundle)
                return self._success(
                    request.request_id,
                    request.operation,
                    {"project_root": str(root), "handoff": handoff.to_dict()},
                )
            if request.operation is DesktopV2Operation.PROJECT_DASHBOARD:
                bundle = ProjectStore(root).open()
                dashboard = build_desktop_project_dashboard(bundle)
                return self._success(
                    request.request_id,
                    request.operation,
                    {"project_root": str(root), "dashboard": dashboard.to_dict()},
                )
            if request.operation is DesktopV2Operation.MODEL_CATALOG:
                return self._success(
                    request.request_id,
                    request.operation,
                    model_catalog_action(root),
                )
        except _PROJECT_READ_ERRORS as error:
            return self._project_failure(request.request_id, request.operation, error)

        raise DesktopIPCError(f"unsupported desktop v2 operation: {request.operation.value}")

    def _handle_model_request(self, request: DesktopV2ModelRequest) -> DesktopV2Response:
        try:
            if isinstance(request, DesktopV2StructurePresentationRequest):
                payload = structure_presentation_action(
                    request.project_root,
                    structure_snapshot_id=request.structure_snapshot_id,
                )
            elif isinstance(request, DesktopV2CreateProjectRequest):
                payload = create_project_action(
                    project_root=request.project_root,
                    name=request.name,
                    slug=request.slug,
                    description=request.description,
                )
            elif isinstance(request, DesktopV2CreateCatalystRequest):
                payload = create_catalyst_action(
                    project_root=request.project_root,
                    name=request.name,
                    slug=request.slug,
                    formula_label=request.formula_label,
                    support_type=request.support_type,
                    series_key=request.series_key,
                    series_value=request.series_value,
                    tags=request.tags,
                )
            elif isinstance(request, DesktopV2BuildGrapheneRequest):
                payload = build_graphene_action(
                    project_root=request.project_root,
                    catalyst_id=request.catalyst_id,
                    variant_name=request.variant_name,
                    nx=request.nx,
                    ny=request.ny,
                    bond_length_angstrom=request.bond_length_angstrom,
                    vacuum_gap_angstrom=request.vacuum_gap_angstrom,
                    label=request.label,
                )
            elif isinstance(request, DesktopV2ImportStructureRequest):
                payload = import_structure_action(
                    project_root=request.project_root,
                    catalyst_id=request.catalyst_id,
                    variant_name=request.variant_name,
                    source_path=request.source_path,
                    format=request.format,
                )
            elif isinstance(request, DesktopV2MutateStructureRequest):
                payload = mutate_structure_action(
                    project_root=request.project_root,
                    source_variant_id=request.source_variant_id,
                    source_snapshot_id=request.source_snapshot_id,
                    variant_name=request.variant_name,
                    vacancy_atom_uids=request.vacancy_atom_uids,
                    substitutions=request.substitutions,
                    label=request.label,
                )
            elif isinstance(request, DesktopV2BuildSingleMetalRequest):
                payload = build_single_metal_action(
                    project_root=request.project_root,
                    source_variant_id=request.source_variant_id,
                    source_snapshot_id=request.source_snapshot_id,
                    variant_name=request.variant_name,
                    metal_element=request.metal_element,
                    coordination_atom_uids=request.coordination_atom_uids,
                    side=request.side,
                    height_angstrom=request.height_angstrom,
                    label=request.label,
                )
            elif isinstance(request, DesktopV2BuildMultiMetalRequest):
                payload = build_multi_metal_action(
                    project_root=request.project_root,
                    source_variant_id=request.source_variant_id,
                    source_snapshot_id=request.source_snapshot_id,
                    variant_name=request.variant_name,
                    centers=request.centers,
                    metal_metal_topology_intent=request.metal_metal_topology_intent,
                    label=request.label,
                )
            elif isinstance(request, DesktopV2CreateActiveSiteRequest):
                payload = create_active_site_action(
                    project_root=request.project_root,
                    structure_variant_id=request.structure_variant_id,
                    source_snapshot_id=request.source_snapshot_id,
                    center_atom_uids=request.center_atom_uids,
                    side_labels=request.side_labels,
                    topology=request.topology,
                    coordination_environment=request.coordination_environment,
                )
            elif isinstance(request, DesktopV2BuildAdsorbateConformerRequest):
                payload = build_adsorbate_conformer_action(
                    project_root=request.project_root,
                    structure_variant_id=request.structure_variant_id,
                    source_snapshot_id=request.source_snapshot_id,
                    active_site_id=request.active_site_id,
                    state_label=request.state_label,
                    template_key=request.template_key,
                    target_center_atom_uids=request.target_center_atom_uids,
                    binding_mode=request.binding_mode,
                    height_angstrom=request.height_angstrom,
                    contacts=request.contacts,
                    conformer_name=request.conformer_name,
                    coverage=request.coverage,
                    reaction_role=request.reaction_role,
                    orientation=request.orientation,
                    rank=request.rank,
                )
            else:  # pragma: no cover - protected by the closed request union
                raise DesktopIPCError("unsupported Model Studio request")
        except _PROJECT_READ_ERRORS as error:
            return self._project_failure(request.request_id, request.operation, error)
        except (OSError, ValueError) as error:
            return self._application_failure(request, error)
        return self._success(request.request_id, request.operation, payload)

    @staticmethod
    def _success(
        request_id: str,
        operation: DesktopV2Operation,
        payload: dict[str, object],
    ) -> DesktopV2Response:
        return DesktopV2Response(
            request_id=request_id,
            operation=operation,
            ok=True,
            payload=payload,
        )

    @classmethod
    def _application_failure(
        cls,
        request: DesktopV2Request,
        error: Exception,
    ) -> DesktopV2Response:
        return cls._failure(
            request.request_id,
            request.operation,
            code="application_rejected",
            message=str(error),
        )

    @staticmethod
    def _failure(
        request_id: str,
        operation: DesktopV2Operation,
        *,
        code: str,
        message: str,
    ) -> DesktopV2Response:
        return DesktopV2Response(
            request_id=request_id,
            operation=operation,
            ok=False,
            error=DesktopError(code=code, message=message),
        )

    @classmethod
    def _project_failure(
        cls,
        request_id: str,
        operation: DesktopV2Operation,
        error: Exception,
    ) -> DesktopV2Response:
        return cls._failure(
            request_id,
            operation,
            code="project_unavailable",
            message=str(error),
        )


def decode_desktop_v2_request(line: str) -> DesktopV2Request:
    """Decode one strict v2 JSON request into an operation-specific request type."""

    try:
        raw: Any = json.loads(line)
    except json.JSONDecodeError as error:
        raise DesktopIPCError("desktop request is not valid JSON") from error
    if not isinstance(raw, dict):
        raise DesktopIPCError("desktop request must be a JSON object")

    protocol_version = _required_string(raw, "protocol_version")
    request_id = _required_string(raw, "request_id")
    operation_text = _required_string(raw, "operation")
    if protocol_version != DESKTOP_IPC_V2_CONTRACT_VERSION:
        raise DesktopIPCError("unsupported desktop IPC contract version")
    try:
        operation = DesktopV2Operation(operation_text)
    except ValueError as error:
        raise DesktopIPCError("unsupported desktop operation") from error

    if operation is DesktopV2Operation.HEALTH:
        _reject_unknown(raw, _BASE_FIELDS, operation)
        return DesktopV2HealthRequest(request_id=request_id)

    if operation in _PROJECT_READ_OPERATIONS:
        _reject_unknown(raw, _PROJECT_FIELDS, operation)
        return DesktopV2ProjectRequest(
            request_id=request_id,
            operation=operation,
            project_root=_required_string(raw, "project_root"),
        )

    if operation is DesktopV2Operation.APPLICATION_REPORT:
        allowed = _PROJECT_FIELDS | {"report_format"}
        _reject_unknown(raw, allowed, operation)
        return DesktopV2ApplicationReportRequest(
            request_id=request_id,
            project_root=_required_string(raw, "project_root"),
            report_format=_required_string(raw, "report_format"),
        )

    if operation is DesktopV2Operation.PREPARE_WORKFLOW:
        allowed = _PROJECT_FIELDS | {
            "workflow_recipe_id",
            "workflow_recipe_version",
            "root_structure_snapshot_id",
            "parameters_hash",
        }
        _reject_unknown(raw, allowed, operation)
        parameters_hash = _optional_string(raw, "parameters_hash")
        return DesktopV2PrepareWorkflowRequest(
            request_id=request_id,
            project_root=_required_string(raw, "project_root"),
            workflow_recipe_id=_required_string(raw, "workflow_recipe_id"),
            workflow_recipe_version=_required_string(raw, "workflow_recipe_version"),
            root_structure_snapshot_id=_required_string(raw, "root_structure_snapshot_id"),
            parameters_hash=parameters_hash,
        )

    return decode_desktop_v2_model_request(
        raw,
        operation=operation,
        request_id=request_id,
    )


def encode_desktop_v2_response(response: DesktopV2Response) -> str:
    """Render one deterministic NDJSON v2 response."""

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
        "operations": [operation.value for operation in DesktopV2Operation],
        "stateless_project_requests": True,
        "supported_protocol_versions": [
            DESKTOP_IPC_CONTRACT_VERSION,
            DESKTOP_IPC_V2_CONTRACT_VERSION,
        ],
        "workflow_recipes": [
            recipe.to_dict() for recipe in list_desktop_workflow_recipes()
        ],
    }


def _validate_common(protocol_version: str, request_id: str) -> None:
    if protocol_version != DESKTOP_IPC_V2_CONTRACT_VERSION:
        raise DesktopIPCError("unsupported desktop IPC contract version")
    _require_nonblank(request_id, "request_id")


def _base_payload(
    protocol_version: str,
    request_id: str,
    operation: DesktopV2Operation,
) -> dict[str, object]:
    return {
        "protocol_version": protocol_version,
        "request_id": request_id,
        "operation": operation.value,
    }


def _reject_unknown(
    raw: dict[str, Any],
    allowed: frozenset[str] | set[str],
    operation: DesktopV2Operation,
) -> None:
    unknown = set(raw) - set(allowed)
    if unknown:
        raise DesktopIPCError(
            f"{operation.value} request contains unknown fields: {sorted(unknown)!r}"
        )


def _required_string(raw: dict[str, Any], field_name: str) -> str:
    value = raw.get(field_name)
    if not isinstance(value, str):
        raise DesktopIPCError(f"{field_name} must be a string")
    _require_nonblank(value, field_name)
    return value


def _optional_string(raw: dict[str, Any], field_name: str) -> str | None:
    if field_name not in raw:
        return None
    value = raw[field_name]
    if not isinstance(value, str):
        raise DesktopIPCError(f"{field_name} must be a string when supplied")
    _require_nonblank(value, field_name)
    return value


def _require_nonblank(value: str, field_name: str) -> None:
    if not value.strip():
        raise DesktopIPCError(f"{field_name} must not be blank")


def _pairs(values: tuple[tuple[str, int], ...]) -> list[list[object]]:
    return [[name, count] for name, count in values]


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdefABCDEF" for character in value
    )
