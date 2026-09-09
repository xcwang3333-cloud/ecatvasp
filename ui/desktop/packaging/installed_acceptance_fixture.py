"""Deterministic raw scientific prerequisites for installed Windows acceptance."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ecatvasp.domain import (
    Analysis,
    AnalysisProducerRef,
    AnalysisStatus,
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
    RemoteJob,
    RetrievalPolicy,
    SchedulerState,
    SchedulerType,
    SpinTreatment,
    StructureSite,
    StructureSnapshot,
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


@dataclass(frozen=True, slots=True)
class InstalledAcceptanceFixture:
    project_root: Path
    project_id: str
    execution_calculation_id: str
    execution_attempt_id: str
    remote_job_id: str
    dos_calculation_id: str
    dos_atom_uid: str
    clean_frequency_calculation_id: str
    ads_frequency_calculation_id: str
    h2_frequency_calculation_id: str
    h2_atom_uids: tuple[str, str]
    doscar_path: Path
    ads_outcar_path: Path


@dataclass(frozen=True, slots=True)
class _ParsedFrequencySource:
    calculation: Calculation
    attempt: ExecutionAttempt
    raw_artifact: Artifact
    parse_analysis: Analysis
    parsed_artifact: Artifact
    provenance_records: tuple[ProvenanceRecord, ...]
    dependency_records: tuple[DependencyRecord, ...]


@dataclass(frozen=True, slots=True)
class _DosSource:
    calculation: Calculation
    attempt: ExecutionAttempt
    doscar_artifact: Artifact
    atom_map_artifact: Artifact


def build_installed_acceptance_fixture(project_root: Path) -> InstalledAcceptanceFixture:
    root = project_root.resolve()
    root.mkdir(parents=True, exist_ok=True)

    project = Project(name="Installed scientific acceptance", slug="installed-scientific")
    clean_snapshot = _surface_snapshot(with_hydrogen=False)
    ads_snapshot = _surface_snapshot(with_hydrogen=True)
    h2_snapshot = _h2_snapshot()

    surface_method = _method(
        recipe_id="WXC.VASP.AdsorbateFrequency",
        elements=("C", "H"),
    )
    gas_method = _method(recipe_id="WXC.VASP.GasFrequency", elements=("H",))
    dos_method = _method(
        recipe_id="ECatVASP.VASP.DOSPrerequisite",
        elements=("C",),
        spin_treatment=SpinTreatment.UNPOLARIZED,
    )
    execution_method = _method(
        recipe_id="ECatVASP.VASP.SlabRelax",
        elements=("C",),
    )

    clean_source = _parsed_frequency_source(
        root=root,
        project=project,
        snapshot=clean_snapshot,
        method=surface_method,
        calculation_type=CalculationType.FREQUENCY,
        energy_ev=-10.0,
        wavenumbers=(500.0, 600.0, 700.0),
    )
    ads_source = _parsed_frequency_source(
        root=root,
        project=project,
        snapshot=ads_snapshot,
        method=surface_method,
        calculation_type=CalculationType.FREQUENCY,
        energy_ev=-13.0,
        wavenumbers=(500.0, 600.0, 700.0, 800.0, 900.0, 1000.0),
    )
    h2_source = _parsed_frequency_source(
        root=root,
        project=project,
        snapshot=h2_snapshot,
        method=gas_method,
        calculation_type=CalculationType.GAS_FREQUENCY,
        energy_ev=-6.0,
        wavenumbers=(6.0, 7.0, 8.0, 9.0, 10.0, 4400.0),
    )
    dos_source = _dos_source(
        root=root,
        project=project,
        snapshot=clean_snapshot,
        method=dos_method,
    )

    execution_calculation = Calculation(
        project_id=project.id,
        calculation_type=CalculationType.RELAX,
        input_structure_snapshot_id=clean_snapshot.id,
        recipe_id=execution_method.recipe.recipe_id,
        method_fingerprint_id=execution_method.id,
        status=CalculationScientificStatus.DRAFT,
        slug="scheduler-complete-science-draft",
    )
    execution_attempt = ExecutionAttempt(
        calculation_id=execution_calculation.id,
        attempt_number=1,
        status=ExecutionAttemptStatus.EXITED,
    )
    remote_job = RemoteJob(
        execution_attempt_id=execution_attempt.id,
        scheduler=SchedulerType.SLURM,
        scheduler_job_id="987654",
        remote_directory="/scratch/ecatvasp-installed-acceptance",
        state=SchedulerState.COMPLETED,
    )

    parsed_sources = (clean_source, ads_source, h2_source)
    bundle = ProjectBundle(
        project=project,
        structure_snapshots=(clean_snapshot, ads_snapshot, h2_snapshot),
        method_fingerprints=(surface_method, gas_method, dos_method, execution_method),
        calculations=(
            execution_calculation,
            dos_source.calculation,
            *(item.calculation for item in parsed_sources),
        ),
        execution_attempts=(
            execution_attempt,
            dos_source.attempt,
            *(item.attempt for item in parsed_sources),
        ),
        remote_jobs=(remote_job,),
        artifacts=(
            dos_source.doscar_artifact,
            dos_source.atom_map_artifact,
            *(item.raw_artifact for item in parsed_sources),
            *(item.parsed_artifact for item in parsed_sources),
        ),
        analyses=tuple(item.parse_analysis for item in parsed_sources),
        provenance_records=tuple(
            record for item in parsed_sources for record in item.provenance_records
        ),
        dependency_records=tuple(
            record for item in parsed_sources for record in item.dependency_records
        ),
    )
    bundle.validate()
    store = ProjectStore(root)
    store.save(bundle)
    reopened = store.open()
    if reopened.project.id != project.id:
        raise RuntimeError("fixture ProjectStore changed permanent Project identity")

    h2_uids = tuple(str(site.atom_uid) for site in h2_snapshot.sites)
    if len(h2_uids) != 2:
        raise RuntimeError("H2 installed acceptance fixture requires exactly two atom UIDs")
    return InstalledAcceptanceFixture(
        project_root=root,
        project_id=str(project.id),
        execution_calculation_id=str(execution_calculation.id),
        execution_attempt_id=str(execution_attempt.id),
        remote_job_id=str(remote_job.id),
        dos_calculation_id=str(dos_source.calculation.id),
        dos_atom_uid=str(clean_snapshot.sites[0].atom_uid),
        clean_frequency_calculation_id=str(clean_source.calculation.id),
        ads_frequency_calculation_id=str(ads_source.calculation.id),
        h2_frequency_calculation_id=str(h2_source.calculation.id),
        h2_atom_uids=(h2_uids[0], h2_uids[1]),
        doscar_path=root / (dos_source.doscar_artifact.local_path or ""),
        ads_outcar_path=root / (ads_source.raw_artifact.local_path or ""),
    )


def _method(
    *,
    recipe_id: str,
    elements: tuple[str, ...],
    spin_treatment: SpinTreatment = SpinTreatment.UNPOLARIZED,
) -> MethodFingerprint:
    return MethodFingerprint(
        method=MethodDefinition(
            xc_functional="PBE",
            potcar_family="PBE_54",
            potcars=tuple(
                PotcarIdentity(
                    element=element,
                    symbol=element,
                    sha256=hashlib.sha256(f"{recipe_id}:{element}".encode()).hexdigest(),
                )
                for element in elements
            ),
            engine_version="6.5.1",
            spin_treatment=spin_treatment,
        ),
        protocol=ProtocolDefinition(
            encut_ev=450.0,
            kpoints=KPointPolicy(KPointPolicyKind.GAMMA_ONLY),
        ),
        recipe=RecipeIdentity(recipe_id),
    )


def _surface_snapshot(*, with_hydrogen: bool) -> StructureSnapshot:
    sites = [StructureSite(new_atom_uid(), "C", (0.5, 0.5, 0.5))]
    if with_hydrogen:
        sites.append(StructureSite(new_atom_uid(), "H", (0.5, 0.5, 0.58)))
    return StructureSnapshot(
        lattice=Lattice(
            vectors=((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 18.0))
        ),
        sites=tuple(sites),
        periodic=(True, True, False),
    )


def _h2_snapshot() -> StructureSnapshot:
    return StructureSnapshot(
        lattice=Lattice(
            vectors=((20.0, 0.0, 0.0), (0.0, 20.0, 0.0), (0.0, 0.0, 20.0))
        ),
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
    if len(wavenumbers) != 3 * len(atom_uids):
        raise RuntimeError("frequency acceptance fixture requires exactly 3N modes")
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
    parsed_relative = (
        Path("calculations") / str(calculation.id) / "scientific" / "parsed.json"
    )
    parsed_body = (
        canonical_json(
            {
                "format": VASP_RESULT_DOCUMENT_FORMAT,
                "version": VASP_RESULT_DOCUMENT_VERSION,
                "calculation_id": calculation.id,
                "analysis_id": parse_analysis.id,
                "intake_hash": intake_hash,
                "result": result,
            }
        )
        + "\n"
    ).encode()
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
    provenance_records = (
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
    dependency_records = (
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
        provenance_records=provenance_records,
        dependency_records=dependency_records,
    )


def _dos_source(
    *,
    root: Path,
    project: Project,
    snapshot: StructureSnapshot,
    method: MethodFingerprint,
) -> _DosSource:
    calculation = Calculation(
        project_id=project.id,
        calculation_type=CalculationType.DOS_STATIC,
        input_structure_snapshot_id=snapshot.id,
        recipe_id=method.recipe.recipe_id,
        method_fingerprint_id=method.id,
        status=CalculationScientificStatus.CONVERGED,
        slug="installed-dos-source",
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
                "atom_uid": str(snapshot.sites[0].atom_uid),
                "element": "C",
                "snapshot_index": 0,
                "poscar_index": 0,
                "vasp_ordinal": 1,
                "selective_dynamics": None,
            }
        ],
    }
    atom_map_body = (canonical_json(atom_map_payload) + "\n").encode()
    atom_map_relative = (
        Path("calculations") / str(calculation.id) / "input" / "atom-index-map.json"
    )
    atom_map_path = root / atom_map_relative
    atom_map_path.parent.mkdir(parents=True, exist_ok=True)
    atom_map_path.write_bytes(atom_map_body)
    atom_map_artifact = Artifact(
        artifact_type=ArtifactType.DERIVED_DATASET,
        producer=CalculationProducerRef(calculation.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=atom_map_relative.as_posix(),
        size_bytes=len(atom_map_body),
        sha256=hashlib.sha256(atom_map_body).hexdigest(),
    )

    doscar_body = (
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
    doscar_relative = Path("calculations") / str(calculation.id) / "attempt-1" / "DOSCAR"
    doscar_path = root / doscar_relative
    doscar_path.parent.mkdir(parents=True, exist_ok=True)
    doscar_path.write_bytes(doscar_body)
    doscar_artifact = Artifact(
        artifact_type=ArtifactType.DOSCAR,
        producer=ExecutionAttemptProducerRef(attempt.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=doscar_relative.as_posix(),
        size_bytes=len(doscar_body),
        sha256=hashlib.sha256(doscar_body).hexdigest(),
    )
    return _DosSource(
        calculation=calculation,
        attempt=attempt,
        doscar_artifact=doscar_artifact,
        atom_map_artifact=atom_map_artifact,
    )


__all__ = ["InstalledAcceptanceFixture", "build_installed_acceptance_fixture"]
