from __future__ import annotations

from pathlib import Path

import pytest

from ecatvasp.api.electronic_workspace import ProjectElectronicAnalysisApplicationService
from ecatvasp.api.read_request_store import RequestScopedVerifiedReadStore
from ecatvasp.desktop.electronic_analysis import (
    electronic_analysis_catalog_action,
    materialize_dos_analysis_action,
)
from ecatvasp.domain import Project
from ecatvasp.storage import ProjectBundle, ProjectStorageError, ProjectStore


def _empty_store(root: Path) -> ProjectStore:
    store = ProjectStore(root)
    store.save(ProjectBundle(project=Project(name="Block 8", slug="block-8")))
    return store


def test_request_scoped_read_store_reuses_one_verified_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    _empty_store(root)
    original_open = ProjectStore.open
    open_calls = 0

    def counted_open(self: ProjectStore) -> ProjectBundle:
        nonlocal open_calls
        open_calls += 1
        return original_open(self)

    monkeypatch.setattr(ProjectStore, "open", counted_open)
    store = RequestScopedVerifiedReadStore(root)

    first = store.open()
    second = store.open()
    third = store.open()

    assert first is second is third
    assert open_calls == 1
    with pytest.raises(ProjectStorageError, match="cannot persist project mutations"):
        store.save(first)


def test_electronic_catalog_action_bounds_reopens_within_one_read_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    persisted = _empty_store(root).open()
    original_open = ProjectStore.open
    open_calls = 0

    def counted_open(self: ProjectStore) -> ProjectBundle:
        nonlocal open_calls
        open_calls += 1
        return original_open(self)

    def fake_catalog(self: ProjectElectronicAnalysisApplicationService) -> dict[str, object]:
        one = self.store.open()
        two = self.store.open()
        three = self.store.open()
        assert one is two is three
        return {"project_id": str(one.project.id), "dos_sources": [], "analyses": []}

    monkeypatch.setattr(ProjectStore, "open", counted_open)
    monkeypatch.setattr(ProjectElectronicAnalysisApplicationService, "catalog", fake_catalog)

    payload = electronic_analysis_catalog_action(root)

    assert payload["project_id"] == str(persisted.project.id)
    assert open_calls == 1


def test_electronic_mutation_action_keeps_uncached_project_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    _empty_store(root)
    observed_store: ProjectStore | None = None

    def fake_materialize(
        self: ProjectElectronicAnalysisApplicationService,
        *,
        calculation_id: object,
    ) -> dict[str, object]:
        nonlocal observed_store
        del calculation_id
        observed_store = self.store
        return {"analysis_id": "01998f0c-7d00-7000-8000-000000000099", "reused": True}

    monkeypatch.setattr(
        ProjectElectronicAnalysisApplicationService,
        "materialize_dos",
        fake_materialize,
    )

    materialize_dos_analysis_action(
        project_root=root,
        calculation_id="01998f0c-7d00-7000-8000-000000000001",
    )

    assert type(observed_store) is ProjectStore
    assert not isinstance(observed_store, RequestScopedVerifiedReadStore)
