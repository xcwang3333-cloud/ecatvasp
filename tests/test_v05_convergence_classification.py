from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from ecatvasp.domain import (
    Calculation,
    CalculationType,
    KPointPolicy,
    KPointPolicyKind,
    MethodDefinition,
    MethodFingerprint,
    ParameterEntry,
    PotcarIdentity,
    ProtocolDefinition,
    RecipeIdentity,
    RetrievalPolicy,
)
from ecatvasp.domain.ids import (
    new_artifact_id,
    new_calculation_id,
    new_execution_attempt_id,
    new_project_id,
    new_structure_snapshot_id,
)
from ecatvasp.vasp import (
    RECIPE_FULL_FREQUENCY,
    RECIPE_GROUND_STATE_STATIC,
    RECIPE_SLAB_RELAX,
    ConvergenceVerdict,
    VaspResultArtifactIntake,
    VaspResultInputFile,
    VaspResultSource,
    VaspResultSourceRole,
    parse_vasp_energy_metadata,
    result_source_artifact_type,
)
from ecatvasp.vasp.convergence import (
    VaspConvergenceError,
    VaspConvergenceEvidenceCode,
    assess_vasp_convergence,
    collect_vasp_convergence_evidence,
)


def _input_file(
    *,
    root: Path,
    role: VaspResultSourceRole,
    filename: str,
    body: bytes,
) -> VaspResultInputFile:
    relative = Path("artifacts") / "convergence-inputs" / filename
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return VaspResultInputFile(
        source=VaspResultSource(
            role=role,
            artifact_id=new_artifact_id(),
            artifact_type=result_source_artifact_type(role),
            sha256=hashlib.sha256(body).hexdigest(),
        ),
        expected_output_path=filename,
        local_relative_path=relative.as_posix(),
        size_bytes=len(body),
        retrieval_policy=RetrievalPolicy.ALWAYS,
    )


def _intake(
    *,
    calculation_id,
    files: tuple[VaspResultInputFile, ...],
    calculation_type: CalculationType,
    recipe_id: str,
) -> VaspResultArtifactIntake:
    return VaspResultArtifactIntake(
        calculation_id=calculation_id,
        calculation_type=calculation_type,
        recipe_id=recipe_id,
        attempt_id=new_execution_attempt_id(),
        attempt_number=1,
        plan_hash="a" * 64,
        input_manifest_hash="b" * 64,
        files=files,
    )


def _fingerprint(
    *,
    recipe_id: str,
    recipe_parameters: tuple[ParameterEntry, ...] = (),
) -> MethodFingerprint:
    return MethodFingerprint(
        method=MethodDefinition(
            xc_functional="PBE",
            potcar_family="PBE_54",
            potcars=(PotcarIdentity("C", "C", "c" * 64),),
            dispersion_model="NONE",
        ),
        protocol=ProtocolDefinition(
            encut_ev=450.0,
            kpoints=KPointPolicy(KPointPolicyKind.GAMMA_ONLY),
        ),
        recipe=RecipeIdentity(recipe_id, parameters=recipe_parameters),
    )


def _calculation(
    *,
    calculation_id,
    fingerprint: MethodFingerprint,
    calculation_type: CalculationType,
) -> Calculation:
    return Calculation(
        id=calculation_id,
        project_id=new_project_id(),
        calculation_type=calculation_type,
        input_structure_snapshot_id=new_structure_snapshot_id(),
        recipe_id=fingerprint.recipe.recipe_id,
        method_fingerprint_id=fingerprint.id,
        slug="v05-convergence-test",
    )


def _parse_and_assess(
    *,
    root: Path,
    calculation_type: CalculationType,
    recipe_id: str,
    outcar_body: bytes,
    oszicar_body: bytes | None,
    recipe_parameters: tuple[ParameterEntry, ...] = (),
):
    calculation_id = new_calculation_id()
    files = [
        _input_file(
            root=root,
            role=VaspResultSourceRole.OUTCAR,
            filename="OUTCAR",
            body=outcar_body,
        )
    ]
    if oszicar_body is not None:
        files.append(
            _input_file(
                root=root,
                role=VaspResultSourceRole.OSZICAR,
                filename="OSZICAR",
                body=oszicar_body,
            )
        )
    intake = _intake(
        calculation_id=calculation_id,
        files=tuple(files),
        calculation_type=calculation_type,
        recipe_id=recipe_id,
    )
    result = parse_vasp_energy_metadata(project_root=root, intake=intake)
    evidence = collect_vasp_convergence_evidence(
        project_root=root,
        intake=intake,
        result=result,
    )
    fingerprint = _fingerprint(
        recipe_id=recipe_id,
        recipe_parameters=recipe_parameters,
    )
    calculation = _calculation(
        calculation_id=calculation_id,
        fingerprint=fingerprint,
        calculation_type=calculation_type,
    )
    assessment = assess_vasp_convergence(
        calculation=calculation,
        fingerprint=fingerprint,
        evidence=evidence,
    )
    return intake, result, evidence, fingerprint, calculation, assessment


def _finished_outcar(
    *,
    nelm: int,
    nsw: int,
    ionic_marker: bool = False,
    ediff_marker: bool = True,
) -> bytes:
    lines = [
        b"vasp.6.4.3\n",
        f" NELM = {nelm}; NELMIN = 2\n".encode(),
        f" NSW = {nsw}\n".encode(),
        b" free energy TOTEN = -10.000000 eV\n",
    ]
    if ediff_marker:
        lines.append(b" aborting loop because EDIFF is reached\n")
    if ionic_marker:
        lines.append(
            b" reached required accuracy - stopping structural energy minimisation\n"
        )
    lines.append(b" General timing and accounting informations for this job:\n")
    return b"".join(lines)


def test_static_converges_with_complete_electronic_evidence(tmp_path: Path) -> None:
    _, _, evidence, _, _, assessment = _parse_and_assess(
        root=tmp_path,
        calculation_type=CalculationType.STATIC,
        recipe_id=RECIPE_GROUND_STATE_STATIC,
        outcar_body=_finished_outcar(nelm=60, nsw=0),
        oszicar_body=b" DAV: 1 -1.0\n DAV: 3 -2.0\n",
    )

    assert evidence.electronic_step_limit == 60
    assert evidence.ionic_step_limit == 0
    assert evidence.max_electronic_steps == 3
    assert assessment.electronic is ConvergenceVerdict.CONVERGED
    assert assessment.ionic is ConvergenceVerdict.NOT_APPLICABLE
    assert assessment.overall is ConvergenceVerdict.CONVERGED
    assert VaspConvergenceEvidenceCode.IONIC_NOT_APPLICABLE.value in (
        assessment.evidence_codes
    )


def test_electronic_nelm_exhaustion_overrides_global_ediff_marker(tmp_path: Path) -> None:
    _, _, _, _, _, assessment = _parse_and_assess(
        root=tmp_path,
        calculation_type=CalculationType.STATIC,
        recipe_id=RECIPE_GROUND_STATE_STATIC,
        outcar_body=_finished_outcar(nelm=4, nsw=0),
        oszicar_body=b" DAV: 1 -1.0\n DAV: 4 -2.0\n",
    )

    assert assessment.electronic is ConvergenceVerdict.UNCONVERGED
    assert assessment.overall is ConvergenceVerdict.UNCONVERGED
    assert VaspConvergenceEvidenceCode.ELECTRONIC_LIMIT_EXHAUSTED.value in (
        assessment.evidence_codes
    )


def test_incomplete_static_result_is_indeterminate_not_failed(tmp_path: Path) -> None:
    outcar = (
        b"vasp.6.4.3\n"
        b" NELM = 60; NSW = 0\n"
        b" free energy TOTEN = -10.0 eV\n"
        b" aborting loop because EDIFF is reached\n"
    )
    _, _, _, _, _, assessment = _parse_and_assess(
        root=tmp_path,
        calculation_type=CalculationType.STATIC,
        recipe_id=RECIPE_GROUND_STATE_STATIC,
        outcar_body=outcar,
        oszicar_body=b" DAV: 3 -2.0\n",
    )

    assert assessment.electronic is ConvergenceVerdict.INDETERMINATE
    assert assessment.ionic is ConvergenceVerdict.NOT_APPLICABLE
    assert assessment.overall is ConvergenceVerdict.INDETERMINATE
    assert VaspConvergenceEvidenceCode.OUTPUT_INCOMPLETE.value in assessment.evidence_codes


def test_relaxation_converges_only_with_explicit_ionic_accuracy_marker(
    tmp_path: Path,
) -> None:
    _, _, _, _, _, assessment = _parse_and_assess(
        root=tmp_path,
        calculation_type=CalculationType.RELAX,
        recipe_id=RECIPE_SLAB_RELAX,
        recipe_parameters=(ParameterEntry("NSW", 2),),
        outcar_body=_finished_outcar(nelm=60, nsw=2, ionic_marker=True),
        oszicar_body=(
            b" DAV: 2 -1.0\n"
            b" 1 F= -.10 E0= -.10\n"
            b" DAV: 3 -2.0\n"
            b" 2 F= -.20 E0= -.20\n"
        ),
    )

    assert assessment.electronic is ConvergenceVerdict.CONVERGED
    assert assessment.ionic is ConvergenceVerdict.CONVERGED
    assert assessment.overall is ConvergenceVerdict.CONVERGED


def test_relaxation_nsw_exhaustion_without_accuracy_marker_is_unconverged(
    tmp_path: Path,
) -> None:
    _, _, _, _, _, assessment = _parse_and_assess(
        root=tmp_path,
        calculation_type=CalculationType.RELAX,
        recipe_id=RECIPE_SLAB_RELAX,
        recipe_parameters=(ParameterEntry("NSW", 2),),
        outcar_body=_finished_outcar(nelm=60, nsw=2),
        oszicar_body=(
            b" DAV: 2 -1.0\n"
            b" 1 F= -.10 E0= -.10\n"
            b" DAV: 3 -2.0\n"
            b" 2 F= -.20 E0= -.20\n"
        ),
    )

    assert assessment.electronic is ConvergenceVerdict.CONVERGED
    assert assessment.ionic is ConvergenceVerdict.UNCONVERGED
    assert assessment.overall is ConvergenceVerdict.UNCONVERGED
    assert VaspConvergenceEvidenceCode.IONIC_LIMIT_EXHAUSTED.value in (
        assessment.evidence_codes
    )


def test_relaxation_without_marker_or_exhaustion_is_indeterminate(tmp_path: Path) -> None:
    _, _, _, _, _, assessment = _parse_and_assess(
        root=tmp_path,
        calculation_type=CalculationType.RELAX,
        recipe_id=RECIPE_SLAB_RELAX,
        recipe_parameters=(ParameterEntry("NSW", 3),),
        outcar_body=_finished_outcar(nelm=60, nsw=3),
        oszicar_body=b" DAV: 2 -1.0\n 1 F= -.10 E0= -.10\n",
    )

    assert assessment.electronic is ConvergenceVerdict.CONVERGED
    assert assessment.ionic is ConvergenceVerdict.INDETERMINATE
    assert assessment.overall is ConvergenceVerdict.INDETERMINATE


def test_outcar_nsw_mismatch_makes_overall_indeterminate(tmp_path: Path) -> None:
    _, _, _, _, _, assessment = _parse_and_assess(
        root=tmp_path,
        calculation_type=CalculationType.RELAX,
        recipe_id=RECIPE_SLAB_RELAX,
        recipe_parameters=(ParameterEntry("NSW", 2),),
        outcar_body=_finished_outcar(nelm=60, nsw=3, ionic_marker=True),
        oszicar_body=b" DAV: 2 -1.0\n 1 F= -.10 E0= -.10\n",
    )

    assert assessment.electronic is ConvergenceVerdict.CONVERGED
    assert assessment.ionic is ConvergenceVerdict.INDETERMINATE
    assert assessment.overall is ConvergenceVerdict.INDETERMINATE
    assert VaspConvergenceEvidenceCode.IONIC_LIMIT_MISMATCH_RECIPE.value in (
        assessment.evidence_codes
    )


def test_static_nsw_mismatch_keeps_ionic_not_applicable_but_blocks_overall(
    tmp_path: Path,
) -> None:
    _, _, _, _, _, assessment = _parse_and_assess(
        root=tmp_path,
        calculation_type=CalculationType.STATIC,
        recipe_id=RECIPE_GROUND_STATE_STATIC,
        outcar_body=_finished_outcar(nelm=60, nsw=1),
        oszicar_body=b" DAV: 2 -1.0\n",
    )

    assert assessment.electronic is ConvergenceVerdict.CONVERGED
    assert assessment.ionic is ConvergenceVerdict.NOT_APPLICABLE
    assert assessment.overall is ConvergenceVerdict.INDETERMINATE


def test_frequency_ionic_convergence_is_not_applicable(tmp_path: Path) -> None:
    _, _, _, _, _, assessment = _parse_and_assess(
        root=tmp_path,
        calculation_type=CalculationType.FREQUENCY,
        recipe_id=RECIPE_FULL_FREQUENCY,
        recipe_parameters=(ParameterEntry("NFREE", 2), ParameterEntry("POTIM", 0.015)),
        outcar_body=_finished_outcar(nelm=60, nsw=1),
        oszicar_body=b" DAV: 2 -1.0\n",
    )

    assert assessment.electronic is ConvergenceVerdict.CONVERGED
    assert assessment.ionic is ConvergenceVerdict.NOT_APPLICABLE
    assert assessment.overall is ConvergenceVerdict.CONVERGED


def test_evidence_collection_requires_exact_parsed_source_bundle(tmp_path: Path) -> None:
    calculation_id = new_calculation_id()
    outcar = _input_file(
        root=tmp_path,
        role=VaspResultSourceRole.OUTCAR,
        filename="OUTCAR",
        body=_finished_outcar(nelm=60, nsw=0),
    )
    intake = _intake(
        calculation_id=calculation_id,
        files=(outcar,),
        calculation_type=CalculationType.STATIC,
        recipe_id=RECIPE_GROUND_STATE_STATIC,
    )
    result = parse_vasp_energy_metadata(project_root=tmp_path, intake=intake)
    foreign_source = replace(result.sources[0], artifact_id=new_artifact_id())
    foreign_result = replace(result, sources=(foreign_source,))

    with pytest.raises(VaspConvergenceError, match="sources do not match"):
        collect_vasp_convergence_evidence(
            project_root=tmp_path,
            intake=intake,
            result=foreign_result,
        )


def test_classifier_rejects_evidence_from_another_calculation(tmp_path: Path) -> None:
    _, _, evidence, fingerprint, _, _ = _parse_and_assess(
        root=tmp_path,
        calculation_type=CalculationType.STATIC,
        recipe_id=RECIPE_GROUND_STATE_STATIC,
        outcar_body=_finished_outcar(nelm=60, nsw=0),
        oszicar_body=b" DAV: 2 -1.0\n",
    )
    foreign_calculation = _calculation(
        calculation_id=new_calculation_id(),
        fingerprint=fingerprint,
        calculation_type=CalculationType.STATIC,
    )

    with pytest.raises(VaspConvergenceError, match="another Calculation"):
        assess_vasp_convergence(
            calculation=foreign_calculation,
            fingerprint=fingerprint,
            evidence=evidence,
        )


def test_evidence_collection_rechecks_source_integrity_after_result_parse(
    tmp_path: Path,
) -> None:
    calculation_id = new_calculation_id()
    outcar = _input_file(
        root=tmp_path,
        role=VaspResultSourceRole.OUTCAR,
        filename="OUTCAR",
        body=_finished_outcar(nelm=60, nsw=0),
    )
    intake = _intake(
        calculation_id=calculation_id,
        files=(outcar,),
        calculation_type=CalculationType.STATIC,
        recipe_id=RECIPE_GROUND_STATE_STATIC,
    )
    result = parse_vasp_energy_metadata(project_root=tmp_path, intake=intake)
    path = tmp_path / outcar.local_relative_path
    path.write_bytes(path.read_bytes().replace(b"-10.000000", b"-11.000000"))

    with pytest.raises(VaspConvergenceError, match="SHA-256 changed"):
        collect_vasp_convergence_evidence(
            project_root=tmp_path,
            intake=intake,
            result=result,
        )


def test_evidence_collection_rejects_ambiguous_outcar_nelm(tmp_path: Path) -> None:
    calculation_id = new_calculation_id()
    body = (
        b"vasp.6.4.3\n"
        b" NELM = 60; NSW = 0\n"
        b" NELM = 80; NSW = 0\n"
        b" free energy TOTEN = -10.0 eV\n"
        b" aborting loop because EDIFF is reached\n"
        b" General timing and accounting informations for this job:\n"
    )
    outcar = _input_file(
        root=tmp_path,
        role=VaspResultSourceRole.OUTCAR,
        filename="OUTCAR",
        body=body,
    )
    intake = _intake(
        calculation_id=calculation_id,
        files=(outcar,),
        calculation_type=CalculationType.STATIC,
        recipe_id=RECIPE_GROUND_STATE_STATIC,
    )
    result = parse_vasp_energy_metadata(project_root=tmp_path, intake=intake)

    with pytest.raises(VaspConvergenceError, match="multiple distinct NELM"):
        collect_vasp_convergence_evidence(
            project_root=tmp_path,
            intake=intake,
            result=result,
        )
