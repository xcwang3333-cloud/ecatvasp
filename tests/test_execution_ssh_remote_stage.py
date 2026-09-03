from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from ecatvasp.domain import (
    ArtifactType,
    Calculation,
    CalculationType,
    ExecutionSettings,
    Project,
    SchedulerType,
    new_method_fingerprint_id,
    new_structure_snapshot_id,
)
from ecatvasp.domain.ids import new_artifact_id
from ecatvasp.execution import (
    CommandResult,
    CommandSpec,
    ExecutionTargetProfile,
    OpenSshTransport,
    OpenSshTransportError,
    RemotePotcarLibrary,
    RemoteStagingError,
    SshSecurityPolicy,
    TargetRelativePath,
    TransportKind,
    create_execution_attempt,
    remote_absolute_path,
    stage_remote_runtime,
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


class _FakeSshTransport:
    transport_kind = TransportKind.SSH

    def __init__(self, *, corrupt_upload: bool = False) -> None:
        self.files: dict[str, bytes] = {}
        self.directories: set[str] = set()
        self.corrupt_upload = corrupt_upload

    def ensure_directory(
        self,
        *,
        target: ExecutionTargetProfile,
        path: TargetRelativePath,
    ) -> None:
        self.directories.add(remote_absolute_path(target, path))

    def upload(
        self,
        *,
        target: ExecutionTargetProfile,
        local_path: Path,
        destination: TargetRelativePath,
    ) -> None:
        body = local_path.read_bytes()
        if self.corrupt_upload:
            body += b"corrupt"
        self.files[remote_absolute_path(target, destination)] = body

    def download(
        self,
        *,
        target: ExecutionTargetProfile,
        source: TargetRelativePath,
        local_path: Path,
    ) -> None:
        local_path.write_bytes(self.files[remote_absolute_path(target, source)])

    def run(
        self,
        *,
        target: ExecutionTargetProfile,
        command: CommandSpec,
    ) -> CommandResult:
        argv = command.argv
        if argv[0] == "mkdir":
            path = argv[-1]
            if path in self.directories:
                return CommandResult(1, stderr="exists")
            self.directories.add(path)
            return CommandResult(0)
        if argv[0] == "sha256sum":
            path = argv[-1]
            body = self.files.get(path)
            if body is None:
                return CommandResult(1, stderr="missing")
            digest = hashlib.sha256(body).hexdigest()
            return CommandResult(0, stdout=f"{digest}  {path}\n")
        if argv[0] == "stat":
            path = argv[-1]
            body = self.files.get(path)
            if body is None:
                return CommandResult(1, stderr="missing")
            return CommandResult(0, stdout=f"{len(body)}\n")
        if argv[0] == "cp":
            source, destination = argv[-2:]
            body = self.files.get(source)
            if body is None:
                return CommandResult(1, stderr="missing")
            self.files[destination] = body
            return CommandResult(0)
        if argv[0] == "dd":
            source = next(item[3:] for item in argv if item.startswith("if="))
            destination = next(item[3:] for item in argv if item.startswith("of="))
            body = self.files.get(source)
            if body is None:
                return CommandResult(1, stderr="missing")
            self.files[destination] = self.files.get(destination, b"") + body
            return CommandResult(0)
        return CommandResult(127, stderr="unsupported")


def _calculation(project: Project) -> Calculation:
    return Calculation(
        project_id=project.id,
        calculation_type=CalculationType.RELAX,
        input_structure_snapshot_id=new_structure_snapshot_id(),
        recipe_id="ECatVASP.VASP.AdsorbateRelax",
        method_fingerprint_id=new_method_fingerprint_id(),
    )


def _write_input(root: Path, name: str, body: bytes) -> StagingInput:
    path = root / "inputs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    role = name.lower()
    artifact_type = {
        "incar": ArtifactType.INCAR,
        "kpoints": ArtifactType.KPOINTS,
        "poscar": ArtifactType.POSCAR,
    }[role]
    return StagingInput(
        role=role,
        kind=StagingInputKind.VASP_INPUT,
        artifact_id=new_artifact_id(),
        artifact_type=artifact_type,
        source_relative_path=f"inputs/{name}",
        target_relative_path=name,
        sha256=hashlib.sha256(body).hexdigest(),
        size_bytes=len(body),
    )


def _plan(root: Path, calculation: Calculation, potcar_body: bytes) -> ExecutionPlan:
    staging = tuple(
        sorted(
            (
                _write_input(root, "INCAR", b"ENCUT = 450\nEDIFF = 1E-5\n"),
                _write_input(root, "KPOINTS", b"Gamma\n0\nGamma\n1 1 1\n0 0 0\n"),
                _write_input(root, "POSCAR", b"Pb\n1.0\n"),
            ),
            key=lambda item: item.role,
        )
    )
    return ExecutionPlan(
        calculation_id=calculation.id,
        recipe_id=calculation.recipe_id,
        system_context=VaspSystemContext(VaspSystemKind.PERIODIC_3D),
        input_manifest_artifact_id=new_artifact_id(),
        input_manifest_sha256="a" * 64,
        preparation_hash="b" * 64,
        staging_inputs=staging,
        potcar_resolution=PotcarResolutionRequest(
            family="PBE_54",
            core_method_hash="c" * 64,
            metadata_hash="d" * 64,
            entries=(
                PotcarResolutionEntry(
                    "Pb",
                    "Pb_d",
                    hashlib.sha256(potcar_body).hexdigest(),
                ),
            ),
        ),
        expected_outputs=(),
        runtime_constraints=VaspRuntimeConstraints(),
        execution_settings=ExecutionSettings(ncore=2, executable="vasp_std"),
    )


def _target() -> ExecutionTargetProfile:
    return ExecutionTargetProfile(
        target_id="hpc-prod",
        transport=TransportKind.SSH,
        scheduler=SchedulerType.SLURM,
        host_alias="cluster-a",
        remote_work_root="/scratch/ecatvasp",
        potcar_resolver_id="pbe54-remote",
        vasp_executable="vasp_std",
        ssh_security=SshSecurityPolicy(),
    )


def test_remote_stage_verifies_inputs_and_keeps_potcar_remote(tmp_path: Path) -> None:
    project = Project(name="Remote", slug="remote")
    calculation = _calculation(project)
    potcar_body = b"licensed-pb-potcar\n"
    plan = _plan(tmp_path, calculation, potcar_body)
    attempt = create_execution_attempt(plan=plan, calculation=calculation)
    target = _target()
    transport = _FakeSshTransport()
    library = RemotePotcarLibrary(
        resolver_id="pbe54-remote",
        family="PBE_54",
        root="/opt/vasp/potpaw_PBE_54",
    )
    remote_source = "/opt/vasp/potpaw_PBE_54/Pb_d/POTCAR"
    transport.files[remote_source] = potcar_body

    package = stage_remote_runtime(
        project_root=tmp_path,
        plan=plan,
        calculation=calculation,
        attempt=attempt,
        target=target,
        transport=transport,
        potcars=library,
    )

    assert package.attempt.status.value == "staging"
    remote_potcar = remote_absolute_path(
        target,
        TargetRelativePath(f"{package.remote_directory.value}/POTCAR"),
    )
    assert transport.files[remote_potcar] == potcar_body
    assert "NCORE = 2" in transport.files[
        remote_absolute_path(
            target,
            TargetRelativePath(f"{package.remote_directory.value}/INCAR"),
        )
    ].decode()
    assert (tmp_path / "inputs" / "INCAR").read_text() == "ENCUT = 450\nEDIFF = 1E-5\n"

    artifact_dir = tmp_path / "artifacts" / "execution" / str(attempt.id)
    assert not (artifact_dir / "POTCAR").exists()
    manifest_text = (artifact_dir / "remote-stage-manifest.json").read_text()
    assert "/opt/vasp/potpaw_PBE_54" not in manifest_text
    assert "cluster-a" not in manifest_text
    assert '"licensed":true' in manifest_text
    assert {item.artifact_type for item in package.artifacts} == {
        ArtifactType.EXECUTION_PLAN,
        ArtifactType.INCAR,
        ArtifactType.REMOTE_STAGE_MANIFEST,
    }


def test_remote_stage_fails_closed_on_remote_potcar_drift(tmp_path: Path) -> None:
    project = Project(name="Remote", slug="remote")
    calculation = _calculation(project)
    plan = _plan(tmp_path, calculation, b"expected-potcar")
    attempt = create_execution_attempt(plan=plan, calculation=calculation)
    target = _target()
    transport = _FakeSshTransport()
    transport.files["/opt/vasp/potpaw_PBE_54/Pb_d/POTCAR"] = b"drifted-potcar"

    with pytest.raises(RemoteStagingError, match="POTCAR hash mismatch"):
        stage_remote_runtime(
            project_root=tmp_path,
            plan=plan,
            calculation=calculation,
            attempt=attempt,
            target=target,
            transport=transport,
            potcars=RemotePotcarLibrary(
                resolver_id="pbe54-remote",
                family="PBE_54",
                root="/opt/vasp/potpaw_PBE_54",
            ),
        )


def test_remote_stage_fails_closed_on_uploaded_input_corruption(tmp_path: Path) -> None:
    project = Project(name="Remote", slug="remote")
    calculation = _calculation(project)
    plan = _plan(tmp_path, calculation, b"potcar")
    attempt = create_execution_attempt(plan=plan, calculation=calculation)
    target = _target()
    transport = _FakeSshTransport(corrupt_upload=True)
    transport.files["/opt/vasp/potpaw_PBE_54/Pb_d/POTCAR"] = b"potcar"

    with pytest.raises(RemoteStagingError, match="integrity mismatch"):
        stage_remote_runtime(
            project_root=tmp_path,
            plan=plan,
            calculation=calculation,
            attempt=attempt,
            target=target,
            transport=transport,
            potcars=RemotePotcarLibrary(
                resolver_id="pbe54-remote",
                family="PBE_54",
                root="/opt/vasp/potpaw_PBE_54",
            ),
        )


def test_ssh_target_rejects_remote_root_shell_expansion() -> None:
    with pytest.raises(ValueError, match="literal safe POSIX"):
        ExecutionTargetProfile(
            target_id="hpc-prod",
            transport=TransportKind.SSH,
            scheduler=SchedulerType.SLURM,
            host_alias="cluster-a",
            remote_work_root="/scratch/$USER/ecatvasp",
            potcar_resolver_id="pbe54-remote",
            ssh_security=SshSecurityPolicy(),
        )


def test_openssh_transport_uses_strict_batch_mode_and_rejects_shell_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(
        argv: tuple[str, ...],
        *,
        capture_output: bool,
        check: bool,
        shell: bool,
    ) -> subprocess.CompletedProcess[bytes]:
        assert capture_output is True
        assert check is False
        assert shell is False
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    transport = OpenSshTransport()
    target = _target()
    transport.ensure_directory(target=target, path=TargetRelativePath("execution/test"))

    assert calls[0][:7] == (
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "--",
        "cluster-a",
    )
    with pytest.raises(OpenSshTransportError, match="shell-inert"):
        transport.run(
            target=target,
            command=CommandSpec(argv=("echo", "unsafe;token")),
        )
    with pytest.raises(OpenSshTransportError, match="path components"):
        remote_absolute_path(target, TargetRelativePath("execution/bad;path"))
