from dataclasses import replace

from ecatvasp.domain.method import (
    ExecutionSettings,
    FingerprintCompatibility,
    KPointPolicy,
    KPointPolicyKind,
    MethodDefinition,
    MethodFingerprint,
    PotcarIdentity,
    ProtocolDefinition,
    RecipeIdentity,
    ScientificInputDigest,
    compare_fingerprints,
)

PB_HASH = "1" * 64
C_HASH = "2" * 64
STRUCTURE_HASH = "3" * 64


def fingerprint(*, ivdw: str = "IVDW=12", encut_ev: float = 450.0) -> MethodFingerprint:
    method = MethodDefinition(
        xc_functional="PBE",
        potcar_family="PBE_54",
        potcars=(
            PotcarIdentity("Pb", "Pb_d", PB_HASH),
            PotcarIdentity("C", "C", C_HASH),
        ),
        engine_version="6.5.1",
        dispersion_model=ivdw,
    )
    protocol = ProtocolDefinition(
        encut_ev=encut_ev,
        kpoints=KPointPolicy(KPointPolicyKind.EXPLICIT_MESH, mesh=(3, 3, 1)),
        ediffg_ev_per_angstrom=-0.02,
    )
    return MethodFingerprint(
        method=method,
        protocol=protocol,
        recipe=RecipeIdentity("WXC.VASP.AdsorbateRelax"),
        input_digests=(ScientificInputDigest("structure", STRUCTURE_HASH),),
    )


def test_ncore_change_is_execution_only() -> None:
    before = fingerprint()
    attempt_a = ExecutionSettings(ncore=4, cores=48, walltime_seconds=86_400)
    attempt_b = replace(attempt_a, ncore=8)

    assert attempt_a.execution_hash != attempt_b.execution_hash
    after = fingerprint()
    assert before.core_method_hash == after.core_method_hash
    assert before.protocol_hash == after.protocol_hash
    assert before.instance_hash == after.instance_hash


def test_ivdw_change_creates_new_core_method() -> None:
    d3_bj = fingerprint(ivdw="IVDW=12")
    d3_zero = fingerprint(ivdw="IVDW=11")

    assert d3_bj.core_method_hash != d3_zero.core_method_hash
    assert d3_bj.protocol_hash == d3_zero.protocol_hash
    assert d3_bj.instance_hash != d3_zero.instance_hash
    assert compare_fingerprints(d3_bj, d3_zero) is FingerprintCompatibility.INCOMPATIBLE


def test_encut_change_creates_protocol_revision_not_new_method() -> None:
    encut_450 = fingerprint(encut_ev=450.0)
    encut_500 = fingerprint(encut_ev=500.0)

    assert encut_450.core_method_hash == encut_500.core_method_hash
    assert encut_450.protocol_hash != encut_500.protocol_hash
    assert encut_450.instance_hash != encut_500.instance_hash
    assert (
        compare_fingerprints(encut_450, encut_500)
        is FingerprintCompatibility.CORE_METHOD_COMPATIBLE
    )
