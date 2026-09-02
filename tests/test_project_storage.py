from __future__ import annotations

import sqlite3

import pytest

from ecatvasp.domain import Project
from ecatvasp.storage import (
    CURRENT_SCHEMA_VERSION,
    MigrationPathError,
    MigrationRegistry,
    ProjectBundle,
    ProjectStorageError,
    ProjectStore,
    dumps_storage,
    loads_storage,
)


def test_storage_codec_round_trips_project() -> None:
    project = Project(name="Storage test", slug="storage-test")

    restored = loads_storage(dumps_storage(project))

    assert restored == project


def test_project_store_creates_file_first_layout_and_reopens(tmp_path) -> None:
    bundle = ProjectBundle(project=Project(name="ECatVASP", slug="ecatvasp"))
    store = ProjectStore(tmp_path)

    store.save(bundle)

    assert (tmp_path / "project.yaml").is_file()
    assert (tmp_path / ".workbench" / "project.sqlite").is_file()
    assert (tmp_path / ".workbench" / "manifests" / "project.json").is_file()
    for directory in (
        "structures",
        "calculations",
        "analyses",
        "references",
        "reactions",
        "figures",
        "exports",
    ):
        assert (tmp_path / directory).is_dir()

    reopened = ProjectStore(tmp_path).open()
    assert reopened == bundle

    connection = sqlite3.connect(store.database_path)
    try:
        schema_version = connection.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone()
    finally:
        connection.close()
    assert schema_version == (str(CURRENT_SCHEMA_VERSION),)


def test_project_store_rejects_database_tampering(tmp_path) -> None:
    store = ProjectStore(tmp_path)
    store.save(ProjectBundle(project=Project(name="Integrity", slug="integrity")))

    connection = sqlite3.connect(store.database_path)
    try:
        connection.execute(
            "UPDATE metadata SET value = 'tampered' WHERE key = 'format'"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ProjectStorageError, match="integrity manifest"):
        store.open()


def test_migration_registry_requires_explicit_consecutive_steps() -> None:
    registry = MigrationRegistry()

    assert registry.plan(1, 1) == ()
    with pytest.raises(MigrationPathError):
        registry.plan(1, 2)
