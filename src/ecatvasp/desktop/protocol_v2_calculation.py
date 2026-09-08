"""Strict calculation-wizard request contracts for desktop IPC v2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeAlias, TypeGuard
from uuid import UUID

from ecatvasp.desktop.protocol import DesktopIPCError
from ecatvasp.desktop.protocol_v2_common import (
    DESKTOP_IPC_V2_CONTRACT_VERSION,
    DesktopV2Operation,
)

_BASE = frozenset({"protocol_version", "request_id", "operation", "project_root"})
_METHOD_FIELDS = frozenset(
    {
        "xc_functional",
        "potcar_family",
        "potcar_root",
        "potcar_symbols",
        "spin_treatment",
        "engine_version",
        "dispersion_model",
    }
)
_PROTOCOL_FIELDS = frozenset(
    {
        "encut_ev",
        "kpoint_kind",
        "kpoint_mesh",
        "kpoint_value",
        "kpoint_centering",
        "vacuum_axis",
        "precision",
        "ediff_ev",
        "ediffg_ev_per_angstrom",
        "ismear",
        "sigma_ev",
        "isym",
    }
)
_RECIPE_FIELDS = frozenset(
    {"frequency_potim_angstrom", "frequency_atom_uids", "dos_nedos", "lobster_nbands"}
)
_ENCUT_FIELDS = frozenset(
    {
        "core_method_hash",
        "potcar_spec_hash",
        "tested_encuts_ev",
        "selected_encut_ev",
        "analysis_hash",
    }
)
_KPOINT_FIELDS = frozenset(
    {
        "core_method_hash",
        "system_kind",
        "tested_plan_hashes",
        "selected_plan_hash",
        "analysis_hash",
    }
)


@dataclass(frozen=True, slots=True)
class DesktopV2CalculationCatalogRequest:
    request_id: str
    project_root: str
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.CALCULATION_CATALOG


@dataclass(frozen=True, slots=True)
class DesktopV2PrepareCalculationWorkflowRequest:
    request_id: str
    project_root: str
    task: str
    root_structure_snapshot_id: str
    method: dict[str, Any]
    protocol: dict[str, Any]
    recipe: dict[str, Any]
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.PREPARE_CALCULATION_WORKFLOW


@dataclass(frozen=True, slots=True)
class DesktopV2MaterializeCalculationStepRequest:
    request_id: str
    project_root: str
    workflow_plan_id: str
    step_key: str
    method_fingerprint_id: str
    task: str
    method: dict[str, Any]
    protocol: dict[str, Any]
    numerical_evidence: dict[str, Any]
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.MATERIALIZE_CALCULATION_STEP


DesktopV2CalculationRequest: TypeAlias = (
    DesktopV2CalculationCatalogRequest
    | DesktopV2PrepareCalculationWorkflowRequest
    | DesktopV2MaterializeCalculationStepRequest
)


def is_desktop_v2_calculation_request(value: object) -> TypeGuard[DesktopV2CalculationRequest]:
    return isinstance(
        value,
        (
            DesktopV2CalculationCatalogRequest,
            DesktopV2PrepareCalculationWorkflowRequest,
            DesktopV2MaterializeCalculationStepRequest,
        ),
    )


def decode_desktop_v2_calculation_request(
    raw: dict[str, Any],
    *,
    operation: DesktopV2Operation,
    request_id: str,
) -> DesktopV2CalculationRequest:
    project_root = _required_string(raw, "project_root")
    if operation is DesktopV2Operation.CALCULATION_CATALOG:
        _reject_unknown(raw, _BASE, operation)
        return DesktopV2CalculationCatalogRequest(request_id=request_id, project_root=project_root)

    if operation is DesktopV2Operation.PREPARE_CALCULATION_WORKFLOW:
        allowed = _BASE | {"task", "root_structure_snapshot_id", "method", "protocol", "recipe"}
        _reject_unknown(raw, allowed, operation)
        snapshot_id = _required_uuid(raw, "root_structure_snapshot_id")
        method = _method_object(raw)
        protocol = _protocol_object(raw)
        recipe = _recipe_object(raw)
        task = _task(raw)
        return DesktopV2PrepareCalculationWorkflowRequest(
            request_id=request_id,
            project_root=project_root,
            task=task,
            root_structure_snapshot_id=snapshot_id,
            method=method,
            protocol=protocol,
            recipe=recipe,
        )

    if operation is DesktopV2Operation.MATERIALIZE_CALCULATION_STEP:
        allowed = _BASE | {
            "workflow_plan_id",
            "step_key",
            "method_fingerprint_id",
            "task",
            "method",
            "protocol",
            "numerical_evidence",
        }
        _reject_unknown(raw, allowed, operation)
        method = _method_object(raw)
        protocol = _protocol_object(raw)
        evidence = _object(raw, "numerical_evidence")
        _reject_unknown(evidence, {"encut", "kpoints"}, operation, prefix="numerical_evidence")
        encut = _object(evidence, "encut")
        _reject_unknown(encut, _ENCUT_FIELDS, operation, prefix="numerical_evidence.encut")
        _validate_sha_fields(encut, ("core_method_hash", "potcar_spec_hash", "analysis_hash"))
        tested_encuts = encut.get("tested_encuts_ev")
        if not isinstance(tested_encuts, list) or not tested_encuts:
            raise DesktopIPCError("numerical_evidence.encut.tested_encuts_ev must be a non-empty list")
        _number(encut, "selected_encut_ev")
        kpoints = evidence.get("kpoints")
        if kpoints is not None:
            if not isinstance(kpoints, dict):
                raise DesktopIPCError("numerical_evidence.kpoints must be an object")
            _reject_unknown(kpoints, _KPOINT_FIELDS, operation, prefix="numerical_evidence.kpoints")
            _validate_sha_fields(kpoints, ("core_method_hash", "selected_plan_hash", "analysis_hash"))
            hashes = kpoints.get("tested_plan_hashes")
            if not isinstance(hashes, list) or not hashes or any(not _is_sha256(item) for item in hashes):
                raise DesktopIPCError("numerical_evidence.kpoints.tested_plan_hashes are invalid")
            _required_string(kpoints, "system_kind")
        return DesktopV2MaterializeCalculationStepRequest(
            request_id=request_id,
            project_root=project_root,
            workflow_plan_id=_required_uuid(raw, "workflow_plan_id"),
            step_key=_required_string(raw, "step_key"),
            method_fingerprint_id=_required_uuid(raw, "method_fingerprint_id"),
            task=_task(raw),
            method=method,
            protocol=protocol,
            numerical_evidence=evidence,
        )

    raise DesktopIPCError("operation is not a calculation-wizard request")


def _method_object(raw: dict[str, Any]) -> dict[str, Any]:
    value = _object(raw, "method")
    _reject_unknown(value, _METHOD_FIELDS, DesktopV2Operation.PREPARE_CALCULATION_WORKFLOW, prefix="method")
    for field in ("xc_functional", "potcar_family", "potcar_root"):
        _required_string(value, field)
    symbols = value.get("potcar_symbols")
    if not isinstance(symbols, list) or not symbols:
        raise DesktopIPCError("method.potcar_symbols must be a non-empty list")
    for index, item in enumerate(symbols):
        if not isinstance(item, dict):
            raise DesktopIPCError("method.potcar_symbols entries must be objects")
        if set(item) != {"element", "symbol"}:
            raise DesktopIPCError(f"method.potcar_symbols[{index}] fields are invalid")
        _required_string(item, "element")
        _required_string(item, "symbol")
    return value


def _protocol_object(raw: dict[str, Any]) -> dict[str, Any]:
    value = _object(raw, "protocol")
    _reject_unknown(value, _PROTOCOL_FIELDS, DesktopV2Operation.PREPARE_CALCULATION_WORKFLOW, prefix="protocol")
    _number(value, "encut_ev")
    _required_string(value, "kpoint_kind")
    mesh = value.get("kpoint_mesh")
    if mesh is not None and (
        not isinstance(mesh, list)
        or len(mesh) != 3
        or any(isinstance(item, bool) or not isinstance(item, int) for item in mesh)
    ):
        raise DesktopIPCError("protocol.kpoint_mesh must contain three integers")
    if "kpoint_value" in value and value["kpoint_value"] is not None:
        _number(value, "kpoint_value")
    return value


def _recipe_object(raw: dict[str, Any]) -> dict[str, Any]:
    value = _object(raw, "recipe")
    _reject_unknown(value, _RECIPE_FIELDS, DesktopV2Operation.PREPARE_CALCULATION_WORKFLOW, prefix="recipe")
    uids = value.get("frequency_atom_uids")
    if uids is not None:
        if not isinstance(uids, list) or any(not isinstance(item, str) for item in uids):
            raise DesktopIPCError("recipe.frequency_atom_uids must be a string list")
        for item in uids:
            try:
                UUID(item)
            except ValueError as error:
                raise DesktopIPCError("recipe.frequency_atom_uids must contain UUIDs") from error
    return value


def _task(raw: dict[str, Any]) -> str:
    value = _required_string(raw, "task")
    if value not in {"slab", "adsorbate", "gas_reference"}:
        raise DesktopIPCError("calculation task is unsupported")
    return value


def _object(raw: dict[str, Any], field: str) -> dict[str, Any]:
    value = raw.get(field)
    if not isinstance(value, dict):
        raise DesktopIPCError(f"{field} must be an object")
    return value


def _required_string(raw: dict[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise DesktopIPCError(f"{field} must be a non-blank string")
    return value


def _required_uuid(raw: dict[str, Any], field: str) -> str:
    value = _required_string(raw, field)
    try:
        UUID(value)
    except ValueError as error:
        raise DesktopIPCError(f"{field} must be a UUID") from error
    return value


def _number(raw: dict[str, Any], field: str) -> float:
    value = raw.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DesktopIPCError(f"{field} must be numeric")
    return float(value)


def _validate_sha_fields(raw: dict[str, Any], fields: tuple[str, ...]) -> None:
    for field in fields:
        value = raw.get(field)
        if not _is_sha256(value):
            raise DesktopIPCError(f"{field} must be a SHA-256 digest")


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def _reject_unknown(
    raw: dict[str, Any],
    allowed: set[str] | frozenset[str],
    operation: DesktopV2Operation,
    *,
    prefix: str = "request",
) -> None:
    unknown = set(raw) - set(allowed)
    if unknown:
        raise DesktopIPCError(
            f"{operation.value} {prefix} contains unknown fields: {sorted(unknown)!r}"
        )
