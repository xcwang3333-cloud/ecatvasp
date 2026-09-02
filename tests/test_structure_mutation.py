from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ecatvasp import domain, structures


def _sites_by_uid(snapshot: domain.StructureSnapshot) -> dict[domain.AtomUid, domain.StructureSite]:
    return {site.atom_uid: site for site in snapshot.sites}


def test_vacancy_creates_immutable_child_and_preserves_remaining_atom_identity() -> None:
    source = structures.build_graphene(structures.GrapheneBuildSpec(nx=2, ny=2))
    original_sites = source.sites
    victim = source.sites[2]

    result = structures.remove_vacancies(source, (victim.atom_uid,), label="single-vacancy")
    child = result.snapshot

    assert source.sites == original_sites
    assert source.origin is domain.StructureOrigin.BUILT
    assert source.parent_snapshot_id is None
    assert child.origin is domain.StructureOrigin.EDITED
    assert child.parent_snapshot_id == source.id
    assert child.label == "single-vacancy"
    assert child.lattice == source.lattice
    assert child.periodic == source.periodic
    assert len(child.sites) == len(source.sites) - 1
    assert not child.contains_atom(victim.atom_uid)

    expected_uids = {site.atom_uid for site in source.sites} - {victim.atom_uid}
    assert {site.atom_uid for site in child.sites} == expected_uids
    assert result.removed_atom_uids == (victim.atom_uid,)
    assert len(result.lineage) == len(source.sites)

    removed = next(
        event for event in result.lineage if event.action is structures.AtomLineageAction.REMOVED
    )
    assert removed.source_atom_uid == victim.atom_uid
    assert removed.target_atom_uid is None
    assert all(
        event.target_atom_uid == event.source_atom_uid
        for event in result.lineage
        if event.action is structures.AtomLineageAction.PRESERVED
    )


@pytest.mark.parametrize("dopant", ["N", "S", "P"])
def test_substitution_terminates_carbon_identity_and_creates_fresh_dopant_uid(
    dopant: str,
) -> None:
    source = structures.build_graphene(structures.GrapheneBuildSpec(nx=2, ny=2))
    carbon = source.sites[3]

    result = structures.substitute_dopants(
        source,
        (structures.DopantSubstitution(atom_uid=carbon.atom_uid, dopant=dopant),),
    )
    child = result.snapshot

    assert source.sites[3].element == "C"
    assert source.sites[3].atom_uid == carbon.atom_uid
    assert not child.contains_atom(carbon.atom_uid)

    replacement = next(
        event for event in result.lineage if event.action is structures.AtomLineageAction.REPLACED
    )
    assert replacement.source_atom_uid == carbon.atom_uid
    assert replacement.source_element == "C"
    assert replacement.target_atom_uid is not None
    assert replacement.target_atom_uid != carbon.atom_uid
    assert replacement.target_element == dopant
    assert result.replacement_pairs == ((carbon.atom_uid, replacement.target_atom_uid),)

    dopant_site = _sites_by_uid(child)[replacement.target_atom_uid]
    assert dopant_site.element == dopant
    assert dopant_site.fractional_coords == carbon.fractional_coords
    assert child.parent_snapshot_id == source.id
    assert child.origin is domain.StructureOrigin.EDITED


def test_combined_mutation_supports_multiple_vacancies_and_substitutions() -> None:
    source = structures.build_graphene(structures.GrapheneBuildSpec(nx=3, ny=2))
    vacancy_uids = (source.sites[1].atom_uid, source.sites[7].atom_uid)
    substitutions = (
        structures.DopantSubstitution(source.sites[3].atom_uid, "n"),
        structures.DopantSubstitution(source.sites[9].atom_uid, "p"),
    )

    result = structures.mutate_structure(
        source,
        vacancy_atom_uids=vacancy_uids,
        substitutions=substitutions,
        label="C8NP",
    )
    child = result.snapshot

    assert len(child.sites) == len(source.sites) - 2
    assert result.removed_atom_uids == vacancy_uids
    assert len(result.replacement_pairs) == 2
    assert sum(site.element == "N" for site in child.sites) == 1
    assert sum(site.element == "P" for site in child.sites) == 1
    assert sum(site.element == "C" for site in child.sites) == len(source.sites) - 4
    assert len(result.lineage) == len(source.sites)
    assert {event.action for event in result.lineage} == {
        structures.AtomLineageAction.PRESERVED,
        structures.AtomLineageAction.REMOVED,
        structures.AtomLineageAction.REPLACED,
    }


def test_mutation_result_rejects_inconsistent_target_indices() -> None:
    source = structures.build_graphene(structures.GrapheneBuildSpec(nx=2, ny=2))
    result = structures.remove_vacancies(source, (source.sites[0].atom_uid,))
    corrupted = (
        result.lineage[0],
        replace(result.lineage[1], target_index=1),
        *result.lineage[2:],
    )

    with pytest.raises(ValueError, match="target indices"):
        structures.StructureMutationResult(
            source_snapshot_id=source.id,
            snapshot=result.snapshot,
            lineage=corrupted,
        )


def test_mutation_rejects_conflicting_missing_and_duplicate_targets() -> None:
    source = structures.build_graphene(structures.GrapheneBuildSpec(nx=2, ny=2))
    first_uid = source.sites[0].atom_uid
    missing_uid = domain.new_atom_uid()

    with pytest.raises(structures.StructureMutationError, match="at least one"):
        structures.mutate_structure(source)
    with pytest.raises(structures.StructureMutationError, match="vacancy atom_uids must be unique"):
        structures.remove_vacancies(source, (first_uid, first_uid))
    with pytest.raises(
        structures.StructureMutationError,
        match="substitution atom_uids must be unique",
    ):
        structures.substitute_dopants(
            source,
            (
                structures.DopantSubstitution(first_uid, "N"),
                structures.DopantSubstitution(first_uid, "P"),
            ),
        )
    with pytest.raises(structures.StructureMutationError, match="both removed and substituted"):
        structures.mutate_structure(
            source,
            vacancy_atom_uids=(first_uid,),
            substitutions=(structures.DopantSubstitution(first_uid, "N"),),
        )
    with pytest.raises(structures.StructureMutationError, match="must exist"):
        structures.remove_vacancies(source, (missing_uid,))
    with pytest.raises(structures.StructureMutationError, match="dopant must be one of"):
        structures.DopantSubstitution(first_uid, "Fe")


def test_mutation_rejects_noncarbon_substitution_and_empty_structure() -> None:
    source = structures.build_graphene(structures.GrapheneBuildSpec(nx=1, ny=1))
    carbon_uid = source.sites[0].atom_uid
    nitrogen_result = structures.substitute_dopants(
        source,
        (structures.DopantSubstitution(carbon_uid, "N"),),
    )
    nitrogen = next(site for site in nitrogen_result.snapshot.sites if site.element == "N")

    with pytest.raises(structures.StructureMutationError, match="target must be carbon"):
        structures.substitute_dopants(
            nitrogen_result.snapshot,
            (structures.DopantSubstitution(nitrogen.atom_uid, "P"),),
        )

    all_uids = tuple(site.atom_uid for site in source.sites)
    with pytest.raises(structures.StructureMutationError, match="remove every atom"):
        structures.remove_vacancies(source, all_uids)


def test_mutated_structure_poscar_round_trip_preserves_target_identity_and_geometry(
    tmp_path: Path,
) -> None:
    source = structures.build_graphene(
        structures.GrapheneBuildSpec(nx=3, ny=2, vacuum_gap_angstrom=20.0)
    )
    result = structures.mutate_structure(
        source,
        vacancy_atom_uids=(source.sites[1].atom_uid,),
        substitutions=(
            structures.DopantSubstitution(source.sites[4].atom_uid, "N"),
            structures.DopantSubstitution(source.sites[8].atom_uid, "S"),
        ),
        label="vacancy-NS-graphene",
    )
    target = tmp_path / "POSCAR"

    structures.export_structure(result.snapshot, target)
    restored = structures.import_structure(target)

    assert restored.metadata.identity_status is structures.AtomIdentityStatus.PRESERVED_SIDECAR
    assert restored.snapshot.periodic == result.snapshot.periodic
    assert set(_sites_by_uid(restored.snapshot)) == set(_sites_by_uid(result.snapshot))

    expected_by_uid = _sites_by_uid(result.snapshot)
    actual_by_uid = _sites_by_uid(restored.snapshot)
    for atom_uid, expected in expected_by_uid.items():
        actual = actual_by_uid[atom_uid]
        assert actual.element == expected.element
        assert actual.fractional_coords == pytest.approx(expected.fractional_coords)
    for actual, expected in zip(
        restored.snapshot.lattice.vectors,
        result.snapshot.lattice.vectors,
        strict=True,
    ):
        assert actual == pytest.approx(expected)
