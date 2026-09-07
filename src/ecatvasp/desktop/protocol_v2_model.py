"""Operation-specific Model Studio requests for desktop IPC v2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeAlias, TypeGuard
from uuid import UUID

from ecatvasp.desktop.model_studio import (
    DesktopAdsorbateContactInput,
    DesktopDopantInput,
    DesktopMetalCenterInput,
)
from ecatvasp.desktop.protocol import DesktopIPCError
from ecatvasp.desktop.protocol_v2_common import (
    DESKTOP_IPC_V2_CONTRACT_VERSION,
    DesktopV2Operation,
)

_BASE_FIELDS = frozenset({"protocol_version", "request_id", "operation"})
_PROJECT_FIELDS = _BASE_FIELDS | {"project_root"}
_MODEL_OPERATIONS = frozenset(
    {
        DesktopV2Operation.STRUCTURE_PRESENTATION,
        DesktopV2Operation.CREATE_PROJECT,
        DesktopV2Operation.CREATE_CATALYST,
        DesktopV2Operation.BUILD_GRAPHENE_MODEL,
        DesktopV2Operation.IMPORT_STRUCTURE_MODEL,
        DesktopV2Operation.MUTATE_STRUCTURE_MODEL,
        DesktopV2Operation.BUILD_SINGLE_METAL_SITE,
        DesktopV2Operation.BUILD_MULTI_METAL_SITE,
        DesktopV2Operation.CREATE_ACTIVE_SITE,
        DesktopV2Operation.BUILD_ADSORBATE_CONFORMER,
    }
)


@dataclass(frozen=True, slots=True)
class DesktopV2StructurePresentationRequest:
    request_id: str
    project_root: str
    structure_snapshot_id: str
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.STRUCTURE_PRESENTATION


@dataclass(frozen=True, slots=True)
class DesktopV2CreateProjectRequest:
    request_id: str
    project_root: str
    name: str
    slug: str
    description: str | None = None
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.CREATE_PROJECT


@dataclass(frozen=True, slots=True)
class DesktopV2CreateCatalystRequest:
    request_id: str
    project_root: str
    name: str
    slug: str
    formula_label: str | None = None
    support_type: str | None = None
    series_key: str | None = None
    series_value: str | int | float | None = None
    tags: tuple[str, ...] = ()
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.CREATE_CATALYST


@dataclass(frozen=True, slots=True)
class DesktopV2BuildGrapheneRequest:
    request_id: str
    project_root: str
    catalyst_id: str
    variant_name: str
    nx: int
    ny: int
    bond_length_angstrom: float
    vacuum_gap_angstrom: float
    label: str | None = None
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.BUILD_GRAPHENE_MODEL


@dataclass(frozen=True, slots=True)
class DesktopV2ImportStructureRequest:
    request_id: str
    project_root: str
    catalyst_id: str
    variant_name: str
    source_path: str
    format: str | None = None
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.IMPORT_STRUCTURE_MODEL


@dataclass(frozen=True, slots=True)
class DesktopV2MutateStructureRequest:
    request_id: str
    project_root: str
    source_variant_id: str
    source_snapshot_id: str
    variant_name: str
    vacancy_atom_uids: tuple[str, ...] = ()
    substitutions: tuple[DesktopDopantInput, ...] = ()
    label: str | None = None
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.MUTATE_STRUCTURE_MODEL


@dataclass(frozen=True, slots=True)
class DesktopV2BuildSingleMetalRequest:
    request_id: str
    project_root: str
    source_variant_id: str
    source_snapshot_id: str
    variant_name: str
    metal_element: str
    coordination_atom_uids: tuple[str, ...]
    side: str
    height_angstrom: float
    label: str | None = None
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.BUILD_SINGLE_METAL_SITE


@dataclass(frozen=True, slots=True)
class DesktopV2BuildMultiMetalRequest:
    request_id: str
    project_root: str
    source_variant_id: str
    source_snapshot_id: str
    variant_name: str
    centers: tuple[DesktopMetalCenterInput, ...]
    metal_metal_topology_intent: str | None = None
    label: str | None = None
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.BUILD_MULTI_METAL_SITE


@dataclass(frozen=True, slots=True)
class DesktopV2CreateActiveSiteRequest:
    request_id: str
    project_root: str
    structure_variant_id: str
    source_snapshot_id: str
    center_atom_uids: tuple[str, ...]
    side_labels: tuple[tuple[str, str], ...] = ()
    topology: str | None = None
    coordination_environment: str | None = None
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.CREATE_ACTIVE_SITE


@dataclass(frozen=True, slots=True)
class DesktopV2BuildAdsorbateConformerRequest:
    request_id: str
    project_root: str
    structure_variant_id: str
    source_snapshot_id: str
    active_site_id: str
    state_label: str
    template_key: str
    target_center_atom_uids: tuple[str, ...]
    binding_mode: str
    height_angstrom: float
    contacts: tuple[DesktopAdsorbateContactInput, ...]
    conformer_name: str
    coverage: float | None = None
    reaction_role: str | None = None
    orientation: str | None = None
    rank: int | None = None
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.BUILD_ADSORBATE_CONFORMER


DesktopV2ModelRequest: TypeAlias = (
    DesktopV2StructurePresentationRequest
    | DesktopV2CreateProjectRequest
    | DesktopV2CreateCatalystRequest
    | DesktopV2BuildGrapheneRequest
    | DesktopV2ImportStructureRequest
    | DesktopV2MutateStructureRequest
    | DesktopV2BuildSingleMetalRequest
    | DesktopV2BuildMultiMetalRequest
    | DesktopV2CreateActiveSiteRequest
    | DesktopV2BuildAdsorbateConformerRequest
)


def is_desktop_v2_model_request(value: object) -> TypeGuard[DesktopV2ModelRequest]:
    return isinstance(
        value,
        (
            DesktopV2StructurePresentationRequest,
            DesktopV2CreateProjectRequest,
            DesktopV2CreateCatalystRequest,
            DesktopV2BuildGrapheneRequest,
            DesktopV2ImportStructureRequest,
            DesktopV2MutateStructureRequest,
            DesktopV2BuildSingleMetalRequest,
            DesktopV2BuildMultiMetalRequest,
            DesktopV2CreateActiveSiteRequest,
            DesktopV2BuildAdsorbateConformerRequest,
        ),
    )


def decode_desktop_v2_model_request(
    raw: dict[str, Any],
    *,
    operation: DesktopV2Operation,
    request_id: str,
) -> DesktopV2ModelRequest:
    """Decode one already-versioned Model Studio request with exact field guards."""

    if operation not in _MODEL_OPERATIONS:
        raise DesktopIPCError("operation is not a Model Studio v2 operation")
    project_root = _required_string(raw, "project_root")

    if operation is DesktopV2Operation.STRUCTURE_PRESENTATION:
        _reject_unknown(raw, _PROJECT_FIELDS | {"structure_snapshot_id"}, operation)
        snapshot_id = _required_uuid_string(raw, "structure_snapshot_id")
        return DesktopV2StructurePresentationRequest(request_id, project_root, snapshot_id)

    if operation is DesktopV2Operation.CREATE_PROJECT:
        _reject_unknown(raw, _PROJECT_FIELDS | {"name", "slug", "description"}, operation)
        return DesktopV2CreateProjectRequest(
            request_id=request_id,
            project_root=project_root,
            name=_required_string(raw, "name"),
            slug=_required_string(raw, "slug"),
            description=_optional_string(raw, "description"),
        )

    if operation is DesktopV2Operation.CREATE_CATALYST:
        allowed = _PROJECT_FIELDS | {
            "name",
            "slug",
            "formula_label",
            "support_type",
            "series_key",
            "series_value",
            "tags",
        }
        _reject_unknown(raw, allowed, operation)
        return DesktopV2CreateCatalystRequest(
            request_id=request_id,
            project_root=project_root,
            name=_required_string(raw, "name"),
            slug=_required_string(raw, "slug"),
            formula_label=_optional_string(raw, "formula_label"),
            support_type=_optional_string(raw, "support_type"),
            series_key=_optional_string(raw, "series_key"),
            series_value=_optional_scalar(raw, "series_value"),
            tags=_string_tuple(raw, "tags"),
        )

    if operation is DesktopV2Operation.BUILD_GRAPHENE_MODEL:
        allowed = _PROJECT_FIELDS | {
            "catalyst_id",
            "variant_name",
            "nx",
            "ny",
            "bond_length_angstrom",
            "vacuum_gap_angstrom",
            "label",
        }
        _reject_unknown(raw, allowed, operation)
        return DesktopV2BuildGrapheneRequest(
            request_id=request_id,
            project_root=project_root,
            catalyst_id=_required_uuid_string(raw, "catalyst_id"),
            variant_name=_required_string(raw, "variant_name"),
            nx=_required_int(raw, "nx"),
            ny=_required_int(raw, "ny"),
            bond_length_angstrom=_required_number(raw, "bond_length_angstrom"),
            vacuum_gap_angstrom=_required_number(raw, "vacuum_gap_angstrom"),
            label=_optional_string(raw, "label"),
        )

    if operation is DesktopV2Operation.IMPORT_STRUCTURE_MODEL:
        allowed = _PROJECT_FIELDS | {
            "catalyst_id",
            "variant_name",
            "source_path",
            "format",
        }
        _reject_unknown(raw, allowed, operation)
        return DesktopV2ImportStructureRequest(
            request_id=request_id,
            project_root=project_root,
            catalyst_id=_required_uuid_string(raw, "catalyst_id"),
            variant_name=_required_string(raw, "variant_name"),
            source_path=_required_string(raw, "source_path"),
            format=_optional_string(raw, "format"),
        )

    if operation is DesktopV2Operation.MUTATE_STRUCTURE_MODEL:
        allowed = _PROJECT_FIELDS | {
            "source_variant_id",
            "source_snapshot_id",
            "variant_name",
            "vacancy_atom_uids",
            "substitutions",
            "label",
        }
        _reject_unknown(raw, allowed, operation)
        substitutions_raw = _object_tuple(raw, "substitutions")
        substitutions: list[DesktopDopantInput] = []
        for item in substitutions_raw:
            _reject_nested_unknown(item, {"atom_uid", "dopant"}, "substitutions")
            substitutions.append(
                DesktopDopantInput(
                    atom_uid=_required_uuid_string(item, "atom_uid"),
                    dopant=_required_string(item, "dopant"),
                )
            )
        return DesktopV2MutateStructureRequest(
            request_id=request_id,
            project_root=project_root,
            source_variant_id=_required_uuid_string(raw, "source_variant_id"),
            source_snapshot_id=_required_uuid_string(raw, "source_snapshot_id"),
            variant_name=_required_string(raw, "variant_name"),
            vacancy_atom_uids=_uuid_string_tuple(raw, "vacancy_atom_uids"),
            substitutions=tuple(substitutions),
            label=_optional_string(raw, "label"),
        )

    if operation is DesktopV2Operation.BUILD_SINGLE_METAL_SITE:
        allowed = _PROJECT_FIELDS | {
            "source_variant_id",
            "source_snapshot_id",
            "variant_name",
            "metal_element",
            "coordination_atom_uids",
            "side",
            "height_angstrom",
            "label",
        }
        _reject_unknown(raw, allowed, operation)
        return DesktopV2BuildSingleMetalRequest(
            request_id=request_id,
            project_root=project_root,
            source_variant_id=_required_uuid_string(raw, "source_variant_id"),
            source_snapshot_id=_required_uuid_string(raw, "source_snapshot_id"),
            variant_name=_required_string(raw, "variant_name"),
            metal_element=_required_string(raw, "metal_element"),
            coordination_atom_uids=_required_uuid_string_tuple(raw, "coordination_atom_uids"),
            side=_required_string(raw, "side"),
            height_angstrom=_required_number(raw, "height_angstrom"),
            label=_optional_string(raw, "label"),
        )

    if operation is DesktopV2Operation.BUILD_MULTI_METAL_SITE:
        allowed = _PROJECT_FIELDS | {
            "source_variant_id",
            "source_snapshot_id",
            "variant_name",
            "centers",
            "metal_metal_topology_intent",
            "label",
        }
        _reject_unknown(raw, allowed, operation)
        centers: list[DesktopMetalCenterInput] = []
        for item in _required_object_tuple(raw, "centers"):
            _reject_nested_unknown(
                item,
                {"metal_element", "coordination_atom_uids", "side", "height_angstrom"},
                "centers",
            )
            centers.append(
                DesktopMetalCenterInput(
                    metal_element=_required_string(item, "metal_element"),
                    coordination_atom_uids=_required_uuid_string_tuple(
                        item, "coordination_atom_uids"
                    ),
                    side=_required_string(item, "side"),
                    height_angstrom=_required_number(item, "height_angstrom"),
                )
            )
        if len(centers) not in (2, 3):
            raise DesktopIPCError("centers must contain exactly two or three entries")
        return DesktopV2BuildMultiMetalRequest(
            request_id=request_id,
            project_root=project_root,
            source_variant_id=_required_uuid_string(raw, "source_variant_id"),
            source_snapshot_id=_required_uuid_string(raw, "source_snapshot_id"),
            variant_name=_required_string(raw, "variant_name"),
            centers=tuple(centers),
            metal_metal_topology_intent=_optional_string(raw, "metal_metal_topology_intent"),
            label=_optional_string(raw, "label"),
        )

    if operation is DesktopV2Operation.CREATE_ACTIVE_SITE:
        allowed = _PROJECT_FIELDS | {
            "structure_variant_id",
            "source_snapshot_id",
            "center_atom_uids",
            "side_labels",
            "topology",
            "coordination_environment",
        }
        _reject_unknown(raw, allowed, operation)
        side_labels: list[tuple[str, str]] = []
        for item in _object_tuple(raw, "side_labels"):
            _reject_nested_unknown(item, {"atom_uid", "side"}, "side_labels")
            side_labels.append(
                (
                    _required_uuid_string(item, "atom_uid"),
                    _required_string(item, "side"),
                )
            )
        return DesktopV2CreateActiveSiteRequest(
            request_id=request_id,
            project_root=project_root,
            structure_variant_id=_required_uuid_string(raw, "structure_variant_id"),
            source_snapshot_id=_required_uuid_string(raw, "source_snapshot_id"),
            center_atom_uids=_required_uuid_string_tuple(raw, "center_atom_uids"),
            side_labels=tuple(side_labels),
            topology=_optional_string(raw, "topology"),
            coordination_environment=_optional_string(raw, "coordination_environment"),
        )

    allowed = _PROJECT_FIELDS | {
        "structure_variant_id",
        "source_snapshot_id",
        "active_site_id",
        "state_label",
        "template_key",
        "target_center_atom_uids",
        "binding_mode",
        "height_angstrom",
        "contacts",
        "conformer_name",
        "coverage",
        "reaction_role",
        "orientation",
        "rank",
    }
    _reject_unknown(raw, allowed, operation)
    contacts: list[DesktopAdsorbateContactInput] = []
    for item in _required_object_tuple(raw, "contacts"):
        _reject_nested_unknown(item, {"adsorbate_atom_key", "site_atom_uid"}, "contacts")
        contacts.append(
            DesktopAdsorbateContactInput(
                adsorbate_atom_key=_required_string(item, "adsorbate_atom_key"),
                site_atom_uid=_required_uuid_string(item, "site_atom_uid"),
            )
        )
    return DesktopV2BuildAdsorbateConformerRequest(
        request_id=request_id,
        project_root=project_root,
        structure_variant_id=_required_uuid_string(raw, "structure_variant_id"),
        source_snapshot_id=_required_uuid_string(raw, "source_snapshot_id"),
        active_site_id=_required_uuid_string(raw, "active_site_id"),
        state_label=_required_string(raw, "state_label"),
        template_key=_required_string(raw, "template_key"),
        target_center_atom_uids=_required_uuid_string_tuple(raw, "target_center_atom_uids"),
        binding_mode=_required_string(raw, "binding_mode"),
        height_angstrom=_required_number(raw, "height_angstrom"),
        contacts=tuple(contacts),
        conformer_name=_required_string(raw, "conformer_name"),
        coverage=_optional_number(raw, "coverage"),
        reaction_role=_optional_string(raw, "reaction_role"),
        orientation=_optional_string(raw, "orientation"),
        rank=_optional_int(raw, "rank"),
    )


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


def _reject_nested_unknown(raw: dict[str, Any], allowed: set[str], field: str) -> None:
    unknown = set(raw) - allowed
    if unknown:
        raise DesktopIPCError(f"{field} entry contains unknown fields: {sorted(unknown)!r}")


def _required_string(raw: dict[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise DesktopIPCError(f"{field} must be a nonblank string")
    return value


def _optional_string(raw: dict[str, Any], field: str) -> str | None:
    if field not in raw or raw[field] is None:
        return None
    value = raw[field]
    if not isinstance(value, str):
        raise DesktopIPCError(f"{field} must be a string or null")
    if not value.strip():
        raise DesktopIPCError(f"{field} must not be blank when supplied")
    return value


def _required_uuid_string(raw: dict[str, Any], field: str) -> str:
    value = _required_string(raw, field)
    try:
        UUID(value)
    except ValueError as error:
        raise DesktopIPCError(f"{field} must be a UUID") from error
    return value


def _string_tuple(raw: dict[str, Any], field: str) -> tuple[str, ...]:
    if field not in raw:
        return ()
    value = raw[field]
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise DesktopIPCError(f"{field} must be an array of strings")
    if any(not item.strip() for item in value):
        raise DesktopIPCError(f"{field} entries must not be blank")
    return tuple(value)


def _uuid_string_tuple(raw: dict[str, Any], field: str) -> tuple[str, ...]:
    values = _string_tuple(raw, field)
    for value in values:
        try:
            UUID(value)
        except ValueError as error:
            raise DesktopIPCError(f"{field} entries must be UUIDs") from error
    return values


def _required_uuid_string_tuple(raw: dict[str, Any], field: str) -> tuple[str, ...]:
    values = _uuid_string_tuple(raw, field)
    if not values:
        raise DesktopIPCError(f"{field} must contain at least one UUID")
    return values


def _object_tuple(raw: dict[str, Any], field: str) -> tuple[dict[str, Any], ...]:
    if field not in raw:
        return ()
    value = raw[field]
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise DesktopIPCError(f"{field} must be an array of objects")
    return tuple(value)


def _required_object_tuple(raw: dict[str, Any], field: str) -> tuple[dict[str, Any], ...]:
    values = _object_tuple(raw, field)
    if not values:
        raise DesktopIPCError(f"{field} must contain at least one object")
    return values


def _required_int(raw: dict[str, Any], field: str) -> int:
    value = raw.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise DesktopIPCError(f"{field} must be an integer")
    return value


def _optional_int(raw: dict[str, Any], field: str) -> int | None:
    if field not in raw or raw[field] is None:
        return None
    return _required_int(raw, field)


def _required_number(raw: dict[str, Any], field: str) -> float:
    value = raw.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DesktopIPCError(f"{field} must be a number")
    return float(value)


def _optional_number(raw: dict[str, Any], field: str) -> float | None:
    if field not in raw or raw[field] is None:
        return None
    return _required_number(raw, field)


def _optional_scalar(raw: dict[str, Any], field: str) -> str | int | float | None:
    if field not in raw or raw[field] is None:
        return None
    value = raw[field]
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise DesktopIPCError(f"{field} must be a string, integer, number, or null")
    if isinstance(value, str) and not value.strip():
        raise DesktopIPCError(f"{field} must not be blank when supplied")
    return value
