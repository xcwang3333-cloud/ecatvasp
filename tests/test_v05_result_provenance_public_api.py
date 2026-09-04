from ecatvasp import vasp


def test_v05_result_provenance_public_api_is_explicit() -> None:
    assert vasp.VASP_SCIENTIFIC_RESULT_PIPELINE_VERSION == "1"
    assert vasp.VASP_CONVERGENCE_ARTIFACT_VERSION == 1
    assert callable(vasp.materialize_vasp_scientific_result)
    assert callable(vasp.reconcile_vasp_calculation_status)
    assert vasp.ExistingVaspImport.__module__ == "ecatvasp.vasp.existing_import"
    assert vasp.ParsedVaspResult.__module__ == "ecatvasp.vasp.existing_import"
