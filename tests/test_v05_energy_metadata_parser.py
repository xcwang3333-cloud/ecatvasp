from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ecatvasp.domain import ArtifactType, CalculationType, RetrievalPolicy
from ecatvasp.domain.ids import (
    new_artifact_id,
    new_calculation_id,
    new_execution_attempt_id,
)
from ecatvasp.vasp import (
    VaspParserEvidenceCode,
    VaspResultArtifactIntake,
    VaspResultInputFile,
    VaspResultParseError,
    VaspResultSource,
    VaspResultSourceRole,
    parse_vasp_energy_metadata,
    result_source_artifact_type,
)


def _input_file(
    *,
    root: Path,
    role: VaspResultSourceRole,
    filename: str,
    body: bytes,
    retrieval_policy: RetrievalPolicy = RetrievalPolicy.ALWAYS,
) -> VaspResultInputFile:
    relative = Path("artifacts") / "result-inputs" / filename
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()
    return VaspResultInputFile(
        source=VaspResultSource(
            role=role,
            artifact_id=new_artifact_id(),
            artifact_type=result_source_artifact_type(role),
            sha256=digest,
        ),
        expected_output_path=filename,
        local_relative_path=relative.as_posix(),
        size_bytes=len(body),
        retrieval_policy=retrieval_policy,
    )


def _intake(
    *,
    files: tuple[VaspResultInputFile, ...],
    calculation_type: CalculationType = CalculationType.STATIC,
) -> VaspResultArtifactIntake:
    return VaspResultArtifactIntake(
        calculation_id=new_calculation_id(),
        calculation_type=calculation_type,
        recipe_id="ECatVASP.VASP.GroundStateStatic",
        attempt_id=new_execution_attempt_id(),
        attempt_number=1,
        plan_hash="a" * 64,
        input_manifest_hash="b" * 64,
        files=files,
    )


def test_parser_extracts_explicit_energy_semantics_and_metadata(tmp_path: Path) -> None:
    outcar = _input_file(
        root=tmp_path,
        role=VaspResultSourceRole.OUTCAR,
        filename="OUTCAR",
        body=(
            b"vasp.6.4.3 18Apr24 complex\n"
            b" free  energy   TOTEN  =      -17.91346915 eV\n"
            b" energy without entropy =      -17.90300000"
            b"  energy(sigma->0) =      -17.90400000\n"
            b" E-fermi :   4.2500000D+00 XC(G=0): 0.0\n"
            b" aborting loop because EDIFF is reached\n"
            b" reached required accuracy - stopping structural energy minimisation\n"
            b" General timing and accounting informations for this job:\n"
        ),
    )
    oszicar = _input_file(
        root=tmp_path,
        role=VaspResultSourceRole.OSZICAR,
        filename="OSZICAR",
        body=(
            b" DAV:   1    -1.0\n"
            b" DAV:   2    -2.0\n"
            b"   1 F= -.10 E0= -.10\n"
            b" DAV:   1    -2.1\n"
            b" DAV:   3    -2.2\n"
            b"   2 F= -.20 E0= -.20\n"
        ),
    )
    intake = _intake(files=(oszicar, outcar))

    document = parse_vasp_energy_metadata(project_root=tmp_path, intake=intake)

    assert document.energies.free_energy_toten_ev == pytest.approx(-17.91346915)
    assert document.energies.energy_without_entropy_ev == pytest.approx(-17.903)
    assert document.energies.energy_sigma0_ev == pytest.approx(-17.904)
    assert document.energies.fermi_energy_ev == pytest.approx(4.25)
    assert document.vasp_version == "6.4.3"
    assert document.ionic_steps == 2
    assert document.electronic_steps == 3
    assert document.termination_observed is True
    assert document.sources == intake.sources
    assert VaspParserEvidenceCode.OUTCAR_ELECTRONIC_EDIFF_REACHED.value in (
        document.evidence_codes
    )
    assert VaspParserEvidenceCode.OUTCAR_IONIC_REQUIRED_ACCURACY_REACHED.value in (
        document.evidence_codes
    )
    assert tuple(sorted(document.evidence_codes)) == document.evidence_codes


def test_parser_uses_latest_observed_energy_values(tmp_path: Path) -> None:
    outcar = _input_file(
        root=tmp_path,
        role=VaspResultSourceRole.OUTCAR,
        filename="OUTCAR",
        body=(
            b"vasp.6.3.2\n"
            b" free energy TOTEN = -1.000000 eV\n"
            b" energy without entropy = -0.900000 energy(sigma->0) = -0.950000\n"
            b" E-fermi : 1.0000\n"
            b" free energy TOTEN = -2.000000 eV\n"
            b" energy without entropy = -1.900000 energy(sigma->0) = -1.950000\n"
            b" E-fermi : 2.0000\n"
        ),
    )

    document = parse_vasp_energy_metadata(
        project_root=tmp_path,
        intake=_intake(files=(outcar,)),
    )

    assert document.energies.free_energy_toten_ev == pytest.approx(-2.0)
    assert document.energies.energy_without_entropy_ev == pytest.approx(-1.9)
    assert document.energies.energy_sigma0_ev == pytest.approx(-1.95)
    assert document.energies.fermi_energy_ev == pytest.approx(2.0)


def test_partial_outcar_remains_parseable_without_inventing_energy(tmp_path: Path) -> None:
    outcar = _input_file(
        root=tmp_path,
        role=VaspResultSourceRole.OUTCAR,
        filename="OUTCAR",
        body=b"vasp.6.3.0\npartial calculation output\n",
    )

    document = parse_vasp_energy_metadata(
        project_root=tmp_path,
        intake=_intake(files=(outcar,)),
    )

    assert document.energies.free_energy_toten_ev is None
    assert document.energies.energy_without_entropy_ev is None
    assert document.energies.energy_sigma0_ev is None
    assert document.energies.fermi_energy_ev is None
    assert document.ionic_steps is None
    assert document.electronic_steps is None
    assert document.termination_observed is False
    assert document.vasp_version == "6.3.0"


def test_malformed_numeric_marker_is_explicit_evidence_not_a_guessed_value(
    tmp_path: Path,
) -> None:
    outcar = _input_file(
        root=tmp_path,
        role=VaspResultSourceRole.OUTCAR,
        filename="OUTCAR",
        body=(
            b"vasp.6.4.0\n"
            b" free energy TOTEN = ******** eV\n"
            b" energy without entropy = ******** energy(sigma->0) = ********\n"
            b" E-fermi : ********\n"
        ),
    )

    document = parse_vasp_energy_metadata(
        project_root=tmp_path,
        intake=_intake(files=(outcar,)),
    )

    assert document.energies.free_energy_toten_ev is None
    assert document.energies.energy_without_entropy_ev is None
    assert document.energies.energy_sigma0_ev is None
    assert document.energies.fermi_energy_ev is None
    assert VaspParserEvidenceCode.OUTCAR_FREE_ENERGY_TOTEN_UNPARSEABLE.value in (
        document.evidence_codes
    )
    assert VaspParserEvidenceCode.OUTCAR_ENERGY_WITHOUT_ENTROPY_UNPARSEABLE.value in (
        document.evidence_codes
    )
    assert VaspParserEvidenceCode.OUTCAR_ENERGY_SIGMA0_UNPARSEABLE.value in (
        document.evidence_codes
    )
    assert VaspParserEvidenceCode.OUTCAR_FERMI_ENERGY_UNPARSEABLE.value in (
        document.evidence_codes
    )


def test_parser_rechecks_source_integrity_at_time_of_use(tmp_path: Path) -> None:
    outcar = _input_file(
        root=tmp_path,
        role=VaspResultSourceRole.OUTCAR,
        filename="OUTCAR",
        body=b"vasp.6.4.0\nfree energy TOTEN = -1.0 eV\n",
    )
    intake = _intake(files=(outcar,))
    (tmp_path / outcar.local_relative_path).write_bytes(
        b"vasp.6.4.0\nfree energy TOTEN = -2.0 eV\n"
    )

    with pytest.raises(VaspResultParseError, match="SHA-256 changed"):
        parse_vasp_energy_metadata(project_root=tmp_path, intake=intake)


def test_parser_rechecks_uninterpreted_intake_sources(tmp_path: Path) -> None:
    outcar = _input_file(
        root=tmp_path,
        role=VaspResultSourceRole.OUTCAR,
        filename="OUTCAR",
        body=b"vasp.6.4.0\n",
    )
    contcar = _input_file(
        root=tmp_path,
        role=VaspResultSourceRole.CONTCAR,
        filename="CONTCAR",
        body=b"relaxed-structure-A\n",
    )
    intake = _intake(
        files=(outcar, contcar),
        calculation_type=CalculationType.RELAX,
    )
    (tmp_path / contcar.local_relative_path).write_bytes(b"relaxed-structure-B\n")

    with pytest.raises(VaspResultParseError, match="SHA-256 changed"):
        parse_vasp_energy_metadata(project_root=tmp_path, intake=intake)


def test_parser_rejects_symlink_escape_after_intake(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-outcar"
    outside.write_bytes(b"vasp.6.4.0\n")
    relative = Path("artifacts") / "result-inputs" / "OUTCAR"
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(outside)
    body = outside.read_bytes()
    outcar = VaspResultInputFile(
        source=VaspResultSource(
            role=VaspResultSourceRole.OUTCAR,
            artifact_id=new_artifact_id(),
            artifact_type=ArtifactType.OUTCAR,
            sha256=hashlib.sha256(body).hexdigest(),
        ),
        expected_output_path="OUTCAR",
        local_relative_path=relative.as_posix(),
        size_bytes=len(body),
        retrieval_policy=RetrievalPolicy.ALWAYS,
    )

    with pytest.raises(VaspResultParseError, match="outside project_root"):
        parse_vasp_energy_metadata(
            project_root=tmp_path,
            intake=_intake(files=(outcar,)),
        )


def test_parser_rejects_concatenated_outcars_with_distinct_vasp_versions(
    tmp_path: Path,
) -> None:
    outcar = _input_file(
        root=tmp_path,
        role=VaspResultSourceRole.OUTCAR,
        filename="OUTCAR",
        body=b"vasp.6.3.0\nvasp.6.4.0\n",
    )

    with pytest.raises(VaspResultParseError, match="multiple distinct VASP versions"):
        parse_vasp_energy_metadata(
            project_root=tmp_path,
            intake=_intake(files=(outcar,)),
        )
