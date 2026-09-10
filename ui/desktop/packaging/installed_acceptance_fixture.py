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
    ExecutionSettings,
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
    new_artifact_id,
    new_atom_uid,
)
from ecatvasp.provenance import (
    DependencyKind,
    DependencyRecord,
    ProvenanceRecord,
    scientific_hash,
)
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.vasp import (
    RECIPE_FULL_FREQUENCY,
    RECIPE_GAS_FREQUENCY,
    ExecutionPlan,
    ExpectedOutput,
    LatticeAxis,
    PotcarResolutionEntry,
    PotcarResolutionRequest,
    StagingInput,
    StagingInputKind,
    VaspRuntimeConstraints,
    VaspSystemContext,
    VaspSystemKind,
    build_vasp_result_artifact_intake,
    frequency_recipe_parameters,
    parse_vasp_energy_metadata,
    parse_vasp_frequency_results,
)
from ecatvasp.vasp.results import (
    VASP_RESULT_DOCUMENT_FORMAT,
    VASP_RESULT_DOCUMENT_VERSION,
    VaspFrequencyEigenvector,
    VaspFrequencyMode,
    VaspFrequencyModeKind,
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
    artifacts: tuple[Artifact, ...]
    analyses: tuple[Analysis, ...]
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
        recipe_id=RECIPE_FULL_FREQUENCY,
        elements=("C", "H"),
    )
    gas_method = _method(recipe_id=RECIPE_GAS_FREQUENCY, elements=("H",))
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
            *(artifact for item in parsed_sources for artifact in item.artifacts),
        ),
        analyses=tuple(
            analysis for item in parsed_sources for analysis in item.analyses
        ),
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
        recipe=RecipeIdentity(
            recipe_id,
            parameters=(
                frequency_recipe_parameters(potim_angstrom=0.015)
                if recipe_id in {RECIPE_FULL_FREQUENCY, RECIPE_GAS_FREQUENCY}
                else ()
            ),
        ),
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


def _frequency_outcar(
    *,
    energy_ev: float,
    wavenumbers: tuple[float, ...],
    atom_count: int,
) -> bytes:
    lines = [
        "vasp.6.5.1 installed acceptance\n",
        f" free energy TOTEN = {energy_ev + 0.2:.8f} eV\n",
        (
            f" energy without entropy = {energy_ev + 0.1:.8f} "
            f"energy(sigma->0) = {energy_ev:.8f}\n"
        ),
        " aborting loop because EDIFF is reached\n",
        " General timing and accounting informations for this job:\n",
        " Eigenvectors and eigenvalues of the dynamical matrix\n",
        " ----------------------------------------------------\n",
    ]
    for mode_index, wavenumber in enumerate(wavenumbers, start=1):
        frequency_thz = wavenumber / 33.3564095
        angular_frequency = frequency_thz * 6.283185307179586
        energy_mev = wavenumber * 0.123984198433
        lines.append(
            f" {mode_index:3d} f = {frequency_thz:.9f} THz "
            f"{angular_frequency:.9f} 2PiTHz "
            f"{wavenumber:.9f} cm-1 {energy_mev:.9f} meV\n"
        )
        lines.append(" X         Y         Z           dx          dy          dz\n")
        for atom_index in range(atom_count):
            scale = float((mode_index * 10) + atom_index + 1) / 100.0
            lines.append(
                f" 0.000000 0.000000 0.000000 {scale:.6f} "
                f"{scale + 0.01:.6f} {scale + 0.02:.6f}\n"
            )
    return "".join(lines).encode("utf-8")


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

    input_root = Path("calculations") / str(calculation.id) / "input"
    poscar_relative = input_root / "POSCAR"
    poscar_body = (
        "installed frequency prerequisite\n"
        "1.0\n"
        "10.0 0.0 0.0\n"
        "0.0 10.0 0.0\n"
        "0.0 0.0 20.0\n"
        + " ".join(site.element for site in snapshot.sites)
        + "\n"
    ).encode("utf-8")
    poscar_path = root / poscar_relative
    poscar_path.parent.mkdir(parents=True, exist_ok=True)
    poscar_path.write_bytes(poscar_body)
    poscar_artifact = Artifact(
        artifact_type=ArtifactType.POSCAR,
        producer=CalculationProducerRef(calculation.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=poscar_relative.as_posix(),
        size_bytes=len(poscar_body),
        sha256=hashlib.sha256(poscar_body).hexdigest(),
    )

    species_order: list[str] = []
    for site in snapshot.sites:
        if site.element not in species_order:
            species_order.append(site.element)
    species_counts = [
        sum(site.element == element for site in snapshot.sites)
        for element in species_order
    ]
    atom_map_payload = {
        "format": "ecatvasp-v03-atom-index-map",
        "version": 1,
        "structure_snapshot_id": str(snapshot.id),
        "structure_sha256": scientific_hash(snapshot),
        "poscar_sha256": poscar_artifact.sha256,
        "species_order": species_order,
        "species_counts": species_counts,
        "entries": [
            {
                "atom_uid": str(site.atom_uid),
                "element": site.element,
                "snapshot_index": index,
                "poscar_index": index,
                "vasp_ordinal": index + 1,
                "selective_dynamics": None,
            }
            for index, site in enumerate(snapshot.sites)
        ],
    }
    atom_map_body = (canonical_json(atom_map_payload) + "\n").encode("utf-8")
    atom_map_relative = input_root / "atom-index-map.json"
    atom_map_path = root / atom_map_relative
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

    plan = ExecutionPlan(
        calculation_id=calculation.id,
        recipe_id=calculation.recipe_id,
        system_context=(
            VaspSystemContext(VaspSystemKind.MOLECULE_0D)
            if calculation_type is CalculationType.GAS_FREQUENCY
            else VaspSystemContext(VaspSystemKind.SLAB_2D, vacuum_axis=LatticeAxis.C)
        ),
        input_manifest_artifact_id=new_artifact_id(),
        input_manifest_sha256=canonical_sha256(
            {"calculation_id": calculation.id, "fixture": "installed-frequency"}
        ),
        preparation_hash=canonical_sha256(
            {"calculation_id": calculation.id, "preparation": "installed-frequency"}
        ),
        staging_inputs=(
            StagingInput(
                role="atom_index_map",
                kind=StagingInputKind.METADATA,
                artifact_id=atom_map_artifact.id,
                artifact_type=ArtifactType.DERIVED_DATASET,
                source_relative_path=atom_map_relative.as_posix(),
                target_relative_path="atom-index-map.json",
                sha256=atom_map_artifact.sha256 or "",
                size_bytes=atom_map_artifact.size_bytes or 0,
            ),
            StagingInput(
                role="poscar",
                kind=StagingInputKind.VASP_INPUT,
                artifact_id=poscar_artifact.id,
                artifact_type=ArtifactType.POSCAR,
                source_relative_path=poscar_relative.as_posix(),
                target_relative_path="POSCAR",
                sha256=poscar_artifact.sha256 or "",
                size_bytes=poscar_artifact.size_bytes or 0,
            ),
        ),
        potcar_resolution=PotcarResolutionRequest(
            family=method.method.potcar_family,
            core_method_hash=method.core_method_hash,
            metadata_hash=canonical_sha256(method.method.potcars),
            entries=tuple(
                PotcarResolutionEntry(item.element, item.symbol, item.sha256)
                for item in method.method.potcars
            ),
        ),
        expected_outputs=(
            ExpectedOutput(
                role="outcar",
                artifact_type=ArtifactType.OUTCAR,
                relative_path="OUTCAR",
                retrieval_policy=RetrievalPolicy.ALWAYS,
                required=True,
            ),
        ),
        runtime_constraints=VaspRuntimeConstraints(),
        execution_settings=ExecutionSettings(),
    )
    attempt = ExecutionAttempt(
        calculation_id=calculation.id,
        attempt_number=1,
        status=ExecutionAttemptStatus.PARSED,
        input_manifest_hash=plan.input_manifest_sha256,
        execution_plan_hash=plan.plan_hash,
    )

    plan_relative = (
        Path("calculations") / str(calculation.id) / "attempt-1" / "execution-plan.json"
    )
    plan_body = (
        canonical_json(
            {"schema_version": 1, "plan_hash": plan.plan_hash, "plan": plan}
        )
        + "\n"
    ).encode("utf-8")
    plan_path = root / plan_relative
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_bytes(plan_body)
    plan_artifact = Artifact(
        artifact_type=ArtifactType.EXECUTION_PLAN,
        producer=ExecutionAttemptProducerRef(attempt.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=plan_relative.as_posix(),
        size_bytes=len(plan_body),
        sha256=hashlib.sha256(plan_body).hexdigest(),
    )

    raw_relative = Path("calculations") / str(calculation.id) / "attempt-1" / "OUTCAR"
    raw_body = _frequency_outcar(
        energy_ev=energy_ev,
        wavenumbers=wavenumbers,
        atom_count=len(snapshot.sites),
    )
    raw_path = root / raw_relative
    raw_path.write_bytes(raw_body)
    raw_artifact = Artifact(
        artifact_type=ArtifactType.OUTCAR,
        producer=ExecutionAttemptProducerRef(attempt.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        local_path=raw_relative.as_posix(),
        size_bytes=len(raw_body),
        sha256=hashlib.sha256(raw_body).hexdigest(),
    )

    intake = build_vasp_result_artifact_intake(
        project_root=root,
        calculation=calculation,
        plan=plan,
        attempt=attempt,
        artifacts=(raw_artifact,),
    )
    result = parse_vasp_energy_metadata(project_root=root, intake=intake)
    result = parse_vasp_frequency_results(
        project_root=root,
        calculation=calculation,
        fingerprint=method,
        plan=plan,
        intake=intake,
        input_snapshot=snapshot,
        result=result,
    )
    parse_analysis = Analysis(
        project_id=project.id,
        analysis_type=AnalysisType.RESULT_PARSE,
        input_artifact_ids=intake.input_artifact_ids,
        status=AnalysisStatus.COMPLETED,
        tool="ecatvasp.vasp.scientific-result-pipeline",
        tool_version="1",
        parameters_hash=intake.intake_hash,
    )
    parsed_relative = (
        Path("calculations") / str(calculation.id) / "scientific" / "parsed-result.json"
    )
    parsed_body = (
        canonical_json(
            {
                "format": VASP_RESULT_DOCUMENT_FORMAT,
                "version": VASP_RESULT_DOCUMENT_VERSION,
                "calculation_id": calculation.id,
                "analysis_id": parse_analysis.id,
                "intake_hash": intake.intake_hash,
                "result": result,
            }
        )
        + "\n"
    ).encode("utf-8")
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
        artifacts=(
            poscar_artifact,
            atom_map_artifact,
            plan_artifact,
            raw_artifact,
            parsed_artifact,
        ),
        analyses=(parse_analysis,),
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
