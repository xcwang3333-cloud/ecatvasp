from __future__ import annotations

import json
from uuid import uuid4

import pytest

from ecatvasp.desktop.protocol import DesktopIPCError
from ecatvasp.desktop.protocol_v2_common import DESKTOP_IPC_V2_CONTRACT_VERSION
from ecatvasp.desktop.protocol_v2_reaction import (
    DesktopV2HERAnalysisBindings,
    DesktopV2MaterializeReactionDiagramRequest,
    DesktopV2ReactionDiagramViewRequest,
    DesktopV2ReactionPreviewRequest,
)
from ecatvasp.desktop.protocol_v2_thermochemistry_gateway import (
    decode_desktop_v2_block7_request,
)


def _conditions(potential_v: float) -> dict[str, object]:
    return {
        "temperature_k": 298.15,
        "potential_v": potential_v,
        "ph": 0.0,
        "potential_reference": "she",
        "ph_semantics": "explicit_activity",
    }


def _her(operation: str = "reaction_preview") -> dict[str, object]:
    return {
        "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
        "request_id": f"request-{operation}",
        "operation": operation,
        "project_root": "/project",
        "preset_kind": "her_volmer_heyrovsky",
        "bindings": {
            "clean_surface_analysis_id": str(uuid4()),
            "h_adsorbed_analysis_id": str(uuid4()),
            "h2_reference_analysis_id": str(uuid4()),
        },
        "baseline_conditions": _conditions(0.0),
        "requested_conditions": _conditions(-0.2),
    }


def test_block7_decodes_fixed_reaction_preview_materialization_and_view() -> None:
    preview = decode_desktop_v2_block7_request(json.dumps(_her()))
    assert isinstance(preview, DesktopV2ReactionPreviewRequest)
    assert preview.preset_kind == "her_volmer_heyrovsky"
    assert isinstance(preview.bindings, DesktopV2HERAnalysisBindings)
    assert preview.requested_conditions.potential_v == -0.2

    materialize = decode_desktop_v2_block7_request(
        json.dumps(_her("materialize_reaction_diagram"))
    )
    assert isinstance(materialize, DesktopV2MaterializeReactionDiagramRequest)

    view = decode_desktop_v2_block7_request(
        json.dumps(
            {
                "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
                "request_id": "reaction-view",
                "operation": "reaction_diagram_view",
                "project_root": "/project",
                "analysis_id": str(uuid4()),
            }
        )
    )
    assert isinstance(view, DesktopV2ReactionDiagramViewRequest)


def test_reaction_ipc_rejects_free_energy_and_generic_payload_escape_hatches() -> None:
    for field, value in (
        ("payload", {"steps": []}),
        ("free_energy_table", [0.0, -0.1]),
        ("gibbs_energy_ev", -1.0),
        ("source_hash", "a" * 64),
        ("artifact_path", "/tmp/source.json"),
    ):
        request = _her()
        request[field] = value
        with pytest.raises(DesktopIPCError, match="unknown fields"):
            decode_desktop_v2_block7_request(json.dumps(request))


def test_reaction_ipc_rejects_unknown_or_wrong_preset_bindings() -> None:
    unknown = _her()
    bindings = unknown["bindings"]
    assert isinstance(bindings, dict)
    bindings["manual_energy_ev"] = -0.1
    with pytest.raises(DesktopIPCError, match="unknown fields"):
        decode_desktop_v2_block7_request(json.dumps(unknown))

    wrong = _her()
    wrong["preset_kind"] = "orr_associative_4e"
    with pytest.raises(DesktopIPCError):
        decode_desktop_v2_block7_request(json.dumps(wrong))


def test_reaction_ipc_rejects_unknown_conditions_and_temperature_drift() -> None:
    unknown = _her()
    conditions = unknown["requested_conditions"]
    assert isinstance(conditions, dict)
    conditions["solvation_override_ev"] = -0.2
    with pytest.raises(DesktopIPCError, match="unknown fields"):
        decode_desktop_v2_block7_request(json.dumps(unknown))

    drift = _her()
    requested = drift["requested_conditions"]
    assert isinstance(requested, dict)
    requested["temperature_k"] = 310.0
    with pytest.raises(DesktopIPCError, match="same temperature_k"):
        decode_desktop_v2_block7_request(json.dumps(drift))


def test_reaction_ipc_rejects_duplicate_analysis_bindings_and_view_hash_injection() -> None:
    request = _her()
    bindings = request["bindings"]
    assert isinstance(bindings, dict)
    duplicate = bindings["clean_surface_analysis_id"]
    bindings["h_adsorbed_analysis_id"] = duplicate
    with pytest.raises(DesktopIPCError, match="must be distinct"):
        decode_desktop_v2_block7_request(json.dumps(request))

    view = {
        "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
        "request_id": "reaction-view-bad",
        "operation": "reaction_diagram_view",
        "project_root": "/project",
        "analysis_id": str(uuid4()),
        "dataset_hash": "a" * 64,
    }
    with pytest.raises(DesktopIPCError, match="unknown fields"):
        decode_desktop_v2_block7_request(json.dumps(view))
