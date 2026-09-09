"""Strict electrocatalysis reaction-preset requests for desktop IPC v2."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, TypeAlias, TypeGuard, cast
from uuid import UUID

from ecatvasp.desktop.protocol import DesktopIPCError
from ecatvasp.desktop.protocol_v2_common import (
    DESKTOP_IPC_V2_CONTRACT_VERSION,
    DesktopV2Operation,
)

_BASE = frozenset({"protocol_version", "request_id", "operation", "project_root"})
_PREVIEW_FIELDS = frozenset(
    {"preset_kind", "bindings", "baseline_conditions", "requested_conditions"}
)
_VIEW_FIELDS = frozenset({"analysis_id"})
_PRESETS = frozenset(
    {
        "her_volmer_heyrovsky",
        "orr_associative_4e",
        "oer_associative_4e",
        "co2rr_to_co_2e",
    }
)
_POTENTIAL_REFERENCES = frozenset({"she", "rhe"})
_PH_SEMANTICS = frozenset({"explicit_activity", "included_in_rhe"})


@dataclass(frozen=True, slots=True)
class DesktopV2ReactionConditions:
    temperature_k: float
    potential_v: float
    ph: float
    potential_reference: str
    ph_semantics: str


@dataclass(frozen=True, slots=True)
class DesktopV2HERAnalysisBindings:
    clean_surface_analysis_id: str
    h_adsorbed_analysis_id: str
    h2_reference_analysis_id: str


@dataclass(frozen=True, slots=True)
class DesktopV2ORRAnalysisBindings:
    clean_surface_analysis_id: str
    ooh_adsorbed_analysis_id: str
    o_adsorbed_analysis_id: str
    oh_adsorbed_analysis_id: str
    o2_reference_analysis_id: str
    h2o_reference_analysis_id: str
    h2_reference_analysis_id: str


@dataclass(frozen=True, slots=True)
class DesktopV2OERAnalysisBindings:
    clean_surface_analysis_id: str
    oh_adsorbed_analysis_id: str
    o_adsorbed_analysis_id: str
    ooh_adsorbed_analysis_id: str
    h2o_reference_analysis_id: str
    o2_reference_analysis_id: str
    h2_reference_analysis_id: str


@dataclass(frozen=True, slots=True)
class DesktopV2CO2RRToCOAnalysisBindings:
    clean_surface_analysis_id: str
    cooh_adsorbed_analysis_id: str
    co_adsorbed_analysis_id: str
    co2_reference_analysis_id: str
    h2o_reference_analysis_id: str
    co_reference_analysis_id: str
    h2_reference_analysis_id: str


DesktopV2ReactionAnalysisBindings: TypeAlias = (
    DesktopV2HERAnalysisBindings
    | DesktopV2ORRAnalysisBindings
    | DesktopV2OERAnalysisBindings
    | DesktopV2CO2RRToCOAnalysisBindings
)


@dataclass(frozen=True, slots=True)
class DesktopV2ReactionPreviewRequest:
    request_id: str
    project_root: str
    preset_kind: str
    bindings: DesktopV2ReactionAnalysisBindings
    baseline_conditions: DesktopV2ReactionConditions
    requested_conditions: DesktopV2ReactionConditions
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.REACTION_PREVIEW


@dataclass(frozen=True, slots=True)
class DesktopV2MaterializeReactionDiagramRequest:
    request_id: str
    project_root: str
    preset_kind: str
    bindings: DesktopV2ReactionAnalysisBindings
    baseline_conditions: DesktopV2ReactionConditions
    requested_conditions: DesktopV2ReactionConditions
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.MATERIALIZE_REACTION_DIAGRAM


@dataclass(frozen=True, slots=True)
class DesktopV2ReactionDiagramViewRequest:
    request_id: str
    project_root: str
    analysis_id: str
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.REACTION_DIAGRAM_VIEW


DesktopV2ReactionRequest: TypeAlias = (
    DesktopV2ReactionPreviewRequest
    | DesktopV2MaterializeReactionDiagramRequest
    | DesktopV2ReactionDiagramViewRequest
)


def is_desktop_v2_reaction_request(value: object) -> TypeGuard[DesktopV2ReactionRequest]:
    return isinstance(
        value,
        (
            DesktopV2ReactionPreviewRequest,
            DesktopV2MaterializeReactionDiagramRequest,
            DesktopV2ReactionDiagramViewRequest,
        ),
    )


def decode_desktop_v2_reaction_request(
    raw: dict[str, Any],
    *,
    operation: DesktopV2Operation,
    request_id: str,
) -> DesktopV2ReactionRequest:
    if operation is DesktopV2Operation.REACTION_DIAGRAM_VIEW:
        _reject_unknown(raw, _BASE | _VIEW_FIELDS, operation.value)
        return DesktopV2ReactionDiagramViewRequest(
            request_id=request_id,
            project_root=_required_string(raw, "project_root"),
            analysis_id=_required_uuid(raw, "analysis_id"),
        )
    if operation not in {
        DesktopV2Operation.REACTION_PREVIEW,
        DesktopV2Operation.MATERIALIZE_REACTION_DIAGRAM,
    }:
        raise DesktopIPCError("operation is not a reaction workspace request")
    _reject_unknown(raw, _BASE | _PREVIEW_FIELDS, operation.value)
    project_root = _required_string(raw, "project_root")
    preset_kind = _choice(raw, "preset_kind", _PRESETS)
    bindings = _reaction_bindings(raw.get("bindings"), preset_kind)
    baseline = _reaction_conditions(raw.get("baseline_conditions"), "baseline_conditions")
    requested = _reaction_conditions(
        raw.get("requested_conditions"),
        "requested_conditions",
    )
    if baseline.temperature_k != requested.temperature_k:
        raise DesktopIPCError(
            "baseline_conditions and requested_conditions must use the same temperature_k"
        )
    if operation is DesktopV2Operation.REACTION_PREVIEW:
        return DesktopV2ReactionPreviewRequest(
            request_id=request_id,
            project_root=project_root,
            preset_kind=preset_kind,
            bindings=bindings,
            baseline_conditions=baseline,
            requested_conditions=requested,
        )
    return DesktopV2MaterializeReactionDiagramRequest(
        request_id=request_id,
        project_root=project_root,
        preset_kind=preset_kind,
        bindings=bindings,
        baseline_conditions=baseline,
        requested_conditions=requested,
    )


def _reaction_conditions(value: object, field_name: str) -> DesktopV2ReactionConditions:
    if not isinstance(value, dict):
        raise DesktopIPCError(f"{field_name} must be an object")
    raw = cast(dict[str, Any], value)
    allowed = {
        "temperature_k",
        "potential_v",
        "ph",
        "potential_reference",
        "ph_semantics",
    }
    _reject_unknown(raw, allowed, field_name)
    return DesktopV2ReactionConditions(
        temperature_k=_positive_number(raw, "temperature_k"),
        potential_v=_finite_number(raw, "potential_v"),
        ph=_finite_number(raw, "ph"),
        potential_reference=_choice(
            raw,
            "potential_reference",
            _POTENTIAL_REFERENCES,
        ),
        ph_semantics=_choice(raw, "ph_semantics", _PH_SEMANTICS),
    )


def _reaction_bindings(
    value: object,
    preset_kind: str,
) -> DesktopV2ReactionAnalysisBindings:
    if not isinstance(value, dict):
        raise DesktopIPCError("bindings must be an object")
    raw = cast(dict[str, Any], value)
    if preset_kind == "her_volmer_heyrovsky":
        fields: tuple[str, ...] = (
            "clean_surface_analysis_id",
            "h_adsorbed_analysis_id",
            "h2_reference_analysis_id",
        )
        values = _binding_values(raw, fields)
        return DesktopV2HERAnalysisBindings(*values)
    if preset_kind == "orr_associative_4e":
        fields = (
            "clean_surface_analysis_id",
            "ooh_adsorbed_analysis_id",
            "o_adsorbed_analysis_id",
            "oh_adsorbed_analysis_id",
            "o2_reference_analysis_id",
            "h2o_reference_analysis_id",
            "h2_reference_analysis_id",
        )
        values = _binding_values(raw, fields)
        return DesktopV2ORRAnalysisBindings(*values)
    if preset_kind == "oer_associative_4e":
        fields = (
            "clean_surface_analysis_id",
            "oh_adsorbed_analysis_id",
            "o_adsorbed_analysis_id",
            "ooh_adsorbed_analysis_id",
            "h2o_reference_analysis_id",
            "o2_reference_analysis_id",
            "h2_reference_analysis_id",
        )
        values = _binding_values(raw, fields)
        return DesktopV2OERAnalysisBindings(*values)
    if preset_kind == "co2rr_to_co_2e":
        fields = (
            "clean_surface_analysis_id",
            "cooh_adsorbed_analysis_id",
            "co_adsorbed_analysis_id",
            "co2_reference_analysis_id",
            "h2o_reference_analysis_id",
            "co_reference_analysis_id",
            "h2_reference_analysis_id",
        )
        values = _binding_values(raw, fields)
        return DesktopV2CO2RRToCOAnalysisBindings(*values)
    raise DesktopIPCError("preset_kind has an unsupported value")


def _binding_values(raw: dict[str, Any], fields: tuple[str, ...]) -> tuple[str, ...]:
    _reject_unknown(raw, set(fields), "bindings")
    values = tuple(_required_uuid(raw, field) for field in fields)
    if len(values) != len(set(values)):
        raise DesktopIPCError("bindings Analysis ids must be distinct")
    return values


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


def _choice(raw: dict[str, Any], field: str, choices: frozenset[str]) -> str:
    value = _required_string(raw, field)
    if value not in choices:
        raise DesktopIPCError(f"{field} has an unsupported value")
    return value


def _positive_number(raw: dict[str, Any], field: str) -> float:
    value = _finite_number(raw, field)
    if value <= 0.0:
        raise DesktopIPCError(f"{field} must be positive")
    return value


def _finite_number(raw: dict[str, Any], field: str) -> float:
    value = raw.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DesktopIPCError(f"{field} must be a finite number")
    converted = float(value)
    if not isfinite(converted):
        raise DesktopIPCError(f"{field} must be a finite number")
    return converted


def _reject_unknown(
    raw: dict[str, Any],
    allowed: set[str] | frozenset[str],
    label: str,
) -> None:
    unknown = set(raw) - set(allowed)
    if unknown:
        raise DesktopIPCError(
            f"{label} contains unknown fields: {sorted(unknown)!r}"
        )


__all__ = [
    "DesktopV2CO2RRToCOAnalysisBindings",
    "DesktopV2HERAnalysisBindings",
    "DesktopV2MaterializeReactionDiagramRequest",
    "DesktopV2OERAnalysisBindings",
    "DesktopV2ORRAnalysisBindings",
    "DesktopV2ReactionAnalysisBindings",
    "DesktopV2ReactionConditions",
    "DesktopV2ReactionDiagramViewRequest",
    "DesktopV2ReactionPreviewRequest",
    "DesktopV2ReactionRequest",
    "decode_desktop_v2_reaction_request",
    "is_desktop_v2_reaction_request",
]
