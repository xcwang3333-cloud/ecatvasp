from ecatvasp import vasp


def test_v05_convergence_public_api_exports() -> None:
    assert callable(vasp.collect_vasp_convergence_evidence)
    assert callable(vasp.assess_vasp_convergence)
    assert vasp.VASP_CONVERGENCE_CLASSIFIER_VERSION == "1"
    assert "VaspConvergenceEvidence" in vasp.__all__
    assert "VaspConvergenceEvidenceCode" in vasp.__all__
