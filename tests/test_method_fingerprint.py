from dataclasses import replace

import pytest

from ecatvasp.domain.method import (
    DipolePolicy,
    FingerprintCompatibility,
    KPointPolicy,
    KPointPolicyKind,
    MethodDefinition,
    MethodFingerprint,
    ParameterEntry,
    PotcarIdentity,
    ProtocolDefinition,
    RecipeIdentity,
    ScientificInputDigest,
    SpinTreatment,
    canonical_json,
    canonical_sha256,
    compare_fingerprints,
)

PB_HASH = "a" * 64
C_HASH = "b" * 64
STRUCTURE_HASH = "c" * 64


def make_method(*, dispersion: str = "IVDW=12") -> MethodDefinition:
    return MethodDefinition(
        xc_functional="PBE",
        potcar_family="PBE_54",
        potcars=(
            PotcarIdentity(element="Pb", symbol="Pb_d", sha256=PB_HASH),
            PotcarIdentity(element="C", symbol="C", sha256=C_HASH),
        ),
        engine_version="6.5.1",
        dispersion_model=dispersion,
    )


def make_protocol(*, encut_ev: float = 450.0) -> ProtocolDefinition:
    return ProtocolDefinition(
        encut_ev=encut_ev,
        kpoints=KPointPolicy(KPointPolicyKind.EXPLICIT_MESH, mesh=(3, 3, 1)),
        ediffg_ev_per_angstrom=-0.02,
        dipole_policy=DipolePolicy.AUTO,
    )


def make_fingerprint(
    *,
    method: MethodDefinition | None = None,
    protocol: ProtocolDefinition | None = None,
    recipe_id: str = "WXC.VASP.AdsorbateRelax",
    structure_hash: str = STRUCTURE_HASH,
) -> MethodFingerprint:
    return MethodFingerprint(
        method=method or make_method(),
        protocol=protocol or make_protocol(),
        recipe=RecipeIdentity(recipe_id),
        input_digests=(ScientificInputDigest("structure", structure_hash),),
    )


def test_canonical_hash_ignores_semantically_irrelevant_tuple_order() -> None:
    original = make_method()
    reordered = MethodDefinition(
        xc_functional=original.xc_functional,
        potcar_family=original.potcar_family,
        potcars=tuple(reversed(original.potcars)),
        engine_version=original.engine_version,
        dispersion_model=original.dispersion_model,
    )

    assert canonical_json(original) == canonical_json(reordered)
    assert canonical_sha256(original) == canonical_sha256(reordered)


def test_recipe_parameter_order_is_canonical() -> None:
    first = RecipeIdentity(
        "WXC.VASP.BaderStatic",
        parameters=(ParameterEntry("LAECHG", True), ParameterEntry("LCHARG", True)),
    )
    second = RecipeIdentity(
        "WXC.VASP.BaderStatic",
        parameters=(ParameterEntry("LCHARG", True), ParameterEntry("LAECHG", True)),
    )

    assert first.recipe_hash == second.recipe_hash


def test_fingerprint_id_is_not_part_of_scientific_hashes() -> None:
    first = make_fingerprint()
    second = make_fingerprint()

    assert first.id != second.id
    assert first.core_method_hash == second.core_method_hash
    assert first.protocol_hash == second.protocol_hash
    assert first.instance_hash == second.instance_hash
    assert compare_fingerprints(first, second) is FingerprintCompatibility.IDENTICAL_INSTANCE


def test_recipe_or_input_change_only_changes_instance_identity() -> None:
    baseline = make_fingerprint()
    recipe_changed = make_fingerprint(recipe_id="WXC.VASP.BaderStatic")
    input_changed = make_fingerprint(structure_hash="d" * 64)

    for changed in (recipe_changed, input_changed):
        assert changed.core_method_hash == baseline.core_method_hash
        assert changed.protocol_hash == baseline.protocol_hash
        assert changed.instance_hash != baseline.instance_hash
        assert compare_fingerprints(baseline, changed) is FingerprintCompatibility.SAME_PROTOCOL


def test_soc_requires_noncollinear_method_identity() -> None:
    with pytest.raises(ValueError, match="SOC requires NONCOLLINEAR"):
        replace(make_method(), soc=True)

    soc_method = replace(
        make_method(),
        soc=True,
        spin_treatment=SpinTreatment.NONCOLLINEAR,
    )
    assert soc_method.soc is True


def test_invalid_hash_and_nonfinite_parameter_fail_closed() -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        PotcarIdentity(element="Pb", symbol="Pb_d", sha256="not-a-hash")
    with pytest.raises(ValueError, match="finite"):
        ParameterEntry("CUSTOM", float("nan"))
