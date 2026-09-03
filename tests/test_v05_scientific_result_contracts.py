from dataclasses import fields

import pytest

from ecatvasp.domain import (
    Analysis,
    AnalysisType,
    ArtifactType,
    CalculationType,
    Project,
    new_artifact_id,
)
from ecatvasp.storage.codec import dumps_storage, loads_storage
from ecatvasp.vasp import (
    ConvergenceVerdict,
    VaspConvergenceAssessment,
    VaspEnergySummary,
    VaspResultContractError,
    VaspResultDocument,
    VaspResultSource,
    VaspResultSourceRole,
    validate_result_parse_analysis,
)


def _source(
    role: VaspResultSourceRole,
    artifact_type: ArtifactType,
    *,
    digest: str = "a" * 64,
) -> VaspResultSource:
    return VaspResultSource(
        role=role,
        artifact_id=new_artifact_id(),
        artifact_type=artifact_type,
        sha256=digest,
    )


def test_result_parse_is_durable_analysis_type_without_schema_change() -> None:
    project = Project(name="v0.5 result contracts", slug="v05-result-contracts")
    analysis = Analysis(
        project_id=project.id,
        analysis_type=AnalysisType.RESULT_PARSE,
        input_artifact_ids=(new_artifact_id(),),
        tool="ecatvasp.vasp.result-parser",
        tool_version="1",
    )

    validate_result_parse_analysis(analysis)
    assert loads_storage(dumps_storage(analysis)) == analysis


def test_result_document_keeps_energy_semantics_explicit_and_parser_only() -> None:
    document = VaspResultDocument(
        calculation_type=CalculationType.RELAX,
        sources=(
            _source(VaspResultSourceRole.OUTCAR, ArtifactType.OUTCAR),
            _source(VaspResultSourceRole.OSZICAR, ArtifactType.OSZICAR, digest="B" * 64),
        ),
        energies=VaspEnergySummary(
            free_energy_toten_ev=-123.4,
            energy_without_entropy_ev=-123.3,
            energy_sigma0_ev=-123.35,
            fermi_energy_ev=2.1,
        ),
        vasp_version="6.4.3",
        ionic_steps=12,
        electronic_steps=8,
        termination_observed=True,
        evidence_codes=("outcar-energy-block", "normal-termination-footer"),
    )

    assert document.sources[1].sha256 == "b" * 64
    field_names = {item.name for item in fields(document)}
    assert "scientific_status" not in field_names
    assert "electronic_converged" not in field_names
    assert "ionic_converged" not in field_names
    assert "total_energy_ev" not in {item.name for item in fields(document.energies)}


def test_result_source_contract_is_fail_closed() -> None:
    with pytest.raises(VaspResultContractError, match="OUTCAR"):
        _source(VaspResultSourceRole.OUTCAR, ArtifactType.OSZICAR)

    with pytest.raises(VaspResultContractError, match="SHA-256"):
        _source(VaspResultSourceRole.OUTCAR, ArtifactType.OUTCAR, digest="not-a-digest")


def test_result_document_requires_exact_outcar_and_unique_sources() -> None:
    oszicar = _source(VaspResultSourceRole.OSZICAR, ArtifactType.OSZICAR)
    with pytest.raises(VaspResultContractError, match="OUTCAR source"):
        VaspResultDocument(calculation_type=CalculationType.STATIC, sources=(oszicar,))

    outcar_a = _source(VaspResultSourceRole.OUTCAR, ArtifactType.OUTCAR)
    outcar_b = _source(VaspResultSourceRole.OUTCAR, ArtifactType.OUTCAR, digest="c" * 64)
    with pytest.raises(VaspResultContractError, match="source roles"):
        VaspResultDocument(
            calculation_type=CalculationType.STATIC,
            sources=(outcar_a, outcar_b),
        )


def test_result_contract_rejects_nonfinite_energy_and_negative_steps() -> None:
    with pytest.raises(VaspResultContractError, match="finite"):
        VaspEnergySummary(free_energy_toten_ev=float("nan"))

    with pytest.raises(VaspResultContractError, match="ionic_steps"):
        VaspResultDocument(
            calculation_type=CalculationType.RELAX,
            sources=(_source(VaspResultSourceRole.OUTCAR, ArtifactType.OUTCAR),),
            ionic_steps=-1,
        )


def test_convergence_assessment_is_separate_scientific_contract() -> None:
    assessment = VaspConvergenceAssessment(
        calculation_type=CalculationType.STATIC,
        electronic=ConvergenceVerdict.CONVERGED,
        ionic=ConvergenceVerdict.NOT_APPLICABLE,
        overall=ConvergenceVerdict.CONVERGED,
        evidence_codes=("electronic-ediff-reached",),
    )

    assert assessment.overall is ConvergenceVerdict.CONVERGED
    assert assessment.ionic is ConvergenceVerdict.NOT_APPLICABLE


def test_result_parse_analysis_requires_explicit_parser_provenance() -> None:
    project = Project(name="parser provenance", slug="parser-provenance")
    missing_version = Analysis(
        project_id=project.id,
        analysis_type=AnalysisType.RESULT_PARSE,
        input_artifact_ids=(new_artifact_id(),),
        tool="ecatvasp.vasp.result-parser",
    )
    with pytest.raises(VaspResultContractError, match="tool_version"):
        validate_result_parse_analysis(missing_version)

    wrong_type = Analysis(
        project_id=project.id,
        analysis_type=AnalysisType.CONVERGENCE,
        input_artifact_ids=(new_artifact_id(),),
        tool="ecatvasp.vasp.convergence",
        tool_version="1",
    )
    with pytest.raises(VaspResultContractError, match="RESULT_PARSE"):
        validate_result_parse_analysis(wrong_type)
