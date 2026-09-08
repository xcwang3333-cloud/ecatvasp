from pathlib import Path


def test_calculation_wizard_root_materialization_uses_authoritative_readiness_signal() -> None:
    source = Path(
        "ui/desktop/src/lib/calculations/CalculationWorkflowWizardView.svelte"
    ).read_text(encoding="utf-8")

    assert (
        'item.blocker_codes.includes("validated_numerical_evidence_required")'
        in source
    )
    assert '!step.blocker_codes.includes("accepted_structure_required")' not in source
