from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest

from ecatvasp.desktop.host import (
    decode_versioned_desktop_request,
    run_stdio_host,
)
from ecatvasp.desktop.protocol import DesktopIPCError
from ecatvasp.desktop.protocol_v2_common import (
    DESKTOP_IPC_V2_CONTRACT_VERSION,
    DesktopV2Operation,
)
from ecatvasp.desktop.protocol_v2_electronic_analysis import (
    DesktopV2ElectronicAnalysisCatalogRequest,
    DesktopV2ElectronicAnalysisViewRequest,
    DesktopV2MaterializeBandCenterRequest,
    DesktopV2MaterializeDosAnalysisRequest,
)
from ecatvasp.desktop.protocol_v2_electronic_gateway import (
    decode_desktop_v2_block6_request,
)
from ecatvasp.domain import Project
from ecatvasp.storage import ProjectBundle, ProjectStore


def _request(operation: str, **fields: object) -> str:
    return json.dumps(
        {
            "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
            "request_id": f"{operation}-1",
            "operation": operation,
            "project_root": "/project",
            **fields,
        }
    )


def _empty_store(root: Path, name: str = "Electronic Workspace") -> ProjectStore:
    store = ProjectStore(root)
    store.save(
        ProjectBundle(
            project=Project(name=name, slug=name.casefold().replace(" ", "-"))
        )
    )
    return store


def test_block6_decoder_returns_closed_request_types() -> None:
    catalog = decode_desktop_v2_block6_request(_request("electronic_analysis_catalog"))
    assert isinstance(catalog, DesktopV2ElectronicAnalysisCatalogRequest)

    calculation_id = "01998f0c-7d00-7000-8000-000000000001"
    dos = decode_desktop_v2_block6_request(
        _request("materialize_dos_analysis", calculation_id=calculation_id)
    )
    assert isinstance(dos, DesktopV2MaterializeDosAnalysisRequest)
    assert dos.calculation_id == calculation_id

    analysis_id = "01998f0c-7d00-7000-8000-000000000002"
    view = decode_desktop_v2_block6_request(
        _request("electronic_analysis_view", analysis_id=analysis_id)
    )
    assert isinstance(view, DesktopV2ElectronicAnalysisViewRequest)
    assert view.analysis_id == analysis_id

    descriptor = decode_desktop_v2_block6_request(
        _request(
            "materialize_band_center",
            source_analysis_id=analysis_id,
            kind="d_band",
            scope="element",
            spin="sum",
            atom_uid=None,
            element="Fe",
            energy_reference="fermi_relative",
            window_lower_ev=-8.0,
            window_upper_ev=3.0,
        )
    )
    assert isinstance(descriptor, DesktopV2MaterializeBandCenterRequest)
    assert descriptor.operation is DesktopV2Operation.MATERIALIZE_BAND_CENTER


def test_block6_decoder_rejects_unknown_and_inconsistent_scientific_fields() -> None:
    with pytest.raises(DesktopIPCError, match="unknown fields"):
        decode_desktop_v2_block6_request(
            _request("electronic_analysis_catalog", scientific_override=True)
        )

    analysis_id = "01998f0c-7d00-7000-8000-000000000002"
    with pytest.raises(DesktopIPCError, match="atom band-center selector"):
        decode_desktop_v2_block6_request(
            _request(
                "materialize_band_center",
                source_analysis_id=analysis_id,
                kind="d_band",
                scope="atom",
                spin="sum",
                atom_uid=None,
                element="Fe",
                energy_reference="fermi_relative",
                window_lower_ev=-8.0,
                window_upper_ev=3.0,
            )
        )

    with pytest.raises(DesktopIPCError, match="positive width"):
        decode_desktop_v2_block6_request(
            _request(
                "materialize_band_center",
                source_analysis_id=analysis_id,
                kind="band",
                scope="system",
                spin="total",
                atom_uid=None,
                element=None,
                energy_reference="vasp_native",
                window_lower_ev=2.0,
                window_upper_ev=2.0,
            )
        )


def test_versioned_production_decoder_routes_block6_without_generic_fallback() -> None:
    decoded = decode_versioned_desktop_request(_request("electronic_analysis_catalog"))
    assert isinstance(decoded, DesktopV2ElectronicAnalysisCatalogRequest)

    with pytest.raises(DesktopIPCError, match="unsupported desktop operation"):
        decode_versioned_desktop_request(_request("run_analysis_shell", command="bader"))


def test_production_stdio_host_serves_electronic_catalog_statelessly(tmp_path: Path) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    store_a = _empty_store(root_a, "Electronic A")
    store_b = _empty_store(root_b, "Electronic B")

    requests = (
        {
            "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
            "request_id": "analysis-a",
            "operation": "electronic_analysis_catalog",
            "project_root": str(root_a),
        },
        {
            "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
            "request_id": "analysis-b",
            "operation": "electronic_analysis_catalog",
            "project_root": str(root_b),
        },
    )
    stdin = StringIO("".join(json.dumps(item) + "\n" for item in requests))
    stdout = StringIO()
    stderr = StringIO()

    assert run_stdio_host(stdin, stdout, stderr) == 0
    assert stderr.getvalue() == ""
    responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [item["operation"] for item in responses] == [
        "electronic_analysis_catalog",
        "electronic_analysis_catalog",
    ]
    assert responses[0]["payload"]["project_id"] == str(store_a.open().project.id)
    assert responses[1]["payload"]["project_id"] == str(store_b.open().project.id)
    assert responses[0]["payload"]["dos_sources"] == []
    assert responses[0]["payload"]["analyses"] == []
    assert responses[1]["payload"]["dos_sources"] == []
    assert responses[1]["payload"]["analyses"] == []
