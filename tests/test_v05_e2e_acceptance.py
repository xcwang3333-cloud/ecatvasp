from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from ecatvasp.domain import (
    Artifact,
    ArtifactAvailability,
    ArtifactType,
    Calculation,
    CalculationProducerRef,
    CalculationScientificStatus,
    CalculationType,
    Catalyst,
    ExecutionAttempt,
    ExecutionAttemptProducerRef,
    ExecutionAttemptStatus,
    ExecutionSettings,
    KPointPolicy,
    KPointPolicyKind,
    Lattice,
    MethodDefinition,
    MethodFingerprint,
    ParameterEntry,
    PotcarIdentity,
    Project,
    ProtocolDefinition,
    RecipeIdentity,
    RetrievalPolicy,
    SpinTreatment,
    StructureOrigin,
    StructureSite,
    StructureSnapshot,
    StructureVariant,
    VariantType,
    new_artifact_id,
    new_atom_uid,
)
from ecatvasp.provenance import (
    DependencyKind,
    FreshnessEngine,
    FreshnessState,
    scientific_hash,
)
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.vasp import (
    RECIPE_FULL_FREQUENCY,
    RECIPE_SLAB_RELAX,
    ConvergenceVerdict,
    ExecutionPlan,
    ExpectedOutput,
    LatticeAxis,
    PotcarResolutionEntry,
    PotcarResolutionRequest,
    StagingInput,
    StagingInputKind,
    VaspCollinearMagnetization,
    VaspFrequencyModeKind,
    VaspRuntimeConstraints,
    VaspStructurePromotionError,
    VaspSystemContext,
    VaspSystemKind,
    assess_vasp_convergence,
    build_vasp_result_artifact_intake,
    collect_vasp_convergence_evidence,
    frequency_recipe_parameters,
    materialize_vasp_scientific_result,
    parse_vasp_energy_metadata,
    parse_vasp_forces_magnetization,
    parse_vasp_frequency_results,
    promote_vasp_contcar_snapshot,
    reconstruct_vasp_contcar_snapshot,
)
from ecatvasp.vasp.result_supporting_provenance import (
    bind_vasp_atom_identity_result_provenance,
)
from ecatvasp.vasp.structure_provenance import (
    build_vasp_contcar_reconstruction_provenance,
)


@dataclass(frozen=True, slots=True)
class _RelaxCase:
    root: Path
    project: Project
    catalyst: Catalyst
    variant: StructureVariant
    input_snapshot: StructureSnapshot
    fingerprint: MethodFingerprint
    calculation: Calculation
    attempt: ExecutionAttempt
    plan: ExecutionPlan
    input_artifacts: tuple[Artifact, ...]
    raw_artifacts: tuple[Artifact, ...]
    atom_map_artifact: Artifact
    contcar_artifact: Artifact
    o_uid: object
    h_uid: object


@dataclass(frozen=True, slots=True)
class _RelaxResult:
    case: _RelaxCase
    result: object
    evidence: object
    assessment: object
    reconstruction: object
    materialization: object
    structure_provenance: object


def _write(root: Path, relative: str, body: bytes) -> tuple[str, int]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest(), len(body)


def _poscar() -> bytes:
    return (
        "v0.5 managed acceptance\n"
        "1.0\n"
        "10.0 0.0 0.0\n"
        "0.0 10.0 0.0\n"
        "0.0 0.0 16.0\n"
        "O H\n"
        "1 1\n"
        "Direct\n"
        "0.500000 0.500000 0.500000\n"
        "0.100000 0.100000 0.100000\n"
    ).encode()


def _contcar() -> bytes:
    return (
        "v0.5 relaxed candidate\n"
        "1.0\n"
        "10.5 0.0 0.0\n"
        "0.0 10.5 0.0\n"
        "0.0 0.0 16.5\n"
        "O H\n"
        "1 1\n"
        "Direct\n"
        "0.510000 0.500000 0.500000\n"
        "0.110000 0.100000 0.100000\n"
    ).encode()


def _magnetization_table() -> str:
    return (
        " magnetization (x)\n"
        " # of ion       s       p       d       tot\n"
        " --------------------------------------------\n"
        " 1 0.000 0.000 0.000 0.600000\n"
        " 2 0.000 0.000 0.000 0.400000\n"
        " --------------------------------------------\n"
        " tot 0.000 0.000 0.000 1.000000\n"
    )


def _relax_outcar(*, converged: bool) -> bytes:
    ionic_marker = (
        " reached required accuracy - stopping structural energy minimisation\n"
        if converged
        else ""
    )
    return (
        "vasp.6.4.3\n"
        " NELM = 60\n"
        " NSW = 2\n"
        " free energy TOTEN = -20.500000 eV\n"
        " energy without entropy = -20.450000 energy(sigma->0) = -20.470000\n"
        " E-fermi : 1.234500\n"
        " POSITION                                       TOTAL-FORCE (eV/Angst)\n"
        " -------------------------------------------------------------------\n"
        " 0.500 0.500 0.500 0.030 0.000 0.000\n"
        " 0.100 0.100 0.100 0.000 0.020 0.000\n"
        " -------------------------------------------------------------------\n"
        " number of electron 10.000000 magnetization 1.100000\n"
        + _magnetization_table()
        + " aborting loop because EDIFF is reached\n"
        + ionic_marker
        + " General timing and accounting informations for this job:\n"
    ).encode()


def _oszicar() -> bytes:
    return (
        " DAV:   1    -0.204000000000E+02\n"
        " DAV:   3    -0.204500000000E+02\n"
        "   1 F= -.20450000E+02 E0= -.20440000E+02 d E =-.100000E-01\n"
        " DAV:   1    -0.205000000000E+02\n"
        " DAV:   2    -0.205000000000E+02\n"
        "   2 F= -.20500000E+02 E0= -.20490000E+02 d E =-.500000E-01\n"
    ).encode()


def _local_input_artifact(
    *,
    calculation: Calculation,
    artifact_id,
    artifact_type: ArtifactType,
    local_path: str,
    body: bytes,
) -> Artifact:
    return Artifact(
        id=artifact_id,
        artifact_type=artifact_type,
        producer=CalculationProducerRef(calculation.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=local_path,
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
    )


def _local_result_artifact(
    *,
    attempt: ExecutionAttempt,
    artifact_type: ArtifactType,
    local_path: str,
    body: bytes,
) -> Artifact:
    return Artifact(
        artifact_type=artifact_type,
        producer=ExecutionAttemptProducerRef(attempt.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=local_path,
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
    )


def _relax_case(tmp_path: Path, *, converged: bool) -> _RelaxCase:
    project = Project(name="v0.5 E2E acceptance", slug="v05-e2e-acceptance")
    catalyst = Catalyst(project_id=project.id, name="H-O model", slug="h-o-model")
    h_uid = new_atom_uid()
    o_uid = new_atom_uid()
    input_snapshot = StructureSnapshot(
        lattice=Lattice(
            vectors=((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 16.0))
        ),
        sites=(
            StructureSite(h_uid, "H", (0.1, 0.1, 0.1)),
            StructureSite(o_uid, "O", (0.5, 0.5, 0.5)),
        ),
        label="managed input",
        origin=StructureOrigin.BUILT,
    )
    variant = StructureVariant(
        catalyst_id=catalyst.id,
        name="managed relax",
        variant_type=VariantType.GEOMETRY,
        current_structure_snapshot_id=input_snapshot.id,
    )
    fingerprint = MethodFingerprint(
        method=MethodDefinition(
            xc_functional="PBE",
            potcar_family="PBE_54",
            potcars=(
                PotcarIdentity("H", "H", "1" * 64),
                PotcarIdentity("O", "O", "2" * 64),
            ),
            engine_version="6.4.3",
            dispersion_model="NONE",
            spin_treatment=SpinTreatment.COLLINEAR,
        ),
        protocol=ProtocolDefinition(
            encut_ev=450.0,
            kpoints=KPointPolicy(KPointPolicyKind.GAMMA_ONLY),
            ediff_ev=1e-5,
            ediffg_ev_per_angstrom=-0.02,
        ),
        recipe=RecipeIdentity(
            RECIPE_SLAB_RELAX,
            parameters=(ParameterEntry("NSW", 2),),
        ),
    )
    calculation = Calculation(
        project_id=project.id,
        calculation_type=CalculationType.RELAX,
        input_structure_snapshot_id=input_snapshot.id,
        recipe_id=fingerprint.recipe.recipe_id,
        method_fingerprint_id=fingerprint.id,
        status=CalculationScientificStatus.READY,
        slug="managed-relax",
    )

    poscar_body = _poscar()
    poscar_sha, poscar_size = _write(tmp_path, "inputs/POSCAR", poscar_body)
    poscar_id = new_artifact_id()
    atom_map_body = json.dumps(
        {
            "format": "ecatvasp-v03-atom-index-map",
            "version": 1,
            "structure_snapshot_id": str(input_snapshot.id),
            "structure_sha256": scientific_hash(input_snapshot),
            "poscar_sha256": poscar_sha,
            "species_order": ["O", "H"],
            "species_counts": [1, 1],
            "entries": [
                {
                    "atom_uid": str(o_uid),
                    "element": "O",
                    "snapshot_index": 1,
                    "poscar_index": 0,
                    "vasp_ordinal": 1,
                    "selective_dynamics": None,
                },
                {
                    "atom_uid": str(h_uid),
                    "element": "H",
                    "snapshot_index": 0,
                    "poscar_index": 1,
                    "vasp_ordinal": 2,
                    "selective_dynamics": None,
                },
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    atom_map_sha, atom_map_size = _write(
        tmp_path,
        "inputs/atom-index-map.json",
        atom_map_body,
    )
    atom_map_id = new_artifact_id()
    input_artifacts = (
        _local_input_artifact(
            calculation=calculation,
            artifact_id=poscar_id,
            artifact_type=ArtifactType.POSCAR,
            local_path="inputs/POSCAR",
            body=poscar_body,
        ),
        _local_input_artifact(
            calculation=calculation,
            artifact_id=atom_map_id,
            artifact_type=ArtifactType.DERIVED_DATASET,
            local_path="inputs/atom-index-map.json",
            body=atom_map_body,
        ),
    )
    plan = ExecutionPlan(
        calculation_id=calculation.id,
        recipe_id=calculation.recipe_id,
        system_context=VaspSystemContext(
            VaspSystemKind.SLAB_2D,
            vacuum_axis=LatticeAxis.C,
        ),
        input_manifest_artifact_id=new_artifact_id(),
        input_manifest_sha256="a" * 64,
        preparation_hash="b" * 64,
        staging_inputs=(
            StagingInput(
                role="atom_index_map",
                kind=StagingInputKind.METADATA,
                artifact_id=atom_map_id,
                artifact_type=ArtifactType.DERIVED_DATASET,
                source_relative_path="inputs/atom-index-map.json",
                target_relative_path="atom-index-map.json",
                sha256=atom_map_sha,
                size_bytes=atom_map_size,
            ),
            StagingInput(
                role="poscar",
                kind=StagingInputKind.VASP_INPUT,
                artifact_id=poscar_id,
                artifact_type=ArtifactType.POSCAR,
                source_relative_path="inputs/POSCAR",
                target_relative_path="POSCAR",
                sha256=poscar_sha,
                size_bytes=poscar_size,
            ),
        ),
        potcar_resolution=PotcarResolutionRequest(
            family="PBE_54",
            core_method_hash=fingerprint.core_method_hash,
            metadata_hash="c" * 64,
            entries=(
                PotcarResolutionEntry("H", "H", "1" * 64),
                PotcarResolutionEntry("O", "O", "2" * 64),
            ),
        ),
        expected_outputs=tuple(
            sorted(
                (
                    ExpectedOutput(
                        "contcar",
                        ArtifactType.CONTCAR,
                        "CONTCAR",
                        RetrievalPolicy.ALWAYS,
                        True,
                    ),
                    ExpectedOutput(
                        "oszicar",
                        ArtifactType.OSZICAR,
                        "OSZICAR",
                        RetrievalPolicy.ALWAYS,
                        False,
                    ),
                    ExpectedOutput(
                        "outcar",
                        ArtifactType.OUTCAR,
                        "OUTCAR",
                        RetrievalPolicy.ALWAYS,
                        True,
                    ),
                ),
                key=lambda item: item.role,
            )
        ),
        runtime_constraints=VaspRuntimeConstraints(),
        execution_settings=ExecutionSettings(),
    )
    attempt = ExecutionAttempt(
        calculation_id=calculation.id,
        attempt_number=1,
        status=ExecutionAttemptStatus.EXITED,
        input_manifest_hash=plan.input_manifest_sha256,
        execution_plan_hash=plan.plan_hash,
    )

    outcar_body = _relax_outcar(converged=converged)
    oszicar_body = _oszicar()
    contcar_body = _contcar()
    _write(tmp_path, "outputs/OUTCAR", outcar_body)
    _write(tmp_path, "outputs/OSZICAR", oszicar_body)
    _write(tmp_path, "outputs/CONTCAR", contcar_body)
    raw_artifacts = (
        _local_result_artifact(
            attempt=attempt,
            artifact_type=ArtifactType.OUTCAR,
            local_path="outputs/OUTCAR",
            body=outcar_body,
        ),
        _local_result_artifact(
            attempt=attempt,
            artifact_type=ArtifactType.OSZICAR,
            local_path="outputs/OSZICAR",
            body=oszicar_body,
        ),
        _local_result_artifact(
            attempt=attempt,
            artifact_type=ArtifactType.CONTCAR,
            local_path="outputs/CONTCAR",
            body=contcar_body,
        ),
    )
    atom_map_artifact = next(
        item for item in input_artifacts if item.id == atom_map_id
    )
    contcar_artifact = next(
        item for item in raw_artifacts if item.artifact_type is ArtifactType.CONTCAR
    )
    return _RelaxCase(
        root=tmp_path,
        project=project,
        catalyst=catalyst,
        variant=variant,
        input_snapshot=input_snapshot,
        fingerprint=fingerprint,
        calculation=calculation,
        attempt=attempt,
        plan=plan,
        input_artifacts=input_artifacts,
        raw_artifacts=raw_artifacts,
        atom_map_artifact=atom_map_artifact,
        contcar_artifact=contcar_artifact,
        o_uid=o_uid,
        h_uid=h_uid,
    )


def _analyze_relax(case: _RelaxCase) -> _RelaxResult:
    intake = build_vasp_result_artifact_intake(
        project_root=case.root,
        calculation=case.calculation,
        plan=case.plan,
        attempt=case.attempt,
        artifacts=case.raw_artifacts,
    )
    result = parse_vasp_energy_metadata(project_root=case.root, intake=intake)
    result = parse_vasp_forces_magnetization(
        project_root=case.root,
        calculation=case.calculation,
        fingerprint=case.fingerprint,
        plan=case.plan,
        intake=intake,
        result=result,
    )
    evidence = collect_vasp_convergence_evidence(
        project_root=case.root,
        intake=intake,
        result=result,
    )
    assessment = assess_vasp_convergence(
        calculation=case.calculation,
        fingerprint=case.fingerprint,
        evidence=evidence,
    )
    reconstruction = reconstruct_vasp_contcar_snapshot(
        project_root=case.root,
        calculation=case.calculation,
        plan=case.plan,
        intake=intake,
        input_snapshot=case.input_snapshot,
    )
    materialization = materialize_vasp_scientific_result(
        project_root=case.root,
        calculation=case.calculation,
        intake=intake,
        result=result,
        assessment=assessment,
    )
    materialization = bind_vasp_atom_identity_result_provenance(
        plan=case.plan,
        result=result,
        materialization=materialization,
    )
    structure_provenance = build_vasp_contcar_reconstruction_provenance(
        calculation=case.calculation,
        plan=case.plan,
        intake=intake,
        input_snapshot=case.input_snapshot,
        reconstruction=reconstruction,
    )
    return _RelaxResult(
        case=case,
        result=result,
        evidence=evidence,
        assessment=assessment,
        reconstruction=reconstruction,
        materialization=materialization,
        structure_provenance=structure_provenance,
    )


def _bundle_relax(
    analyzed: _RelaxResult,
    *,
    variant: StructureVariant,
) -> ProjectBundle:
    case = analyzed.case
    materialization = analyzed.materialization
    reconstruction = analyzed.reconstruction
    structure_provenance = analyzed.structure_provenance
    return ProjectBundle(
        project=case.project,
        catalysts=(case.catalyst,),
        structure_variants=(variant,),
        structure_snapshots=(case.input_snapshot, reconstruction.snapshot),
        method_fingerprints=(case.fingerprint,),
        calculations=(materialization.updated_calculation,),
        execution_attempts=(case.attempt,),
        artifacts=(
            *case.input_artifacts,
            *case.raw_artifacts,
            *materialization.artifacts,
        ),
        analyses=materialization.analyses,
        provenance_records=(
            *materialization.provenance_records,
            structure_provenance.provenance_record,
        ),
        dependency_records=(
            *materialization.dependency_records,
            *structure_provenance.dependency_records,
        ),
    )


def _freshness_with_drift(
    bundle: ProjectBundle,
    *,
    drift_artifact: Artifact,
):
    dependencies = bundle.dependency_records
    node_ids = {
        node_id
        for record in dependencies
        for node_id in (record.upstream_id, record.downstream_id)
    }
    entity_by_id = {entity.id: entity for entity in bundle.provenance_entities()}
    upstream_ids = {record.upstream_id for record in dependencies}
    current_hashes = {
        upstream_id: scientific_hash(entity_by_id[upstream_id])
        for upstream_id in upstream_ids
    }
    current_hashes[drift_artifact.id] = "f" * 64
    return FreshnessEngine(dependencies).evaluate(
        node_ids=node_ids,
        current_hashes=current_hashes,
    )


def test_managed_converged_relax_accepts_full_v05_scientific_handoff(
    tmp_path: Path,
) -> None:
    analyzed = _analyze_relax(_relax_case(tmp_path, converged=True))
    case = analyzed.case

    assert case.attempt.status is ExecutionAttemptStatus.EXITED
    assert case.calculation.status is CalculationScientificStatus.READY
    assert analyzed.assessment.overall is ConvergenceVerdict.CONVERGED
    assert analyzed.result.energies.free_energy_toten_ev == pytest.approx(-20.5)
    assert analyzed.result.energies.energy_without_entropy_ev == pytest.approx(-20.45)
    assert analyzed.result.forces is not None
    assert tuple(item.atom_uid for item in analyzed.result.forces.site_forces) == (
        case.o_uid,
        case.h_uid,
    )
    assert analyzed.result.forces.max_force_ev_per_angstrom == pytest.approx(0.03)
    assert isinstance(analyzed.result.magnetization, VaspCollinearMagnetization)
    assert analyzed.result.magnetization.projected_total_mu_b == pytest.approx(1.0)

    promotion = promote_vasp_contcar_snapshot(
        variant=case.variant,
        calculation=case.calculation,
        fingerprint=case.fingerprint,
        evidence=analyzed.evidence,
        input_snapshot=case.input_snapshot,
        reconstruction=analyzed.reconstruction,
    )
    assert promotion.updated_variant.current_structure_snapshot_id == promotion.snapshot.id
    assert promotion.snapshot.origin is StructureOrigin.RELAXED
    assert promotion.snapshot.parent_snapshot_id == case.input_snapshot.id
    assert (
        analyzed.materialization.updated_calculation.status
        is CalculationScientificStatus.CONVERGED
    )

    parse_inputs = set(
        analyzed.materialization.result_parse_analysis.input_artifact_ids
    )
    assert case.atom_map_artifact.id in parse_inputs
    assert {item.id for item in case.raw_artifacts}.issubset(parse_inputs)

    bundle = _bundle_relax(analyzed, variant=promotion.updated_variant)
    bundle.validate()
    ProjectStore(tmp_path).save(bundle)
    reopened = ProjectStore(tmp_path).open()
    assert reopened == bundle

    atom_map_freshness = _freshness_with_drift(
        reopened,
        drift_artifact=case.atom_map_artifact,
    )
    assert (
        atom_map_freshness[analyzed.reconstruction.snapshot.id].state
        is FreshnessState.STALE
    )
    assert (
        atom_map_freshness[analyzed.materialization.result_parse_analysis.id].state
        is FreshnessState.STALE
    )
    assert (
        atom_map_freshness[analyzed.materialization.convergence_artifact.id].state
        is FreshnessState.STALE
    )

    contcar_freshness = _freshness_with_drift(
        reopened,
        drift_artifact=case.contcar_artifact,
    )
    assert (
        contcar_freshness[analyzed.reconstruction.snapshot.id].state
        is FreshnessState.STALE
    )
    assert (
        contcar_freshness[analyzed.materialization.parsed_result_artifact.id].state
        is FreshnessState.STALE
    )


def test_managed_unconverged_relax_keeps_candidate_without_promotion(
    tmp_path: Path,
) -> None:
    analyzed = _analyze_relax(_relax_case(tmp_path, converged=False))
    case = analyzed.case

    assert case.attempt.status is ExecutionAttemptStatus.EXITED
    assert analyzed.assessment.overall is ConvergenceVerdict.UNCONVERGED
    assert (
        analyzed.materialization.updated_calculation.status
        is CalculationScientificStatus.COMPLETED_UNCONVERGED
    )
    assert analyzed.reconstruction.snapshot.id != case.input_snapshot.id
    assert analyzed.reconstruction.snapshot.origin is StructureOrigin.RELAXED

    with pytest.raises(VaspStructurePromotionError, match="scientifically converged"):
        promote_vasp_contcar_snapshot(
            variant=case.variant,
            calculation=case.calculation,
            fingerprint=case.fingerprint,
            evidence=analyzed.evidence,
            input_snapshot=case.input_snapshot,
            reconstruction=analyzed.reconstruction,
        )
    assert case.variant.current_structure_snapshot_id == case.input_snapshot.id

    bundle = _bundle_relax(analyzed, variant=case.variant)
    bundle.validate()
    ProjectStore(tmp_path).save(bundle)
    reopened = ProjectStore(tmp_path).open()
    assert reopened.calculations[0].status is CalculationScientificStatus.COMPLETED_UNCONVERGED
    assert reopened.structure_variants[0].current_structure_snapshot_id == case.input_snapshot.id
    assert analyzed.reconstruction.snapshot.id in {
        item.id for item in reopened.structure_snapshots
    }


def _frequency_mode_block() -> str:
    lines = [
        " Eigenvectors and eigenvalues of the dynamical matrix\n",
        " ----------------------------------------------------\n",
    ]
    for mode_index in range(1, 4):
        marker = "f/i" if mode_index == 1 else "f"
        frequency = 10.0 + mode_index
        lines.append(
            f" {mode_index:3d} {marker} = {frequency:.6f} THz "
            f"{frequency * 6.283185:.6f} 2PiTHz "
            f"{frequency * 33.3564:.6f} cm-1 {frequency * 4.13567:.6f} meV\n"
        )
        lines.append(" X         Y         Z           dx          dy          dz\n")
        scale = mode_index / 100.0
        lines.append(
            f" 0.000000 0.000000 0.000000 {scale:.6f} "
            f"{scale + 0.01:.6f} {scale + 0.02:.6f}\n"
        )
    return "".join(lines)


def test_managed_frequency_acceptance_stops_before_thermochemistry(tmp_path: Path) -> None:
    project = Project(name="v0.5 frequency acceptance", slug="v05-frequency-acceptance")
    atom_uid = new_atom_uid()
    snapshot = StructureSnapshot(
        lattice=Lattice(
            vectors=((12.0, 0.0, 0.0), (0.0, 12.0, 0.0), (0.0, 0.0, 12.0))
        ),
        sites=(StructureSite(atom_uid, "H", (0.5, 0.5, 0.5)),),
        periodic=(False, False, False),
    )
    fingerprint = MethodFingerprint(
        method=MethodDefinition(
            xc_functional="PBE",
            potcar_family="PBE_54",
            potcars=(PotcarIdentity("H", "H", "1" * 64),),
            dispersion_model="NONE",
            spin_treatment=SpinTreatment.UNPOLARIZED,
        ),
        protocol=ProtocolDefinition(
            encut_ev=450.0,
            kpoints=KPointPolicy(KPointPolicyKind.GAMMA_ONLY),
            ediff_ev=1e-8,
        ),
        recipe=RecipeIdentity(
            RECIPE_FULL_FREQUENCY,
            parameters=frequency_recipe_parameters(potim_angstrom=0.015),
        ),
    )
    calculation = Calculation(
        project_id=project.id,
        calculation_type=CalculationType.FREQUENCY,
        input_structure_snapshot_id=snapshot.id,
        recipe_id=fingerprint.recipe.recipe_id,
        method_fingerprint_id=fingerprint.id,
        status=CalculationScientificStatus.READY,
        slug="managed-frequency",
    )
    poscar_body = (
        "H frequency\n1.0\n12 0 0\n0 12 0\n0 0 12\nH\n1\nDirect\n0.5 0.5 0.5\n"
    ).encode()
    poscar_sha, poscar_size = _write(tmp_path, "freq/POSCAR", poscar_body)
    poscar_id = new_artifact_id()
    atom_map_body = json.dumps(
        {
            "format": "ecatvasp-v03-atom-index-map",
            "version": 1,
            "structure_snapshot_id": str(snapshot.id),
            "structure_sha256": scientific_hash(snapshot),
            "poscar_sha256": poscar_sha,
            "species_order": ["H"],
            "species_counts": [1],
            "entries": [
                {
                    "atom_uid": str(atom_uid),
                    "element": "H",
                    "snapshot_index": 0,
                    "poscar_index": 0,
                    "vasp_ordinal": 1,
                    "selective_dynamics": None,
                }
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    atom_map_sha, atom_map_size = _write(
        tmp_path,
        "freq/atom-index-map.json",
        atom_map_body,
    )
    atom_map_id = new_artifact_id()
    input_artifacts = (
        _local_input_artifact(
            calculation=calculation,
            artifact_id=poscar_id,
            artifact_type=ArtifactType.POSCAR,
            local_path="freq/POSCAR",
            body=poscar_body,
        ),
        _local_input_artifact(
            calculation=calculation,
            artifact_id=atom_map_id,
            artifact_type=ArtifactType.DERIVED_DATASET,
            local_path="freq/atom-index-map.json",
            body=atom_map_body,
        ),
    )
    plan = ExecutionPlan(
        calculation_id=calculation.id,
        recipe_id=calculation.recipe_id,
        system_context=VaspSystemContext(VaspSystemKind.MOLECULE_0D),
        input_manifest_artifact_id=new_artifact_id(),
        input_manifest_sha256="d" * 64,
        preparation_hash="e" * 64,
        staging_inputs=(
            StagingInput(
                role="atom_index_map",
                kind=StagingInputKind.METADATA,
                artifact_id=atom_map_id,
                artifact_type=ArtifactType.DERIVED_DATASET,
                source_relative_path="freq/atom-index-map.json",
                target_relative_path="atom-index-map.json",
                sha256=atom_map_sha,
                size_bytes=atom_map_size,
            ),
            StagingInput(
                role="poscar",
                kind=StagingInputKind.VASP_INPUT,
                artifact_id=poscar_id,
                artifact_type=ArtifactType.POSCAR,
                source_relative_path="freq/POSCAR",
                target_relative_path="POSCAR",
                sha256=poscar_sha,
                size_bytes=poscar_size,
            ),
        ),
        potcar_resolution=PotcarResolutionRequest(
            family="PBE_54",
            core_method_hash=fingerprint.core_method_hash,
            metadata_hash="f" * 64,
            entries=(PotcarResolutionEntry("H", "H", "1" * 64),),
        ),
        expected_outputs=(
            ExpectedOutput(
                "outcar",
                ArtifactType.OUTCAR,
                "OUTCAR",
                RetrievalPolicy.ALWAYS,
                True,
            ),
        ),
        runtime_constraints=VaspRuntimeConstraints(),
        execution_settings=ExecutionSettings(),
    )
    attempt = ExecutionAttempt(
        calculation_id=calculation.id,
        attempt_number=1,
        status=ExecutionAttemptStatus.EXITED,
        input_manifest_hash=plan.input_manifest_sha256,
        execution_plan_hash=plan.plan_hash,
    )
    outcar_body = (
        "vasp.6.4.3\n"
        " NELM = 60\n"
        " NSW = 1\n"
        " free energy TOTEN = -1.234500 eV\n"
        " aborting loop because EDIFF is reached\n"
        + _frequency_mode_block()
        + " General timing and accounting informations for this job:\n"
    ).encode()
    _write(tmp_path, "freq/OUTCAR", outcar_body)
    outcar = _local_result_artifact(
        attempt=attempt,
        artifact_type=ArtifactType.OUTCAR,
        local_path="freq/OUTCAR",
        body=outcar_body,
    )
    intake = build_vasp_result_artifact_intake(
        project_root=tmp_path,
        calculation=calculation,
        plan=plan,
        attempt=attempt,
        artifacts=(outcar,),
    )
    result = parse_vasp_energy_metadata(project_root=tmp_path, intake=intake)
    result = parse_vasp_frequency_results(
        project_root=tmp_path,
        calculation=calculation,
        fingerprint=fingerprint,
        plan=plan,
        intake=intake,
        input_snapshot=snapshot,
        result=result,
    )
    evidence = collect_vasp_convergence_evidence(
        project_root=tmp_path,
        intake=intake,
        result=result,
    )
    assessment = assess_vasp_convergence(
        calculation=calculation,
        fingerprint=fingerprint,
        evidence=evidence,
    )
    materialization = materialize_vasp_scientific_result(
        project_root=tmp_path,
        calculation=calculation,
        intake=intake,
        result=result,
        assessment=assessment,
    )
    materialization = bind_vasp_atom_identity_result_provenance(
        plan=plan,
        result=result,
        materialization=materialization,
    )

    assert assessment.overall is ConvergenceVerdict.CONVERGED
    assert result.frequencies is not None
    assert result.frequencies.degrees_of_freedom == 3
    assert result.frequencies.imaginary_mode_count == 1
    assert result.frequencies.modes[0].kind is VaspFrequencyModeKind.IMAGINARY
    assert result.frequencies.modes[0].eigenvectors[0].atom_uid == atom_uid
    assert materialization.updated_calculation.status is CalculationScientificStatus.CONVERGED
    assert atom_map_id in materialization.result_parse_analysis.input_artifact_ids

    parsed_path = tmp_path / (materialization.parsed_result_artifact.local_path or "")
    parsed_payload = parsed_path.read_text(encoding="utf-8")
    assert '"frequencies"' in parsed_payload
    assert '"zpe"' not in parsed_payload.casefold()
    assert '"entropy"' not in parsed_payload.casefold()
    assert not hasattr(result, "zpe_ev")

    bundle = ProjectBundle(
        project=project,
        structure_snapshots=(snapshot,),
        method_fingerprints=(fingerprint,),
        calculations=(materialization.updated_calculation,),
        execution_attempts=(attempt,),
        artifacts=(*input_artifacts, outcar, *materialization.artifacts),
        analyses=materialization.analyses,
        provenance_records=materialization.provenance_records,
        dependency_records=materialization.dependency_records,
    )
    bundle.validate()
    ProjectStore(tmp_path).save(bundle)
    reopened = ProjectStore(tmp_path).open()
    assert reopened == bundle

    atom_map = next(item for item in input_artifacts if item.id == atom_map_id)
    freshness = _freshness_with_drift(reopened, drift_artifact=atom_map)
    assert (
        freshness[materialization.result_parse_analysis.id].state
        is FreshnessState.STALE
    )
    assert (
        freshness[materialization.parsed_result_artifact.id].state
        is FreshnessState.STALE
    )
