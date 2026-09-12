from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path

from ecatvasp.api import (
    ApplicationReportFormat,
    HeadlessProjectStatus,
    open_project,
)
from ecatvasp.cli import _status_text, main
from ecatvasp.domain import Project
from ecatvasp.schema.version import SCHEMA_VERSION
from ecatvasp.storage import ProjectBundle, ProjectStore, UnsupportedSchemaVersionError


def _store(tmp_path: Path) -> ProjectStore:
    store = ProjectStore(tmp_path / "project")
    store.save(
        ProjectBundle(
            project=Project(
                name="Headless acceptance",
                slug="headless-acceptance",
                schema_version=SCHEMA_VERSION,
            )
        )
    )
    return store


def test_facade_reopens_latest_project_store_state(tmp_path: Path) -> None:
    store = _store(tmp_path)
    facade = open_project(store.root)

    first = facade.inspect()
    updated_project = replace(store.open().project, name="Headless acceptance updated")
    store.save(ProjectBundle(project=updated_project))
    second = facade.inspect()

    assert first.projection.project_name == "Headless acceptance"
    assert second.projection.project_name == "Headless acceptance updated"
    assert first.projection.project_id == second.projection.project_id
    assert first.projection.projection_hash != second.projection.projection_hash


def test_status_contract_keeps_lifecycle_namespaces_separate() -> None:
    status = HeadlessProjectStatus(
        project_id="project-id",
        project_name="Status acceptance",
        project_slug="status-acceptance",
        schema_version=SCHEMA_VERSION,
        projection_hash="a" * 64,
        calculations=(("converged", 2),),
        analyses=(("completed", 3),),
        execution_attempts=(("succeeded", 4),),
        scheduler_jobs=(("completed", 5),),
        freshness=(("fresh", 6), ("stale", 1)),
        attention_rows=1,
    )

    text = _status_text(status)

    assert "Calculations: converged=2" in text
    assert "Analyses: completed=3" in text
    assert "Execution attempts: succeeded=4" in text
    assert "Scheduler jobs: completed=5" in text
    assert "Freshness: fresh=6, stale=1" in text
    assert "Attention rows: 1" in text


def test_cli_inspect_json_is_deterministic_and_source_linked(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = io.StringIO()
    second = io.StringIO()

    assert main(["--project", str(store.root), "inspect", "--json"], stdout=first) == 0
    assert main(["--project", str(store.root), "inspect", "--json"], stdout=second) == 0

    assert first.getvalue() == second.getvalue()
    payload = json.loads(first.getvalue())
    assert payload["project"]["name"] == "Headless acceptance"
    assert payload["project"]["schema_version"] == SCHEMA_VERSION
    assert payload["entity_counts"]["projects"] == 1
    assert payload["lifecycle_status"] == {
        "analyses": [],
        "calculations": [],
        "execution_attempts": [],
        "scheduler_jobs": [],
    }
    assert payload["inventory"][0]["entity_kind"] == "project"
    assert payload["inventory"][0]["freshness"] == "fresh"


def test_cli_report_reuses_exact_application_report_rendering(tmp_path: Path) -> None:
    store = _store(tmp_path)
    expected = open_project(store.root).report(format=ApplicationReportFormat.JSON).content
    output = io.StringIO()

    code = main(
        ["--project", str(store.root), "report", "--format", "json"],
        stdout=output,
    )

    assert code == 0
    assert output.getvalue() == expected
    payload = json.loads(output.getvalue())
    assert payload["project"]["schema_version"] == SCHEMA_VERSION
    assert payload["contract_version"] == "ecatvasp-scientific-report-v1"


def test_cli_missing_project_fails_closed_without_traceback(tmp_path: Path) -> None:
    output = io.StringIO()
    errors = io.StringIO()

    code = main(
        ["--project", str(tmp_path / "missing"), "status"],
        stdout=output,
        stderr=errors,
    )

    assert code == 2
    assert output.getvalue() == ""
    assert errors.getvalue().startswith("ecatvasp: error: ")
    assert "project database or manifest is missing" in errors.getvalue()


def test_cli_future_schema_fails_closed_without_traceback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def reject_open(_store: ProjectStore) -> ProjectBundle:
        raise UnsupportedSchemaVersionError("project schema is newer than this backend")

    monkeypatch.setattr(ProjectStore, "open", reject_open)
    output = io.StringIO()
    errors = io.StringIO()

    code = main(
        ["--project", str(tmp_path / "project"), "status"],
        stdout=output,
        stderr=errors,
    )

    assert code == 2
    assert output.getvalue() == ""
    assert errors.getvalue().startswith("ecatvasp: error: ")
    assert "project schema is newer than this backend" in errors.getvalue()
    assert "Traceback" not in errors.getvalue()


def test_block7_keeps_schema_version_three(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert SCHEMA_VERSION == 3
    assert open_project(store.root).status().schema_version == 3
