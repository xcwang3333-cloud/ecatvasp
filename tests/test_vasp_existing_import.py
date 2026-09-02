from __future__ import annotations

import json
from pathlib import Path

import pytest

from ecatvasp.domain import (
    ArtifactType,
    CalculationScientificStatus,
    Catalyst,
    KPointPolicy,
    KPointPolicyKind,
    MethodDefinition,
    MethodFingerprint,
    ParameterEntry,
    PotcarIdentity,
    Project,
    ProtocolDefinition,
    RecipeIdentity,
    SpinTreatment,
    StructureVariant,
    VariantType,
)
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.vasp import VaspImportError, import_existing_vasp_folder, inspect_vasp_folder

FIXTURES = Path(__file__).parent / "fixtures" / "vasp"


def _fingerprint(*, encut_ev: float = 450.0) -> MethodFingerprint:
    return MethodFingerprint(
        method=MethodDefinition(
            xc_functional="PBE",
            potcar_family="PBE_54",
            potcars=(
                PotcarIdentity(element="C", symbol="C", sha256="a" * 64),
                PotcarIdentity(element="Pb", symbol="Pb_d", sha256="b" * 64),
            ),
            engine_version="6.4.3",
            dispersion_model="D3(BJ)",
            spin_treatment=SpinTreatment.COLLINEAR,
            extra_parameters=(ParameterEntry("IVDW", 12),),
        ),
        protocol=ProtocolDefinition(
            encut_ev=encut_ev,
            kpoints=KPointPolicy(
                kind=KPointPolicyKind.EXPLICIT_MESH,
                mesh=(3, 3, 1),
            ),
            precision="Accurate",
            ediff_ev=1e-5,
            ediffg_ev_per_angstrom=-0.02,
            ismear=0,
            sigma_ev=0.05,
        ),
        recipe=RecipeIdentity(
            recipe_id="ECatVASP.VASP.AdsorbateRelax",
            parameters=(
                ParameterEntry("IBRION", 2),
                ParameterEntry("NSW", 200),
                ParameterEntry("ISIF", 2),
            ),
        ),
    )


def _context() -> tuple[Project, Catalyst, StructureVariant]:
    project = Project(name="Existing VASP import", slug="existing-vasp-import")
    catalyst = Catalyst(
        project_id=project.id,
        name="Pb2-NC",
        slug="pb2-nc",
    )
    variant = StructureVariant(
        catalyst_id=catalyst.id,
        name="opposite-side",
        variant_type=VariantType.SITE_TOPOLOGY,
        topology_tags=("opposite-side",),
    )
    return project, catalyst, variant


def test_inspection_requires_minimum_vasp_files(tmp_path: Path) -> None:
    with pytest.raises(VaspImportError, match="POSCAR"):
        inspect_vasp_folder(tmp_path)


def test_converged_fixture_imports_with_stable_atom_identity(tmp_path: Path) -> None:
    project, _, variant = _context()
    imported = import_existing_vasp_folder(
        folder=FIXTURES / "relax_converged",
        project_root=tmp_path,
        project=project,
        variant=variant,
        method_fingerprint=_fingerprint(),
    )

    assert imported.parsed_result.scientific_status is CalculationScientificStatus.CONVERGED
    assert imported.parsed_result.total_energy_ev == pytest.approx(-123.456789)
    assert imported.parsed_result.fermi_energy_ev == pytest.approx(2.3456)
    assert imported.parsed_result.max_force_ev_per_angstrom == pytest.approx(0.01)
    assert imported.parsed_result.ionic_steps == 2
    assert imported.parsed_result.electronic_steps == 3
    assert imported.parsed_result.vasp_version == "6.4.3"

    input_uids = tuple(site.atom_uid for site in imported.input_snapshot.sites)
    final_uids = tuple(site.atom_uid for site in imported.final_snapshot.sites)
    assert input_uids == final_uids
    assert imported.final_snapshot.parent_snapshot_id == imported.input_snapshot.id
    assert imported.updated_variant.current_structure_snapshot_id == imported.final_snapshot.id

    artifact_types = {artifact.artifact_type for artifact in imported.artifacts}
    assert ArtifactType.POSCAR in artifact_types
    assert ArtifactType.CONTCAR in artifact_types
    assert ArtifactType.OUTCAR in artifact_types
    assert ArtifactType.PARSED_RESULT in artifact_types
    assert len(imported.dependency_records) == 3


def test_unconverged_fixture_is_parsed_not_mislabeled_converged(tmp_path: Path) -> None:
    project, _, variant = _context()
    imported = import_existing_vasp_folder(
        folder=FIXTURES / "relax_unconverged",
        project_root=tmp_path,
        project=project,
        variant=variant,
        method_fingerprint=_fingerprint(),
    )

    assert imported.calculation.status is CalculationScientificStatus.COMPLETED_UNCONVERGED
    assert imported.parsed_result.electronic_converged is False
    assert imported.parsed_result.ionic_converged is False
    assert imported.parsed_result.total_energy_ev == pytest.approx(-120.25)
    assert imported.parsed_result.electronic_steps == 60


def test_import_rejects_method_fingerprint_incar_contradiction(tmp_path: Path) -> None:
    project, _, variant = _context()
    with pytest.raises(VaspImportError, match="ENCUT"):
        import_existing_vasp_folder(
            folder=FIXTURES / "relax_converged",
            project_root=tmp_path,
            project=project,
            variant=variant,
            method_fingerprint=_fingerprint(encut_ev=500.0),
        )


def test_existing_vasp_vertical_slice_survives_project_reopen(tmp_path: Path) -> None:
    project, catalyst, variant = _context()
    fingerprint = _fingerprint()
    project_root = tmp_path / "project"

    imported = import_existing_vasp_folder(
        folder=FIXTURES / "relax_converged",
        project_root=project_root,
        project=project,
        variant=variant,
        method_fingerprint=fingerprint,
    )
    bundle = ProjectBundle(
        project=project,
        catalysts=(catalyst,),
        structure_variants=(imported.updated_variant,),
        structure_snapshots=(imported.input_snapshot, imported.final_snapshot),
        method_fingerprints=(fingerprint,),
        calculations=(imported.calculation,),
        execution_attempts=(imported.execution_attempt,),
        artifacts=imported.artifacts,
        provenance_records=imported.provenance_records,
        dependency_records=imported.dependency_records,
    )

    ProjectStore(project_root).save(bundle)
    reopened = ProjectStore(project_root).open()

    assert reopened == bundle
    assert reopened.calculations[0].status is CalculationScientificStatus.CONVERGED
    assert (
        reopened.structure_variants[0].current_structure_snapshot_id
        == imported.final_snapshot.id
    )
    assert tuple(site.atom_uid for site in reopened.structure_snapshots[0].sites) == tuple(
        site.atom_uid for site in reopened.structure_snapshots[1].sites
    )
    assert reopened.dependency_records == imported.dependency_records

    parsed_artifact = next(
        artifact
        for artifact in reopened.artifacts
        if artifact.artifact_type is ArtifactType.PARSED_RESULT
    )
    assert parsed_artifact.local_path is not None
    summary_path = project_root / parsed_artifact.local_path
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    assert payload["scientific_status"] == "converged"
    assert payload["total_energy_ev"] == pytest.approx(-123.456789)
