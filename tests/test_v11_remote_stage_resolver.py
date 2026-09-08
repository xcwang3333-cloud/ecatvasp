from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ecatvasp.api.application import ApplicationServiceError
from ecatvasp.api.remote_stage_resolver import resolve_remote_stage_package
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
    KPointPolicy,
    KPointPolicyKind,
    Lattice,
    MethodDefinition,
    MethodFingerprint,
    PotcarIdentity,
    Project,
    ProtocolDefinition,
    RecipeIdentity,
    SchedulerType,
    StructureSite,
    StructureSnapshot,
    canonical_json,
    new_artifact_id,
    new_atom_uid,
)
from ecatvasp.execution.remote import RemoteStageManifest
from ecatvasp.execution.targets import (
    ExecutionTargetProfile,
    SshSecurityPolicy,
    TransportKind,
)
from ecatvasp.storage import ProjectBundle, ProjectStore
from ecatvasp.vasp.contracts import VaspSystemContext, VaspSystemKind
from ecatvasp.vasp.execution_plan import (
    ExecutionPlan,
    PotcarResolutionEntry,
    PotcarResolutionRequest,
    VaspRuntimeConstraints,
)


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _target(name: str) -> ExecutionTargetProfile:
    return ExecutionTargetProfile(
        target_id=name,
        transport=TransportKind.SSH,
        scheduler=SchedulerType.SLURM,
        host_alias=name,
        remote_work_root="/scratch/ecatvasp",
        potcar_resolver_id="pbe54-remote",
        vasp_executable="vasp_std",
        launcher="srun",
        ssh_security=SshSecurityPolicy(),
    )


def _case(tmp_path: Path):
    project = Project(name="Stage resume", slug="stage-resume")
    snapshot = StructureSnapshot(
        lattice=Lattice(
            vectors=((8.0, 0.0, 0.0), (0.0, 8.0, 0.0), (0.0, 0.0, 15.0))
        ),
        sites=(StructureSite(new_atom_uid(), "C", (0.5, 0.5, 0.5)),),
    )
    fingerprint = MethodFingerprint(
        method=MethodDefinition(
            xc_functional="PBE",
            potcar_family="PBE_54",
            potcars=(PotcarIdentity("C", "C", "1" * 64),),
        ),
        protocol=ProtocolDefinition(
            encut_ev=450.0,
            kpoints=KPointPolicy(KPointPolicyKind.GAMMA_ONLY),
            ediffg_ev_per_angstrom=-0.02,
        ),
        recipe=RecipeIdentity("ECatVASP.VASP.SlabRelax"),
    )
    calculation = Calculation(
        project_id=project.id,
        calculation_type=CalculationType.RELAX,
        input_structure_snapshot_id=snapshot.id,
        recipe_id=fingerprint.recipe.recipe_id,
        method_fingerprint_id=fingerprint.id,
    )
    plan = ExecutionPlan(
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
            entries=(PotcarResolutionEntry("C", "C", "1" * 64),),
        ),
        expected_outputs=(),
        runtime_constraints=VaspRuntimeConstraints(),
        execution_settings=ExecutionSettings(
            nodes=1,
            cores=1,
            mpi_ranks=1,
            walltime_seconds=3600,
        ),
    )
    attempt = ExecutionAttempt(
        calculation_id=calculation.id,
        attempt_number=1,
        status=ExecutionAttemptStatus.STAGING,
        input_manifest_hash=plan.input_manifest_sha256,
        execution_plan_hash=plan.plan_hash,
    )
    target = _target("cluster-a")
    stage = RemoteStageManifest(
        attempt_id=attempt.id,
        plan_hash=plan.plan_hash,
        execution_settings_hash=plan.execution_settings_hash,
        environment=target.sanitized_environment(),
        remote_directory=f"execution/{attempt.id}",
        files=(),
    )

    artifact_dir = tmp_path / "artifacts" / "execution" / str(attempt.id)
    artifact_dir.mkdir(parents=True)
    plan_text = canonical_json(
        {"schema_version": 1, "plan_hash": plan.plan_hash, "plan": plan}
    ) + "\n"
    bodies = {
        ArtifactType.EXECUTION_PLAN: plan_text.encode(),
        ArtifactType.INCAR: b"ENCUT = 450\n",
        ArtifactType.REMOTE_STAGE_MANIFEST: stage.text.encode(),
    }
    filenames = {
        ArtifactType.EXECUTION_PLAN: "execution-plan.json",
        ArtifactType.INCAR: "INCAR.runtime",
        ArtifactType.REMOTE_STAGE_MANIFEST: "remote-stage-manifest.json",
    }
    artifacts: list[Artifact] = []
    for artifact_type, body in bodies.items():
        path = artifact_dir / filenames[artifact_type]
        path.write_bytes(body)
        artifacts.append(
            Artifact(
                artifact_type=artifact_type,
                producer=ExecutionAttemptProducerRef(attempt.id),
                availability=ArtifactAvailability.LOCAL,
                local_path=path.relative_to(tmp_path).as_posix(),
                size_bytes=len(body),
                sha256=_sha(body),
            )
        )

    store = ProjectStore(tmp_path)
    store.save(
        ProjectBundle(
            project=project,
            structure_snapshots=(snapshot,),
            method_fingerprints=(fingerprint,),
            calculations=(calculation,),
            execution_attempts=(attempt,),
            artifacts=tuple(artifacts),
        )
    )
    return store, attempt, plan, target


def test_remote_stage_reconstructs_after_project_reopen(tmp_path: Path) -> None:
    store, attempt, plan, target = _case(tmp_path)

    package = resolve_remote_stage_package(
        project_root=store.root,
        bundle=store.open(),
        attempt_id=attempt.id,
        target=target,
    )

    assert package.attempt == attempt
    assert package.plan == plan
    assert package.target == target
    assert package.remote_directory.value == f"execution/{attempt.id}"
    assert {item.artifact_type for item in package.artifacts} == {
        ArtifactType.EXECUTION_PLAN,
        ArtifactType.INCAR,
        ArtifactType.REMOTE_STAGE_MANIFEST,
    }


def test_remote_stage_rejects_target_drift(tmp_path: Path) -> None:
    store, attempt, _, _ = _case(tmp_path)

    with pytest.raises(ApplicationServiceError, match="target does not match"):
        resolve_remote_stage_package(
            project_root=store.root,
            bundle=store.open(),
            attempt_id=attempt.id,
            target=_target("cluster-b"),
        )
