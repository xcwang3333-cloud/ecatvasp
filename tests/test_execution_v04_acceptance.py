from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from ecatvasp.domain import (
    ArtifactType,
    Calculation,
    CalculationType,
    ExecutionSettings,
    Project,
    RetrievalPolicy,
    new_artifact_id,
    new_method_fingerprint_id,
    new_structure_snapshot_id,
)
from ecatvasp.execution import (
    ExecutionHandoffSource,
    ExecutionTargetProfile,
    LocalExecutor,
    LocalPotcarResolution,
    TransportKind,
    build_local_execution_handoff,
    collect_local_outputs,
    create_execution_attempt,
    materialize_local_runtime,
)
from ecatvasp.vasp.contracts import VaspSystemContext, VaspSystemKind
from ecatvasp.vasp.execution_plan import (
    ExecutionPlan,
    ExpectedOutput,
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


def _acceptance_fixture(tmp_path: Path) -> tuple[
    Path,
    Calculation,
    ExecutionPlan,
    ExecutionTargetProfile,
    LocalPotcarResolution,
]:
    project_root = tmp_path / "project"
    project_root.mkdir()
    project = Project(name="v0.4 acceptance", slug="v04-acceptance")
    calculation = Calculation(
        project_id=project.id,
        calculation_type=CalculationType.RELAX,
        input_structure_snapshot_id=new_structure_snapshot_id(),
        recipe_id="ECatVASP.VASP.AdsorbateRelax",
        method_fingerprint_id=new_method_fingerprint_id(),
    )

    source_files = {
        "inputs/INCAR": b"ENCUT = 450\nEDIFF = 1E-5\n",
        "inputs/POSCAR": b"Pb\n1.0\n1 0 0\n0 1 0\n0 0 1\nPb\n1\nDirect\n0 0 0\n",
        "inputs/KPOINTS": b"Automatic mesh\n0\nGamma\n1 1 1\n0 0 0\n",
        "inputs/input-manifest.json": b"{}\n",
    }
    for relative, body in source_files.items():
        _write(project_root / relative, body)

    specs = (
        ("incar", StagingInputKind.VASP_INPUT, ArtifactType.INCAR, "inputs/INCAR", "INCAR"),
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
        ("poscar", StagingInputKind.VASP_INPUT, ArtifactType.POSCAR, "inputs/POSCAR", "POSCAR"),
    )
    staging: list[StagingInput] = []
    manifest_artifact_id = None
    for role, kind, artifact_type, source, target in specs:
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
    potcar_spec = PotcarSpec(
        core_method_hash="c" * 64,
        entries=(entry,),
        text=spec_text,
        sha256=_sha(spec_text.encode()),
    )
    resolved = ResolvedPotcarSet(
        spec=potcar_spec,
        files=(ResolvedPotcarFile(path=potcar_path, entry=entry),),
    )
    request = PotcarResolutionRequest(
        family="PBE_54",
        core_method_hash=potcar_spec.core_method_hash,
        metadata_hash=potcar_spec.metadata_hash,
        entries=(PotcarResolutionEntry("Pb", "Pb_d", entry.sha256),),
    )

    plan = ExecutionPlan(
        calculation_id=calculation.id,
        recipe_id=calculation.recipe_id,
        system_context=VaspSystemContext(VaspSystemKind.PERIODIC_3D),
        input_manifest_artifact_id=manifest_artifact_id,
        input_manifest_sha256=_sha(source_files["inputs/input-manifest.json"]),
        preparation_hash="b" * 64,
        staging_inputs=tuple(staging),
        potcar_resolution=request,
        expected_outputs=(
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
        runtime_constraints=VaspRuntimeConstraints(),
        execution_settings=ExecutionSettings(
            ncore=2,
            kpar=1,
            omp_threads=2,
            executable="fake_vasp",
        ),
    )
    target = ExecutionTargetProfile(
        target_id="local-acceptance",
        transport=TransportKind.LOCAL,
        potcar_resolver_id="local-pbe54",
        vasp_executable="fake_vasp",
    )
    potcars = LocalPotcarResolution(
        resolver_id="local-pbe54",
        resolved=resolved,
    )
    return project_root, calculation, plan, target, potcars


def _fake_vasp(path: Path) -> None:
    path.write_text(
        "#!/bin/sh\n"
        'printf "OUTCAR acceptance\\n" > OUTCAR\n'
        'printf "CONTCAR acceptance\\n" > CONTCAR\n'
        'printf "OSZICAR acceptance\\n" > OSZICAR\n'
        'printf "%s\\n" "$OMP_NUM_THREADS"\n'
        "exit 0\n"
    )
    path.chmod(0o755)


def test_v04_local_execution_acceptance_reaches_final_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, calculation, plan, target, potcars = _acceptance_fixture(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_vasp(bin_dir / "fake_vasp")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    attempt = create_execution_attempt(plan=plan, calculation=calculation)
    runtime = materialize_local_runtime(
        project_root=project_root,
        run_directory=tmp_path / "scratch" / "run",
        plan=plan,
        calculation=calculation,
        attempt=attempt,
        target=target,
        potcars=potcars,
    )
    result = LocalExecutor().execute(runtime)
    outputs = collect_local_outputs(
        project_root=project_root,
        calculation=calculation,
        plan=plan,
        runtime=runtime,
        result=result,
    )
    handoff = build_local_execution_handoff(
        calculation=calculation,
        plan=plan,
        runtime=runtime,
        result=result,
        outputs=outputs,
    )

    assert handoff.source is ExecutionHandoffSource.LOCAL
    assert handoff.execution_attempt_id == attempt.id
    assert handoff.plan_hash == plan.plan_hash
    assert handoff.process_exit_code == 0
    assert len(handoff.locally_available_output_artifact_ids) == 3
    assert (project_root / "inputs/INCAR").read_text() == "ENCUT = 450\nEDIFF = 1E-5\n"
    runtime_incar = (runtime.run_directory / "INCAR").read_text()
    assert "NCORE = 2" in runtime_incar
    assert "KPAR = 1" in runtime_incar

    artifact_directory = project_root / runtime.artifact_directory_relative
    assert not any(path.name == "POTCAR" for path in artifact_directory.rglob("*"))
    persisted = {item.artifact_type: item for item in outputs.output_artifacts}
    assert (project_root / persisted[ArtifactType.OUTCAR].local_path).is_file()
    assert (project_root / persisted[ArtifactType.CONTCAR].local_path).is_file()
    assert (project_root / persisted[ArtifactType.OSZICAR].local_path).is_file()
