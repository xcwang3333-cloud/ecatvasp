from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from ecatvasp.domain import (
    Analysis,
    AnalysisProducerRef,
    AnalysisStatus,
    AnalysisType,
    Artifact,
    ArtifactAvailability,
    ArtifactType,
    Calculation,
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
    StructureSite,
    StructureSnapshot,
    canonical_json,
    canonical_sha256,
    new_artifact_id,
    new_atom_uid,
)
from ecatvasp.domain.ids import new_analysis_id
from ecatvasp.provenance import (
    DependencyKind,
    DependencyRecord,
    ProvenanceRecord,
    scientific_hash,
)
from ecatvasp.schema.version import SCHEMA_VERSION
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.thermo import (
    BoundGasReferenceThermochemistry,
    CHEConditions,
    CHEHydrogenReference,
    CHEPhSemantics,
    CHEReactionSource,
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
    ModeExclusion,
    ModeExclusionReason,
    MolecularReferenceReactionSource,
    ReactionDiagramDescriptorDefinition,
    ReactionDiagramDescriptorKind,
    ReactionDiagramDescriptorUnit,
    ReactionDiagramError,
    ReactionDiagramSourceReceipt,
    ReactionEnergySource,
    ReactionEnergySourceKind,
    ReactionSourceArtifactBinding,
    ThermochemicalConditions,
    ThermochemicalStandardState,
    ThermochemistryComponents,
    ThermochemistryIdentity,
    ThermochemistryModeSelection,
    ThermochemistryReactionSource,
    ThermochemistryResult,
    ThermochemistrySubjectKind,
    VibrationalModePolicy,
    compile_co2rr_to_co_2e_preset,
    compile_her_volmer_heyrovsky_preset,
    compile_oer_associative_4e_preset,
    compile_orr_associative_4e_preset,
    define_her_delta_g_h_star_descriptor,
    define_limiting_potential_descriptor,
    define_reversible_potential_descriptor,
    derive_reversible_potential,
    evaluate_her_delta_g_h_star,
    evaluate_potential_dependent_pathway_view,
    evaluate_reaction_pathway,
    materialize_harmonic_thermochemistry,
    materialize_ideal_gas_thermochemistry,
    materialize_reaction_diagram,
    proton_electron_chemical_potential,
    solve_limiting_potential,
)
from ecatvasp.vasp.results import (
    VASP_RESULT_DOCUMENT_FORMAT,
    VASP_RESULT_DOCUMENT_VERSION,
    VaspEnergySummary,
    VaspFrequencyDataset,
    VaspFrequencyEigenvector,
    VaspFrequencyMode,
    VaspFrequencyModeKind,
    VaspResultDocument,
    VaspResultSource,
    VaspResultSourceRole,
)
from ecatvasp.workflow import (
    THERMOCHEMISTRY_ANALYSIS_TYPES,
    ThermochemistryAnalysisRequirement,
    ThermochemistryAnalysisScientificState,
    ThermochemistryReconciliationError,
    WorkflowStepReadiness,
    reconcile_thermochemistry_analyses_from_store,
)


@dataclass(frozen=True, slots=True)
class _ParsedFrequencySource:
    calculation: Calculation
    attempt: ExecutionAttempt
    raw_artifact: Artifact
    parse_analysis: Analysis
    parsed_artifact: Artifact
    result: VaspResultDocument
    provenance_records: tuple[ProvenanceRecord, ...]
    dependency_records: tuple[DependencyRecord, ...]


def _method(*, recipe_id: str, elements: tuple[str, ...]) -> MethodFingerprint:
    return MethodFingerprint(
        method=MethodDefinition(
            xc_functional="PBE",
            potcar_family="PBE_54",
            potcars=tuple(
                PotcarIdentity(element, element, element.lower().encode().hex().ljust(64, "0")[:64])
                for element in elements
            ),
            engine_version="6.5.1",
        ),
        protocol=ProtocolDefinition(
            encut_ev=450.0,
            kpoints=KPointPolicy(KPointPolicyKind.GAMMA_ONLY),
        ),
        recipe=RecipeIdentity(recipe_id),
    )


def _surface_snapshot(*, with_hydrogen: bool) -> StructureSnapshot:
    carbon_uid = new_atom_uid()
    sites = [StructureSite(carbon_uid, "C", (0.5, 0.5, 0.5))]
    if with_hydrogen:
        sites.append(StructureSite(new_atom_uid(), "H", (0.5, 0.5, 0.58)))
    return StructureSnapshot(
        lattice=Lattice(((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 18.0))),
        sites=tuple(sites),
        periodic=(True, True, False),
    )


def _h2_snapshot() -> StructureSnapshot:
    return StructureSnapshot(
        lattice=Lattice(((20.0, 0.0, 0.0), (0.0, 20.0, 0.0), (0.0, 0.0, 20.0))),
        sites=(
            StructureSite(new_atom_uid(), "H", (0.48, 0.5, 0.5)),
            StructureSite(new_atom_uid(), "H", (0.52, 0.5, 0.5)),
        ),
        periodic=(True, True, True),
    )


def _mode(
    index: int,
    *,
    atom_uids: tuple[object, ...],
    wavenumber_cm_inverse: float,
) -> VaspFrequencyMode:
    return VaspFrequencyMode(
        mode_index=index,
        kind=VaspFrequencyModeKind.REAL,
        frequency_thz=max(wavenumber_cm_inverse / 33.3564095, 0.001),
        angular_frequency_2pi_thz=max(wavenumber_cm_inverse / 5.309, 0.001),
        wavenumber_cm_inverse=wavenumber_cm_inverse,
        energy_mev=max(wavenumber_cm_inverse * 0.1239841984, 0.001),
        eigenvectors=tuple(
            VaspFrequencyEigenvector(
                atom_uid=atom_uid,
                components=(0.01 * index, 0.0, 0.0),
            )
            for atom_uid in atom_uids
        ),
    )


def _parsed_frequency_source(
    *,
    root: Path,
    project: Project,
    snapshot: StructureSnapshot,
    method: MethodFingerprint,
    calculation_type: CalculationType,
    energy_ev: float,
    wavenumbers: tuple[float, ...],
) -> _ParsedFrequencySource:
    calculation = Calculation(
        project_id=project.id,
        calculation_type=calculation_type,
        input_structure_snapshot_id=snapshot.id,
        recipe_id=method.recipe.recipe_id,
        method_fingerprint_id=method.id,
        status=CalculationScientificStatus.CONVERGED,
    )
    attempt = ExecutionAttempt(
        calculation_id=calculation.id,
        attempt_number=1,
        status=ExecutionAttemptStatus.PARSED,
    )
    raw_relative = Path("calculations") / str(calculation.id) / "attempt-1" / "OUTCAR"
    raw_body = f"synthetic exact OUTCAR for {calculation.id}\n".encode()
    raw_path = root / raw_relative
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(raw_body)
    raw_hash = hashlib.sha256(raw_body).hexdigest()
    raw_artifact = Artifact(
        artifact_type=ArtifactType.OUTCAR,
        producer=ExecutionAttemptProducerRef(attempt.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=raw_relative.as_posix(),
        size_bytes=len(raw_body),
        sha256=raw_hash,
    )
    atom_uids = tuple(site.atom_uid for site in snapshot.sites)
    result = VaspResultDocument(
        calculation_type=calculation_type,
        sources=(
            VaspResultSource(
                role=VaspResultSourceRole.OUTCAR,
                artifact_id=raw_artifact.id,
                artifact_type=ArtifactType.OUTCAR,
                sha256=raw_hash,
            ),
        ),
        energies=VaspEnergySummary(
            free_energy_toten_ev=energy_ev + 0.2,
            energy_without_entropy_ev=energy_ev + 0.1,
            energy_sigma0_ev=energy_ev,
        ),
        frequencies=VaspFrequencyDataset(
            atom_uids=atom_uids,
            displaced_atom_uids=atom_uids,
            modes=tuple(
                _mode(
                    index,
                    atom_uids=atom_uids,
                    wavenumber_cm_inverse=wavenumber,
                )
                for index, wavenumber in enumerate(wavenumbers, start=1)
            ),
        ),
    )
    intake_hash = canonical_sha256(
        {
            "calculation_id": calculation.id,
            "raw_artifact_id": raw_artifact.id,
            "raw_sha256": raw_hash,
            "result": result,
        }
    )
    parse_analysis = Analysis(
        project_id=project.id,
        analysis_type=AnalysisType.RESULT_PARSE,
        input_artifact_ids=(raw_artifact.id,),
        status=AnalysisStatus.COMPLETED,
        tool="ecatvasp.vasp.scientific-result-pipeline",
        tool_version="1",
        parameters_hash=intake_hash,
    )
    parsed_relative = Path("calculations") / str(calculation.id) / "scientific" / "parsed.json"
    parsed_payload = {
        "format": VASP_RESULT_DOCUMENT_FORMAT,
        "version": VASP_RESULT_DOCUMENT_VERSION,
        "calculation_id": calculation.id,
        "analysis_id": parse_analysis.id,
        "intake_hash": intake_hash,
        "result": result,
    }
    parsed_body = (canonical_json(parsed_payload) + "\n").encode()
    parsed_path = root / parsed_relative
    parsed_path.parent.mkdir(parents=True, exist_ok=True)
    parsed_path.write_bytes(parsed_body)
    parsed_artifact = Artifact(
        artifact_type=ArtifactType.PARSED_RESULT,
        producer=AnalysisProducerRef(parse_analysis.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=parsed_relative.as_posix(),
        size_bytes=len(parsed_body),
        sha256=hashlib.sha256(parsed_body).hexdigest(),
    )
    provenance = (
        ProvenanceRecord(
            subject_id=parse_analysis.id,
            tool=parse_analysis.tool,
            tool_version=parse_analysis.tool_version,
            parameters_hash=parse_analysis.parameters_hash,
            method_fingerprint_id=method.id,
        ),
        ProvenanceRecord(
            subject_id=parsed_artifact.id,
            tool=parse_analysis.tool,
            tool_version=parse_analysis.tool_version,
            parameters_hash=parsed_artifact.sha256,
            method_fingerprint_id=method.id,
        ),
    )
    dependencies = (
        DependencyRecord(
            upstream_id=raw_artifact.id,
            downstream_id=parse_analysis.id,
            kind=DependencyKind.SCIENTIFIC,
            role="outcar_source",
            recorded_hash=scientific_hash(raw_artifact),
        ),
        DependencyRecord(
            upstream_id=parse_analysis.id,
            downstream_id=parsed_artifact.id,
            kind=DependencyKind.SCIENTIFIC,
            role="parsed_vasp_result",
            recorded_hash=scientific_hash(parse_analysis),
        ),
    )
    return _ParsedFrequencySource(
        calculation=calculation,
        attempt=attempt,
        raw_artifact=raw_artifact,
        parse_analysis=parse_analysis,
        parsed_artifact=parsed_artifact,
        result=result,
        provenance_records=provenance,
        dependency_records=dependencies,
    )


def _surface_identity(subject_kind: ThermochemistrySubjectKind) -> ThermochemistryIdentity:
    return ThermochemistryIdentity(
        subject_kind=subject_kind,
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
    )


def _h2_identity(snapshot: StructureSnapshot) -> ThermochemistryIdentity:
    atom_uids = tuple(site.atom_uid for site in snapshot.sites)
    return ThermochemistryIdentity(
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
            low_frequency_policy=LowFrequencyPolicy.EXCLUDE_EXPLICIT,
            exclusions=(
                ModeExclusion(1, ModeExclusionReason.TRANSLATIONAL),
                ModeExclusion(2, ModeExclusionReason.TRANSLATIONAL),
                ModeExclusion(3, ModeExclusionReason.TRANSLATIONAL),
                ModeExclusion(4, ModeExclusionReason.ROTATIONAL),
                ModeExclusion(5, ModeExclusionReason.ROTATIONAL),
            ),
        ),
        gas_model=GasMoleculeModel(
            geometry_kind=GasGeometryKind.LINEAR,
            symmetry_number=2,
            spin_multiplicity=1,
            atomic_masses=tuple(
                GasAtomicMass(atom_uid, 1.00784, "1H") for atom_uid in atom_uids
            ),
        ),
    )


def _baseline_conditions() -> CHEConditions:
    return CHEConditions(
        temperature_k=298.15,
        potential_v=0.0,
        ph=0.0,
        potential_reference=ElectrodePotentialReference.SHE,
        ph_semantics=CHEPhSemantics.EXPLICIT_ACTIVITY,
    )


def test_v08_final_e2e_reopen_requested_conditions_and_raw_source_drift(
    tmp_path: Path,
) -> None:
    project = Project(name="v0.8 final acceptance", slug="v08-final-acceptance")
    clean_snapshot = _surface_snapshot(with_hydrogen=False)
    ads_snapshot = _surface_snapshot(with_hydrogen=True)
    h2_snapshot = _h2_snapshot()
    surface_method = _method(recipe_id="WXC.VASP.AdsorbateFrequency", elements=("C", "H"))
    gas_method = _method(recipe_id="WXC.VASP.GasFrequency", elements=("H",))

    clean_source = _parsed_frequency_source(
        root=tmp_path,
        project=project,
        snapshot=clean_snapshot,
        method=surface_method,
        calculation_type=CalculationType.FREQUENCY,
        energy_ev=-10.0,
        wavenumbers=(500.0,),
    )
    ads_source = _parsed_frequency_source(
        root=tmp_path,
        project=project,
        snapshot=ads_snapshot,
        method=surface_method,
        calculation_type=CalculationType.FREQUENCY,
        energy_ev=-13.0,
        wavenumbers=(500.0, 1000.0),
    )
    h2_source = _parsed_frequency_source(
        root=tmp_path,
        project=project,
        snapshot=h2_snapshot,
        method=gas_method,
        calculation_type=CalculationType.GAS_FREQUENCY,
        energy_ev=-6.0,
        wavenumbers=(6.0, 7.0, 8.0, 9.0, 10.0, 4400.0),
    )

    clean_thermo = materialize_harmonic_thermochemistry(
        project_root=tmp_path,
        calculation=clean_source.calculation,
        method_fingerprint=surface_method,
        structure_snapshot=clean_snapshot,
        source_analysis=clean_source.parse_analysis,
        source_artifact=clean_source.parsed_artifact,
        source_result=clean_source.result,
        identity=_surface_identity(ThermochemistrySubjectKind.SURFACE),
    )
    ads_thermo = materialize_harmonic_thermochemistry(
        project_root=tmp_path,
        calculation=ads_source.calculation,
        method_fingerprint=surface_method,
        structure_snapshot=ads_snapshot,
        source_analysis=ads_source.parse_analysis,
        source_artifact=ads_source.parsed_artifact,
        source_result=ads_source.result,
        identity=_surface_identity(ThermochemistrySubjectKind.ADSORBATE),
    )
    h2_thermo = materialize_ideal_gas_thermochemistry(
        project_root=tmp_path,
        reference=GasReferenceDefinition(GasReferenceSpecies.H2),
        calculation=h2_source.calculation,
        method_fingerprint=gas_method,
        structure_snapshot=h2_snapshot,
        source_analysis=h2_source.parse_analysis,
        source_artifact=h2_source.parsed_artifact,
        source_result=h2_source.result,
        identity=_h2_identity(h2_snapshot),
    )
    bound_h2 = BoundGasReferenceThermochemistry(
        reference=h2_thermo.reference,
        result=h2_thermo.result,
    )
    baseline_conditions = _baseline_conditions()
    che = proton_electron_chemical_potential(
        hydrogen_reference=CHEHydrogenReference(raw=bound_h2),
        conditions=baseline_conditions,
    )
    preset = compile_her_volmer_heyrovsky_preset(
        pathway_key="final-her",
        clean_surface_key="star",
        h_adsorbed_key="H_star",
        h2_reference_key="H2",
        che_key="H_plus_e",
    )
    clean_reaction = ThermochemistryReactionSource("star", clean_thermo.result)
    ads_reaction = ThermochemistryReactionSource("H_star", ads_thermo.result)
    h2_reaction = MolecularReferenceReactionSource("H2", raw=bound_h2)
    sources: tuple[ReactionEnergySource, ...] = (
        clean_reaction,
        ads_reaction,
        h2_reaction,
        CHEReactionSource("H_plus_e", che),
    )
    baseline = evaluate_reaction_pathway(definition=preset.definition, sources=sources)
    limiting = solve_limiting_potential(
        baseline_pathway=baseline,
        baseline_conditions=baseline_conditions,
    )
    reversible = derive_reversible_potential(
        baseline_pathway=baseline,
        baseline_conditions=baseline_conditions,
    )
    h_adsorption = evaluate_her_delta_g_h_star(
        step_key="her_h_adsorption",
        label="H* adsorption",
        initial_state_key="clean",
        final_state_key="H_star",
        hydrogen_adsorbed_source=ads_reaction,
        clean_surface_source=clean_reaction,
        h2_reference=h2_reaction,
    )
    requested_conditions = CHEConditions(
        temperature_k=298.15,
        potential_v=-0.25,
        ph=7.0,
        potential_reference=ElectrodePotentialReference.SHE,
        ph_semantics=CHEPhSemantics.EXPLICIT_ACTIVITY,
    )
    expected_view = evaluate_potential_dependent_pathway_view(
        baseline_pathway=baseline,
        baseline_conditions=baseline_conditions,
        target_conditions=requested_conditions,
    )
    diagram = materialize_reaction_diagram(
        project_root=tmp_path,
        definition=preset.definition,
        sources=sources,
        source_bindings=(
            ReactionSourceArtifactBinding("star", clean_thermo.analysis, clean_thermo.artifact),
            ReactionSourceArtifactBinding("H_star", ads_thermo.analysis, ads_thermo.artifact),
            ReactionSourceArtifactBinding("H2", h2_thermo.analysis, h2_thermo.artifact),
        ),
        baseline_conditions=baseline_conditions,
        requested_conditions=requested_conditions,
        descriptor_definitions=(
            define_limiting_potential_descriptor(key="u_lim", result=limiting),
            define_reversible_potential_descriptor(key="u_rev", result=reversible),
            define_her_delta_g_h_star_descriptor(key="delta_g_h_star", result=h_adsorption),
        ),
    )
    assert diagram.dataset.potential_view == expected_view
    assert diagram.dataset.baseline_pathway_result_hash == baseline.result_hash
    assert diagram.dataset.requested_conditions == requested_conditions
    assert {item.key for item in diagram.dataset.descriptor_definitions} == {
        "delta_g_h_star",
        "u_lim",
        "u_rev",
    }

    parsed_sources = (clean_source, ads_source, h2_source)
    bundle = ProjectBundle(
        project=project,
        structure_snapshots=(clean_snapshot, ads_snapshot, h2_snapshot),
        method_fingerprints=(surface_method, gas_method),
        calculations=tuple(item.calculation for item in parsed_sources),
        execution_attempts=tuple(item.attempt for item in parsed_sources),
        artifacts=(
            *(item.raw_artifact for item in parsed_sources),
            *(item.parsed_artifact for item in parsed_sources),
            clean_thermo.artifact,
            ads_thermo.artifact,
            h2_thermo.artifact,
            diagram.artifact,
        ),
        analyses=(
            *(item.parse_analysis for item in parsed_sources),
            clean_thermo.analysis,
            ads_thermo.analysis,
            h2_thermo.analysis,
            diagram.analysis,
        ),
        provenance_records=(
            *(record for item in parsed_sources for record in item.provenance_records),
            *clean_thermo.provenance_records,
            *ads_thermo.provenance_records,
            *h2_thermo.provenance_records,
            *diagram.provenance_records,
        ),
        dependency_records=(
            *(record for item in parsed_sources for record in item.dependency_records),
            *clean_thermo.dependency_records,
            *ads_thermo.dependency_records,
            *h2_thermo.dependency_records,
            *diagram.dependency_records,
        ),
    )
    bundle.validate()
    store = ProjectStore(tmp_path)
    store.save(bundle)
    assert store.open() == bundle

    thermo_items = (
        ("clean", clean_thermo.analysis),
        ("ads", ads_thermo.analysis),
        ("h2", h2_thermo.analysis),
        ("diagram", diagram.analysis),
    )
    requirements = tuple(
        ThermochemistryAnalysisRequirement(
            key=key,
            project_id=project.id,
            analysis_type=analysis.analysis_type,
            input_artifact_ids=analysis.input_artifact_ids,
            parameters_hash=analysis.parameters_hash or "",
        )
        for key, analysis in thermo_items
    )
    first = reconcile_thermochemistry_analyses_from_store(
        store=store,
        requirements=requirements,
    )
    second = reconcile_thermochemistry_analyses_from_store(
        store=store,
        requirements=requirements,
    )
    assert first == second
    assert first.report_hash == second.report_hash
    for key in ("clean", "ads", "h2", "diagram"):
        projection = first.requirement(key)
        assert projection.scientific_state is ThermochemistryAnalysisScientificState.COMPLETED
        assert projection.readiness is WorkflowStepReadiness.SATISFIED

    raw_drift = reconcile_thermochemistry_analyses_from_store(
        store=store,
        requirements=requirements,
        current_hash_overrides={ads_source.raw_artifact.id: "0" * 64},
    )
    assert (
        raw_drift.requirement("ads").scientific_state
        is ThermochemistryAnalysisScientificState.STALE
    )
    assert (
        raw_drift.requirement("diagram").scientific_state
        is ThermochemistryAnalysisScientificState.STALE
    )
    assert raw_drift.requirement("diagram").readiness is WorkflowStepReadiness.BLOCKED

    policy_drift = reconcile_thermochemistry_analyses_from_store(
        store=store,
        requirements=requirements,
        current_hash_overrides={ads_thermo.analysis.id: "f" * 64},
    )
    assert (
        policy_drift.requirement("diagram").scientific_state
        is ThermochemistryAnalysisScientificState.STALE
    )
    assert policy_drift.report_hash != first.report_hash


def _synthetic_surface_source(
    species_key: str,
    energy_ev: float,
    *,
    adsorbate: bool,
) -> ThermochemistryReactionSource:
    policy = None
    selection = None
    subject = ThermochemistrySubjectKind.SURFACE
    if adsorbate:
        subject = ThermochemistrySubjectKind.ADSORBATE
        policy = VibrationalModePolicy(
            frequency_cutoff_cm_inverse=50.0,
            imaginary_mode_policy=ImaginaryModePolicy.REJECT_ANY,
            low_frequency_policy=LowFrequencyPolicy.REJECT_BELOW_CUTOFF,
        )
        selection = ThermochemistryModeSelection(accepted_mode_indices=(1,))
    result = ThermochemistryResult(
        identity=ThermochemistryIdentity(
            subject_kind=subject,
            conditions=ThermochemicalConditions(
                temperature_k=298.15,
                standard_state=ThermochemicalStandardState.SURFACE_FIXED_CELL,
            ),
            electronic_energy_kind=ElectronicEnergyKind.SIGMA_ZERO,
            electronic_entropy_policy=ElectronicEntropyPolicy.NEGLECTED,
            vibrational_policy=policy,
        ),
        components=ThermochemistryComponents(electronic_energy_ev=energy_ev),
        mode_selection=selection,
    )
    return ThermochemistryReactionSource(species_key, result)


def _synthetic_molecular_source(
    species_key: str,
    species: GasReferenceSpecies,
    energy_ev: float,
) -> MolecularReferenceReactionSource:
    elements_by_species = {
        GasReferenceSpecies.H2: ("H", "H"),
        GasReferenceSpecies.H2O: ("H", "H", "O"),
        GasReferenceSpecies.O2: ("O", "O"),
        GasReferenceSpecies.CO: ("C", "O"),
        GasReferenceSpecies.CO2: ("C", "O", "O"),
    }
    masses = {"H": 1.00784, "C": 12.011, "O": 15.999}
    elements = elements_by_species[species]
    atom_uids = tuple(new_atom_uid() for _ in elements)
    geometry = GasGeometryKind.NONLINEAR if species is GasReferenceSpecies.H2O else GasGeometryKind.LINEAR
    symmetry = 2 if species in {GasReferenceSpecies.H2, GasReferenceSpecies.H2O, GasReferenceSpecies.O2, GasReferenceSpecies.CO2} else 1
    multiplicity = 3 if species is GasReferenceSpecies.O2 else 1
    result = ThermochemistryResult(
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
                    GasAtomicMass(atom_uid, masses[element])
                    for atom_uid, element in zip(atom_uids, elements, strict=True)
                ),
            ),
        ),
        components=ThermochemistryComponents(electronic_energy_ev=energy_ev),
        mode_selection=ThermochemistryModeSelection(accepted_mode_indices=(1,)),
    )
    return MolecularReferenceReactionSource(
        species_key,
        raw=BoundGasReferenceThermochemistry(
            reference=GasReferenceDefinition(species),
            result=result,
        ),
    )


def test_all_representative_electrocatalysis_presets_share_generic_evaluator() -> None:
    conditions = _baseline_conditions()
    h2 = _synthetic_molecular_source("H2", GasReferenceSpecies.H2, -6.0)
    che = CHEReactionSource(
        "H_plus_e",
        proton_electron_chemical_potential(
            hydrogen_reference=CHEHydrogenReference(raw=h2.raw),
            conditions=conditions,
        ),
    )
    surface = _synthetic_surface_source("star", -10.0, adsorbate=False)
    h_star = _synthetic_surface_source("H_star", -13.0, adsorbate=True)
    ooh = _synthetic_surface_source("OOH_star", -15.0, adsorbate=True)
    o = _synthetic_surface_source("O_star", -14.0, adsorbate=True)
    oh = _synthetic_surface_source("OH_star", -13.5, adsorbate=True)
    cooh = _synthetic_surface_source("COOH_star", -15.2, adsorbate=True)
    co_star = _synthetic_surface_source("CO_star", -12.8, adsorbate=True)
    h2o = _synthetic_molecular_source("H2O", GasReferenceSpecies.H2O, -14.0)
    o2 = _synthetic_molecular_source("O2", GasReferenceSpecies.O2, -9.0)
    co2 = _synthetic_molecular_source("CO2", GasReferenceSpecies.CO2, -18.0)
    co = _synthetic_molecular_source("CO_gas", GasReferenceSpecies.CO, -12.0)

    cases = (
        (
            compile_her_volmer_heyrovsky_preset(
                pathway_key="her",
                clean_surface_key="star",
                h_adsorbed_key="H_star",
                h2_reference_key="H2",
                che_key="H_plus_e",
            ),
            (surface, h_star, h2, che),
        ),
        (
            compile_orr_associative_4e_preset(
                pathway_key="orr",
                clean_surface_key="star",
                ooh_adsorbed_key="OOH_star",
                o_adsorbed_key="O_star",
                oh_adsorbed_key="OH_star",
                o2_reference_key="O2",
                h2o_reference_key="H2O",
                che_key="H_plus_e",
            ),
            (surface, ooh, o, oh, o2, h2o, che),
        ),
        (
            compile_oer_associative_4e_preset(
                pathway_key="oer",
                clean_surface_key="star",
                oh_adsorbed_key="OH_star",
                o_adsorbed_key="O_star",
                ooh_adsorbed_key="OOH_star",
                h2o_reference_key="H2O",
                o2_reference_key="O2",
                che_key="H_plus_e",
            ),
            (surface, oh, o, ooh, h2o, o2, che),
        ),
        (
            compile_co2rr_to_co_2e_preset(
                pathway_key="co2-to-co",
                clean_surface_key="star",
                cooh_adsorbed_key="COOH_star",
                co_adsorbed_key="CO_star",
                co2_reference_key="CO2",
                h2o_reference_key="H2O",
                co_reference_key="CO_gas",
                che_key="H_plus_e",
            ),
            (surface, cooh, co_star, co2, h2o, co, che),
        ),
    )
    for preset, sources in cases:
        result = evaluate_reaction_pathway(
            definition=preset.definition,
            sources=cast(tuple[ReactionEnergySource, ...], sources),
        )
        assert result.pathway_hash == preset.definition.content_hash
        assert len(result.step_results) == len(preset.definition.steps)
        assert result.state_keys == preset.definition.state_keys


def test_v08_final_runtime_public_enums_fail_closed() -> None:
    with pytest.raises(ReactionDiagramError, match="descriptor kind"):
        ReactionDiagramDescriptorDefinition(
            key="u_lim",
            kind=cast(ReactionDiagramDescriptorKind, "limiting_potential"),
            value=-0.4,
            unit=ReactionDiagramDescriptorUnit.VOLT,
            source_result_hash="1" * 64,
            pathway_hash="2" * 64,
            baseline_result_hash="3" * 64,
        )
    with pytest.raises(ReactionDiagramError, match="descriptor unit"):
        ReactionDiagramDescriptorDefinition(
            key="delta_g_h",
            kind=ReactionDiagramDescriptorKind.HER_DELTA_G_H_STAR,
            value=0.0,
            unit=cast(ReactionDiagramDescriptorUnit, "eV"),
            source_result_hash="1" * 64,
        )
    with pytest.raises(ReactionDiagramError, match="source_kind"):
        ReactionDiagramSourceReceipt(
            species_key="star",
            source_kind=cast(ReactionEnergySourceKind, "thermochemistry"),
            source_hash="1" * 64,
            analysis_id=new_analysis_id(),
            artifact_id=new_artifact_id(),
            artifact_sha256="2" * 64,
            artifact_result_hash="3" * 64,
        )
    with pytest.raises(ThermochemistryReconciliationError, match="analysis_type"):
        ThermochemistryAnalysisRequirement(
            key="diagram",
            project_id=Project(name="runtime", slug="runtime").id,
            analysis_type=cast(AnalysisType, "reaction_diagram"),
            input_artifact_ids=(new_artifact_id(),),
            parameters_hash="4" * 64,
        )


def test_v08_final_scope_lock_keeps_schema_and_analysis_surface_frozen() -> None:
    assert SCHEMA_VERSION == 3
    assert THERMOCHEMISTRY_ANALYSIS_TYPES == frozenset(
        {AnalysisType.THERMOCHEMISTRY, AnalysisType.REACTION_DIAGRAM}
    )
