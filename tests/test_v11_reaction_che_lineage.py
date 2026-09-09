from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ecatvasp.api.reaction_workspace import (
    ORRPresetAnalysisBindings,
    ProjectReactionWorkspaceApplicationService,
)
from ecatvasp.api.thermochemistry_workspace_support import (
    observed_artifact_state,
    thermochemistry_projection,
)
from ecatvasp.domain import (
    Analysis,
    AnalysisProducerRef,
    AnalysisStatus,
    AnalysisType,
    Artifact,
    ArtifactAvailability,
    ArtifactType,
    Project,
    RetrievalPolicy,
    canonical_json,
    canonical_sha256,
    new_atom_uid,
)
from ecatvasp.provenance import (
    DependencyKind,
    DependencyRecord,
    ProvenanceRecord,
    scientific_hash,
)
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.thermo import (
    CANONICAL_HARMONIC_THERMOCHEMISTRY_FORMAT,
    CANONICAL_HARMONIC_THERMOCHEMISTRY_VERSION,
    CANONICAL_IDEAL_GAS_THERMOCHEMISTRY_FORMAT,
    CANONICAL_IDEAL_GAS_THERMOCHEMISTRY_VERSION,
    HARMONIC_THERMOCHEMISTRY_TOOL_NAME,
    HARMONIC_THERMOCHEMISTRY_TOOL_VERSION,
    IDEAL_GAS_THERMOCHEMISTRY_TOOL_NAME,
    IDEAL_GAS_THERMOCHEMISTRY_TOOL_VERSION,
    CHEConditions,
    CHEPhSemantics,
    ElectrocatalysisPresetKind,
    ElectrodePotentialReference,
    ElectronicEnergyKind,
    ElectronicEntropyPolicy,
    GasAtomicMass,
    GasGeometryKind,
    GasMoleculeModel,
    GasReferenceDefinition,
    GasReferenceSpecies,
    ImaginaryModePolicy,
    LowFrequencyPolicy,
    ThermochemicalConditions,
    ThermochemicalStandardState,
    ThermochemistryComponents,
    ThermochemistryIdentity,
    ThermochemistryModeSelection,
    ThermochemistryResult,
    ThermochemistrySubjectKind,
    VibrationalModePolicy,
)
from ecatvasp.workflow import (
    ThermochemistryAnalysisScientificState,
    WorkflowStepReadiness,
)


@dataclass(frozen=True, slots=True)
class _Fixture:
    source_analysis: Analysis
    source_artifact: Artifact
    analysis: Analysis
    artifact: Artifact
    provenance: tuple[ProvenanceRecord, ...]
    dependencies: tuple[DependencyRecord, ...]


def _surface_result(subject: ThermochemistrySubjectKind, energy_ev: float) -> ThermochemistryResult:
    return ThermochemistryResult(
        identity=ThermochemistryIdentity(
            subject_kind=subject,
            conditions=ThermochemicalConditions(
                temperature_k=298.15,
                standard_state=ThermochemicalStandardState.SURFACE_FIXED_CELL,
            ),
            electronic_energy_kind=ElectronicEnergyKind.SIGMA_ZERO,
            electronic_entropy_policy=ElectronicEntropyPolicy.NEGLECTED,
            vibrational_policy=VibrationalModePolicy(
                frequency_cutoff_cm_inverse=50.0,
                imaginary_mode_policy=ImaginaryModePolicy.REJECT_ANY,
                low_frequency_policy=LowFrequencyPolicy.REJECT_BELOW_CUTOFF,
            ),
        ),
        components=ThermochemistryComponents(electronic_energy_ev=energy_ev),
        mode_selection=ThermochemistryModeSelection(accepted_mode_indices=(1,)),
    )


def _gas_result(species: GasReferenceSpecies, energy_ev: float) -> ThermochemistryResult:
    if species is GasReferenceSpecies.H2:
        masses = (1.00784, 1.00784)
        geometry = GasGeometryKind.LINEAR
        symmetry = 2
        multiplicity = 1
    elif species is GasReferenceSpecies.O2:
        masses = (15.999, 15.999)
        geometry = GasGeometryKind.LINEAR
        symmetry = 2
        multiplicity = 3
    elif species is GasReferenceSpecies.H2O:
        masses = (1.00784, 1.00784, 15.999)
        geometry = GasGeometryKind.NONLINEAR
        symmetry = 2
        multiplicity = 1
    else:
        raise AssertionError("test only needs H2/O2/H2O")
    return ThermochemistryResult(
        identity=ThermochemistryIdentity(
            subject_kind=ThermochemistrySubjectKind.GAS,
            conditions=ThermochemicalConditions(
                temperature_k=298.15,
                standard_state=ThermochemicalStandardState.IDEAL_GAS_1_BAR,
                pressure_pa=100_000.0,
            ),
            electronic_energy_kind=ElectronicEnergyKind.SIGMA_ZERO,
            electronic_entropy_policy=ElectronicEntropyPolicy.NEGLECTED,
            vibrational_policy=VibrationalModePolicy(
                frequency_cutoff_cm_inverse=50.0,
                imaginary_mode_policy=ImaginaryModePolicy.REJECT_ANY,
                low_frequency_policy=LowFrequencyPolicy.REJECT_BELOW_CUTOFF,
            ),
            gas_model=GasMoleculeModel(
                geometry_kind=geometry,
                symmetry_number=symmetry,
                spin_multiplicity=multiplicity,
                atomic_masses=tuple(
                    GasAtomicMass(new_atom_uid(), mass) for mass in masses
                ),
            ),
        ),
        components=ThermochemistryComponents(electronic_energy_ev=energy_ev),
        mode_selection=ThermochemistryModeSelection(accepted_mode_indices=(1,)),
    )


def _artifact(
    *,
    root: Path,
    relative: Path,
    body: bytes,
    artifact_type: ArtifactType,
    producer: AnalysisProducerRef,
) -> Artifact:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return Artifact(
        artifact_type=artifact_type,
        producer=producer,
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=relative.as_posix(),
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
    )


def _thermo(
    root: Path,
    project: Project,
    result: ThermochemistryResult,
    *,
    reference: GasReferenceDefinition | None = None,
) -> _Fixture:
    source_analysis = Analysis(
        project_id=project.id,
        analysis_type=AnalysisType.RESULT_PARSE,
        input_artifact_ids=(),
        status=AnalysisStatus.COMPLETED,
        tool="test.v11.che-lineage-source",
        tool_version="1",
        parameters_hash=canonical_sha256(result.result_hash),
    )
    source_body = (canonical_json({"result_hash": result.result_hash}) + "\n").encode()
    source_artifact = _artifact(
        root=root,
        relative=Path("che-lineage-sources") / str(source_analysis.id) / "parsed-result.json",
        body=source_body,
        artifact_type=ArtifactType.PARSED_RESULT,
        producer=AnalysisProducerRef(source_analysis.id),
    )
    if reference is None:
        tool = HARMONIC_THERMOCHEMISTRY_TOOL_NAME
        tool_version = HARMONIC_THERMOCHEMISTRY_TOOL_VERSION
        format_name = CANONICAL_HARMONIC_THERMOCHEMISTRY_FORMAT
        version = CANONICAL_HARMONIC_THERMOCHEMISTRY_VERSION
        filename = "canonical-harmonic-thermochemistry.json"
        receipt: dict[str, object] = {
            "source_analysis_id": source_analysis.id,
            "source_artifact_id": source_artifact.id,
            "source_artifact_sha256": source_artifact.sha256,
            "thermochemistry_parameters_hash": result.identity.parameters_hash,
        }
    else:
        tool = IDEAL_GAS_THERMOCHEMISTRY_TOOL_NAME
        tool_version = IDEAL_GAS_THERMOCHEMISTRY_TOOL_VERSION
        format_name = CANONICAL_IDEAL_GAS_THERMOCHEMISTRY_FORMAT
        version = CANONICAL_IDEAL_GAS_THERMOCHEMISTRY_VERSION
        filename = "canonical-ideal-gas-thermochemistry.json"
        receipt = {
            "reference": reference,
            "reference_content_hash": reference.content_hash,
            "source_analysis_id": source_analysis.id,
            "source_artifact_id": source_artifact.id,
            "source_artifact_sha256": source_artifact.sha256,
            "thermochemistry_parameters_hash": result.identity.parameters_hash,
        }
    receipt_hash = canonical_sha256(receipt)
    analysis = Analysis(
        project_id=project.id,
        analysis_type=AnalysisType.THERMOCHEMISTRY,
        input_artifact_ids=(source_artifact.id,),
        status=AnalysisStatus.COMPLETED,
        tool=tool,
        tool_version=tool_version,
        parameters_hash=receipt_hash,
    )
    payload = {
        "format": format_name,
        "version": version,
        "analysis_id": analysis.id,
        "source_receipt": receipt,
        "source_receipt_hash": receipt_hash,
        "result_hash": result.result_hash,
        "result": result,
    }
    body = (canonical_json(payload) + "\n").encode()
    artifact = _artifact(
        root=root,
        relative=Path("analyses") / str(analysis.id) / filename,
        body=body,
        artifact_type=ArtifactType.DERIVED_DATASET,
        producer=AnalysisProducerRef(analysis.id),
    )
    provenance = tuple(
        ProvenanceRecord(
            subject_id=subject_id,
            tool=subject_tool,
            tool_version=subject_version,
            parameters_hash=parameters_hash,
        )
        for subject_id, subject_tool, subject_version, parameters_hash in (
            (
                source_analysis.id,
                source_analysis.tool,
                source_analysis.tool_version,
                source_analysis.parameters_hash,
            ),
            (
                source_artifact.id,
                source_analysis.tool,
                source_analysis.tool_version,
                source_artifact.sha256,
            ),
            (analysis.id, analysis.tool, analysis.tool_version, analysis.parameters_hash),
            (artifact.id, analysis.tool, analysis.tool_version, artifact.sha256),
        )
    )
    dependencies = (
        DependencyRecord(
            upstream_id=source_analysis.id,
            downstream_id=source_artifact.id,
            kind=DependencyKind.SCIENTIFIC,
            role="parsed_result",
            recorded_hash=scientific_hash(source_analysis),
        ),
        DependencyRecord(
            upstream_id=source_artifact.id,
            downstream_id=analysis.id,
            kind=DependencyKind.SCIENTIFIC,
            role="parsed_result",
            recorded_hash=scientific_hash(source_artifact),
        ),
        DependencyRecord(
            upstream_id=analysis.id,
            downstream_id=artifact.id,
            kind=DependencyKind.SCIENTIFIC,
            role="thermochemistry",
            recorded_hash=scientific_hash(analysis),
        ),
    )
    return _Fixture(
        source_analysis=source_analysis,
        source_artifact=source_artifact,
        analysis=analysis,
        artifact=artifact,
        provenance=provenance,
        dependencies=dependencies,
    )


def _conditions(potential_v: float) -> CHEConditions:
    return CHEConditions(
        temperature_k=298.15,
        potential_v=potential_v,
        ph=0.0,
        potential_reference=ElectrodePotentialReference.SHE,
        ph_semantics=CHEPhSemantics.EXPLICIT_ACTIVITY,
    )


def test_orr_diagram_tracks_durable_h2_reference_used_by_che(tmp_path: Path) -> None:
    project = Project(name="Block 7 CHE lineage", slug="block7-che-lineage")
    clean = _thermo(tmp_path, project, _surface_result(ThermochemistrySubjectKind.SURFACE, -10.0))
    ooh = _thermo(tmp_path, project, _surface_result(ThermochemistrySubjectKind.ADSORBATE, -20.0))
    oxygen = _thermo(tmp_path, project, _surface_result(ThermochemistrySubjectKind.ADSORBATE, -15.0))
    hydroxyl = _thermo(tmp_path, project, _surface_result(ThermochemistrySubjectKind.ADSORBATE, -12.0))
    o2 = _thermo(
        tmp_path,
        project,
        _gas_result(GasReferenceSpecies.O2, -9.0),
        reference=GasReferenceDefinition(GasReferenceSpecies.O2),
    )
    h2o = _thermo(
        tmp_path,
        project,
        _gas_result(GasReferenceSpecies.H2O, -14.0),
        reference=GasReferenceDefinition(GasReferenceSpecies.H2O),
    )
    h2 = _thermo(
        tmp_path,
        project,
        _gas_result(GasReferenceSpecies.H2, -6.0),
        reference=GasReferenceDefinition(GasReferenceSpecies.H2),
    )
    fixtures = (clean, ooh, oxygen, hydroxyl, o2, h2o, h2)
    store = ProjectStore(tmp_path)
    store.save(
        ProjectBundle(
            project=project,
            analyses=tuple(
                item
                for fixture in fixtures
                for item in (fixture.source_analysis, fixture.analysis)
            ),
            artifacts=tuple(
                item
                for fixture in fixtures
                for item in (fixture.source_artifact, fixture.artifact)
            ),
            provenance_records=tuple(
                item for fixture in fixtures for item in fixture.provenance
            ),
            dependency_records=tuple(
                item for fixture in fixtures for item in fixture.dependencies
            ),
        )
    )
    service = ProjectReactionWorkspaceApplicationService(store)
    receipt = service.materialize_preset_diagram(
        preset_kind=ElectrocatalysisPresetKind.ORR_ASSOCIATIVE_4E,
        bindings=ORRPresetAnalysisBindings(
            clean_surface_analysis_id=clean.analysis.id,
            ooh_adsorbed_analysis_id=ooh.analysis.id,
            o_adsorbed_analysis_id=oxygen.analysis.id,
            oh_adsorbed_analysis_id=hydroxyl.analysis.id,
            o2_reference_analysis_id=o2.analysis.id,
            h2o_reference_analysis_id=h2o.analysis.id,
            h2_reference_analysis_id=h2.analysis.id,
        ),
        baseline_conditions=_conditions(0.0),
        requested_conditions=_conditions(0.8),
    )

    bundle = store.open()
    diagram = next(
        item for item in bundle.analyses if str(item.id) == receipt["analysis_id"]
    )
    assert h2.artifact.id in diagram.input_artifact_ids
    assert any(
        item.kind is DependencyKind.SCIENTIFIC
        and item.upstream_id == h2.artifact.id
        and item.downstream_id == diagram.id
        and item.role == "reaction_che_reference_artifact:CHE_H2"
        for item in bundle.dependency_records
    )
    assert any(
        item.kind is DependencyKind.SCIENTIFIC
        and item.upstream_id == h2.analysis.id
        and item.downstream_id == diagram.id
        and item.role == "reaction_che_reference_analysis:CHE_H2"
        for item in bundle.dependency_records
    )

    observations, invalid = observed_artifact_state(tmp_path, bundle)
    before = thermochemistry_projection(
        store=store,
        bundle=bundle,
        analysis=diagram,
        current_hash_overrides=observations,
        invalid_ids=invalid,
    )
    assert before.scientific_state is ThermochemistryAnalysisScientificState.COMPLETED
    assert before.readiness is WorkflowStepReadiness.SATISFIED

    h2_path = tmp_path / h2.artifact.local_path
    h2_path.write_bytes(h2_path.read_bytes() + b"drift")
    observations, invalid = observed_artifact_state(tmp_path, bundle)
    after = thermochemistry_projection(
        store=store,
        bundle=bundle,
        analysis=diagram,
        current_hash_overrides=observations,
        invalid_ids=invalid,
    )
    assert after.scientific_state is ThermochemistryAnalysisScientificState.STALE
    assert after.readiness is WorkflowStepReadiness.BLOCKED
