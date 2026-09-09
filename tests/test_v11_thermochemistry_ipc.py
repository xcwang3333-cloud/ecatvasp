from __future__ import annotations

import json
from uuid import uuid4

import pytest

from ecatvasp.desktop.protocol import DesktopIPCError
from ecatvasp.desktop.protocol_v2_common import DESKTOP_IPC_V2_CONTRACT_VERSION
from ecatvasp.desktop.protocol_v2_reaction import (
    DesktopV2MaterializeReactionDiagramRequest,
    DesktopV2ReactionDiagramViewRequest,
    DesktopV2ReactionPreviewRequest,
)
from ecatvasp.desktop.protocol_v2_thermochemistry import (
    DesktopV2MaterializeGasReferenceRequest,
    DesktopV2MaterializeHarmonicThermochemistryRequest,
    DesktopV2ThermochemistryCatalogRequest,
    DesktopV2ThermochemistryViewRequest,
)
from ecatvasp.desktop.protocol_v2_thermochemistry_gateway import (
    decode_desktop_v2_block7_request,
)


def _base(operation: str) -> dict[str, object]:
    return {
        "protocol_version": DESKTOP_IPC_V2_CONTRACT_VERSION,
        "request_id": f"request-{operation}",
        "operation": operation,
        "project_root": "/project",
    }


def _harmonic() -> dict[str, object]:
    return {
        **_base("materialize_harmonic_thermochemistry"),
        "calculation_id": str(uuid4()),
        "subject_kind": "adsorbate",
        "temperature_k": 298.15,
        "electronic_energy_kind": "energy_sigma0_ev",
        "electronic_entropy_policy": "neglected",
        "frequency_cutoff_cm_inverse": 50.0,
        "imaginary_mode_policy": "exclude_explicit",
        "low_frequency_policy": "exclude_explicit",
        "exclusions": [
            {"mode_index": 1, "reason": "imaginary", "note": "explicit transition noise"}
        ],
    }


def _gas() -> dict[str, object]:
    atom_a = str(uuid4())
    atom_b = str(uuid4())
    return {
        **_base("materialize_gas_reference"),
        "calculation_id": str(uuid4()),
        "species": "H2",
        "temperature_k": 298.15,
        "pressure_pa": 100000.0,
        "standard_state": "ideal_gas_1_bar",
        "electronic_energy_kind": "energy_sigma0_ev",
        "electronic_entropy_policy": "spin_degeneracy",
        "geometry_kind": "linear",
        "symmetry_number": 2,
        "spin_multiplicity": 1,
        "atomic_masses": [
            {"atom_uid": atom_a, "mass_amu": 1.00784},
            {"atom_uid": atom_b, "mass_amu": 1.00784},
        ],
        "frequency_cutoff_cm_inverse": 50.0,
        "imaginary_mode_policy": "reject_any",
        "low_frequency_policy": "exclude_explicit",
        "exclusions": [],
    }


def _reaction(operation: str = "reaction_preview") -> dict[str, object]:
    return {
        **_base(operation),
        "preset_kind": "her_volmer_heyrovsky",
        "bindings": {
            "clean_surface_analysis_id": str(uuid4()),
            "h_adsorbed_analysis_id": str(uuid4()),
            "h2_reference_analysis_id": str(uuid4()),
        },
        "baseline_conditions": {
            "temperature_k": 298.15,
            "potential_v": 0.0,
            "ph": 0.0,
            "potential_reference": "she",
            "ph_semantics": "explicit_activity",
        },
        "requested_conditions": {
            "temperature_k": 298.15,
            "potential_v": -0.2,
            "ph": 0.0,
            "potential_reference": "she",
            "ph_semantics": "explicit_activity",
        },
    }


def test_block7_decodes_operation_specific_thermochemistry_requests() -> None:
    catalog = decode_desktop_v2_block7_request(
        json.dumps(_base("thermochemistry_catalog"))
    )
    assert isinstance(catalog, DesktopV2ThermochemistryCatalogRequest)

    harmonic = decode_desktop_v2_block7_request(json.dumps(_harmonic()))
    assert isinstance(harmonic, DesktopV2MaterializeHarmonicThermochemistryRequest)
    assert harmonic.exclusions[0].mode_index == 1
    assert harmonic.exclusions[0].reason == "imaginary"

    gas = decode_desktop_v2_block7_request(json.dumps(_gas()))
    assert isinstance(gas, DesktopV2MaterializeGasReferenceRequest)
    assert gas.species == "H2"
    assert len(gas.atomic_masses) == 2

    view_payload = {
        **_base("thermochemistry_view"),
        "analysis_id": str(uuid4()),
    }
    view = decode_desktop_v2_block7_request(json.dumps(view_payload))
    assert isinstance(view, DesktopV2ThermochemistryViewRequest)


def test_block7_decodes_operation_specific_reaction_requests() -> None:
    preview = decode_desktop_v2_block7_request(json.dumps(_reaction()))
    assert isinstance(preview, DesktopV2ReactionPreviewRequest)
    assert preview.preset_kind == "her_volmer_heyrovsky"

    materialize = decode_desktop_v2_block7_request(
        json.dumps(_reaction("materialize_reaction_diagram"))
    )
    assert isinstance(materialize, DesktopV2MaterializeReactionDiagramRequest)

    view = decode_desktop_v2_block7_request(
        json.dumps(
            {
                **_base("reaction_diagram_view"),
                "analysis_id": str(uuid4()),
            }
        )
    )
    assert isinstance(view, DesktopV2ReactionDiagramViewRequest)


def test_block7_rejects_generic_scientific_escape_hatches() -> None:
    for field, value in (
        ("payload", {"energy_ev": -10.0}),
        ("artifact_path", "/tmp/result.json"),
        ("source_hash", "a" * 64),
        ("gibbs_energy_ev", -1.0),
        ("scientific_verdict", "accepted"),
    ):
        request = _harmonic()
        request[field] = value
        with pytest.raises(DesktopIPCError, match="unknown fields"):
            decode_desktop_v2_block7_request(json.dumps(request))

        reaction = _reaction()
        reaction[field] = value
        with pytest.raises(DesktopIPCError, match="unknown fields"):
            decode_desktop_v2_block7_request(json.dumps(reaction))


def test_block7_rejects_unknown_nested_thermochemistry_fields() -> None:
    harmonic = _harmonic()
    exclusions = harmonic["exclusions"]
    assert isinstance(exclusions, list)
    exclusion = exclusions[0]
    assert isinstance(exclusion, dict)
    exclusion["energy_override_ev"] = -0.1
    with pytest.raises(DesktopIPCError, match="unknown fields"):
        decode_desktop_v2_block7_request(json.dumps(harmonic))

    gas = _gas()
    masses = gas["atomic_masses"]
    assert isinstance(masses, list)
    mass = masses[0]
    assert isinstance(mass, dict)
    mass["element"] = "H"
    with pytest.raises(DesktopIPCError, match="unknown fields"):
        decode_desktop_v2_block7_request(json.dumps(gas))


def test_block7_rejects_unknown_nested_reaction_fields() -> None:
    reaction = _reaction()
    bindings = reaction["bindings"]
    assert isinstance(bindings, dict)
    bindings["clean_surface_energy_ev"] = -10.0
    with pytest.raises(DesktopIPCError, match="unknown fields"):
        decode_desktop_v2_block7_request(json.dumps(reaction))

    reaction = _reaction()
    requested = reaction["requested_conditions"]
    assert isinstance(requested, dict)
    requested["che_shift_ev"] = -0.2
    with pytest.raises(DesktopIPCError, match="unknown fields"):
        decode_desktop_v2_block7_request(json.dumps(reaction))

    view = {
        **_base("reaction_diagram_view"),
        "analysis_id": str(uuid4()),
        "dataset_hash": "a" * 64,
    }
    with pytest.raises(DesktopIPCError, match="unknown fields"):
        decode_desktop_v2_block7_request(json.dumps(view))


def test_block7_rejects_invalid_ids_enums_and_numeric_contracts() -> None:
    bad_uuid = _harmonic()
    bad_uuid["calculation_id"] = "latest-frequency"
    with pytest.raises(DesktopIPCError, match="must be a UUID"):
        decode_desktop_v2_block7_request(json.dumps(bad_uuid))

    bad_subject = _harmonic()
    bad_subject["subject_kind"] = "gas"
    with pytest.raises(DesktopIPCError, match="unsupported value"):
        decode_desktop_v2_block7_request(json.dumps(bad_subject))

    bad_temperature = _harmonic()
    bad_temperature["temperature_k"] = 0.0
    with pytest.raises(DesktopIPCError, match="finite positive number"):
        decode_desktop_v2_block7_request(json.dumps(bad_temperature))

    bad_standard_state = _gas()
    bad_standard_state["standard_state"] = "surface_fixed_cell"
    with pytest.raises(DesktopIPCError, match="unsupported value"):
        decode_desktop_v2_block7_request(json.dumps(bad_standard_state))

    bad_reaction = _reaction()
    bad_reaction["preset_kind"] = "custom_equation"
    with pytest.raises(DesktopIPCError, match="unsupported value"):
        decode_desktop_v2_block7_request(json.dumps(bad_reaction))

    bad_reaction = _reaction()
    requested = bad_reaction["requested_conditions"]
    assert isinstance(requested, dict)
    requested["temperature_k"] = 310.0
    with pytest.raises(DesktopIPCError, match="same temperature_k"):
        decode_desktop_v2_block7_request(json.dumps(bad_reaction))


def test_block7_requires_unique_nested_scientific_identity_keys() -> None:
    harmonic = _harmonic()
    exclusions = harmonic["exclusions"]
    assert isinstance(exclusions, list)
    exclusions.append({"mode_index": 1, "reason": "constrained"})
    with pytest.raises(DesktopIPCError, match="must be unique"):
        decode_desktop_v2_block7_request(json.dumps(harmonic))

    gas = _gas()
    masses = gas["atomic_masses"]
    assert isinstance(masses, list)
    first = masses[0]
    assert isinstance(first, dict)
    duplicate_uid = first["atom_uid"]
    second = masses[1]
    assert isinstance(second, dict)
    second["atom_uid"] = duplicate_uid
    with pytest.raises(DesktopIPCError, match="must be unique"):
        decode_desktop_v2_block7_request(json.dumps(gas))

    reaction = _reaction()
    bindings = reaction["bindings"]
    assert isinstance(bindings, dict)
    bindings["h_adsorbed_analysis_id"] = bindings["clean_surface_analysis_id"]
    with pytest.raises(DesktopIPCError, match="must be distinct"):
        decode_desktop_v2_block7_request(json.dumps(reaction))
