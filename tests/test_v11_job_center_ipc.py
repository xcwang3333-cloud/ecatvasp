from __future__ import annotations

import json

import pytest

from ecatvasp.desktop import (
    DESKTOP_IPC_V2_CONTRACT_VERSION,
    DesktopIPCError,
    DesktopV2JobCatalogRequest,
    DesktopV2PrepareExecutionRequest,
    DesktopV2SubmitSlurmJobRequest,
    decode_desktop_v2_request,
)

_PROJECT_ROOT = "/project"
_CALCULATION_ID = "018f0000-0000-7000-8000-000000000001"


def _execution_settings() -> dict[str, object]:
    return {
        "nodes": 1,
        "cores": 32,
        "mpi_ranks": 32,
        "walltime_seconds": 3600,
        "executable": "vasp_std",
    }


def _target() -> dict[str, object]:
    return {
        "target_id": "cluster-a",
        "host_alias": "cluster-a",
        "remote_work_root": "/scratch/ecatvasp",
        "potcar_resolver_id": "pbe54-remote",
        "vasp_executable": "vasp_std",
        "launcher": "srun",
        "module_loads": ["vasp/6"],
    }


def _base(operation: str) -> dict[str, object]:
    return {
        "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
        "request_id": f"{operation}-1",
        "operation": operation,
        "project_root": _PROJECT_ROOT,
    }


def test_job_catalog_and_prepare_execution_decode_to_typed_requests() -> None:
    catalog = decode_desktop_v2_request(json.dumps(_base("job_catalog")))
    assert isinstance(catalog, DesktopV2JobCatalogRequest)

    raw = _base("prepare_execution")
    raw.update(
        {
            "calculation_id": _CALCULATION_ID,
            "potcar_root": "/licensed/PBE_54",
            "execution_settings": _execution_settings(),
        }
    )
    prepared = decode_desktop_v2_request(json.dumps(raw))
    assert isinstance(prepared, DesktopV2PrepareExecutionRequest)
    assert prepared.calculation_id == _CALCULATION_ID


def test_submit_slurm_job_uses_noncredential_target_contract() -> None:
    raw = _base("submit_slurm_job")
    raw.update(
        {
            "calculation_id": _CALCULATION_ID,
            "potcar_root": "/licensed/PBE_54",
            "execution_settings": _execution_settings(),
            "target": _target(),
            "remote_potcar": {
                "resolver_id": "pbe54-remote",
                "family": "PBE_54",
                "root": "/apps/vasp/potpaw_PBE.54",
            },
        }
    )
    request = decode_desktop_v2_request(json.dumps(raw))
    assert isinstance(request, DesktopV2SubmitSlurmJobRequest)
    assert set(request.target) == {
        "target_id",
        "host_alias",
        "remote_work_root",
        "potcar_resolver_id",
        "vasp_executable",
        "launcher",
        "module_loads",
    }


@pytest.mark.parametrize("credential_field", ["password", "private_key", "token"])
def test_job_center_target_rejects_credential_fields(credential_field: str) -> None:
    raw = _base("submit_slurm_job")
    target = _target()
    target[credential_field] = "secret"
    raw.update(
        {
            "calculation_id": _CALCULATION_ID,
            "potcar_root": "/licensed/PBE_54",
            "execution_settings": _execution_settings(),
            "target": target,
            "remote_potcar": {
                "resolver_id": "pbe54-remote",
                "family": "PBE_54",
                "root": "/apps/vasp/potpaw_PBE.54",
            },
        }
    )
    with pytest.raises(DesktopIPCError, match="target contains unknown fields"):
        decode_desktop_v2_request(json.dumps(raw))


def test_prepare_execution_rejects_manual_numerical_evidence_payload() -> None:
    raw = _base("prepare_execution")
    raw.update(
        {
            "calculation_id": _CALCULATION_ID,
            "potcar_root": "/licensed/PBE_54",
            "execution_settings": _execution_settings(),
            "numerical_evidence": {"analysis_hash": "a" * 64},
        }
    )
    with pytest.raises(DesktopIPCError, match="unknown fields"):
        decode_desktop_v2_request(json.dumps(raw))


def test_prepare_execution_rejects_nonpositive_scheduler_resources() -> None:
    settings = _execution_settings()
    settings["nodes"] = 0
    raw = _base("prepare_execution")
    raw.update(
        {
            "calculation_id": _CALCULATION_ID,
            "potcar_root": "/licensed/PBE_54",
            "execution_settings": settings,
        }
    )
    with pytest.raises(DesktopIPCError, match="nodes must be a positive integer"):
        decode_desktop_v2_request(json.dumps(raw))
