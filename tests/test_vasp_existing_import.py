from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from ecatvasp.domain import (
    AnalysisProducerRef,
    AnalysisType,
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
    new_structure_snapshot_id,
)
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.vasp import (
    VASP_CONVERGENCE_ARTIFACT_FORMAT,
    VASP_RESULT_DOCUMENT_FORMAT,
    ConvergenceVerdict,
    VaspImportError,
    import_existing_vasp_folder,
    inspect_vasp_folder,
)

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


def test_converged_fixture_imports_through_normalized_v05_pipeline(tmp_path: Path) -> None:
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
    assert imported.normalized_result.energies.free_energy_toten_ev == pytest.approx(
        -123.456789
    )
    assert imported.convergence_assessment.overall is ConvergenceVerdict.CONVERGED

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
    assert {analysis.analysis_type for analysis in imported.analyses} == {
        AnalysisType.RESULT_PARSE,
        AnalysisType.CONVERGENCE,
    }
    parsed_artifact = next(
        item for item in imported.artifacts if item.artifact_type is ArtifactType.PARSED_RESULT
    )
    assert isinstance(parsed_artifact.producer, AnalysisProducerRef)
    parse_analysis = next(
        item for item in imported.analyses if item.analysis_type is AnalysisType.RESULT_PARSE
    )
    assert parsed_artifact.producer.id == parse_analysis.id


def test_converged_import_does_not_replace_existing_variant_current_snapshot(
    tmp_path: Path,
) -> None:
    project, _, variant = _context()
    existing_snapshot_id = new_structure_snapshot_id()
    pinned_variant = replace(
        variant,
        current_structure_snapshot_id=existing_snapshot_id,
    )

    imported = import_existing_vasp_folder(
        folder=FIXTURES / "relax_converged",
        project_root=tmp_path,
        project=project,
        variant=pinned_variant,
        method_fingerprint=_fingerprint(),
    )

    assert imported.convergence_assessment.overall is ConvergenceVerdict.CONVERGED
    assert imported.updated_variant.current_structure_snapshot_id == existing_snapshot_id
    assert imported.final_snapshot.id != existing_snapshot_id
    assert any(
        "already points to another current snapshot" in warning
        for warning in imported.inspection.warnings
    )


def test_unconverged_fixture_keeps_contcar_as_unpromoted_candidate(tmp_path: Path) -> None:
    project, _, variant = _context()
    imported = import_existing_vasp_folder(
        folder=FIXTURES / "relax_unconverged",
        project_root=tmp_path,
        project=project,
        variant=variant,
        method_fingerprint=_fingerprint(),
    )

    assert imported.calculation.status is CalculationScientificStatus.COMPLETED_UNCONVERGED
    assert imported.convergence_assessment.overall is ConvergenceVerdict.UNCONVERGED
    assert imported.parsed_result.electronic_converged is False
    assert imported.parsed_result.ionic_converged is None
    assert imported.parsed_result.total_energy_ev == pytest.approx(-120.25)
    assert imported.parsed_result.electronic_steps == 60
    assert imported.final_snapshot.id != imported.input_snapshot.id
    assert imported.updated_variant.current_structure_snapshot_id == imported.input_snapshot.id


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


def test_existing_vasp_v05_chain_survives_project_reopen(tmp_path: Path) -> None:
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
        analyses=imported.analyses,
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
    assert {item.analysis_type for item in reopened.analyses} == {
        AnalysisType.RESULT_PARSE,
        AnalysisType.CONVERGENCE,
    }

    parsed_artifact = next(
        artifact
        for artifact in reopened.artifacts
        if artifact.artifact_type is ArtifactType.PARSED_RESULT
    )
    assert parsed_artifact.local_path is not None
    parsed_payload = json.loads(
        (project_root / parsed_artifact.local_path).read_text(encoding="utf-8")
    )
    assert parsed_payload["format"] == VASP_RESULT_DOCUMENT_FORMAT
    assert parsed_payload["result"]["energies"]["free_energy_toten_ev"] == pytest.approx(
        -123.456789
    )
    convergence_artifact = next(
        artifact
        for artifact in reopened.artifacts
        if artifact.artifact_type is ArtifactType.DERIVED_DATASET
        and artifact.local_path is not None
        and artifact.local_path.endswith("convergence.json")
    )
    convergence_payload = json.loads(
        (project_root / (convergence_artifact.local_path or "")).read_text(
            encoding="utf-8"
        )
    )
    assert convergence_payload["format"] == VASP_CONVERGENCE_ARTIFACT_FORMAT
    assert convergence_payload["assessment"]["overall"] == "converged"
