from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from ecatvasp.analysis import (
    BandCenterEnergyReference,
    BandCenterError,
    BandCenterIntegrationRule,
    BandCenterKind,
    BandCenterNormalization,
    BandCenterParameters,
    BandCenterSelector,
    BandCenterSpinMode,
    ElectronicEnergyReference,
    ProjectionScope,
    load_band_center_artifact,
    load_canonical_dos_artifact,
    materialize_band_center_analysis,
    materialize_canonical_dos_analysis,
    parse_vasp_doscar,
)
from ecatvasp.domain import (
    AnalysisType,
    Artifact,
    ArtifactAvailability,
    ArtifactType,
    Calculation,
    CalculationProducerRef,
    CalculationScientificStatus,
    CalculationType,
    ExecutionAttempt,
    ExecutionAttemptProducerRef,
    ExecutionAttemptStatus,
    KPointPolicy,
    KPointPolicyKind,
    Lattice,
    MethodDefinition,
    MethodFingerprint,
    PotcarIdentity,
    Project,
    ProtocolDefinition,
    RecipeIdentity,
    RetrievalPolicy,
    SpinTreatment,
    StructureSite,
    StructureSnapshot,
    new_atom_uid,
)
from ecatvasp.schema.version import SCHEMA_VERSION
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.workflow import (
    ELECTRONIC_ANALYSIS_TYPES,
    ElectronicAnalysisRequirement,
    ElectronicAnalysisScientificState,
    WorkflowStepReadiness,
    reconcile_electronic_analyses_from_store,
)


@dataclass(frozen=True, slots=True)
class _AcceptanceCase:
    project: Project
    snapshot: StructureSnapshot
    fingerprint: MethodFingerprint
    calculation: Calculation
    attempt: ExecutionAttempt
    doscar_artifact: Artifact
    atom_map_artifact: Artifact


def _write(path: Path, body: bytes) -> tuple[int, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return len(body), hashlib.sha256(body).hexdigest()


def _case(tmp_path: Path) -> _AcceptanceCase:
    project = Project(name="v0.7 final acceptance", slug="v07-final-acceptance")
    atom_uid = new_atom_uid()
    snapshot = StructureSnapshot(
        lattice=Lattice(
            vectors=((8.0, 0.0, 0.0), (0.0, 8.0, 0.0), (0.0, 0.0, 16.0))
        ),
        sites=(StructureSite(atom_uid, "C", (0.5, 0.5, 0.5)),),
    )
    fingerprint = MethodFingerprint(
        method=MethodDefinition(
            xc_functional="PBE",
            potcar_family="PBE_54",
            potcars=(PotcarIdentity("C", "C", "c" * 64),),
            dispersion_model="NONE",
            spin_treatment=SpinTreatment.UNPOLARIZED,
        ),
        protocol=ProtocolDefinition(
            encut_ev=450.0,
            kpoints=KPointPolicy(KPointPolicyKind.GAMMA_ONLY),
        ),
        recipe=RecipeIdentity("ECatVASP.VASP.DOSPrerequisite"),
    )
    calculation = Calculation(
        project_id=project.id,
        calculation_type=CalculationType.DOS_STATIC,
        input_structure_snapshot_id=snapshot.id,
        recipe_id=fingerprint.recipe.recipe_id,
        method_fingerprint_id=fingerprint.id,
        status=CalculationScientificStatus.CONVERGED,
        slug="final-dos-source",
    )
    attempt = ExecutionAttempt(
        calculation_id=calculation.id,
        attempt_number=1,
        status=ExecutionAttemptStatus.PARSED,
    )

    atom_map_payload = {
        "format": "ecatvasp-v03-atom-index-map",
        "version": 1,
        "structure_snapshot_id": str(snapshot.id),
        "structure_sha256": "a" * 64,
        "poscar_sha256": "b" * 64,
        "species_order": ["C"],
        "species_counts": [1],
        "entries": [
            {
                "atom_uid": str(atom_uid),
                "element": "C",
                "snapshot_index": 0,
                "poscar_index": 0,
                "vasp_ordinal": 1,
                "selective_dynamics": None,
            }
        ],
    }
    atom_map_bytes = (
        json.dumps(atom_map_payload, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    atom_map_path = (
        tmp_path
        / "calculations"
        / str(calculation.id)
        / "input"
        / "atom-index-map.json"
    )
    atom_map_size, atom_map_hash = _write(atom_map_path, atom_map_bytes)
    atom_map_artifact = Artifact(
        artifact_type=ArtifactType.DERIVED_DATASET,
        producer=CalculationProducerRef(calculation.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=atom_map_path.relative_to(tmp_path).as_posix(),
        size_bytes=atom_map_size,
        sha256=atom_map_hash,
    )

    doscar_bytes = (
        b"1 1 1 0\n"
        b"header 2\n"
        b"header 3\n"
        b"header 4\n"
        b"header 5\n"
        b"1.0 -1.0 2 0.2\n"
        b"-1.0 1.0 0.0\n"
        b"1.0 2.0 1.0\n"
        b"1.0 -1.0 2 0.2\n"
        b"-1.0 1 2 3 4 5 6 7 8 9\n"
        b"1.0 2 3 4 5 6 7 8 9 10\n"
    )
    doscar_path = (
        tmp_path
        / "calculations"
        / str(calculation.id)
        / "attempt-1"
        / "DOSCAR"
    )
    doscar_size, doscar_hash = _write(doscar_path, doscar_bytes)
    doscar_artifact = Artifact(
        artifact_type=ArtifactType.DOSCAR,
        producer=ExecutionAttemptProducerRef(attempt.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=doscar_path.relative_to(tmp_path).as_posix(),
        size_bytes=doscar_size,
        sha256=doscar_hash,
    )
    return _AcceptanceCase(
        project=project,
        snapshot=snapshot,
        fingerprint=fingerprint,
        calculation=calculation,
        attempt=attempt,
        doscar_artifact=doscar_artifact,
        atom_map_artifact=atom_map_artifact,
    )


def test_v07_final_e2e_dos_descriptor_reconciliation_reopen_and_drift(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)
    doscar_bytes = (tmp_path / (case.doscar_artifact.local_path or "")).read_bytes()
    atom_map_bytes = (tmp_path / (case.atom_map_artifact.local_path or "")).read_bytes()
    intake = parse_vasp_doscar(
        doscar_bytes=doscar_bytes,
        atom_index_map_bytes=atom_map_bytes,
        structure_snapshot_id=case.snapshot.id,
        spin_treatment=SpinTreatment.UNPOLARIZED,
    )
    source = materialize_canonical_dos_analysis(
        project_root=tmp_path,
        calculation=case.calculation,
        execution_attempt=case.attempt,
        doscar_artifact=case.doscar_artifact,
        atom_index_map_artifact=case.atom_map_artifact,
        intake=intake,
    )
    canonical = load_canonical_dos_artifact(
        project_root=tmp_path,
        analysis=source.analysis,
        artifact=source.artifact,
    )
    assert canonical.energy_axis.reference is ElectronicEnergyReference.VASP_NATIVE
    assert canonical.energy_axis.energies_ev == (-1.0, 1.0)
    assert canonical.energy_axis.fermi_energy_ev == 0.2
    assert canonical.energy_axis.relative_to_fermi() == pytest.approx((-1.2, 0.8))
    projected_uids = {
        item.atom_uid
        for item in canonical.series
        if item.scope is ProjectionScope.ATOM
    }
    assert projected_uids == {case.snapshot.sites[0].atom_uid}

    descriptor = materialize_band_center_analysis(
        project_root=tmp_path,
        source_analysis=source.analysis,
        source_artifact=source.artifact,
        parameters=BandCenterParameters(
            kind=BandCenterKind.D_BAND,
            selector=BandCenterSelector(
                scope=ProjectionScope.ATOM,
                spin=BandCenterSpinMode.TOTAL,
                atom_uid=case.snapshot.sites[0].atom_uid,
                element="C",
            ),
            energy_reference=BandCenterEnergyReference.VASP_NATIVE,
            window_lower_ev=-1.0,
            window_upper_ev=1.0,
        ),
    )
    reopened_descriptor = load_band_center_artifact(
        project_root=tmp_path,
        source_analysis=source.analysis,
        source_artifact=source.artifact,
        analysis=descriptor.analysis,
        artifact=descriptor.artifact,
    )
    assert reopened_descriptor == descriptor.result
    assert descriptor.result.source_artifact_sha256 == source.artifact.sha256

    payload_path = tmp_path / (source.artifact.local_path or "")
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    receipt = payload["source_receipt"]
    assert receipt["doscar_sha256"] == case.doscar_artifact.sha256
    assert receipt["atom_index_map_sha256"] == case.atom_map_artifact.sha256
    assert receipt["parser_version"] == source.analysis.tool_version
    assert payload["source_receipt_hash"] == source.analysis.parameters_hash

    bundle = ProjectBundle(
        project=case.project,
        structure_snapshots=(case.snapshot,),
        method_fingerprints=(case.fingerprint,),
        calculations=(case.calculation,),
        execution_attempts=(case.attempt,),
        artifacts=(
            case.doscar_artifact,
            case.atom_map_artifact,
            source.artifact,
            descriptor.artifact,
        ),
        analyses=(source.analysis, descriptor.analysis),
        provenance_records=(
            *source.provenance_records,
            *descriptor.provenance_records,
        ),
        dependency_records=(
            *source.dependency_records,
            *descriptor.dependency_records,
        ),
    )
    bundle.validate()
    store = ProjectStore(tmp_path)
    store.save(bundle)
    assert store.open() == bundle

    requirements = (
        ElectronicAnalysisRequirement(
            key="canonical-dos",
            project_id=case.project.id,
            analysis_type=AnalysisType.DOS,
            input_artifact_ids=(
                case.doscar_artifact.id,
                case.atom_map_artifact.id,
            ),
            parameters_hash=source.analysis.parameters_hash,
        ),
        ElectronicAnalysisRequirement(
            key="d-band-center",
            project_id=case.project.id,
            analysis_type=AnalysisType.BAND_CENTER,
            input_artifact_ids=(source.artifact.id,),
            parameters_hash=descriptor.analysis.parameters_hash,
        ),
    )
    first = reconcile_electronic_analyses_from_store(
        store=store,
        requirements=requirements,
    )
    second = reconcile_electronic_analyses_from_store(
        store=store,
        requirements=requirements,
    )
    assert first == second
    assert first.report_hash == second.report_hash
    for key in ("canonical-dos", "d-band-center"):
        projection = first.requirement(key)
        assert projection.scientific_state is ElectronicAnalysisScientificState.COMPLETED
        assert projection.readiness is WorkflowStepReadiness.SATISFIED

    drifted = reconcile_electronic_analyses_from_store(
        store=store,
        requirements=requirements,
        current_hash_overrides={source.artifact.id: "0" * 64},
    )
    descriptor_projection = drifted.requirement("d-band-center")
    assert descriptor_projection.scientific_state is ElectronicAnalysisScientificState.STALE
    assert descriptor_projection.readiness is WorkflowStepReadiness.BLOCKED
    assert drifted.report_hash != first.report_hash


def test_v07_descriptor_runtime_enums_fail_closed() -> None:
    atom_uid = new_atom_uid()
    with pytest.raises(BandCenterError, match="spin mode"):
        BandCenterSelector(
            scope=ProjectionScope.ATOM,
            spin=cast(BandCenterSpinMode, "invented-spin"),
            atom_uid=atom_uid,
            element="C",
        )

    selector = BandCenterSelector(
        scope=ProjectionScope.ATOM,
        spin=BandCenterSpinMode.TOTAL,
        atom_uid=atom_uid,
        element="C",
    )
    with pytest.raises(BandCenterError, match="kind"):
        BandCenterParameters(
            kind=cast(BandCenterKind, "invented-kind"),
            selector=selector,
            energy_reference=BandCenterEnergyReference.VASP_NATIVE,
            window_lower_ev=-1.0,
            window_upper_ev=1.0,
        )
    with pytest.raises(BandCenterError, match="energy reference"):
        BandCenterParameters(
            kind=BandCenterKind.D_BAND,
            selector=selector,
            energy_reference=cast(BandCenterEnergyReference, "vacuum"),
            window_lower_ev=-1.0,
            window_upper_ev=1.0,
        )
    with pytest.raises(BandCenterError, match="integration rule"):
        BandCenterParameters(
            kind=BandCenterKind.D_BAND,
            selector=selector,
            energy_reference=BandCenterEnergyReference.VASP_NATIVE,
            window_lower_ev=-1.0,
            window_upper_ev=1.0,
            integration_rule=cast(BandCenterIntegrationRule, "simpson"),
        )
    with pytest.raises(BandCenterError, match="normalization"):
        BandCenterParameters(
            kind=BandCenterKind.D_BAND,
            selector=selector,
            energy_reference=BandCenterEnergyReference.VASP_NATIVE,
            window_lower_ev=-1.0,
            window_upper_ev=1.0,
            normalization=cast(BandCenterNormalization, "absolute-weight"),
        )


def test_v07_final_scope_lock_keeps_schema_and_analysis_surface_frozen() -> None:
    assert SCHEMA_VERSION == 3
    assert ELECTRONIC_ANALYSIS_TYPES == frozenset(
        {
            AnalysisType.DOS,
            AnalysisType.PDOS,
            AnalysisType.BADER,
            AnalysisType.CHARGE_DIFFERENCE,
            AnalysisType.COHP,
            AnalysisType.BAND_CENTER,
        }
    )
