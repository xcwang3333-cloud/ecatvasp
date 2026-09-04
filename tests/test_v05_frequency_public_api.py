from ecatvasp import vasp


def test_frequency_result_public_api_is_exposed() -> None:
    assert vasp.VASP_FREQUENCY_RESULT_PARSER_NAME == "ecatvasp.vasp.frequency-result-parser"
    assert vasp.VASP_FREQUENCY_RESULT_PARSER_VERSION == "1"
    assert vasp.VASP_RESULT_DOCUMENT_VERSION == 3
    assert vasp.VaspFrequencyModeKind.REAL.value == "real"
    assert vasp.VaspFrequencyModeKind.IMAGINARY.value == "imaginary"
    assert callable(vasp.parse_vasp_frequency_results)
