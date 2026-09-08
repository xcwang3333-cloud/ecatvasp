"""Strict Job Center request contracts for desktop IPC v2."""

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
_EXECUTION_FIELDS = frozenset(
    {
        "ncore",
        "kpar",
        "nodes",
        "cores",
        "memory_mb",
        "walltime_seconds",
        "partition",
        "mpi_ranks",
        "omp_threads",
        "executable",
    }
)
_TARGET_FIELDS = frozenset(
    {
        "target_id",
        "host_alias",
        "remote_work_root",
        "potcar_resolver_id",
        "vasp_executable",
        "launcher",
        "module_loads",
    }
)
_REMOTE_POTCAR_FIELDS = frozenset({"resolver_id", "family", "root"})
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
class DesktopV2JobCatalogRequest:
    request_id: str
    project_root: str
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.JOB_CATALOG


@dataclass(frozen=True, slots=True)
class DesktopV2PrepareExecutionRequest:
    request_id: str
    project_root: str
    calculation_id: str
    potcar_root: str
    numerical_evidence: dict[str, Any]
    execution_settings: dict[str, Any]
    frequency_atom_uids: tuple[str, ...]
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.PREPARE_EXECUTION


@dataclass(frozen=True, slots=True)
class DesktopV2SubmitSlurmJobRequest:
    request_id: str
    project_root: str
    calculation_id: str
    potcar_root: str
    numerical_evidence: dict[str, Any]
    execution_settings: dict[str, Any]
    target: dict[str, Any]
    remote_potcar: dict[str, Any]
    frequency_atom_uids: tuple[str, ...]
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.SUBMIT_SLURM_JOB


@dataclass(frozen=True, slots=True)
class DesktopV2RemoteJobRequest:
    request_id: str
    project_root: str
    operation: DesktopV2Operation
    remote_job_id: str
    target: dict[str, Any]
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.operation not in {
            DesktopV2Operation.REFRESH_SLURM_JOB,
            DesktopV2Operation.CANCEL_SLURM_JOB,
        }:
            raise DesktopIPCError("remote Job Center request operation is invalid")


@dataclass(frozen=True, slots=True)
class DesktopV2RetrieveJobOutputsRequest:
    request_id: str
    project_root: str
    remote_job_id: str
    target: dict[str, Any]
    requested_roles: tuple[str, ...]
    release_remote_roles: tuple[str, ...]
    discard_remote_roles: tuple[str, ...]
    protocol_version: str = DESKTOP_IPC_V2_CONTRACT_VERSION
    operation: DesktopV2Operation = DesktopV2Operation.RETRIEVE_JOB_OUTPUTS


DesktopV2JobCenterRequest: TypeAlias = (
    DesktopV2JobCatalogRequest
    | DesktopV2PrepareExecutionRequest
    | DesktopV2SubmitSlurmJobRequest
    | DesktopV2RemoteJobRequest
    | DesktopV2RetrieveJobOutputsRequest
)


def is_desktop_v2_job_center_request(
    value: object,
) -> TypeGuard[DesktopV2JobCenterRequest]:
    return isinstance(
        value,
        (
            DesktopV2JobCatalogRequest,
            DesktopV2PrepareExecutionRequest,
            DesktopV2SubmitSlurmJobRequest,
            DesktopV2RemoteJobRequest,
            DesktopV2RetrieveJobOutputsRequest,
        ),
    )


def decode_desktop_v2_job_center_request(
    raw: dict[str, Any],
    *,
    operation: DesktopV2Operation,
    request_id: str,
) -> DesktopV2JobCenterRequest:
    """Decode one strict Job Center operation, rejecting extra or credential fields."""

    project_root = _required_string(raw, "project_root")
    if operation is DesktopV2Operation.JOB_CATALOG:
        _reject_unknown(raw, _BASE, operation)
        return DesktopV2JobCatalogRequest(request_id=request_id, project_root=project_root)

    if operation in {
        DesktopV2Operation.PREPARE_EXECUTION,
        DesktopV2Operation.SUBMIT_SLURM_JOB,
    }:
        allowed = _BASE | {
            "calculation_id",
            "potcar_root",
            "numerical_evidence",
            "execution_settings",
            "frequency_atom_uids",
        }
        if operation is DesktopV2Operation.SUBMIT_SLURM_JOB:
            allowed |= {"target", "remote_potcar"}
        _reject_unknown(raw, allowed, operation)
        calculation_id = _required_uuid(raw, "calculation_id")
        potcar_root = _required_string(raw, "potcar_root")
        numerical_evidence = _numerical_evidence_object(raw, operation)
        execution_settings = _execution_object(raw, operation)
        frequency_atom_uids = _uuid_list(raw, "frequency_atom_uids", required=False)
        if operation is DesktopV2Operation.PREPARE_EXECUTION:
            return DesktopV2PrepareExecutionRequest(
                request_id=request_id,
                project_root=project_root,
                calculation_id=calculation_id,
                potcar_root=potcar_root,
                numerical_evidence=numerical_evidence,
                execution_settings=execution_settings,
                frequency_atom_uids=frequency_atom_uids,
            )
        return DesktopV2SubmitSlurmJobRequest(
            request_id=request_id,
            project_root=project_root,
            calculation_id=calculation_id,
            potcar_root=potcar_root,
            numerical_evidence=numerical_evidence,
            execution_settings=execution_settings,
            target=_target_object(raw, operation),
            remote_potcar=_remote_potcar_object(raw, operation),
            frequency_atom_uids=frequency_atom_uids,
        )

    if operation in {
        DesktopV2Operation.REFRESH_SLURM_JOB,
        DesktopV2Operation.CANCEL_SLURM_JOB,
    }:
        _reject_unknown(raw, _BASE | {"remote_job_id", "target"}, operation)
        return DesktopV2RemoteJobRequest(
            request_id=request_id,
            project_root=project_root,
            operation=operation,
            remote_job_id=_required_uuid(raw, "remote_job_id"),
            target=_target_object(raw, operation),
        )

    if operation is DesktopV2Operation.RETRIEVE_JOB_OUTPUTS:
        _reject_unknown(
            raw,
            _BASE
            | {
                "remote_job_id",
                "target",
                "requested_roles",
                "release_remote_roles",
                "discard_remote_roles",
            },
            operation,
        )
        return DesktopV2RetrieveJobOutputsRequest(
            request_id=request_id,
            project_root=project_root,
            remote_job_id=_required_uuid(raw, "remote_job_id"),
            target=_target_object(raw, operation),
            requested_roles=_string_list(raw, "requested_roles", required=False),
            release_remote_roles=_string_list(
                raw,
                "release_remote_roles",
                required=False,
            ),
            discard_remote_roles=_string_list(
                raw,
                "discard_remote_roles",
                required=False,
            ),
        )

    raise DesktopIPCError("operation is not a Job Center request")


def _execution_object(
    raw: dict[str, Any],
    operation: DesktopV2Operation,
) -> dict[str, Any]:
    value = _object(raw, "execution_settings")
    _reject_unknown(value, _EXECUTION_FIELDS, operation, prefix="execution_settings")
    for field in ("nodes", "cores", "mpi_ranks", "walltime_seconds"):
        _positive_int(value, field, required=True)
    for field in ("ncore", "kpar", "memory_mb", "omp_threads"):
        _positive_int(value, field, required=False)
    _required_string(value, "executable")
    partition = value.get("partition")
    if partition is not None and (not isinstance(partition, str) or not partition.strip()):
        raise DesktopIPCError("execution_settings.partition must be a non-blank string")
    return value


def _target_object(
    raw: dict[str, Any],
    operation: DesktopV2Operation,
) -> dict[str, Any]:
    value = _object(raw, "target")
    _reject_unknown(value, _TARGET_FIELDS, operation, prefix="target")
    for field in (
        "target_id",
        "host_alias",
        "remote_work_root",
        "potcar_resolver_id",
        "vasp_executable",
    ):
        _required_string(value, field)
    launcher = value.get("launcher")
    if launcher is not None and (not isinstance(launcher, str) or not launcher.strip()):
        raise DesktopIPCError("target.launcher must be a non-blank string when supplied")
    modules = value.get("module_loads", [])
    if not isinstance(modules, list) or any(
        not isinstance(item, str) or not item.strip() for item in modules
    ):
        raise DesktopIPCError("target.module_loads must be a string list")
    if len(modules) != len(set(modules)):
        raise DesktopIPCError("target.module_loads must not contain duplicates")
    return value


def _remote_potcar_object(
    raw: dict[str, Any],
    operation: DesktopV2Operation,
) -> dict[str, Any]:
    value = _object(raw, "remote_potcar")
    _reject_unknown(value, _REMOTE_POTCAR_FIELDS, operation, prefix="remote_potcar")
    for field in ("resolver_id", "family", "root"):
        _required_string(value, field)
    return value


def _numerical_evidence_object(
    raw: dict[str, Any],
    operation: DesktopV2Operation,
) -> dict[str, Any]:
    evidence = _object(raw, "numerical_evidence")
    _reject_unknown(
        evidence,
        {"encut", "kpoints"},
        operation,
        prefix="numerical_evidence",
    )
    encut = _object(evidence, "encut")
    _reject_unknown(
        encut,
        _ENCUT_FIELDS,
        operation,
        prefix="numerical_evidence.encut",
    )
    for field in ("core_method_hash", "potcar_spec_hash", "analysis_hash"):
        _sha256(encut, field)
    tested = encut.get("tested_encuts_ev")
    if not isinstance(tested, list) or not tested or any(
        isinstance(item, bool) or not isinstance(item, (int, float)) for item in tested
    ):
        raise DesktopIPCError(
            "numerical_evidence.encut.tested_encuts_ev must be a non-empty number list"
        )
    _number(encut, "selected_encut_ev")

    kpoints = evidence.get("kpoints")
    if kpoints is not None:
        if not isinstance(kpoints, dict):
            raise DesktopIPCError("numerical_evidence.kpoints must be an object")
        _reject_unknown(
            kpoints,
            _KPOINT_FIELDS,
            operation,
            prefix="numerical_evidence.kpoints",
        )
        for field in ("core_method_hash", "selected_plan_hash", "analysis_hash"):
            _sha256(kpoints, field)
        tested_hashes = kpoints.get("tested_plan_hashes")
        if (
            not isinstance(tested_hashes, list)
            or not tested_hashes
            or any(not _is_sha256(item) for item in tested_hashes)
        ):
            raise DesktopIPCError(
                "numerical_evidence.kpoints.tested_plan_hashes are invalid"
            )
        _required_string(kpoints, "system_kind")
    return evidence


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


def _positive_int(raw: dict[str, Any], field: str, *, required: bool) -> int | None:
    value = raw.get(field)
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DesktopIPCError(f"{field} must be a positive integer")
    return value


def _number(raw: dict[str, Any], field: str) -> float:
    value = raw.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DesktopIPCError(f"{field} must be numeric")
    return float(value)


def _sha256(raw: dict[str, Any], field: str) -> str:
    value = raw.get(field)
    if not _is_sha256(value):
        raise DesktopIPCError(f"{field} must be a SHA-256 digest")
    assert isinstance(value, str)
    return value


def _uuid_list(
    raw: dict[str, Any],
    field: str,
    *,
    required: bool,
) -> tuple[str, ...]:
    values = _string_list(raw, field, required=required)
    for value in values:
        try:
            UUID(value)
        except ValueError as error:
            raise DesktopIPCError(f"{field} must contain UUIDs") from error
    return values


def _string_list(
    raw: dict[str, Any],
    field: str,
    *,
    required: bool,
) -> tuple[str, ...]:
    value = raw.get(field)
    if value is None and not required:
        return ()
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise DesktopIPCError(f"{field} must be a string list")
    if len(value) != len(set(value)):
        raise DesktopIPCError(f"{field} must not contain duplicates")
    return tuple(value)


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


__all__ = [
    "DesktopV2JobCenterRequest",
    "DesktopV2JobCatalogRequest",
    "DesktopV2PrepareExecutionRequest",
    "DesktopV2RemoteJobRequest",
    "DesktopV2RetrieveJobOutputsRequest",
    "DesktopV2SubmitSlurmJobRequest",
    "decode_desktop_v2_job_center_request",
    "is_desktop_v2_job_center_request",
]
