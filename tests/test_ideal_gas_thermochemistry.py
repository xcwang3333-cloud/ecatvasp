from __future__ import annotations

import hashlib
from dataclasses import replace
from math import log
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
    ONE_ATM_PA,
    ONE_BAR_PA,
    ElectronicEnergyKind,
    ElectronicEntropyPolicy,
    GasAtomicMass,
    GasGeometryKind,
    GasMoleculeModel,
    GasReferenceDefinition,
    GasReferenceSpecies,
    GasThermochemistryError,
    ImaginaryModePolicy,
    LowFrequencyPolicy,
    ModeExclusion,
    ModeExclusionReason,
    ThermochemicalConditions,
    ThermochemicalStandardState,
    ThermochemistryIdentity,
    ThermochemistrySubjectKind,
    VibrationalModePolicy,
    calculate_ideal_gas_thermochemistry,
    materialize_ideal_gas_thermochemistry,
)
from ecatvasp.thermo.gas import BOLTZMANN_EV_PER_K
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
    atom_uids: tuple[object, ...],
    wavenumber: float,
    energy_mev: float,
) -> VaspFrequencyMode:
    return VaspFrequencyMode(
        mode_index=index,
        kind=VaspFrequencyModeKind.REAL,
        frequency_thz=max(wavenumber / 33.3564095, 0.001),
        angular_frequency_2pi_thz=max(wavenumber / 5.309, 0.001),
        wavenumber_cm_inverse=wavenumber,
        energy_mev=energy_mev,
        eigenvectors=tuple(
            VaspFrequencyEigenvector(
                atom_uid=atom_uid,
                components=(0.01 * index, 0.0, 0.0),
            )
            for atom_uid in atom_uids
        ),
    )


def _gas_result(
    atom_uids: tuple[object, ...],
    *,
    vibrational_wavenumbers: tuple[float, ...],
    electronic_energy_ev: float = -6.8,
) -> VaspResultDocument:
    rigid_count = 3 * len(atom_uids) - len(vibrational_wavenumbers)
    rigid_modes = tuple(
        _mode(
            index,
            atom_uids=atom_uids,
            wavenumber=5.0 + index,
            energy_mev=0.5 + 0.1 * index,
        )
        for index in range(1, rigid_count + 1)
    )
    vibrational_modes = tuple(
        _mode(
            rigid_count + offset,
            atom_uids=atom_uids,
            wavenumber=wavenumber,
            energy_mev=wavenumber * 0.1239841984,
        )
        for offset, wavenumber in enumerate(
            vibrational_wavenumbers,
            start=1,
        )
    )
    return VaspResultDocument(
        calculation_type=CalculationType.GAS_FREQUENCY,
        sources=(
            VaspResultSource(
                role=VaspResultSourceRole.OUTCAR,
                artifact_id=new_artifact_id(),
                artifact_type=ArtifactType.OUTCAR,
                sha256="a" * 64,
            ),
        ),
        energies=VaspEnergySummary(
            free_energy_toten_ev=electronic_energy_ev + 0.2,
            energy_without_entropy_ev=electronic_energy_ev + 0.1,
            energy_sigma0_ev=electronic_energy_ev,
        ),
        frequencies=VaspFrequencyDataset(
            atom_uids=atom_uids,
            displaced_atom_uids=atom_uids,
            modes=rigid_modes + vibrational_modes,
        ),
    )


def _linear_snapshot(
    elements: tuple[str, ...],
    atom_uids: tuple[object, ...],
) -> StructureSnapshot:
    fractions = (
        (0.46, 0.5, 0.5),
        (0.50, 0.5, 0.5),
        (0.54, 0.5, 0.5),
    )
    sites = tuple(
        StructureSite(
            atom_uid=atom_uid,
            element=element,
            fractional_coords=fractions[index],
        )
        for index, (element, atom_uid) in enumerate(
            zip(elements, atom_uids, strict=True)
        )
    )
    return StructureSnapshot(
        lattice=Lattice(
            (
                (20.0, 0.0, 0.0),
                (0.0, 20.0, 0.0),
                (0.0, 0.0, 20.0),
            )
        ),
        sites=sites,
        periodic=(True, True, True),
    )


def _water_snapshot(
    atom_uids: tuple[object, object, object],
) -> StructureSnapshot:
    return StructureSnapshot(
        lattice=Lattice(
            (
                (20.0, 0.0, 0.0),
                (0.0, 20.0, 0.0),
                (0.0, 0.0, 20.0),
            )
        ),
        sites=(
            StructureSite(atom_uids[0], "O", (0.5, 0.5, 0.5)),
            StructureSite(atom_uids[1], "H", (0.5478, 0.5, 0.5)),
            StructureSite(atom_uids[2], "H", (0.4880, 0.5462, 0.5)),
        ),
        periodic=(True, True, True),
    )


def _identity(
    *,
    atom_masses: tuple[GasAtomicMass, ...],
    geometry: GasGeometryKind,
    symmetry_number: int,
    spin_multiplicity: int,
    translational_indices: tuple[int, int, int],
    rotational_indices: tuple[int, ...],
    pressure_pa: float = ONE_BAR_PA,
    standard_state: ThermochemicalStandardState = (
        ThermochemicalStandardState.IDEAL_GAS_1_BAR
    ),
    electronic_entropy_policy: ElectronicEntropyPolicy = (
        ElectronicEntropyPolicy.NEGLECTED
    ),
) -> ThermochemistryIdentity:
    exclusions = tuple(
        ModeExclusion(index, ModeExclusionReason.TRANSLATIONAL)
        for index in translational_indices
    ) + tuple(
        ModeExclusion(index, ModeExclusionReason.ROTATIONAL)
        for index in rotational_indices
    )
    return ThermochemistryIdentity(
        subject_kind=ThermochemistrySubjectKind.GAS,
        conditions=ThermochemicalConditions(
            temperature_k=298.15,
            standard_state=standard_state,
            pressure_pa=pressure_pa,
        ),
        electronic_energy_kind=ElectronicEnergyKind.SIGMA_ZERO,
        electronic_entropy_policy=electronic_entropy_policy,
        vibrational_policy=VibrationalModePolicy(
            frequency_cutoff_cm_inverse=50.0,
            imaginary_mode_policy=ImaginaryModePolicy.REJECT_ANY,
            low_frequency_policy=LowFrequencyPolicy.EXCLUDE_EXPLICIT,
            exclusions=exclusions,
        ),
        gas_model=GasMoleculeModel(
            geometry_kind=geometry,
            symmetry_number=symmetry_number,
            spin_multiplicity=spin_multiplicity,
            atomic_masses=atom_masses,
        ),
    )


def _h2_case() -> tuple[
    GasReferenceDefinition,
    StructureSnapshot,
    VaspResultDocument,
    ThermochemistryIdentity,
]:
    atom_uids = (new_atom_uid(), new_atom_uid())
    snapshot = _linear_snapshot(("H", "H"), atom_uids)
    result = _gas_result(atom_uids, vibrational_wavenumbers=(4400.0,))
    identity = _identity(
        atom_masses=(
            GasAtomicMass(atom_uids[0], 1.00784, "1H"),
            GasAtomicMass(atom_uids[1], 1.00784, "1H"),
        ),
        geometry=GasGeometryKind.LINEAR,
        symmetry_number=2,
        spin_multiplicity=1,
        translational_indices=(1, 2, 3),
        rotational_indices=(4, 5),
    )
    return (
        GasReferenceDefinition(GasReferenceSpecies.H2),
        snapshot,
        result,
        identity,
    )


def test_h2_ideal_gas_components_are_explicit_and_positive() -> None:
    reference, snapshot, source, identity = _h2_case()
    result, rotor = calculate_ideal_gas_thermochemistry(
        reference=reference,
        structure_snapshot=snapshot,
        source_result=source,
        identity=identity,
    )

    components = result.components
    assert components.electronic_energy_ev == -6.8
    expected_zpe = 0.5 * 4400.0 * 0.1239841984 / 1000.0
    assert components.zpe_ev == pytest.approx(expected_zpe)
    assert components.translational_thermal_energy_ev == pytest.approx(
        1.5 * BOLTZMANN_EV_PER_K * 298.15
    )
    assert components.rotational_thermal_energy_ev == pytest.approx(
        BOLTZMANN_EV_PER_K * 298.15
    )
    assert components.pv_ev == pytest.approx(
        BOLTZMANN_EV_PER_K * 298.15
    )
    assert components.translational_entropy_ev_per_k > 0.0
    assert components.rotational_entropy_ev_per_k > 0.0
    assert rotor.geometry_kind is GasGeometryKind.LINEAR
    assert rotor.principal_moments_kg_m2[0] == pytest.approx(
        0.0,
        abs=1.0e-50,
    )
    assert result.mode_selection is not None
    assert result.mode_selection.accepted_mode_indices == (6,)


def test_pressure_and_standard_state_are_not_silently_collapsed() -> None:
    reference, snapshot, source, identity = _h2_case()
    one_bar, _ = calculate_ideal_gas_thermochemistry(
        reference=reference,
        structure_snapshot=snapshot,
        source_result=source,
        identity=identity,
    )
    two_bar_identity = replace(
        identity,
        conditions=replace(
            identity.conditions,
            pressure_pa=2.0 * ONE_BAR_PA,
        ),
    )
    two_bar, _ = calculate_ideal_gas_thermochemistry(
        reference=reference,
        structure_snapshot=snapshot,
        source_result=source,
        identity=two_bar_identity,
    )
    expected_shift = BOLTZMANN_EV_PER_K * 298.15 * log(2.0)
    observed_shift = two_bar.gibbs_free_energy_ev - one_bar.gibbs_free_energy_ev
    assert observed_shift == pytest.approx(expected_shift)

    one_atm_identity = replace(
        identity,
        conditions=ThermochemicalConditions(
            temperature_k=298.15,
            standard_state=ThermochemicalStandardState.IDEAL_GAS_1_ATM,
            pressure_pa=ONE_ATM_PA,
        ),
    )
    one_atm, _ = calculate_ideal_gas_thermochemistry(
        reference=reference,
        structure_snapshot=snapshot,
        source_result=source,
        identity=one_atm_identity,
    )
    assert one_atm_identity.parameters_hash != identity.parameters_hash
    assert one_atm.gibbs_free_energy_ev != one_bar.gibbs_free_energy_ev


def test_water_nonlinear_rotor_has_three_positive_moments() -> None:
    atom_uids = (new_atom_uid(), new_atom_uid(), new_atom_uid())
    snapshot = _water_snapshot(atom_uids)
    source = _gas_result(
        atom_uids,
        vibrational_wavenumbers=(1595.0, 3657.0, 3756.0),
    )
    identity = _identity(
        atom_masses=(
            GasAtomicMass(atom_uids[0], 15.999, "16O"),
            GasAtomicMass(atom_uids[1], 1.00784, "1H"),
            GasAtomicMass(atom_uids[2], 1.00784, "1H"),
        ),
        geometry=GasGeometryKind.NONLINEAR,
        symmetry_number=2,
        spin_multiplicity=1,
        translational_indices=(1, 2, 3),
        rotational_indices=(4, 5, 6),
    )
    result, rotor = calculate_ideal_gas_thermochemistry(
        reference=GasReferenceDefinition(GasReferenceSpecies.H2O),
        structure_snapshot=snapshot,
        source_result=source,
        identity=identity,
    )

    assert all(value > 0.0 for value in rotor.principal_moments_kg_m2)
    assert result.components.rotational_thermal_energy_ev == pytest.approx(
        1.5 * BOLTZMANN_EV_PER_K * 298.15
    )
    assert result.mode_selection is not None
    assert result.mode_selection.accepted_mode_indices == (7, 8, 9)


def test_o2_triplet_spin_degeneracy_is_explicit_entropy() -> None:
    atom_uids = (new_atom_uid(), new_atom_uid())
    snapshot = _linear_snapshot(("O", "O"), atom_uids)
    source = _gas_result(
        atom_uids,
        vibrational_wavenumbers=(1580.0,),
        electronic_energy_ev=-9.9,
    )
    identity = _identity(
        atom_masses=(
            GasAtomicMass(atom_uids[0], 15.999, "16O"),
            GasAtomicMass(atom_uids[1], 15.999, "16O"),
        ),
        geometry=GasGeometryKind.LINEAR,
        symmetry_number=2,
        spin_multiplicity=3,
        translational_indices=(1, 2, 3),
        rotational_indices=(4, 5),
        electronic_entropy_policy=ElectronicEntropyPolicy.SPIN_DEGENERACY,
    )
    result, _ = calculate_ideal_gas_thermochemistry(
        reference=GasReferenceDefinition(GasReferenceSpecies.O2),
        structure_snapshot=snapshot,
        source_result=source,
        identity=identity,
    )

    expected_entropy = BOLTZMANN_EV_PER_K * log(3.0)
    assert result.components.electronic_entropy_ev_per_k == pytest.approx(
        expected_entropy
    )


def test_registry_fails_closed_on_composition_mass_and_geometry_mismatch() -> None:
    reference, snapshot, source, identity = _h2_case()
    with pytest.raises(GasThermochemistryError, match="composition differs"):
        calculate_ideal_gas_thermochemistry(
            reference=GasReferenceDefinition(GasReferenceSpecies.O2),
            structure_snapshot=snapshot,
            source_result=source,
            identity=identity,
        )

    atom_uids = tuple(site.atom_uid for site in snapshot.sites)
    foreign_uid = new_atom_uid()
    wrong_mass_identity = replace(
        identity,
        gas_model=GasMoleculeModel(
            geometry_kind=GasGeometryKind.LINEAR,
            symmetry_number=2,
            spin_multiplicity=1,
            atomic_masses=(
                GasAtomicMass(atom_uids[0], 1.00784),
                GasAtomicMass(foreign_uid, 1.00784),
            ),
        ),
    )
    with pytest.raises(
        GasThermochemistryError,
        match="atomic masses must cover exactly",
    ):
        calculate_ideal_gas_thermochemistry(
            reference=reference,
            structure_snapshot=snapshot,
            source_result=source,
            identity=wrong_mass_identity,
        )

    water_uids = (new_atom_uid(), new_atom_uid(), new_atom_uid())
    water_snapshot = _water_snapshot(water_uids)
    water_source = _gas_result(
        water_uids,
        vibrational_wavenumbers=(1595.0, 3657.0, 3756.0),
    )
    wrong_geometry = _identity(
        atom_masses=(
            GasAtomicMass(water_uids[0], 15.999),
            GasAtomicMass(water_uids[1], 1.00784),
            GasAtomicMass(water_uids[2], 1.00784),
        ),
        geometry=GasGeometryKind.LINEAR,
        symmetry_number=2,
        spin_multiplicity=1,
        translational_indices=(1, 2, 3),
        rotational_indices=(4, 5),
    )
    with pytest.raises(
        GasThermochemistryError,
        match="LINEAR gas model disagrees",
    ):
        calculate_ideal_gas_thermochemistry(
            reference=GasReferenceDefinition(GasReferenceSpecies.H2O),
            structure_snapshot=water_snapshot,
            source_result=water_source,
            identity=wrong_geometry,
        )


def _method(recipe_id: str) -> MethodFingerprint:
    return MethodFingerprint(
        method=MethodDefinition(
            xc_functional="PBE",
            potcar_family="PBE_54",
            potcars=(PotcarIdentity("H", "H", "b" * 64),),
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
    result: VaspResultDocument,
) -> tuple[Analysis, Artifact]:
    analysis = Analysis(
        project_id=project.id,
        analysis_type=AnalysisType.RESULT_PARSE,
        input_artifact_ids=(result.sources[0].artifact_id,),
        status=AnalysisStatus.COMPLETED,
        tool="ecatvasp.vasp.scientific-result-pipeline",
        tool_version="1",
        parameters_hash="c" * 64,
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
        "intake_hash": "d" * 64,
        "result": result,
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


def test_gas_materialization_builds_existing_scientific_dag(
    tmp_path: Path,
) -> None:
    reference, snapshot, source, identity = _h2_case()
    project = Project(name="gas", slug="gas")
    method = _method("WXC.VASP.GasFrequency")
    calculation = Calculation(
        project_id=project.id,
        calculation_type=CalculationType.GAS_FREQUENCY,
        input_structure_snapshot_id=snapshot.id,
        recipe_id=method.recipe.recipe_id,
        method_fingerprint_id=method.id,
        status=CalculationScientificStatus.CONVERGED,
    )
    source_analysis, source_artifact = _parsed_source(
        root=tmp_path,
        project=project,
        calculation=calculation,
        result=source,
    )

    durable = materialize_ideal_gas_thermochemistry(
        project_root=tmp_path,
        reference=reference,
        calculation=calculation,
        method_fingerprint=method,
        structure_snapshot=snapshot,
        source_analysis=source_analysis,
        source_artifact=source_artifact,
        source_result=source,
        identity=identity,
    )

    assert durable.analysis.analysis_type is AnalysisType.THERMOCHEMISTRY
    assert durable.artifact.artifact_type is ArtifactType.DERIVED_DATASET
    assert isinstance(durable.artifact.producer, AnalysisProducerRef)
    assert durable.artifact.producer.id == durable.analysis.id
    assert durable.artifact.local_path is not None
    output = tmp_path / durable.artifact.local_path
    assert output.is_file()
    assert durable.artifact.sha256 == hashlib.sha256(output.read_bytes()).hexdigest()
    assert {record.role for record in durable.dependency_records} == {
        "gas_frequency_calculation",
        "method_fingerprint",
        "gas_structure_snapshot",
        "parsed_result_analysis",
        "parsed_gas_frequency_result",
        "ideal_gas_thermochemistry",
    }
