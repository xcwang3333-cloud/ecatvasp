"""Desktop-facing typed Job Center actions for v1.1 Block 4."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from ecatvasp.api.job_center import (
    JobCenterExecutionPreparation,
    JobCenterObservationReceipt,
    ProjectJobCenterApplicationService,
)
from ecatvasp.desktop.calculation_wizard import numerical_evidence_from_payload
from ecatvasp.domain import (
    CalculationId,
    ExecutionSettings,
    RemoteJobId,
    SchedulerType,
)
from ecatvasp.execution.remote import RemotePotcarLibrary
from ecatvasp.execution.ssh import OpenSshTransport
from ecatvasp.execution.targets import (
    ExecutionTargetProfile,
    SshSecurityPolicy,
    TransportKind,
)
from ecatvasp.storage import ProjectStore


def job_catalog_action(project_root: Path | str) -> dict[str, object]:
    """Return task-oriented execution state without collapsing lifecycle namespaces."""

    service = ProjectJobCenterApplicationService(ProjectStore(project_root))
    return {"project_root": str(Path(project_root)), **service.catalog()}


def prepare_execution_action(
    *,
    project_root: Path | str,
    calculation_id: str,
    potcar_root: str,
    numerical_evidence: dict[str, Any],
    execution_settings: dict[str, Any],
    frequency_atom_uids: tuple[str, ...] = (),
) -> dict[str, object]:
    """Validate and preview one exact execution plan without scheduler side effects."""

    preparation = _prepare_execution(
        project_root=project_root,
        calculation_id=calculation_id,
        potcar_root=potcar_root,
        numerical_evidence=numerical_evidence,
        execution_settings=execution_settings,
        frequency_atom_uids=frequency_atom_uids,
    )
    return {
        "project_root": str(Path(project_root)),
        "calculation_id": str(preparation.calculation_id),
        "workflow_plan_id": str(preparation.workflow_plan_id),
        "step_key": preparation.step_key,
        "plan_hash": preparation.plan.plan_hash,
        "execution_settings_hash": preparation.plan.execution_settings_hash,
        "input_manifest_sha256": preparation.plan.input_manifest_sha256,
        "reused_input_artifacts": preparation.reused_input_artifacts,
        "expected_outputs": [
            {
                "role": item.role,
                "artifact_type": item.artifact_type.value,
                "relative_path": item.relative_path,
                "retrieval_policy": item.retrieval_policy.value,
                "required": item.required,
            }
            for item in preparation.plan.expected_outputs
        ],
    }


def submit_slurm_job_action(
    *,
    project_root: Path | str,
    calculation_id: str,
    potcar_root: str,
    numerical_evidence: dict[str, Any],
    execution_settings: dict[str, Any],
    target: dict[str, Any],
    remote_potcar: dict[str, Any],
    frequency_atom_uids: tuple[str, ...] = (),
) -> dict[str, object]:
    """Prepare current inputs, then stage/submit through the system OpenSSH boundary."""

    preparation = _prepare_execution(
        project_root=project_root,
        calculation_id=calculation_id,
        potcar_root=potcar_root,
        numerical_evidence=numerical_evidence,
        execution_settings=execution_settings,
        frequency_atom_uids=frequency_atom_uids,
    )
    service = ProjectJobCenterApplicationService(ProjectStore(project_root))
    receipt = service.submit_remote_slurm(
        preparation=preparation,
        target=_target_profile(target),
        remote_potcars=_remote_potcar_library(remote_potcar),
        transport=OpenSshTransport(),
    )
    return {
        "project_root": str(Path(project_root)),
        "calculation_id": str(receipt.calculation_id),
        "attempt_id": str(receipt.attempt_id),
        "remote_job_id": str(receipt.remote_job_id),
        "attempt_status": receipt.attempt_status.value,
        "scheduler_state": receipt.scheduler_state.value,
        "scheduler_job_id": receipt.scheduler_job_id,
        "plan_hash": receipt.plan_hash,
    }


def refresh_slurm_job_action(
    *,
    project_root: Path | str,
    remote_job_id: str,
    target: dict[str, Any],
) -> dict[str, object]:
    """Observe one Slurm job and persist scheduler/execution truth separately."""

    service = ProjectJobCenterApplicationService(ProjectStore(project_root))
    receipt = service.refresh_job(
        remote_job_id=RemoteJobId(UUID(remote_job_id)),
        target=_target_profile(target),
        transport=OpenSshTransport(),
    )
    return _observation_payload(project_root, receipt)


def cancel_slurm_job_action(
    *,
    project_root: Path | str,
    remote_job_id: str,
    target: dict[str, Any],
) -> dict[str, object]:
    """Request Slurm cancellation and persist only the state actually observed."""

    service = ProjectJobCenterApplicationService(ProjectStore(project_root))
    receipt = service.cancel_job(
        remote_job_id=RemoteJobId(UUID(remote_job_id)),
        target=_target_profile(target),
        transport=OpenSshTransport(),
    )
    return _observation_payload(project_root, receipt)


def retrieve_job_outputs_action(
    *,
    project_root: Path | str,
    remote_job_id: str,
    target: dict[str, Any],
    requested_roles: tuple[str, ...] = (),
    release_remote_roles: tuple[str, ...] = (),
    discard_remote_roles: tuple[str, ...] = (),
) -> dict[str, object]:
    """Retrieve policy-selected outputs without performing scientific result parsing."""

    service = ProjectJobCenterApplicationService(ProjectStore(project_root))
    receipt = service.retrieve_job_outputs(
        remote_job_id=RemoteJobId(UUID(remote_job_id)),
        target=_target_profile(target),
        transport=OpenSshTransport(),
        requested_roles=requested_roles,
        release_remote_roles=release_remote_roles,
        discard_remote_roles=discard_remote_roles,
    )
    return {
        "project_root": str(Path(project_root)),
        "calculation_id": str(receipt.calculation_id),
        "attempt_id": str(receipt.attempt_id),
        "remote_job_id": str(receipt.remote_job_id),
        "attempt_status": receipt.attempt_status.value,
        "retrieved_artifact_ids": list(receipt.retrieved_artifact_ids),
        "retrieval_hash": receipt.retrieval_hash,
    }


def _prepare_execution(
    *,
    project_root: Path | str,
    calculation_id: str,
    potcar_root: str,
    numerical_evidence: dict[str, Any],
    execution_settings: dict[str, Any],
    frequency_atom_uids: tuple[str, ...],
) -> JobCenterExecutionPreparation:
    service = ProjectJobCenterApplicationService(ProjectStore(project_root))
    return service.prepare_execution(
        calculation_id=CalculationId(UUID(calculation_id)),
        potcar_root=Path(potcar_root),
        numerical_evidence=numerical_evidence_from_payload(numerical_evidence),
        execution_settings=_execution_settings(execution_settings),
        frequency_atom_uids=frequency_atom_uids,
    )


def _execution_settings(raw: dict[str, Any]) -> ExecutionSettings:
    return ExecutionSettings(
        ncore=_optional_int(raw.get("ncore")),
        kpar=_optional_int(raw.get("kpar")),
        nodes=_optional_int(raw.get("nodes")),
        cores=_optional_int(raw.get("cores")),
        memory_mb=_optional_int(raw.get("memory_mb")),
        walltime_seconds=_optional_int(raw.get("walltime_seconds")),
        partition=_optional_text(raw.get("partition")),
        mpi_ranks=_optional_int(raw.get("mpi_ranks")),
        omp_threads=_optional_int(raw.get("omp_threads")),
        executable=str(raw.get("executable", "vasp_std")),
    )


def _target_profile(raw: dict[str, Any]) -> ExecutionTargetProfile:
    module_loads_raw = raw.get("module_loads", [])
    if not isinstance(module_loads_raw, list) or any(
        not isinstance(item, str) for item in module_loads_raw
    ):
        raise ValueError("target.module_loads must be a string list")
    return ExecutionTargetProfile(
        target_id=str(raw["target_id"]),
        transport=TransportKind.SSH,
        scheduler=SchedulerType.SLURM,
        host_alias=str(raw["host_alias"]),
        remote_work_root=str(raw["remote_work_root"]),
        potcar_resolver_id=str(raw["potcar_resolver_id"]),
        vasp_executable=str(raw.get("vasp_executable", "vasp_std")),
        launcher=_optional_text(raw.get("launcher")),
        module_loads=tuple(module_loads_raw),
        ssh_security=SshSecurityPolicy(),
    )


def _remote_potcar_library(raw: dict[str, Any]) -> RemotePotcarLibrary:
    return RemotePotcarLibrary(
        resolver_id=str(raw["resolver_id"]),
        family=str(raw["family"]),
        root=str(raw["root"]),
    )


def _observation_payload(
    project_root: Path | str,
    receipt: JobCenterObservationReceipt,
) -> dict[str, object]:
    return {
        "project_root": str(Path(project_root)),
        "calculation_id": str(receipt.calculation_id),
        "attempt_id": str(receipt.attempt_id),
        "remote_job_id": str(receipt.remote_job_id),
        "attempt_status": receipt.attempt_status.value,
        "scheduler_state": receipt.scheduler_state.value,
        "ionic_step": receipt.ionic_step,
        "electronic_iteration": receipt.electronic_iteration,
    }


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("execution integer fields must be integers and not boolean")
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional text fields must be strings")
    return value if value.strip() else None


__all__ = [
    "cancel_slurm_job_action",
    "job_catalog_action",
    "prepare_execution_action",
    "refresh_slurm_job_action",
    "retrieve_job_outputs_action",
    "submit_slurm_job_action",
]
