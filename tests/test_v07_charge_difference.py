from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pytest
from ase import Atoms
from ase.calculators.vasp import VaspChargeDensity

from ecatvasp import domain, vasp
from ecatvasp.analysis.charge_difference import (
    ChargeDifferenceAnalysisError,
    ChargeDifferenceRole,
    ChargeDifferenceSource,
    DurableChargeDifference,
    load_charge_difference_artifacts,
    materialize_charge_difference_analysis,
)
from ecatvasp.provenance import FreshnessEngine, FreshnessState, scientific_hash
from ecatvasp.schema.version import SCHEMA_VERSION
from ecatvasp.storage import ProjectBundle, ProjectStore


@dataclass(frozen=True, slots=True)
class _Source:
    member: vasp.ChargeDifferenceTripletMember
    attempt: domain.ExecutionAttempt
    artifact: domain.Artifact
    density: np.ndarray


@dataclass(frozen=True, slots=True)
class _Case:
    project: domain.Project
    triplet: vasp.ChargeDifferenceTriplet
    combined: _Source
    slab: _Source
    adsorbate: _Source
    expected_delta: np.ndarray


def _snapshot(
    *,
    carbon_uid: domain.AtomUid,
    oxygen_uid: domain.AtomUid,
) -> tuple[domain.StructureSnapshot, domain.StructureSnapshot, domain.StructureSnapshot]:
    lattice = domain.Lattice(
        vectors=((4.0, 0.0, 0.0), (0.0, 4.0, 0.0), (0.0, 0.0, 24.0))
    )
    carbon = domain.StructureSite(carbon_uid, "C", (0.25, 0.25, 0.45))
    oxygen = domain.StructureSite(oxygen_uid, "O", (0.50, 0.50, 0.57))
    combined = domain.StructureSnapshot(
        lattice=lattice,
        sites=(carbon, oxygen),
        periodic=(True, True, False),
    )
    slab = domain.StructureSnapshot(
        lattice=lattice,
        sites=(carbon,),
        periodic=combined.periodic,
    )
    adsorbate = domain.StructureSnapshot(
        lattice=lattice,
        sites=(oxygen,),
        periodic=combined.periodic,
    )
    return combined, slab, adsorbate


def _member(
    *,
    project: domain.Project,
    snapshot: domain.StructureSnapshot,
    potcars: tuple[domain.PotcarIdentity, ...],
    protocol: domain.ProtocolDefinition,
    context: vasp.VaspSystemContext,
) -> vasp.ChargeDifferenceTripletMember:
    method = domain.MethodDefinition(
        xc_functional="PBE",
        potcar_family="PBE_54",
        potcars=potcars,
        dispersion_model="NONE",
        spin_treatment=domain.SpinTreatment.UNPOLARIZED,
    )
    recipe = domain.RecipeIdentity(vasp.RECIPE_CHARGE_DENSITY_STATIC)
    fingerprint = domain.MethodFingerprint(method=method, protocol=protocol, recipe=recipe)
    calculation = domain.Calculation(
        project_id=project.id,
        calculation_type=domain.CalculationType.CHARGE_STATIC,
        input_structure_snapshot_id=snapshot.id,
        recipe_id=recipe.recipe_id,
        method_fingerprint_id=fingerprint.id,
        status=domain.CalculationScientificStatus.CONVERGED,
    )
    evidence_hash = hashlib.sha256(
        f"encut-{fingerprint.core_method_hash}".encode()
    ).hexdigest()
    encut_evidence = vasp.EncCutValidationEvidence(
        core_method_hash=fingerprint.core_method_hash,
        potcar_spec_hash="a" * 64,
        tested_encuts_ev=(400.0, 450.0, 500.0),
        selected_encut_ev=450.0,
        analysis_hash=evidence_hash,
    )
    lock = vasp.ProjectNumericalLock(
        project_id=project.id,
        system_kind=context.kind,
        core_method_hash=fingerprint.core_method_hash,
        encut_ev=protocol.encut_ev,
        encut_validation_hash=evidence_hash,
        kpoints=protocol.kpoints,
    )
    return vasp.ChargeDifferenceTripletMember(
        calculation=calculation,
        snapshot=snapshot,
        fingerprint=fingerprint,
        project_lock=lock,
        encut_evidence=encut_evidence,
    )


def _write_chgcar(
    *,
    project_root: Path,
    member: vasp.ChargeDifferenceTripletMember,
    attempt: domain.ExecutionAttempt,
    density: np.ndarray,
    header_snapshot: domain.StructureSnapshot | None = None,
) -> domain.Artifact:
    snapshot = header_snapshot or member.snapshot
    prepared = vasp.prepare_poscar(snapshot)
    symbols = [
        snapshot.sites[item.snapshot_index].element for item in prepared.index_map.entries
    ]
    scaled_positions = [
        snapshot.sites[item.snapshot_index].fractional_coords
        for item in prepared.index_map.entries
    ]
    atoms = Atoms(
        symbols=symbols,
        cell=snapshot.lattice.vectors,
        scaled_positions=scaled_positions,
    )
    charge = VaspChargeDensity(None)
    charge.atoms = [atoms]
    charge.chg = [np.asarray(density, dtype=float)]
    path = (
        project_root
        / "calculations"
        / str(member.calculation.id)
        / f"attempt-{attempt.attempt_number}"
        / "CHGCAR"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    charge.write(str(path), format="chgcar")
    body = path.read_bytes()
    return domain.Artifact(
        artifact_type=domain.ArtifactType.CHGCAR,
        producer=domain.ExecutionAttemptProducerRef(attempt.id),
        availability=domain.ArtifactAvailability.LOCAL,
        retrieval_policy=domain.RetrievalPolicy.ALWAYS,
        local_path=path.relative_to(project_root).as_posix(),
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
    )


def _source(
    *,
    project_root: Path,
    member: vasp.ChargeDifferenceTripletMember,
    density: np.ndarray,
    header_snapshot: domain.StructureSnapshot | None = None,
) -> _Source:
    attempt = domain.ExecutionAttempt(
        calculation_id=member.calculation.id,
        attempt_number=1,
        status=domain.ExecutionAttemptStatus.PARSED,
    )
    artifact = _write_chgcar(
        project_root=project_root,
        member=member,
        attempt=attempt,
        density=density,
        header_snapshot=header_snapshot,
    )
    return _Source(member=member, attempt=attempt, artifact=artifact, density=density)


def _case(tmp_path: Path) -> _Case:
    project = domain.Project(name="Charge Difference", slug="charge-difference-v07")
    carbon_uid = domain.new_atom_uid()
    oxygen_uid = domain.new_atom_uid()
    combined_snapshot, slab_snapshot, adsorbate_snapshot = _snapshot(
        carbon_uid=carbon_uid,
        oxygen_uid=oxygen_uid,
    )
    c_potcar = domain.PotcarIdentity("C", "C", "c" * 64)
    o_potcar = domain.PotcarIdentity("O", "O", "d" * 64)
    protocol = domain.ProtocolDefinition(
        encut_ev=450.0,
        kpoints=domain.KPointPolicy(domain.KPointPolicyKind.GAMMA_ONLY),
        precision="Accurate",
        ediff_ev=1.0e-6,
        ismear=0,
        sigma_ev=0.05,
    )
    context = vasp.VaspSystemContext(
        vasp.VaspSystemKind.SLAB_2D,
        vacuum_axis=vasp.LatticeAxis.C,
    )
    combined_member = _member(
        project=project,
        snapshot=combined_snapshot,
        potcars=(c_potcar, o_potcar),
        protocol=protocol,
        context=context,
    )
    slab_member = _member(
        project=project,
        snapshot=slab_snapshot,
        potcars=(c_potcar,),
        protocol=protocol,
        context=context,
    )
    adsorbate_member = _member(
        project=project,
        snapshot=adsorbate_snapshot,
        potcars=(o_potcar,),
        protocol=protocol,
        context=context,
    )
    triplet = vasp.ChargeDifferenceTriplet(
        combined=combined_member,
        slab=slab_member,
        adsorbate=adsorbate_member,
        system_context=context,
    )
    combined_density = np.asarray(
        [
            [[0.10, 0.12], [0.14, 0.16]],
            [[0.18, 0.20], [0.22, 0.24]],
        ],
        dtype=float,
    )
    slab_density = np.full((2, 2, 2), 0.04, dtype=float)
    adsorbate_density = np.full((2, 2, 2), 0.03, dtype=float)
    combined = _source(
        project_root=tmp_path,
        member=combined_member,
        density=combined_density,
    )
    slab = _source(
        project_root=tmp_path,
        member=slab_member,
        density=slab_density,
    )
    adsorbate = _source(
        project_root=tmp_path,
        member=adsorbate_member,
        density=adsorbate_density,
    )
    return _Case(
        project=project,
        triplet=triplet,
        combined=combined,
        slab=slab,
        adsorbate=adsorbate,
        expected_delta=combined_density - slab_density - adsorbate_density,
    )


def _materialize(tmp_path: Path, case: _Case) -> DurableChargeDifference:
    return materialize_charge_difference_analysis(
        project_root=tmp_path,
        triplet=case.triplet,
        combined_source=ChargeDifferenceSource(
            ChargeDifferenceRole.COMBINED,
            case.combined.attempt,
            case.combined.artifact,
        ),
        slab_source=ChargeDifferenceSource(
            ChargeDifferenceRole.SLAB,
            case.slab.attempt,
            case.slab.artifact,
        ),
        adsorbate_source=ChargeDifferenceSource(
            ChargeDifferenceRole.ADSORBATE,
            case.adsorbate.attempt,
            case.adsorbate.artifact,
        ),
    )


def test_charge_difference_materializes_physical_density(tmp_path: Path) -> None:
    case = _case(tmp_path)
    result = _materialize(tmp_path, case)

    assert result.analysis.analysis_type is domain.AnalysisType.CHARGE_DIFFERENCE
    assert result.analysis.status is domain.AnalysisStatus.COMPLETED
    assert result.analysis.input_artifact_ids == (
        case.combined.artifact.id,
        case.slab.artifact.id,
        case.adsorbate.artifact.id,
    )
    assert isinstance(result.density_artifact.producer, domain.AnalysisProducerRef)
    assert isinstance(result.metadata_artifact.producer, domain.AnalysisProducerRef)
    assert result.density_artifact.producer.id == result.analysis.id
    assert result.metadata_artifact.producer.id == result.analysis.id
    assert result.metadata.grid_shape_xyz == (2, 2, 2)
    assert result.metadata.density_unit == "1/angstrom^3"

    reopened = load_charge_difference_artifacts(
        project_root=tmp_path,
        analysis=result.analysis,
        density_artifact=result.density_artifact,
        metadata_artifact=result.metadata_artifact,
    )
    assert np.allclose(reopened.density, case.expected_delta, rtol=0.0, atol=1.0e-11)
    cell_volume = 4.0 * 4.0 * 24.0
    voxel_volume = cell_volume / 8.0
    assert result.metadata.delta_electron_integral == pytest.approx(
        float(np.sum(case.expected_delta) * voxel_volume),
        abs=1.0e-9,
    )


def test_charge_difference_project_store_roundtrip_and_freshness(tmp_path: Path) -> None:
    case = _case(tmp_path)
    result = _materialize(tmp_path, case)
    members = (case.triplet.combined, case.triplet.slab, case.triplet.adsorbate)
    sources = (case.combined, case.slab, case.adsorbate)
    bundle = ProjectBundle(
        project=case.project,
        structure_snapshots=tuple(item.snapshot for item in members),
        method_fingerprints=tuple(item.fingerprint for item in members),
        calculations=tuple(item.calculation for item in members),
        execution_attempts=tuple(item.attempt for item in sources),
        artifacts=(
            case.combined.artifact,
            case.slab.artifact,
            case.adsorbate.artifact,
            result.density_artifact,
            result.metadata_artifact,
        ),
        analyses=(result.analysis,),
        provenance_records=result.provenance_records,
        dependency_records=result.dependency_records,
    )
    bundle.validate()
    ProjectStore(tmp_path).save(bundle)
    assert ProjectStore(tmp_path).open() == bundle
    assert SCHEMA_VERSION == 3

    upstreams = tuple(
        [item.calculation for item in members]
        + [item.snapshot for item in members]
        + [item.fingerprint for item in members]
        + [item.artifact for item in sources]
    )
    node_ids = {
        *(item.id for item in upstreams),
        result.analysis.id,
        result.density_artifact.id,
        result.metadata_artifact.id,
    }
    current_hashes = {item.id: scientific_hash(item) for item in upstreams}
    current_hashes[case.slab.artifact.id] = "f" * 64
    current_hashes[result.analysis.id] = scientific_hash(result.analysis)
    freshness = FreshnessEngine(result.dependency_records).evaluate(
        node_ids=node_ids,
        current_hashes=current_hashes,
    )
    assert freshness[result.analysis.id].state is FreshnessState.STALE
    assert freshness[result.density_artifact.id].state is FreshnessState.STALE
    assert freshness[result.metadata_artifact.id].state is FreshnessState.STALE
    attempt_ids = {item.attempt.id for item in sources}
    analysis_upstreams = {
        item.upstream_id
        for item in result.dependency_records
        if item.downstream_id == result.analysis.id
    }
    assert not attempt_ids & analysis_upstreams


def test_charge_difference_rejects_fft_grid_mismatch(tmp_path: Path) -> None:
    case = _case(tmp_path)
    replacement_density = np.full((2, 2, 3), 0.03, dtype=float)
    replacement = _source(
        project_root=tmp_path,
        member=case.adsorbate.member,
        density=replacement_density,
    )
    with pytest.raises(ChargeDifferenceAnalysisError, match="FFT grids"):
        materialize_charge_difference_analysis(
            project_root=tmp_path,
            triplet=case.triplet,
            combined_source=ChargeDifferenceSource(
                ChargeDifferenceRole.COMBINED,
                case.combined.attempt,
                case.combined.artifact,
            ),
            slab_source=ChargeDifferenceSource(
                ChargeDifferenceRole.SLAB,
                case.slab.attempt,
                case.slab.artifact,
            ),
            adsorbate_source=ChargeDifferenceSource(
                ChargeDifferenceRole.ADSORBATE,
                replacement.attempt,
                replacement.artifact,
            ),
        )


def test_charge_difference_rejects_chgcar_header_coordinate_drift(tmp_path: Path) -> None:
    case = _case(tmp_path)
    original_site = case.slab.member.snapshot.sites[0]
    moved_snapshot = replace(
        case.slab.member.snapshot,
        sites=(replace(original_site, fractional_coords=(0.25, 0.25, 0.46)),),
    )
    moved_source = _source(
        project_root=tmp_path,
        member=case.slab.member,
        density=case.slab.density,
        header_snapshot=moved_snapshot,
    )
    with pytest.raises(ChargeDifferenceAnalysisError, match="coordinates differ"):
        materialize_charge_difference_analysis(
            project_root=tmp_path,
            triplet=case.triplet,
            combined_source=ChargeDifferenceSource(
                ChargeDifferenceRole.COMBINED,
                case.combined.attempt,
                case.combined.artifact,
            ),
            slab_source=ChargeDifferenceSource(
                ChargeDifferenceRole.SLAB,
                moved_source.attempt,
                moved_source.artifact,
            ),
            adsorbate_source=ChargeDifferenceSource(
                ChargeDifferenceRole.ADSORBATE,
                case.adsorbate.attempt,
                case.adsorbate.artifact,
            ),
        )


def test_charge_difference_requires_converged_calculations(tmp_path: Path) -> None:
    case = _case(tmp_path)
    bad_calculation = replace(
        case.slab.member.calculation,
        status=domain.CalculationScientificStatus.COMPLETED_UNCONVERGED,
    )
    bad_member = replace(case.slab.member, calculation=bad_calculation)
    bad_triplet = replace(case.triplet, slab=bad_member)
    with pytest.raises(ChargeDifferenceAnalysisError, match="scientifically converged"):
        materialize_charge_difference_analysis(
            project_root=tmp_path,
            triplet=bad_triplet,
            combined_source=ChargeDifferenceSource(
                ChargeDifferenceRole.COMBINED,
                case.combined.attempt,
                case.combined.artifact,
            ),
            slab_source=ChargeDifferenceSource(
                ChargeDifferenceRole.SLAB,
                case.slab.attempt,
                case.slab.artifact,
            ),
            adsorbate_source=ChargeDifferenceSource(
                ChargeDifferenceRole.ADSORBATE,
                case.adsorbate.attempt,
                case.adsorbate.artifact,
            ),
        )


def test_charge_difference_loader_detects_density_tamper(tmp_path: Path) -> None:
    case = _case(tmp_path)
    result = _materialize(tmp_path, case)
    density_path = tmp_path / (result.density_artifact.local_path or "")
    density_path.write_bytes(density_path.read_bytes() + b"tamper")

    with pytest.raises(ChargeDifferenceAnalysisError, match="byte size changed"):
        load_charge_difference_artifacts(
            project_root=tmp_path,
            analysis=result.analysis,
            density_artifact=result.density_artifact,
            metadata_artifact=result.metadata_artifact,
        )
