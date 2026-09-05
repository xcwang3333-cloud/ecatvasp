from __future__ import annotations

import hashlib
from pathlib import Path

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
    new_artifact_id,
    new_atom_uid,
)
from ecatvasp.thermo import (
    ElectronicEnergyKind,
    ElectronicEntropyPolicy,
    ImaginaryModePolicy,
    LowFrequencyPolicy,
    ModeExclusion,
    ModeExclusionReason,
    ThermochemicalConditions,
    ThermochemicalStandardState,
    ThermochemistryIdentity,
    ThermochemistrySubjectKind,
    VibrationalModePolicy,
)
from ecatvasp.thermo.harmonic import (
    BOLTZMANN_EV_PER_K,
    CANONICAL_HARMONIC_THERMOCHEMISTRY_FORMAT,
    HARMONIC_THERMOCHEMISTRY_TOOL_NAME,
    HarmonicThermochemistryError,
    calculate_harmonic_thermochemistry,
    materialize_harmonic_thermochemistry,
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


def _mode(
    index: int,
    *,
    wavenumber_cm_inverse: float,
    energy_mev: float,
    atom_uids: tuple[object, object],
    kind: VaspFrequencyModeKind = VaspFrequencyModeKind.REAL,
) -> VaspFrequencyMode:
    return VaspFrequencyMode(
        mode_index=index,
        kind=kind,
        frequency_thz=max(wavenumber_cm_inverse / 33.3564095, 0.001),
        angular_frequency_2pi_thz=max(wavenumber_cm_inverse / 5.309, 0.001),
        wavenumber_cm_inverse=wavenumber_cm_inverse,
        energy_mev=energy_mev,
        eigenvectors=tuple(
            VaspFrequencyEigenvector(
                atom_uid=atom_uid,
                components=(0.1 * index, 0.0, 0.0),
            )
            for atom_uid in atom_uids
        ),
    )


def _source_result(
    atom_uids: tuple[object, object],
    *,
    imaginary_first: bool = False,
) -> VaspResultDocument:
    raw_artifact_id = new_artifact_id()
    kinds = (
        VaspFrequencyModeKind.IMAGINARY if imaginary_first else VaspFrequencyModeKind.REAL
    )
    modes = (
        _mode(
            1,
            wavenumber_cm_inverse=25.0,
            energy_mev=3.1,
            atom_uids=atom_uids,
            kind=kinds,
        ),
        _mode(2, wavenumber_cm_inverse=40.0, energy_mev=5.0, atom_uids=atom_uids),
        _mode(3, wavenumber_cm_inverse=100.0, energy_mev=12.4, atom_uids=atom_uids),
        _mode(4, wavenumber_cm_inverse=250.0, energy_mev=31.0, atom_uids=atom_uids),
        _mode(5, wavenumber_cm_inverse=500.0, energy_mev=62.0, atom_uids=atom_uids),
        _mode(6, wavenumber_cm_inverse=1000.0, energy_mev=124.0, atom_uids=atom_uids),
    )
    return VaspResultDocument(
        calculation_type=CalculationType.FREQUENCY,
        sources=(
            VaspResultSource(
                role=VaspResultSourceRole.OUTCAR,
                artifact_id=raw_artifact_id,
                artifact_type=ArtifactType.OUTCAR,
                sha256="1" * 64,
            ),
        ),
        energies=VaspEnergySummary(
            free_energy_toten_ev=-9.7,
            energy_without_entropy_ev=-9.9,
            energy_sigma0_ev=-10.0,
        ),
        frequencies=VaspFrequencyDataset(
            atom_uids=atom_uids,
            displaced_atom_uids=atom_uids,
            modes=modes,
        ),
    )


def _identity(
    *,
    exclusions: tuple[ModeExclusion, ...] | None = None,
    imaginary_policy: ImaginaryModePolicy = ImaginaryModePolicy.REJECT_ANY,
    low_policy: LowFrequencyPolicy = LowFrequencyPolicy.EXCLUDE_EXPLICIT,
) -> ThermochemistryIdentity:
    resolved_exclusions = exclusions
    if resolved_exclusions is None:
        resolved_exclusions = (
            ModeExclusion(1, ModeExclusionReason.LOW_FREQUENCY),
            ModeExclusion(2, ModeExclusionReason.LOW_FREQUENCY),
        )
    return ThermochemistryIdentity(
        subject_kind=ThermochemistrySubjectKind.ADSORBATE,
        conditions=ThermochemicalConditions(
            temperature_k=298.15,
            standard_state=ThermochemicalStandardState.SURFACE_FIXED_CELL,
        ),
        electronic_energy_kind=ElectronicEnergyKind.SIGMA_ZERO,
        electronic_entropy_policy=ElectronicEntropyPolicy.NEGLECTED,
        vibrational_policy=VibrationalModePolicy(
            frequency_cutoff_cm_inverse=50.0,
            imaginary_mode_policy=imaginary_policy,
            low_frequency_policy=low_policy,
            exclusions=resolved_exclusions,
        ),
    )


def _snapshot(atom_uids: tuple[object, object]) -> StructureSnapshot:
    return StructureSnapshot(
        lattice=Lattice(
            (
                (12.0, 0.0, 0.0),
                (0.0, 12.0, 0.0),
                (0.0, 0.0, 18.0),
            )
        ),
        sites=(
            StructureSite(
                atom_uid=atom_uids[0],
                element="C",
                fractional_coords=(0.4, 0.5, 0.5),
            ),
            StructureSite(
                atom_uid=atom_uids[1],
                element="O",
                fractional_coords=(0.6, 0.5, 0.5),
            ),
        ),
        periodic=(True, True, False),
    )


def _method(recipe_id: str) -> MethodFingerprint:
    return MethodFingerprint(
        method=MethodDefinition(
            xc_functional="PBE",
            potcar_family="PBE_54",
            potcars=(
                PotcarIdentity("C", "C", "2" * 64),
                PotcarIdentity("O", "O", "3" * 64),
            ),
            engine_version="6.5.1",
        ),
        protocol=ProtocolDefinition(
            encut_ev=450.0,
            kpoints=KPointPolicy(KPointPolicyKind.GAMMA_ONLY),
        ),
        recipe=RecipeIdentity(recipe_id),
    )


def _parsed_source(
    *,
    root: Path,
    project: Project,
    calculation: Calculation,
    source_result: VaspResultDocument,
) -> tuple[Analysis, Artifact]:
    raw_artifact_id = source_result.sources[0].artifact_id
    analysis = Analysis(
        project_id=project.id,
        analysis_type=AnalysisType.RESULT_PARSE,
        input_artifact_ids=(raw_artifact_id,),
        status=AnalysisStatus.COMPLETED,
        tool="ecatvasp.vasp.scientific-result-pipeline",
        tool_version="1",
        parameters_hash="4" * 64,
    )
    relative = (
        Path("calculations")
        / str(calculation.id)
        / "scientific"
        / "parsed-result.json"
    )
    payload = {
        "format": VASP_RESULT_DOCUMENT_FORMAT,
        "version": VASP_RESULT_DOCUMENT_VERSION,
        "calculation_id": calculation.id,
        "analysis_id": analysis.id,
        "intake_hash": "5" * 64,
        "result": source_result,
    }
    body = (canonical_json(payload) + "\n").encode()
    absolute = root / relative
    absolute.parent.mkdir(parents=True)
    absolute.write_bytes(body)
    artifact = Artifact(
        artifact_type=ArtifactType.PARSED_RESULT,
        producer=AnalysisProducerRef(analysis.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=relative.as_posix(),
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
    )
    return analysis, artifact


def test_harmonic_terms_use_explicit_energy_semantic_and_mode_policy() -> None:
    atom_uids = (new_atom_uid(), new_atom_uid())
    result = calculate_harmonic_thermochemistry(
        source_result=_source_result(atom_uids),
        identity=_identity(),
    )

    assert result.components.electronic_energy_ev == -10.0
    assert result.mode_selection is not None
    assert result.mode_selection.accepted_mode_indices == (3, 4, 5, 6)
    assert tuple(item.mode_index for item in result.mode_selection.excluded_modes) == (1, 2)
    assert result.components.zpe_ev == pytest.approx(0.5 * (0.0124 + 0.031 + 0.062 + 0.124))
    assert result.components.vibrational_thermal_energy_ev > 0.0
    assert result.components.vibrational_entropy_ev_per_k > 0.0
    assert result.gibbs_free_energy_ev < result.components.electronic_energy_ev + 0.2
    assert pytest.approx(8.617333262145e-5) == BOLTZMANN_EV_PER_K


def test_harmonic_policy_rejects_unacknowledged_imaginary_mode() -> None:
    atom_uids = (new_atom_uid(), new_atom_uid())
    with pytest.raises(HarmonicThermochemistryError, match="imaginary VASP modes"):
        calculate_harmonic_thermochemistry(
            source_result=_source_result(atom_uids, imaginary_first=True),
            identity=_identity(
                exclusions=(ModeExclusion(2, ModeExclusionReason.LOW_FREQUENCY),),
            ),
        )


def test_explicit_imaginary_exclusion_is_auditable() -> None:
    atom_uids = (new_atom_uid(), new_atom_uid())
    result = calculate_harmonic_thermochemistry(
        source_result=_source_result(atom_uids, imaginary_first=True),
        identity=_identity(
            exclusions=(
                ModeExclusion(1, ModeExclusionReason.IMAGINARY),
                ModeExclusion(2, ModeExclusionReason.LOW_FREQUENCY),
            ),
            imaginary_policy=ImaginaryModePolicy.EXCLUDE_EXPLICIT,
        ),
    )

    assert result.mode_selection is not None
    assert result.mode_selection.accepted_mode_indices == (3, 4, 5, 6)
    assert result.mode_selection.excluded_modes[0].reason is ModeExclusionReason.IMAGINARY


def test_selected_missing_electronic_energy_fails_closed() -> None:
    atom_uids = (new_atom_uid(), new_atom_uid())
    source = _source_result(atom_uids)
    source = VaspResultDocument(
        calculation_type=source.calculation_type,
        sources=source.sources,
        energies=VaspEnergySummary(
            free_energy_toten_ev=source.energies.free_energy_toten_ev,
            energy_without_entropy_ev=source.energies.energy_without_entropy_ev,
        ),
        frequencies=source.frequencies,
    )
    with pytest.raises(HarmonicThermochemistryError, match="selected VASP electronic-energy"):
        calculate_harmonic_thermochemistry(source_result=source, identity=_identity())


def test_materialization_builds_scientific_dependency_chain(tmp_path: Path) -> None:
    project = Project(name="thermo", slug="thermo")
    atom_uids = (new_atom_uid(), new_atom_uid())
    snapshot = _snapshot(atom_uids)
    method = _method("WXC.VASP.AdsorbateFrequency")
    calculation = Calculation(
        project_id=project.id,
        calculation_type=CalculationType.FREQUENCY,
        input_structure_snapshot_id=snapshot.id,
        recipe_id=method.recipe.recipe_id,
        method_fingerprint_id=method.id,
        status=CalculationScientificStatus.CONVERGED,
    )
    source_result = _source_result(atom_uids)
    source_analysis, source_artifact = _parsed_source(
        root=tmp_path,
        project=project,
        calculation=calculation,
        source_result=source_result,
    )

    durable = materialize_harmonic_thermochemistry(
        project_root=tmp_path,
        calculation=calculation,
        method_fingerprint=method,
        structure_snapshot=snapshot,
        source_analysis=source_analysis,
        source_artifact=source_artifact,
        source_result=source_result,
        identity=_identity(),
    )

    assert durable.analysis.analysis_type is AnalysisType.THERMOCHEMISTRY
    assert durable.analysis.status is AnalysisStatus.COMPLETED
    assert durable.analysis.tool == HARMONIC_THERMOCHEMISTRY_TOOL_NAME
    assert durable.analysis.input_artifact_ids == (source_artifact.id,)
    assert durable.artifact.artifact_type is ArtifactType.DERIVED_DATASET
    assert isinstance(durable.artifact.producer, AnalysisProducerRef)
    assert durable.artifact.producer.id == durable.analysis.id
    assert durable.artifact.local_path is not None
    output = tmp_path / durable.artifact.local_path
    assert output.is_file()
    assert durable.artifact.sha256 == hashlib.sha256(output.read_bytes()).hexdigest()
    assert CANONICAL_HARMONIC_THERMOCHEMISTRY_FORMAT in output.read_text()
    assert {record.role for record in durable.dependency_records} == {
        "frequency_calculation",
        "method_fingerprint",
        "frequency_structure_snapshot",
        "parsed_result_analysis",
        "parsed_frequency_result",
        "harmonic_thermochemistry",
    }
    assert len(durable.provenance_records) == 2


def test_materialization_rejects_unconverged_or_tampered_source(tmp_path: Path) -> None:
    project = Project(name="thermo", slug="thermo")
    atom_uids = (new_atom_uid(), new_atom_uid())
    snapshot = _snapshot(atom_uids)
    method = _method("WXC.VASP.AdsorbateFrequency")
    calculation = Calculation(
        project_id=project.id,
        calculation_type=CalculationType.FREQUENCY,
        input_structure_snapshot_id=snapshot.id,
        recipe_id=method.recipe.recipe_id,
        method_fingerprint_id=method.id,
        status=CalculationScientificStatus.CONVERGED,
    )
    source_result = _source_result(atom_uids)
    source_analysis, source_artifact = _parsed_source(
        root=tmp_path,
        project=project,
        calculation=calculation,
        source_result=source_result,
    )

    blocked = Calculation(
        project_id=calculation.project_id,
        calculation_type=calculation.calculation_type,
        input_structure_snapshot_id=calculation.input_structure_snapshot_id,
        recipe_id=calculation.recipe_id,
        method_fingerprint_id=calculation.method_fingerprint_id,
        id=calculation.id,
        status=CalculationScientificStatus.COMPLETED_UNCONVERGED,
    )
    with pytest.raises(HarmonicThermochemistryError, match="scientifically CONVERGED"):
        materialize_harmonic_thermochemistry(
            project_root=tmp_path,
            calculation=blocked,
            method_fingerprint=method,
            structure_snapshot=snapshot,
            source_analysis=source_analysis,
            source_artifact=source_artifact,
            source_result=source_result,
            identity=_identity(),
        )

    assert source_artifact.local_path is not None
    (tmp_path / source_artifact.local_path).write_text("tampered\n")
    with pytest.raises(HarmonicThermochemistryError, match=r"byte size differs|SHA-256 differs"):
        materialize_harmonic_thermochemistry(
            project_root=tmp_path,
            calculation=calculation,
            method_fingerprint=method,
            structure_snapshot=snapshot,
            source_analysis=source_analysis,
            source_artifact=source_artifact,
            source_result=source_result,
            identity=_identity(),
        )
