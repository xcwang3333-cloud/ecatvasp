from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from ecatvasp.domain import (
    ArtifactType,
    Calculation,
    CalculationType,
    ExecutionAttemptStatus,
    ExecutionSettings,
    ParameterEntry,
    Project,
    new_method_fingerprint_id,
    new_structure_snapshot_id,
)
from ecatvasp.domain.ids import new_artifact_id
from ecatvasp.execution import (
    ExecutionTargetProfile,
    LocalExecutor,
    LocalPotcarResolution,
    RuntimeMaterializationError,
    TransportKind,
    create_execution_attempt,
    materialize_local_runtime,
)
from ecatvasp.vasp.contracts import VaspSystemContext, VaspSystemKind
from ecatvasp.vasp.execution_plan import (
    ExecutionPlan,
    PotcarResolutionEntry,
    PotcarResolutionRequest,
    StagingInput,
    StagingInputKind,
    VaspRuntimeConstraints,
)
from ecatvasp.vasp.potcar import (
    PotcarSpec,
    PotcarSpecEntry,
    ResolvedPotcarFile,
    ResolvedPotcarSet,
)


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def _fixture(
    tmp_path: Path,
    *,
    executable: str = "vasp_std",
    incar_text: str = "ENCUT = 450\nEDIFF = 1E-5\n",
    settings: ExecutionSettings | None = None,
) -> tuple[
    Path,
    Calculation,
    ExecutionPlan,
    ExecutionTargetProfile,
    LocalPotcarResolution,
]:
    project_root = tmp_path / "project"
    project_root.mkdir()

    project = Project(name="Runtime", slug="runtime")
    calculation = Calculation(
        project_id=project.id,
        calculation_type=CalculationType.RELAX,
        input_structure_snapshot_id=new_structure_snapshot_id(),
        recipe_id="ECatVASP.VASP.AdsorbateRelax",
        method_fingerprint_id=new_method_fingerprint_id(),
    )

    source_files = {
        "inputs/INCAR": incar_text.encode(),
        "inputs/POSCAR": b"Pb\n1.0\n1 0 0\n0 1 0\n0 0 1\nPb\n1\nDirect\n0 0 0\n",
        "inputs/KPOINTS": b"Automatic mesh\n0\nGamma\n1 1 1\n0 0 0\n",
        "inputs/input-manifest.json": b"{}\n",
    }
    for relative, body in source_files.items():
        _write(project_root / relative, body)

    role_specs = (
        (
            "incar",
            StagingInputKind.VASP_INPUT,
            ArtifactType.INCAR,
            "inputs/INCAR",
            "INCAR",
        ),
        (
            "input_manifest",
            StagingInputKind.METADATA,
            ArtifactType.DERIVED_DATASET,
            "inputs/input-manifest.json",
            "input-manifest.json",
        ),
        (
            "kpoints",
            StagingInputKind.VASP_INPUT,
            ArtifactType.KPOINTS,
            "inputs/KPOINTS",
            "KPOINTS",
        ),
        (
            "poscar",
            StagingInputKind.VASP_INPUT,
            ArtifactType.POSCAR,
            "inputs/POSCAR",
            "POSCAR",
        ),
    )
    staging: list[StagingInput] = []
    manifest_artifact_id = None
    for role, kind, artifact_type, source, target in role_specs:
        artifact_id = new_artifact_id()
        if role == "input_manifest":
            manifest_artifact_id = artifact_id
        body = source_files[source]
        staging.append(
            StagingInput(
                role=role,
                kind=kind,
                artifact_id=artifact_id,
                artifact_type=artifact_type,
                source_relative_path=source,
                target_relative_path=target,
                sha256=_sha(body),
                size_bytes=len(body),
            )
        )
    assert manifest_artifact_id is not None

    potcar_body = b"licensed Pb_d POTCAR body\n"
    potcar_path = tmp_path / "licensed" / "Pb_d" / "POTCAR"
    _write(potcar_path, potcar_body)
    entry = PotcarSpecEntry(
        element="Pb",
        symbol="Pb_d",
        family="PBE_54",
        titel="PAW_PBE Pb_d",
        zval=14.0,
        enmax_ev=240.0,
        sha256=_sha(potcar_body),
    )
    spec_text = "Pb_d\n"
    spec = PotcarSpec(
        core_method_hash="c" * 64,
        entries=(entry,),
        text=spec_text,
        sha256=_sha(spec_text.encode()),
    )
    resolved = ResolvedPotcarSet(
        spec=spec,
        files=(ResolvedPotcarFile(path=potcar_path, entry=entry),),
    )
    request = PotcarResolutionRequest(
        family="PBE_54",
        core_method_hash=spec.core_method_hash,
        metadata_hash=spec.metadata_hash,
        entries=(PotcarResolutionEntry("Pb", "Pb_d", entry.sha256),),
    )

    execution_settings = settings or ExecutionSettings(
        ncore=2,
        kpar=1,
        omp_threads=4,
        executable=executable,
    )
    manifest_body = source_files["inputs/input-manifest.json"]
    plan = ExecutionPlan(
        calculation_id=calculation.id,
        recipe_id=calculation.recipe_id,
        system_context=VaspSystemContext(VaspSystemKind.PERIODIC_3D),
        input_manifest_artifact_id=manifest_artifact_id,
        input_manifest_sha256=_sha(manifest_body),
        preparation_hash="b" * 64,
        staging_inputs=tuple(staging),
        potcar_resolution=request,
        expected_outputs=(),
        runtime_constraints=VaspRuntimeConstraints(),
        execution_settings=execution_settings,
    )
    target = ExecutionTargetProfile(
        target_id="local-test",
        transport=TransportKind.LOCAL,
        potcar_resolver_id="local-pbe54",
        vasp_executable=executable,
    )
    potcars = LocalPotcarResolution(
        resolver_id="local-pbe54",
        resolved=resolved,
    )
    return project_root, calculation, plan, target, potcars


def _materialize(
    tmp_path: Path,
    *,
    executable: str = "vasp_std",
    incar_text: str = "ENCUT = 450\nEDIFF = 1E-5\n",
    settings: ExecutionSettings | None = None,
):
    project_root, calculation, plan, target, potcars = _fixture(
        tmp_path,
        executable=executable,
        incar_text=incar_text,
        settings=settings,
    )
    attempt = create_execution_attempt(plan=plan, calculation=calculation)
    package = materialize_local_runtime(
        project_root=project_root,
        run_directory=tmp_path / "scratch" / "run",
        plan=plan,
        calculation=calculation,
        attempt=attempt,
        target=target,
        potcars=potcars,
    )
    return project_root, calculation, plan, target, potcars, attempt, package


def test_local_runtime_preserves_inputs_and_potcar_boundary(tmp_path: Path) -> None:
    source_incar = "ENCUT = 450\nEDIFF = 1E-5\n"
    project_root, _, plan, _, _, attempt, package = _materialize(
        tmp_path,
        incar_text=source_incar,
    )

    assert (project_root / "inputs/INCAR").read_text() == source_incar
    runtime_incar = (package.run_directory / "INCAR").read_text()
    assert "NCORE = 2" in runtime_incar
    assert "KPAR = 1" in runtime_incar
    assert (package.run_directory / "POTCAR").read_bytes().startswith(b"licensed")
    assert not any(
        path.name == "POTCAR"
        for path in (project_root / package.artifact_directory_relative).iterdir()
    )
    assert package.attempt.status is ExecutionAttemptStatus.STAGING
    assert package.attempt.id == attempt.id
    assert package.attempt.execution_plan_hash == plan.plan_hash

    artifact_types = {artifact.artifact_type for artifact in package.artifacts}
    assert artifact_types == {
        ArtifactType.EXECUTION_PLAN,
        ArtifactType.INCAR,
        ArtifactType.DERIVED_DATASET,
    }
    assert all(artifact.producer.id == attempt.id for artifact in package.artifacts)

    manifest_text = package.manifest.text
    assert str(tmp_path / "licensed") not in manifest_text
    assert '"licensed":true' in manifest_text
    assert package.manifest.plan_hash == plan.plan_hash


def test_local_runtime_rejects_execution_keys_in_scientific_incar(tmp_path: Path) -> None:
    project_root, calculation, plan, target, potcars = _fixture(
        tmp_path,
        incar_text="ENCUT = 450\nNCORE = 2\n",
    )
    attempt = create_execution_attempt(plan=plan, calculation=calculation)

    with pytest.raises(RuntimeMaterializationError, match="execution-only keys"):
        materialize_local_runtime(
            project_root=project_root,
            run_directory=tmp_path / "scratch" / "run",
            plan=plan,
            calculation=calculation,
            attempt=attempt,
            target=target,
            potcars=potcars,
        )


def test_local_runtime_rejects_ncore_npar_conflict(tmp_path: Path) -> None:
    settings = ExecutionSettings(
        ncore=2,
        extra_parameters=(ParameterEntry("NPAR", 2),),
    )
    project_root, calculation, plan, target, potcars = _fixture(
        tmp_path,
        settings=settings,
    )
    attempt = create_execution_attempt(plan=plan, calculation=calculation)

    with pytest.raises(RuntimeMaterializationError, match="NCORE and NPAR"):
        materialize_local_runtime(
            project_root=project_root,
            run_directory=tmp_path / "scratch" / "run",
            plan=plan,
            calculation=calculation,
            attempt=attempt,
            target=target,
            potcars=potcars,
        )


def test_local_runtime_rejects_modified_licensed_potcar(tmp_path: Path) -> None:
    project_root, calculation, plan, target, potcars = _fixture(tmp_path)
    attempt = create_execution_attempt(plan=plan, calculation=calculation)
    potcars.resolved.files[0].path.write_bytes(b"changed licensed body\n")

    with pytest.raises(RuntimeMaterializationError, match="POTCAR hash mismatch"):
        materialize_local_runtime(
            project_root=project_root,
            run_directory=tmp_path / "scratch" / "run",
            plan=plan,
            calculation=calculation,
            attempt=attempt,
            target=target,
            potcars=potcars,
        )


def test_local_runtime_requires_transient_directory_outside_project(tmp_path: Path) -> None:
    project_root, calculation, plan, target, potcars = _fixture(tmp_path)
    attempt = create_execution_attempt(plan=plan, calculation=calculation)

    with pytest.raises(RuntimeMaterializationError, match="outside project_root"):
        materialize_local_runtime(
            project_root=project_root,
            run_directory=project_root / "runtime",
            plan=plan,
            calculation=calculation,
            attempt=attempt,
            target=target,
            potcars=potcars,
        )


def _fake_executable(path: Path, *, exit_code: int) -> None:
    path.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$OMP_NUM_THREADS"\n'
        'printf "fake stderr\\n" >&2\n'
        'printf "transient outcar\\n" > OUTCAR\n'
        f"exit {exit_code}\n"
    )
    path.chmod(0o755)


def test_local_executor_records_process_provenance_without_parsing_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = "fake_vasp"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_executable(bin_dir / executable, exit_code=0)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    project_root, _, _, _, _, _, package = _materialize(
        tmp_path,
        executable=executable,
    )
    result = LocalExecutor().execute(package)

    assert result.launched is True
    assert result.command_result is not None
    assert result.command_result.exit_code == 0
    assert result.command_result.stdout.strip() == "4"
    assert result.attempt.status is ExecutionAttemptStatus.EXITED
    assert result.attempt.started_at is not None
    assert result.attempt.finished_at is not None

    by_type = {artifact.artifact_type: artifact for artifact in result.artifacts}
    assert ArtifactType.STDOUT in by_type
    assert ArtifactType.STDERR in by_type
    assert ArtifactType.OUTCAR not in by_type
    assert (package.run_directory / "OUTCAR").is_file()
    stdout_path = by_type[ArtifactType.STDOUT].local_path
    assert stdout_path is not None
    assert (project_root / stdout_path).read_text().strip() == "4"


def test_local_executor_nonzero_exit_is_process_fact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = "fake_vasp"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_executable(bin_dir / executable, exit_code=7)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    *_, package = _materialize(tmp_path, executable=executable)
    result = LocalExecutor().execute(package)

    assert result.launched is True
    assert result.command_result is not None
    assert result.command_result.exit_code == 7
    assert result.attempt.status is ExecutionAttemptStatus.EXITED


def test_local_executor_launch_failure_marks_attempt_failed(tmp_path: Path) -> None:
    *_, package = _materialize(tmp_path, executable="missing_vasp_binary")
    result = LocalExecutor().execute(package)

    assert result.launched is False
    assert result.command_result is None
    assert result.attempt.status is ExecutionAttemptStatus.FAILED
    stderr_artifact = next(
        item for item in result.artifacts if item.artifact_type is ArtifactType.STDERR
    )
    assert stderr_artifact.local_path is not None
    assert (package.project_root / stderr_artifact.local_path).read_text()
