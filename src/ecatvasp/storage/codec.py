"""Deterministic JSON codec for persisted ECatVASP domain objects."""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, cast
from uuid import UUID

from ecatvasp.domain import (
    ActiveSite,
    AdsorptionState,
    Analysis,
    Artifact,
    BindingEdge,
    BindingMode,
    Calculation,
    Catalyst,
    DftUSetting,
    DipolePolicy,
    ExecutionAttempt,
    ExecutionSettings,
    FingerprintCompatibility,
    KPointPolicy,
    KPointPolicyKind,
    Lattice,
    MethodDefinition,
    MethodFingerprint,
    ParameterEntry,
    PotcarIdentity,
    Project,
    ProtocolDefinition,
    RecipeIdentity,
    RemoteJob,
    ScientificInputDigest,
    ScientificWorkflowPlan,
    SideLabel,
    SiteSide,
    SpinTreatment,
    StateConformer,
    StructureOrigin,
    StructureSite,
    StructureSnapshot,
    StructureVariant,
    VariantType,
    WorkflowEdgeSpec,
    WorkflowRecipeIdentity,
    WorkflowStepBinding,
    WorkflowStepSpec,
)
from ecatvasp.domain.calculation import (
    AnalysisProducerRef,
    AnalysisStatus,
    AnalysisType,
    ArtifactAvailability,
    ArtifactProducerKind,
    ArtifactType,
    CalculationEngine,
    CalculationProducerRef,
    CalculationScientificStatus,
    CalculationType,
    ExecutionAttemptProducerRef,
    ExecutionAttemptStatus,
    RetrievalPolicy,
    SchedulerState,
    SchedulerType,
)
from ecatvasp.provenance import DependencyKind, DependencyRecord, ProvenanceRecord

_TAG = "$ecatvasp"

_DATACLASS_TYPES: tuple[type[Any], ...] = (
    Project,
    Catalyst,
    StructureVariant,
    StructureSnapshot,
    ActiveSite,
    AdsorptionState,
    StateConformer,
    Calculation,
    ExecutionAttempt,
    RemoteJob,
    Artifact,
    Analysis,
    ScientificWorkflowPlan,
    WorkflowStepBinding,
    WorkflowRecipeIdentity,
    WorkflowStepSpec,
    WorkflowEdgeSpec,
    DependencyRecord,
    ProvenanceRecord,
    CalculationProducerRef,
    ExecutionAttemptProducerRef,
    AnalysisProducerRef,
    Lattice,
    StructureSite,
    BindingEdge,
    SideLabel,
    ParameterEntry,
    PotcarIdentity,
    DftUSetting,
    KPointPolicy,
    MethodDefinition,
    ProtocolDefinition,
    RecipeIdentity,
    ExecutionSettings,
    ScientificInputDigest,
    MethodFingerprint,
)
_DATACLASS_BY_NAME = {item.__name__: item for item in _DATACLASS_TYPES}

_ENUM_TYPES: tuple[type[StrEnum], ...] = (
    StructureOrigin,
    VariantType,
    SiteSide,
    BindingMode,
    CalculationType,
    CalculationEngine,
    CalculationScientificStatus,
    ExecutionAttemptStatus,
    SchedulerType,
    SchedulerState,
    ArtifactType,
    ArtifactAvailability,
    RetrievalPolicy,
    AnalysisType,
    AnalysisStatus,
    ArtifactProducerKind,
    SpinTreatment,
    KPointPolicyKind,
    DipolePolicy,
    FingerprintCompatibility,
    DependencyKind,
)
_ENUM_BY_NAME = {item.__name__: item for item in _ENUM_TYPES}


class StorageCodecError(ValueError):
    """Raised when persisted domain data cannot be encoded or decoded safely."""


def dumps_storage(value: object) -> str:
    """Encode a supported domain value to deterministic JSON."""

    return json.dumps(
        _encode(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def loads_storage(payload: str) -> object:
    """Decode one deterministic domain JSON payload."""

    try:
        raw: object = json.loads(payload)
    except json.JSONDecodeError as error:
        raise StorageCodecError("invalid persisted JSON payload") from error
    return _decode(raw)


def _encode(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        encoded_fields: dict[str, object] = {}
        for item in fields(value):
            if item.init:
                encoded_fields[item.name] = _encode(getattr(value, item.name))
        return {
            _TAG: "dataclass",
            "class": type(value).__name__,
            "fields": encoded_fields,
        }
    if isinstance(value, StrEnum):
        return {_TAG: "enum", "class": type(value).__name__, "value": value.value}
    if isinstance(value, UUID):
        return {_TAG: "uuid", "value": str(value)}
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise StorageCodecError("persisted datetimes must be timezone-aware")
        return {_TAG: "datetime", "value": value.isoformat()}
    if isinstance(value, tuple):
        return {_TAG: "tuple", "items": [_encode(item) for item in value]}
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise StorageCodecError("persisted mappings require string keys")
        return {
            _TAG: "mapping",
            "items": {str(key): _encode(item) for key, item in value.items()},
        }
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise StorageCodecError(f"unsupported persisted value type: {type(value).__name__}")


def _decode(value: object) -> object:
    if isinstance(value, list):
        return [_decode(item) for item in value]
    if not isinstance(value, dict):
        return value

    mapping = cast(dict[str, object], value)
    tag = mapping.get(_TAG)
    if tag is None:
        return {key: _decode(item) for key, item in mapping.items()}
    if not isinstance(tag, str):
        raise StorageCodecError("invalid storage type tag")

    if tag == "uuid":
        raw_value = mapping.get("value")
        if not isinstance(raw_value, str):
            raise StorageCodecError("UUID payload requires a string value")
        try:
            return UUID(raw_value)
        except ValueError as error:
            raise StorageCodecError("invalid persisted UUID") from error

    if tag == "datetime":
        raw_value = mapping.get("value")
        if not isinstance(raw_value, str):
            raise StorageCodecError("datetime payload requires a string value")
        try:
            result = datetime.fromisoformat(raw_value)
        except ValueError as error:
            raise StorageCodecError("invalid persisted datetime") from error
        if result.tzinfo is None:
            raise StorageCodecError("persisted datetime is not timezone-aware")
        return result

    if tag == "tuple":
        raw_items = mapping.get("items")
        if not isinstance(raw_items, list):
            raise StorageCodecError("tuple payload requires an items list")
        return tuple(_decode(item) for item in raw_items)

    if tag == "mapping":
        raw_items = mapping.get("items")
        if not isinstance(raw_items, dict):
            raise StorageCodecError("mapping payload requires an items mapping")
        typed_items = cast(dict[str, object], raw_items)
        return {key: _decode(item) for key, item in typed_items.items()}

    if tag == "enum":
        class_name = mapping.get("class")
        raw_value = mapping.get("value")
        if not isinstance(class_name, str) or not isinstance(raw_value, str):
            raise StorageCodecError("enum payload requires class and value strings")
        enum_type = _ENUM_BY_NAME.get(class_name)
        if enum_type is None:
            raise StorageCodecError(f"unsupported persisted enum: {class_name}")
        try:
            return enum_type(raw_value)
        except ValueError as error:
            raise StorageCodecError(f"invalid {class_name} value") from error

    if tag == "dataclass":
        class_name = mapping.get("class")
        raw_fields = mapping.get("fields")
        if not isinstance(class_name, str) or not isinstance(raw_fields, dict):
            raise StorageCodecError("dataclass payload requires class and fields")
        constructor = _DATACLASS_BY_NAME.get(class_name)
        if constructor is None:
            raise StorageCodecError(f"unsupported persisted dataclass: {class_name}")
        typed_fields = cast(dict[str, object], raw_fields)
        kwargs = {key: _decode(item) for key, item in typed_fields.items()}
        try:
            return cast(object, constructor(**kwargs))
        except (TypeError, ValueError) as error:
            raise StorageCodecError(f"invalid persisted {class_name}") from error

    raise StorageCodecError(f"unsupported storage type tag: {tag}")
