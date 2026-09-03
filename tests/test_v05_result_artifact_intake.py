from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from ecatvasp.domain import (
    Artifact,
    ArtifactAvailability,
    ArtifactType,
    Calculation,
    CalculationType,
    ExecutionAttempt,
    ExecutionAttemptProducerRef,
    ExecutionAttemptStatus,
    ExecutionSettings,
    Project,
    RetrievalPolicy,
    new_execution_attempt_id,
    new_method_fingerprint_id,
    new_structure_snapshot_id,
)
from ecatvasp.domain.ids import new_artifact_id
from ecatvasp.vasp import (
    ExecutionPlan,
    ExpectedOutput,
    PotcarResolutionEntry,
    PotcarResolutionRequest,
    VaspResultIntakeError,
    VaspResultSourceRole,
    VaspRuntimeConstraints,
    VaspSystemContext,
    VaspSystemKind,
    build_vasp_result_artifact_intake,
)


def _calculation(
    *,
    calculation_type: CalculationType = CalculationType.STATIC,
) -> Calculation:
    project = Project(name="v0.5 result intake", slug="v05-result-intake")
    recipe_id = (
        "ECatVASP.VASP.AdsorbateRelax"
        if calculation_type is CalculationType.RELAX
        else "ECatVASP.VASP.GroundStateStatic"
    )
    return Calculation(
        project_id=project.id,
        calculation_type=calculation_type,
        input_structure_snapshot_id=new_structure_snapshot_id(),
        recipe_id=recipe_id,
        method_fingerprint_id=new_method_fingerprint_id(),
    )


def _plan(
    calculation: Calculation,
    *,
    include_vasprun: bool = False,
    malformed_outcar_type: bool = False,
) -> ExecutionPlan:
    outputs = [
        ExpectedOutput(
            "oszicar",
            ArtifactType.OSZICAR,
            "OSZICAR",
            RetrievalPolicy.ALWAYS,
            False,
        ),
        ExpectedOutput(
            "outcar",
            ArtifactType.OSZICAR if malformed_outcar_type else ArtifactType.OUTCAR,
            "OUTCAR",
            RetrievalPolicy.ALWAYS,
            True,
        ),
    ]
    if calculation.calculation_type is CalculationType.RELAX:
        outputs.append(
            ExpectedOutput(
                "contcar",
                ArtifactType.CONTCAR,
                "CONTCAR",
                RetrievalPolicy.ALWAYS,
                True,
            )
        )
    if include_vasprun:
        outputs.append(
            ExpectedOutput(
                "vasprun_xml",
                ArtifactType.VASPRUN_XML,
                "vasprun.xml",
                RetrievalPolicy.ON_DEMAND,
                False,
            )
        )
    return ExecutionPlan(
        calculation_id=calculation.id,
        recipe_id=calculation.recipe_id,
        system_context=VaspSystemContext(VaspSystemKind.PERIODIC_3D),
        input_manifest_artifact_id=new_artifact_id(),
        input_manifest_sha256="a" * 64,
        preparation_hash="b" * 64,
        staging_inputs=(),
        potcar_resolution=PotcarResolutionRequest(
            family="PBE_54",
            core_method_hash="c" * 64,
            metadata_hash="d" * 64,
            entries=(PotcarResolutionEntry("Pb", "Pb_d", "e" * 64),),
        ),
        expected_outputs=tuple(sorted(outputs, key=lambda item: item.role)),
        runtime_constraints=VaspRuntimeConstraints(),
        execution_settings=ExecutionSettings(),
    )


def _attempt(
    calculation: Calculation,
    plan: ExecutionPlan,
    *,
    status: ExecutionAttemptStatus = ExecutionAttemptStatus.EXITED,
) -> ExecutionAttempt:
    return ExecutionAttempt(
        calculation_id=calculation.id,
        attempt_number=1,
        status=status,
        input_manifest_hash=plan.input_manifest_sha256,
        execution_plan_hash=plan.plan_hash,
    )


def _local_artifact(
    *,
    root: Path,
    attempt: ExecutionAttempt,
    artifact_type: ArtifactType,
    filename: str,
    body: bytes,
    retrieval_policy: RetrievalPolicy = RetrievalPolicy.ALWAYS,
) -> Artifact:
    relative = Path("artifacts") / "execution" / str(attempt.id) / "outputs" / filename
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return Artifact(
        artifact_type=artifact_type,
        producer=ExecutionAttemptProducerRef(attempt.id),
        availability=ArtifactAvailability.LOCAL,
        retrieval_policy=retrieval_policy,
        local_path=relative.as_posix(),
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
    )


def test_static_intake_accepts_exact_verified_sources(tmp_path: Path) -> None:
    calculation = _calculation()
    plan = _plan(calculation)
    attempt = _attempt(calculation, plan)
    outcar = _local_artifact(
        root=tmp_path,
        attempt=attempt,
        artifact_type=ArtifactType.OUTCAR,
        filename="OUTCAR",
        body=b"outcar-body\n",
    )
    oszicar = _local_artifact(
        root=tmp_path,
        attempt=attempt,
        artifact_type=ArtifactType.OSZICAR,
        filename="OSZICAR",
        body=b"1 F= -.1\n",
    )

    intake = build_vasp_result_artifact_intake(
        project_root=tmp_path,
        calculation=calculation,
        plan=plan,
        attempt=attempt,
        artifacts=(oszicar, outcar),
    )

    assert tuple(source.role for source in intake.sources) == (
        VaspResultSourceRole.OSZICAR,
        VaspResultSourceRole.OUTCAR,
    )
    assert intake.input_artifact_ids == tuple(source.artifact_id for source in intake.sources)
    assert len(intake.intake_hash) == 64
    assert intake.plan_hash == plan.plan_hash


def test_intake_rejects_file_content_drift(tmp_path: Path) -> None:
    calculation = _calculation()
    plan = _plan(calculation)
    attempt = _attempt(calculation, plan)
    outcar = _local_artifact(
        root=tmp_path,
        attempt=attempt,
        artifact_type=ArtifactType.OUTCAR,
        filename="OUTCAR",
        body=b"OUTCAR-A\n",
    )
    assert outcar.local_path is not None
    (tmp_path / outcar.local_path).write_bytes(b"OUTCAR-B\n")

    with pytest.raises(VaspResultIntakeError, match="SHA-256 changed"):
        build_vasp_result_artifact_intake(
            project_root=tmp_path,
            calculation=calculation,
            plan=plan,
            attempt=attempt,
            artifacts=(outcar,),
        )


def test_intake_rejects_foreign_attempt_artifact(tmp_path: Path) -> None:
    calculation = _calculation()
    plan = _plan(calculation)
    attempt = _attempt(calculation, plan)
    outcar = _local_artifact(
        root=tmp_path,
        attempt=attempt,
        artifact_type=ArtifactType.OUTCAR,
        filename="OUTCAR",
        body=b"outcar\n",
    )
    foreign = replace(
        outcar,
        producer=ExecutionAttemptProducerRef(new_execution_attempt_id()),
    )

    with pytest.raises(VaspResultIntakeError, match="exact ExecutionAttempt"):
        build_vasp_result_artifact_intake(
            project_root=tmp_path,
            calculation=calculation,
            plan=plan,
            attempt=attempt,
            artifacts=(foreign,),
        )


def test_intake_rejects_unpinned_or_active_attempt(tmp_path: Path) -> None:
    calculation = _calculation()
    plan = _plan(calculation)
    attempt = _attempt(calculation, plan)
    outcar = _local_artifact(
        root=tmp_path,
        attempt=attempt,
        artifact_type=ArtifactType.OUTCAR,
        filename="OUTCAR",
        body=b"outcar\n",
    )

    with pytest.raises(VaspResultIntakeError, match="does not pin"):
        build_vasp_result_artifact_intake(
            project_root=tmp_path,
            calculation=calculation,
            plan=plan,
            attempt=replace(attempt, execution_plan_hash="f" * 64),
            artifacts=(outcar,),
        )

    with pytest.raises(VaspResultIntakeError, match="not parse-ready"):
        build_vasp_result_artifact_intake(
            project_root=tmp_path,
            calculation=calculation,
            plan=plan,
            attempt=replace(attempt, status=ExecutionAttemptStatus.RUNNING),
            artifacts=(outcar,),
        )


def test_relaxation_requires_local_contcar(tmp_path: Path) -> None:
    calculation = _calculation(calculation_type=CalculationType.RELAX)
    plan = _plan(calculation)
    attempt = _attempt(calculation, plan)
    outcar = _local_artifact(
        root=tmp_path,
        attempt=attempt,
        artifact_type=ArtifactType.OUTCAR,
        filename="OUTCAR",
        body=b"outcar\n",
    )

    with pytest.raises(VaspResultIntakeError, match="contcar"):
        build_vasp_result_artifact_intake(
            project_root=tmp_path,
            calculation=calculation,
            plan=plan,
            attempt=attempt,
            artifacts=(outcar,),
        )

    contcar = _local_artifact(
        root=tmp_path,
        attempt=attempt,
        artifact_type=ArtifactType.CONTCAR,
        filename="CONTCAR",
        body=b"Pb relaxed structure\n",
    )
    intake = build_vasp_result_artifact_intake(
        project_root=tmp_path,
        calculation=calculation,
        plan=plan,
        attempt=attempt,
        artifacts=(outcar, contcar),
    )
    assert {source.role for source in intake.sources} == {
        VaspResultSourceRole.OUTCAR,
        VaspResultSourceRole.CONTCAR,
    }


def test_optional_remote_or_missing_sources_are_not_guessed(tmp_path: Path) -> None:
    calculation = _calculation()
    plan = _plan(calculation, include_vasprun=True)
    attempt = _attempt(calculation, plan)
    outcar = _local_artifact(
        root=tmp_path,
        attempt=attempt,
        artifact_type=ArtifactType.OUTCAR,
        filename="OUTCAR",
        body=b"outcar\n",
    )
    oszicar = Artifact(
        artifact_type=ArtifactType.OSZICAR,
        producer=ExecutionAttemptProducerRef(attempt.id),
        availability=ArtifactAvailability.MISSING,
        retrieval_policy=RetrievalPolicy.ALWAYS,
    )
    vasprun = Artifact(
        artifact_type=ArtifactType.VASPRUN_XML,
        producer=ExecutionAttemptProducerRef(attempt.id),
        availability=ArtifactAvailability.REMOTE,
        retrieval_policy=RetrievalPolicy.ON_DEMAND,
        remote_path=f"execution/{attempt.id}/vasprun.xml",
        size_bytes=12,
        sha256="1" * 64,
    )

    intake = build_vasp_result_artifact_intake(
        project_root=tmp_path,
        calculation=calculation,
        plan=plan,
        attempt=attempt,
        artifacts=(outcar, oszicar, vasprun),
    )
    assert tuple(source.role for source in intake.sources) == (
        VaspResultSourceRole.OUTCAR,
    )


def test_optional_declared_vasprun_is_included_only_after_local_verification(
    tmp_path: Path,
) -> None:
    calculation = _calculation()
    plan = _plan(calculation, include_vasprun=True)
    attempt = _attempt(calculation, plan)
    outcar = _local_artifact(
        root=tmp_path,
        attempt=attempt,
        artifact_type=ArtifactType.OUTCAR,
        filename="OUTCAR",
        body=b"outcar\n",
    )
    vasprun = _local_artifact(
        root=tmp_path,
        attempt=attempt,
        artifact_type=ArtifactType.VASPRUN_XML,
        filename="vasprun.xml",
        body=b"<modeling/>\n",
        retrieval_policy=RetrievalPolicy.ON_DEMAND,
    )

    intake = build_vasp_result_artifact_intake(
        project_root=tmp_path,
        calculation=calculation,
        plan=plan,
        attempt=attempt,
        artifacts=(outcar, vasprun),
    )
    assert {source.role for source in intake.sources} == {
        VaspResultSourceRole.OUTCAR,
        VaspResultSourceRole.VASPRUN_XML,
    }


def test_failed_attempt_with_outcar_can_be_parsed_as_evidence(tmp_path: Path) -> None:
    calculation = _calculation()
    plan = _plan(calculation)
    attempt = _attempt(calculation, plan, status=ExecutionAttemptStatus.FAILED)
    outcar = _local_artifact(
        root=tmp_path,
        attempt=attempt,
        artifact_type=ArtifactType.OUTCAR,
        filename="OUTCAR",
        body=b"partial outcar\n",
    )

    intake = build_vasp_result_artifact_intake(
        project_root=tmp_path,
        calculation=calculation,
        plan=plan,
        attempt=attempt,
        artifacts=(outcar,),
    )
    assert intake.attempt_id == attempt.id
    assert tuple(source.role for source in intake.sources) == (
        VaspResultSourceRole.OUTCAR,
    )


def test_required_remote_outcar_is_not_parse_ready(tmp_path: Path) -> None:
    calculation = _calculation()
    plan = _plan(calculation)
    attempt = _attempt(calculation, plan)
    outcar = Artifact(
        artifact_type=ArtifactType.OUTCAR,
        producer=ExecutionAttemptProducerRef(attempt.id),
        availability=ArtifactAvailability.REMOTE,
        retrieval_policy=RetrievalPolicy.ALWAYS,
        remote_path=f"execution/{attempt.id}/OUTCAR",
        size_bytes=7,
        sha256="2" * 64,
    )

    with pytest.raises(VaspResultIntakeError, match="not locally available"):
        build_vasp_result_artifact_intake(
            project_root=tmp_path,
            calculation=calculation,
            plan=plan,
            attempt=attempt,
            artifacts=(outcar,),
        )


def test_intake_rejects_policy_path_and_plan_semantic_mismatch(tmp_path: Path) -> None:
    calculation = _calculation()
    plan = _plan(calculation)
    attempt = _attempt(calculation, plan)
    outcar = _local_artifact(
        root=tmp_path,
        attempt=attempt,
        artifact_type=ArtifactType.OUTCAR,
        filename="OUTCAR",
        body=b"outcar\n",
    )

    with pytest.raises(VaspResultIntakeError, match="retrieval policy"):
        build_vasp_result_artifact_intake(
            project_root=tmp_path,
            calculation=calculation,
            plan=plan,
            attempt=attempt,
            artifacts=(replace(outcar, retrieval_policy=RetrievalPolicy.ON_DEMAND),),
        )

    unsafe = replace(outcar, local_path="../OUTCAR")
    with pytest.raises(VaspResultIntakeError, match="normalized relative POSIX path"):
        build_vasp_result_artifact_intake(
            project_root=tmp_path,
            calculation=calculation,
            plan=plan,
            attempt=attempt,
            artifacts=(unsafe,),
        )

    malformed = _plan(calculation, malformed_outcar_type=True)
    malformed_attempt = _attempt(calculation, malformed)
    with pytest.raises(VaspResultIntakeError, match="incompatible ArtifactType"):
        build_vasp_result_artifact_intake(
            project_root=tmp_path,
            calculation=calculation,
            plan=malformed,
            attempt=malformed_attempt,
            artifacts=(),
        )
