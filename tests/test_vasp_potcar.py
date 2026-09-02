from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from ecatvasp import domain, vasp
from ecatvasp.domain.ids import new_project_id


def _snapshot() -> domain.StructureSnapshot:
    return domain.StructureSnapshot(
        lattice=domain.Lattice(
            vectors=((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 20.0))
        ),
        sites=(
            domain.StructureSite(domain.new_atom_uid(), "C", (0.0, 0.0, 0.25)),
            domain.StructureSite(domain.new_atom_uid(), "Pb", (0.25, 0.25, 0.55)),
            domain.StructureSite(domain.new_atom_uid(), "C", (0.5, 0.5, 0.25)),
            domain.StructureSite(domain.new_atom_uid(), "O", (0.25, 0.25, 0.65)),
        ),
        periodic=(True, True, False),
    )


def _potcar_text(symbol: str, *, zval: float, enmax_ev: float) -> str:
    return (
        f"PAW_PBE {symbol} 01Jan2026\n"
        "parameters from PSCTR are:\n"
        f"   TITEL  = PAW_PBE {symbol} 01Jan2026\n"
        f"   POMASS = 1.000; ZVAL = {zval:.3f} mass and valenz\n"
        f"   ENMAX = {enmax_ev:.3f}; ENMIN = {enmax_ev * 0.75:.3f} eV\n"
        " End of Dataset\n"
    )


def _library_and_method(
    tmp_path: Path,
    *,
    bad_titel_for: str | None = None,
) -> tuple[vasp.LocalPotcarLibrary, domain.MethodDefinition]:
    root = tmp_path / "PBE_54"
    definitions = (
        ("C", "C", 4.0, 400.0),
        ("Pb", "Pb_d", 14.0, 237.5),
        ("O", "O", 6.0, 400.0),
    )
    identities: list[domain.PotcarIdentity] = []
    for element, symbol, zval, enmax_ev in definitions:
        path = root / symbol / "POTCAR"
        path.parent.mkdir(parents=True, exist_ok=True)
        titel_symbol = "Pb" if element == bad_titel_for else symbol
        text = _potcar_text(titel_symbol, zval=zval, enmax_ev=enmax_ev)
        path.write_text(text, encoding="utf-8")
        identities.append(
            domain.PotcarIdentity(
                element=element,
                symbol=symbol,
                sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            )
        )
    return (
        vasp.LocalPotcarLibrary(family="PBE_54", root=root),
        domain.MethodDefinition(
            xc_functional="PBE",
            potcar_family="PBE_54",
            potcars=tuple(identities),
        ),
    )


def _resolved(
    tmp_path: Path,
) -> tuple[vasp.ResolvedPotcarSet, domain.MethodDefinition]:
    library, method = _library_and_method(tmp_path)
    prepared = vasp.prepare_poscar(_snapshot())
    return library.resolve(prepared_poscar=prepared, method=method), method


def _evidence(
    spec: vasp.PotcarSpec,
    *,
    selected_encut_ev: float = 450.0,
    analysis_hash: str = "d" * 64,
) -> vasp.EncCutValidationEvidence:
    return vasp.EncCutValidationEvidence(
        core_method_hash=spec.core_method_hash,
        potcar_spec_hash=spec.metadata_hash,
        tested_encuts_ev=(400.0, 450.0, 500.0),
        selected_encut_ev=selected_encut_ev,
        analysis_hash=analysis_hash,
    )


def _lock(
    spec: vasp.PotcarSpec,
    evidence: vasp.EncCutValidationEvidence,
) -> vasp.ProjectNumericalLock:
    return vasp.ProjectNumericalLock(
        project_id=new_project_id(),
        system_kind=vasp.VaspSystemKind.SLAB_2D,
        core_method_hash=spec.core_method_hash,
        encut_ev=evidence.selected_encut_ev,
        encut_validation_hash=evidence.analysis_hash,
        kpoints=domain.KPointPolicy(
            domain.KPointPolicyKind.EXPLICIT_MESH,
            mesh=(3, 3, 1),
        ),
        kpoints_validation_hash="e" * 64,
    )


def test_local_resolver_follows_prepared_poscar_species_order(tmp_path: Path) -> None:
    resolved, method = _resolved(tmp_path)

    assert tuple(identity.element for identity in method.potcars) == ("C", "O", "Pb")
    assert resolved.spec.species_order == ("C", "Pb", "O")
    assert tuple(entry.symbol for entry in resolved.spec.entries) == ("C", "Pb_d", "O")
    assert resolved.spec.text == "C\nPb_d\nO\n"
    assert "TITEL" not in resolved.spec.text
    assert resolved.spec.max_enmax_ev == 400.0
    assert len(resolved.spec.metadata_hash) == 64
    assert tuple(path.parent.name for path in resolved.ordered_paths) == ("C", "Pb_d", "O")


def test_resolved_spec_records_titel_zval_enmax_and_exact_hash(tmp_path: Path) -> None:
    resolved, _ = _resolved(tmp_path)
    lead = resolved.spec.entries[1]

    assert lead.element == "Pb"
    assert lead.symbol == "Pb_d"
    assert lead.family == "PBE_54"
    assert lead.titel == "PAW_PBE Pb_d 01Jan2026"
    assert lead.zval == 14.0
    assert lead.enmax_ev == 237.5
    assert len(lead.sha256) == 64


def test_resolver_rejects_hash_mismatch(tmp_path: Path) -> None:
    library, method = _library_and_method(tmp_path)
    path = library.root / "Pb_d" / "POTCAR"
    path.write_text(path.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")

    with pytest.raises(vasp.PotcarPreparationError, match="hash mismatch"):
        library.resolve(prepared_poscar=vasp.prepare_poscar(_snapshot()), method=method)


def test_resolver_rejects_titel_symbol_mismatch(tmp_path: Path) -> None:
    library, method = _library_and_method(tmp_path, bad_titel_for="Pb")

    with pytest.raises(vasp.PotcarPreparationError, match="TITEL"):
        library.resolve(prepared_poscar=vasp.prepare_poscar(_snapshot()), method=method)


def test_resolver_rejects_missing_potcar_and_family_mismatch(tmp_path: Path) -> None:
    library, method = _library_and_method(tmp_path)
    (library.root / "O" / "POTCAR").unlink()
    prepared = vasp.prepare_poscar(_snapshot())

    with pytest.raises(vasp.PotcarPreparationError, match="missing"):
        library.resolve(prepared_poscar=prepared, method=method)

    wrong_family = vasp.LocalPotcarLibrary(family="PBE_64", root=library.root)
    with pytest.raises(vasp.PotcarPreparationError, match="family"):
        wrong_family.resolve(prepared_poscar=prepared, method=method)


def test_resolver_requires_method_identities_to_cover_poscar_exactly(tmp_path: Path) -> None:
    library, method = _library_and_method(tmp_path)
    incomplete = replace(
        method,
        potcars=tuple(identity for identity in method.potcars if identity.element != "O"),
    )

    with pytest.raises(vasp.PotcarPreparationError, match="exactly cover"):
        library.resolve(
            prepared_poscar=vasp.prepare_poscar(_snapshot()),
            method=incomplete,
        )


def test_encut_baseline_is_max_enmax_not_a_production_default(tmp_path: Path) -> None:
    resolved, _ = _resolved(tmp_path)

    baseline = vasp.suggest_encut_baseline(resolved.spec)

    assert baseline.max_enmax_ev == 400.0
    assert baseline.suggested_encut_ev == 400.0
    assert baseline.potcar_spec_hash == resolved.spec.metadata_hash


def test_encut_evidence_must_bind_exact_method_and_potcar_spec(tmp_path: Path) -> None:
    resolved, _ = _resolved(tmp_path)
    evidence = _evidence(resolved.spec)

    vasp.validate_encut_evidence(spec=resolved.spec, evidence=evidence)

    with pytest.raises(vasp.PotcarPreparationError, match="core method"):
        vasp.validate_encut_evidence(
            spec=resolved.spec,
            evidence=replace(evidence, core_method_hash="a" * 64),
        )
    with pytest.raises(vasp.PotcarPreparationError, match="POTCAR spec hash"):
        vasp.validate_encut_evidence(
            spec=resolved.spec,
            evidence=replace(evidence, potcar_spec_hash="b" * 64),
        )


def test_encut_evidence_below_enmax_baseline_fails_closed(tmp_path: Path) -> None:
    resolved, _ = _resolved(tmp_path)
    evidence = vasp.EncCutValidationEvidence(
        core_method_hash=resolved.spec.core_method_hash,
        potcar_spec_hash=resolved.spec.metadata_hash,
        tested_encuts_ev=(350.0, 400.0, 450.0),
        selected_encut_ev=350.0,
        analysis_hash="d" * 64,
    )

    with pytest.raises(vasp.PotcarPreparationError, match="below"):
        vasp.validate_encut_evidence(spec=resolved.spec, evidence=evidence)


def test_project_lock_encut_must_match_convergence_evidence(tmp_path: Path) -> None:
    resolved, _ = _resolved(tmp_path)
    evidence = _evidence(resolved.spec)
    lock = _lock(resolved.spec, evidence)

    vasp.validate_project_lock_encut(
        lock=lock,
        spec=resolved.spec,
        evidence=evidence,
    )

    with pytest.raises(vasp.PotcarPreparationError, match="validated ENCUT"):
        vasp.validate_project_lock_encut(
            lock=replace(lock, encut_ev=500.0),
            spec=resolved.spec,
            evidence=evidence,
        )
    with pytest.raises(vasp.PotcarPreparationError, match="validation hash"):
        vasp.validate_project_lock_encut(
            lock=replace(lock, encut_validation_hash="f" * 64),
            spec=resolved.spec,
            evidence=evidence,
        )


def test_encut_evidence_selected_value_must_have_been_tested(tmp_path: Path) -> None:
    resolved, _ = _resolved(tmp_path)

    with pytest.raises(ValueError, match="one of the tested"):
        vasp.EncCutValidationEvidence(
            core_method_hash=resolved.spec.core_method_hash,
            potcar_spec_hash=resolved.spec.metadata_hash,
            tested_encuts_ev=(400.0, 450.0),
            selected_encut_ev=425.0,
            analysis_hash="d" * 64,
        )
