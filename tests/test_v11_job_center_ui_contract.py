from pathlib import Path


def test_job_center_is_mounted_and_keeps_execution_namespaces_separate() -> None:
    app = Path("ui/desktop/src/App.svelte").read_text(encoding="utf-8")
    source = Path("ui/desktop/src/lib/jobs/JobCenterView.svelte").read_text(
        encoding="utf-8"
    )

    assert "<JobCenterView" in app
    assert "Scientific" in source
    assert "Attempt" in source
    assert "Scheduler" in source
    assert "Scientific convergence remains unclassified until results are parsed." in source
    assert "Scheduler state is execution evidence only." in source


def test_job_center_normal_ui_does_not_collect_credentials_or_scientific_hashes() -> None:
    source = Path("ui/desktop/src/lib/jobs/JobCenterView.svelte").read_text(
        encoding="utf-8"
    )
    contracts = Path("ui/desktop/src/lib/jobs/contracts.ts").read_text(encoding="utf-8")

    for forbidden in ("private_key", "privateKey", "password", "token"):
        assert forbidden not in contracts
    assert "numerical_evidence" not in contracts
    assert "core_method_hash" not in source
    assert "potcar_spec_hash" not in source
    assert "analysis_hash" not in source
    assert "calculation_id" not in source.split("Advanced execution identity")[0]
