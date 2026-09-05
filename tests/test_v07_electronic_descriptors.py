from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID

import pytest

from ecatvasp.analysis.descriptors import (
    BandCenterEnergyReference,
    BandCenterError,
    BandCenterKind,
    BandCenterParameters,
    BandCenterSelector,
    BandCenterSpinMode,
    DurableBandCenter,
    calculate_band_center,
    load_band_center_artifact,
    materialize_band_center_analysis,
)
from ecatvasp.analysis.dos_materialization import (
    DurableDosMaterialization,
    materialize_canonical_dos_analysis,
)
from ecatvasp.analysis.doscar import CanonicalDosIntake, parse_vasp_doscar
from ecatvasp.analysis.electronic import (
    CanonicalDosResult,
    DosSeries,
    ElectronicEnergyAxis,
    OrbitalChannel,
    ProjectionScope,
    SpinChannel,
)
from ecatvasp.domain import (
    AnalysisProducerRef,
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
from ecatvasp.domain.ids import StructureSnapshotId
from ecatvasp.provenance import FreshnessEngine, FreshnessState, scientific_hash
from ecatvasp.schema.version import SCHEMA_VERSION
from ecatvasp.storage import ProjectBundle, ProjectStore


_PY = OrbitalChannel("py", 1)
_PZ = OrbitalChannel("pz", 1)
_PX = OrbitalChannel("px", 1)
_DXY = OrbitalChannel("dxy", 2)


def _pure_unpolarized() -> tuple[CanonicalDosResult, object, object]:
    atom_a = new_atom_uid()
    atom_b = new_atom_uid()
    snapshot_id = StructureSnapshotId(UUID("00000000-0000-7000-8000-000000000701"))
    energies = (0.0, 1.0, 2.0)
    series = [
        DosSeries(ProjectionScope.SYSTEM, SpinChannel.TOTAL, (2.0, 2.0, 2.0)),
    ]
    for orbital in (_PY, _PZ, _PX):
        series.append(
            DosSeries(
                ProjectionScope.ATOM,
                SpinChannel.TOTAL,
                (1.0, 1.0, 1.0),
                atom_uid=atom_a,
                element="C",
                orbital=orbital,
            )
        )
        series.append(
            DosSeries(
                ProjectionScope.ATOM,
                SpinChannel.TOTAL,
                (2.0, 2.0, 2.0),
                atom_uid=atom_b,
                element="C",
                orbital=orbital,
            )
        )
    series.extend(
        (
            DosSeries(
                ProjectionScope.ATOM,
                SpinChannel.TOTAL,
                (0.0, 1.0, 3.0),
                atom_uid=atom_a,
                element="C",
                orbital=_DXY,
            ),
            DosSeries(
                ProjectionScope.ATOM,
                SpinChannel.TOTAL,
                (3.0, 1.0, 0.0),
                atom_uid=atom_b,
                element="C",
                orbital=_DXY,
            ),
        )
    )
    return (
        CanonicalDosResult(
            structure_snapshot_id=snapshot_id,
            energy_axis=ElectronicEnergyAxis(energies, 1.0),
            series=tuple(series),
            atom_index_map_sha256="a" * 64,
        ),
        atom_a,
        atom_b,
    )


def _pure_spin_polarized() -> tuple[CanonicalDosResult, object]:
    atom_uid = new_atom_uid()
    snapshot_id = StructureSnapshotId(UUID("00000000-0000-7000-8000-000000000702"))
    result = CanonicalDosResult(
        structure_snapshot_id=snapshot_id,
        energy_axis=ElectronicEnergyAxis((0.0, 1.0, 2.0), 1.0),
        series=(
            DosSeries(ProjectionScope.SYSTEM, SpinChannel.UP, (1.0, 1.0, 1.0)),
            DosSeries(ProjectionScope.SYSTEM, SpinChannel.DOWN, (3.0, 3.0, 3.0)),
            DosSeries(
                ProjectionScope.ATOM,
                SpinChannel.UP,
                (1.0, 1.0, 1.0),
                atom_uid=atom_uid,
                element="Fe",
                orbital=_DXY,
            ),
            DosSeries(
                ProjectionScope.ATOM,
                SpinChannel.DOWN,
                (3.0, 3.0, 3.0),
                atom_uid=atom_uid,
                element="Fe",
                orbital=_DXY,
            ),
        ),
        atom_index_map_sha256="b" * 64,
    )
    return result, atom_uid


def test_system_band_center_has_explicit_native_and_fermi_relative_frames() -> None:
    source, _, _ = _pure_unpolarized()
    native = calculate_band_center(
        source=source,
        source_artifact_sha256="c" * 64,
        parameters=BandCenterParameters(
            kind=BandCenterKind.BAND,
            selector=BandCenterSelector(
                ProjectionScope.SYSTEM,
                BandCenterSpinMode.TOTAL,
            ),
            energy_reference=BandCenterEnergyReference.VASP_NATIVE,
            window_lower_ev=0.0,
            window_upper_ev=2.0,
        ),
    )
    relative = calculate_band_center(
        source=source,
        source_artifact_sha256="c" * 64,
        parameters=replace(
            native.parameters,
            energy_reference=BandCenterEnergyReference.FERMI_RELATIVE,
            window_lower_ev=-1.0,
            window_upper_ev=1.0,
        ),
    )
    assert native.center_ev == pytest.approx(1.0)
    assert relative.center_ev == pytest.approx(0.0)
    assert native.center_ev - relative.center_ev == pytest.approx(1.0)
    assert native.parameters.content_hash != relative.parameters.content_hash


def test_p_band_atom_and_element_aggregation_use_orbital_l_and_permanent_uid() -> None:
    source, atom_a, _ = _pure_unpolarized()
    atom_result = calculate_band_center(
        source=source,
        source_artifact_sha256="d" * 64,
        parameters=BandCenterParameters(
            kind=BandCenterKind.P_BAND,
            selector=BandCenterSelector(
                ProjectionScope.ATOM,
                BandCenterSpinMode.TOTAL,
                atom_uid=atom_a,
                element="C",
            ),
            energy_reference=BandCenterEnergyReference.VASP_NATIVE,
            window_lower_ev=0.0,
            window_upper_ev=2.0,
        ),
    )
    element_result = calculate_band_center(
        source=source,
        source_artifact_sha256="d" * 64,
        parameters=replace(
            atom_result.parameters,
            selector=BandCenterSelector(
                ProjectionScope.ELEMENT,
                BandCenterSpinMode.TOTAL,
                element="C",
            ),
        ),
    )
    assert atom_result.center_ev == pytest.approx(1.0)
    assert atom_result.contributing_series_count == 3
    assert element_result.center_ev == pytest.approx(1.0)
    assert element_result.contributing_series_count == 6


def test_d_band_filter_excludes_p_channels() -> None:
    source, atom_a, _ = _pure_unpolarized()
    result = calculate_band_center(
        source=source,
        source_artifact_sha256="e" * 64,
        parameters=BandCenterParameters(
            kind=BandCenterKind.D_BAND,
            selector=BandCenterSelector(
                ProjectionScope.ATOM,
                BandCenterSpinMode.TOTAL,
                atom_uid=atom_a,
                element="C",
            ),
            energy_reference=BandCenterEnergyReference.VASP_NATIVE,
            window_lower_ev=0.0,
            window_upper_ev=2.0,
        ),
    )
    assert result.contributing_series_count == 1
    assert result.center_ev > 1.0


def test_spin_sum_is_explicit_and_total_is_rejected_for_collinear_dos() -> None:
    source, atom_uid = _pure_spin_polarized()
    summed = calculate_band_center(
        source=source,
        source_artifact_sha256="f" * 64,
        parameters=BandCenterParameters(
            kind=BandCenterKind.D_BAND,
            selector=BandCenterSelector(
                ProjectionScope.ATOM,
                BandCenterSpinMode.SUM,
                atom_uid=atom_uid,
                element="Fe",
            ),
            energy_reference=BandCenterEnergyReference.FERMI_RELATIVE,
            window_lower_ev=-1.0,
            window_upper_ev=1.0,
        ),
    )
    assert summed.center_ev == pytest.approx(0.0)
    assert summed.contributing_series_count == 2
    with pytest.raises(BandCenterError, match="no TOTAL channel"):
        calculate_band_center(
            source=source,
            source_artifact_sha256="f" * 64,
            parameters=replace(
                summed.parameters,
                selector=replace(summed.parameters.selector, spin=BandCenterSpinMode.TOTAL),
            ),
        )


def test_window_endpoints_are_linearly_interpolated_without_resampling_source() -> None:
    source, _, _ = _pure_unpolarized()
    custom = replace(
        source,
        series=(DosSeries(ProjectionScope.SYSTEM, SpinChannel.TOTAL, (0.0, 1.0, 2.0)),),
    )
    result = calculate_band_center(
        source=custom,
        source_artifact_sha256="1" * 64,
        parameters=BandCenterParameters(
            kind=BandCenterKind.BAND,
            selector=BandCenterSelector(ProjectionScope.SYSTEM, BandCenterSpinMode.TOTAL),
            energy_reference=BandCenterEnergyReference.VASP_NATIVE,
            window_lower_ev=0.5,
            window_upper_ev=1.5,
        ),
    )
    assert result.quadrature_point_count == 3
    assert result.zeroth_moment_states == pytest.approx(1.0)
    assert result.first_moment_ev_states == pytest.approx(1.125)
    assert result.center_ev == pytest.approx(1.125)


def test_descriptor_fails_closed_for_missing_projection_window_and_nonpositive_weight() -> None:
    source, atom_a, _ = _pure_unpolarized()
    p_params = BandCenterParameters(
        kind=BandCenterKind.P_BAND,
        selector=BandCenterSelector(
            ProjectionScope.ATOM,
            BandCenterSpinMode.TOTAL,
            atom_uid=atom_a,
            element="C",
        ),
        energy_reference=BandCenterEnergyReference.VASP_NATIVE,
        window_lower_ev=0.0,
        window_upper_ev=2.0,
    )
    with pytest.raises(BandCenterError, match="inside the DOS energy range"):
        calculate_band_center(
            source=source,
            source_artifact_sha256="2" * 64,
            parameters=replace(p_params, window_lower_ev=-0.1),
        )
    with pytest.raises(BandCenterError, match="matches no atom-projected"):
        calculate_band_center(
            source=source,
            source_artifact_sha256="2" * 64,
            parameters=replace(
                p_params,
                selector=replace(p_params.selector, element="N"),
            ),
        )
    negative = replace(
        source,
        series=(DosSeries(ProjectionScope.SYSTEM, SpinChannel.TOTAL, (-1.0, -1.0, -1.0)),),
    )
    with pytest.raises(BandCenterError, match="non-positive zeroth moment"):
        calculate_band_center(
            source=negative,
            source_artifact_sha256="2" * 64,
            parameters=BandCenterParameters(
                kind=BandCenterKind.BAND,
                selector=BandCenterSelector(
                    ProjectionScope.SYSTEM,
                    BandCenterSpinMode.TOTAL,
                ),
                energy_reference=BandCenterEnergyReference.VASP_NATIVE,
                window_lower_ev=0.0,
                window_upper_ev=2.0,
            ),
        )


@dataclass(frozen=True, slots=True)
class _DurableCase:
    project: Project
    snapshot: StructureSnapshot
    fingerprint: MethodFingerprint
    calculation: Calculation
    attempt: ExecutionAttempt
    doscar_artifact: Artifact
    atom_map_artifact: Artifact
    intake: CanonicalDosIntake


def _write(path: Path, body: bytes) -> tuple[int, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return len(body), hashlib.sha256(body).hexdigest()


def _durable_case(tmp_path: Path) -> _DurableCase:
    project = Project(name="Band Center", slug="band-center-v07")
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
        slug="band-center-source",
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
    atom_map_path = tmp_path / "calculations" / str(calculation.id) / "input" / "atom-index-map.json"
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
        "1 1 1 0\n"
        "header 2\n"
        "header 3\n"
        "header 4\n"
        "header 5\n"
        "1.0 -1.0 2 0.2\n"
        "-1.0 1.0 0.0\n"
        "1.0 2.0 1.0\n"
        "1.0 -1.0 2 0.2\n"
        "-1.0 1 2 3 4 5 6 7 8 9\n"
        "1.0 2 3 4 5 6 7 8 9 10\n"
    ).encode()
    doscar_path = tmp_path / "calculations" / str(calculation.id) / "attempt-1" / "DOSCAR"
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
    intake = parse_vasp_doscar(
        doscar_bytes=doscar_bytes,
        atom_index_map_bytes=atom_map_bytes,
        structure_snapshot_id=snapshot.id,
        spin_treatment=SpinTreatment.UNPOLARIZED,
    )
    return _DurableCase(
        project=project,
        snapshot=snapshot,
        fingerprint=fingerprint,
        calculation=calculation,
        attempt=attempt,
        doscar_artifact=doscar_artifact,
        atom_map_artifact=atom_map_artifact,
        intake=intake,
    )


def _durable_source(tmp_path: Path, case: _DurableCase) -> DurableDosMaterialization:
    return materialize_canonical_dos_analysis(
        project_root=tmp_path,
        calculation=case.calculation,
        execution_attempt=case.attempt,
        doscar_artifact=case.doscar_artifact,
        atom_index_map_artifact=case.atom_map_artifact,
        intake=case.intake,
    )


def _durable_descriptor(
    tmp_path: Path,
    case: _DurableCase,
    source: DurableDosMaterialization,
) -> DurableBandCenter:
    return materialize_band_center_analysis(
        project_root=tmp_path,
        source_analysis=source.analysis,
        source_artifact=source.artifact,
        parameters=BandCenterParameters(
            kind=BandCenterKind.D_BAND,
            selector=BandCenterSelector(
                ProjectionScope.ATOM,
                BandCenterSpinMode.TOTAL,
                atom_uid=case.snapshot.sites[0].atom_uid,
                element="C",
            ),
            energy_reference=BandCenterEnergyReference.VASP_NATIVE,
            window_lower_ev=-1.0,
            window_upper_ev=1.0,
        ),
    )


def test_durable_band_center_reopens_and_project_store_preserves_chain(tmp_path: Path) -> None:
    case = _durable_case(tmp_path)
    source = _durable_source(tmp_path, case)
    descriptor = _durable_descriptor(tmp_path, case, source)
    assert descriptor.analysis.input_artifact_ids == (source.artifact.id,)
    assert isinstance(descriptor.artifact.producer, AnalysisProducerRef)
    reopened = load_band_center_artifact(
        project_root=tmp_path,
        source_analysis=source.analysis,
        source_artifact=source.artifact,
        analysis=descriptor.analysis,
        artifact=descriptor.artifact,
    )
    assert reopened == descriptor.result
    assert reopened.center_ev == pytest.approx(1.0 / 15.0)

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
        provenance_records=(*source.provenance_records, *descriptor.provenance_records),
        dependency_records=(*source.dependency_records, *descriptor.dependency_records),
    )
    bundle.validate()
    ProjectStore(tmp_path).save(bundle)
    assert ProjectStore(tmp_path).open() == bundle
    assert SCHEMA_VERSION == 3


def test_band_center_freshness_propagates_from_exact_canonical_dos_artifact(tmp_path: Path) -> None:
    case = _durable_case(tmp_path)
    source = _durable_source(tmp_path, case)
    descriptor = _durable_descriptor(tmp_path, case, source)
    dependencies = (*source.dependency_records, *descriptor.dependency_records)
    node_ids = {
        case.calculation.id,
        case.doscar_artifact.id,
        case.atom_map_artifact.id,
        source.analysis.id,
        source.artifact.id,
        descriptor.analysis.id,
        descriptor.artifact.id,
    }
    current_hashes = {
        case.calculation.id: scientific_hash(case.calculation),
        case.doscar_artifact.id: scientific_hash(case.doscar_artifact),
        case.atom_map_artifact.id: scientific_hash(case.atom_map_artifact),
        source.analysis.id: scientific_hash(source.analysis),
        source.artifact.id: "0" * 64,
        descriptor.analysis.id: scientific_hash(descriptor.analysis),
    }
    freshness = FreshnessEngine(dependencies).evaluate(
        node_ids=node_ids,
        current_hashes=current_hashes,
    )
    assert freshness[descriptor.analysis.id].state is FreshnessState.STALE
    assert freshness[descriptor.artifact.id].state is FreshnessState.STALE


def test_band_center_identity_changes_with_selector_window_and_energy_reference(tmp_path: Path) -> None:
    case = _durable_case(tmp_path)
    source = _durable_source(tmp_path, case)
    first = _durable_descriptor(tmp_path, case, source)
    second = materialize_band_center_analysis(
        project_root=tmp_path,
        source_analysis=source.analysis,
        source_artifact=source.artifact,
        parameters=replace(
            first.result.parameters,
            energy_reference=BandCenterEnergyReference.FERMI_RELATIVE,
            window_lower_ev=-1.2,
            window_upper_ev=0.8,
        ),
    )
    assert first.analysis.parameters_hash != second.analysis.parameters_hash
    assert first.result.content_hash != second.result.content_hash


def test_band_center_loader_rejects_analysis_input_drift_and_output_tamper(tmp_path: Path) -> None:
    case = _durable_case(tmp_path)
    source = _durable_source(tmp_path, case)
    descriptor = _durable_descriptor(tmp_path, case, source)
    drifted = replace(descriptor.analysis, input_artifact_ids=(case.doscar_artifact.id,))
    with pytest.raises(BandCenterError, match="input differs"):
        load_band_center_artifact(
            project_root=tmp_path,
            source_analysis=source.analysis,
            source_artifact=source.artifact,
            analysis=drifted,
            artifact=descriptor.artifact,
        )
    output_path = tmp_path / (descriptor.artifact.local_path or "")
    output_path.write_bytes(output_path.read_bytes() + b"tamper")
    with pytest.raises(BandCenterError, match="byte size changed"):
        load_band_center_artifact(
            project_root=tmp_path,
            source_analysis=source.analysis,
            source_artifact=source.artifact,
            analysis=descriptor.analysis,
            artifact=descriptor.artifact,
        )
