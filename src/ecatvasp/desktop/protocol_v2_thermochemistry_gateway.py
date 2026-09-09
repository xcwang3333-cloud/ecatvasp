"""Block 7 extension gateway for the frozen desktop IPC v2 family.

The wire protocol remains ``ecatvasp-desktop-ipc-v2``. Existing Block 1-6 requests are forwarded
unchanged; only explicit Thermochemistry & Reaction Workspace operations are decoded here.
"""

from __future__ import annotations

import json
from typing import Any, TypeAlias, TypeGuard, cast

from ecatvasp.desktop.protocol import DesktopError, DesktopIPCError
from ecatvasp.desktop.protocol_v2 import DesktopV2Response
from ecatvasp.desktop.protocol_v2_common import (
    DESKTOP_IPC_V2_CONTRACT_VERSION,
    DesktopV2Operation,
)
from ecatvasp.desktop.protocol_v2_electronic_gateway import (
    DesktopBackendV2Block6,
    DesktopV2Block6Request,
    decode_desktop_v2_block6_request,
    encode_desktop_v2_response,
    is_desktop_v2_block6_request,
)
from ecatvasp.desktop.protocol_v2_reaction import (
    DesktopV2MaterializeReactionDiagramRequest,
    DesktopV2ReactionDiagramViewRequest,
    DesktopV2ReactionPreviewRequest,
    DesktopV2ReactionRequest,
    decode_desktop_v2_reaction_request,
    is_desktop_v2_reaction_request,
)
from ecatvasp.desktop.protocol_v2_thermochemistry import (
    DesktopV2MaterializeGasReferenceRequest,
    DesktopV2MaterializeHarmonicThermochemistryRequest,
    DesktopV2ThermochemistryCatalogRequest,
    DesktopV2ThermochemistryRequest,
    DesktopV2ThermochemistryViewRequest,
    decode_desktop_v2_thermochemistry_request,
    is_desktop_v2_thermochemistry_request,
)
from ecatvasp.desktop.reaction import (
    materialize_reaction_diagram_action,
    reaction_diagram_view_action,
    reaction_preview_action,
)
from ecatvasp.desktop.thermochemistry import (
    materialize_gas_reference_action,
    materialize_harmonic_thermochemistry_action,
    thermochemistry_catalog_action,
    thermochemistry_view_action,
)
from ecatvasp.storage import (
    MigrationPathError,
    ProjectIntegrityError,
    ProjectStorageError,
    StorageCodecError,
    UnsupportedSchemaVersionError,
)

_THERMOCHEMISTRY_OPERATIONS = frozenset(
    {
        DesktopV2Operation.THERMOCHEMISTRY_CATALOG,
        DesktopV2Operation.MATERIALIZE_HARMONIC_THERMOCHEMISTRY,
        DesktopV2Operation.MATERIALIZE_GAS_REFERENCE,
        DesktopV2Operation.THERMOCHEMISTRY_VIEW,
    }
)
_REACTION_OPERATIONS = frozenset(
    {
        DesktopV2Operation.REACTION_PREVIEW,
        DesktopV2Operation.MATERIALIZE_REACTION_DIAGRAM,
        DesktopV2Operation.REACTION_DIAGRAM_VIEW,
    }
)
_PROJECT_READ_ERRORS = (
    MigrationPathError,
    ProjectIntegrityError,
    ProjectStorageError,
    StorageCodecError,
    UnsupportedSchemaVersionError,
)

DesktopV2Block7Request: TypeAlias = (
    DesktopV2Block6Request | DesktopV2ThermochemistryRequest | DesktopV2ReactionRequest
)


def is_desktop_v2_block7_request(value: object) -> TypeGuard[DesktopV2Block7Request]:
    return (
        is_desktop_v2_block6_request(value)
        or is_desktop_v2_thermochemistry_request(value)
        or is_desktop_v2_reaction_request(value)
    )


def decode_desktop_v2_block7_request(line: str) -> DesktopV2Block7Request:
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
    typed_raw = cast(dict[str, Any], raw)
    if operation in _THERMOCHEMISTRY_OPERATIONS:
        return decode_desktop_v2_thermochemistry_request(
            typed_raw,
            operation=operation,
            request_id=request_id,
        )
    if operation in _REACTION_OPERATIONS:
        return decode_desktop_v2_reaction_request(
            typed_raw,
            operation=operation,
            request_id=request_id,
        )
    return decode_desktop_v2_block6_request(line)


class DesktopBackendV2Block7:
    """Compatibility-preserving v2 backend with explicit Block 7 operations."""

    def __init__(self) -> None:
        self._base = DesktopBackendV2Block6()

    def handle(self, request: DesktopV2Block7Request) -> DesktopV2Response:
        if is_desktop_v2_thermochemistry_request(request):
            return self._handle_thermochemistry(request)
        if is_desktop_v2_reaction_request(request):
            return self._handle_reaction(request)
        return self._base.handle(cast(DesktopV2Block6Request, request))

    def _handle_thermochemistry(
        self,
        request: DesktopV2ThermochemistryRequest,
    ) -> DesktopV2Response:
        try:
            if isinstance(request, DesktopV2ThermochemistryCatalogRequest):
                payload = thermochemistry_catalog_action(request.project_root)
            elif isinstance(request, DesktopV2MaterializeHarmonicThermochemistryRequest):
                payload = materialize_harmonic_thermochemistry_action(
                    project_root=request.project_root,
                    calculation_id=request.calculation_id,
                    subject_kind=request.subject_kind,
                    temperature_k=request.temperature_k,
                    electronic_energy_kind=request.electronic_energy_kind,
                    electronic_entropy_policy=request.electronic_entropy_policy,
                    frequency_cutoff_cm_inverse=request.frequency_cutoff_cm_inverse,
                    imaginary_mode_policy=request.imaginary_mode_policy,
                    low_frequency_policy=request.low_frequency_policy,
                    exclusions=request.exclusions,
                )
            elif isinstance(request, DesktopV2MaterializeGasReferenceRequest):
                payload = materialize_gas_reference_action(
                    project_root=request.project_root,
                    calculation_id=request.calculation_id,
                    species=request.species,
                    temperature_k=request.temperature_k,
                    pressure_pa=request.pressure_pa,
                    standard_state=request.standard_state,
                    electronic_energy_kind=request.electronic_energy_kind,
                    electronic_entropy_policy=request.electronic_entropy_policy,
                    geometry_kind=request.geometry_kind,
                    symmetry_number=request.symmetry_number,
                    spin_multiplicity=request.spin_multiplicity,
                    atomic_masses=request.atomic_masses,
                    frequency_cutoff_cm_inverse=request.frequency_cutoff_cm_inverse,
                    imaginary_mode_policy=request.imaginary_mode_policy,
                    low_frequency_policy=request.low_frequency_policy,
                    exclusions=request.exclusions,
                )
            elif isinstance(request, DesktopV2ThermochemistryViewRequest):
                payload = thermochemistry_view_action(
                    project_root=request.project_root,
                    analysis_id=request.analysis_id,
                )
            else:  # pragma: no cover - guarded by request TypeGuard
                raise DesktopIPCError("unsupported Thermochemistry Workspace request")
        except _PROJECT_READ_ERRORS as error:
            return _failure(request, code="project_unavailable", message=str(error))
        except (OSError, ValueError) as error:
            return _failure(request, code="application_rejected", message=str(error))
        return _success(request, payload)

    def _handle_reaction(self, request: DesktopV2ReactionRequest) -> DesktopV2Response:
        try:
            if isinstance(request, DesktopV2ReactionPreviewRequest):
                payload = reaction_preview_action(
                    project_root=request.project_root,
                    preset_kind=request.preset_kind,
                    bindings=request.bindings,
                    baseline_conditions=request.baseline_conditions,
                    requested_conditions=request.requested_conditions,
                )
            elif isinstance(request, DesktopV2MaterializeReactionDiagramRequest):
                payload = materialize_reaction_diagram_action(
                    project_root=request.project_root,
                    preset_kind=request.preset_kind,
                    bindings=request.bindings,
                    baseline_conditions=request.baseline_conditions,
                    requested_conditions=request.requested_conditions,
                )
            elif isinstance(request, DesktopV2ReactionDiagramViewRequest):
                payload = reaction_diagram_view_action(
                    project_root=request.project_root,
                    analysis_id=request.analysis_id,
                )
            else:  # pragma: no cover - guarded by request TypeGuard
                raise DesktopIPCError("unsupported Reaction Workspace request")
        except _PROJECT_READ_ERRORS as error:
            return _failure(request, code="project_unavailable", message=str(error))
        except (OSError, ValueError) as error:
            return _failure(request, code="application_rejected", message=str(error))
        return _success(request, payload)


def _success(
    request: DesktopV2ThermochemistryRequest | DesktopV2ReactionRequest,
    payload: dict[str, object],
) -> DesktopV2Response:
    return DesktopV2Response(
        request_id=request.request_id,
        operation=request.operation,
        ok=True,
        payload=payload,
    )


def _failure(
    request: DesktopV2ThermochemistryRequest | DesktopV2ReactionRequest,
    *,
    code: str,
    message: str,
) -> DesktopV2Response:
    return DesktopV2Response(
        request_id=request.request_id,
        operation=request.operation,
        ok=False,
        error=DesktopError(code=code, message=message),
    )


def _required_string(raw: dict[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise DesktopIPCError(f"{field} must be a non-blank string")
    return value


__all__ = [
    "DesktopBackendV2Block7",
    "DesktopV2Block7Request",
    "decode_desktop_v2_block7_request",
    "encode_desktop_v2_response",
    "is_desktop_v2_block7_request",
]
